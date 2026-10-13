# Random File Viewer

**Project maintainer:** Adam Flores (GitHub: [@AdamFlores1](https://github.com/AdamFlores1)).

A small local utility for reviewing large folders of mixed media in randomized order.

It recursively discovers supported files, opens them one at a time in a browser UI, remembers review progress, and lets you either keep a file or send it to the operating system's Recycle Bin / Trash.

**Release status:** The Windows 11 / Python 3.13 owner test run passed 41 tests (3 expected skips) and 7 separate disposable-file OS checks. The GitHub Actions Windows/Linux Python 3.10/3.13 and Chromium matrix [passed on `main`](https://github.com/AdamFlores1/Random-File-Viewer/actions/runs/38481266599). A screenshot may be added in a future update. Report bugs via [GitHub Issues](https://github.com/AdamFlores1/Random-File-Viewer/issues).

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

## Testing and validation

Run the unit and integration tests after installing dependencies:

```bash
python -m unittest discover -s tests -v
```

On Windows, `RUN_WINDOWS_CHECKS.ps1` runs the same suite using the local `.venv` interpreter. The suite currently has 44 tests; skips vary with platform and symlink privileges. It exercises path safety, file replacement checks, progress persistence and recovery, Trash-failure handling, and a real Windows junction test.

The [GitHub Actions workflow](.github/workflows/tests.yml) tests Python 3.10/3.13 on Windows and Linux, plus Chromium browser safety tests including a disposable-file Flask flow. The corrected Windows-path test branch passed all five CI jobs. The original main-branch run failed three tests due to test-fixture path normalization and was repaired in PR #1.

Historical owner-run checks: Windows 11 / Python 3.13, 41 passed, 3 skipped, no failures; 7/7 separate live Windows disposable-file checks passed, including a verified Recycle Bin move. The standalone Windows helper used for those seven checks is not distributed following a heuristic antivirus alert. Those results are historical and cannot be reproduced using the current Windows launcher alone.

For manual checks with disposable files, see [Windows validation](WINDOWS_VALIDATION.md). Automated checks do not guarantee compatibility with every media codec, system configuration, or hostile filesystem race.

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
├─ .gitignore
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

This is primarily a portfolio utility. Reliability and safe filesystem behavior come before adding features. See [ROADMAP.md](ROADMAP.md) for possible future improvements.


## License

**Copyright (c) 2026 Adam Flores** (for copyrightable material owned by the named holder).

Random File Viewer is released under the **Mozilla Public License, Version 2.0** ([MPL-2.0](LICENSE)). The MPL is an open-source, file-level copyleft license. Commercial use and distribution are permitted; when you distribute modified MPL-covered source files, you must make those files available under the MPL. Distribution of an executable also requires making its MPL-covered source available and telling recipients how to obtain it.

The license covers this project’s first-party source code; external Python dependencies retain their own licenses. Potential future paid editions or support offerings are not precluded by MPL 2.0, subject to the applicable licensing rights.

### Authorship and attribution

This project was developed with substantial AI assistance in implementation, testing, and review. Adam Flores directed the project requirements, product decisions, and safety/release priorities. The maintainer credit and copyright notices are not a claim that every line was written manually, or that copyright necessarily subsists in every AI-generated element.

When redistributing covered source, preserve the applicable MPL and copyright notices as required by Section 3.4. MPL 2.0 does not require a visible in-app credit or a promotional endorsement.
