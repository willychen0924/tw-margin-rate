#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
cd "$SCRIPT_DIR"

TASK_PYTHON="${TW_MARGIN_PYTHON:-}"
if [[ -z "$TASK_PYTHON" ]]; then
  CODEX_PYTHON="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
  if [[ -x "$CODEX_PYTHON" ]]; then
    TASK_PYTHON="$CODEX_PYTHON"
  else
    TASK_PYTHON="$(command -v python3)"
  fi
fi

if ! "$TASK_PYTHON" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
  print -u2 "需要 Python 3.11 以上版本。請安裝 Python 或設定 TW_MARGIN_PYTHON。"
  exit 2
fi

TASK_DEPS="${TW_MARGIN_DEPS:-$HOME/Library/Caches/tw-margin-rate/python}"
if ! PYTHONPATH="$TASK_DEPS" "$TASK_PYTHON" -c 'import duckdb,lxml,numpy,pandas,pyarrow' 2>/dev/null; then
  mkdir -p "$TASK_DEPS"
  "$TASK_PYTHON" -m pip install --target "$TASK_DEPS" \
    'duckdb>=1.5,<2' 'lxml>=5,<7' 'numpy>=2,<3' 'pandas>=2.2,<3' 'pyarrow>=20,<26'
fi

export PYTHONPATH="$TASK_DEPS"
export PYTHONDONTWRITEBYTECODE=1
exec "$TASK_PYTHON" scripts/update_margin_rate.py "$@"
