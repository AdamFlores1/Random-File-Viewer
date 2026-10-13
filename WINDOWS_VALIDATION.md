# Windows validation using disposable files

**Validation update (2026-10-13):** RC8 passed on the owner's Windows 11 / Python 3.13 PC: **44 tests executed; 41 passed, 3 expected platform skips, zero failures/errors**. A separate, previously executed private Windows integration harness also completed **7/7 disposable-file checks, zero inconclusive**, including a real Recycle Bin move and outside-root junction rejection. That helper is **no longer distributed** after a heuristic antivirus alert. The launcher in this package runs the 44 Python tests only. The cross-platform GitHub Actions matrix passed on the corrected Windows test branch; media/GUI usability is not established by these fixtures.

## Windows test launcher (Python tests only)

With a `.venv` and installed `requirements.txt`, run this in the extracted project directory. It runs the standard Python suite, which uses temporary fixtures and mocks for deletion. **It does not run the removed Windows-only system-check helper or send anything to your Recycle Bin.**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\RUN_WINDOWS_CHECKS.ps1
```

This runs all **44 Python tests** (with platform-specific skips), including the Windows junction test. A previously run private system-check harness verified seven additional Windows behaviors on the owner's PC; those results remain documented above but the harness has been removed from this public package after a heuristic antivirus detection. **Failures in the shipped test suite should still be investigated before release.** To repeat real Recycle Bin behavior or interactive checks, use the disposable-media manual steps below.

Browser keyboard behavior has separately passed five Chromium mock-network checks, and the GitHub Actions browser job includes a real Flask+Chromium disposable-file flow.

Some checks are inherently not established by automation alone: actual Windows UI usability on your display, compatibility with your own media/video codecs, and screenshots suitable for public GitHub. Those are optional presentation/compatibility checks, not reasons to risk testing your personal files.

## 1. Run tests on your Windows PC

In PowerShell, from this project folder:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Expect 44 Python tests in this package. Record the total number run, failures/errors, and tests skipped. Windows symlink creation may require Developer Mode or privilege; a skipped real-symlink test is **not** the same as a pass. The mocked resolved-containment test is intended to run even without that privilege.

Optional browser keyboard regression (does not delete files):

```powershell
.\.venv\Scripts\python.exe -m pip install playwright
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe tests\browser_keyboard_probe.py
```

## 2. Generate a disposable review folder

**Do not run destructive tests on irreplaceable media.** This creates a unique folder under your Windows TEMP directory and fills it with 15 identical tiny PNG images. Keep the printed location for subsequent steps.

```powershell
$TestRoot = Join-Path $env:TEMP ("RandomFileViewerSmoke-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $TestRoot | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $TestRoot 'Nested') | Out-Null
$Png = [Convert]::FromBase64String('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl9GHQAAAAASUVORK5CYII=')
1..15 | ForEach-Object {
    $Folder = if ($_ % 2 -eq 0) { Join-Path $TestRoot 'Nested' } else { $TestRoot }
    [IO.File]::WriteAllBytes((Join-Path $Folder ("fixture-{0:00}.png" -f $_)), $Png)
}
Write-Host "TEST FOLDER: $TestRoot"
```

Start the viewer in its default **Recycle Bin mode** (do not use `--permanent`):

```powershell
.\.venv\Scripts\python.exe app.py "$TestRoot"
```

## 3. Browser checks with that disposable folder

- [ ] The browser opens on `http://127.0.0.1:5151/` and displays one fixture.
- [ ] Hold **Delete** briefly and confirm at most one image is removed per physical press (the counter and remaining files should agree).
- [ ] In a separate **disposable** folder and with `--permanent` explicitly enabled, ensure each Delete requires a confirmation prompt; Cancel must leave the file untouched. Never run this check on important files.
- [ ] Press **Skip** and confirm that image still exists in the disposable folder.
- [ ] Confirm Delete in default mode moves the selected image to the **Windows Recycle Bin**, not permanent deletion.
- [ ] Close/restart the app on the same test folder and confirm previously skipped files remain reviewed.
- [ ] Include a safe HTML file, a GIF, and a real video you own to confirm their preview behavior; some video codecs may not play in the browser.
- [ ] Using disposable files only, while a file is displayed, overwrite or replace it at the same relative path. Skip/Delete should refuse to act on the changed file and prompt you to restart; the replacement must remain untouched.
- [ ] With another fresh disposable test folder, confirm the app stays responsive while scanning a large number of directory entries. Record approximate scan rate and progress-save latency, but do not claim an unmeasured benchmark.
- [ ] Check that hidden files and unsupported file types are not served; confirm the app shows clear errors for inaccessible/unreadable progress data.
- [ ] Start two viewer processes on **different** disposable folders with distinct `--port` values; Skip one file in each and restart both. Confirm both folders retain their own review history in `review_progress.json`. Do not run two instances against the same folder.
- [ ] Test a standalone HTML sample with inline code and an attempted fetch/image/beacon request to a harmless local test endpoint. Verify its external requests are blocked and that offline-only HTML preview still works to the extent documented.
- [ ] Temporarily deny access to a throwaway subfolder with a previously skipped file; confirm the viewer reports an incomplete scan without forgetting that file's review history. Restore access and restart before continuing. (Avoid changing permissions on real folders.)
- [ ] Test a junction pointing outside the review root on Windows, using disposable directories, and confirm traversal is not performed. This is manual platform-specific verification, not covered by the cross-platform mocked test.

Do not automatically remove the test directory with a script until you have confirmed `$TestRoot` points to this disposable folder and only these files.

## 4. Public repository hygiene

From the **actual Git repository**, if one exists:

```powershell
git status --short
git ls-files -- review_progress.json review_progress.json.tmp 'review_progress.json.corrupt*'
git log --all -- review_progress.json
```

The ZIP in this package deliberately contains no progress JSON, corrupt backups, Git metadata, credentials, or machine-local files. If any progress files were committed in an earlier repository, inspect and sanitize the public history as appropriate; `.gitignore` alone does not remove committed history.

An authentic UI screenshot is optional and may be added after the first release. Set repository metadata when convenient. MPL 2.0 is selected and included in the release package. **RC8 has passed the automated Windows unit and live OS checks on Python 3.13**. The additional manual walkthrough below is optional usability/codec testing using disposable files, not a prerequisite to repeat the already-successful automated checks. The corrected Windows/Linux CI matrix passed on PR #1. Confirm the latest main-branch run before publishing a release.
