from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import time
from pathlib import Path
from typing import BinaryIO
from urllib.parse import unquote, urlsplit

from .errors import StorageError, StorageErrorCode
from .keys import validate_key
from .models import StorageObject


class LocalFileStorage:
    """Secure local implementation of the provider-neutral storage port."""

    _CHUNK_SIZE = 64 * 1024
    _METADATA_FILENAME = ".storage-metadata.json"
    _DEFAULT_CONTENT_TYPE = "application/octet-stream"

    def __init__(self, root: str | Path, *, signing_secret: bytes | None = None):
        self._root = Path(root).resolve(strict=False)
        self._signing_secret = (
            signing_secret if signing_secret is not None else secrets.token_bytes(32)
        )
        self._metadata_path = self._root / self._METADATA_FILENAME
        self._content_types = self._load_content_types()

    def put(
        self,
        stream: BinaryIO,
        *,
        key: str,
        content_type: str,
        max_bytes: int | None = None,
    ) -> StorageObject:
        if max_bytes is not None and max_bytes <= 0:
            raise StorageError(
                StorageErrorCode.FILE_TOO_LARGE,
                "max_bytes must be positive",
            )

        key = validate_key(key)
        destination = self._path_for_key(key)
        temporary: Path | None = None

        try:
            self._root.mkdir(parents=True, exist_ok=True)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=".storage-",
                suffix=".tmp",
                dir=self._root,
                delete=False,
            ) as output:
                temporary = Path(output.name)
                size = 0
                digest = hashlib.sha256()
                while chunk := stream.read(self._CHUNK_SIZE):
                    size += len(chunk)
                    if max_bytes is not None and size > max_bytes:
                        raise StorageError(
                            StorageErrorCode.FILE_TOO_LARGE,
                            "stream exceeds max_bytes",
                        )
                    output.write(chunk)
                    digest.update(chunk)
                output.flush()

            os.replace(temporary, destination)
            temporary = None
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError(
                StorageErrorCode.BACKEND_UNAVAILABLE,
                "unable to write stored object",
            ) from exc
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

        checksum = f"sha256:{digest.hexdigest()}"
        previous_content_type = self._content_types.get(key)
        self._content_types[key] = content_type
        try:
            self._persist_content_types()
        except StorageError:
            if previous_content_type is None:
                self._content_types.pop(key, None)
            else:
                self._content_types[key] = previous_content_type
            raise
        return StorageObject(
            uri=f"local://{key}",
            key=key,
            size_bytes=size,
            checksum=checksum,
            content_type=content_type,
        )

    def get(self, uri: str) -> BinaryIO:
        _key, path = self._path_for_reference(uri)
        try:
            return path.open("rb")
        except FileNotFoundError as exc:
            raise StorageError(
                StorageErrorCode.OBJECT_NOT_FOUND,
                "stored object was not found",
            ) from exc
        except OSError as exc:
            raise StorageError(
                StorageErrorCode.BACKEND_UNAVAILABLE,
                "unable to open stored object",
            ) from exc

    def stat(self, uri: str) -> StorageObject:
        key, path = self._path_for_reference(uri)
        try:
            size, digest = self._checksum(path)
        except FileNotFoundError as exc:
            raise StorageError(
                StorageErrorCode.OBJECT_NOT_FOUND,
                "stored object was not found",
            ) from exc
        except OSError as exc:
            raise StorageError(
                StorageErrorCode.BACKEND_UNAVAILABLE,
                "unable to stat stored object",
            ) from exc

        return StorageObject(
            uri=f"local://{key}",
            key=key,
            size_bytes=size,
            checksum=f"sha256:{digest}",
            content_type=self._content_types.get(key, self._DEFAULT_CONTENT_TYPE),
        )

    def exists(self, uri: str) -> bool:
        _key, path = self._path_for_reference(uri)
        try:
            return path.is_file()
        except OSError as exc:
            raise StorageError(
                StorageErrorCode.BACKEND_UNAVAILABLE,
                "unable to inspect stored object",
            ) from exc

    def delete(self, uri: str) -> None:
        key, path = self._path_for_reference(uri)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise StorageError(
                StorageErrorCode.BACKEND_UNAVAILABLE,
                "unable to delete stored object",
            ) from exc
        if key in self._content_types:
            self._content_types.pop(key, None)
            self._persist_content_types()

    def create_download_url(self, uri: str, *, expires_in: int = 300) -> str:
        if not isinstance(expires_in, int) or isinstance(expires_in, bool) or expires_in <= 0:
            raise StorageError(
                StorageErrorCode.DOWNLOAD_URL_INVALID,
                "download URL expiry must be positive",
            )

        key, _path = self._path_for_local_uri(uri)
        payload = json.dumps(
            {
                "expires_at": int(time.time()) + expires_in,
                "uri": f"local://{key}",
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        encoded_payload = self._encode_token_part(payload)
        signature = hmac.new(
            self._signing_secret,
            payload,
            hashlib.sha256,
        ).digest()
        encoded_signature = self._encode_token_part(signature)
        return f"local-download://{encoded_payload}.{encoded_signature}"

    def _checksum(self, path: Path) -> tuple[int, str]:
        size = 0
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(self._CHUNK_SIZE):
                size += len(chunk)
                digest.update(chunk)
        return size, digest.hexdigest()

    def _load_content_types(self) -> dict[str, str]:
        if not self._metadata_path.is_file():
            return {}

        try:
            payload = json.loads(self._metadata_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("storage metadata must be an object")
            content_types: dict[str, str] = {}
            for key, content_type in payload.items():
                if not isinstance(key, str) or not isinstance(content_type, str):
                    raise ValueError("storage metadata values must be strings")
                validate_key(key)
                if key == self._METADATA_FILENAME or key.startswith(
                    f"{self._METADATA_FILENAME}/"
                ):
                    raise ValueError("storage metadata contains a reserved key")
                content_types[key] = content_type
            return content_types
        except (OSError, TypeError, ValueError, json.JSONDecodeError, StorageError) as exc:
            raise StorageError(
                StorageErrorCode.BACKEND_UNAVAILABLE,
                "unable to load storage metadata",
            ) from exc

    def _persist_content_types(self) -> None:
        if not self._content_types:
            try:
                self._metadata_path.unlink(missing_ok=True)
            except OSError as exc:
                raise StorageError(
                    StorageErrorCode.BACKEND_UNAVAILABLE,
                    "unable to persist storage metadata",
                ) from exc
            return

        temporary: Path | None = None
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                prefix=".storage-metadata-",
                suffix=".tmp",
                dir=self._root,
                delete=False,
            ) as output:
                temporary = Path(output.name)
                json.dump(
                    self._content_types,
                    output,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self._metadata_path)
            temporary = None
        except (OSError, TypeError, ValueError) as exc:
            raise StorageError(
                StorageErrorCode.BACKEND_UNAVAILABLE,
                "unable to persist storage metadata",
            ) from exc
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def _path_for_reference(self, uri: str) -> tuple[str, Path]:
        if not isinstance(uri, str):
            raise self._invalid_uri()

        try:
            parsed = urlsplit(uri)
        except ValueError as exc:
            raise self._invalid_uri() from exc

        if parsed.scheme == "local-download":
            local_uri = self._verify_download_url(uri)
            return self._path_for_local_uri(local_uri)
        return self._path_for_local_uri(uri)

    def _path_for_local_uri(self, uri: str) -> tuple[str, Path]:
        if not isinstance(uri, str):
            raise self._invalid_uri()
        if "?" in uri or "#" in uri:
            raise self._invalid_uri()

        try:
            parsed = urlsplit(uri)
        except ValueError as exc:
            raise self._invalid_uri() from exc

        if parsed.scheme != "local" or not parsed.netloc:
            raise self._invalid_uri()

        raw_key = f"{parsed.netloc}{parsed.path}"
        try:
            validate_key(raw_key)
            key = unquote(raw_key)
            validate_key(key)
        except StorageError:
            raise
        except Exception as exc:
            raise self._invalid_uri() from exc
        return key, self._path_for_key(key)

    def _path_for_key(self, key: str) -> Path:
        validate_key(key)
        if key == self._METADATA_FILENAME or key.startswith(
            f"{self._METADATA_FILENAME}/"
        ):
            raise StorageError(
                StorageErrorCode.INVALID_KEY,
                "object key is reserved by local storage",
            )
        try:
            candidate = (self._root / Path(key)).resolve(strict=False)
            candidate.relative_to(self._root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise StorageError(
                StorageErrorCode.INVALID_URI,
                "object reference must remain within local storage",
            ) from exc
        return candidate

    def _verify_download_url(self, uri: str) -> str:
        if "?" in uri or "#" in uri:
            raise self._download_url_invalid()
        try:
            parsed = urlsplit(uri)
        except ValueError as exc:
            raise self._download_url_invalid() from exc
        if parsed.scheme != "local-download" or not parsed.netloc or parsed.path:
            raise self._download_url_invalid()

        parts = parsed.netloc.split(".")
        if len(parts) != 2 or not all(parts):
            raise self._download_url_invalid()
        try:
            payload = self._decode_token_part(parts[0])
            signature = self._decode_token_part(parts[1])
        except ValueError as exc:
            raise self._download_url_invalid() from exc

        expected = hmac.new(
            self._signing_secret,
            payload,
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(signature, expected):
            raise self._download_url_invalid()

        try:
            claims = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise self._download_url_invalid() from exc
        if not isinstance(claims, dict):
            raise self._download_url_invalid()
        target = claims.get("uri")
        expires_at = claims.get("expires_at")
        if (
            not isinstance(target, str)
            or not isinstance(expires_at, int)
            or isinstance(expires_at, bool)
        ):
            raise self._download_url_invalid()

        try:
            self._path_for_local_uri(target)
        except StorageError as exc:
            raise self._download_url_invalid() from exc
        if int(time.time()) >= expires_at:
            raise StorageError(
                StorageErrorCode.DOWNLOAD_URL_EXPIRED,
                "download URL has expired",
            )
        return target

    @staticmethod
    def _encode_token_part(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    @staticmethod
    def _decode_token_part(value: str) -> bytes:
        if not value or any(
            character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
            for character in value
        ):
            raise ValueError("invalid token encoding")
        padded = value + "=" * (-len(value) % 4)
        try:
            decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        except (binascii.Error, ValueError) as exc:
            raise ValueError("invalid token encoding") from exc
        if LocalFileStorage._encode_token_part(decoded) != value:
            raise ValueError("invalid token encoding")
        return decoded

    @staticmethod
    def _invalid_uri() -> StorageError:
        return StorageError(
            StorageErrorCode.INVALID_URI,
            "storage URI is invalid",
        )

    @staticmethod
    def _download_url_invalid() -> StorageError:
        return StorageError(
            StorageErrorCode.DOWNLOAD_URL_INVALID,
            "download URL is invalid",
        )
