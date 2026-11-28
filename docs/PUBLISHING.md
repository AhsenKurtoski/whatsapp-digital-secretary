# Publishing to GitHub

The live repository is [AhsenKurtoski/whatsapp-digital-secretary](https://github.com/AhsenKurtoski/whatsapp-digital-secretary).
It is public and MIT-licensed. The latest published release is
[v1.4.0](https://github.com/AhsenKurtoski/whatsapp-digital-secretary/releases/tag/v1.4.0)
(28 November 2026), whose notes live in
[the v1.4.0 notes](releases/v1.4.0.md) and in the `## [1.4.0]` section of
[CHANGELOG.md](../CHANGELOG.md).

`main` now carries only new changes under `## [Unreleased]`, prepared for the next
release (**v1.5.0**). Nothing in `Unreleased` is tagged or published yet.

| Version | State |
|---|---|
| `v1.4.0` | Latest published release (2026-11-28) |
| `v1.2.0` | Superseded draft notes, never tagged (kept in `docs/releases/`) |
| `v1.1.1` | Previous release |
| `v1.5.0` | Next release, from the current `Unreleased` section |

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

## 4. Prepare and release the next version

The flow below is the one used for v1.4.0; replace `X.Y.Z` and the title.

### 4.1 Freeze the changelog and version

Before tagging:

1. In [CHANGELOG.md](../CHANGELOG.md), rename `## [Unreleased]` to
   `## [X.Y.Z] - YYYY-MM-DD`.
2. Add a fresh empty `## [Unreleased]` section and update the link references at the
   bottom of the file:
   - `[Unreleased]: .../compare/vX.Y.Z...HEAD`
   - `[X.Y.Z]: .../releases/tag/vX.Y.Z`
3. Set `VERSION = "X.Y.Z"` in `healthcheck.py`.
4. Set `version = "X.Y.Z"` in `pyproject.toml`.
5. Write the release notes in `docs/releases/vX.Y.Z.md` (upgrade steps first, then the
   changelog highlights) and link them from this file.
6. Update the **Version** row in `README.md` and `README.it.md`.
7. Re-read the rendered README, including these images:
   - `docs/screenshot-dashboard.png`
   - `docs/email-preview.png`
   - `docs/email-preview-mobile.png`
   - `docs/dashboard-preview-mobile.png` (dashboard at 375 px with the demo preview)

### 4.2 Run the final checks

```bash
python3 -m py_compile *.py google-bridge/*.py
python3 -m unittest discover -s tests
python3 google-bridge/test_bridge.py
node google-bridge/test_code_gs.js
python3 link_check.py
python3 healthcheck.py --self-test
python3 doctor.py --self-test
```

The same checks run in `.github/workflows/ci.yml` on Python 3.11–3.14. Wait for the
CI run on the release commit to pass before creating the tag.

### 4.3 Tag and publish

```bash
git tag -a vX.Y.Z -m "vX.Y.Z — short title"
git push origin main
git push origin vX.Y.Z

gh release create vX.Y.Z \
  --title "vX.Y.Z — Short title" \
  --notes-file docs/releases/vX.Y.Z.md
```

Use curated notes from `docs/releases/`, not `--generate-notes`: the changelog is the
source of truth and the release page must show the upgrade steps.

### 4.4 Verify the release

- Open the release page and check the tag, title and notes.
- Verify that CI is green for the tagged commit.
- Confirm that the README version row and the CHANGELOG links point to `vX.Y.Z`.
- Check that the dashboard is still shown as local-only (`127.0.0.1`), never exposed on
  a public port.

For v1.4.0 the published tag is `v1.4.0` at the commit that contains this document's
release notes.

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
