# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project adheres to [Semantic Versioning](https://semver.org/).

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

[1.0.0]: https://github.com/your-name/whatsapp-digital-secretary/releases/tag/v1.0.0
