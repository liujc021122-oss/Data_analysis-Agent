import hashlib
import json

from ..api.schemas import AnalysisTaskCreateRequest


def compute_request_hash(request: AnalysisTaskCreateRequest) -> str:
    payload = request.model_dump(mode="json", exclude={"idempotency_key"})
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
