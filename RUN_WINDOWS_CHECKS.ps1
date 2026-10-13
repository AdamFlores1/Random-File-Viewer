# Copyright (c) 2026 Adam Flores
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# Run the 44 Python unit/integration tests in disposable test directories.
# Does NOT run the separate Windows-only OS validation helper used before release.
# Run from PowerShell within the project folder:
#   .\RUN_WINDOWS_CHECKS.ps1
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    Write-Error 'Missing .venv. Create it using: py -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt'
    exit 2
}
& $python -m unittest discover -s tests -v
exit $LASTEXITCODE
