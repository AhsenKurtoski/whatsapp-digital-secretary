#!/usr/bin/env bash
# Installa la segreteria digitale accanto a OpenClaw (Linux/WSL2 con systemd utente).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE="${SECRETARY_BASE:-$HOME/.openclaw/secretary}"
CONFIG="${SECRETARY_CONFIG:-$HOME/.config/digital-secretary/config.json}"
OPENCLAW_CONFIG="${OPENCLAW_CONFIG:-$HOME/.openclaw/openclaw.json}"
UNIT_DIR="$HOME/.config/systemd/user"
DROPIN="$UNIT_DIR/openclaw-gateway.service.d"
PYTHON="${PYTHON:-$(command -v python3 || command -v python || true)}"

if [ -z "$PYTHON" ]; then
  echo "python3 non trovato. Installalo prima di continuare." >&2
  exit 1
fi
if ! "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
  echo "Python 3.11+ richiesto. Aggiorna python3 e riprova." >&2
  exit 1
fi

# Modalità isolata per test/debug: non tocca file, systemd o configurazione.
if [ "${1:-}" = "--check-compat" ]; then
  exec "$PYTHON" "$HERE/scripts/openclaw_compat.py" --install-plan
fi

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemd non disponibile: usa Docker o avvia i processi manualmente." >&2
  exit 1
fi
if [ ! -f "$CONFIG" ]; then
  echo "Configurazione assente. Esegui prima: python3 setup.py" >&2
  exit 1
fi
if [ ! -f "$OPENCLAW_CONFIG" ]; then
  echo "OpenClaw non configurato: esegui prima 'openclaw onboard'." >&2
  exit 1
fi

# Questi file sono necessari al funzionamento dell'installazione: se mancano,
# l'installer deve fermarsi subito invece di produrre un'installazione incompleta.
for required in remote_access.py gog-secretary; do
  if [ ! -f "$HERE/$required" ]; then
    echo "File richiesto mancante: $HERE/$required" >&2
    exit 1
  fi
done

# L'unità dashboard legge dashboard_port dal JSON. Usa lo stesso valore anche
# per i controlli HTTP finali, rispettando DASHBOARD_PORT se già impostata.
if [ -z "${DASHBOARD_PORT:-}" ]; then
  DASHBOARD_PORT="$(
    PYTHONPATH="$HERE" SECRETARY_CONFIG="$CONFIG" \
      "$PYTHON" -c 'import config; print(config.get("dashboard_port", 8787))'
  )"
fi

# Rileva versione e feature prima di installare: le versioni vecchie non
# conoscono --accept-capabilities o allowConversationAccess. Il probe non
# modifica la configurazione.
OC_PRESENT=0
OC_VERSION=""
OC_VERSION_OK=0
OC_ACCEPT_CAPABILITIES=0
OC_CONVERSATION_ACCESS=0
OC_INSTALL_ALLOWED=0
OC_REFUSE=0
OC_SET_CONVERSATION_ACCESS=0
OC_BURST_ONLY=0
OC_PLAN_WARNING=""
OC_MIN_VERSION="2026.4.22"
if command -v openclaw >/dev/null 2>&1; then
  if compat="$("$PYTHON" "$HERE/scripts/openclaw_compat.py" --shell 2>/dev/null)" && [ -n "$compat" ]; then
    eval "$compat"
  fi
fi
if [ "$OC_REFUSE" = 1 ]; then
  echo "${OC_PLAN_WARNING:-OpenClaw ${OC_VERSION:-sconosciuto} non supportato: serve >= ${OC_MIN_VERSION}.}" >&2
  exit 1
fi
if [ "$OC_INSTALL_ALLOWED" != 1 ]; then
  echo "${OC_PLAN_WARNING:-warning: compatibilità OpenClaw non disponibile; plugin saltato.}" >&2
elif [ "$OC_ACCEPT_CAPABILITIES" = 0 ]; then
  echo "warning: OpenClaw non conosce --accept-capabilities; tento l'installazione legacy. Aggiorna se richiede consenso." >&2
fi

mkdir -p "$BASE" "$UNIT_DIR" "$DROPIN"
chmod 700 "$BASE"

install -m 700 "$HERE/config.py" "$BASE/config.py"
install -m 700 "$HERE/dashboard.py" "$BASE/dashboard.py"
install -m 644 "$HERE/dashboard.html" "$BASE/dashboard.html"
install -m 644 "$HERE/i18n.json" "$BASE/i18n.json"
install -m 700 "$HERE/sync_fallbacks.py" "$BASE/sync_fallbacks.py"
install -m 700 "$HERE/watchdog.py" "$BASE/watchdog.py"
install -m 700 "$HERE/healthcheck.py" "$BASE/healthcheck.py"
install -m 700 "$HERE/monitor.py" "$BASE/monitor.py"
install -m 700 "$HERE/digest.py" "$BASE/digest.py"
install -m 700 "$HERE/approval_store.py" "$BASE/approval_store.py"
install -m 700 "$HERE/email_template.py" "$BASE/email_template.py"
install -m 700 "$HERE/quote_pdf.py" "$BASE/quote_pdf.py"
install -m 700 "$HERE/contacts.py" "$BASE/contacts.py"
install -m 700 "$HERE/reminders.py" "$BASE/reminders.py"
install -m 700 "$HERE/gateway_policy.py" "$BASE/gateway_policy.py"
install -m 700 "$HERE/approval_mcp.py" "$BASE/approval_mcp.py"
install -m 700 "$HERE/approval_setup.py" "$BASE/approval_setup.py"
install -m 700 "$HERE/doctor.py" "$BASE/doctor.py"
install -m 700 "$HERE/remote_access.py" "$BASE/remote_access.py"
# Moduli della piattaforma: dashboard, MCP e plugin li importano dalla copia
# installata, quindi devono essere presenti con i permessi privati.
for module in lead_intel memory cache identity quotas router webhooks stats \
              conversations gdpr backup live; do
  install -m 700 "$HERE/$module.py" "$BASE/$module.py"
done
mkdir -p "$BASE/plugins"
chmod 700 "$BASE/plugins"
rm -rf "$BASE/plugins/secretary-guard"
cp -r "$HERE/plugins/secretary-guard" "$BASE/plugins/secretary-guard"
chmod -R u+rwX,go-rwx "$BASE/plugins/secretary-guard"

install -m 700 "$HERE/google-bridge/bridge_client.py" "$BASE/bridge_client.py"
mkdir -p "$BASE/google-bridge"
chmod 700 "$BASE/google-bridge"
install -m 600 "$HERE/google-bridge/Code.gs" "$BASE/google-bridge/Code.gs"
install -m 600 "$HERE/google-bridge/appsscript.json" "$BASE/google-bridge/appsscript.json"
install -m 700 "$HERE/google-bridge/installa-bridge.sh" "$BASE/google-bridge/installa-bridge.sh"
install -m 600 "$HERE/google-bridge/Config.gs.example" "$BASE/google-bridge/Config.gs.example"
install -m 600 "$HERE/google-bridge/README.md" "$BASE/google-bridge/README.md"
mkdir -p "$HOME/.local/bin"
install -m 700 "$HERE/gog-secretary" "$HOME/.local/bin/gog-secretary"

for unit in openclaw-dashboard.service openclaw-watchdog.service openclaw-watchdog.timer \
            openclaw-secretary-monitor.service openclaw-secretary-monitor.timer \
            openclaw-secretary-digest.service openclaw-secretary-digest.timer \
            openclaw-secretary-health.service openclaw-secretary-health.timer \
            openclaw-secretary-reminders.service openclaw-secretary-reminders.timer; do
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
# Anti-raffica WhatsApp: unisce debounce e queue senza sovrascrivere le scelte
# già presenti nel file openclaw.json.
"$PYTHON" "$BASE/gateway_policy.py" || true
# La registrazione dei tool è obbligatoria: senza di essa il bot non può
# preparare preventivi o email. Un errore qui non va ignorato.
if ! "$PYTHON" "$BASE/approval_setup.py"; then
  echo "errore: registrazione dei tool di approvazione non riuscita. Il bot non potrebbe creare preventivi." >&2
  exit 1
fi
# Verifica il runtime MCP della copia installata: tools/list e motore PDF.
if ! "$PYTHON" "$BASE/approval_mcp.py" --selftest; then
  echo "errore: il server MCP installato non espone i tool. Rilancia: bash scripts/install.sh" >&2
  exit 1
fi

systemctl --user enable --now openclaw-dashboard.service
systemctl --user enable --now openclaw-watchdog.timer
systemctl --user enable --now openclaw-secretary-monitor.timer
systemctl --user enable --now openclaw-secretary-digest.timer
systemctl --user enable --now openclaw-secretary-health.timer
systemctl --user enable --now openclaw-secretary-reminders.timer
systemctl --user restart openclaw-dashboard.service

if [ "$OC_INSTALL_ALLOWED" = 1 ]; then
  INSTALL_ARGS=(--link "$BASE/plugins/secretary-guard" --force)
  ENABLE_ARGS=(secretary-guard)
  if [ "$OC_ACCEPT_CAPABILITIES" = 1 ]; then
    INSTALL_ARGS+=(--accept-capabilities)
    ENABLE_ARGS+=(--accept-capabilities)
  fi
  if ! openclaw plugins install "${INSTALL_ARGS[@]}" >/dev/null 2>&1; then
    echo "warning: plugin secretary-guard non installato; riesegui: openclaw plugins install ${INSTALL_ARGS[*]}" >&2
    if [ "$OC_ACCEPT_CAPABILITIES" = 0 ]; then
      echo "warning: questa versione non supporta --accept-capabilities; aggiorna a >= ${OC_MIN_VERSION} se l'installazione richiede consenso." >&2
    fi
  elif ! openclaw plugins enable "${ENABLE_ARGS[@]}" >/dev/null 2>&1; then
    echo "warning: plugin secretary-guard non abilitato; riesegui: openclaw plugins enable ${ENABLE_ARGS[*]}" >&2
  fi
  # agent_end osserva la fine del turno per rilasciare il retry dopo un errore.
  # before_dispatch non richiede questo permesso; senza di esso la guardia
  # inbound funziona ma un messaggio fallito resterebbe soppresso mentre è in coda.
  if [ "$OC_SET_CONVERSATION_ACCESS" = 1 ]; then
    if ! openclaw config set 'plugins.entries.secretary-guard.hooks.allowConversationAccess' true >/dev/null 2>&1; then
      echo "warning: permesso retry del plugin non impostato; esegui: openclaw config set 'plugins.entries.secretary-guard.hooks.allowConversationAccess' true" >&2
    fi
  elif [ "$OC_BURST_ONLY" = 1 ]; then
    echo "warning: questa versione non espone allowConversationAccess; burst attivo, retry disattivato. Aggiorna OpenClaw >= ${OC_MIN_VERSION}." >&2
  else
    echo "warning: permesso retry del plugin non disponibile; aggiorna OpenClaw >= ${OC_MIN_VERSION}." >&2
  fi
fi

# Riavvia il gateway per caricare i drop-in; è normale che WhatsApp impieghi
# qualche decina di secondi a riconnettersi.
systemctl --user restart openclaw-gateway.service 2>/dev/null || true

for _ in $(seq 1 30); do
  if curl -fsS -o /dev/null "http://127.0.0.1:$DASHBOARD_PORT/" 2>/dev/null; then break; fi
  sleep 1
done

if [ -f "$BASE/dashboard_token" ]; then
  token="$(tr -d '\r\n' < "$BASE/dashboard_token")"
  unauth="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$DASHBOARD_PORT/api/status" || true)"
  auth="$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $token" "http://127.0.0.1:$DASHBOARD_PORT/api/status" || true)"
  echo "dashboard: http=$unauth (senza token), authenticated=$auth"
fi

echo "dashboard: http://127.0.0.1:$DASHBOARD_PORT"
echo "token:     $BASE/dashboard_token"
echo
echo "Diagnosi installazione:" || true
"$PYTHON" "$BASE/doctor.py" --short || true
current_user="${USER:-$(id -un 2>/dev/null || true)}"
if command -v loginctl >/dev/null 2>&1 && [ -n "$current_user" ]; then
  linger="$(loginctl show-user "$current_user" -p Linger --value 2>/dev/null || true)"
  if [ "$linger" != "yes" ]; then
    echo "nota: per mantenere i servizi senza login esegui 'sudo loginctl enable-linger $current_user'" >&2
  fi
fi
