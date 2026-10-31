# Publishing to GitHub

The repository already has a clean history: no previous commits, no secrets and no
site files. Replace `YOUR_GITHUB_USER` with your account before running the commands.

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

## 2. Create the remote

With GitHub CLI already installed and authenticated:

```bash
cd whatsapp-digital-secretary
gh repo create whatsapp-digital-secretary \
  --public \
  --source=. \
  --remote=origin \
  --description "Self-hosted WhatsApp digital secretary with approval-gated Gmail and Calendar"
git push -u origin main
```

If `gh` is not installed on Windows:

```powershell
winget install --id GitHub.cli
gh auth login
```

Then run the `gh repo create` command above. Alternatively create an empty repository
in the browser and run:

```bash
git remote add origin https://github.com/YOUR_GITHUB_USER/whatsapp-digital-secretary.git
git push -u origin main
```

## 3. Topics and repository settings

```bash
gh repo edit --add-topic whatsapp --add-topic chatbot --add-topic openclaw \
  --add-topic gmail --add-topic google-calendar --add-topic python \
  --add-topic self-hosted --add-topic privacy
```

In **Settings → Code security** enable private vulnerability reporting and Dependabot
alerts. In **Settings → Actions → General** allow the default GitHub Actions workflow.

## 4. Release v1.0.0

```bash
git tag -a v1.0.0 -m "v1.0.0 — first public release"
git push origin v1.0.0

gh release create v1.0.0 \
  --title "v1.0.0 — WhatsApp Digital Secretary" \
  --notes-file CHANGELOG.md
```

Check the release page and the rendered README. The screenshot must load from
`docs/screenshot-dashboard.png`.

## 5. What stays private

- `~/.config/digital-secretary/config.json` — business data.
- `~/.openclaw/secretary/secrets.env` — API keys and keyring password.
- `~/.openclaw/secretary/dashboard_token` — dashboard access token.
- `~/.openclaw/secretary/google_bridge.json` — bridge URL and HMAC secret.
- `~/.openclaw/agents/...` — WhatsApp session and conversations.
- The public website folder and privacy pages (not part of this repository).

The website can be deployed separately to Netlify Drop from its own zip; it must not
be committed here.

## 6. After publishing

- Verify the CI workflow passes on the first push.
- Open the Issues tab and check that the templates render.
- Rotate any key that was ever pasted into a terminal, chat or issue.
- Keep the dashboard on `127.0.0.1`; never publish port 8787.
