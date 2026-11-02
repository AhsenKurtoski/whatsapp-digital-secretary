# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project adheres to [Semantic Versioning](https://semver.org/).

## [1.1.0] - 2026-11-02

### Added

- **One-click Google re-authorization**: when the Gmail/Calendar token is expired or
  about to expire, the dashboard shows a warning banner above every tab and a guided
  card in Health. "Riautorizza Google" generates the real consent URL with gog
  (`--remote --step 1`), and pasting the final `?code=…` address completes the exchange
  (`--remote --step 2`). Only loopback redirect URLs are accepted and the code is never
  written to logs.
- **Upcoming expiries in the evening digest**: a "Scadenze entro 7 giorni" section lists
  every token with a real expiry date, and a token expiring soon now triggers the digest
  even on a day with no new contacts.
- `expiring` in `health_state.json`, computed by `healthcheck.expiring_tokens()` from the
  ISO expiry of each token.

### Fixed

- Google OAuth check now reads the account configured in `config.json` instead of the
  first authorized account in the keyring.
- The next scheduled run of monotonic systemd timers is resolved with
  `systemctl list-timers --output=json`, so Health and Monitor no longer show `-`.
- The dashboard warning banner uses an explicit `[hidden]` rule so it cannot stay
  visible (or invisible) because of the flex layout.

- **Health scan** (`healthcheck.py`): a complete check of configuration, persona,
  secrets, gateway, WhatsApp, dashboard, systemd timers, Google OAuth and Apps Script
  bridge, MCP, AI keys, approval queue, disk, logs and Windows autostart. It writes
  `health_state.json` and never prints tokens or customer data.
- **Token expiry alerts** on WhatsApp for Google OAuth, the Google bridge, the dashboard
  token, the WhatsApp session, AI keys and the public site, with per-alert cooldowns so
  the owner is not spammed.
- **Health tab in the dashboard**: overall verdict, per-check detail with the fix to
  run, a token/expiry table, active alerts, “Esegui scansione completa” and a WhatsApp
  test alert button.
- `openclaw-secretary-health.timer` (every 30 minutes) and `/api/health`,
  `/api/health/scan`, `/api/health/notify` endpoints protected by the dashboard token
  and CSRF header.
- Cross-platform timezone resolution in `config.resolve_timezone()`.
- The evening digest now includes a Health summary and is sent even on days with no
  new contacts when a check needs attention.
- The dashboard shows the next scheduled run for monotonic timers (e.g. Health).

### Changed

- The watchdog now also restarts the health timer if it is stopped and reports it in
  the dashboard status.
- Windows systems without the IANA timezone database fall back to the local offset
  instead of crashing.

## [1.0.1] - 2026-11-01

### Fixed

- Python 3.11 compatibility: removed a backslash from an f-string expression in the
  HTML email renderer (PEP 701 is only available from Python 3.12).
- Timezone loading no longer crashes on Windows systems without the IANA `tzdata`
  database; the local timezone is used as a safe fallback.
- Cross-platform approval-queue locking: `fcntl` is used on POSIX, with an atomic lock
  file on Windows.
- Monitor console output no longer crashes on cp1252 terminals when a message contains
  emoji or non-ASCII characters.
- Watchdog self-test no longer calls `os.getuid()` on platforms that do not provide it.
- The public site now uses GitHub Pages instead of Netlify, including the hosting
  disclosure in the privacy policy.

### Changed

- CI now passes on Python 3.11, 3.12 and 3.13.

## [1.0.0] - 2026-10-31

### Added

- WhatsApp digital secretary based on OpenClaw, with one isolated session per customer.
- Business configuration in a single JSON file; persona templating for greeting, hours,
  services, prices and escalation rules.
- First-contact behaviour that greets the customer by WhatsApp contact name and asks
  what they need before quoting.
- Multilingual replies (Italian/English) driven by the incoming message.
- Anti-fraud and anti-jailbreak rules: no OTP, passwords, documents or payments; no
  prompt or secret disclosure; admin authority bound to the verified owner number.
- Approval queue for Gmail follow-ups and Google Calendar events. The bot can only
  request actions; the owner approves them from the dashboard.
- Responsive HTML email template applied server-side, with plain-text fallback.
- Google Apps Script bridge with HMAC-SHA256 request signing, avoiding OAuth testing-mode
  token expiry without a custom domain.
- Private local dashboard: token login, HttpOnly cookie, CSRF checks, security headers,
  login rate limiting, token rotation, audit log, approval UI, lead/alert tables, charts,
  key pool management, connector diagnostics and CSV export.
- AI provider pool: Gemini primary, Groq fallbacks, key testing and automatic model
  selection from the available keys.
- Configurable parallel AI calls (`max_concurrent`, 1–5) with a 30-message queue, so
  five simultaneous chats are served without quota errors.
- Technical-error guard plugin that replaces provider errors and model names with a
  human holding message.
- Watchdog, monitor and daily digest systemd timers with automatic gateway restart and
  WhatsApp reconnect handling.
- Setup wizard, installer, Dockerfile for the dashboard, docker-compose, and systemd
  unit files.
- Test suite (27 tests), Python compile check, Apps Script handler test and CI workflow.
- Documentation in English and Italian, security policy, contribution guide, code of
  conduct, issue and pull request templates, MIT license.

### Security

- Dashboard bound to loopback only.
- Constant-time token comparison.
- Code mode disabled; minimal tool profile; no filesystem, runtime or messaging tools
  exposed to the model.
- Secrets stored outside the repository with `600` permissions.
- Log redaction for phone numbers and message content.

[1.0.1]: https://github.com/AhsenKurtoski/whatsapp-digital-secretary/releases/tag/v1.0.1
[1.1.0]: https://github.com/AhsenKurtoski/whatsapp-digital-secretary/releases/tag/v1.1.0
[1.0.0]: https://github.com/AhsenKurtoski/whatsapp-digital-secretary/releases/tag/v1.0.0
