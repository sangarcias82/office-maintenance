#!/usr/bin/env bash
# Build a clean project zip (no .git / venv / local DB / .env).
set -euo pipefail
APP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="$(cd "$APP_ROOT/.." && pwd)"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

mkdir -p "$STAGE/maintenances"
rsync -a \
  --exclude '.git' \
  --exclude 'venv' \
  --exclude '.venv' \
  --exclude '__pycache__' \
  --exclude '.pytest_cache' \
  --exclude '*.db' \
  --exclude '.env' \
  --exclude 'uploads/*' \
  --exclude 'tests/test_uploads' \
  --exclude 'tests/test_maintenance.db' \
  --exclude '.DS_Store' \
  "$APP_ROOT/" "$STAGE/maintenances/"

mkdir -p "$STAGE/maintenances/uploads"
touch "$STAGE/maintenances/uploads/.gitkeep"
chmod +x "$STAGE/maintenances/for_testing"

ZIP="$OUT_DIR/office-maintenance.zip"
rm -f "$ZIP"
(cd "$STAGE" && zip -r -X "$ZIP" maintenances \
  -x '*/.git/*' '*/venv/*' '*/.venv/*' '*/node_modules/*' \
  -x '*__pycache__*' '*/.pytest_cache/*' '*.db' '*/.env' '*.pyc' '*/.DS_Store')

echo "Wrote $ZIP"
unzip -l "$ZIP" | grep '\.git/' && echo "FAIL: .git present" && exit 1 || echo "OK: no .git"
ls -lh "$ZIP"
