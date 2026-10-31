#!/usr/bin/env bash
# Attiva il bridge Google gratuito (senza dominio) per la segreteria digitale.
# Richiede un solo accesso Google nel browser; poi il bridge resta autorizzato.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BASE="${SECRETARY_BASE:-$HOME/.openclaw/secretary}"
TARGET="$BASE/google-bridge"
CONFIG="$BASE/google_bridge.json"
NODE_BIN="${NODE_BIN:-}"
if [ -z "$NODE_BIN" ]; then
  for candidate in "$HOME"/.nvm/versions/node/*/bin; do
    if [ -x "$candidate/npx" ]; then NODE_BIN="$candidate"; fi
  done
fi
if [ -n "$NODE_BIN" ] && [ -x "$NODE_BIN/npx" ]; then
  CLASP=("$NODE_BIN/npx" --yes @google/clasp)
else
  CLASP=(npx --yes @google/clasp)
fi

mkdir -p "$TARGET"
chmod 700 "$BASE" "$TARGET"
install -m 600 "$HERE/Code.gs" "$TARGET/Code.gs"
install -m 600 "$HERE/appsscript.json" "$TARGET/appsscript.json"
install -m 700 "$HERE/bridge_client.py" "$TARGET/bridge_client.py"
install -m 700 "$HERE/bridge_client.py" "$BASE/bridge_client.py"

if [ ! -f "$HOME/.clasprc.json" ]; then
  echo "Apro l'accesso Google: scegli l'account che gestisce l'attività e autorizza Apps Script."
  "${CLASP[@]}" login
fi

cd "$TARGET"

if [ ! -f ".clasp.json" ]; then
  echo "Creo il progetto Apps Script privato…"
  cp -f Code.gs /tmp/digital-secretary-bridge-Code.gs
  cp -f appsscript.json /tmp/digital-secretary-bridge-appsscript.json
  "${CLASP[@]}" create --type standalone --title "Digital Secretary Bridge" --rootDir .
  cp -f /tmp/digital-secretary-bridge-Code.gs Code.gs
  cp -f /tmp/digital-secretary-bridge-appsscript.json appsscript.json
  rm -f Code.js /tmp/digital-secretary-bridge-Code.gs /tmp/digital-secretary-bridge-appsscript.json
fi

if [ ! -s "Config.gs" ]; then
  umask 077
  python3 - <<'PY'
from pathlib import Path
import secrets
secret = secrets.token_urlsafe(36)
Path("Config.gs").write_text(
    "// File privato generato dall'installer. Non condividerlo.\n"
    "const BRIDGE_SECRET = '" + secret + "';\n",
    encoding="utf-8",
)
Path("Config.gs").chmod(0o600)
PY
fi

echo "Carico il codice su Apps Script…"
"${CLASP[@]}" push --force

echo "Creo la versione…"
VERSION_OUT="$("${CLASP[@]}" version "Bridge $(date +%F-%H%M)" 2>&1)" || {
  printf '%s\n' "$VERSION_OUT"
  echo "Creazione versione non riuscita. Apri il progetto con: ${CLASP[*]} open" >&2
  exit 1
}
printf '%s\n' "$VERSION_OUT"
VER="$(printf '%s\n' "$VERSION_OUT" | grep -oE '[0-9]+' | tail -1 || true)"
if [ -z "$VER" ]; then
  echo "Versione non rilevata; uso l'ultima disponibile." >&2
  VER="$("${CLASP[@]}" list-versions 2>/dev/null | grep -oE '[0-9]+' | tail -1 || true)"
fi

echo "Creo il deployment web…"
if [ -n "$VER" ]; then
  OUT="$("${CLASP[@]}" deploy --version "$VER" --description "Digital Secretary bridge $(date +%F)" 2>&1)" || {
    printf '%s\n' "$OUT"
    echo "Deployment non riuscito. Apri il progetto con: ${CLASP[*]} open" >&2
    exit 1
  }
else
  OUT="$("${CLASP[@]}" deploy --description "Digital Secretary bridge $(date +%F)" 2>&1)" || {
    printf '%s\n' "$OUT"
    echo "Deployment non riuscito. Apri il progetto con: ${CLASP[*]} open" >&2
    exit 1
  }
fi
printf '%s\n' "$OUT"
URL="$(printf '%s\n' "$OUT" | grep -oE 'https://script\.google\.com/macros/s/[A-Za-z0-9_-]+/exec' | tail -1 || true)"

if [ -z "$URL" ]; then
  echo "Non ho trovato l'URL nel deployment; recupero la lista…"
  DEPLOYMENTS="$("${CLASP[@]}" deployments 2>&1 || true)"
  URL="$(printf '%s\n' "$DEPLOYMENTS" | grep -oE 'https://script\.google\.com/macros/s/[A-Za-z0-9_-]+/exec' | tail -1 || true)"
fi

if [ -z "$URL" ]; then
  echo "Deployment creato, ma URL non trovato. Apri il progetto e copia l'URL /exec." >&2
  exit 1
fi

python3 - "$URL" "$CONFIG" <<'PY'
import sys
from pathlib import Path
url, config_path = sys.argv[1], sys.argv[2]
secret = ""
for line in Path("Config.gs").read_text(encoding="utf-8").splitlines():
    if "BRIDGE_SECRET" in line and "'" in line:
        secret = line.split("'", 2)[1]
        break
if len(secret) < 32:
    raise SystemExit("Config.gs non contiene un segreto valido")
import bridge_client
bridge_client.write_config(url, secret, config_path)
print("Configurazione scritta:", config_path)
PY

echo "Verifico il bridge…"
python3 bridge_client.py --config "$CONFIG" health || {
  echo "Il deployment esiste, ma la prima esecuzione richiede l'autorizzazione Google." >&2
  echo "Apri questo URL nel browser, premi Consenti, poi rilancia questo script:" >&2
  printf '%s\n' "$URL" >&2
  exit 1
}

echo "Bridge attivo. Gmail e Calendar ora usano Apps Script, senza dominio e senza scadenza di 7 giorni."
if [ -z "${SECRETARY_BASE:-}" ]; then
  systemctl --user restart openclaw-dashboard.service 2>/dev/null || true
  echo "Dashboard aggiornata. Apri http://127.0.0.1:8787 e premi Verifica bridge."
fi
