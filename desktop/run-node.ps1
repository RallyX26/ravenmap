# SparrowMap camera node - launcher (Windows).
#
# Starts the camera-control UI (which owns the webcam and serves its video to
# the detector) and then the detector, which posts sightings to the public
# SparrowMap network. Installed by install-node-windows.ps1; also run at login
# and from the desktop shortcut.
#
# A self-hoster points this at their own hub by setting SPARROW_HUB before it
# runs (e.g. setx SPARROW_HUB http://localhost:8150). Everyone else contributes
# to https://map.sparrowmap.com with no configuration.

$ErrorActionPreference = 'Stop'
$App   = Split-Path $PSScriptRoot -Parent
$Py    = Join-Path $App '.venv\Scripts\python.exe'
$Place = Join-Path $App 'camctl\placement.json'
$Hub   = if ($env:SPARROW_HUB) { $env:SPARROW_HUB.TrimEnd('/') } else { 'https://map.sparrowmap.com' }

if (-not (Test-Path $Py)) {
  Write-Host "SparrowMap is not installed yet. Run install-node-windows.ps1 first." -ForegroundColor Yellow
  Read-Host "Press Enter to close"; exit 1
}

function Camctl-Up {
  try { (Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 'http://localhost:8160/').StatusCode -eq 200 }
  catch { $false }
}
function Enrolled {
  if (-not (Test-Path $Place)) { return $false }
  try { [bool]((Get-Content $Place -Raw | ConvertFrom-Json).enrolled) } catch { $false }
}

Write-Host ""
Write-Host "  SparrowMap camera node" -ForegroundColor Cyan
Write-Host "    posts to  $Hub"
Write-Host ""

# 1) Camera control: owns the webcam, re-serves it as MJPEG for the detector,
#    and hosts the aiming page. Start it first, or the detector opens the camera
#    first and the control page shows a black rectangle.
if (-not (Camctl-Up)) {
  Write-Host "  Starting camera control..."
  Start-Process -WindowStyle Minimized $Py -ArgumentList 'camctl\camctl.py' -WorkingDirectory $App | Out-Null
  for ($i = 0; $i -lt 40 -and -not (Camctl-Up); $i++) { Start-Sleep 1 }
}
if (-not (Camctl-Up)) {
  Write-Host "  Camera control did not start - is a webcam connected?" -ForegroundColor Yellow
  Read-Host "Press Enter to close"; exit 1
}

# 2) First run: aim the camera and draw the road. Saving enrolls the camera on
#    the network and writes its id + token to placement.json.
if (-not (Enrolled)) {
  Write-Host "  Opening the setup page. Aim your camera, draw the stretch of"
  Write-Host "  public road it watches, and click Save. Waiting for that..."
  Start-Process 'http://localhost:8160/'
  while (-not (Enrolled)) { Start-Sleep 3 }
  Write-Host "  Camera enrolled." -ForegroundColor Green
}

# 3) Run the detector against the hub. It reads the node id + token from the
#    placement file (never pasted here), and is restarted if it dies for a real
#    reason. Exit code 1 is the single-instance guard - do not loop on it.
while ($true) {
  $place = Get-Content $Place -Raw | ConvertFrom-Json
  Write-Host "  Detector running as $($place.node_id). Close this window to stop."
  & $Py 'detect\run_live.py' --post --visual --hub $Hub --node $place.node_id --token $place.token
  if ($LASTEXITCODE -eq 1) {
    Write-Host "  Another detector is already running for this camera. Close it first." -ForegroundColor Yellow
    Read-Host "Press Enter to close"; break
  }
  Write-Host "  Detector exited; restarting in 10s (close this window to stop)." -ForegroundColor Yellow
  Start-Sleep 10
}
