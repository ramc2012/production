<#
.SYNOPSIS
  Install or upgrade FlowTwin as a Windows service. Offline: everything comes from this folder.

.DESCRIPTION
  1. Installs a private Python 3.12 into <InstallDir>\python (only if not there yet).
  2. Creates <InstallDir>\venv and installs FlowTwin and its dependencies from .\wheels (no internet).
  3. Creates the data folder (default %ProgramData%\FlowTwin) and its settings file flowtwin.env.
  4. Registers the "FlowTwin" service (WinSW), running as NT AUTHORITY\LocalService, start = automatic.
  Running it again with a newer bundle upgrades the program; the data folder is never touched,
  and database migrations run when the service starts.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\install.ps1
  powershell -ExecutionPolicy Bypass -File .\install.ps1 -BindHost 0.0.0.0 -Port 8443 -TlsCert C:\certs\ggs.pem -TlsKey C:\certs\ggs.key -OpenFirewall
#>
[CmdletBinding()]
param(
    [string]$InstallDir = "$env:ProgramFiles\FlowTwin",
    [string]$DataDir = "$env:ProgramData\FlowTwin",
    [string]$BindHost = "127.0.0.1",        # 0.0.0.0 = reachable from the intranet
    [int]$Port = 8400,
    [string]$TlsCert = "",
    [string]$TlsKey = "",
    [string]$SiteCode = "GGS-1",
    [switch]$OpenFirewall
)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$svc = "FlowTwin"

function Step($t) { Write-Host "`n== $t" -ForegroundColor Cyan }
$me = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $me.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script from an elevated PowerShell (Run as administrator)."
}
if (-not (Test-Path "$here\wheels")) { throw "wheels folder not found next to install.ps1" }

# ---------------------------------------------------------------- stop an existing service (upgrade)
$existing = Get-Service -Name $svc -ErrorAction SilentlyContinue
if ($existing) {
    Step "Stopping the running FlowTwin service for the upgrade"
    if ($existing.Status -ne "Stopped") { Stop-Service $svc -Force; Start-Sleep -Seconds 3 }
}

# ---------------------------------------------------------------- 1. private Python
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
$py = Join-Path $InstallDir "python\python.exe"
if (-not (Test-Path $py)) {
    Step "Installing Python 3.12 for FlowTwin into $InstallDir\python"
    if (-not (Test-Path "$here\python-installer.exe")) { throw "python-installer.exe missing from the bundle" }
    $pyArgs = "/quiet InstallAllUsers=1 TargetDir=`"$InstallDir\python`" PrependPath=0 Include_launcher=0 " +
            "Include_test=0 Include_doc=0 Include_tcltk=0 Include_pip=1 Shortcuts=0 AssociateFiles=0"
    $p = Start-Process -FilePath "$here\python-installer.exe" -ArgumentList $pyArgs -Wait -PassThru
    if ($p.ExitCode -ne 0) { throw "Python installer failed (exit $($p.ExitCode))" }
}
& $py -c "import sys; assert sys.version_info >= (3, 10), sys.version" | Out-Null

# ---------------------------------------------------------------- 2. virtual environment + FlowTwin
$venv = Join-Path $InstallDir "venv"
if (-not (Test-Path "$venv\Scripts\python.exe")) {
    Step "Creating the program environment"
    & $py -m venv $venv
}
Step "Installing FlowTwin from the bundled wheels (offline)"
$wheel = Get-ChildItem "$here\wheels\flowtwin-*.whl" | Sort-Object Name | Select-Object -Last 1
& "$venv\Scripts\python.exe" -m pip install --no-index --find-links "$here\wheels" --upgrade --force-reinstall --no-deps $wheel.FullName
& "$venv\Scripts\python.exe" -m pip install --no-index --find-links "$here\wheels" "$($wheel.FullName)[app]" tzdata colorama
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

if (Test-Path "$here\sample") {
    Copy-Item -Recurse -Force "$here\sample" (Join-Path $InstallDir "sample")
}

# ---------------------------------------------------------------- 3. data folder and settings
Step "Preparing the data folder $DataDir"
foreach ($d in @($DataDir, "$DataDir\logs", "$DataDir\backups", "$DataDir\dropzone", "$DataDir\runs")) {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
}
$envFile = Join-Path $DataDir "flowtwin.env"
if (-not (Test-Path $envFile)) {
    $lines = @(
        "# FlowTwin settings. Restart the FlowTwin service after a change.",
        "FLOWTWIN_HOST=$BindHost",
        "FLOWTWIN_PORT=$Port",
        "FLOWTWIN_SITE_CODE=$SiteCode",
        "FLOWTWIN_SAMPLE_DIR=$InstallDir\sample",
        "# FLOWTWIN_DAILY_RUN_TIME=06:30",
        "# FLOWTWIN_BACKUP_KEEP=14"
    )
    if ($TlsCert) { $lines += "FLOWTWIN_TLS_CERT=$TlsCert"; $lines += "FLOWTWIN_TLS_KEY=$TlsKey" }
    $lines | Set-Content -Encoding UTF8 $envFile
} else {
    Write-Host "Keeping the existing $envFile"
}
# least privilege: the service account may change the data folder and only read the program
icacls $DataDir /grant "NT AUTHORITY\LocalService:(OI)(CI)M" /T /Q | Out-Null
icacls $InstallDir /grant "NT AUTHORITY\LocalService:(OI)(CI)RX" /T /Q | Out-Null
if ($TlsKey) { icacls $TlsKey /grant "NT AUTHORITY\LocalService:R" /Q | Out-Null }

# ---------------------------------------------------------------- 4. Windows service (WinSW)
Step "Registering the FlowTwin service"
$exe = Join-Path $InstallDir "FlowTwinService.exe"
$xml = Join-Path $InstallDir "FlowTwinService.xml"
Copy-Item -Force "$here\WinSW-x64.exe" $exe
(Get-Content "$here\flowtwin-service.xml" -Raw).
    Replace("{{VENV}}", $venv).Replace("{{DATA}}", $DataDir) | Set-Content -Encoding UTF8 $xml
if (-not $existing) {
    & $exe install
    if ($LASTEXITCODE -ne 0) { throw "service registration failed" }
} else {
    & $exe refresh
}
sc.exe config $svc obj= "NT AUTHORITY\LocalService" password= "" | Out-Null
sc.exe failure $svc reset= 86400 actions= restart/10000/restart/30000/restart/60000 | Out-Null

if ($OpenFirewall) {
    Step "Opening TCP $Port in Windows Firewall (domain/private profiles only)"
    Get-NetFirewallRule -DisplayName "FlowTwin" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    New-NetFirewallRule -DisplayName "FlowTwin" -Direction Inbound -Protocol TCP -LocalPort $Port `
        -Action Allow -Profile Domain,Private | Out-Null
}

Step "Starting FlowTwin"
Start-Service $svc
Start-Sleep -Seconds 6
$scheme = if ($TlsCert) { "https" } else { "http" }
$hostShown = if ($BindHost -eq "0.0.0.0") { $env:COMPUTERNAME } else { $BindHost }
Write-Host ""
Write-Host "FlowTwin is running:  ${scheme}://${hostShown}:$Port" -ForegroundColor Green
$pw = Join-Path $DataDir "initial_admin_password.txt"
if (Test-Path $pw) { Write-Host "First sign-in: user 'admin', password in $pw (delete the file afterwards)." }
Write-Host "Data folder: $DataDir   Logs: $DataDir\logs   Backups: $DataDir\backups"
