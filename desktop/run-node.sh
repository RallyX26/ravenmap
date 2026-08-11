#!/usr/bin/env bash
# SparrowMap camera node - launcher (Linux / macOS).
#
# Starts the camera-control UI (which owns the webcam and serves its video to
# the detector) and then the detector, which posts sightings to the public
# SparrowMap network. Installed by install-node-linux.sh; also run by the
# systemd --user service and from the desktop entry.
#
# A self-hoster points this at their own hub by setting SPARROW_HUB before it
# runs (e.g. export SPARROW_HUB=http://localhost:8150). Everyone else
# contributes to https://map.sparrowmap.com with no configuration.

set -euo pipefail

SELF="$(cd "$(dirname "$0")" && pwd)"
APP="$(dirname "$SELF")"
PY="$APP/.venv/bin/python"
PLACE="$APP/camctl/placement.json"
HUB="${SPARROW_HUB:-https://map.sparrowmap.com}"
HUB="${HUB%/}"

if [ ! -x "$PY" ]; then
  echo "SparrowMap is not installed yet. Run install-node-linux.sh first." >&2
  exit 1
fi

open_url() {
  if command -v xdg-open >/dev/null 2>&1; then xdg-open "$1" >/dev/null 2>&1 || true
  elif command -v open >/dev/null 2>&1; then open "$1" >/dev/null 2>&1 || true
  else echo "  Open $1 in your browser."; fi
}
camctl_up() { curl -fsS -m 2 http://localhost:8160/ >/dev/null 2>&1; }
enrolled() {
  [ -f "$PLACE" ] || return 1
  "$PY" -c "import json,sys; sys.exit(0 if json.load(open('$PLACE')).get('enrolled') else 1)" 2>/dev/null
}

echo
echo "  SparrowMap camera node"
echo "    posts to  $HUB"
echo

# 1) Camera control first: it owns the webcam and re-serves it as MJPEG. If the
#    detector opens the camera first, the control page shows a black rectangle.
if ! camctl_up; then
  echo "  Starting camera control..."
  ( cd "$APP" && "$PY" camctl/camctl.py >/dev/null 2>&1 & )
  for _ in $(seq 1 40); do camctl_up && break; sleep 1; done
fi
if ! camctl_up; then
  echo "  Camera control did not start - is a webcam connected?" >&2
  exit 1
fi

# 2) First run: aim the camera and draw the road. Saving enrolls the camera on
#    the network and writes its id + token to placement.json.
if ! enrolled; then
  echo "  Opening the setup page. Aim your camera, draw the stretch of public"
  echo "  road it watches, and click Save. Waiting for that..."
  open_url 'http://localhost:8160/'
  until enrolled; do sleep 3; done
  echo "  Camera enrolled."
fi

# 3) Run the detector against the hub. It reads the node id + token from the
#    placement file (never pasted here) and is restarted if it dies for a real
#    reason. Exit code 1 is the single-instance guard - do not loop on it.
while true; do
  NODE="$("$PY" -c "import json;print(json.load(open('$PLACE'))['node_id'])")"
  TOKEN="$("$PY" -c "import json;print(json.load(open('$PLACE'))['token'])")"
  echo "  Detector running as $NODE. Ctrl-C to stop."
  set +e
  ( cd "$APP" && "$PY" detect/run_live.py --post --visual --hub "$HUB" --node "$NODE" --token "$TOKEN" )
  code=$?
  set -e
  if [ "$code" -eq 1 ]; then
    echo "  Another detector is already running for this camera. Close it first." >&2
    exit 1
  fi
  echo "  Detector exited; restarting in 10s (Ctrl-C to stop)."
  sleep 10
done
