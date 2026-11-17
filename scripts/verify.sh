#!/usr/bin/env bash
# Verifica completa in un comando: la stessa matrice usata dalla CI.
# Uso: bash scripts/verify.sh
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

fail=0
run() {
  printf '\n▶ %s\n' "$*"
  if "$@"; then
    printf '  ✅ ok\n'
  else
    printf '  ❌ FALLITO\n'
    fail=$((fail + 1))
  fi
}

run python3 -m py_compile *.py google-bridge/*.py scripts/*.py
run python3 -m unittest discover -s tests
run python3 scripts/openclaw_compat.py --json
run python3 google-bridge/test_bridge.py
run node google-bridge/test_code_gs.js
run node plugins/secretary-guard/test_guard.js
run python3 quote_pdf.py /tmp/preventivo-verifica.pdf

for script in monitor watchdog healthcheck contacts reminders gateway_policy doctor remote_access; do
  run python3 "$script.py" --self-test
done

# Questi controlli richiedono OpenClaw o un'installazione reale: si eseguono
# solo sul host dell'utente, non in CI.
if command -v openclaw >/dev/null 2>&1; then
  # Rete di sicurezza: il teardown dello smoke test chiude l'intero gruppo di
  # processi e non può bloccarsi; questo limite massimo protegge comunque la
  # matrice da un figlio che ignorasse anche SIGKILL o da un host lentissimo.
  if command -v timeout >/dev/null 2>&1; then
    run timeout --kill-after=15 240 python3 scripts/smoke_guard.py
  else
    run python3 scripts/smoke_guard.py
  fi
fi
if [ -f "$HOME/.openclaw/secretary/doctor.py" ]; then
  run python3 "$HOME/.openclaw/secretary/doctor.py" --offline
fi

printf '\n'
if [ "$fail" -eq 0 ]; then
  printf 'VERIFICA OK: nessun controllo fallito.\n'
else
  printf 'VERIFICA FALLITA: %s controlli.\n' "$fail"
fi
exit "$fail"
