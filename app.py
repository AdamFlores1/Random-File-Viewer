# Copyright (c) 2026 Adam Flores
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Random File Viewer.

Shows supported media from a folder one item at a time in randomized order so
large collections can be reviewed without browsing them sequentially.

Run:
    python app.py "C:\\path\\to\\your\\folder"
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import datetime as dt
import hmac
import json
import os
import random
import stat
import secrets
import sys
import threading
import webbrowser
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from flask import Flask, jsonify, render_template, request, send_file, url_for

try:
    from send2trash import send2trash
except ImportError:  # pragma: no cover - exercised by tests through monkey-patching
    send2trash = None

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".tif"}
GIF_EXTS = {".gif"}
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".avi", ".mkv", ".m4v"}
HTML_EXTS = {".html", ".htm"}
SUPPORTED_EXTS = IMAGE_EXTS | GIF_EXTS | VIDEO_EXTS | HTML_EXTS
VALID_STATUSES = {"skipped", "deleted"}

BASE_DIR = Path(__file__).resolve().parent
PROGRESS_FILE = BASE_DIR / "review_progress.json"

# Keep index.html beside app.py so the utility stays easy to copy and run.
app = Flask(__name__, template_folder=str(BASE_DIR))

STATE_LOCK = threading.RLock()
PROGRESS_IO_LOCK = threading.RLock()
STATE: dict[str, Any] = {
    "root": None,
    "permanent_delete": False,
    "pool": [],
    "current": None,
    "presented": set(),
    "presented_identity": {},
    "known_files": set(),
    "reviewed": {},
    "all_progress": {},
    "total_count": 0,
    "scan_complete": False,
    "scan_error": None,
    "scan_scanned_entries": 0,
    "scan_supported_files": 0,
    "api_token": secrets.token_urlsafe(32),
}


class PathSafetyError(ValueError):
    """Raised when a browser-supplied path fails containment checks."""


class ProgressFileError(OSError):
    """Raised when existing review history cannot be safely loaded or preserved."""


def _is_link_like(path: Path) -> bool:
    """Reject symlinks and Windows reparse points, including on Python 3.10.

    Path.is_junction() only exists in Python 3.12+. On Windows, the
    FILE_ATTRIBUTE_REPARSE_POINT bit from lstat() also covers junctions on
    older supported Python versions. An unreadable path fails closed.
    """
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if is_junction and is_junction():
            return True
        if os.name == "nt":
            attrs = getattr(path.lstat(), "st_file_attributes", 0)
            return bool(attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT)
        return False
    except FileNotFoundError:
        # An absent path cannot itself be a junction. In particular, stale
        # review entries must reach _confirmed_absent_after_scan()'s final
        # existence check so clean scans can discard genuinely missing files.
        return False
    except OSError:
        # Access errors and other uncertain results remain fail-closed.
        return True


def normalize_relpath(value: Any) -> str:
    """Validate and canonicalize a browser-supplied POSIX relative path."""
    if not isinstance(value, str) or not value or "\x00" in value:
        raise PathSafetyError("Invalid file path.")
    if "\\" in value:
        # The app emits POSIX-style relative paths even on Windows. Rejecting
        # backslashes avoids alternate-separator traversal tricks.
        raise PathSafetyError("Invalid path separator.")

    rel = PurePosixPath(value)
    if rel.is_absolute() or not rel.parts or any(part in {".", ".."} for part in rel.parts):
        raise PathSafetyError("Path must stay inside the configured root.")
    if len(rel.parts) > 1 and len(rel.parts[0]) == 2 and rel.parts[0][0].isalpha() and rel.parts[0][1] == ":":
        # Windows drive-qualified paths must not become relative names on POSIX.
        raise PathSafetyError("Windows drive path is not allowed.")
    if os.name == "nt" and any(":" in part for part in rel.parts):
        # Avoid Windows drive/alternate-data-stream syntax. Colons are valid
        # ordinary filename characters on POSIX filesystems.
        raise PathSafetyError("Invalid path component.")

    normalized = rel.as_posix()
    if normalized != value:
        # Prevent ambiguous aliases such as repeated separators or trailing '/'.
        raise PathSafetyError("Path is not in canonical form.")
    return normalized


def resolve_target(relpath: Any, *, must_exist: bool = True) -> tuple[Path, str]:
    """Resolve relpath and prove the final target remains inside STATE['root'].

    Path.resolve() follows symlinks/junction-like filesystem aliases. The final
    resolved target is then checked with relative_to(), so a path that looks
    lexically safe but resolves outside the configured root is rejected.
    """
    root_value = STATE.get("root")
    if not root_value:
        raise PathSafetyError("Viewer root is not initialized.")

    normalized = normalize_relpath(relpath)
    root = Path(root_value).resolve(strict=True)
    candidate = root.joinpath(*PurePosixPath(normalized).parts)

    try:
        resolved = candidate.resolve(strict=must_exist)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise PathSafetyError(f"Could not resolve path safely: {exc}") from exc

    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise PathSafetyError("Resolved path escapes the configured root.") from exc

    if must_exist and not resolved.is_file():
        raise FileNotFoundError(normalized)

    return resolved, normalized


def _confirmed_absent_after_scan(root: Path, relpath: str) -> bool:
    """Prune old skip history only when the original path is demonstrably gone.

    Skipped links/junctions, existing-but-not-enumerated files, and filesystem
    errors do not prove that the user removed a reviewed file. Checking only
    stale entries keeps this extra validation off the regular scan hot path.
    """
    candidate = root
    for part in PurePosixPath(relpath).parts:
        candidate = candidate / part
        if _is_link_like(candidate):
            return False
    try:
        candidate.lstat()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False


def _file_identity(path: Path) -> tuple[int, int, int, int]:
    """Cheap identity check for a file that was presented earlier in this run.

    This prevents ordinary replacements/edits from authorizing deletion of a
    different file at the same name. It does not close malicious OS-level races.
    """
    st = path.stat()
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns


def _still_presented_file(relpath: str, full: Path) -> bool:
    original = STATE["presented_identity"].get(relpath)
    return original is not None and original == _file_identity(full)


@contextmanager
def _progress_guard():
    """Serialize read/modify/write progress across app processes.

    File locks are advisory; all cooperating viewer instances use this lock.
    The file is not removed on exit, avoiding a delete/recreate lock race.
    """
    lock_path = PROGRESS_FILE.with_name(PROGRESS_FILE.name + ".lock")
    with PROGRESS_IO_LOCK:
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            with lock_path.open("a+b") as handle:
                if os.name == "nt":
                    import msvcrt

                    if handle.seek(0, os.SEEK_END) == 0:
                        handle.write(b"0")
                        handle.flush()
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                    try:
                        yield
                    finally:
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                    try:
                        yield
                    finally:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except ProgressFileError:
            raise
        except OSError as exc:
            raise ProgressFileError(f"Cannot lock progress file {PROGRESS_FILE}: {exc}") from exc


def _read_progress_unlocked() -> dict[str, Any]:
    """Read existing progress while the caller holds the cross-process lock."""
    try:
        if not PROGRESS_FILE.exists():
            return {}
        with PROGRESS_FILE.open("r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("Progress must be a JSON object.")
        return data
    except (ValueError, UnicodeError) as exc:
        # Never overwrite the sole copy of unreadable review history.
        try:
            backup = PROGRESS_FILE.with_name(PROGRESS_FILE.name + ".corrupt")
            suffix = 1
            while backup.exists():
                backup = PROGRESS_FILE.with_name(PROGRESS_FILE.name + f".corrupt.{suffix}")
                suffix += 1
            os.replace(PROGRESS_FILE, backup)
        except OSError as preserve_error:
            raise ProgressFileError(
                f"Cannot preserve damaged progress file {PROGRESS_FILE}: {preserve_error}"
            ) from preserve_error
        print(f"Warning: damaged progress preserved in {backup}: {exc}", flush=True)
        return {}
    except OSError as exc:
        raise ProgressFileError(
            f"Cannot read progress file {PROGRESS_FILE}: {exc}"
        ) from exc


def load_progress() -> dict[str, Any]:
    with _progress_guard():
        return _read_progress_unlocked()


def save_progress(all_progress: dict[str, Any]) -> None:
    """Persist only this viewer's root, retaining concurrent viewers' other roots.

    Hold a cross-process lock from fresh read through atomic replace; without
    both steps, two viewers started on different folders can silently clobber
    one another's progress. Same-root simultaneous review is not supported.
    """
    root_key = STATE.get("root")
    if not isinstance(root_key, str) or root_key not in all_progress:
        raise ProgressFileError("Review root is not initialized for progress save.")
    with _progress_guard():
        latest = _read_progress_unlocked()
        latest[root_key] = all_progress[root_key]
        temp_file = PROGRESS_FILE.with_name(PROGRESS_FILE.name + ".tmp")
        try:
            with temp_file.open("w", encoding="utf-8") as handle:
                json.dump(latest, handle, indent=2, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_file, PROGRESS_FILE)
        except OSError as exc:
            raise ProgressFileError(f"Cannot save progress file {PROGRESS_FILE}: {exc}") from exc


def get_file_type(relpath: str) -> str:
    ext = Path(relpath).suffix.lower()
    if ext in GIF_EXTS:
        return "gif"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in HTML_EXTS:
        return "html"
    return "unknown"


def _iter_supported_files(
    root: str | Path,
    progress_callback: Callable[[int, int], None] | None = None,
    *,
    progress_every: int = 1000,
    scan_error_callback: Callable[[Path, OSError], None] | None = None,
):
    """Yield supported paths incrementally without following symlinks/junctions.

    Unlike os.walk(), os.scandir() lets us report progress while a very large
    single directory is still being enumerated.
    """
    root_path = Path(root).resolve(strict=True)
    stack = [root_path]
    scanned_entries = 0
    supported = 0

    while stack:
        current_dir = stack.pop()
        child_dirs: list[Path] = []
        try:
            with os.scandir(current_dir) as entries:
                for entry in entries:
                    scanned_entries += 1
                    if progress_callback and progress_every > 0 and scanned_entries % progress_every == 0:
                        progress_callback(scanned_entries, supported)

                    name = entry.name
                    if name.startswith("."):
                        continue
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            child = Path(entry.path)
                            if _is_link_like(child):
                                continue
                            # On older Python versions, and if a directory is
                            # swapped between checks, never descend outside root.
                            try:
                                child.resolve(strict=True).relative_to(root_path)
                            except OSError as exc:
                                if scan_error_callback:
                                    scan_error_callback(child, exc)
                                continue
                            except ValueError:
                                continue
                            child_dirs.append(child)
                            continue

                        if not entry.is_file(follow_symlinks=False):
                            continue
                    except OSError as exc:
                        if scan_error_callback:
                            scan_error_callback(Path(entry.path), exc)
                        continue

                    if Path(name).suffix.lower() not in SUPPORTED_EXTS:
                        continue
                    try:
                        rel = Path(entry.path).relative_to(root_path).as_posix()
                        rel = normalize_relpath(rel)
                    except (ValueError, PathSafetyError):
                        continue
                    supported += 1
                    yield rel
        except OSError as exc:
            if scan_error_callback:
                scan_error_callback(current_dir, exc)
            print(f"Warning: could not scan {current_dir}: {exc}", flush=True)

        random.shuffle(child_dirs)
        stack.extend(child_dirs)

    if progress_callback:
        progress_callback(scanned_entries, supported)


def scan_folder(
    root: str | Path,
    progress_callback: Callable[[int, int], None] | None = None,
    *,
    progress_every: int = 1000,
) -> list[str]:
    """Synchronous scan used by tests and small-folder callers."""
    return list(_iter_supported_files(root, progress_callback, progress_every=progress_every))


def _prepare_state(root: str | Path, permanent_delete: bool) -> Path:
    root_path = Path(root).resolve(strict=True)
    if not root_path.is_dir():
        raise NotADirectoryError(root_path)

    all_progress = load_progress()
    root_key = str(root_path)
    reviewed = _sanitize_reviewed(all_progress.get(root_key, {}))
    all_progress[root_key] = reviewed

    with STATE_LOCK:
        STATE["root"] = root_key
        STATE["permanent_delete"] = bool(permanent_delete)
        STATE["pool"] = []
        STATE["current"] = None
        STATE["presented"] = set()
        STATE["presented_identity"] = {}
        STATE["known_files"] = set()
        STATE["reviewed"] = reviewed
        STATE["all_progress"] = all_progress
        STATE["total_count"] = len(reviewed)
        STATE["scan_complete"] = False
        STATE["scan_error"] = None
        STATE["scan_scanned_entries"] = 0
        STATE["scan_supported_files"] = 0
    return root_path


def _scan_into_state(root_path: Path, progress_callback: Callable[[int, int], None] | None = None) -> None:
    batch: list[str] = []
    BATCH_SIZE = 32
    scan_errors = 0

    def record_scan_error(path: Path, exc: OSError) -> None:
        nonlocal scan_errors
        scan_errors += 1
        if scan_errors <= 3:
            print(f"Warning: incomplete scan at {path}: {exc}", flush=True)

    def flush_batch() -> None:
        nonlocal batch
        if not batch:
            return
        # Preserve quick incremental discovery; api_next chooses randomly from
        # the entire discovered pool, not just from each latest shuffled batch.
        with STATE_LOCK:
            for relpath in batch:
                STATE["known_files"].add(relpath)
                existing = STATE["reviewed"].get(relpath)
                if existing and existing.get("status") == "skipped":
                    continue
                if existing and existing.get("status") == "deleted":
                    # A deleted path exists again, e.g. restored from Recycle Bin.
                    STATE["reviewed"].pop(relpath, None)
                    STATE["all_progress"].setdefault(STATE["root"], {}).pop(relpath, None)
                STATE["pool"].append(relpath)
            STATE["scan_supported_files"] = max(int(STATE["scan_supported_files"]), len(STATE["known_files"]))
            STATE["total_count"] = len(STATE["reviewed"]) + len(STATE["pool"]) + (1 if STATE["current"] else 0)
        batch = []

    try:
        for relpath in _iter_supported_files(
            root_path, progress_callback, progress_every=250,
            scan_error_callback=record_scan_error,
        ):
            batch.append(relpath)
            if len(batch) >= BATCH_SIZE:
                flush_batch()
        flush_batch()

        with STATE_LOCK:
            # An incomplete enumeration is not evidence that a missing entry
            # was removed. Even after a clean enumeration, link-like paths,
            # unreadable paths, and files missed in a filesystem race must
            # retain their review history.
            stale = [] if scan_errors else [
                rel for rel, entry in STATE["reviewed"].items()
                if entry.get("status") == "skipped"
                and rel not in STATE["known_files"]
                and _confirmed_absent_after_scan(root_path, rel)
            ]
            if stale:
                # Prepare and persist a copy before mutating live state. If
                # the write fails, the next Skip/Delete must not accidentally
                # commit an earlier cleanup which never reached disk.
                updated_reviewed = dict(STATE["reviewed"])
                for rel in stale:
                    updated_reviewed.pop(rel, None)
                updated_progress = dict(STATE["all_progress"])
                updated_progress[STATE["root"]] = updated_reviewed
                try:
                    save_progress(updated_progress)
                except OSError as exc:
                    STATE["scan_error"] = (
                        "Scan finished, but progress cleanup could not be saved. "
                        "Previous review history was preserved. "
                        f"Check the progress-file storage: {exc}"
                    )
                    print(f"Warning: could not reconcile progress: {exc}", flush=True)
                else:
                    STATE["reviewed"] = updated_reviewed
                    STATE["all_progress"] = updated_progress
            STATE["total_count"] = len(STATE["reviewed"]) + len(STATE["pool"]) + (1 if STATE["current"] else 0)
            if scan_errors:
                STATE["scan_error"] = (
                    f"Scan incomplete: {scan_errors} filesystem error(s). "
                    "Saved review history was preserved. Check folder access and restart."
                )
            STATE["scan_complete"] = True
    except Exception as exc:
        with STATE_LOCK:
            STATE["scan_error"] = str(exc)
            STATE["scan_complete"] = True
        print(f"Scan failed: {exc}", flush=True)


def start_background_scan(root_path: Path, progress_callback: Callable[[int, int], None] | None = None) -> threading.Thread:
    thread = threading.Thread(
        target=_scan_into_state,
        args=(root_path, progress_callback),
        name="random-file-viewer-scan",
        daemon=True,
    )
    thread.start()
    return thread

def _sanitize_reviewed(raw: Any) -> dict[str, dict[str, str]]:
    if not isinstance(raw, dict):
        return {}
    clean: dict[str, dict[str, str]] = {}
    for relpath, entry in raw.items():
        try:
            normalized = normalize_relpath(relpath)
        except PathSafetyError:
            continue
        if not isinstance(entry, dict) or entry.get("status") not in VALID_STATUSES:
            continue
        clean[normalized] = {
            "status": entry["status"],
            "timestamp": str(entry.get("timestamp", "")),
        }
    return clean


def init_state(
    root: str | Path,
    permanent_delete: bool,
    scan_progress: Callable[[int, int], None] | None = None,
) -> None:
    """Initialize synchronously. Kept for tests and small-folder use."""
    root_path = _prepare_state(root, permanent_delete)
    _scan_into_state(root_path, scan_progress)


def persist_status(relpath: str, status: str) -> None:
    if status not in VALID_STATUSES:
        raise ValueError(f"Unsupported status: {status}")

    entry = {"status": status, "timestamp": dt.datetime.now().astimezone().isoformat()}
    root_key = STATE["root"]
    all_progress = STATE["all_progress"]
    root_progress = all_progress.setdefault(root_key, {})
    previous_root_entry = root_progress.get(relpath)
    previous_reviewed_entry = STATE["reviewed"].get(relpath)
    root_progress[relpath] = entry
    STATE["reviewed"][relpath] = entry
    try:
        save_progress(all_progress)
    except Exception:
        # The HTTP operation reports a failed save. Restore the in-memory
        # record too, so status does not falsely claim a successful review.
        if previous_root_entry is None:
            root_progress.pop(relpath, None)
        else:
            root_progress[relpath] = previous_root_entry
        if previous_reviewed_entry is None:
            STATE["reviewed"].pop(relpath, None)
        else:
            STATE["reviewed"][relpath] = previous_reviewed_entry
        raise


def require_api_token() -> tuple[dict[str, Any], int] | None:
    supplied = request.headers.get("X-Review-Token", "")
    expected = str(STATE["api_token"])
    # compare_digest(str, str) raises TypeError for non-ASCII input. Compare
    # UTF-8 bytes so malformed headers are rejected instead of becoming 500s.
    if not supplied or not hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
        return {"ok": False, "error": "Invalid request token."}, 403
    return None


def _json_error(message: str, status: int):
    return jsonify({"ok": False, "error": message}), status


def _request_relpath(*, require_presented: bool) -> tuple[str | None, Any | None]:
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or "path" not in data:
        return None, _json_error("Missing JSON file path.", 400)

    try:
        normalized = normalize_relpath(data["path"])
    except PathSafetyError as exc:
        return None, _json_error(str(exc), 400)

    if normalized not in STATE["known_files"]:
        return None, _json_error("File is not part of this review set.", 403)
    if require_presented and normalized not in STATE["presented"]:
        return None, _json_error("File has not been presented in this session.", 409)
    return normalized, None


@app.before_request
def reject_untrusted_host():
    # Defense against DNS-rebinding/Host-header access to this local filesystem
    # service. The app itself only opens 127.0.0.1.
    hostname = request.host.split(":", 1)[0].strip("[]").lower()
    if hostname not in {"127.0.0.1", "localhost"}:
        return _json_error("Untrusted host.", 400)
    return None


@app.after_request
def add_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/")
def index():
    return render_template("index.html", api_token=STATE["api_token"])


@app.route("/api/status")
def api_status():
    with STATE_LOCK:
        skipped = sum(1 for item in STATE["reviewed"].values() if item["status"] == "skipped")
        deleted = sum(1 for item in STATE["reviewed"].values() if item["status"] == "deleted")
        reviewed_count = skipped + deleted
        total = int(STATE["total_count"])
        remaining = len(STATE["pool"]) + (1 if STATE["current"] else 0)
        permanent = bool(STATE["permanent_delete"])
        trash_available = send2trash is not None

        return jsonify(
            {
                "root": STATE["root"],
                "remaining_count": remaining,
                "pool_remaining": len(STATE["pool"]) + (1 if STATE["current"] else 0),
                "skipped_count": skipped,
                "deleted_count": deleted,
                "reviewed_count": reviewed_count,
                "total_count": total,
                "permanent_delete": permanent,
                "trash_available": trash_available,
                "delete_available": permanent or trash_available,
                "delete_mode": "permanent" if permanent else ("trash" if trash_available else "unavailable"),
                "scan_complete": bool(STATE["scan_complete"]),
                "scan_error": STATE["scan_error"],
                "scan_scanned_entries": int(STATE["scan_scanned_entries"]),
                "scan_supported_files": int(STATE["scan_supported_files"]),
            }
        )


@app.route("/api/next", methods=["POST"])
def api_next():
    token_error = require_api_token()
    if token_error:
        payload, status = token_error
        return jsonify(payload), status

    with STATE_LOCK:
        current = STATE["current"]
        if current:
            try:
                current_full, _ = resolve_target(current, must_exist=True)
                if current not in STATE["presented_identity"]:
                    STATE["presented_identity"][current] = _file_identity(current_full)
            except (FileNotFoundError, PathSafetyError):
                STATE["current"] = None
            else:
                return jsonify(
                    {
                        "done": False,
                        "path": current,
                        "type": get_file_type(current),
                        "url": url_for("serve_file", relpath=current),
                    }
                )

        while STATE["pool"]:
            # Swap with the last element for O(1) random selection. Popping
            # a shuffled 32-item batch from the end biases NTFS directory order.
            i = random.randrange(len(STATE["pool"]))
            STATE["pool"][i], STATE["pool"][-1] = STATE["pool"][-1], STATE["pool"][i]
            relpath = STATE["pool"].pop()
            try:
                full, _ = resolve_target(relpath, must_exist=True)
                identity = _file_identity(full)
            except (FileNotFoundError, PathSafetyError):
                # The file disappeared or became unsafe after the initial scan.
                STATE["known_files"].discard(relpath)
                continue

            STATE["current"] = relpath
            STATE["presented"].add(relpath)
            STATE["presented_identity"][relpath] = identity
            return jsonify(
                {
                    "done": False,
                    "path": relpath,
                    "type": get_file_type(relpath),
                    "url": url_for("serve_file", relpath=relpath),
                }
            )

        if STATE["scan_error"]:
            return jsonify({
                "ok": False,
                "error": str(STATE["scan_error"]),
                "scan_error": True,
            }), 500
        if not STATE["scan_complete"]:
            return jsonify({
                "done": False,
                "waiting": True,
                "scan_scanned_entries": STATE["scan_scanned_entries"],
                "scan_supported_files": STATE["scan_supported_files"],
            })
        return jsonify({"done": True, "waiting": False})


@app.route("/api/skip", methods=["POST"])
def api_skip():
    token_error = require_api_token()
    if token_error:
        payload, status = token_error
        return jsonify(payload), status

    with STATE_LOCK:
        relpath, error = _request_relpath(require_presented=True)
        if error:
            return error
        assert relpath is not None

        try:
            full, _ = resolve_target(relpath, must_exist=True)
            unchanged = _still_presented_file(relpath, full)
        except FileNotFoundError:
            return _json_error("File no longer exists.", 404)
        except PathSafetyError as exc:
            return _json_error(str(exc), 403)
        if not unchanged:
            return _json_error("File changed since presentation; restart the viewer before reviewing it.", 409)

        try:
            persist_status(relpath, "skipped")
        except OSError as exc:
            return _json_error(f"Could not save progress: {exc}", 500)

        if STATE["current"] == relpath:
            STATE["current"] = None
        return jsonify({"ok": True})


@app.route("/api/delete", methods=["POST"])
def api_delete():
    token_error = require_api_token()
    if token_error:
        payload, status = token_error
        return jsonify(payload), status

    with STATE_LOCK:
        relpath, error = _request_relpath(require_presented=True)
        if error:
            return error
        assert relpath is not None

        if not STATE["permanent_delete"] and send2trash is None:
            return _json_error(
                "Recycle Bin support is unavailable. Nothing was deleted. Install send2trash or restart with --permanent to explicitly allow permanent deletion.",
                503,
            )

        try:
            full, _ = resolve_target(relpath, must_exist=True)
            unchanged = _still_presented_file(relpath, full)
        except FileNotFoundError:
            return _json_error("File no longer exists.", 404)
        except PathSafetyError as exc:
            return _json_error(str(exc), 403)
        if not unchanged:
            return _json_error("File changed since presentation; restart the viewer before deleting it.", 409)

        try:
            if STATE["permanent_delete"]:
                os.remove(full)
            else:
                # Never fall back to os.remove() if Recycle Bin support fails.
                send2trash(str(full))
        except Exception as exc:  # send2trash raises platform-specific exceptions
            return _json_error(f"Delete failed; file was left in place: {exc}", 500)

        try:
            persist_status(relpath, "deleted")
        except OSError as exc:
            # The file operation succeeded but persistence failed. Report this
            # explicitly instead of claiming the full workflow succeeded.
            if STATE["current"] == relpath:
                STATE["current"] = None
            return jsonify({
                "ok": False,
                "error": f"File deleted, but progress could not be saved: {exc}",
                "file_deleted": True,
                "progress_saved": False,
            }), 500

        if STATE["current"] == relpath:
            STATE["current"] = None
        return jsonify({"ok": True})


@app.route("/file/<path:relpath>")
def serve_file(relpath: str):
    if request.headers.get("Sec-Fetch-Site", "").lower() == "cross-site":
        return _json_error("Cross-site file requests are not allowed.", 403)

    try:
        full, normalized = resolve_target(relpath, must_exist=True)
    except FileNotFoundError:
        return _json_error("File not found.", 404)
    except PathSafetyError as exc:
        return _json_error(str(exc), 403)

    with STATE_LOCK:
        if normalized not in STATE["known_files"]:
            return _json_error("File is not part of this review set.", 403)
        # Hold the lock until the file is opened, preventing a concurrent
        # review action from invalidating membership between check and serve.
        response = send_file(full, conditional=True)
    if Path(normalized).suffix.lower() in HTML_EXTS:
        # The opaque sandbox origin prevents reads from the parent, but alone
        # does NOT prevent outbound requests to other services on localhost.
        # Allow inline standalone HTML, never external network dependencies.
        response.headers["Content-Security-Policy"] = (
            "sandbox allow-scripts; default-src 'none'; "
            "script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
            "img-src data: blob:; media-src data: blob:; font-src data:; "
            "connect-src 'none'; form-action 'none'; base-uri 'none'; "
            "object-src 'none'; frame-src 'none'"
        )
    return response


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Randomly review supported media in a folder and safely decide what to keep or delete."
    )
    parser.add_argument("folder", help="Folder to review recursively")
    parser.add_argument("--port", type=int, default=5151, help="Local port (default: 5151)")
    parser.add_argument(
        "--permanent",
        action="store_true",
        help="Explicitly opt in to permanent deletion instead of using the Recycle Bin",
    )
    args = parser.parse_args()

    print(f"Review folder     : {Path(args.folder)}", flush=True)

    last_report = {"scanned": -1}

    def report_scan(scanned: int, supported: int) -> None:
        with STATE_LOCK:
            STATE["scan_scanned_entries"] = scanned
            STATE["scan_supported_files"] = max(int(STATE["scan_supported_files"]), supported)
        # Update browser-visible scan state frequently, but avoid flooding CMD.
        if scanned == last_report["scanned"]:
            return
        should_print = last_report["scanned"] < 0 or scanned - last_report["scanned"] >= 5000
        if should_print:
            last_report["scanned"] = scanned
            print(
                f"  scanned {scanned:,} entries; found {supported:,} supported media files",
                flush=True,
            )

    try:
        root_path = _prepare_state(args.folder, args.permanent)
    except ProgressFileError as exc:
        print(f"Error: could not load review progress: {exc}", flush=True)
        print("Existing review history was not overwritten. Check file permissions and backups.", flush=True)
        sys.exit(1)
    except (FileNotFoundError, NotADirectoryError, OSError) as exc:
        print(f"Error: invalid review folder: {exc}", flush=True)
        sys.exit(1)

    if not args.permanent and send2trash is None:
        print(
            "Warning: Recycle Bin support is unavailable because send2trash is not installed.\n"
            "Delete is DISABLED; files will not be permanently deleted as a fallback.\n"
            "Install it with: pip install send2trash"
        )
    elif args.permanent:
        print("WARNING: --permanent is enabled. Delete actions cannot be undone by this app.")

    print(f"Root folder      : {STATE['root']}", flush=True)
    print(f"Progress file    : {PROGRESS_FILE}", flush=True)
    print("Starting viewer now; the recursive scan will continue in the background...", flush=True)
    start_background_scan(root_path, report_scan)

    url = f"http://127.0.0.1:{args.port}"
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=args.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
