#!/usr/bin/env bash
# One-command start: creates venv + installs deps on first run, seeds DB, serves on 0.0.0.0:${PORT:-8000}
#   ./run.sh        foreground
#   ./run.sh --bg   background (logs -> server.log, pid -> server.pid); stop with ./stop.sh
set -e
cd "$(dirname "$0")"
command -v tesseract >/dev/null || { echo "Install tesseract first: sudo apt-get install -y tesseract-ocr"; exit 1; }
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt; }
[ -f menu_markup.db ] || .venv/bin/python seed.py
CMD=(.venv/bin/uvicorn app:app --host 0.0.0.0 --port "${PORT:-8000}")
if [ "$1" = "--bg" ]; then
  setsid nohup "${CMD[@]}" > server.log 2>&1 < /dev/null & echo $! > server.pid
  echo "Menu Markup running in background (pid $(cat server.pid)) on http://0.0.0.0:${PORT:-8000}"
else
  exec "${CMD[@]}"
fi
