#!/usr/bin/env bash
# Build a clean project zip (no .git / venv / local DB / .env).
# Layout inside the zip mirrors the repo:
#   for_testing          (lives outside the app, next to maintenances/)
#   maintenances/...
set -euo pipefail
APP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="$(cd "$APP_ROOT/.." && pwd)"
RUNNER="$OUT_DIR/for_testing"
if [ ! -f "$RUNNER" ]; then
  echo "error: $RUNNER not found (for_testing must sit next to maintenances/)" >&2
  exit 1
fi
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
  --exclude 'for_testing' \
  "$APP_ROOT/" "$STAGE/maintenances/"

mkdir -p "$STAGE/maintenances/uploads"
touch "$STAGE/maintenances/uploads/.gitkeep"
cp "$RUNNER" "$STAGE/for_testing"
chmod +x "$STAGE/for_testing"

ZIP="$OUT_DIR/office-maintenance.zip"
rm -f "$ZIP"
(cd "$STAGE" && zip -r -X "$ZIP" for_testing maintenances \
  -x '*/.git/*' '*/venv/*' '*/.venv/*' '*/node_modules/*' \
  -x '*__pycache__*' '*/.pytest_cache/*' '*.db' '*/.env' '*.pyc' '*/.DS_Store')

echo "Wrote $ZIP"
unzip -l "$ZIP" | grep '\.git/' && echo "FAIL: .git present" && exit 1 || echo "OK: no .git"
ls -lh "$ZIP"
