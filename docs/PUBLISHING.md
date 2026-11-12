# Publishing to GitHub

The live repository is [AhsenKurtoski/whatsapp-digital-secretary](https://github.com/AhsenKurtoski/whatsapp-digital-secretary).
It is public and MIT-licensed. The latest published release is
[v1.1.1](https://github.com/AhsenKurtoski/whatsapp-digital-secretary/releases/tag/v1.1.1).

`main` currently contains the changes listed under `## [Unreleased]` in
[CHANGELOG.md](../CHANGELOG.md). They are prepared for the next minor release,
**v1.2.0** (new demo quote preview, mobile email layout, approval-flow tests and
related documentation). v1.2.0 is not tagged yet.

| Version | State |
|---|---|
| `v1.1.1` | Latest published release |
| `v1.2.0` | Next release, from the current `Unreleased` section |

## 1. Final secret scan

Run from the repository root. Every command must print `CLEAN`.

```bash
# Replace the placeholders with your name, email and phone digits.
grep -rInE --exclude-dir=.git --exclude='*.png' --exclude-dir=__pycache__ \
  'YOUR_REAL_NAME|YOUR_REAL_EMAIL|YOUR_PHONE_DIGITS|YOUR_CITY' . || echo CLEAN

grep -rInE --exclude-dir=.git --exclude='*.png' --exclude-dir=__pycache__ \
  'gsk_[A-Za-z0-9]{10,}|AIza[0-9A-Za-z_-]{20,}|sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}' . || echo CLEAN

git status --short --branch
```

`git status` must be clean (or contain only your intentional changes). Never commit
`.env`, `secrets.env`, `dashboard_token`, `google_bridge.json`, WhatsApp sessions,
databases or the public website folder.

## 2. Push the current `main`

```bash
git fetch origin
git status --short --branch
git push origin main
```

For a fork, add the remote first:

```bash
git remote add origin https://github.com/<owner>/whatsapp-digital-secretary.git
git push -u origin main
```

If `gh` is not installed on Windows and you need it for the release commands:

```powershell
winget install --id GitHub.cli
gh auth login
```

## 3. Repository settings

The repository already uses these twelve topics. Keep them aligned when the scope
changes:

```bash
gh repo edit AhsenKurtoski/whatsapp-digital-secretary \
  --add-topic ai-assistant --add-topic apps-script --add-topic chatbot \
  --add-topic gmail --add-topic google-calendar --add-topic openclaw \
  --add-topic privacy --add-topic python --add-topic self-hosted \
  --add-topic systemd --add-topic whatsapp --add-topic whatsapp-bot
```

In **Settings → Code security** enable private vulnerability reporting and Dependabot
alerts. In **Settings → Actions → General** allow the default GitHub Actions workflow.
Keep the description and homepage current in **Settings → General**.

## 4. Prepare and release v1.2.0

### 4.1 Freeze the changelog and version

Before tagging:

1. In [CHANGELOG.md](../CHANGELOG.md), rename `## [Unreleased]` to
   `## [1.2.0] - YYYY-MM-DD`.
2. Add a fresh empty `## [Unreleased]` section and update the link references at the
   bottom of the file:
   - `[Unreleased]: .../compare/v1.2.0...HEAD`
   - `[1.2.0]: .../compare/v1.1.1...v1.2.0`
3. Set `VERSION = "1.2.0"` in `healthcheck.py`.
4. Update the **Latest release** row and verification date in `README.md` and
   `README.it.md`.
5. Re-read the rendered README, including these images:
   - `docs/screenshot-dashboard.png`
   - `docs/email-preview.png`
   - `docs/email-preview-mobile.png`
   - `docs/dashboard-preview-mobile.png` (dashboard at 375 px with the demo preview)

### 4.2 Run the final checks

```bash
python -m py_compile *.py google-bridge/*.py
python -m unittest discover -s tests
python google-bridge/test_bridge.py
node google-bridge/test_code_gs.js
```

The same checks run in `.github/workflows/ci.yml` on Python 3.11–3.14. Wait for the
CI run on the release commit to pass before creating the tag.

### 4.3 Tag and publish

```bash
git tag -a v1.2.0 -m "v1.2.0 — demo quote preview and mobile layout"
git push origin main
git push origin v1.2.0

gh release create v1.2.0 \
  --title "v1.2.0 — Demo quote preview and mobile layout" \
  --generate-notes
```

`--generate-notes` creates notes from merged changes. If you prefer curated notes,
extract the `## [1.2.0]` section into a temporary file and use `--notes-file` instead.

### 4.4 Verify the release

- Open the release page and check the title, tag and notes.
- Verify that CI is green for the tagged commit.
- Confirm that the README images load on GitHub and that the release links point to
  `v1.2.0`.
- Check that the dashboard is still shown as local-only (`127.0.0.1`), never exposed on
  a public port.

Future releases follow the same flow: move `Unreleased` under the new version, bump
`healthcheck.VERSION`, update the README release row, run the checks, tag, publish.

## 5. What stays private

- `~/.config/digital-secretary/config.json` — business data.
- `~/.openclaw/secretary/secrets.env` — API keys and keyring password.
- `~/.openclaw/secretary/dashboard_token` — dashboard access token.
- `~/.openclaw/secretary/google_bridge.json` — bridge URL and HMAC secret.
- `~/.openclaw/agents/...` — WhatsApp session and conversations.
- The public website folder and privacy pages (not part of this repository).

The public website is published separately on GitHub Pages. Its source and URL live
outside this repository and must never be committed here.

## 6. After publishing

- Verify the CI workflow passes on the release commit.
- Open the Issues tab and check that the templates render.
- Rotate any key that was ever pasted into a terminal, chat or issue.
- Keep the dashboard on `127.0.0.1`; never publish port 8787.
