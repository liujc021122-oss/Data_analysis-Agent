# M11 Final Review Fix Report

## Scope

- Base: `b2942e2` (`fix: export report format from reports`)
- Review target: the working-tree changes on `codex/m09-secure-execution`
- Scope: the 7 Important and 2 Minor findings recorded in `sdd/final-review.md`
- Out of scope: PDF rendering
- Protected fixture preserved: `sdd/task-3-review.md`

## Findings Closed

1. Numeric evidence is now fail-closed. `ReportService` validates numeric narrative claims against verified document metrics when no registry validation is supplied, and replaces unsupported values with the pending-confirmation marker.
2. Chart references now resolve from the verified `ChartArtifact.file_path`, while `filename` remains a display/lookup identity. Nested paths and paths containing spaces or parentheses use safe Markdown destinations.
3. Template-generated titles, metric metadata, chart labels, evidence claims, and error codes use context-specific Markdown escaping. HTML and DOCX renderers decode only the supported safe subset before their own escaping.
4. `artifact_storage` and `storage` remain separate dependencies. Successful artifact registration now creates a download URL and exposes it through `ReportFormatResult.download_url`; URL failures preserve the local report and record a sanitized storage error.
5. Temporary output cleanup is best effort and file-only. Cleanup errors cannot escape the per-format failure boundary, and unexpected directories are left for lifecycle cleanup rather than recursively removed.
6. Report errors use the shared service sanitizer, including bearer credentials, credential URLs, sensitive query/assignment values, UNC paths, Windows drive paths, and POSIX paths.
7. `ReportService` requires a trusted `allowed_output_root` and enforces containment. `DataAnalysisAgent` supplies its session output directory when constructing the service.
8. HTML now renders the supported bold and italic Markdown subset consistently with Markdown and DOCX.
9. The service validates the active template version before rendering and records the validated version in `ReportBundle`.

## Verification

The following commands were run with the repository's Anaconda interpreter:

```text
E:\anaconda\python.exe -m pytest -q tests/reports tests/agent tests/storage tests/contract/test_report_contract.py tests/test_final_review_fixes.py
281 passed, 2 skipped, 9 warnings

E:\anaconda\python.exe -m pytest -q
835 passed, 3 skipped, 18 warnings in 66.10s

E:\anaconda\python.exe -m compileall -q src
exit code 0

git diff --check
exit code 0
```

The three skipped tests are two async tests without an async pytest plugin and one symlink-dependent test unavailable in the current Windows environment. No real model or network API is used by the test suite.

An independent read-only whole-branch review of the current diff reported zero Critical, Important, or Minor findings. It confirmed that all 7 Important and 2 Minor findings from the authoritative review are covered by the implementation and regression suite.

## Residual Risks

- Numeric validation establishes equality to a verified value, not semantic association between a sentence and a specific metric; stronger claim-to-metric binding belongs in a later evidence-model iteration.
- Path containment does not eliminate every local-user time-of-check/time-of-use race; deployments with hostile local users need stronger filesystem ownership/handle controls.
- The report renderers intentionally support a bounded Markdown subset rather than full CommonMark; parser differentials outside that subset remain out of scope.
