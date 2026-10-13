# Random File Viewer

**Project maintainer:** Adam Flores (GitHub: [@AdamFlores1](https://github.com/AdamFlores1)).

A small local utility for reviewing large folders of mixed media in randomized order.

It recursively discovers supported files, opens them one at a time in a browser UI, remembers review progress, and lets you either keep a file or send it to the operating system's Recycle Bin / Trash.

**Release status:** RC8 passed the automated safety checks on the owner's Windows 11 / Python 3.13 system on 2026-10-13: 44 Python tests executed (41 passed, 3 expected platform skips, 0 failures/errors) and 7/7 disposable-file OS integration checks passed, including a real Recycle Bin move and an outside-root junction rejection. Five headless Chromium regressions also passed in the audit environment. If any further tests produce errors or bugs, contact Adam Flores.

**The GitHub Actions matrix has not run on a public repository, and a real application screenshot remains pending. The source is licensed under MPL 2.0.** See [validation status](VALIDATION_STATUS.md) and [release checklist](RELEASE_CHECKLIST.md).

## Features

- Recursive media discovery
- Incremental discovery with random selection from all currently discovered pending files (not a uniform shuffle of files that have not been scanned yet)
- Images, GIFs, videos, and HTML
- Keyboard navigation with held-key repeat suppression for destructive actions
- Persistent keep/delete progress (unreadable/corrupt history is preserved rather than silently overwritten)
- Previous-item navigation within the current browser session
- Recycle Bin / Trash integration through `send2trash`
- Explicit permanent-delete mode only when started with `--permanent`
- Local-only Flask server bound to `127.0.0.1`

## Safety model

Deletion is deliberately conservative.

- Browser-submitted paths are treated as untrusted input.
- Paths must be canonical relative paths; traversal such as `../` and Windows alternate separators are rejected.
- The server resolves the configured root and the requested target and verifies that the final resolved target is still inside the root.
- Symlinked files/directories are excluded during scanning, and resolved-path containment is checked again before serving, keeping, or deleting a file.
- A file must belong to the scanned review set and must have been presented by the server before a keep/delete action is accepted. The current run also records basic filesystem identity (device/inode, size, modification time) and rejects a file replaced or edited after presentation.
- Mutating API calls require a per-process request token.
- `/api/next` is a protected `POST`, not a state-changing `GET`.
- The local server rejects untrusted Host headers and cross-site media requests.
- Reviewed HTML has an opaque sandbox origin and a restrictive Content Security Policy (CSP). Inline scripts and styles can run for compatibility, but external scripts/styles, normal outbound fetch/beacon/image connections, and form submissions are blocked. This is still not a general-purpose sandbox for hostile HTML: iframe navigation and browser-specific behaviors have limitations.
- If `send2trash` is unavailable, Delete is disabled. The app **does not** silently fall back to permanent deletion.
- Permanent deletion is possible only after explicitly starting the app with `--permanent`, and the page also asks for confirmation on each permanent delete.

This is a local desktop-style utility, not a hardened multi-user service. A hostile local process that can modify filesystem objects at exactly the right time could still create operating-system-level race conditions between validation and file operations. The app is intentionally not designed as a security boundary against another malicious process running under the same user account.

## Requirements

- Python 3.10+
- Flask 3+
- send2trash 1.8+

Install:

```bash
pip install -r requirements.txt
```

## Run

On **Windows (PowerShell)**, from the extracted project directory:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py "C:\path\to\your\media"
```

On **macOS/Linux**, from the extracted project directory:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python app.py "/path/to/media"
```

Alternatively, if Python and dependencies are already available:

```bash
python app.py "C:\Users\YourName\Pictures\ToReview"
```

Startup prints the selected folder immediately, then reports recursive scan progress every 5,000 files so large folders do not appear to hang.

The app opens:

```text
http://127.0.0.1:5151
```

Use another port if needed:

```bash
python app.py "C:\path\to\folder" --port 5200
```

### Permanent deletion

Permanent deletion is disabled by default.

To opt in explicitly:

```bash
python app.py "C:\path\to\folder" --permanent
```

When this flag is active, the UI labels the action **Delete permanently**, shows that there is no app-level undo, and asks for confirmation before every deletion.

## Controls

| Action | Mouse | Keyboard |
|---|---|---|
| Previous | Previous button | Left Arrow |
| Keep / Skip | Skip button | Right Arrow or Space |
| Delete | Delete button | Delete |

`Skip` means the file is kept and recorded as reviewed. It will not reappear on the next run unless its progress entry is removed. Progress is path-based across restarts: if you replace a previously skipped file with a different file at the same path between runs, the app cannot detect that change and will continue treating it as reviewed. Reset that folder's progress if you need to review replacements. During a live session, a changed file is rejected until the viewer restarts.

## Supported media

- Images: `.jpg`, `.jpeg`, `.png`, `.webp`, `.bmp`, `.tiff`, `.tif`
- GIF: `.gif`
- Video: `.mp4`, `.webm`, `.mov`, `.avi`, `.mkv`, `.m4v`
- HTML: `.html`, `.htm`

HTML is displayed inside a sandboxed iframe with an opaque origin. A restrictive CSP blocks external scripts, CSS, images, fetch, beacons, and form submissions; inline scripts/styles and `data:`/`blob:` images can still work. Some HTML pages will therefore not render fully, and sandboxing cannot be treated as a guarantee against every navigation or browser implementation issue. Only files admitted to the current scan can be served.

Hidden names (files or directories beginning with `.`), symlinks/junctions and unsupported extensions are excluded from discovery. On Windows, `:` and `\` are rejected as path separators/alternate-data-stream risks. On macOS/Linux, `:` in an ordinary filename is supported; backslashes remain deliberately rejected for consistent browser-path handling.

## Progress persistence

Progress is stored in `review_progress.json` beside `app.py`, keyed by the resolved absolute review folder.

Progress writes also take an OS-level interprocess lock on `review_progress.json.lock`, re-read the latest file and update **only the active folder's entry**. This prevents separate viewer processes reviewing different folders from overwriting one another's progress. Running two viewers on the *same* folder at once remains unsupported because their in-memory review queues and item statuses are independent. The lock file may safely remain on disk; don't delete it while a viewer is running.

Writes use a temporary file, `fsync()`, and `os.replace()` so an interrupted save is much less likely to leave partially written JSON. Status changes are rolled back in memory if persistence fails. If a file was already moved to Trash but saving its status fails, the API reports partial success and the UI shows a persistent warning rather than inviting another deletion. The file operation itself cannot be rolled back by the app. If the file has invalid JSON or invalid UTF-8, the app preserves it as `review_progress.json.corrupt` (or `.corrupt.1`, etc.) before starting with empty progress. UTF-8 BOM-prefixed JSON is supported.

An unreadable progress file, or one that cannot be safely preserved, **aborts startup** rather than risk replacing existing review history. The error message identifies the progress file separately from folder errors.

If any folder cannot be fully enumerated because of a filesystem error, the scan is marked incomplete, missing skipped items are **not removed from progress**, and the UI displays a persistent scan warning after the available items are reviewed. Restart after restoring access. After a fully successful scan, a skipped entry is pruned only when its path is confirmed absent and the cleanup can be saved. Previously reviewed paths hidden by a symlink/junction or inaccessible filesystem path are preserved. A cleanup-save error preserves the in-memory history and is reported instead of silently poisoning a later save.

The currently presented item is also retained in server memory until it is actually kept or deleted. Refreshing the page therefore does not silently pop another item from the queue. If Delete succeeded but a later status refresh fails, the page records that deletion and tells you to reload rather than incorrectly claiming it failed.

`review_progress.json`, its `.lock` file, its temporary file, and its `.corrupt*` backups are ignored by Git and should not be committed. These files may reveal absolute folder paths and filenames.

## Large folders

The current implementation deliberately keeps the architecture simple:

- scanning is `O(n)` using `os.scandir`, with periodic console progress output;
- the hot scan loop avoids resolving every ordinary file; strict resolved containment is rechecked before every serve/keep/delete operation;
- all discovered relative paths are held in memory;
- pending files are selected with a random index from the full discovered pool, instead of popping from shuffled 32-file batches;
- progress is stored as JSON and rewritten atomically after each decision.

The viewer opens as files are discovered rather than waiting for a full inventory. The browser displays directory-entry and supported-media counts while scanning. Slow Windows storage can delay enumeration even with incremental scanning; compare against a smaller subfolder if progress appears stuck.

This is appropriate for ordinary personal media collections and keeps the project understandable. For extremely large collections, the first likely bottleneck is the growing JSON progress file rather than randomization itself. A SQLite-backed progress store is the recommended next scalability upgrade **only if profiling shows noticeable save latency**.

## Windows Python test suite

Once you have installed `requirements.txt` in `.venv`, run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\RUN_WINDOWS_CHECKS.ps1
```

This launcher runs the **44 Python tests only**; it does not run a separate Windows system-check helper or move a real file to your Recycle Bin. The suite includes a real junction test on Windows (using its own temporary directories), plus mocked Recycle Bin and error cases. The independent, previously completed **7/7 live Windows checks** remain recorded as historical release evidence, but their testing helper is **not distributed** because an antivirus flagged it heuristically. See `WINDOWS_VALIDATION.md` for a safe optional manual walkthrough.

A separate CI browser test exercises a real local Flask server using a **fake trash folder**, so it never deletes or trashes personal files.

## Tests

The repository contains **44 standard-library `unittest` cases** for the high-risk server behaviors (Flask is required to import the app):

```bash
python -m unittest discover -s tests -v
```

They cover traversal and resolved-path containment (including a mocked Windows-compatible case), file-replacement protection, safe Trash failures, explicit permanent deletion, request tokens including non-ASCII inputs, membership checks, scan-completion locking, scan-end progress persistence, incomplete-scan preservation, failed-cleanup preservation, link-obscured history, damaged progress files, multi-viewer progress merging for different folders, random pool selection, POSIX filenames, and HTML CSP response directives. **RC8 Windows result (2026-10-13): all 44 tests executed, 41 passed, 3 expected platform skips, 0 failures/errors. A previously executed private Windows integration harness also reported 7 passed OS checks and 0 inconclusive, including actual Send2Trash/Recycle Bin behavior and a real junction escape test. That harness is not included in the public package.** The audit environment could not run the full Flask suite; it independently reran five headless Chromium regression scenarios successfully. The GitHub Actions matrix is configured but remains unverified until the repository is pushed.

An **optional Chromium keyboard regression** runs against simulated in-browser API responses, without Flask or real files:

```bash
pip install playwright
playwright install chromium
python tests/browser_keyboard_probe.py
```

This checks that an initial Delete key press followed by synthetic auto-repeat events performs **only one** deletion. It also exercises the Delete button when the mocked API reports that a deletion completed but saving progress failed, when a successful deletion is followed by a failed status refresh, whether an incomplete scan produces a visible warning, and whether permanent deletion requires affirmative confirmation. Playwright is a QA-only dependency, not required to use the viewer. These simulations do **not** replace the manual Windows Recycle Bin test.

**GitHub Actions:** [`.github/workflows/tests.yml`](.github/workflows/tests.yml) is configured to run the entire Python suite on Windows and Linux using Python 3.10 and 3.13, plus Chromium safety regressions against both simulated responses and a real temporary Flask server on Linux. It will execute automatically on GitHub pushes and pull requests; results are **not yet known**. The Windows unit suite now includes a **real junction** regression. CI by itself does not establish how the Recycle Bin behaves on the owner's PC.

See `WINDOWS_VALIDATION.md` for the safe throwaway-folder procedure; see `RELEASE_CHECKLIST.md` for publication steps.

## Project layout

```text
Random File Viewer/
├─ app.py
├─ index.html
├─ requirements.txt
├─ README.md
├─ AUDIT.md
├─ ROADMAP.md
├─ WINDOWS_VALIDATION.md
├─ LEARNING_GUIDE.md
├─ .gitignore
├─ RELEASE_CHECKLIST.md
├─ LICENSE
├─ RUN_WINDOWS_CHECKS.ps1
├─ .github/workflows/tests.yml
└─ tests/
   ├─ test_app.py
   ├─ test_windows_junction.py
   ├─ browser_keyboard_probe.py   (optional, needs Playwright)
   └─ browser_live_smoke.py     (CI, real Flask + Playwright)
```

## Scope

This is primarily a portfolio utility. Reliability and safe filesystem behavior come before adding features. See `ROADMAP.md` for the recommended next steps; `LEARNING_GUIDE.md` provides an optional code-reading path once you want to study the implementation.


## License

**Copyright (c) 2026 Adam Flores** (for copyrightable material owned by the named holder).

Random File Viewer is released under the **Mozilla Public License, Version 2.0** ([MPL-2.0](LICENSE)). The MPL is an open-source, file-level copyleft license. Commercial use and distribution are permitted; when you distribute modified MPL-covered source files, you must make those files available under the MPL. Distribution of an executable also requires making its MPL-covered source available and telling recipients how to obtain it.

The license covers this project’s first-party source code; external Python dependencies retain their own licenses. Potential future paid editions or support offerings are not precluded by MPL 2.0, subject to the applicable licensing rights.

### Authorship and attribution

This project was developed with substantial AI assistance in implementation, testing, and review. Adam Flores directed the project requirements, product decisions, and safety/release priorities. The maintainer credit and copyright notices are not a claim that every line was written manually, or that copyright necessarily subsists in every AI-generated element.

When redistributing covered source, preserve the applicable MPL and copyright notices as required by Section 3.4. MPL 2.0 does not require a visible in-app credit or a promotional endorsement.
