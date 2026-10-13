# Copyright (c) 2026 Adam Flores
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Real Windows junction regression. No personal folders or files are accessed."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as viewer  # noqa: E402


@unittest.skipUnless(sys.platform == "win32", "Windows-only junction integration")
class WindowsJunctionIntegrationTests(unittest.TestCase):
    def test_real_junction_is_not_scanned_or_served(self):
        with tempfile.TemporaryDirectory(prefix="rfv-junction-test-") as td:
            base = Path(td)
            inside = base / "inside"
            outside = base / "outside"
            inside.mkdir()
            outside.mkdir()
            (inside / "allowed.png").write_bytes(b"test fixture")
            secret = outside / "outside.png"
            secret.write_bytes(b"must not be served")
            junction = inside / "junction"
            command = subprocess.run(
                ["cmd", "/d", "/c", "mklink", "/J", str(junction), str(outside)],
                capture_output=True, text=True,
            )
            self.assertEqual(command.returncode, 0, command.stdout + command.stderr)
            # This assertion specifically guards Python 3.10 where pathlib
            # Path.is_junction() does not exist.
            self.assertTrue(viewer._is_link_like(junction),
                            "Windows junction must be classified as link-like")
            self.assertNotIn("junction/outside.png", viewer.scan_folder(inside))
            previous_progress = viewer.PROGRESS_FILE
            try:
                viewer.PROGRESS_FILE = base / "progress.json"
                viewer.init_state(inside, False)
                with viewer.STATE_LOCK:
                    self.assertNotIn("junction/outside.png", viewer.STATE["known_files"])
                response = viewer.app.test_client().get("/file/junction/outside.png")
                self.assertIn(response.status_code, (403, 404))
                response.close()
                self.assertEqual(secret.read_bytes(), b"must not be served")
            finally:
                viewer.PROGRESS_FILE = previous_progress
                # Remove the junction as a link only. TemporaryDirectory cleanup
                # is otherwise left to the OS/Python once this block exits.
                if junction.exists() or junction.is_symlink():
                    junction.rmdir()


if __name__ == "__main__":
    unittest.main()
