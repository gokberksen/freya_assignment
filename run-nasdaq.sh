#!/bin/sh
set -eu
cd "$(dirname "$0")"
exec .venv/bin/python -m uvicorn nasdaq.app:app --host "${NASDAQ_BIND:-127.0.0.1}" --port "${NASDAQ_PORT:-8767}" --workers 1
