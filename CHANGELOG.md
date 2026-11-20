# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Security

- **Encrypted contact memory**: `memory/contacts/*.json` is now AES-256-GCM with a
  `0600` key (`memory.key` or `SECRETARY_MEMORY_KEY`); legacy plaintext is read
  transparently and re-encrypted, and `python3 memory.py --encrypt-all` migrates the
  whole folder. Filenames were already hashed, so neither numbers nor transcripts are
  readable on disk.
- **TOTP second factor and short sessions**: `identity.py --enable-2fa` prints an
  `otpauth://` URI; login then requires the six-digit code. Successful logins receive a
  random server-side session id (12 hours) instead of the token, and the new
  **Esci da tutti i dispositivi** button revokes every session and rotates the token.
- **Tamper-evident audit**: each audit entry stores the previous hash and its own
  SHA-256; `identity.py --verify-audit` reports the first modified or removed line.
  Rotation no longer truncates the file (which used to break the chain): at 1 MB the log
  moves to a numbered `user_audit.NNNN.jsonl` segment and the new file continues from
  the last rotated hash, so every entry stays verifiable across segments.
- **Real HTTP tests for the new auth paths**: 2FA login (missing and wrong code rejected,
  correct code opens a working session) and `POST /api/sessions/revoke` (viewer gets 403,
  admin ends every session, clears the cookie and rotates the token) now run against the
  live test server instead of only the session helpers.
- **Off-site backups**: `python3 backup.py --offsite <file>` runs a configured,
  shell-free `{archive}` command (for example rclone) and fails loudly when the copy
  did not happen.


### Fixed

- **Coverage matrix skipped six self-tests**: `contacts`, `monitor`, `reminders`,
  `watchdog`, `healthcheck` and `doctor` all support `--self-test` and are run by
  `verify.sh`, but they were missing from `scripts/coverage.sh`, so their code counted as
  uncovered. They are measured now, together with `link_check.py`. `docs/TESTING.md` also
  still claimed 158 tests; it now matches the suite.
- **`recent_audit` had no upper bound**: `/api/audit/users?limit=1000000000` would walk
  the whole archive; the limit is capped at `MAX_RECENT_AUDIT` (500 rows).
- **The Events panel lost history after an audit rotation**: `recent_audit()` read only
  `user_audit.jsonl`, so the newest entries vanished from the dashboard until new events
  arrived. It now starts from the live file, walks the numbered segments from newest to
  oldest and stops at the requested limit instead of parsing the whole archive on every
  dashboard refresh; a test counts the parsed lines so the early exit cannot regress,
  and an HTTP test covers `/api/audit/users` right after a rotation.
- **`scripts/coverage.sh` measured coverage but never enforced it**: the documentation
  and CI claimed an 86% gate while the script only printed the report. `coverage report`
  now runs with `--fail-under=86`, so CI fails below the documented threshold.
- **`quote_pdf.py --self-test` wrote a PDF named `--self-test`** into the current
  directory instead of running a check. It now uses argparse, supports a real
  self-test and keeps the optional output path; `scripts/coverage.sh` exercises it.
- **Hidden banners were rendered anyway**: `.health-banner` sets `display:flex`, which
  overrode the browser's `[hidden]{display:none}`. The capabilities banner therefore
  stayed on screen even after its check passed (the token banner already had an explicit
  rule). Added `#capabilitiesBanner[hidden]{display:none}` and a regression test.
- **`python3 dashboard.py --help` hung**: the entrypoint ignored arguments and started
  the server. It now uses argparse, prints usage and exits, and accepts `--port` for a
  local alternative port (the bind address stays `127.0.0.1`). Covered by
  `tests/test_cli_surfaces.py`.

### Added

- **Relative-link gate**: `link_check.py` scans every Markdown and HTML file (code
  examples, anchors, absolute URLs and data URIs excluded) and fails with the exact file
  and missing target instead of letting the install guide 404. It runs in
  `scripts/verify.sh`, in CI, with a self-test and five unit tests.
- **Author credit across the public site**: "Ahsen Kurtoski" appears in the hero byline,
  the closing CTA, the author section and the footer of the homepage, and on the privacy,
  terms and 404 pages; the structured data now declares `creator`, `copyrightHolder` and
  `maintainer`, and every indexable page has `hreflang` alternates and
  `twitter:image:alt`.
- **Public landing page built to sell**: the site now explains the problem, who it is
  for (practices, tradespeople, agencies, local services), how installation works, the
  real requirements, privacy and security measures and the honest cost model (MIT, no
  subscription, you pay only your own machine and AI keys). The FAQ grew to six
  questions mirrored by the `FAQPage` data, and a `HowTo` graph describes the real
  install steps.
- **Repository discovery**: `README.md` and `README.it.md` gained a "who it is for"
  section, descriptive image alt text and a documentation index linking every guide in
  `docs/`.
- **Demo dashboard that shows memory**: `demo.py` now seeds three customers with
  facts and redacted transcripts (Mario/website quote, Giulia/maintenance, Luca/Thursday
  appointment), so the **Memoria** row in the transcript panel has real data. The demo
  instance sets `demo_mode` and does not show the critical "bot non vede i tool" banner,
  which would be misleading without OpenClaw installed.
- **Sharper per-customer memory** (`memory.py`, `conversations.py`, dashboard): facts now
  carry `fact_meta.updated_at`, and updating one moves it to the top so the 40-fact cap
  drops stale entries instead of recent ones. `context()` ranks facts by overlap with
  the current message, then by recency, and shows up to ten instead of the first eight.
  New `memory.search()` (CLI `--search`) searches facts and redacted messages across
  contacts, and the dashboard transcript panel shows the customer's facts from
  `/api/conversations/<phone>`. Identical consecutive messages are collapsed to one
  entry with the latest timestamp. Covered by `test_gina_platform` and the module
  self-test; documented in `docs/MEMORY.md`.
- **Informational cookie notice without analytics**: the public site now always shows a
  cookie banner. When no GA4 or Cloudflare ID is configured it is a non-blocking
  **Nessun cookie di profilazione** notice with a single "Ho capito" action: it records
  no consent (`gina_consent_v1` stays untouched) and only stores `gina_notice_v1` so it
  is not repeated. The consent gate remains unchanged when an analytics ID is set, and
  `privacy.html` documents the new key in its cookie table.
- **Revocable cookie consent and legal pages on the public site**: the banner is now a
  real consent gate. `analytics.js` only registers Consent Mode v2 defaults until the
  visitor accepts; the Google Tag Manager and Cloudflare beacon loaders are created
  inside `load()` and never contacted before that choice. The footer of every page has
  a **Cookie** link that reopens the preferences with the current state, so revoking
  is as easy as accepting, and `privacy.html` documents every cookie and storage key
  (name, provider, purpose, duration, consent) in a table at `#cookie`. A new
  `termini.html` covers MIT licence, no-warranty, trademarks, acceptable use, external
  links, liability and Italian law. Covered by new assertions in `tests/test_site.py`.
- **Lighthouse CI on the live site** (`.github/workflows/lighthouse.yml`,
  `lighthouserc.json`): three runs per URL (`/`, `/privacy.html`, `/termini.html`) in mobile emulation with simulated mobile
  throttling, median assertions (Performance ≥ 0,90, Accessibility/Best Practices ≥
  0,95, SEO = 1,00, LCP ≤ 2,5 s, CLS ≤ 0,10, TBT warning above 200 ms) and HTML
  reports uploaded as a build artifact. Runs on push to `main`, pull requests, a
  weekly schedule and manual dispatch. `/termini.html` was added to the audited URLs on
  19/11/2026; the real run then covered nine runs, all assertions passed, 100
  Performance / Accessibility / Best Practices / SEO on the median of all three URLs,
  LCP 0,85 s / 0,81 s / 0,81 s, CLS 0.
- **Automatic Vercel deployments on `main`**: `vercel.json` now declares
  `git.deploymentEnabled.main: true`. Verified with a real push (`4c71e47`): Vercel
  created a production deployment four seconds after the push, promoted it to
  `gina-secretary.vercel.app` and no manual deploy call was needed.
- **Memory and quote-recall tools for Gina** (`approval_mcp.py`): `remember_fact`
  stores confirmed, redacted customer facts per WhatsApp number so they survive
  restarts and new chats; `list_quotes` lists only that customer's quotes with number,
  date, total, status and PDF availability; `request_quote_resend` regenerates the PDF
  from the original quote and queues a new approval. The persona now passes the
  customer number to `client_brief` on every message and no longer tells customers
  that a PDF command is missing. Covered by real JSON-RPC tests, including isolation
  between two customers and the protocol self-test.
- **Public SEO website** (`site/`, `site_build.py`, `seo_audit.py`): a separate public
  page for Gina and its author with canonical URLs, Open Graph/Twitter cards, JSON-LD
  (`Person`, `SoftwareApplication`, `WebSite`, `FAQPage`), sitemap, robots,
  `security.txt`, GA4 with Consent Mode v2, optional cookieless Cloudflare analytics,
  Google/Bing/Yandex verification tags, strict security headers (`_headers` and
  `vercel.json`) and a build that refuses empty/malformed IDs. `seo_audit.py` gates
  title, description, headings, structured data, sitemap, robots and headers; CI and
  `scripts/verify.sh` run both. Live at <https://gina-secretary.vercel.app> (Vercel
  project `gina-secretary`, static output in `public/`). Documented in
  `docs/WEBSITE.md`. The dashboard stays local-only and is never published.
- **CodeQL workflow** for Python and JavaScript on push, pull request and weekly
  schedule, alongside the existing Dependabot, Bandit and `pip-audit` checks.

### Changed

- **Social preview image**: the 843 KiB `docs/screenshot-dashboard.png` was copied to
  the site as-is for Open Graph. `site/assets/og.jpg` is now a 1200×917 progressive
  JPEG at 96 KiB (82 % quality, Lanczos resize), declared with `og:image:type` and the
  real 1200×917 dimensions; the build copies it directly, so Vercel needs no Pillow.
- **Harder security headers on the public site**: HSTS raised to two years with
  `includeSubDomains; preload`, `X-Permitted-Cross-Domain-Policies: none`, the
  `Permissions-Policy` now also blocks accelerometer, gyroscope, magnetometer,
  display capture and browsing topics, and the CSP denies `frame-src`, `worker-src`
  and `media-src` on top of the existing `object-src 'none'`.

### Fixed

- **Mobile Lighthouse performance on the public site**: the hero was a lazily
  loaded 843 KiB PNG rendered at 364 px, giving a 5.3 s LCP. It now uses four WebP
  widths with `srcset`/`sizes`, a matching preload, `loading="eager"` and
  `fetchpriority="high"`; the built site shrank from 1.8 MiB to 1.1 MiB. Live
  Lighthouse mobile: Performance 100, Accessibility 100, Best Practices 100, SEO
  100, LCP 1.0 s.
- **Dashboard table rows stayed Italian in English**: the DOM translator skipped
  every `<tbody>`, so contacts, conversations and analytics cells were never
  translated. Rows are now translated, composed strings use `t()` (customer/Gina
  bubble labels, chart tooltips, intent names, empty states, contact summary dialogs)
  and the server returns the contact summary and AI-quota alerts in the requested
  language (`/api/contacts?lang=`, `/api/usage?lang=`). Covered by new assertions in
  `tests/test_dashboard_i18n.py`, `tests/test_contacts.py` and
  `tests/test_gina_platform.py`.

- **Live reply pipeline** (`live.py`): the same function the `client_brief` MCP tool
  calls on every message now joins classification, per-customer memory and a shared
  answer cache. Gina receives a ready, verified answer for factual questions
  (hours, city, services, site, pricing note), memory context so it never asks again
  for known data, intent and guidance (quote, appointment, support, scam).
  Answers that contain emails, phone numbers, IBANs, quotes or appointments never
  enter the shared cache; the plugin `secretary-guard` posts every real inbound and
  outbound message to authenticated loopback endpoints (`/api/live/inbound`,
  `/api/live/outbound`) with a dedicated `live_token` (mode 600), fire-and-forget so
  replies are never delayed. Prove end to end by `scripts/smoke_guard.py`, which now
  checks the gateway- plugin- Python path (inbound/outbound/signed token).
- **Complete dashboard translation** (IT/EN): `i18n.json` holds an English version
  for every static string of all legacy tabs (300 strings, checked by
  `tests/test_dashboard_i18n.py`), and a DOM translator with `MutationObserver`
  covers dynamically rendered text and attributes (`placeholder`, `title`,
  `aria-label`) while leaving transcripts and user data untouched.
- **Platform modules for Gina** (each with self-test and unit tests):
  `lead_intel.py` (IT/EN intent, 0-100 score, scam signals, PII redaction, audit
  trail), `memory.py` (per-customer language, facts, intents, redacted transcript,
  handoff, export/erase), `cache.py` (thread-safe TTL+LRU with persisted stats),
  `identity.py` (admin/operator/viewer, SHA-256 tokens, per-user audit),
  `quotas.py` (per-provider requests, tokens, 429s, configurable alert
  thresholds), `router.py` (model choice by load, latency and provider health),
  `webhooks.py` (HMAC-SHA256 signed deliveries, event filters, retries, log),
  `stats.py` (real KPIs and 14-day series), `conversations.py` (read-only
  OpenClaw transcripts with redaction and handoff), `gdpr.py` (access, erasure,
  retention, inventory) and `backup.py` (AES-256-GCM with scrypt, rotation and a
  restore that verifies every SHA-256).
- **Dashboard**: Conversations tab with masked transcripts and one-click
  handoff/release, Analytics tab with KPIs, a 14-day chart, AI quota usage and
  the recommended model; role-aware UI from `GET /api/me`; new APIs for
  conversations, stats, usage, users, webhooks, GDPR and model application; every
  mutation is written to the per-user audit log.
- **Internationalisation** of the dashboard shell and the new views (IT/EN with a
  language selector persisted in `localStorage`), plus accessibility fixes:
  skip link, ARIA tablist/tab/tabpanel, `aria-selected`, keyboard navigation with
  arrow keys, visible focus ring, screen-reader-only labels and a higher-contrast
  secondary text colour.
- **Guided setup** (`python3 setup.py --wizard`, `setup_wizard.py`): validates the
  AI key against the real provider, reads gateway and WhatsApp channel state, sends
  a real test email through the same path used by approvals, and writes validated
  keys only to `secrets.env` (mode 600) after confirmation.
- **Quality gates**: `requirements-dev.txt`, `pyproject.toml` (ruff, mypy, bandit,
  coverage threshold 86%, currently 87% over 256 tests plus every module self-test),
  `scripts/coverage.sh`, a `quality` CI job and a
  `release` workflow that validates SemVer tags and publishes release notes from
  this changelog; Dependabot for pip, npm and GitHub Actions.
- **Operations documentation**: `docs/E2E.md` (dedicated WhatsApp number and
  Google sandbox, step-by-step checklist), `docs/WALKTHROUGH.md` (60-second
  storyboard and ffmpeg commands), `docs/DISASTER_RECOVERY.md`,
  `docs/WHATSAPP_BAN.md`, `docs/GDPR.md` with a DPA template, `docs/PLUGINS.md`
  with a runnable `examples/hello-plugin`, and `docs/DOCKER.md` with
  `docker-compose.full.yml` for OpenClaw + dashboard.

### Changed

- `scripts/verify.sh` also runs the self-test of every new module, including
  `live.py`, and the guard smoke test now fails if the live pipeline is not fed.
- The installer copies every platform module, `i18n.json` and the plugin that emits
  live events, so an installed dashboard is never missing an import.
- Backup skips transient `.lock` and `.pid` files: an archive created while a lock
  changes can no longer fail its own SHA-256 verification.
- The dashboard's legacy token still works as the `admin` owner, so upgrades do not
  lock the owner out; additional users get their own token and role.

- **Manual quote builder** (`dashboard.py`, `dashboard.html`): *Nuovo preventivo* creates
  a quote and its PDF from the dashboard without the model, the AI provider or WhatsApp.
  Items, discount, VAT, validity and notes are validated server-side, totals are always
  recalculated by `quote_pdf`, and the request lands in the normal approval queue.
  *Duplica* reuses a previous quote with a new number.
- **Quote lifecycle in the dashboard**: expiry date with a “scaduto” badge, search by
  number/client/email, per-quote HTML email preview, and a CSV export (formula-injection
  safe) for Excel and accountants. `quotes_data()` now exposes `valid_until`,
  `days_left`, `expired` and the item list.
- **Runtime MCP self-test** (`approval_mcp.py --selftest`, run by `scripts/install.sh`):
  validates the three tools, their JSON schemas and a real PDF render on the installed
  copy, so a stale or broken install fails the installer instead of silently disabling
  quotes.
- **`mcp_runtime` check in `doctor.py`**: executes the installed MCP server and lists its
  tools; a missing `request_quote` is now a FAIL with the exact repair command.
- **Bot capability banner**: `GET /api/capabilities` inspects the installed module and
  the dashboard tells the owner when the bot cannot see the approval tools.
- **`tests/test_mcp_protocol.py`**: end-to-end JSON-RPC over stdio (initialize,
  tools/list, tools/call, invalid input, unknown tool, CLI self-test) against a real PDF.
- **`tests/test_quote_builder.py`**: manual quote creation, duplication, validation,
  expiry, CSV escaping, email preview and capability detection.
- **`scripts/verify.sh`**: one command for the whole verification matrix, also suitable
  for CI and coding agents.
- **`demo.py`**: a 60-second local demo with fake data, no keys, no WhatsApp and no
  sending; it copies the dashboard into a temporary directory and prints the token.
- **Secretary name**: the default `bot_name` is now **Gina**; it appears in the dashboard
  title, avatar, login, footer and on the PDF header.
- **`linger` check in `doctor.py`**: without `loginctl enable-linger`, user services stop
  at logout and do not start after a reboot until someone logs in. The check reports it
  with the exact `sudo` command, so a 24/7 setup can be verified instead of assumed.
- **`docs/ALWAYS_ON.md`**: verified free 24/7 options (own device, Oracle Always Free),
  what the watchdog already repairs, and the limits none of them removes (sleep, QR
  re-scan, idle reclamation, free AI quotas).
- **`scripts/install-oracle.sh`**: a verified install on an Oracle Cloud Always Free VM
  (Ubuntu 24.04). `--check` is read-only and reports blockers (OS/Python/Node/OpenClaw,
  `linger`, repository, secrets); `--prepare` installs Node 22, OpenClaw, the repository
  and lingering; `--install` runs `setup.py` non-interactively, `install.sh` and
  `verify.sh`. It refuses to run as root and treats a dashboard listening on anything
  other than `127.0.0.1` (or a firewall rule opening its port) as a failure, while never
  touching Oracle Security Lists. The SSH tunnel command is printed at the end.
- **`tests/test_smoke_guard_teardown.py`**: regression test for a gateway child that
  ignores `SIGTERM`; the teardown must return quickly and leave no process alive.
- **PDF acceptance block**: quotes end with a client acceptance area (date and signature)
  and the header uses the configured secretary name.

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

- **`scripts/verify.sh` could hang forever in the smoke-test teardown**: the gateway was
  started without its own session, so `terminate()` killed only the parent, and the second
  `process.wait()` re-raised `TimeoutExpired` inside the `finally` block. Surviving
  children kept the pipeline open. `stop_process()` now signals the whole process group
  (`SIGTERM`, then `SIGKILL`), never propagates an exception, closes the streams, and the
  gateway runs with `start_new_session=True`; `verify.sh` additionally bounds the smoke
  test with `timeout --kill-after=15 240`.
- **The bot refused to create PDF quotes** (“non ho il tool”): the persona referenced
  `request_quote` while OpenClaw exposes `secretary__request_quote`, and it contained an
  explicit escape hatch (“Se il tool non è disponibile…”). The persona now uses the exact
  tool names, forbids that answer, and `approval_setup.py` allowlists both
  `secretary__*` and `mcp__secretary__*` namespaces, then re-reads the written JSON and
  fails loudly if a tool is missing.
- **The installer swallowed approval-registration errors**: `approval_setup.py` is no
  longer run with `|| true`, and `install.sh` now runs the MCP self-test before finishing.
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
