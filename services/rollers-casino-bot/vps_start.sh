#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

PYTHON="${PYTHON_BIN:-python3}"
VENV_DIR="$PROJECT_DIR/.venv"

if ! command -v "$PYTHON" >/dev/null 2>&1 && [[ ! -x "$PYTHON" ]]; then
  echo "[setup] Python executable not found: $PYTHON" >&2
  exit 1
fi

echo "[setup] Using Python: $("$PYTHON" --version 2>&1)"

if ! "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
  echo "[setup] Python 3.11 or newer is required." >&2
  "$PYTHON" --version >&2 || true
  exit 1
fi

if [[ -x "$VENV_DIR/bin/python" ]] && ! "$VENV_DIR/bin/python" -c 'import sys; raise SystemExit(0 if sys.prefix != sys.base_prefix else 1)' >/dev/null 2>&1; then
  echo "[setup] Existing virtual environment is unusable; recreating it"
  rm -rf "$VENV_DIR"
fi

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  echo "[setup] Creating Python virtual environment at $VENV_DIR"
  if ! "$PYTHON" -m venv --without-pip "$VENV_DIR"; then
    echo "[setup] Could not create a virtual environment. Install the Python venv/ensurepip package and retry." >&2
    exit 1
  fi
fi

VENV_PYTHON="$VENV_DIR/bin/python"

if ! "$VENV_PYTHON" -m pip --version >/dev/null 2>&1; then
  echo "[setup] Bootstrapping pip inside the virtual environment"
  if ! "$VENV_PYTHON" -m ensurepip --upgrade >/dev/null 2>&1; then
    echo "[setup] Python was built without ensurepip; install python3-venv/python3-pip on the VPS and retry." >&2
    exit 1
  fi
fi

if [[ ! -f "$PROJECT_DIR/requirements.txt" ]]; then
  echo "[setup] requirements.txt was not found in $PROJECT_DIR" >&2
  exit 1
fi

# NexCloud's generic template can inject stale package names through
# PY_PACKAGES. Do not let that unrelated value affect this application;
# install only the dependencies declared by this repository.
unset PY_PACKAGES
export CASINO_NON_INTERACTIVE=1

echo "[setup] Installing dependencies from requirements.txt"
# Some hosting panels set pip's global `user = true` option. That cannot be
# used from a virtualenv, so explicitly keep the install inside this venv.
# Do not self-upgrade pip here: a VPS package mirror can make that unrelated
# network request block the bot before its real dependencies are installed.
"$VENV_PYTHON" -m pip install \
  --disable-pip-version-check \
  --no-user \
  --no-cache-dir \
  --retries 4 \
  --timeout 60 \
  --upgrade-strategy only-if-needed \
  -r requirements.txt

echo "[setup] Checking installed dependency metadata"
"$VENV_PYTHON" -m pip check

echo "[setup] Verifying imports"
"$VENV_PYTHON" - <<'PY'
import flask
import httpx
import qrcode
import requests
import telegram
from PIL import Image

print(f"[setup] Python dependency check passed ({telegram.__version__=})")
PY

if [[ -z "${TELEGRAM_BOT_TOKEN:-}" && -z "${BOT_TOKEN:-}" && ! -f "$PROJECT_DIR/bot.env" && ! -f "$PROJECT_DIR/.env" ]]; then
  echo "[startup] Missing Telegram token. Set TELEGRAM_BOT_TOKEN in the VPS variables panel or create bot.env from bot.env.example." >&2
  exit 78
fi

export PYTHON_BIN="$VENV_PYTHON"
exec bash "$PROJECT_DIR/run.sh"