# Copyright (c) 2026 Adam Flores
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Headless Chromium against a *real* temporary Flask server and throwaway files.

Unlike browser_keyboard_probe.py, this exercises real HTTP endpoints. It
replaces send2trash *only* within this test so it does not populate the user's
Recycle Bin. Actual Windows Trash was validated separately on the owner's PC
before this public package; that extra system-level helper is not distributed.
"""
from __future__ import annotations

import base64
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as viewer  # noqa: E402

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl9GHQAAAAASUVORK5CYII="
)


def run() -> None:
    with tempfile.TemporaryDirectory(prefix="rfv-browser-live-") as td:
        base = Path(td)
        root, trash = base / "review", base / "fake-trash"
        root.mkdir()
        trash.mkdir()
        for index in range(8):
            (root / f"fixture-{index:02}.png").write_bytes(PNG)
        progress = base / "progress.json"
        moved: list[str] = []

        def only_disposable_trash(path: str) -> None:
            source = Path(path).resolve(strict=True)
            # The test cannot delete or move anything outside its own review dir.
            assert source.parent == root.resolve(), source
            shutil.move(str(source), str(trash / source.name))
            moved.append(source.name)

        with patch.object(viewer, "PROGRESS_FILE", progress), \
             patch.object(viewer, "send2trash", side_effect=only_disposable_trash):
            viewer.init_state(root, False)
            server = make_server("127.0.0.1", 0, viewer.app, threaded=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with sync_playwright() as p:
                    executable = os.environ.get("RFV_CHROMIUM_PATH")
                    browser = p.chromium.launch(
                        headless=True, executable_path=executable or None,
                        args=["--no-sandbox"],
                    )
                    try:
                        page = browser.new_page()
                        page.goto(f"http://127.0.0.1:{server.server_port}/")
                        page.wait_for_function("document.getElementById('fileName').textContent.startsWith('fixture-')")
                        first = page.locator("#fileName").inner_text()
                        page.locator("#btnSkip").click()
                        page.wait_for_function(
                            "previous => document.getElementById('fileName').textContent !== previous",
                            arg=first,
                        )
                        assert (root / first).exists(), "Skip removed a file"
                        second = page.locator("#fileName").inner_text()
                        assert second != first
                        # Synthetic repeated keydown events must not delete any file.
                        page.evaluate("""() => {
                            document.activeElement.blur();
                            for (let i = 0; i < 12; i++) document.dispatchEvent(
                                new KeyboardEvent('keydown', {key:'Delete', repeat:true, bubbles:true})
                            );
                        }""")
                        assert moved == [], f"Repeated key events caused deletion: {moved}"
                        # Normal Delete press must move precisely one disposable fixture.
                        page.keyboard.press("Delete")
                        page.wait_for_function(
                            "previous => document.getElementById('fileName').textContent !== previous",
                            arg=second,
                        )
                        assert moved == [second], moved
                        assert (trash / second).exists() and not (root / second).exists()
                        print("PASS: real Flask browser Skip + one Delete; repeats suppressed")
                        # A fresh app session must not offer the old skipped file.
                        viewer.init_state(root, False)
                        assert first in viewer.STATE["reviewed"]
                        assert viewer.STATE["reviewed"][first]["status"] == "skipped"
                        page.reload()
                        page.wait_for_function("document.getElementById('fileName').textContent.startsWith('fixture-')")
                        assert page.locator("#fileName").inner_text() != first
                        print("PASS: real Flask browser restart preserves Skip history")
                        page.close()
                    finally:
                        browser.close()
            finally:
                server.shutdown()
                thread.join(timeout=5)


if __name__ == "__main__":
    run()
