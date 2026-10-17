$ErrorActionPreference = 'Stop'
if ([System.Environment]::OSVersion.Platform -ne 'Win32NT') { throw 'Build this installer on Windows x64.' }
Set-Location (Split-Path $PSScriptRoot -Parent)
python -m venv .build-venv
if ($LASTEXITCODE) { throw 'Python venv failed' }
$Python = Join-Path $PWD '.build-venv\Scripts\python.exe'
& $Python -m pip install -r packaging/build-requirements.txt
if ($LASTEXITCODE) { throw 'Dependency installation failed' }
& $Python -m unittest discover -s tests -v
if ($LASTEXITCODE) { throw 'Tests failed' }
& $Python -m compileall -q main.py config.py ai integration router services ui
if ($LASTEXITCODE) { throw 'Compilation failed' }
& $Python scripts/create_icon.py
if ($LASTEXITCODE) { throw 'Icon generation failed' }
& $Python -m PyInstaller packaging/Mote.spec --noconfirm --clean
if ($LASTEXITCODE) { throw 'PyInstaller failed' }
$env:QT_QPA_PLATFORM = 'offscreen'
$env:MOTE_SMOKE_REPORT = Join-Path $PWD 'dist\Mote-smoke-error.txt'
$smoke = Start-Process -FilePath "$PWD\dist\Mote\Mote.exe" -ArgumentList '--smoke-test' -PassThru
if (-not $smoke.WaitForExit(90000)) { $smoke.Kill(); throw 'Packaged application smoke test hung' }
if ($smoke.ExitCode -ne 0) {
    if (Test-Path $env:MOTE_SMOKE_REPORT) { Get-Content $env:MOTE_SMOKE_REPORT }
    throw "Packaged application smoke test failed: $($smoke.ExitCode)"
}
Remove-Item Env:MOTE_SMOKE_REPORT
Remove-Item Env:QT_QPA_PLATFORM
$ISCC = Get-Command ISCC.exe -ErrorAction SilentlyContinue
if ($ISCC) { $Compiler = $ISCC.Source } else { $Compiler = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" }
if (-not (Test-Path $Compiler)) { throw 'Install Inno Setup 6 (https://jrsoftware.org/isdl.php), then run this script again.' }
& $Compiler packaging/Mote.iss
if ($LASTEXITCODE) { throw 'Installer compilation failed' }
& $Python -m pip freeze | Out-File -Encoding utf8 dist/installer/build-dependencies.txt
Get-FileHash dist/installer/MoteSetup.exe -Algorithm SHA256 | Format-List
Write-Host 'Installer: dist\installer\MoteSetup.exe. Complete the native Windows release checklist before distributing.'
