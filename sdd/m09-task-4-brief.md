# Task 4: Execution Backend Selection and Production Fail-Closed Configuration

## Context

Task 3 provides local and container execution backends, but no application
configuration currently selects between them. Production must never silently
choose the in-process IPython backend or fall back to it when Docker is
unavailable.

## Requirements

- Add typed execution settings for backend selection and container image.
- Development and test default to the local compatibility backend.
- Production must select the container backend and require an image.
- Explicit production requests for the local backend must fail with a clear
  configuration error.
- Reject unknown backend and network-policy values with the configuration key
  in the error message.
- Add a factory that returns a `CodeExecutionBackend` without real Docker or
  model/network calls in tests.
- Runtime unavailability must produce the stable execution result and must not
  instantiate or call the local backend as fallback.
- Keep production secrets out of settings representations and diagnostics.

## Verification

- Add focused tests first and capture RED.
- Verify development/test isolation from production dotenv files.
- Verify production configuration failures name the missing/invalid key.
- Run execution/configuration regressions and the full offline suite.
- Write `sdd/m09-task-4-report.md` and commit the task separately.
