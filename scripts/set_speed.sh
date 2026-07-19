#!/usr/bin/env bash
# Set the Booster physics simulator speed factor via its WebSocket API.
#
# Usage:
#   scripts/set_speed.sh          # defaults to 3.0
#   scripts/set_speed.sh 5.0      # set 5x
#   SIM_SPEED=2.5 scripts/set_speed.sh
#
# The physics WS server binds container port 8788 and is normally mapped to a
# random host port by Docker. This script auto-discovers the host port via
# `docker port my26v-k1 8788`. On this machine the actual speed cap is CPU-bound
# around 1.7-1.8x regardless of the factor you request; setting higher just
# lets the physics thread run flat-out.

set -euo pipefail

SPEED="${1:-${SIM_SPEED:-3.0}}"
CONTAINER="${BOOSTER_CONTAINER:-my26v-k1}"
INTERNAL_PORT="${PHYSICS_WS_PORT:-8788}"

PORT="$(docker port "$CONTAINER" "$INTERNAL_PORT" 2>/dev/null | head -1 | cut -d: -f2)"
if [[ -z "$PORT" ]]; then
    echo "error: no host port found for $CONTAINER:$INTERNAL_PORT — is the container running?" >&2
    exit 1
fi

python3 - "$PORT" "$SPEED" <<'PY'
import json, sys, websocket
port, speed = sys.argv[1], float(sys.argv[2])
ws = websocket.create_connection(f"ws://localhost:{port}", timeout=3)
ws.send(json.dumps({"type": "command", "command": "set_speed", "params": {"speed_factor": speed}}))
ws.close()
print(f"physics speed_factor set to {speed}x (host port {port})")
PY
