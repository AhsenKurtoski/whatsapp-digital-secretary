#!/usr/bin/env bash
# Installa la segreteria digitale su una VM Oracle Cloud Always Free.
#
# La dashboard resta in ascolto SOLO su 127.0.0.1: questo script non apre
# porte, non modifica le Security List di Oracle e non espone la dashboard.
# Ci si collega con un tunnel SSH (vedi docs/REMOTE_ACCESS.md).
#
# Uso:
#   bash scripts/install-oracle.sh --check      # sola lettura, nessuna modifica
#   bash scripts/install-oracle.sh --prepare    # dipendenze, Node, OpenClaw, repo, linger
#   bash scripts/install-oracle.sh --install --business-name "Studio Rossi" \
#        --owner-name "Mario Rossi" --owner-number +393331234567
#
# Opzioni: --yes (non chiede conferme), --repo-dir DIR, --port N,
#          --bot-name, --city, --hours, --timezone, --google-account, --services
set -uo pipefail

REPO_URL="https://github.com/AhsenKurtoski/whatsapp-digital-secretary.git"
REPO_DIR="${REPO_DIR:-$HOME/whatsapp-digital-secretary}"
MODE=""
ASSUME_YES=0
PORT="${DASHBOARD_PORT:-8787}"
BUSINESS_NAME=""
OWNER_NAME=""
OWNER_NUMBER=""
BOT_NAME="Gina"
CITY=""
HOURS=""
TIMEZONE="Europe/Rome"
GOOGLE_ACCOUNT=""
SERVICES=""

MIN_PYTHON="3.11"
MIN_NODE_MAJOR=22
MIN_OPENCLAW="2026.4.22"

fails=0
warns=0

log() { printf '\n▶ %s\n' "$*"; }
ok() { printf '  ✅ %s\n' "$*"; }
warn() { warns=$((warns + 1)); printf '  ⚠️  %s\n' "$*" >&2; }
fail() { fails=$((fails + 1)); printf '  ❌ %s\n' "$*" >&2; }

usage() {
  sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

confirm() {
  [ "$ASSUME_YES" = 1 ] && return 0
  [ -t 0 ] || return 0
  local answer
  printf '%s [s/N] ' "$1"
  read -r answer
  case "$answer" in s|S|si|Si|sì|Sì|y|Y|yes|YES) return 0 ;; *) return 1 ;; esac
}

version_ge() { # version_ge ATUALE MINIMA
  [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -n1)" = "$2" ]
}

while [ $# -gt 0 ]; do
  case "$1" in
    --check|--prepare|--install) MODE="${1#--}" ;;
    --yes|-y) ASSUME_YES=1 ;;
    --repo-dir) REPO_DIR="${2:?manca il valore di --repo-dir}"; shift ;;
    --port) PORT="${2:?manca il valore di --port}"; shift ;;
    --business-name) BUSINESS_NAME="${2:-}"; shift ;;
    --owner-name) OWNER_NAME="${2:-}"; shift ;;
    --owner-number) OWNER_NUMBER="${2:-}"; shift ;;
    --bot-name) BOT_NAME="${2:-}"; shift ;;
    --city) CITY="${2:-}"; shift ;;
    --hours) HOURS="${2:-}"; shift ;;
    --timezone) TIMEZONE="${2:-}"; shift ;;
    --google-account) GOOGLE_ACCOUNT="${2:-}"; shift ;;
    --services) SERVICES="${2:-}"; shift ;;
    -h|--help) usage 0 ;;
    *) printf 'Opzione sconosciuta: %s\n' "$1" >&2; usage 1 ;;
  esac
  shift
done

[ -n "$MODE" ] || { printf 'Specifica --check, --prepare o --install.\n' >&2; usage 1; }
[ -n "${HOME:-}" ] || { printf 'HOME non impostata.\n' >&2; exit 1; }

if [ "$(id -u)" -eq 0 ]; then
  printf "Non eseguire come root: usa l'utente della VM (ubuntu) con sudo.\n" >&2
  exit 1
fi
if ! command -v sudo >/dev/null 2>&1; then
  printf 'sudo non trovato: serve un utente con sudo.\n' >&2
  exit 1
fi

# ---------------------------------------------------------------- sistema
check_system() {
  log "Sistema operativo e runtime"
  if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    ok "OS: ${PRETTY_NAME:-$ID $VERSION_ID}"
    case "${ID:-}" in
      ubuntu)
        if version_ge "${VERSION_ID:-0}" "24.04"; then
          ok "Ubuntu ${VERSION_ID} supportato (Python di sistema ≥ 3.11)"
        else
          fail "Serve Ubuntu 24.04 o successivo su Oracle (${VERSION_ID} ha Python < 3.11: ricrea la VM con l'immagine 24.04)"
        fi
        ;;
      debian)
        if version_ge "${VERSION_ID:-0}" "12"; then
          ok "Debian ${VERSION_ID} supportato"
        else
          fail "Serve Debian 12 o successivo (trovato ${VERSION_ID})"
        fi
        ;;
      *)
        warn "Distribuzione ${ID:-sconosciuta} non verificata: lo script supporta Ubuntu/Debian"
        ;;
    esac
  else
    fail "/etc/os-release assente: distribuzione non riconosciuta"
  fi

  if command -v python3 >/dev/null 2>&1; then
    local py
    py="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo 0.0)"
    if version_ge "$py" "$MIN_PYTHON"; then
      ok "Python $py (minimo $MIN_PYTHON)"
    else
      fail "Python $py troppo vecchio: serve $MIN_PYTHON+ (Ubuntu 24.04 lo include)"
    fi
  else
    fail "python3 non installato: esegui --prepare"
  fi

  if command -v git >/dev/null 2>&1; then
    ok "git presente"
  else
    warn "git non installato: esegui --prepare"
  fi

  if command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
    ok "systemd utente disponibile"
  else
    fail "systemd utente non disponibile: servono i servizi 24/7 (VM Ubuntu, non container)"
  fi

  if command -v loginctl >/dev/null 2>&1; then
    local user linger
    user="${USER:-$(id -un)}"
    linger="$(loginctl show-user "$user" -p Linger --value 2>/dev/null || true)"
    if [ "$linger" = "yes" ]; then
      ok "linger attivo per $user (i servizi partono senza login)"
    else
      warn "linger non attivo: esegui 'sudo loginctl enable-linger $user' (lo fa --prepare)"
    fi
  else
    warn "loginctl assente: impossibile verificare l'avvio senza login"
  fi

  if command -v node >/dev/null 2>&1; then
    local major
    major="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"
    if [ "$major" -ge "$MIN_NODE_MAJOR" ] 2>/dev/null; then
      ok "Node.js $(node -v) (minimo $MIN_NODE_MAJOR)"
    else
      fail "Node.js $(node -v) troppo vecchio: serve $MIN_NODE_MAJOR+ (lo installa --prepare)"
    fi
  else
    warn "Node.js non installato: esegui --prepare"
  fi

  if command -v openclaw >/dev/null 2>&1; then
    local oc
    oc="$(openclaw --version 2>/dev/null | grep -Eo '[0-9]{4}\.[0-9]+\.[0-9]+' | head -n1)"
    if [ -n "$oc" ] && version_ge "$oc" "$MIN_OPENCLAW"; then
      ok "OpenClaw $oc (minimo $MIN_OPENCLAW)"
    else
      fail "OpenClaw ${oc:-sconosciuto} non supportato: serve $MIN_OPENCLAW+ (aggiorna con --prepare)"
    fi
    if [ -f "$HOME/.openclaw/openclaw.json" ]; then
      ok "Configurazione OpenClaw presente (~/.openclaw/openclaw.json)"
    else
      warn "OpenClaw non ancora configurato: esegui 'openclaw onboard' e collega WhatsApp"
    fi
  else
    warn "OpenClaw non installato: esegui --prepare"
  fi
}

# ---------------------------------------------------------------- progetto
check_repo() {
  log "Repository della segreteria"
  if [ -d "$REPO_DIR/.git" ]; then
    ok "Repository: $REPO_DIR"
    local remote
    remote="$(git -C "$REPO_DIR" remote get-url origin 2>/dev/null || true)"
    [ -n "$remote" ] && ok "origin: $remote"
    if [ -n "$(git -C "$REPO_DIR" status --porcelain 2>/dev/null)" ]; then
      warn "Ci sono modifiche locali non committate in $REPO_DIR (non verranno sovrascritte)"
    else
      ok "Working tree pulito"
    fi
  else
    warn "Repository assente in $REPO_DIR: esegui --prepare"
  fi

  local config="$HOME/.config/digital-secretary/config.json"
  if [ -f "$config" ]; then
    ok "Configurazione: $config"
  else
    warn "Configurazione assente: esegui --install (o python3 setup.py)"
  fi

  local secrets="$HOME/.openclaw/secretary/secrets.env"
  if [ -f "$secrets" ]; then
    if [ -n "$(find "$secrets" -maxdepth 0 -perm -o+r 2>/dev/null)" ]; then
      fail "$secrets è leggibile da altri utenti: esegui 'chmod 600 $secrets'"
    else
      ok "secrets.env presente con permessi privati"
    fi
    if grep -Eq '^[A-Z0-9_]*API_KEY=.+' "$secrets" 2>/dev/null; then
      ok "Almeno una chiave API compilata in secrets.env"
    else
      warn "Nessuna chiave API compilata in $secrets (la segreteria non può rispondere)"
    fi
  else
    warn "secrets.env assente: verrà creato; poi inserisci le chiavi API senza condividerle"
  fi
}

# ------------------------------------------------------------ dashboard
# Controlla che la dashboard non sia raggiungibile da fuori. Un solo indirizzo
# pubblico qui è un problema di sicurezza: la dashboard contiene dati clienti.
check_dashboard_exposure() {
  log "Esposizione della dashboard (deve restare privata)"
  local found=0 addr listeners
  if command -v ss >/dev/null 2>&1; then
    listeners="$(ss -ltnH 2>/dev/null | awk -v p=":$PORT" '$4 ~ p"$" {print $4}' || true)"
    if [ -z "$listeners" ]; then
      warn "Nessun processo in ascolto su $PORT (dashboard non avviata: normale prima di --install)"
    else
      while IFS= read -r addr; do
        [ -n "$addr" ] || continue
        found=1
        case "$addr" in
          127.0.0.1:*|\[::1\]:*) ok "In ascolto solo su $addr" ;;
          *) fail "La dashboard ascolta su $addr: deve essere 127.0.0.1, non un indirizzo pubblico" ;;
        esac
      done <<< "$listeners"
    fi
  else
    warn "ss non disponibile: impossibile verificare l'indirizzo di ascolto"
  fi

  local unit="$HOME/.config/systemd/user/openclaw-dashboard.service"
  if [ -f "$unit" ]; then
    if grep -Eq 'DASHBOARD_HOST=[^ ]*(0\.0\.0\.0|::|\*)' "$unit" 2>/dev/null; then
      fail "$unit imposta DASHBOARD_HOST pubblico: rimuovi quella riga"
    else
      ok "Unit systemd senza bind pubblico"
    fi
  fi
  if [ -f "$HOME/.openclaw/secretary/dashboard.env" ] && \
     grep -Eq 'DASHBOARD_HOST=[^ ]*(0\.0\.0\.0|::|\*)' "$HOME/.openclaw/secretary/dashboard.env" 2>/dev/null; then
    fail "dashboard.env imposta DASHBOARD_HOST pubblico: rimuovi quella riga"
  fi

  if command -v ufw >/dev/null 2>&1; then
    local ufw_state
    ufw_state="$(sudo -n ufw status 2>/dev/null || true)"
    if printf '%s' "$ufw_state" | grep -q 'Status: active'; then
      if printf '%s' "$ufw_state" | grep -Eq "(^|[[:space:]])$PORT(/tcp)?([[:space:]]|$)"; then
        fail "ufw consente la porta $PORT: rimuovila con 'sudo ufw delete allow $PORT/tcp'"
      else
        ok "ufw attivo e la porta $PORT non è aperta"
      fi
    else
      ok "ufw non attivo (nessuna porta aperta dal firewall locale)"
    fi
  else
    warn "ufw assente: verifica comunque che la Security List Oracle non apra $PORT"
  fi

  cat <<EOF

  Promemoria Oracle (non verificabile da qui):
    • Security List / NSG della VNIC: solo TCP 22 (SSH). MAI $PORT.
    • Accesso alla dashboard solo dal tuo computer:
        ssh -N -L 127.0.0.1:$PORT:127.0.0.1:$PORT ubuntu@<IP-pubblico>
      poi apri http://127.0.0.1:$PORT (vedi docs/REMOTE_ACCESS.md).
EOF
  [ "$found" -eq 1 ] || true
}

# ---------------------------------------------------------------- prepare
do_prepare() {
  log "Pacchetti di base"
  confirm "Installo/aggiorno i pacchetti di sistema con apt?" || { printf 'Annullato.\n'; exit 1; }
  sudo apt-get update -y
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    python3 python3-venv python3-pip git curl ca-certificates
  ok "Pacchetti di base installati"

  log "Node.js $MIN_NODE_MAJOR"
  local major=0
  if command -v node >/dev/null 2>&1; then
    major="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"
  fi
  if [ "$major" -lt "$MIN_NODE_MAJOR" ] 2>/dev/null; then
    curl -fsSL "https://deb.nodesource.com/setup_${MIN_NODE_MAJOR}.x" | sudo -E bash -
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs
  fi
  ok "Node.js $(node -v)"

  log "OpenClaw (>= $MIN_OPENCLAW)"
  local oc=""
  if command -v openclaw >/dev/null 2>&1; then
    oc="$(openclaw --version 2>/dev/null | grep -Eo '[0-9]{4}\.[0-9]+\.[0-9]+' | head -n1)"
  fi
  if [ -z "$oc" ] || ! version_ge "$oc" "$MIN_OPENCLAW"; then
    sudo npm install -g openclaw@latest
  fi
  oc="$(openclaw --version 2>/dev/null | grep -Eo '[0-9]{4}\.[0-9]+\.[0-9]+' | head -n1)"
  if [ -n "$oc" ] && version_ge "$oc" "$MIN_OPENCLAW"; then
    ok "OpenClaw $oc"
  else
    fail "OpenClaw non aggiornato (${oc:-assente}): controlla 'npm install -g openclaw@latest'"
  fi

  log "Repository"
  if [ -d "$REPO_DIR/.git" ]; then
    if [ -n "$(git -C "$REPO_DIR" status --porcelain 2>/dev/null)" ]; then
      warn "Modifiche locali in $REPO_DIR: salto l'aggiornamento per non sovrascriverle"
    else
      git -C "$REPO_DIR" fetch --quiet origin
      git -C "$REPO_DIR" pull --ff-only --quiet
      ok "Repository aggiornato (fast-forward)"
    fi
  else
    git clone --quiet "$REPO_URL" "$REPO_DIR"
    ok "Repository clonato in $REPO_DIR"
  fi

  log "Avvio senza login (linger)"
  sudo loginctl enable-linger "${USER:-$(id -un)}"
  ok "linger attivo: i servizi partono al boot"

  cat <<EOF

Prossimi passi (interattivi, servono il tuo telefono):
  1. openclaw onboard          # configura il modello AI e il gateway
  2. apri l'interfaccia di controllo OpenClaw e collega WhatsApp col QR
  3. inserisci le chiavi API in ~/.openclaw/secretary/secrets.env (chmod 600)
  4. bash "$REPO_DIR/scripts/install-oracle.sh" --install \\
       --business-name "..." --owner-name "..." --owner-number +39...
  5. verifica: python3 ~/.openclaw/secretary/doctor.py --short
EOF
}

# ---------------------------------------------------------------- install
do_install() {
  [ -d "$REPO_DIR" ] || { printf 'Repository assente: esegui prima --prepare\n' >&2; exit 1; }
  [ -f "$HOME/.openclaw/openclaw.json" ] || {
    printf "OpenClaw non configurato: esegui 'openclaw onboard' prima di --install\n" >&2
    exit 1
  }
  [ -n "$BUSINESS_NAME" ] || { printf 'Serve --business-name per --install\n' >&2; exit 1; }
  [ -n "$OWNER_NAME" ] || { printf 'Serve --owner-name per --install\n' >&2; exit 1; }
  [ -n "$OWNER_NUMBER" ] || { printf 'Serve --owner-number (formato +39...) per --install\n' >&2; exit 1; }
  case "$PORT" in ''|*[!0-9]*) printf 'Porta non valida: %s\n' "$PORT" >&2; exit 1 ;; esac

  local args=(
    --non-interactive
    --business-name "$BUSINESS_NAME"
    --owner-name "$OWNER_NAME"
    --owner-number "$OWNER_NUMBER"
    --bot-name "$BOT_NAME"
    --timezone "$TIMEZONE"
    --port "$PORT"
  )
  [ -n "$CITY" ] && args+=(--city "$CITY")
  [ -n "$HOURS" ] && args+=(--hours "$HOURS")
  [ -n "$GOOGLE_ACCOUNT" ] && args+=(--google-account "$GOOGLE_ACCOUNT")
  [ -n "$SERVICES" ] && args+=(--services "$SERVICES")

  log "Configurazione dell'attività"
  ( cd "$REPO_DIR" && python3 setup.py "${args[@]}" )
  [ "${PIPESTATUS[0]:-0}" -eq 0 ] || { printf 'setup.py fallito: correggi i dati e riprova.\n' >&2; exit 1; }
  ok "config.json e persona generati"

  log "Installazione di dashboard, plugin, timer e MCP"
  ( cd "$REPO_DIR" && bash scripts/install.sh )
  [ "${PIPESTATUS[0]:-0}" -eq 0 ] || { printf 'install.sh fallito: vedi gli errori sopra.\n' >&2; exit 1; }
  ok "install.sh completato"

  log "Verifica finale"
  check_dashboard_exposure
  if [ -f "$HOME/.openclaw/secretary/doctor.py" ]; then
    python3 "$HOME/.openclaw/secretary/doctor.py" --short || warn "doctor segnala problemi: risolvi i FAIL elencati"
  fi
  ( cd "$REPO_DIR" && bash scripts/verify.sh ) || fail "scripts/verify.sh non è verde: vedi l'output"
}

case "$MODE" in
  check) check_system; check_repo; check_dashboard_exposure ;;
  prepare) do_prepare ;;
  install) do_install ;;
esac

printf '\n'
if [ "$fails" -gt 0 ]; then
  printf 'ORACLE CHECK FALLITO: %s problemi, %s avvisi.\n' "$fails" "$warns"
  exit 1
fi
printf 'ORACLE CHECK OK: nessun problema bloccante, %s avvisi.\n' "$warns"
[ "$MODE" = check ] && printf 'La dashboard resta privata su 127.0.0.1:%s.\n' "$PORT"
exit 0
