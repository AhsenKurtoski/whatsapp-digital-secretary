#!/usr/bin/env bash
# Installa la segreteria digitale accanto a OpenClaw (Linux/WSL2 con systemd utente).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE="${SECRETARY_BASE:-$HOME/.openclaw/secretary}"
UNIT_DIR="$HOME/.config/systemd/user"
DROPIN="$UNIT_DIR/openclaw-gateway.service.d"
PYTHON="${PYTHON:-$(command -v python3 || true)}"

if [ -z "$PYTHON" ]; then
  echo "python3 non trovato. Installalo prima di continuare." >&2
  exit 1
fi
if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemd non disponibile: usa Docker o avvia i processi manualmente." >&2
  exit 1
fi
if [ ! -f "$HOME/.config/digital-secretary/config.json" ]; then
  echo "Configurazione assente. Esegui prima: python3 setup.py" >&2
  exit 1
fi
if [ ! -f "$HOME/.openclaw/openclaw.json" ]; then
  echo "OpenClaw non configurato: esegui prima 'openclaw onboard'." >&2
  exit 1
fi

mkdir -p "$BASE" "$UNIT_DIR" "$DROPIN"
chmod 700 "$BASE"

install -m 700 "$HERE/config.py" "$BASE/config.py"
install -m 700 "$HERE/dashboard.py" "$BASE/dashboard.py"
install -m 644 "$HERE/dashboard.html" "$BASE/dashboard.html"
install -m 700 "$HERE/sync_fallbacks.py" "$BASE/sync_fallbacks.py"
install -m 700 "$HERE/watchdog.py" "$BASE/watchdog.py"
install -m 700 "$HERE/monitor.py" "$BASE/monitor.py"
install -m 700 "$HERE/digest.py" "$BASE/digest.py"
install -m 700 "$HERE/approval_store.py" "$BASE/approval_store.py"
install -m 700 "$HERE/email_template.py" "$BASE/email_template.py"
install -m 700 "$HERE/approval_mcp.py" "$BASE/approval_mcp.py"
install -m 700 "$HERE/approval_setup.py" "$BASE/approval_setup.py"
install -d -m 700 "$BASE/plugins"
rm -rf "$BASE/plugins/secretary-guard"
cp -r "$HERE/plugins/secretary-guard" "$BASE/plugins/secretary-guard"
chmod -R u+rwX,go-rwx "$BASE/plugins/secretary-guard"

install -m 700 "$HERE/google-bridge/bridge_client.py" "$BASE/bridge_client.py"
install -d -m 700 "$BASE/google-bridge"
install -m 600 "$HERE/google-bridge/Code.gs" "$BASE/google-bridge/Code.gs"
install -m 600 "$HERE/google-bridge/appsscript.json" "$BASE/google-bridge/appsscript.json"
install -m 700 "$HERE/google-bridge/installa-bridge.sh" "$BASE/google-bridge/installa-bridge.sh"
install -m 600 "$HERE/google-bridge/Config.gs.example" "$BASE/google-bridge/Config.gs.example"
install -m 600 "$HERE/google-bridge/README.md" "$BASE/google-bridge/README.md"
install -m 700 "$HERE/gog-secretary" "$HOME/.local/bin/gog-secretary" 2>/dev/null || true

for unit in openclaw-dashboard.service openclaw-watchdog.service openclaw-watchdog.timer \
            openclaw-secretary-monitor.service openclaw-secretary-monitor.timer \
            openclaw-secretary-digest.service openclaw-secretary-digest.timer; do
  install -m 644 "$HERE/systemd/$unit" "$UNIT_DIR/$unit"
done
install -m 644 "$HERE/systemd/gateway-secrets.conf" "$DROPIN/secretary.conf"
install -m 644 "$HERE/systemd/dns.conf" "$DROPIN/20-dns.conf"

cat > "$DROPIN/timeout.conf" <<'EOF'
[Service]
TimeoutStartSec=180
EOF

systemctl --user daemon-reload

"$PYTHON" "$BASE/sync_fallbacks.py" || true
"$PYTHON" "$BASE/approval_setup.py" || true

systemctl --user enable --now openclaw-dashboard.service
systemctl --user enable --now openclaw-watchdog.timer
systemctl --user enable --now openclaw-secretary-monitor.timer
systemctl --user enable --now openclaw-secretary-digest.timer
systemctl --user restart openclaw-dashboard.service

if command -v openclaw >/dev/null 2>&1; then
  openclaw plugins install --link "$BASE/plugins/secretary-guard" --force >/dev/null 2>&1 || true
  openclaw plugins enable secretary-guard >/dev/null 2>&1 || true
fi

# Riavvia il gateway per caricare i drop-in; è normale che WhatsApp impieghi
# qualche decina di secondi a riconnettersi.
systemctl --user restart openclaw-gateway.service 2>/dev/null || true

for _ in $(seq 1 30); do
  if curl -fsS -o /dev/null "http://127.0.0.1:${DASHBOARD_PORT:-8787}/" 2>/dev/null; then break; fi
  sleep 1
done

if [ -f "$BASE/dashboard_token" ]; then
  token="$(tr -d '\r\n' < "$BASE/dashboard_token")"
  unauth="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${DASHBOARD_PORT:-8787}/api/status" || true)"
  auth="$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $token" "http://127.0.0.1:${DASHBOARD_PORT:-8787}/api/status" || true)"
  echo "dashboard: http=$unauth (senza token), authenticated=$auth"
fi

echo "dashboard: http://127.0.0.1:${DASHBOARD_PORT:-8787}"
echo "token:     $BASE/dashboard_token"
if command -v loginctl >/dev/null 2>&1; then
  linger="$(loginctl show-user "$USER" -p Linger --value 2>/dev/null || true)"
  if [ "$linger" != "yes" ]; then
    echo "nota: per mantenere i servizi senza login esegui 'sudo loginctl enable-linger $USER'" >&2
  fi
fi
