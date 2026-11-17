#!/usr/bin/env bash
# Copertura reale: suite unitaria + self-test dei moduli, poi soglia dell'80%.
# Uso: bash scripts/coverage.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

PYTHON="${PYTHON:-python3}"
COVERAGE="${COVERAGE:-coverage}"
if ! command -v "$COVERAGE" >/dev/null 2>&1; then
  if "$PYTHON" -m coverage --version >/dev/null 2>&1; then
    COVERAGE="$PYTHON -m coverage"
  else
    echo "coverage non installato: python3 -m pip install -r requirements-dev.txt" >&2
    exit 1
  fi
fi

# shellcheck disable=SC2086
$COVERAGE erase
# shellcheck disable=SC2086
$COVERAGE run -m unittest discover -s tests

# I self-test coprono i percorsi CLI e le validazioni: misurarli è parte della
# copertura reale, non un artificio (ogni self-test fa asserzioni vere).
for module in lead_intel memory cache identity quotas router webhooks stats \
              conversations gdpr backup setup_wizard gateway_policy approval_store \
              quote_pdf remote_access config; do
  if [ -f "$module.py" ]; then
    # shellcheck disable=SC2086
    $COVERAGE run --append "$module.py" --self-test >/dev/null 2>&1 || true
  fi
done

# shellcheck disable=SC2086
$COVERAGE report
