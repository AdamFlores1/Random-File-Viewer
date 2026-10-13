# Random File Viewer — Portfolio Roadmap

## Current target: v1.0 portfolio release candidate

The application fixes and regression tests are implemented, and **RC8 automated validation passed on Windows 11 / Python 3.13** (41 passed + 3 expected skips from 44 tests, plus 7/7 live Windows checks). It remains a release candidate until GitHub CI runs and remaining publication assets are finalized. The owner selected MPL 2.0, and the release package includes the license and source notices.

Keep the application small and demonstrate that local filesystem utilities can be built safely.

### Must-have before calling v1.0 complete

- **Completed 2026-10-13:** Run the **44 Python safety tests** on Windows; 41 passed, 3 expected skips, no failures.
- Perform a manual review with a disposable test folder containing nested folders, Unicode filenames, spaces, very long names, images, GIFs, videos, and HTML.
- **Completed:** Real Recycle Bin deletion passed the automated Windows live check; the separate unit test for failure without permanent fallback passed.
- Test a realistically large folder and record startup time and per-decision progress-save latency.
- Add one real screenshot or short GIF to the README for portfolio presentation (no synthetic mockups presented as the real UI).
- Add basic repository metadata: a short description and topics. **License selected:** MPL 2.0 (full text and source notices included).
- Check Git history for previously committed `review_progress.json` or other private paths before publishing.

## Next improvements — only after v1.0 safety validation

### Small quality upgrades

1. **Session resume details** — optionally remember the shuffled pending order/current item across process restarts, not just reviewed status.
2. **Folder filters** — include/exclude selected subfolders or extensions.
3. **Favorites / ratings** — lightweight non-destructive review metadata.
4. **Metadata panel** — dimensions, file size, modified time, duration where cheap to obtain.
5. **Statistics** — reviewed count, kept/deleted ratio, approximate bytes moved to Trash.

### Conditional scalability upgrade

Move progress from JSON to SQLite only if measurements show that atomic JSON rewrites become noticeably slow for the collection sizes the tool is expected to handle. Do not add a database preemptively.

### Later optional features

- Undo for trash operations where the platform can support it safely
- Duplicate detection using staged hashing (size -> fast hash -> full hash)
- Slideshow / timed advance

## Features intentionally deferred

- Accounts or cloud sync
- Remote/LAN server mode
- Plugin systems
- Complex monetization/licensing
- Permanent-delete automation
- Large framework rewrites

Those would add more risk and maintenance than portfolio value for this project.

**RC6:** The RC5 history safeguards remain, and follow-up work addresses random selection, separate-folder concurrent viewers, outgoing HTML resources, non-ASCII tokens and permanent-delete confirmation. Windows validation remains mandatory.

**RC8 Windows correction and validation (2026-10-13):** The Windows reparse fallback distinguishes missing paths from unsafe junctions. The full Windows 3.13 suite passed (41/44 plus 3 expected skips), followed by 7/7 disposable-file OS checks. GitHub Actions on Windows 3.10/3.13 and Linux 3.10/3.13 remains pending.
