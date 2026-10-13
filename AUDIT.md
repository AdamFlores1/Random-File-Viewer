# Random File Viewer — Safety & Architecture Audit

## Critical findings fixed

### 1. Delete path traversal / root escape

**Original behavior:** `/api/delete` directly combined `STATE["root"]` with a browser-provided relative path and called the deletion function. A value such as `../target.jpg` could escape the configured root.

**Fix:** all browser paths now pass through canonical relative-path validation, filesystem resolution, and a final `resolved.relative_to(root)` containment check before filesystem access.

### 2. Unsafe permanent-delete fallback

**Original behavior:** if `send2trash` was unavailable, the normal Delete path called `os.remove()` even when permanent deletion had not been requested.

**Fix:** Delete now returns an error and leaves the file untouched when Trash support is unavailable. `os.remove()` is reachable only when the process was explicitly started with `--permanent`.

### 3. Symlink / junction escape risk

**Original behavior:** no explicit resolved-target containment check was performed.

**Fix:** symlink-like scan entries are excluded, and the final filesystem target is resolved and checked against the resolved root on every serve/skip/delete path.

### 4. State-changing GET

**Original behavior:** `GET /api/next` popped a file from the in-memory pool. Refreshes or unsolicited GETs could consume pending items.

**Fix:** `/api/next` is now a token-protected `POST`. The server keeps one current item and returns that same item until the user acts on it.

### 5. Cross-origin mutation surface

**Original behavior:** JSON was parsed with `force=True`, while mutating endpoints had no request token. A hostile page could potentially submit requests to the localhost service.

**Fix:** mutating API calls require a random per-process `X-Review-Token`, normal JSON parsing is used, untrusted Host headers are rejected, and cross-site media requests are blocked.

### 6. HTML sandbox was too permissive

**Original behavior:** reviewed HTML used `sandbox="allow-scripts allow-same-origin"`. Combining scripts with same-origin substantially weakens iframe sandbox isolation for content served from the app origin.

**Fix:** the iframe now uses `allow-scripts` without `allow-same-origin`, and HTML responses also receive `Content-Security-Policy: sandbox allow-scripts`.

## Correctness / persistence fixes

- Progress writes are atomic (`temp file -> fsync -> os.replace`).
- Progress data is schema-filtered on load.
- A failed delete is not recorded as deleted.
- A failed progress save after a successful delete is reported explicitly.
- Skip/delete operations only accept files discovered by the scan and presented during the current process session.
- The UI now checks HTTP failures instead of always showing a success toast.
- Progress percentage uses a stable total rather than a denominator that changes when `/api/next` pops an item.
- The app explicitly binds to `127.0.0.1` and disables the reloader.
- `index.html` can live beside `app.py`; Flask is configured accordingly.

## Performance review

### Current behavior

- Recursive discovery: `O(n)` filesystem walk.
- Random order (RC6): incremental scan with O(1) random selection from the entire currently discovered pending pool (not a globally uniform shuffle of files that have not been discovered).
- Memory: one relative-path string per supported file plus progress entries.
- Per decision: JSON progress is serialized and atomically rewritten.

### Likely large-folder bottleneck

For very large review histories, rewriting the complete JSON progress document after every action will eventually dominate interaction latency. The simplest portfolio-quality response is to benchmark first and move persistence to SQLite only if this is observable at intended collection sizes.

The scan itself is intentionally simple and should remain so unless profiling proves it is a problem.

### Synthetic check in the review environment

An earlier synthetic benchmark (not repeated in this release-candidate patch, and not a substitute for Windows testing on the user's actual disk) reported:

- 20,000-file `os.walk` + collection + shuffle: ~0.31 s.
- 10,000 progress entries serialized + flushed + fsynced: ~0.02 s (~1.1 MiB).
- 50,000 entries: ~0.11 s (~5.4 MiB).
- 100,000 entries: ~0.22 s (~10.8 MiB).

These historical measurements are context only, not evidence of a controlled benchmark for the current build. They support measuring before replacing the current scan/batch-shuffle design. At around 100,000 reviewed entries, rewriting JSON on every decision can become perceptible, especially on slower Windows storage; that is the point where SQLite becomes a justified follow-up rather than premature architecture.

## Browser/server architecture assessment

A small Flask server plus a static browser UI is reasonable for this utility because the browser provides strong media rendering while Python handles local filesystem access. Splitting this into Electron/Tauri/native code now would increase project size without solving a current requirement.

The architecture should remain explicitly local-only and single-user.

## Remaining limitations

- The app is not intended to defend against a hostile local process running under the same OS user and racing filesystem changes between validation and an operation.
- Progress JSON is shared by app instances. RC6 uses an interprocess lock and merges updates by review root, supporting separate folders concurrently. Running two copies on the **same** root is still unsupported.
- Browser codec support determines whether a discovered video format can actually play.
- HTML pages that require same-origin privileges may not fully work because that privilege is intentionally withheld for safety.

## Startup responsiveness fix

The initial hardened build performed the complete recursive scan before printing any console status, which made a large directory appear frozen even though the process was working. Startup now prints the target immediately and reports scan progress every 5,000 files. The scan hot path also avoids an unnecessary regular-file stat while preserving link filtering and operation-time resolved containment checks.

## 2026-10-11 — Release-candidate hardening (post independent audit)

The earlier sections describe the original architectural hardening. This follow-up addresses bugs exposed in a separate 2026-10-10 independent audit of the original and first patched ZIPs.

### Additional safety/correctness changes

1. **Held Delete:** the keyboard handler ignores `keydown` events with `e.repeat`. Backspace no longer deletes; the UI and README advertise Delete only.
2. **False completion:** `/api/next` makes the current/pool/scan-status decision under one `STATE_LOCK` acquisition, so it cannot return `done` based on scan state observed after a separate earlier pool check.
3. **Media route allowlist:** `/file/<path>` checks scanned-file membership under the lock and rejects unknown/hidden/non-media files, in addition to canonical path and root containment checks. Reviewed HTML still uses a sandboxed origin; external relative assets may not load.
4. **Damaged progress:** UTF-8 BOM works; malformed progress JSON and invalid UTF-8 are renamed to `.corrupt*` before a fresh state is created; files that cannot be read or preserved abort startup with an accurate progress-specific error.
5. **Scan-end progress:** stale skipped entries are persisted while the state lock is held. Failed action-level progress writes now also roll back their in-memory status mutation.
6. **Distribution privacy:** `review_progress.json`, `.tmp`, and `.corrupt*` are ignored; the clean release ZIP has no progress data or local Git metadata.

### Added tests and practical verification

- The first safety patch added 8 Python regression tests (20 total), independently reported passing on Linux with Python 3.13, Flask 3.1.3, and Send2Trash 2.1.0; the independent browser investigation reported that the held-Delete case went from 13 deletions to 1 on 15 throwaway files.
- This follow-up adds **8 more Python regression cases** (28 total), including mocked canonical containment independent of symlink creation, the `/api/next` lock timing, scan-end lock ownership, missing `known_files` membership, failures reading/preserving the progress file, accurate startup error output, and state rollback on failed progress save.
- A standalone Playwright/Chromium synthetic keyboard probe (`tests/browser_keyboard_probe.py`) passed in the patch environment: one initial Delete keydown and twelve repeated keydown events led to exactly one simulated deletion. No real files were deleted in this probe.
- Python source/test compilation passed in the first RC environment, but the 28-test Flask suite could not be run there. An independent Linux review later reported **28/28 passing, no skips**, with Python 3.13, Flask 3.1.3, Send2Trash 2.1.0, plus mutation and browser checks. This does **not** establish Windows-specific behavior.

### Known constraints, not release-blocking code changes

- Paths containing a colon are intentionally rejected even on POSIX, and skipped during scanning, to preserve the current cross-platform path policy (especially Windows alternate data streams).
- Scan order is randomized incrementally in batches, not guaranteed to be a uniform shuffle of all files in an arbitrarily large folder.
- Actual Windows Trash behavior, junction/permission scenarios, performance on the owner's largest folder, and a real GUI smoke test still require Windows validation. See `WINDOWS_VALIDATION.md`.

## 2026-10-12 — Follow-up after independent Linux verification

- Independent review reported **28/28 Python tests passing on Linux** (Python 3.13, Flask 3.1.3, Send2Trash 2.1.0), and 27 of 29 independently selected mutations caught. This is evidence reported by the reviewer, not a Windows test result.
- Corrected the earlier documentation that inaccurately implied no one had executed the 28 tests.
- Addressed a further UI edge case: when moving a file to Trash succeeds but recording that action fails, `/api/delete` responds with HTTP 500 plus explicit `file_deleted: true` and `progress_saved: false`. The browser marks the item as already deleted, disables Skip/Delete for it, and shows a persistent warning rather than offering operations on a non-existent file. The app does not falsely mark the progress as saved or silently continue.
- Added **one backend regression test**, bringing the Python suite to **29**. Extended the optional Playwright browser probe to exercise the actual Delete-button failure path using simulated responses. The updated browser probe passed in Chromium, and Python source/test compilation passed here.
- **Verification at the time:** the 29-test Flask suite had not been run in that packaging environment. The later section below describes the additional tests and current validation requirements.


## 2026-10-12 — Second RC review (additional confirmed edge cases)

- **Successful deletion, failed UI refresh:** a controlled Chromium scenario showed that an HTTP 503 from `/api/status` after a successful `/api/delete` caused the page to say "Could not delete" and display the removed file. The page now distinguishes a post-deletion refresh error from failure of the destructive operation, shows a durable recovery message, and disables repeat actions. The optional browser probe now pins this scenario.
- **Changed file at a previously presented path:** a direct backend reproduction showed that after a file was presented and skipped, a different file written at the same path could still be deleted, because `presented` was path-only. The current session now stores a cheap stat-based identity and Skip/Delete reject a mismatch with HTTP 409. Three cross-platform Python regression tests cover a newly replaced current item, a replaced previously skipped item, and an edited file before Skip. This is accidental-replacement hardening, not a guarantee against malicious concurrent filesystem changes.
- **Validation status at RC3:** 32 Python tests were included, but that updated suite had not been executed with Flask in that environment. Python syntax compilation, targeted backend execution using a Flask substitute, and the real Chromium synthetic keyboard, partial-save and post-delete-refresh browser probes pass. The independent earlier 28/28 Linux result remains valid for that earlier revision only. Actual Windows validation, Recycle Bin behavior, and junction traversals remain unverified.
- **Persistence limitation:** cross-session review progress is still keyed to path. File replacement between application runs is not detected for items already recorded as skipped; use a fresh progress state for a changed collection. The current-session identity check only covers files presented during the active process.

## 2026-10-12 — Third RC review: incomplete-scan progress preservation

A reproducible scan error was discovered: when a previously reviewed subfolder became temporarily unreadable, `os.scandir()` logged the error but scan reconciliation treated its skipped files as deleted, removed their review history, and saved the empty state. On the next accessible run those files reappeared as unreviewed.

The scanner now reports enumeration errors to its state owner. If any directory/entry traversal step fails, existing skipped progress is preserved, and the end of the available review queue reports an incomplete scan. Only a fully successful scan may prune skipped entries for files no longer present. An added Python regression test simulates a temporary subfolder permission error and confirms progress preservation, error reporting, and subsequent recovery.

**Verification:** the regression was reproduced against RC3 before this patch with a Flask substitute, and the new safety path has been probed with the same method; the complete RC4 33-test Flask suite and Windows integration tests remained to be executed at that point.


## 2026-10-12 — Fourth RC review: scan-end history reconciliation

Two additional history-loss scenarios were reproduced against RC4 using disposable directories and a lightweight Flask import substitute (not the full Flask integration environment):

1. **Failed cleanup-save followed by successful review:** the scanner pruned a missing skipped entry from its live dictionary before saving. When the cleanup save failed, the next successful Skip saved the pruned state and permanently removed the older entry from `review_progress.json`, even though the original failed save had left it intact. The cleanup now builds a separate candidate state, persists it first, and commits it to memory only after a successful write. On failure it preserves history and reports a scan/progress warning.
2. **Previously reviewed path beneath an excluded symlink:** the scanner skipped the link correctly, but treated the unseen reviewed descendant as deleted and pruned its history. Reconciliation now tests only stale candidates and prunes them only when the original path is confirmed absent, rather than merely absent from enumeration. A link/junction, unreadable path, or an existing file not discovered in that scan does not prove deletion.

Three new Python regressions cover failed cleanup-save + subsequent Skip, an excluded link prefix without requiring symlink privilege, and a file missed by enumeration. The suite now contains **36 Python tests**. Python syntax checks, direct backend reproductions of the two failures before/after correction, a normal-stale-file reconciliation probe, and the optional Chromium keyboard/partial-save/refresh/incomplete-scan regression probe passed in this environment. Flask was unavailable and dependency installation was blocked; **the full 36-test Flask suite has not been executed here**, and Windows integration remains unverified. An independent reviewer previously verified 28/28 tests on Linux for an earlier revision, not the current build.


## 2026-10-12 — RC6: independent-review reconciliation

Claude's report names an RC4 ZIP but also reports a 36-test run, which corresponds to RC5's test count. The exact revision tested is therefore not established by that report. I reviewed the attached **RC5** source directly and confirmed the following issues were still present there:

1. **Biased review order:** pop-from-end on independently shuffled 32-file batches biases directory-sorted listings. RC6 randomly selects an index from the entire available pool in O(1) time using a swap-and-pop operation. This improves randomness without claiming a uniform global shuffle of not-yet-discovered files.
2. **Concurrent different-folder progress clobbering:** every instance previously wrote its stale snapshot of the complete JSON file. RC6 takes an advisory cross-process lock on `review_progress.json.lock`, reads the latest data inside that lock, and replaces only the active root entry before atomic save. **Simultaneous review of the same root remains unsupported.** The lock file must not be removed while any viewer is running.
3. **HTML outbound connections:** the old `sandbox allow-scripts` blocked same-origin reads but still allowed fetch/beacon/image network requests. RC6 adds a restrictive CSP: offline inline JS/CSS allowed, network fetch and external resources blocked, forms disabled. Browser navigation restrictions are imperfect; reviewed HTML remains untrusted.
4. **Non-ASCII request tokens:** compare byte encodings to avoid a `TypeError`/HTTP 500. Malformed tokens now return 403.
5. **POSIX colon names:** the Windows `:` restriction is now platform-scoped. Dotfiles/dotfolders and symlinks remain intentionally omitted, documented in README.
6. **Permanent deletion:** the browser now requests explicit confirmation for each permanent Delete in addition to requiring the CLI `--permanent` opt-in. Keyboard navigation skips focused interactive controls. This UI confirmation is not a server-side second-factor; direct authenticated API requests with a valid review token still work as before.
7. **Tests:** Five additional Python regression cases (41 total) cover random selection, stale different-root progress snapshots, malformed tokens, the restrictive HTML CSP, and POSIX colon names. File-serving tests close their successful responses. The optional Chromium browser probe now also verifies permanent-mode confirmation.

**Observed here:** Five standalone Chromium browser checks passed. Focused backend exercises with a lightweight Flask import substitute passed for concurrent collision merging, random selection, token rejection, HTML CSP header and POSIX filenames. Python and JavaScript syntax checks passed. The **41-test suite has not run with real Flask** in this environment, and Windows integration tests remain open.

## 2026-10-13 — Windows automation supplement

The user's **prior RC6** Windows/Python 3.13 run completed 41 tests: 38 passed, 3 skipped, 0 failed. The new automation release candidate adds two tests including a real Windows junction regression (43 Python tests total), a Python 3.10-compatible reparse-point fallback in `_is_link_like`, a temporary Windows integration harness (actual Recycle Bin and disposable junction checks; since removed from public packages), and a Playwright flow against a real temporary Flask server. They have not yet all been run on Windows. The local Chromium mock-network regressions passed in the packaging environment; Flask was unavailable there. Do not conflate preparation with platform verification.

## 2026-10-13 — RC8: Windows scan-cleanup regression correction

The owner's first RC7 Windows run (Python 3.13, real Flask) executed 43 tests: 1 assertion failure, 1 error, and 3 platform skips. The failing tests were `test_scan_end_progress_save_occurs_while_state_lock_is_held` and `test_failed_scan_cleanup_does_not_erase_history_on_next_save`. The real Windows junction safety test and the fallback reparse attribute test both passed. Because `RUN_WINDOWS_CHECKS.ps1` stops after failed unit tests, it did not execute the broader Windows live checks.

**Root cause:** RC7 updated `_is_link_like()` to inspect Windows file reparse attributes with `path.lstat()`. Its broad `except OSError: return True` also captured `FileNotFoundError` for a genuinely removed progress path. This incorrectly prevented `_confirmed_absent_after_scan()` from pruning stale entries, so the affected tests never reached `save_progress()` or its simulated failure.

**RC8 change:** handle `FileNotFoundError` separately as `False` (the absent path is not itself a link), while treating other `OSError` conditions conservatively as link-like/unsafe. Add a cross-platform Windows-reparse fallback regression for both missing and unreadable paths, raising the suite from 43 to **44 tests**. Targeted Windows-branch simulations verified the two former scan-end failures and the new distinction, and five Chromium checks passed on Linux. The full 44-test suite and live Windows checks have **not** been executed for RC8 in the packaging environment.


## 2026-10-13 — Independent RC8 end-to-end release audit

- The owner's PowerShell log for the actual RC8 archive reports **44 Python tests executed: 41 passed, 3 expected platform skips, 0 failures/errors** on Windows 11 / Python 3.13 with Flask 3.1.3 and Send2Trash 2.1.0. Two skips relate to Windows symlink privileges; one to POSIX colon filenames.
- After unit tests, the real Windows disposable-file checks produced **7 PASS / 0 INCONCLUSIVE**, including verified Recycle Bin entry, a real rejected outside-root junction, skip/restart progress, changed-file deletion rejection, HTML CSP response, and a 1,000-file scan.
- A fresh audit of this exact codebase reran the five standalone headless Chromium checks successfully on Linux and checked package contents, documentation, and source safety boundaries. Flask is unavailable in that audit environment, so it did not independently rerun the full Flask suite; the owner's actual Windows execution supplies that evidence.
- This review found **no additional confirmed destructive-file defect**. Remaining release tasks are to capture an authentic UI screenshot, publish a clean MPL-2.0-licensed repository, and examine GitHub Actions results on the configured operating systems/Python versions.
- The test suite does not prove race freedom against malicious same-user filesystem swaps, guarantee all video codecs render, or establish compatibility with every Windows permission arrangement. Simultaneous use of the same review root across processes remains unsupported.

## 2026-10-14 — Public packaging: optional Windows helper removed

A heuristic antivirus alert targeted the standalone Windows OS-validation helper, not the main viewer. The helper was removed from the public ZIP rather than restored or allowlisted. `RUN_WINDOWS_CHECKS.ps1` now runs only `python -m unittest discover -s tests -v`; GitHub Actions already used the standard Python and Playwright tests and did not depend on the removed script. The earlier owner-run 7/7 Windows OS integration checks remain recorded as historical evidence, **not** as a promise that the current launcher repeats them. Application logic and the 44 Python test cases are unchanged.
