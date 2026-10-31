#!/bin/sh
set -eu

BASE="${SECRETARY_BASE:-/data}"
mkdir -p "$BASE"

# The data volume is persistent; refresh the code on every start without touching
# the token, approvals or JSONL data.
for file in dashboard.py dashboard.html config.py approval_store.py approval_mcp.py \
            approval_setup.py email_template.py bridge_client.py sync_fallbacks.py \
            monitor.py watchdog.py digest.py; do
  if [ -f "/app/$file" ]; then
    cp -f "/app/$file" "$BASE/$file"
    chmod 700 "$BASE/$file" 2>/dev/null || true
  fi
done
chmod 644 "$BASE/dashboard.html" 2>/dev/null || true

exec python3 "$BASE/dashboard.py"
