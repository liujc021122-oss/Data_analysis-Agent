# M20 Task 7 Report: Nginx Routing and Local HTTPS

## Scope

Implemented the Task 7 deployment contract on the M20 worktree. The change adds standalone HTTP and HTTPS Nginx configurations, an HTTPS Compose override, local certificate helper scripts for PowerShell and Bash, certificate ignore rules, and static deployment contract tests.

The existing `compose.yaml` was left unchanged as required. The HTTPS override mounts `reverse-proxy-https.conf` over Nginx's default configuration and replaces the inherited shell command so the mounted configuration is used directly.

## Implementation

- `deploy/nginx/reverse-proxy.conf`
  - Defines `frontend:8080` and `backend:8000` upstreams.
  - Serves `GET /healthz` with `200 ok`.
  - Proxies `/api/` to the backend and all other paths to the frontend.
  - Preserves `Host`, `X-Real-IP`, `X-Forwarded-For`, `X-Forwarded-Proto`, and generated `X-Request-ID` headers.
  - Uses 5 second connect and 30 second read/send proxy timeouts.
  - Disables access logging so request credentials are not written to access logs.

- `deploy/nginx/reverse-proxy-https.conf`
  - Retains the same routes and forwarding behavior on `443 ssl`.
  - References `/etc/nginx/certs/local.crt` and `/etc/nginx/certs/local.key`.
  - Nginx will fail startup when either mounted certificate file is absent.

- `compose.https.yaml`
  - Adds `8443:443`.
  - Mounts the HTTPS configuration read-only.
  - Mounts `./deploy/certs` at `/etc/nginx/certs` read-only.
  - Replaces the inherited inline configuration command and uses an HTTPS healthcheck.

- Certificate helpers
  - Both helpers create `deploy/certs` and produce exactly `local.crt` and `local.key`.
  - Existing outputs are preserved unless `--force` (Bash) or `-Force` (PowerShell) is supplied.
  - Both fail with explicit messages when OpenSSL is unavailable.
  - The PowerShell helper creates a localhost self-signed certificate with `New-SelfSignedCertificate`, then exports PEM files through OpenSSL.
  - The Bash helper uses the requested OpenSSL RSA 2048, 30 day, localhost SAN command.

- `tests/deployment/test_contract.py`
  - Adds proxy routing, HTTPS mount, helper safety, and ignore-rule contracts.

- `.gitignore`
  - Adds `deploy/certs/*.crt`; existing key and compose environment ignores remain in place.

## TDD and verification

The new proxy tests were run before implementation and failed because the requested files did not exist. After implementation, the focused contract suite passed:

```text
12 passed in 0.18s
```

Additional checks passed:

- `pytest tests/deployment -q`: 12 passed.
- PowerShell parser accepted `scripts/generate-local-certificate.ps1`.
- PyYAML parsed `compose.https.yaml`.
- `git check-ignore deploy/certs/local.crt deploy/certs/local.key .env.compose` confirmed all three paths are ignored.
- `git diff --check` reported no whitespace errors.

The local environment does not provide `bash` or Docker, so Bash syntax validation and Docker/Nginx execution were deferred to Docker-enabled CI as specified by the task brief.

## Review notes

The Nginx files intentionally contain `upstream` and `server` blocks without top-level `events` or `http` blocks because Compose mounts them under `/etc/nginx/conf.d/default.conf`. The base Compose file still contains its earlier inline HTTP configuration because changing base services was outside Task 7 scope.

## Task 7 review fix

Updated both proxy configurations to forward the caller's `X-Request-ID` value with `proxy_set_header X-Request-ID $http_x_request_id;`. This preserves valid caller IDs for the existing backend validation path; the backend remains responsible for generating a UUID when the header is missing or invalid. Added static assertions for both configurations and for removal of the old `$request_id` forwarding. No other proxy or TLS behavior changed.

Focused verification command and exact output:

```text
PS> .\.venv\Scripts\python.exe -m pytest tests/deployment/test_contract.py -q
............                                                             [100%]
12 passed in 0.18s
```
