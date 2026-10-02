# Local Docker Compose deployment

This guide starts the M20 local topology: the frontend, FastAPI backend, Worker,
MySQL 8.0, Redis, MinIO, migration job, and Nginx reverse proxy. It uses the
checked-in `.env.compose.example` as a safe configuration template and keeps
the database, queue, and object-storage data in named Docker volumes.

## Prerequisites and configuration

Install Docker Desktop with the Docker Compose v2 plugin, then run these
commands from the repository root in PowerShell:

```powershell
Copy-Item .env.compose.example .env.compose
```

Open `.env.compose` and replace every `change-me` value with a local secret.
Set `OPENAI_API_KEY` to a real provider key when analysis calls are needed.
An empty `OPENAI_API_KEY` is valid for health-only startup and verification;
analysis requests that call the configured LLM require a key.

The Compose network uses service names internally. The MySQL container listens
on port 3306 inside Compose and is published as host port **3307** to avoid a
conflict with an existing local MySQL installation. The backend and Worker use
the `mysql:3306` service address from `DATABASE_URL`.

Validate interpolation and the merged Compose model before starting services:

```powershell
docker compose --env-file .env.compose -f compose.yaml config --quiet
```

Expected outcome: Compose validates the interpolated configuration and exits with
code 0 without printing resolved secrets. Check that the database password in
`DATABASE_URL` matches `MYSQL_PASSWORD` before continuing.

## Start and verify HTTP

Build the images and start the full topology:

```powershell
docker compose --env-file .env.compose -f compose.yaml up -d --build
```

The `migrate` service runs `alembic upgrade head` after MySQL is healthy. The
backend and Worker start only after MySQL, Redis, MinIO, bucket initialization,
and migration have completed successfully. The reverse proxy is available at
`http://localhost:8080`.

Check the proxy health endpoint, service state, and logs:

```powershell
Invoke-WebRequest http://localhost:8080/healthz
docker compose --env-file .env.compose -f compose.yaml ps
docker compose --env-file .env.compose -f compose.yaml logs backend worker migrate
```

`/healthz` should return HTTP 200 with `ok`. `ps` should show the long-running
services as healthy or running and the migration and MinIO initialization jobs
as completed. The logs should show migration completion and no startup
dependency errors.

For direct API readiness and migration inspection, run:

```powershell
docker compose --env-file .env.compose -f compose.yaml exec backend wget -qO- http://127.0.0.1:8000/health/ready
docker compose --env-file .env.compose -f compose.yaml exec migrate alembic current
```

The readiness command returns the API dependency status and succeeds only when
the database, Redis, and object storage checks pass. `alembic current` prints
the applied migration revision from the migration container.

## Optional local HTTPS

The HTTPS setup uses a locally generated self-signed certificate. Generate it
before starting the HTTPS override:

```powershell
pwsh ./scripts/generate-local-certificate.ps1
docker compose --env-file .env.compose -f compose.yaml -f compose.https.yaml up -d --build
```

Open `https://localhost:8443`. Browsers and command-line clients will warn that
the certificate is self-signed and not issued by a trusted public certificate
authority. That warning is expected for local development. The helper creates
local files under `deploy/certs/`; they are ignored by Git. This workflow has
no automatic public certificate renewal and must not be treated as a production
certificate management process.

The HTTPS proxy still serves `/healthz`, so a browser warning can be followed
by an explicit local check after accepting the certificate exception. To stop
the HTTPS stack, use the same `down` command below with both Compose files if
the override was used.

## Restart and data cleanup

To verify that named-volume data survives a normal restart:

```powershell
docker compose --env-file .env.compose -f compose.yaml down
docker compose --env-file .env.compose -f compose.yaml up -d
docker compose --env-file .env.compose -f compose.yaml exec backend wget -qO- http://127.0.0.1:8000/health/ready
```

`down` removes the containers and network while preserving the named
`mysql_data`, `redis_data`, and `minio_data` volumes. Use it for routine stops
and restarts.

`down -v` is destructive: it also removes those named volumes, deleting the
local MySQL database, Redis append-only data, and MinIO objects. Run it only
when a clean local environment is intended:

```powershell
docker compose --env-file .env.compose -f compose.yaml down -v
```

After `down -v`, the next `up -d --build` creates empty services and reruns the
migrations and bucket initialization.

## Local verification boundary

The repository's deployment contract tests validate Compose structure, service
gates, health checks, ports, secret injection, certificate mounts, and ignored
local credentials. Docker is not installed on the current development
machine, so image builds, container startup, network routing, migrations, and
runtime health responses must be verified on a machine with Docker Desktop or
in CI. The installed local MySQL service is separate from this Compose stack;
the documented host port 3307 prevents the two services from competing for
port 3306.
