# Safety and architecture review

This document describes Random File Viewer's safety boundaries and why they exist. Historical RC-by-RC repair notes remain in Git history; this is a summary of the current implementation, not a claim of formal security certification.

## File access and deletion

- Browser paths are validated as canonical relative paths. The final resolved target must stay within the resolved review root, even when links or junctions are involved.
- Scan membership, current-session presentation, and basic file identity checks prevent deletion of files never presented or files changed since presentation.
- The default Delete operation uses `send2trash`; an unavailable or failing Trash operation never silently falls back to permanent removal.
- Permanent deletion requires the explicit `--permanent` start flag and confirmation for each deletion in the browser.
- The scanner excludes symlink-like entries and verifies target containment again when serving or acting on files.

## Local browser/server controls

- Flask binds to `127.0.0.1` and disables the development reloader.
- State-changing requests require a per-process token; `/api/next` is a POST and retains the current item until an action completes.
- Host and cross-site media request checks reduce exposure to other websites.
- HTML files render in a sandboxed iframe with opaque origin and a restrictive Content Security Policy. Inline scripts and styles may work; normal outbound resource requests and forms are restricted. This is **not** a fully hardened sandbox for malicious HTML.

## Review history and concurrency

- Progress writes are atomic, using temporary files, flushing, `fsync`, and `os.replace`.
- Corrupt JSON/UTF-8 progress is preserved as a `.corrupt*` backup; unreadable or unpreservable progress aborts startup rather than risking an overwrite.
- The viewer distinguishes a successful Trash move with a failed progress save and warns of partial success rather than repeating deletion.
- Incomplete scans and directories obscured by inaccessible or linked paths preserve previously recorded history until they can be conclusively reconciled.
- Cross-process locking and read/merge/write updates avoid lost progress when independent viewer instances review **different** roots. Concurrent viewers of the **same** root are unsupported.
- The current displayed item remains stable across refreshes, and random selection samples the discovered pool rather than favoring the latest scan batch.

## Scale and known limitations

- Recursive scanning is incremental, with console progress messages; all discovered paths remain in memory. History is JSON and may become expensive to rewrite for exceptionally large collections.
- Windows/macOS/Linux filesystem semantics and browser video codec support vary. Testing has not established performance on arbitrary huge media archives.
- Concurrent hostile filesystem modifications by another local process can introduce validation-to-use races; this is a local personal utility, not a sandbox or multi-user file service.
- Progress is path-based across restarts. A replacement at an already reviewed path between sessions may remain marked reviewed.
- HTML previews may be incomplete under the sandbox/CSP. Always use disposable files when testing deletion.

## Verification evidence

- Owner-run Windows 11 / Python 3.13: 44 tests executed, 41 passed, 3 skipped; seven additional live disposable-file Windows checks passed, including a verified Recycle Bin move, real outside-root junction rejection, and 1,000-image discovery.
- GitHub Actions on the [Windows test portability fix](https://github.com/AdamFlores1/Random-File-Viewer/actions/runs/38479788989): all five jobs passed, covering Python 3.10/3.13 on Windows/Linux and Chromium safety regressions.
- The seven-check helper was removed from public distribution following a heuristic antivirus detection; `RUN_WINDOWS_CHECKS.ps1` only invokes the existing Python tests. Removal does not establish that all other files are malware-free.

See [README.md](README.md) for installation and behavior, [WINDOWS_VALIDATION.md](WINDOWS_VALIDATION.md) for safe manual validation, and [GitHub Actions](https://github.com/AdamFlores1/Random-File-Viewer/actions) for ongoing CI.
