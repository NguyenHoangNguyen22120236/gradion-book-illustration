#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -n "${BACKEND_PYTHON:-}" && -x "$BACKEND_PYTHON" ]]; then
  PYTHON="$BACKEND_PYTHON"
elif [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  PYTHON="$ROOT_DIR/.venv/bin/python"
elif [[ -x "$ROOT_DIR/.venv/Scripts/python.exe" ]]; then
  PYTHON="$ROOT_DIR/.venv/Scripts/python.exe"
else
  echo "Missing .venv. Create it and install backend/requirements-dev.txt first." >&2
  exit 1
fi

(
  cd "$ROOT_DIR/backend"
  "$PYTHON" -m pytest
)

(
  cd "$ROOT_DIR/frontend"
  npm test
)
