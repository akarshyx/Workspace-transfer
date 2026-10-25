#!/usr/bin/env bash
set -Eeuo pipefail

# Use this as the NexCloud/Pterodactyl startup command:
#   bash nexcloud_start.sh
#
# It intentionally does not consume the panel's PY_PACKAGES variable. The
# panel may contain stale package names from an older project configuration;
# requirements.txt in this repository is the single dependency source.

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

unset PY_PACKAGES
export CASINO_NON_INTERACTIVE=1
export PIP_DISABLE_PIP_VERSION_CHECK=1
export PYTHONUNBUFFERED=1

exec bash "$PROJECT_DIR/vps_start.sh"
