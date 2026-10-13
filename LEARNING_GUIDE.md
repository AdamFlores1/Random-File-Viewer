# Understanding Random File Viewer — at your own pace

You do **not** need to understand every line before running it on a disposable folder. Start with the user-visible workflow and follow one request through the code. This is a suggested study order, not a requirement for using the tool.

## 1. What each file does

- `app.py`: configures the local Flask server, scans folders, checks paths, tracks review state, deletes/keeps files, and persists progress.
- `index.html`: browser interface, keyboard shortcuts, navigation history, requests to Flask.
- `tests/test_app.py`: Python regression tests for safety rules. The cross-platform containment test deliberately mocks filesystem resolution so it runs without creating a real symlink.
- `tests/browser_keyboard_probe.py`: optional real-browser test using fake API responses; it **never deletes real files**.
- `README.md`: running instructions and the behavior users can expect.
- `AUDIT.md`: security engineering rationale and limitations.
- `WINDOWS_VALIDATION.md`: real-world tests required before claiming the Windows release is verified.

## 2. Suggested learning sessions

**Session A — The viewing flow**

1. Open `app.py`, find `main`, `_prepare_state`, `start_background_scan`, `api_next`.
2. Open `index.html`, find `init`, `fetchNext`, `goNext`, and `render`.
3. Explain why `api_next` returns the same current file until Skip/Delete, and why an incomplete scan returns `waiting` rather than `done`.

**Session B — The deletion safety model**

1. Trace `handleDelete` → `/api/delete` → `_request_relpath` → `resolve_target` → `send2trash` → `persist_status`.
2. Explain why valid *textual* relative paths can still resolve outside the root through symlinks or junctions.
3. Explain why `known_files`, `presented`, and `presented_identity` enforce different rules, and why a token is required on each mutating API call.
4. Verify that without `--permanent`, a Trash error never falls back to `os.remove`. In permanent mode, the browser also asks for confirmation before each action.

**Session C — Progress and failures**

1. Read `load_progress`, `save_progress`, and `persist_status`.
2. Explain why bad JSON is renamed to `.corrupt*`, why read/rename errors abort startup, and why a failed write restores the old in-memory status.
3. Explain the purpose of the interprocess lock file (`_progress_guard`), the fresh read-and-merge in `save_progress`, and the `flush`/`fsync`/`os.replace` sequence. Why must the lock cover both the read and the write? Two viewers of *different* folders can share progress; simultaneous reviews of the *same* folder are still unsupported.
4. Trace the partial-success case: the file reaches Trash, saving its status fails, the server reports `file_deleted: true`, and the browser blocks repeat deletion while warning that progress was not saved.
5. Trace another case: Delete and progress saving succeed, but a later status request fails. Explain why the UI must not say deletion failed.

**Session D — Concurrency and testing**

1. Read `_iter_supported_files`, `_scan_into_state`, and `api_next` to understand incremental scanning, small batches, and O(1) random selection from the currently discovered pool.
2. Explain what `STATE_LOCK` protects and why deciding whether the review is `done` must happen under one lock acquisition.
3. Run `python -m unittest discover -s tests -v` and focus on the tests for path containment, scan completion, scan-end persistence, and failed Trash deletion.
4. In a separate disposable **copy** of the project, intentionally break one safety check, confirm the relevant test fails, then discard the copy. Never do this against real media or while `--permanent` is enabled.

## 3. Portfolio interview readiness

After studying, aim to answer these without notes:

- Why is the project a local Flask server rather than a plain HTML file?
- How does it handle very large folders without waiting for the scan to finish?
- What stops a malicious `../` path, and what stops a symlink that points outside the root?
- What stops an external site from sending valid deletion requests?
- How does the viewer avoid deleting multiple files when Delete is held?
- What happens when moving a file to Trash succeeds but saving progress fails?
- What did automated tests establish, and which Windows-specific behaviors still need manual verification?

A useful description of AI involvement is: *AI-assisted implementation with owner-defined safety requirements, independent review, regression testing, and later hands-on code study.* Only claim parts of the code you have personally reviewed or tested.
