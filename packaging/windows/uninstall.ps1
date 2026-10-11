<#
.SYNOPSIS
  Remove the FlowTwin service and program. The data folder (database, backups, reports) is KEPT.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\uninstall.ps1
#>
[CmdletBinding()]
param([string]$InstallDir = "$env:ProgramFiles\FlowTwin", [string]$DataDir = "$env:ProgramData\FlowTwin")
$ErrorActionPreference = "Stop"
$svc = "FlowTwin"
$exe = Join-Path $InstallDir "FlowTwinService.exe"
if (Get-Service -Name $svc -ErrorAction SilentlyContinue) {
    Stop-Service $svc -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 3
    if (Test-Path $exe) { & $exe uninstall } else { sc.exe delete $svc | Out-Null }
}
Get-NetFirewallRule -DisplayName "FlowTwin" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
if (Test-Path $InstallDir) { Remove-Item -Recurse -Force $InstallDir }
Write-Host "FlowTwin removed. Your data is still in $DataDir (database, backups, reports)." -ForegroundColor Green
Write-Host "Delete that folder yourself only if you are sure it is no longer needed."
