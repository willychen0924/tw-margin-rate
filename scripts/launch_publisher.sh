#!/bin/zsh
# Invoked by the native app, never opens Terminal. No model or Codex required.
set -eu
TASK_ROOT="${0:A:h:h}"
cd "$TASK_ROOT"
TASK_LOCAL="$HOME/Library/Application Support/tw-margin-rate"
mkdir -p "$TASK_LOCAL"
TASK_PYTHON="${TW_MARGIN_PYTHON:-}"
if [[ -z "$TASK_PYTHON" ]]; then
  for candidate in "$TASK_LOCAL/runtime/bin/python3" "$TASK_ROOT/.venv/bin/python" /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
    if [[ -x "$candidate" ]] && "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' 2>/dev/null; then
      TASK_PYTHON="$candidate"
      break
    fi
  done
fi
if [[ -z "$TASK_PYTHON" ]]; then
  print 'RESULT:已停止：此 Mac 需要 Python 3.11 以上，請先安裝 Python。'
  exit 1
fi
if ! "$TASK_PYTHON" -c 'import duckdb,lxml,numpy,pandas,pyarrow,requests,xlrd,playwright' 2>/dev/null; then
  print '首次準備必要套件，可能需要數分鐘…'
  if [[ ! -x "$TASK_LOCAL/runtime/bin/python3" ]]; then
    "$TASK_PYTHON" -m venv "$TASK_LOCAL/runtime"
  fi
  TASK_PYTHON="$TASK_LOCAL/runtime/bin/python3"
  "$TASK_PYTHON" -m pip install --disable-pip-version-check -r "$TASK_ROOT/config/publisher-requirements.txt"
fi
"$TASK_PYTHON" -m playwright install chromium
export PYTHONDONTWRITEBYTECODE=1
export PYTHONUNBUFFERED=1
unset PYTHONOPTIMIZE
exec "$TASK_PYTHON" "$TASK_ROOT/scripts/update_and_publish.py" "$@"
