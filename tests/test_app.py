# Copyright (c) 2026 Adam Flores
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import app as viewer  # noqa: E402


class RandomFileViewerSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.base = Path(self.tempdir.name)
        self.root = self.base / "root"
        self.root.mkdir()
        self.progress = self.base / "progress.json"

        self.progress_patch = patch.object(viewer, "PROGRESS_FILE", self.progress)
        self.progress_patch.start()

        viewer.app.testing = True
        viewer.STATE["api_token"] = "test-token"
        self.client = viewer.app.test_client()
        self.headers = {
            "Content-Type": "application/json",
            "X-Review-Token": "test-token",
        }

    def tearDown(self):
        self.progress_patch.stop()
        self.tempdir.cleanup()

    def make_media(self, relpath="image.jpg", content=b"test"):
        target = self.root / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return target

    def init(self, permanent=False):
        viewer.init_state(self.root, permanent)

    def next_item(self):
        response = self.client.post("/api/next", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def test_windows_reparse_attribute_fallback_without_is_junction(self):
        # Path.is_junction was introduced only in Python 3.12. Simulate a
        # Windows 3.10 path reporting FILE_ATTRIBUTE_REPARSE_POINT via lstat.
        import stat
        from types import SimpleNamespace

        class OldWindowsPath:
            def is_symlink(self):
                return False

            def lstat(self):
                return SimpleNamespace(st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)

        with patch.object(viewer, "os", SimpleNamespace(name="nt")):
            self.assertTrue(viewer._is_link_like(OldWindowsPath()))

    def test_windows_reparse_fallback_distinguishes_missing_and_unreadable(self):
        # Reproduces the Windows RC7 regression without requiring Windows:
        # the fallback lstat raises FileNotFoundError for a deleted path.
        from types import SimpleNamespace

        class FailedWindowsPath:
            def __init__(self, error):
                self.error = error

            def is_symlink(self):
                return False

            def lstat(self):
                raise self.error

        with patch.object(viewer, "os", SimpleNamespace(name="nt")):
            self.assertFalse(viewer._is_link_like(FailedWindowsPath(FileNotFoundError())))
            self.assertTrue(viewer._is_link_like(FailedWindowsPath(PermissionError())))

    def test_random_selection_samples_whole_pool_not_latest_chunk(self):
        for index in range(40):
            self.make_media(f"file-{index:02}.jpg")
        self.init()
        with viewer.STATE_LOCK:
            viewer.STATE["pool"] = [f"file-{index:02}.jpg" for index in range(40)]
        # A random index of zero must choose the first path, not the end of
        # the latest batch. This distinguishes reservoir selection from LIFO.
        with patch.object(viewer.random, "randrange", return_value=0) as draw:
            first = self.next_item()
        self.assertEqual(first["path"], "file-00.jpg")
        draw.assert_called_once_with(40)

    def test_two_viewers_of_different_roots_do_not_overwrite_progress(self):
        other = self.base / "other-root"
        other.mkdir()
        (other / "other.jpg").write_bytes(b"other")
        self.make_media("first.jpg")
        # Simulate two processes which both loaded progress *before* either
        # saved. Their STATE dictionaries are independent snapshots.
        self.init()
        state_a = viewer.STATE["all_progress"]
        root_a = viewer.STATE["root"]
        viewer.init_state(other, False)
        viewer.persist_status("other.jpg", "skipped")
        viewer.STATE["root"] = root_a
        viewer.STATE["all_progress"] = state_a
        viewer.STATE["reviewed"] = state_a[root_a]
        viewer.persist_status("first.jpg", "skipped")
        on_disk = viewer.load_progress()
        self.assertEqual(on_disk[root_a]["first.jpg"]["status"], "skipped")
        self.assertEqual(on_disk[str(other.resolve())]["other.jpg"]["status"], "skipped")

    def test_non_ascii_request_token_is_forbidden_not_server_error(self):
        self.make_media()
        self.init()
        bad_headers = {**self.headers, "X-Review-Token": "wrong-é"}
        for endpoint in ("/api/next", "/api/skip", "/api/delete"):
            with self.subTest(endpoint=endpoint):
                response = self.client.post(endpoint, headers=bad_headers,
                                            json={"path": "image.jpg"})
                self.assertEqual(response.status_code, 403)

    def test_html_csp_restricts_outbound_network_and_forms(self):
        self.make_media("page.html", b"<script>console.log('offline')</script>")
        self.init()
        response = self.client.get("/file/page.html")
        try:
            self.assertEqual(response.status_code, 200)
            policy = response.headers["Content-Security-Policy"]
            for required in ("sandbox allow-scripts", "default-src 'none'",
                             "connect-src 'none'", "form-action 'none'",
                             "img-src data: blob:", "script-src 'unsafe-inline'"):
                with self.subTest(directive=required):
                    self.assertIn(required, policy)
        finally:
            response.close()

    @unittest.skipIf(sys.platform == "win32", "Windows forbids colon filenames")
    def test_posix_colon_filename_is_scanned(self):
        self.make_media("capture:12.jpg")
        self.init()
        self.assertEqual(self.next_item()["path"], "capture:12.jpg")

    def test_failed_skip_progress_write_rolls_back_in_memory_status(self):
        self.make_media("image.jpg")
        self.init()
        self.next_item()
        with patch.object(viewer, "save_progress", side_effect=OSError("disk full")):
            response = self.client.post("/api/skip", headers=self.headers,
                                        json={"path": "image.jpg"})
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("image.jpg", viewer.STATE["reviewed"])
        self.assertNotIn("image.jpg", viewer.STATE["all_progress"][str(self.root.resolve())])
        self.assertEqual(self.next_item()["path"], "image.jpg")
        self.assertTrue((self.root / "image.jpg").exists())

    def test_successful_trash_move_with_failed_progress_save_reports_partial_success(self):
        target = self.make_media("image.jpg")
        self.init()
        self.next_item()
        moved = []

        def fake_trash(path):
            moved.append(path)
            Path(path).unlink()

        with patch.object(viewer, "send2trash", side_effect=fake_trash), \
             patch.object(viewer, "save_progress", side_effect=OSError("disk full")):
            response = self.client.post("/api/delete", headers=self.headers,
                                        json={"path": "image.jpg"})

        self.assertEqual(response.status_code, 500)
        payload = response.get_json()
        self.assertIs(payload["file_deleted"], True)
        self.assertIs(payload["progress_saved"], False)
        self.assertEqual(moved, [str(target.resolve())])
        self.assertFalse(target.exists())
        self.assertIsNone(viewer.STATE["current"])
        self.assertNotIn("image.jpg", viewer.STATE["reviewed"])
        # A repeat cannot delete an unseen replacement or report success.
        repeated = self.client.post("/api/delete", headers=self.headers,
                                    json={"path": "image.jpg"})
        self.assertEqual(repeated.status_code, 404)

    def test_canonical_containment_rejects_resolved_escape_without_symlinks(self):
        """Mock resolution, so this still runs on Windows without symlink privilege."""
        target = self.make_media("inside.jpg")
        outside = self.base / "outside.jpg"
        outside.write_bytes(b"private")
        self.init(permanent=True)
        self.next_item()
        original_resolve = Path.resolve

        def fake_resolve(path, *, strict=False):
            if path == target:
                return outside
            return original_resolve(path, strict=strict)

        with patch.object(Path, "resolve", autospec=True, side_effect=fake_resolve):
            with self.assertRaisesRegex(viewer.PathSafetyError, "escapes"):
                viewer.resolve_target("inside.jpg")
            response = self.client.post("/api/delete", headers=self.headers,
                                        json={"path": "inside.jpg"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(target.read_bytes(), b"test")
        self.assertEqual(outside.read_bytes(), b"private")

    def test_next_never_reports_done_if_scan_finishes_between_locks(self):
        """Fail if the endpoint releases its lock before examining scan state."""
        self.make_media("new.jpg")
        viewer._prepare_state(self.root, False)
        old_lock = viewer.STATE_LOCK

        class FinishScanAtFirstUnlock:
            triggered = False

            def __enter__(self):
                old_lock.acquire()
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                old_lock.release()
                if not self.triggered:
                    self.triggered = True
                    with old_lock:
                        viewer.STATE["known_files"].add("new.jpg")
                        viewer.STATE["pool"].append("new.jpg")
                        viewer.STATE["scan_complete"] = True

        with patch.object(viewer, "STATE_LOCK", FinishScanAtFirstUnlock()):
            first = self.client.post("/api/next", headers=self.headers)
        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.get_json().get("waiting"), first.get_json())
        self.assertFalse(first.get_json().get("done"), first.get_json())
        self.assertEqual(self.next_item()["path"], "new.jpg")

    def test_scan_end_progress_save_occurs_while_state_lock_is_held(self):
        # A recorded skipped file that no longer exists triggers scan-end save.
        self.progress.write_text(json.dumps({str(self.root.resolve()): {
            "missing.jpg": {"status": "skipped", "timestamp": "earlier"}
        }}), encoding="utf-8")
        old_lock = viewer.STATE_LOCK

        class LockSpy:
            held = 0

            def __enter__(self):
                old_lock.acquire()
                self.held += 1
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                self.held -= 1
                old_lock.release()

        lock_spy = LockSpy()
        actual_save = viewer.save_progress
        calls = []

        def asserted_save(progress):
            self.assertGreater(lock_spy.held, 0, "Progress saved outside STATE_LOCK")
            calls.append(True)
            actual_save(progress)

        with patch.object(viewer, "STATE_LOCK", lock_spy), \
             patch.object(viewer, "save_progress", side_effect=asserted_save):
            self.init()
        self.assertEqual(calls, [True])
        self.assertTrue(viewer.STATE["scan_complete"])
        self.assertIsNone(viewer.STATE["scan_error"])
        self.assertNotIn("missing.jpg", viewer.STATE["reviewed"])

    def test_skip_and_delete_reject_file_removed_from_known_set(self):
        self.make_media()
        self.init(permanent=True)
        self.next_item()
        viewer.STATE["known_files"].remove("image.jpg")
        for endpoint in ("/api/skip", "/api/delete"):
            with self.subTest(endpoint=endpoint):
                response = self.client.post(endpoint, headers=self.headers,
                                            json={"path": "image.jpg"})
                self.assertEqual(response.status_code, 403)
        self.assertTrue((self.root / "image.jpg").exists())
        self.assertNotIn("image.jpg", viewer.STATE["reviewed"])

    def test_progress_read_error_aborts_without_changing_existing_file(self):
        original = b'{"previous-review": {"safe.jpg": {"status": "skipped"}}}'
        self.progress.write_bytes(original)
        original_open = Path.open

        def deny_only_progress_file(path, *args, **kwargs):
            if path == self.progress:
                raise PermissionError("denied")
            return original_open(path, *args, **kwargs)

        with patch.object(Path, "open", autospec=True, side_effect=deny_only_progress_file):
            with self.assertRaisesRegex(viewer.ProgressFileError, "Cannot read progress"):
                viewer.load_progress()
        self.assertEqual(self.progress.read_bytes(), original)
        self.assertFalse(self.progress.with_name("progress.json.corrupt").exists())

    def test_corrupt_progress_cannot_be_renamed_aborts_without_overwrite(self):
        original = b"\xff\xfe"
        self.progress.write_bytes(original)
        with patch.object(viewer.os, "replace", side_effect=OSError("rename denied")):
            with self.assertRaisesRegex(viewer.ProgressFileError, "Cannot preserve damaged"):
                self.init()
        self.assertEqual(self.progress.read_bytes(), original)

    def test_startup_distinguishes_progress_error_from_invalid_folder(self):
        with patch.object(viewer.sys, "argv", ["app.py", str(self.root)]), \
             patch.object(viewer, "_prepare_state", side_effect=viewer.ProgressFileError("read denied")), \
             patch("builtins.print") as printed:
            with self.assertRaises(SystemExit) as exit_result:
                viewer.main()
        self.assertEqual(exit_result.exception.code, 1)
        self.assertTrue(any("could not load review progress" in str(call) for call in printed.call_args_list))
        self.assertFalse(any("invalid review folder" in str(call) for call in printed.call_args_list))

    def test_replaced_presented_file_is_not_permanently_deleted(self):
        original = self.make_media("image.jpg", b"original")
        self.init(permanent=True)
        self.next_item()
        original.unlink()
        original.write_bytes(b"different replacement")
        with patch.object(viewer.os, "remove") as permanent_remove:
            response = self.client.post("/api/delete", headers=self.headers,
                                        json={"path": "image.jpg"})
        self.assertEqual(response.status_code, 409)
        permanent_remove.assert_not_called()
        self.assertEqual(original.read_bytes(), b"different replacement")
        self.assertNotIn("image.jpg", viewer.STATE["reviewed"])

    def test_replaced_skipped_file_is_not_deleted_on_previous_item(self):
        original = self.make_media("image.jpg", b"original")
        self.init(permanent=True)
        self.next_item()
        self.assertEqual(self.client.post("/api/skip", headers=self.headers,
                           json={"path": "image.jpg"}).status_code, 200)
        original.unlink()
        original.write_bytes(b"new file not shown")
        with patch.object(viewer.os, "remove") as permanent_remove:
            response = self.client.post("/api/delete", headers=self.headers,
                                        json={"path": "image.jpg"})
        self.assertEqual(response.status_code, 409)
        permanent_remove.assert_not_called()
        self.assertEqual(original.read_bytes(), b"new file not shown")
        self.assertEqual(viewer.STATE["reviewed"]["image.jpg"]["status"], "skipped")

    def test_changed_file_cannot_be_marked_skipped_without_review(self):
        original = self.make_media("image.jpg", b"original")
        self.init()
        self.next_item()
        original.write_bytes(b"changed file of a different size")
        response = self.client.post("/api/skip", headers=self.headers,
                                    json={"path": "image.jpg"})
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("image.jpg", viewer.STATE["reviewed"])

    def test_failed_scan_cleanup_does_not_erase_history_on_next_save(self):
        """A failed cleanup write must not poison the next successful save."""
        self.make_media("new.jpg")
        root_key = str(self.root.resolve())
        old_entry = {"status": "skipped", "timestamp": "earlier"}
        self.progress.write_text(json.dumps({root_key: {"missing.jpg": old_entry}}), encoding="utf-8")
        with patch.object(viewer, "save_progress", side_effect=OSError("disk full")):
            self.init()
        self.assertEqual(viewer.STATE["reviewed"]["missing.jpg"], old_entry)
        self.assertEqual(viewer.STATE["all_progress"][root_key]["missing.jpg"], old_entry)
        self.assertIn("progress cleanup could not be saved", viewer.STATE["scan_error"])
        self.assertEqual(self.next_item()["path"], "new.jpg")
        response = self.client.post("/api/skip", headers=self.headers, json={"path": "new.jpg"})
        self.assertEqual(response.status_code, 200)
        saved = json.loads(self.progress.read_text(encoding="utf-8"))[root_key]
        self.assertIn("missing.jpg", saved)
        self.assertEqual(saved["new.jpg"]["status"], "skipped")

    def test_clean_scan_preserves_reviewed_item_beneath_excluded_link(self):
        """When a reviewed directory is now a link, absence from scan is not deletion."""
        root_key = str(self.root.resolve())
        old_entry = {"status": "skipped", "timestamp": "earlier"}
        self.progress.write_text(json.dumps({root_key: {"sub/media.jpg": old_entry}}), encoding="utf-8")
        link = self.root / "sub"
        original_check = viewer._is_link_like

        def fake_link_check(path):
            return Path(path) == link or original_check(path)

        # Cross-platform: simulate a directory intentionally excluded by
        # the scanner without needing Windows symlink permissions.
        with patch.object(viewer, "_iter_supported_files", return_value=iter(())), \
             patch.object(viewer, "_is_link_like", side_effect=fake_link_check):
            self.init()
        self.assertEqual(viewer.STATE["reviewed"]["sub/media.jpg"], old_entry)
        self.assertEqual(json.loads(self.progress.read_text(encoding="utf-8"))[root_key], {"sub/media.jpg": old_entry})
        self.assertIsNone(viewer.STATE["scan_error"])

    def test_clean_scan_preserves_existing_unenumerated_file(self):
        """A file that appeared after enumeration should not lose its old skip."""
        self.make_media("late.jpg")
        root_key = str(self.root.resolve())
        old_entry = {"status": "skipped", "timestamp": "earlier"}
        self.progress.write_text(json.dumps({root_key: {"late.jpg": old_entry}}), encoding="utf-8")
        with patch.object(viewer, "_iter_supported_files", return_value=iter(())):
            self.init()
        self.assertEqual(viewer.STATE["reviewed"]["late.jpg"], old_entry)
        self.assertEqual(json.loads(self.progress.read_text(encoding="utf-8"))[root_key], {"late.jpg": old_entry})

    def test_unreadable_subfolder_preserves_skipped_history(self):
        """An incomplete scan must not erase history for files it couldn't enumerate."""
        inner = self.root / "sub"
        inner.mkdir()
        target = inner / "media.jpg"
        target.write_bytes(b"reviewed before")
        prior = {str(self.root.resolve()): {
            "sub/media.jpg": {"status": "skipped", "timestamp": "earlier"}
        }}
        self.progress.write_text(json.dumps(prior), encoding="utf-8")
        original_scandir = viewer.os.scandir

        def intermittent_failure(path):
            if Path(path) == inner:
                raise PermissionError("temporary access failure")
            return original_scandir(path)

        with patch.object(viewer.os, "scandir", side_effect=intermittent_failure):
            self.init()

        self.assertTrue(target.exists())
        self.assertEqual(viewer.STATE["reviewed"]["sub/media.jpg"]["status"], "skipped")
        self.assertEqual(json.loads(self.progress.read_text(encoding="utf-8")), prior)
        self.assertTrue(viewer.STATE["scan_complete"])
        self.assertIn("Scan incomplete", viewer.STATE["scan_error"])
        response = self.client.post("/api/next", headers=self.headers)
        self.assertEqual(response.status_code, 500)
        self.assertTrue(response.get_json()["scan_error"])

        # Once the scan can read the directory, it should retain the old skip.
        self.init()
        self.assertIsNone(viewer.STATE["scan_error"])
        self.assertEqual(viewer.STATE["pool"], [])
        self.assertIn("sub/media.jpg", viewer.STATE["reviewed"])

    def test_relative_traversal_is_rejected(self):
        self.make_media()
        self.init()
        with self.assertRaises(viewer.PathSafetyError):
            viewer.resolve_target("../outside.jpg")
        with self.assertRaises(viewer.PathSafetyError):
            viewer.resolve_target("folder/../../outside.jpg")
        with self.assertRaises(viewer.PathSafetyError):
            viewer.resolve_target("..\\outside.jpg")

    def test_absolute_and_windows_special_paths_are_rejected(self):
        self.make_media()
        self.init()
        paths = ["/etc/passwd", "C:/Windows/file.jpg"]
        if sys.platform == "win32":
            paths.append("image.jpg:stream")
        for path in paths:
            with self.subTest(path=path):
                with self.assertRaises(viewer.PathSafetyError):
                    viewer.resolve_target(path)

    def test_symlink_escape_is_rejected_and_not_scanned(self):
        outside = self.base / "outside.jpg"
        outside.write_bytes(b"outside")
        link = self.root / "linked.jpg"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks unavailable on this platform")

        self.init()
        self.assertNotIn("linked.jpg", viewer.STATE["known_files"])
        with self.assertRaises(viewer.PathSafetyError):
            viewer.resolve_target("linked.jpg")

    def test_delete_never_falls_back_when_trash_is_unavailable(self):
        target = self.make_media()
        self.init(permanent=False)
        item = self.next_item()
        self.assertEqual(item["path"], "image.jpg")

        with patch.object(viewer, "send2trash", None):
            response = self.client.post(
                "/api/delete",
                headers=self.headers,
                data=json.dumps({"path": "image.jpg"}),
            )

        self.assertEqual(response.status_code, 503)
        self.assertTrue(target.exists())
        self.assertNotIn("image.jpg", viewer.STATE["reviewed"])

    def test_permanent_delete_requires_explicit_mode(self):
        target = self.make_media()
        self.init(permanent=True)
        self.next_item()

        response = self.client.post(
            "/api/delete",
            headers=self.headers,
            data=json.dumps({"path": "image.jpg"}),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(target.exists())
        self.assertEqual(viewer.STATE["reviewed"]["image.jpg"]["status"], "deleted")

    def test_unpresented_file_cannot_be_deleted(self):
        first = self.make_media("a.jpg")
        second = self.make_media("b.jpg")
        self.init(permanent=True)
        presented = self.next_item()["path"]
        unpresented = "b.jpg" if presented == "a.jpg" else "a.jpg"

        response = self.client.post(
            "/api/delete",
            headers=self.headers,
            data=json.dumps({"path": unpresented}),
        )
        self.assertEqual(response.status_code, 409)
        self.assertTrue((self.root / unpresented).exists())
        self.assertTrue(first.exists() or second.exists())

    def test_next_is_protected_and_idempotent_until_action(self):
        self.make_media("a.jpg")
        self.make_media("b.jpg")
        self.init()

        no_token = self.client.post("/api/next")
        self.assertEqual(no_token.status_code, 403)

        first = self.next_item()
        second = self.next_item()
        self.assertEqual(first["path"], second["path"])

    def test_skip_is_persisted_atomically_and_next_advances(self):
        self.make_media("a.jpg")
        self.make_media("b.jpg")
        self.init()
        first = self.next_item()["path"]

        response = self.client.post(
            "/api/skip",
            headers=self.headers,
            data=json.dumps({"path": first}),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.progress.exists())
        self.assertFalse(self.progress.with_name(self.progress.name + ".tmp").exists())

        second = self.next_item()["path"]
        self.assertNotEqual(first, second)

    def test_media_route_rejects_traversal(self):
        self.make_media("inside.jpg")
        outside = self.base / "outside.jpg"
        outside.write_bytes(b"outside")
        self.init()

        response = self.client.get("/file/%2E%2E/outside.jpg")
        self.assertIn(response.status_code, {400, 403, 404})
        self.assertNotEqual(response.data, b"outside")

    def test_media_route_rejects_cross_site_requests(self):
        self.make_media("inside.jpg", b"inside")
        self.init()

        response = self.client.get(
            "/file/inside.jpg",
            headers={"Sec-Fetch-Site": "cross-site"},
        )
        self.assertEqual(response.status_code, 403)

    def test_html_is_served_with_sandbox_csp(self):
        self.make_media("page.html", b"<script>document.body.textContent='ok'</script>")
        self.init()

        response = self.client.get("/file/page.html")
        try:
            self.assertEqual(response.status_code, 200)
            self.assertIn("sandbox allow-scripts", response.headers.get("Content-Security-Policy", ""))
            self.assertIn("connect-src 'none'", response.headers.get("Content-Security-Policy", ""))
        finally:
            response.close()

    def test_untrusted_host_is_rejected(self):
        self.make_media()
        self.init()
        response = self.client.get("/api/status", headers={"Host": "evil.example"})
        self.assertEqual(response.status_code, 400)


    def test_trash_failure_never_falls_back_to_permanent_delete(self):
        target = self.make_media()
        self.init()
        self.next_item()
        with patch.object(viewer, "send2trash", side_effect=OSError("trash unavailable")) as trash, \
             patch.object(viewer.os, "remove") as permanent_remove:
            response = self.client.post("/api/delete", headers=self.headers,
                                        json={"path": "image.jpg"})
        self.assertEqual(response.status_code, 500)
        trash.assert_called_once_with(str(target.resolve()))
        permanent_remove.assert_not_called()
        self.assertTrue(target.exists())
        self.assertNotIn("image.jpg", viewer.STATE["reviewed"])

    def test_successful_trash_delete_calls_resolved_target_once(self):
        target = self.make_media()
        self.init()
        self.next_item()
        with patch.object(viewer, "send2trash") as trash:
            response = self.client.post("/api/delete", headers=self.headers,
                                        json={"path": "image.jpg"})
        self.assertEqual(response.status_code, 200)
        trash.assert_called_once_with(str(target.resolve()))
        self.assertEqual(viewer.STATE["reviewed"]["image.jpg"]["status"], "deleted")

    def test_skip_and_delete_reject_missing_or_bad_token(self):
        self.make_media()
        self.init(permanent=True)
        self.next_item()
        for endpoint in ("/api/skip", "/api/delete"):
            for headers in ({}, {"X-Review-Token": "wrong"}):
                with self.subTest(endpoint=endpoint, headers=headers):
                    response = self.client.post(endpoint, headers=headers,
                                                json={"path": "image.jpg"})
                    self.assertEqual(response.status_code, 403)
        self.assertTrue((self.root / "image.jpg").exists())

    def test_media_route_only_serves_scanned_files(self):
        self.make_media("image.jpg", b"image")
        self.make_media("notes.txt", b"secret")
        hidden = self.make_media(".hidden/secret.jpg", b"hidden")
        self.init()
        response = self.client.get("/file/image.jpg")
        try:
            self.assertEqual(response.status_code, 200)
        finally:
            response.close()
        self.assertEqual(self.client.get("/file/notes.txt").status_code, 403)
        self.assertEqual(self.client.get("/file/.hidden/secret.jpg").status_code, 403)
        self.assertTrue(hidden.exists())

    def test_bom_progress_preserves_existing_history(self):
        old = {"other-root": {"a.jpg": {"status": "skipped"}}}
        self.progress.write_bytes(b"\xef\xbb\xbf" + json.dumps(old).encode("utf-8"))
        self.init()
        self.assertIn("other-root", viewer.STATE["all_progress"])
        self.assertFalse(self.progress.with_name("progress.json.corrupt").exists())

    def test_invalid_progress_is_preserved_before_reset(self):
        self.progress.write_bytes(b"\xff\xfe\x00")
        self.init()
        backup = self.progress.with_name("progress.json.corrupt")
        self.assertEqual(backup.read_bytes(), b"\xff\xfe\x00")
        self.assertFalse(self.progress.exists())

    def test_non_object_progress_is_preserved(self):
        self.progress.write_text("[]", encoding="utf-8")
        self.init()
        self.assertEqual(self.progress.with_name("progress.json.corrupt").read_text(), "[]")

    def test_media_route_rejects_symlink_escape_even_when_known(self):
        outside = self.base / "outside.jpg"
        outside.write_bytes(b"private")
        self.make_media("inside.jpg")
        self.init()
        link = self.root / "inside.jpg"
        link.unlink()
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks unavailable")
        response = self.client.get("/file/inside.jpg")
        self.assertEqual(response.status_code, 403)
        self.assertNotEqual(response.data, b"private")


if __name__ == "__main__":
    unittest.main()
