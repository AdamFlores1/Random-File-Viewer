# Validation status — RC8 Windows regression correction, 2026-10-13

## Owner's real Windows results

- **RC6:** 41 Python tests run on Windows 11 / Python 3.13, 38 passed, 3 platform skips, 0 failures.
- **RC7:** 43 tests run on the same Windows PC: **1 failure, 1 error, 3 skips**. Two scan-completion tests failed because the new Windows reparse-point fallback treated `FileNotFoundError` from `lstat()` as an unsafe link, preventing ordinary removal of old progress records. The **real Windows junction safety test passed**; the older-Python reparse fallback test passed. The Windows live-check script stopped after the failed unit tests and did **not** run its further checks.

## RC8 correction and checks performed in the packaging environment

- `_is_link_like()` now treats `FileNotFoundError` as missing, not an unsafe link, while other `OSError` values still fail closed. This preserves outside-junction refusal and permits safe confirmation of stale removed files.
- A new regression test simulates missing and inaccessible paths under the Windows reparse-point fallback without needing Windows or symlink privileges.
- Python 3.13 compilation succeeded; code was parsed using Python 3.10 syntax.
- A targeted test under a lightweight Flask import substitute simulated the Windows branch of link detection and verified both formerly failing scan-end scenarios: cleanup occurs while the state lock is held, and a failed persistence attempt retains history and reports the error. **This is not the full Flask suite**.
- Five synthetic-response Chromium keyboard/partial-failure regressions passed on Linux.

## RC8 — confirmed real Windows results (2026-10-13)

- Windows 11, Python 3.13, Flask 3.1.3, Send2Trash 2.1.0: **44 tests executed, 41 passed, 3 skipped, 0 failures and 0 errors**. Two skips require symlink permissions; one tests POSIX-only colon filenames. A separate real Windows junction safety test passed.
- A historical private Windows system-check harness continued to real OS checks: **7 PASS, 0 INCONCLUSIVE**. Specifically: Skip retained and persisted a file across restart; Send2Trash moved a test file with a Recycle Bin entry verified by Windows Shell; default-mode delete affected only the disposable fixture; modified-file deletion was refused; an outside-root junction was rejected; HTML preview had restrictive CSP; and 1,000 disposable images were enumerated.
- Separate audit environment: **five headless Chromium browser regressions passed** against simulated API responses. The real live Flask+Chromium CI test has not been executed here because Flask is unavailable in the audit container.

## Still pending

- GitHub Actions matrix (Python 3.10 and 3.13 on Windows/Linux, plus real Flask/Chromium browser flow) has not yet run on GitHub.
- The owner selected MPL 2.0, and the release package now includes the full license and source notices. A true app screenshot, repository metadata, the first GitHub Actions run, and the first release tag remain publication steps.
- Optional on-screen media/codec checks with disposable GIF/video/HTML samples may improve compatibility evidence but are not part of the seven completed OS tests.

## Windows command

Run in RC8's extracted project directory after creating a `.venv` and installing `requirements.txt`:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\RUN_WINDOWS_CHECKS.ps1
```

The RC8 Windows results above have been inspected: 44 Python tests executed (41 passed, 3 skipped), and a separate private helper produced 7/7 disposable-file OS checks. The public package **omits that Windows-only helper** after a heuristic antivirus alert; `RUN_WINDOWS_CHECKS.ps1` now runs only the 44 standard Python tests. GitHub-hosted CI is separate and remains unverified.
