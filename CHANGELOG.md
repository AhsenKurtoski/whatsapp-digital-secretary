# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **Full-colour transactional email** (`email_template.py`): a self-contained
  violet/cyan/indigo design with a coloured hero, body card, quote summary (number,
  total, validity, PDF badge), call to action and footer. Every block carries an explicit
  `bgcolor` so Gmail and Outlook keep the colours; there are no remote images, web
  fonts, tracking pixels or external CSS. Quote emails now include the summary, and both
  email types can be previewed in the dashboard.
- **Demo quote email**: *Preventivo di prova* in the Approvazioni tab sends the owner a
  complete example with the generated PDF attached, to verify colours and delivery on a
  real Gmail account without involving a customer.
- **Local demo preview**: *Anteprima demo* in the Preventivi tab renders the demo email
  and its PDF in the dashboard without queueing or sending anything. The template also
  stacks the quote number, PDF badge, total and validity cells at 375 px, with a mobile
  reference screenshot in `docs/email-preview-mobile.png`.
- **Email delivery tests** (`tests/test_email_delivery.py`): assert that gog receives the
  HTML file and the PDF attachment, that the Apps Script bridge receives base64
  attachments, and that the demo quote email embeds a real PDF.
- **Automatic PDF quotes** (`quote_pdf.py`): a dependency-free PDF 1.4 writer draws a
  branded A4 quote with gradient accent, client block, item table, discount, VAT and
  totals. Totals are always recalculated server-side, the file lives in the private
  `~/.openclaw/secretary/quotes` folder, is downloadable from the dashboard and is
  attached to the approval email (both `gog --attach` and the Apps Script bridge).
  `request_quote` is available to the bot as a third approval tool, and a demo quote can
  be generated from the Preventivi tab. The document includes PDF metadata (title,
  author, subject, creator, keywords and Italian language), wraps long client details
  and shortens long footer lines instead of drawing outside the page margins.
- **Real WhatsApp contact history** (`contacts.py`): read-only aggregation of the local
  OpenClaw sessions and transcripts, merged with classified leads and open approvals.
  The dashboard has a Contatti tab with the history and can send the owner a compact
  WhatsApp summary of past requests.
- **Owner reminders** (`reminders.py` + hourly systemd timer): pending approvals,
  upcoming appointments and expiring tokens in one deduplicated WhatsApp message.
- **Business-only scope** in the persona: personal questions about the owner or other
  people get one neutral refusal and the conversation returns to the business.
- **Inbound burst protection before the model** (`plugins/secretary-guard/guard.js`):
  `before_dispatch` now drops identical copies from the same sender/conversation while
  the first is pending (up to 120 seconds, covering slow gateway queues) and for 15
  seconds after it has been answered, returning `{ handled: true }` without text. The
  first copy is answered; six identical "1" messages produce one reply, zero duplicate
  messages and no extra model calls. Different senders, texts and conversations remain
  independent, and the hook is registered defensively so older OpenClaw versions keep
  the outbound guard.
- **Guard regression test** (`plugins/secretary-guard/test_guard.js`): dependency-free
  Node tests for the inbound window, sender/conversation isolation, missing identity,
  failed-run detection and the existing 45-second outbound duplicate guard; CI runs it
  on Node 22.
- **Private remote dashboard access**: `remote_access.py` and
  [`docs/REMOTE_ACCESS.md`](docs/REMOTE_ACCESS.md) document and automate a free
  Tailscale + SSH local-forward setup. The dashboard remains bound to `127.0.0.1`;
  the helper refuses option-injection hosts, binds the tunnel only on loopback, keeps
  the local and remote port equal for CSRF compatibility, and checks SSH, Tailscale
  and the running dashboard. Login and CSRF tests cover the forwarded loopback origin
  and reject a foreign `Origin`.
- **Capability disclosure in the persona**: when asked what it can do, the secretary now explicitly mentions PDF quotes, email requests and appointment requests, while stating that the PDF is generated locally and sending remains subject to the owner's approval.
- **Retry after a failed first message**: `agent_end` releases the pending inbound key
  when a run fails, including the webchat case where OpenClaw reports success with an
  empty assistant message. `before_dispatch` needs no extra permission; the installer
  grants the non-bundled plugin `allowConversationAccess=true` for `agent_end` and
  passes `--accept-capabilities` to the plugin install. `doctor.py` reports the missing
  permission and `--fix` applies it with `openclaw config set`.
- **OpenClaw compatibility probe** (`scripts/openclaw_compat.py`): the installer now
  checks the minimum version, detects `--accept-capabilities` and
  `allowConversationAccess`, and falls back to a clearly reported burst-only mode on
  older hosts instead of silently omitting the retry permission.
- **Repeatable guard smoke test** (`scripts/smoke_guard.py`): starts an isolated local
  OpenClaw gateway and mock model, verifies six identical messages produce one model
  call and five suppressions, then verifies a failed first turn is released and retried.
- **Installer compatibility regression test** (`tests/test_openclaw_compat.py`):
  exercises `install.sh --check-compat` with a fake `openclaw` command for an old
  version (refused), missing `--accept-capabilities` (legacy install) and missing
  `allowConversationAccess` (burst-only fallback). `--check-compat` is read-only and
  reports the plan as JSON without touching systemd or the configuration.
- **AI agent setup guide** (`AGENTS.md`): install, Google bridge/OAuth, WhatsApp,
  verification commands and the guardrails an agent must not bypass. The runtime persona
  remains `persona_template.md` and is not overwritten by this file.
- **Signed bridge health** (`google-bridge/Code.gs` and `bridge_client.py`): `health`
  is now a signed POST that probes both `GmailApp` and `CalendarApp`; the dashboard,
  `healthcheck.py` and `installa-bridge.sh` report success only when both services are
  accessible. An older deployment is shown as needing an update instead of a false OK.
- **Burst protection**: `gateway_policy.py` adds the documented OpenClaw WhatsApp
  debounce/queue settings without overriding user choices, and the guard plugin cancels
  an identical reply repeated within 45 seconds.
- **Self-diagnosis** (`doctor.py`): a PASS/WARN/FAIL report over installed files, the
  Preventivi/Contatti tabs, the PDF engine, persona scope rules, anti-burst policy, the
  OpenClaw minimum version and compatibility features, the MCP tool allowlist, AI keys,
  systemd units and the local dashboard APIs. Every failure
  carries the command that fixes it. Available as `--json`, `--short`, `--offline`,
  from `GET /api/doctor` and from the dashboard (Sistema → Diagnosi); `install.sh` runs it
  at the end. `--fix` applies the repairs it can make locally (MCP allowlist, anti-burst
  policy, inactive services) one at a time, asking confirmation before each change;
  `--yes` skips the questions for non-interactive runs.

### Fixed

- **Deterministic dashboard HTTP tests on Windows**: the test harness now uses one
  sequential loopback server per test class instead of a shared threaded server, so a
  slow or lingering handler cannot leak into the next test and cause transient 10 s
  timeouts. Requests, timeouts and assertions are unchanged. Five consecutive full
  suite runs passed locally (144 tests each).
- **Remote access helper now really installed**: `scripts/install.sh` copies
  `remote_access.py` into `~/.openclaw/secretary` without the previous
  `2>/dev/null || true`, which could hide a missing source. Required source files are
  checked before the installation starts, `gog-secretary` is no longer silently
  skipped, and `tests/test_install.py` runs the installer in a temporary home to
  verify the file, its private mode and the explicit failure when it is missing.
- **Installer dashboard port**: the final readiness checks and summary now read
  `dashboard_port` from the configuration instead of assuming `8787`, so a custom
  port is tested and reported correctly after installation.
- **Intermittent dashboard connection reset**: early 401/403 responses now consume the POST body before closing, so Windows clients no longer receive `WinError 10053` instead of the intended HTTP error. Authenticated handlers keep their body available for parsing.
- **Installer Python detection**: `install.sh --check-compat` and the installer fall back to `python` when `python3` is not on PATH (for example in Git Bash) and reject interpreters older than Python 3.11 with a clear message.
- **Dashboard at 375 px**: tabs now wrap instead of being cut off horizontally, empty tables hide their header and show the message at full width, and the demo email fits the preview frame at 309 px without horizontal scrolling. Added `docs/dashboard-preview-mobile.png`.
- **PDF quotes could not be created by the bot**: `approval_setup.py` wrote an MCP
  `toolFilter.include` list with only `request_email` and `request_calendar`, so
  `request_quote` was filtered out even though the tool existed. Re-running the installer
  now exposes it. This was the reason the PDF never appeared on existing installs.
- **Apps Script bridge sent emails without the PDF**: `Code.gs` ignored
  `payload.attachments`. It now decodes up to three base64 attachments (1.2 MB each,
  1.5 MB total), skips invalid ones instead of failing the email, and the body limit was
  raised to 2 MB. Requires `clasp push` and a new deployment.
- **The monitor no longer silences a contact forever**: alert deduplication was a
  permanent list per session, so a returning customer never produced a new hot/email
  alert. Alerts now expire after 12 hours and the old state format is migrated.
- **Terms with accented characters and the euro sign render correctly in PDF quotes**
  (WinAnsi/cp1252 text encoding).

- **The Docker dashboard now ships `healthcheck.py`**: without it the Health tab scan
  failed inside the container (`docker-entrypoint.sh` copies it into the data volume on
  start).
- `scripts/install.sh` creates `~/.local/bin` before installing the `gog-secretary`
  wrapper, so the wrapper is no longer silently skipped on a fresh user.

### Changed

- **Smoke guard hardening**: the retry phase now accepts a dedicated
  `--retry-timeout` and waits for the model call with the same generous budget used
  by `chat.send`, instead of a fixed 30-second window that could fail on a slow local
  gateway. The smoke test still asserts exactly one burst call, five suppressions
  and a successful retry.
- The README now presents this repository as the official source of the project,
  while keeping the explicit disclaimer that it is independent and not affiliated
  with WhatsApp or Meta. README, README.it, AGENTS.md and docs/TESTING.md list the
  explicit installer, remote-access and OpenClaw compatibility test commands and the
  144-test baseline.
- `doctor.py` now requires both inbound and outbound deduplication in the installed
  guard plugin, so an old plugin is reported with the reinstall command.
- Added `.editorconfig`; `CONTRIBUTING.md`, the pull request template and
  `docs/TESTING.md` now list the healthcheck self-test, the guard test and the real test
  count.
- `scripts/install.sh` installs the new modules and the reminders timer, and applies
  the WhatsApp burst policy to `openclaw.json` before restarting the gateway.
- The Code of Conduct points to a concrete private reporting route.
- `docs/PUBLISHING.md` now documents the next v1.2.0 release, the current topics and
  the release checklist.

## [1.1.1] - 2026-11-02

### Fixed

- **Bridge requests with accented text no longer fail with `unauthorized`**: the signed
  envelope is serialized as pure ASCII (`\uXXXX` escapes), so Apps Script signature
  verification cannot break when the POST body is decoded as Latin-1. Every HTML email
  and the dashboard test email failed while plain-ASCII messages worked.
- **Bridge Calendar responses never fail after the event exists**: `createEvent` reads
  `getHtmlLink()` only when the calendar exposes it, so a successful creation is not
  reported as `server_error` (which caused duplicate events on retry).
- **Health understands the bridge**: when the Apps Script bridge is configured, an
  expired `gog` token and the unused Google Workspace MCP are reported as `info`
  ("Gmail e Calendar passano dal bridge"), not as critical/warning, and no longer count
  as an urgent expiry in the digest or in the dashboard banner.
- **Next timer run parses on Python 3.11+**: `strptime` with `%Z` accepts only
  UTC/GMT/local time, so a `CET`/`CEST` timestamp raised `ValueError` and the field
  showed `-`; the abbreviation is now stripped and the configured timezone applied.

### Changed

- CI now tests Python 3.14 as well, so the interpreter used on the developer machine is
  covered by the same suite as 3.11, 3.12 and 3.13.

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
