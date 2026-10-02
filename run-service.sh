#!/bin/bash
# Start the takeoff detection service with local env (API keys etc.)

# The job store lives OUTSIDE OneDrive, deliberately.
#
# It held 1.3 GB across 1,481 files inside the synced tree, and every
# detection writes a ~700 KB result plus page renders. OneDrive opens each
# one to upload it while the service still has it open, which is a Windows
# sharing violation - the "another app is using this file" errors, and the
# freeze that took the machine out once already. Same reasoning that put
# Postgres in C:/Users/kobys/takeoff-pg.
# core-redesign: its OWN job store and port, so it never touches the live
# product's jobs (detection-engine: takeoff-jobs, :8100) or the merge fork's
# (core-cutfill: core-cutfill-jobs, :8500). The ledger, accounts, branding
# and cut / fill projects all live under this directory.
export TAKEOFF_JOBS_DIR="${TAKEOFF_JOBS_DIR:-C:/Users/kobys/core-redesign-jobs}"
cd "$(dirname "$0")"
set -a; [ -f .env ] && source .env; set +a
# Tesseract (raster OCR): harness-launched shells can miss the user PATH
# entry, and ocr_words then fails silently ([]). Append it when present.
case ":$PATH:" in
  *"Tesseract-OCR"*) ;;
  *) [ -d "/c/Program Files/Tesseract-OCR" ] \
       && export PATH="$PATH:/c/Program Files/Tesseract-OCR" ;;
esac
# Prefer a local venv (Windows: .venv/Scripts, POSIX: .venv/bin), else PATH python3.
if [ -x .venv/Scripts/python.exe ]; then PY=.venv/Scripts/python.exe
elif [ -x .venv/bin/python ]; then PY=.venv/bin/python
else PY=python3; fi
exec "$PY" -m uvicorn server:app --port "${PORT:-8600}"
