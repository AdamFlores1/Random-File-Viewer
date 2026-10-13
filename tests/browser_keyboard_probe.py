# Copyright (c) 2026 Adam Flores
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Optional real-Chromium regression for held Delete (requires Playwright + Chromium).

Runs entirely against simulated API responses and synthetic disposable filenames;
no Flask server, real filesystem deletion, or --permanent mode is used.

    python tests/browser_keyboard_probe.py

If Playwright cannot find Chromium, install a browser with `playwright install chromium`
or provide the executable path in RFV_CHROMIUM_PATH.
"""

import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

PAGE_HTML = (Path(__file__).resolve().parents[1] / "index.html").read_text(encoding="utf-8")
PAGE_HTML = PAGE_HTML.replace("{{ api_token|tojson }}", json.dumps("test-token"))
FILES = [f"disposable-{n:02}.jpg" for n in range(15)]


def run() -> None:
    with sync_playwright() as playwright:
        chromium_path = os.environ.get("RFV_CHROMIUM_PATH")
        if not chromium_path and Path("/usr/bin/chromium").exists():
            chromium_path = "/usr/bin/chromium"
        browser = playwright.chromium.launch(headless=True, executable_path=chromium_path)
        try:
            page = browser.new_page()
            # Install a controlled simulated API in about:blank before loading
            # the unmodified viewer script. No network calls or actual deletion.
            page.evaluate("""() => {
                window.fixture = {
                    pending: Array.from({length: 15}, (_, n) => `disposable-${String(n).padStart(2,'0')}.jpg`),
                    current: null,
                    deleted: []
                };
                window.fetch = async (url, options = {}) => {
                    const state = window.fixture;
                    let result;
                    let status = 200;
                    if (url === '/api/status') {
                        result = {
                            remaining_count: state.pending.length + Number(state.current !== null),
                            total_count: 15,
                            reviewed_count: state.deleted.length,
                            skipped_count: 0,
                            deleted_count: state.deleted.length,
                            scan_complete: true,
                            delete_mode: 'trash',
                            delete_available: true,
                        };
                    } else if (url === '/api/next') {
                        if (!state.current && state.pending.length) state.current = state.pending.shift();
                        result = state.current
                          ? {done: false, path: state.current, type: 'image', url: '/file/' + state.current}
                          : {done: true, waiting: false};
                    } else if (url === '/api/delete') {
                        const supplied = JSON.parse(options.body || '{}').path;
                        if (supplied !== state.current) {
                            status = 409;
                            result = {error: 'not current'};
                        } else {
                            state.deleted.push(supplied);
                            state.current = null;
                            result = {ok: true};
                        }
                    } else {
                        status = 404;
                        result = {error: 'unknown fake endpoint'};
                    }
                    return {ok: status === 200, status, json: async () => result};
                };
            }""")
            page.set_content(PAGE_HTML)
            page.wait_for_function("document.getElementById('fileName').textContent === 'disposable-00.jpg'")
            page.keyboard.down("Delete")
            page.wait_for_function("document.getElementById('fileName').textContent === 'disposable-01.jpg'")
            for _ in range(12):
                page.evaluate("document.dispatchEvent(new KeyboardEvent('keydown', "
                              "{key: 'Delete', repeat: true, bubbles: true}))")
            page.keyboard.up("Delete")
            page.wait_for_timeout(150)
            state = page.evaluate("window.fixture")
            assert state["deleted"] == ["disposable-00.jpg"], state["deleted"]
            assert len(state["pending"]) == 13, state
            print("PASS: one initial Delete keydown + 12 repeats produced exactly one delete")

            # Exercise the actual page Delete-button handler on a simulated
            # partial failure: the file was removed, but status persistence failed.
            page.evaluate("""() => {
                const underlyingFetch = window.fetch;
                window.fetch = async (url, options = {}) => {
                    const response = await underlyingFetch(url, options);
                    if (url !== '/api/delete') return response;
                    return {
                        ok: false, status: 500,
                        json: async () => ({
                            ok: false, file_deleted: true, progress_saved: false,
                            error: 'File deleted, but progress could not be saved: disk full'
                        })
                    };
                };
            }""")
            page.locator('#btnDelete').click()
            page.wait_for_function("document.getElementById('viewer').textContent.includes('progress could not be saved')")
            assert page.evaluate("window.fixture.deleted") == ['disposable-00.jpg', 'disposable-01.jpg']
            assert page.locator('#btnSkip').is_disabled()
            assert page.locator('#btnDelete').is_disabled()
            assert page.locator('#fileName').text_content() == 'disposable-01.jpg'
            print("PASS: Delete button reports deleted-but-unsaved, without offering a second delete")

            # Successful deletion followed by an unrelated status refresh
            # failure must never be described as a failed deletion.
            refresh_page = browser.new_page()
            refresh_page.evaluate("""() => {
                window.fixture = {current: 'fresh-01.jpg', deleted: false};
                window.fetch = async (url, options = {}) => {
                    const state = window.fixture;
                    if (url === '/api/status') {
                        if (state.deleted) return {
                            ok: false, status: 503,
                            json: async () => ({error: 'Status temporarily unavailable'})
                        };
                        return {ok: true, status: 200, json: async () => ({
                            total_count: 1, remaining_count: 1, reviewed_count: 0,
                            skipped_count: 0, deleted_count: 0, scan_complete: true,
                            delete_mode: 'trash', delete_available: true
                        })};
                    }
                    if (url === '/api/next') return {ok: true, status: 200,
                        json: async () => ({done: false, path: state.current,
                            type: 'image', url: '/file/' + state.current})};
                    if (url === '/api/delete') {
                        state.deleted = true;
                        return {ok: true, status: 200, json: async () => ({ok: true})};
                    }
                    return {ok: false, status: 404,
                        json: async () => ({error: 'Unknown endpoint'})};
                };
            }""")
            refresh_page.set_content(PAGE_HTML)
            refresh_page.wait_for_function("document.getElementById('fileName').textContent === 'fresh-01.jpg'")
            refresh_page.locator('#btnDelete').click()
            refresh_page.wait_for_function(
                "document.getElementById('viewer').textContent.includes('file was deleted successfully')")
            assert refresh_page.evaluate("window.fixture.deleted") is True
            assert refresh_page.locator('#btnSkip').is_disabled()
            assert refresh_page.locator('#btnDelete').is_disabled()
            assert 'Deleted, but the viewer could not refresh' in refresh_page.locator('#toast').text_content()
            print("PASS: successful Delete + failed status refresh does not claim deletion failed")

            # An incomplete filesystem scan must leave a visible, persistent
            # error instead of quietly claiming that every file was reviewed.
            scan_page = browser.new_page()
            scan_page.evaluate("""() => {
                window.fetch = async (url) => {
                    if (url === '/api/status') return {ok: true, status: 200,
                        json: async () => ({total_count: 0, remaining_count: 0,
                            reviewed_count: 0, skipped_count: 0, deleted_count: 0,
                            scan_complete: true, delete_mode: 'trash',
                            delete_available: true})};
                    if (url === '/api/next') return {ok: false, status: 500,
                        json: async () => ({scan_error: true,
                            error: 'Scan incomplete: 1 filesystem error(s). Saved review history was preserved.'})};
                    return {ok: false, status: 404,
                        json: async () => ({error: 'Unexpected endpoint'})};
                };
            }""")
            scan_page.set_content(PAGE_HTML)
            scan_page.wait_for_function(
                "document.getElementById('fileName').textContent === 'Scan incomplete'")
            assert 'Saved review history was preserved' in scan_page.locator('#viewer').text_content()
            assert scan_page.locator('#btnSkip').is_disabled()
            assert scan_page.locator('#btnDelete').is_disabled()
            print('PASS: incomplete scan is visible and blocks further review actions')

            # The permanent mode is an explicit CLI opt-in, and the browser
            # requires a separate confirmation before each destructive action.
            permanent_page = browser.new_page()
            permanent_page.evaluate("""() => {
                window.permanentFixture = { deleted: 0, confirmed: false, prompts: 0 };
                window.confirm = () => {
                    window.permanentFixture.prompts++;
                    return window.permanentFixture.confirmed;
                };
                window.fetch = async (url) => {
                    if (url === '/api/status') return {ok:true,status:200,json:async()=>({
                        total_count:1, remaining_count:1, reviewed_count:0,
                        skipped_count:0, deleted_count:0, scan_complete:true,
                        delete_mode:'permanent',delete_available:true
                    })};
                    if (url === '/api/next') return {ok:true,status:200,json:async()=>({
                        done:false,path:'danger.jpg',type:'image',url:'/file/danger.jpg'
                    })};
                    if (url === '/api/delete') {
                        window.permanentFixture.deleted++;
                        return {ok:true,status:200,json:async()=>({ok:true})};
                    }
                    return {ok:false,status:404,json:async()=>({error:'unknown'})};
                };
            }""")
            permanent_page.set_content(PAGE_HTML)
            permanent_page.wait_for_function("document.getElementById('fileName').textContent === 'danger.jpg'")
            permanent_page.locator('#btnDelete').click()
            assert permanent_page.evaluate('window.permanentFixture.deleted') == 0
            assert permanent_page.evaluate('window.permanentFixture.prompts') == 1
            permanent_page.evaluate('window.permanentFixture.confirmed = true')
            permanent_page.locator('#btnDelete').click()
            permanent_page.wait_for_function('window.permanentFixture.deleted === 1')
            print('PASS: permanent deletion requires positive confirmation')
        finally:
            browser.close()


if __name__ == "__main__":
    run()
