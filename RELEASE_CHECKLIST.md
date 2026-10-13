# Release checklist — Random File Viewer

This file records the steps required before marking RC8 as the public **v1.0** release. Do not represent automated checks as completed until their output has been inspected.

## Automated checks (on GitHub)

- [ ] Push the source to the intended repository, leaving the final release unpublished initially.
- [ ] Open the **Actions** tab and check **Tests**: Python 3.10 and 3.13 on Windows and Linux (four combinations), plus the separate Chromium browser regression job.
- [ ] Record any failures, errors or skipped tests. In particular, a skipped real Windows symlink test is not positive evidence that symlink/junction behavior was verified.
- [ ] Investigate failures before attaching a stable v1.0 tag. Do not edit out safety tests to make the workflow green.

## Real Windows validation (owner's PC)

- [x] Run the automated disposable-file validation on Windows 11 (2026-10-13); optional manual media/GUI walkthrough remains available.
- [x] Real Send2Trash deletion and an actual Windows Recycle Bin entry were verified by the Shell-based automated check.
- [x] Confirm a junction pointing outside the review root is not descended into or served (real Windows junction integration test and disposable-file check passed on 2026-10-13).
- [x] Skip/restart persistence and modified-file rejection passed on Windows; the separate headless Chromium regressions checked held-Delete and permanent-mode confirmation. Optional on-screen manual review remains possible.
- [ ] Optional interactive large-folder responsiveness check. An automated 1,000-image discovery check passed on Windows, but that is not a real-browser responsiveness benchmark.

## Publication decisions and hygiene

- [x] Owner selected **Mozilla Public License 2.0 (MPL-2.0)**; full `LICENSE` and source notices are included. This permits commercial use while requiring distributed modifications to covered files to remain MPL-licensed.
- [ ] Capture a **real app screenshot** using dummy media (no private folders, addresses or other personal content). Save it at `docs/screenshot.png`, and add it to the README.
- [ ] Review the Git history of the source repository, if any: `git log --all -- review_progress.json` and `git ls-files -- review_progress.json`.
- [ ] Exclude progress data, backups, private files, virtual environments and caches from public commits.
- [ ] Review `README.md`, including known limitations and no-silent-permanent-delete guarantee; remove RC wording only after validation.
- [ ] Set repository description and topics, then publish a tagged `v1.0.0` release when validated.

**Suggested repository description:** Local Python/Flask utility to review media folders in randomized order, track progress, and safely send unwanted files to Trash.

**Suggested GitHub topics:** `python`, `flask`, `file-management`, `media-viewer`, `filesystem`, `open-source` (MPL-2.0 licensed).

**Suggested release heading:** Random File Viewer v1.0.0 — First public release.

**Future direction:** Free portfolio utility and practical engineering/learning reference. Avoid payment infrastructure and new features until first release feedback justifies them.

## Automated release gate added October 13, 2026

- [x] On Windows 11 / Python 3.13 (2026-10-13): 44 tests executed, 41 passed, 3 expected skips, 0 failures/errors; the historical separate Windows live-check helper then reported 7/7 OS integration passes, 0 inconclusive. The helper was excluded from the public archive after a heuristic antivirus detection; `RUN_WINDOWS_CHECKS.ps1` now runs only the Python suite.
- [ ] Confirm GitHub Actions Windows Python 3.10/3.13 **real junction** tests pass after upload.
- [ ] Confirm GitHub Actions Linux Playwright **real Flask/Chromium** flow passes after upload.
- [x] The Windows Shell Recycle Bin check returned FOUND, so no manual Shell fallback was necessary.
