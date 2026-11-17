# AGENTS.md — install and verify this project

This file is for AI coding/install agents. The **runtime WhatsApp persona** is
[`persona_template.md`](persona_template.md); `setup.py` renders it to
`~/.openclaw/workspace/AGENTS.md`. Never copy this repository file over that generated
persona.

## What "done" means

Install the secretary for the user, connect the business data and the real Google
account, and prove the installation with the checks in this file. Do not claim that
Gmail, Calendar or WhatsApp work from the presence of a config file alone. Gmail and
Calendar actions stay **approval-gated** by design: "active" means connected and
healthy, not able to send without the owner's approval.

## Fast path

1. Read `README.md` and `SECURITY.md`. Inspect the machine: Linux/WSL2, Python 3.11+,
   Node.js 22+, OpenClaw 2026.4.22+ (required for the inbound `before_dispatch` guard;
   the installer probes `--accept-capabilities` and `allowConversationAccess` and
   reports a burst-only fallback when the retry hook is unavailable) and the bot
   WhatsApp number.
2. Run `python3 setup.py` and ask the user only for business data (name, owner number,
   services, prices, hours, timezone). Keep secrets out of `config.json`.
3. Create the private environment file:
   `install -m 600 secrets.env.example ~/.openclaw/secretary/secrets.env`, then let the
   user fill in the AI keys. Never print, commit or paste a key into a chat.
4. Connect Google **before** calling the installation complete:
   - Preferred: the Apps Script bridge in [`google-bridge/README.md`](google-bridge/README.md).
     Run `bash ~/.openclaw/secretary/google-bridge/installa-bridge.sh`, have the user
     approve Gmail and Calendar in the browser, then verify the bridge.
   - Alternative: `gog-secretary auth add <account> --services gmail,calendar --gmail-scope read-send --manual`.
     OAuth apps in Testing expire after 7 days; the bridge avoids that expiry.
5. Run `bash scripts/install.sh`. It installs the dashboard, the guard plugin, the
   timers, the WhatsApp debounce/queue policy and `remote_access.py` into
   `~/.openclaw/secretary`. The installer stops with a clear error if a required
   source file (including `remote_access.py`) is missing.
6. Connect WhatsApp: open the OpenClaw control UI, run the WhatsApp login and have the
   user scan the QR with the bot phone.
7. Run the verification commands below. Fix every `FAIL`; report every remaining `WARN`
   with its remedy. Only then tell the user the installation is ready.

## Verification commands

```bash
bash scripts/verify.sh
python3 -m unittest discover -s tests -v
python3 -m unittest discover -s tests -p 'test_install.py' -v
python3 -m unittest discover -s tests -p 'test_remote_access.py' -v
python3 -m unittest discover -s tests -p 'test_openclaw_compat.py' -v
python3 -m py_compile *.py google-bridge/*.py scripts/*.py
python3 scripts/openclaw_compat.py --json
bash scripts/install.sh --check-compat
python3 scripts/smoke_guard.py
node plugins/secretary-guard/test_guard.js
node google-bridge/test_code_gs.js
python3 google-bridge/test_bridge.py
python3 healthcheck.py --self-test
python3 doctor.py --self-test
python3 remote_access.py --self-test
python3 ~/.openclaw/secretary/doctor.py --offline
python3 ~/.openclaw/secretary/healthcheck.py --json
```

Expected: `scripts/verify.sh` prints `VERIFICA OK`; 162 unit tests pass, including
the isolated installer, remote-access, OpenClaw compatibility and MCP protocol tests; every self-test prints `SELF-TEST OK`; the guard test
prints `secretary guard: OK`; on a host with supported OpenClaw, `--check-compat`
reports `allowed` with the expected fallback flags; the smoke test prints `SMOKE OK` (one model call for six identical
messages, then two calls for the failed/retried pair);   `doctor.py` has no `FAIL` and
reports the OpenClaw version, the MCP runtime (`mcp_runtime`) and feature checks; and `healthcheck.py` reports
`google_oauth` as `ok` or `info` and `google_bridge` as `ok` when the bridge is
configured. Then send a real test message from a second phone and approve a real demo
quote/email from the dashboard, plus one Calendar request. Record the date and result.

## Built-in protections — do not remove or bypass

- **Anti-jailbreak / business-only scope**: `persona_template.md` refuses unrelated
  requests with one neutral line and ignores prompt extraction, role-play, DAN,
  emergency pretexts and encoded instructions. The refusal phrase is tested; change it
  only with an explicit request and update the test.
- **Inbound burst guard**: `plugins/secretary-guard` suppresses identical inbound
  copies through `before_dispatch`, before the model is called. A copy stays pending
  (and suppressed) for up to 120 seconds while the first one is queued or running;
  after a successful answer, identical copies are suppressed for another 15 seconds.
  Six identical `1` messages produce one answer and no extra token spend. The first
  copy is never suppressed, and different senders/texts/conversations are independent.
  If the first turn fails, `agent_end` releases its key so the retry reaches the model.
  `before_dispatch` needs no extra permission. `agent_end` is a conversation hook and
  needs `plugins.entries.secretary-guard.hooks.allowConversationAccess=true`, which
  `scripts/install.sh` sets; without it the burst guard still works but a failed turn
  can stay suppressed for up to 120 seconds.
- **Outbound duplicate guard**: the same plugin cancels an identical reply to the same
  recipient within 45 seconds and replaces technical/provider errors with a human line.
- **Approvals and sandbox**: never disable the approval queue, the MCP allowlist, the
  localhost-only dashboard, CSRF checks or the tool sandbox to "make it work". Gmail
  and Calendar are executed only after the owner approves a request.
- **Secrets**: keys, OAuth tokens, `secrets.env`, `google_bridge.json`, `Config.gs`,
  WhatsApp session files and dashboard tokens are private. Never commit them, log them
  or send them to a customer.

## Google troubleshooting

- `google_oauth` critical: reauthorize `gog` or use the bridge.
- `google_bridge` warn: test the deployment URL, confirm the Apps Script authorization
  includes Gmail and Calendar, and push a new deployment after changing `Code.gs`.
  `bridge_client.py health` must return `healthy: true` with both
  `services.gmail` and `services.calendar` true; an old deployment without these fields
  is reported as needing an update, not as healthy.
- If the bridge is configured, Gmail and Calendar both use it; no 7-day OAuth expiry.
- A configured bridge is not proof of delivery: use **Preventivo di prova** and a real
  Calendar approval, then confirm the message/event exists.

## Useful files

| File | Purpose |
| --- | --- |
| `setup.py` | Business configuration and persona rendering |
| `scripts/install.sh` | Install/update everything on the machine, including `remote_access.py` |
| `gateway_policy.py` | WhatsApp debounce and `collect` queue |
| `plugins/secretary-guard/` | Inbound burst guard, outbound dedupe and error scrubber |
| `scripts/openclaw_compat.py` | OpenClaw version and feature probe for the installer |
| `scripts/smoke_guard.py` | Isolated repeatable burst + retry smoke test |
| `remote_access.py` | Private Tailscale + SSH tunnel helper, installed as `~/.openclaw/secretary/remote_access.py` |
| `healthcheck.py` | Live Google, bridge, gateway and queue health |
| `doctor.py` | Installation diagnosis and safe repairs |
| `docs/TESTING.md` | Manual WhatsApp/dashboard/Google checklist |
