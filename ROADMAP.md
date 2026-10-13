# Random File Viewer — Portfolio Roadmap

## Publication milestones

- Confirm the current `main` branch passes the full GitHub Actions matrix.
- Tag `v1.0.0` only after release checks succeed.

## Potential enhancements (based on user feedback)

### Small quality upgrades

1. **Session resume details** — optionally remember the shuffled pending order/current item across process restarts, not just reviewed status.
2. **Folder filters** — include/exclude selected subfolders or extensions.
3. **Favorites / ratings** — lightweight non-destructive review metadata.
4. **Metadata panel** — dimensions, file size, modified time, duration where cheap to obtain.
5. **Statistics** — reviewed count, kept/deleted ratio, approximate bytes moved to Trash.

### Conditional scalability upgrade

Move progress from JSON to SQLite only if measurements show that atomic JSON rewrites become noticeably slow for the collection sizes the tool is expected to handle. Do not add a database preemptively.

### Later optional features

- Add an authentic README screenshot/demo using disposable, non-private media when convenient.

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
