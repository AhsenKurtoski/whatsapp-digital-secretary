# Security Policy

## Supported versions

| Version | Supported |
| --- | --- |
| 1.x | Yes |

## Reporting a vulnerability

Please **do not open a public issue** for a security problem. Send a private report to
the maintainers through the repository's *Security → Report a vulnerability* tab
(GitHub private vulnerability reporting). Include:

- affected version and file;
- steps to reproduce;
- impact and any proof of concept;
- whether personal data, WhatsApp sessions or API keys may be exposed.

You can expect an acknowledgement within 72 hours and a status update within seven
days. Please allow a reasonable disclosure window before publishing details.

## Threat model

This project runs on a personal computer or a private VPS. It is designed to protect:

1. **The owner's accounts** — WhatsApp session, Gmail/Calendar, AI provider keys.
2. **Customer conversations** — message content and phone numbers.
3. **The host** — the bot must not be able to run shell commands or read arbitrary files.

### Assets

| Asset | Location | Protection |
| --- | --- | --- |
| Dashboard token | `~/.openclaw/secretary/dashboard_token` | `600`, never served to the browser |
| AI API keys | `~/.openclaw/secretary/secrets.env` | `600`, never sent to the model |
| WhatsApp session | `~/.openclaw` | Filesystem permissions, never committed |
| Google bridge secret | `~/.openclaw/secretary/google_bridge.json` | `600`, HMAC-SHA256 |
| Conversations / leads | Local SQLite and JSONL under `~/.openclaw` | Local only, no cloud dashboard |
| Quote PDFs | `~/.openclaw/secretary/quotes` | `700` directory, `600` files, served only after dashboard login |

### Trust boundaries

- **WhatsApp inbound messages are untrusted.** They are validated, length-limited and
  never interpreted as instructions beyond the model's persona. Prompt injection is
  mitigated by a narrow tool profile and by never exposing secrets to the model.
- **The model is untrusted.** It can only request an email, a calendar event or a PDF
  quote through three MCP tools. It cannot execute code, read arbitrary files, change
  configuration, send messages as the owner, download the PDF, or approve its own
  requests. Quote totals are recalculated from validated numbers, and the attachment
  path is confined to the private quotes directory.
- **The dashboard browser is trusted only after token login.** Mutations require a
  same-origin `Origin` header and `X-Requested-With`, which blocks cross-site form
  posts. The cookie is HttpOnly and SameSite=Strict.
- **The Google bridge is a private web app.** Requests must carry a valid HMAC signature
  over the exact body and a timestamp within five minutes. It exposes only `sendEmail`
  and `createEvent`, never a generic proxy.

### Out of scope

- A compromised host, root access, or malware reading the user's files.
- WhatsApp account bans caused by WhatsApp Web automation.
- Abuse by the owner or by someone the owner gives the dashboard token to.
- Denial of service against a third-party AI provider.
- Google Apps Script platform vulnerabilities.

## Hardening checklist

- [ ] Dashboard reachable only on `127.0.0.1` (or through an SSH tunnel over Tailscale; never a public bind).
- [ ] `~/.openclaw/secretary` is `700`; secret files are `600`.
- [ ] A dedicated WhatsApp number is used for the bot.
- [ ] `OWNER_NUMBER` is the real admin number and is verified.
- [ ] `maxConcurrent` is set to a value the machine and provider quota can sustain.
- [ ] The setup wizard password/token has been rotated after the first login.
- [ ] Backups of `~/.openclaw` are encrypted.
- [ ] Logs are reviewed for unexpected `login failed` or `csrf` entries.
- [ ] `~/.openclaw/secretary/quotes` is `700` and quote PDFs are `600`.
- [ ] Remote access uses Tailscale plus a loopback SSH tunnel; port `8787` and the gateway port are not exposed publicly.
- [ ] Third-party dependencies are updated (`npm audit` for OpenClaw, `pip-audit` if
  extra Python packages are added).

## Privacy and GDPR

- The bot stores conversations locally to keep context per customer.
- Logs redact phone numbers and message bodies by default. The anti-duplicate guard
  writes only a truncated SHA-256 fingerprint and the text length, never the customer
  message, and quote PDF downloads are audited by request id only.
- Leads and alerts can be deleted from the dashboard; deleting `~/.openclaw` removes
  the local dataset.
- Transactional emails are self-contained: no remote images, web fonts, tracking
  pixels or third-party CSS, so opening a quote does not notify anyone else.
- The AI provider receives the message text needed to generate a reply. Choose a
  provider and plan that match your data-processing obligations; do not send special
  category data.
- Publish a privacy notice for your customers and define a retention period. This
  repository does not provide legal advice.

## Known limitations

- The WhatsApp integration uses WhatsApp Web automation and may break when WhatsApp
  changes its web client.
- Free AI tiers are rate-limited; a burst of conversations may be queued.
- An OAuth app left in Google "Testing" mode expires refresh tokens after about seven
  days. The Apps Script bridge avoids this; publishing the OAuth app requires a
  verified domain.

## Active data, second factor and sessions

- **Contact memory is encrypted at rest.** Each file under `memory/contacts/` is
  AES-256-GCM (`GINAENC1` header, random 96-bit nonce) with a key in
  `memory/memory.key` (0600) or `SECRETARY_MEMORY_KEY`. The filename is a truncated
  SHA-256 of the phone number. Plaintext files from older versions are re-encrypted on
  the next write; run `python3 memory.py --encrypt-all` to migrate immediately.
- **Second factor.** `python3 identity.py --enable-2fa` prints an `otpauth://` URI for
  any TOTP app. When enabled, `/login` requires the six-digit code in addition to the
  token; failures count toward the same 10-per-5-minutes lockout. `--disable-2fa`
  removes it.
- **Short sessions and revocation.** A successful login stores a random session id in
  an HttpOnly, SameSite=Strict cookie; sessions live in server memory for 12 hours and
  the id is not the token. **Sicurezza → Esci da tutti i dispositivi**
  (`POST /api/sessions/revoke`, admin) clears every session and rotates the token, so
  other browsers and devices must sign in again.
- **Tamper-evident audit.** Every `user_audit.jsonl` entry stores `prev` and a
  SHA-256 `hash` over the previous hash and its fields. At 1 MB the log rotates to a
  numbered segment (`user_audit.0001.jsonl`, …) without losing entries: the new file
  continues from the last rotated hash, and `python3 identity.py --verify-audit`
  recomputes segments and live file in order, reporting the first broken line and the
  file it is in. Deleting, editing or reordering a segment or a line breaks the chain.
- **Off-site backup.** `backup_offsite_command` (or
  `SECRETARY_BACKUP_OFFSITE_COMMAND`) must contain `{archive}`; the command runs
  without a shell on the encrypted archive and a non-zero exit is reported as failure.
