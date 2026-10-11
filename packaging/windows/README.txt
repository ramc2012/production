FlowTwin - offline Windows installation
=======================================

Needs: Windows 10/11 or Windows Server 2016+, 64-bit, administrator rights. No internet, no Docker,
no Python required (a private Python is installed for FlowTwin only).

Install (or upgrade to this version)
  1. Unzip this folder on the server, e.g. C:\Install\FlowTwin-1.1.0-win64
  2. Open PowerShell as administrator in that folder and run:
         powershell -ExecutionPolicy Bypass -File .\install.ps1
     For intranet access over HTTPS with your certificate:
         powershell -ExecutionPolicy Bypass -File .\install.ps1 -BindHost 0.0.0.0 -Port 8443 `
             -TlsCert C:\certs\ggs.pem -TlsKey C:\certs\ggs.key -OpenFirewall
  3. Open the address it prints. First sign-in: user "admin"; the password is in
     %ProgramData%\FlowTwin\initial_admin_password.txt. You must change it; then delete the file.

Where things are
  Program        C:\Program Files\FlowTwin          (replaced on upgrade)
  Data           C:\ProgramData\FlowTwin            (never touched by install/uninstall)
     flowtwin.sqlite   database        backups\   nightly copies (14 kept)
     runs\             reports         dropzone\  drop CSV/Excel files here for automatic import
     logs\             service logs    flowtwin.env  settings (restart the service after editing)

Service
  services.msc -> "FlowTwin" (automatic start, restarts itself after a failure)
  PowerShell:  Restart-Service FlowTwin

Restore a backup
  Stop-Service FlowTwin
  $env:FLOWTWIN_DATA_DIR = "C:\ProgramData\FlowTwin"
  & "C:\Program Files\FlowTwin\venv\Scripts\flowtwin.exe" restore flowtwin-YYYYMMDD-HHMMSS.sqlite
  Start-Service FlowTwin
  (the database being replaced is kept as flowtwin.before-restore.sqlite)

Uninstall
  powershell -ExecutionPolicy Bypass -File .\uninstall.ps1      (data folder is kept)

Safety: FlowTwin only reads data. It has no way to write to a DCS, PLC, SCADA, OPC UA, Modbus or
historian system. The Level-5 "autonomous" loop runs on a plant simulator inside FlowTwin.
