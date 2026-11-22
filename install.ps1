<#
.SYNOPSIS
  Installs HyperHUD for the current user and makes it start with Windows.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\install.ps1
  powershell -ExecutionPolicy Bypass -File .\install.ps1 -Uninstall
#>
param([switch]$Uninstall)

$ErrorActionPreference = 'Stop'
$InstallDir   = Join-Path $env:LOCALAPPDATA 'HyperHUD'
$StartupLnk   = Join-Path ([Environment]::GetFolderPath('Startup')) 'HyperHUD.lnk'
$StartMenuLnk = Join-Path ([Environment]::GetFolderPath('Programs')) 'HyperHUD.lnk'

function Stop-HyperHUD {
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
        Where-Object { $_.CommandLine -like '*hyperhud.py*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
}

if ($Uninstall) {
    Stop-HyperHUD
    Remove-Item $StartupLnk, $StartMenuLnk -ErrorAction SilentlyContinue
    if (Test-Path $InstallDir) { Remove-Item $InstallDir -Recurse -Force }
    Write-Host 'HyperHUD uninstalled.'
    return
}

# Find pythonw.exe (Python with no console window).
$pythonw = $null
$cmd = Get-Command pythonw.exe -ErrorAction SilentlyContinue
if ($cmd -and $cmd.Source -notlike '*WindowsApps*') { $pythonw = $cmd.Source }
if (-not $pythonw -and (Get-Command py.exe -ErrorAction SilentlyContinue)) {
    $exe = & py.exe -3 -c 'import sys; print(sys.executable)'
    $candidate = Join-Path (Split-Path $exe) 'pythonw.exe'
    if (Test-Path $candidate) { $pythonw = $candidate }
}
if (-not $pythonw) {
    throw 'Python 3 was not found. Install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH") and run this again.'
}

Stop-HyperHUD
New-Item -ItemType Directory -Force $InstallDir | Out-Null
Copy-Item (Join-Path $PSScriptRoot 'hyperhud.py') $InstallDir -Force  # config.json is kept if present

$shell = New-Object -ComObject WScript.Shell
foreach ($path in $StartupLnk, $StartMenuLnk) {
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath       = $pythonw
    $lnk.Arguments        = '"' + (Join-Path $InstallDir 'hyperhud.py') + '"'
    $lnk.WorkingDirectory = $InstallDir
    $lnk.Description      = 'HyperHUD - Hyperliquid ticker overlay (Ctrl+Alt+H)'
    $lnk.Save()
}

Start-Process $StartupLnk
Write-Host "HyperHUD installed to $InstallDir and set to start with Windows."
Write-Host 'Ctrl+Alt+H shows/hides it. Settings: ' (Join-Path $InstallDir 'config.json')
