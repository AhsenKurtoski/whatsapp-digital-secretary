# WhatsApp Digital Secretary

[![CI](https://github.com/AhsenKurtoski/whatsapp-digital-secretary/actions/workflows/ci.yml/badge.svg)](https://github.com/AhsenKurtoski/whatsapp-digital-secretary/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/AhsenKurtoski/whatsapp-digital-secretary?color=5eead4&label=release)](https://github.com/AhsenKurtoski/whatsapp-digital-secretary/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-7dd3fc.svg)](LICENSE)

A self-hosted, private digital secretary for WhatsApp. It answers customers in your
business tone, knows your services and prices, collects leads and appointment
requests, and prepares Gmail/Calendar actions that **you approve** from a local
dashboard.

> **English** · [Italiano](README.it.md)

![Dashboard](docs/screenshot-dashboard.png)

## Why this project

Most WhatsApp bots are either generic chatbots or expensive SaaS products. This
project is different: it runs on your computer, uses your real business data, keeps
conversation memory per customer, and never sends an email or creates a calendar
event without an explicit approval.

It is built on top of [OpenClaw](https://openclaw.ai) and the unofficial WhatsApp
Web client. It is not a WhatsApp Business API product and is not affiliated with
Meta or WhatsApp.

## Features

- **Human-like first contact** — greets the customer by the WhatsApp contact name,
  introduces itself, and asks what they need before quoting a price.
- **Business knowledge** — services, prices, hours, city, portfolio, FAQs and tone
  live in one configuration file, not in the prompt source code.
- **Lead qualification** — detects quotes, calls, support requests and suspicious
  messages; scores leads; writes an audit trail.
- **Appointment requests** — collects date/time and attendees, then queues a
  Calendar request for approval.
- **Email follow-ups** — writes a plain-text email, applies a responsive HTML
  template server-side, and queues it for approval.
- **Multilingual** — answers in Italian or English based on the customer's message.
- **Multiple conversations** — one isolated session per phone number, with a queue
  for bursts. Configure `maxConcurrent` for your machine and provider quota.
- **Anti-fraud and anti-jailbreak** — never asks for OTPs, passwords, documents or
  payments; ignores role-play and prompt-extraction attempts; never reveals keys,
  prompts, internal files or other customers' data.
- **Admin recognition** — only the owner's verified number gets operational
  summaries; a customer cannot become admin by claiming to be the owner.
- **Technical-error guard** — a local plugin replaces provider errors, rate limits
  and model names with a short human holding message.
- **Private dashboard** — localhost only, token login, HttpOnly cookie, CSRF
  protection, rate limiting, security headers, audit log, key pool management,
  approval queue, and email preview.
- **Operations** — systemd user services, watchdog, daily digest, optional Docker
  dashboard, and a setup wizard.

## Architecture

```text
WhatsApp (unofficial Web client)
        │
        ▼
OpenClaw gateway (Node.js, systemd user service)
        ├── per-customer session + context
        ├── model pool: Gemini first, Groq fallback
        ├── secretary-guard plugin (scrubs technical errors)
        └── approval MCP tools (request email / calendar)
                 │
                 ▼
Local dashboard (Python, 127.0.0.1:8787)
        ├── approvals → Gmail / Google Calendar
        ├── leads, alerts, audit, key pool
        └── watchdog, monitor, digest timers
```

The dashboard and the approval queue run on the same machine as the gateway. No
customer data is sent to a third-party dashboard service.

## Requirements

- Linux or WSL2 with systemd user services
- Python **3.11+** (standard library only; on native Windows install `tzdata` for the
  full IANA timezone database)
- Node.js **22+** and [OpenClaw](https://openclaw.ai) installed globally
- A WhatsApp account for the bot (a separate number is recommended)
- At least one AI provider key (Gemini and/or Groq free tiers work)
- Optional: `gog` CLI + Google account for Gmail/Calendar, or the included
  Google Apps Script bridge

> WhatsApp Web automation can lead to a number ban. Use a dedicated number, respect
> WhatsApp's terms, and read the [security notes](SECURITY.md).

## Quick start

```bash
git clone https://github.com/AhsenKurtoski/whatsapp-digital-secretary.git
cd whatsapp-digital-secretary

# 1. Create the business configuration and the persona
python3 setup.py

# 2. Put your AI keys in the private environment file
install -m 600 secrets.env.example ~/.openclaw/secretary/secrets.env
${EDITOR:-nano} ~/.openclaw/secretary/secrets.env

# 3. Install dashboard, timers, plugin and gateway drop-ins
bash scripts/install.sh

# 4. Open the dashboard and copy the local token when asked
#    http://127.0.0.1:8787
#    token: ~/.openclaw/secretary/dashboard_token
```

The installer runs `sync_fallbacks.py`, which selects Gemini as the main model when
a Gemini key exists and keeps Groq as fallback. It restarts the gateway; WhatsApp may
take a minute to reconnect.

### WhatsApp connection

OpenClaw manages the WhatsApp session. On first run, open the OpenClaw control UI or
run its channel login command and scan the QR code with the bot's phone. Session files
are stored under `~/.openclaw` and must never be committed.

## Configuration

All business data lives in `~/.config/digital-secretary/config.json`. Start from
[`config.example.json`](config.example.json), or let `setup.py` create it.

| Field | Meaning |
| --- | --- |
| `business_name` | Name shown to customers and in the dashboard |
| `owner_name` | Owner's name used by the bot |
| `bot_name` | Assistant name |
| `owner_number` | Verified admin number in E.164 format |
| `google_account` | Gmail account used for follow-ups |
| `language` | Default language (`italiano`, `English`, …) |
| `timezone` | IANA timezone, e.g. `Europe/Rome` |
| `site_url` | Portfolio or website |
| `city` | Service area |
| `services` | List of services |
| `hours` | Opening hours / out-of-office text |
| `pricing_note` | Approved price wording; never invent prices |
| `dashboard_port` | Local dashboard port (default `8787`) |
| `max_concurrent` | AI calls processed in parallel (default `3`) |
| `max_reply_lines` | Maximum WhatsApp reply length |
| `whatsapp_number` | Bot number, for display only |

Environment variables such as `BUSINESS_NAME`, `OWNER_NUMBER` and
`DASHBOARD_PORT` override the JSON file.

### Persona

`persona_template.md` contains the behavioural rules. `setup.py` renders it with your
business data into `~/.openclaw/workspace/AGENTS.md`. Edit the template (not the
generated file) and re-run `python3 setup.py` to apply changes.

### AI provider pool

The dashboard **Keys** page stores keys in `~/.openclaw/secretary/secrets.env` with
mode `600` and can test each key. Gemini supports a comma-separated pool through
`GEMINI_API_KEYS`; Groq backup keys create `groq2…groq7` providers.

Free tiers have real limits. A single Gemini key may reject a burst of five
simultaneous conversations with HTTP 429. For heavy parallel use, add two or three
Gemini keys from separate projects/accounts, or lower `max_concurrent` and let the
queue absorb the burst.

### Gmail and Google Calendar

There are two options:

1. **Apps Script bridge (recommended, no domain)** — a private Apps Script web app
   signs requests with HMAC-SHA256 and exposes only `sendEmail` and `createEvent`.
   It avoids the 7-day refresh-token expiry of an OAuth app in Testing mode. See
   [`google-bridge/README.md`](google-bridge/README.md).
2. **Local `gog` CLI** — OAuth desktop flow. The dashboard invokes `gog` only after
   approval. Testing-mode apps may require re-authorization after about seven days.

The bot itself has no direct Gmail/Calendar tool. It can only create an approval
request; the dashboard executes the approved action.

## Security

- Dashboard binds to `127.0.0.1` only and requires a local token.
- Token comparison is constant-time; the cookie is `HttpOnly` + `SameSite=Strict`.
- POST requests require `X-Requested-With: dashboard` and a same-origin `Origin`.
- Security headers: CSP, `X-Frame-Options: DENY`, `nosniff`, referrer policy.
- Login rate limiting and token rotation from the Security tab.
- Secrets are stored in `600` files outside the repository.
- Tool profile is `minimal`; code mode is disabled; only the two approval MCP tools
  are exposed to the model.
- The guard plugin scrubs technical errors before they reach WhatsApp.
- Logs redact phone numbers and message content by default.

See [SECURITY.md](SECURITY.md) for the threat model and how to report a vulnerability.

## Tests

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile *.py google-bridge/*.py
node google-bridge/test_code_gs.js
python3 monitor.py --self-test
python3 watchdog.py --self-test
```

The suite covers configuration, persona rendering, approval storage, email template
safety, dashboard authentication/CSRF, watchdog status, and automatic model
selection. Integration with a real WhatsApp number and a real Google account cannot
be tested in CI; see [docs/TESTING.md](docs/TESTING.md) for the manual checklist.

## Docker (dashboard only)

The dashboard can run in a container while OpenClaw and WhatsApp run on the host:

```bash
cp .env.example .env
# edit .env
docker compose up -d --build
```

The compose file mounts `~/.openclaw` and `~/.config/digital-secretary`, publishes the
port on host loopback only, and reaches the gateway through the WSL2/Linux localhost
forwarding. Inside the container the dashboard listens on `0.0.0.0`, but it is never
exposed beyond `127.0.0.1` on the host. The image expects host UID/GID 1000 (the
default first Linux/WSL2 user). This is not a full WhatsApp container: the unofficial
WhatsApp client and its session are intentionally kept on the host, and systemd-only
widgets (gateway uptime, timers) are unavailable inside the container. Key management
and the restart button in the container do not control the host gateway: edit
`~/.openclaw/secretary/secrets.env` on the host or use the host dashboard.

## Deployment on a VPS

1. Install Node.js, OpenClaw and Python 3.11+.
2. Complete the OpenClaw onboarding and WhatsApp login.
3. Run `python3 setup.py` and `bash scripts/install.sh`.
4. Keep the dashboard on localhost and access it through an SSH tunnel:
   `ssh -L 8787:127.0.0.1:8787 user@vps`.
5. Enable user lingering (`sudo loginctl enable-linger $USER`) so services survive
   logout.
6. Back up `~/.openclaw` and `~/.config/digital-secretary` encrypted; they contain
   the WhatsApp session and private keys.

Do not expose port 8787 to the public Internet.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Dashboard says `auth` | Copy the token exactly from `~/.openclaw/secretary/dashboard_token` |
| WhatsApp keeps reconnecting | Check gateway logs; a 408 is normal during reconnect, restart only after repeated failures |
| `413` / context overflow | Use a model with a larger context or reduce the persona; Gemini is preferred for long prompts |
| `429` quota | Add another Gemini/Groq key or lower `maxConcurrent`; free tiers are limited |
| Gmail fails | Re-authorize `gog` or activate the Apps Script bridge; check the dashboard Connectors tab |
| Dashboard is slow | The status endpoint reads the watchdog snapshot; run `watchdog.py` once and check `watchdog_state.json` |
| Gateway startup is slow | Large session stores and first-run maintenance can take minutes; keep the service running and avoid restart loops |

## Roadmap

- Official WhatsApp Business Cloud API adapter
- Telegram and email channels
- Internal calendar with availability rules
- CSV export and CRM webhooks
- Voice-message transcription with a local model
- Helm chart and a hardened container image

## Contributing

Issues and pull requests are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) and
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) first. Never include real customer data,
tokens or phone numbers in an issue.

## License

MIT — see [LICENSE](LICENSE).

## Disclaimer

This project uses unofficial WhatsApp Web automation. It is not endorsed by or
affiliated with WhatsApp, Meta, Google, Groq or OpenClaw. You are responsible for
complying with WhatsApp's Terms of Service, Google's API policies, and applicable
privacy law (GDPR included). Use at your own risk.
