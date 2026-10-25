#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

if [[ ! -f "$PROJECT_DIR/main.py" ]]; then
  echo "[startup] main.py was not found in $PROJECT_DIR" >&2
  exit 1
fi

if [[ -n "${PYTHON_BIN:-}" ]]; then
  exec env PYTHONUNBUFFERED=1 "$PYTHON_BIN" main.py
fi

# A standard VPS install created by vps_start.sh.
if [[ -x "$PROJECT_DIR/.venv/bin/python" ]]; then
  exec env PYTHONUNBUFFERED=1 "$PROJECT_DIR/.venv/bin/python" main.py
fi

# Replit's project-local uv environment.
if [[ -x "$PROJECT_DIR/.pythonlibs/bin/python" ]]; then
  exec env PYTHONUNBUFFERED=1 "$PROJECT_DIR/.pythonlibs/bin/python" main.py
fi

# Replit's managed Python environment is one directory above this project.
if [[ -x "$PROJECT_DIR/../.pythonlibs/bin/python" ]]; then
  exec env PYTHONUNBUFFERED=1 "$PROJECT_DIR/../.pythonlibs/bin/python" main.py
fi

# On a VPS, uv uses this folder's pyproject.toml and lockfile. The lockfile
# may require a newer interpreter than the machine provides, so prefer the
# explicit venv above and only use uv when its Python is compatible.
if command -v uv >/dev/null 2>&1; then
  if uv run --no-sync python -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
    exec env PYTHONUNBUFFERED=1 uv run python main.py
  fi
fi

if command -v python3 >/dev/null 2>&1; then
  if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
    echo "Python 3.11 or newer is required. Found: $(python3 --version)" >&2
    exit 1
  fi
  exec env PYTHONUNBUFFERED=1 python3 main.py
fi

echo "Python 3.11 or newer is required. Install Python or set PYTHON_BIN." >&2
exit 1