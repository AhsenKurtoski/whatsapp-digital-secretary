# Contributing

Thanks for helping improve the WhatsApp Digital Secretary. This project aims to stay
small, readable and safe to self-host.

## Before you start

- Read [README.md](README.md) and [SECURITY.md](SECURITY.md).
- Search existing issues and pull requests.
- For a security problem, follow the private reporting process in `SECURITY.md`.
- For a large feature, open an issue first so we can agree on the scope.

## Development setup

```bash
git clone https://github.com/your-name/whatsapp-digital-secretary.git
cd whatsapp-digital-secretary
python3 -m unittest discover -s tests -v
```

The tests use only the Python standard library and an isolated temporary home. They do
not touch your real `~/.openclaw`, WhatsApp session or Google account.

For a manual end-to-end run you need Node.js, OpenClaw and a WhatsApp test number. Never
use your primary WhatsApp number for development.

## What to change

- Keep business data in `config.example.json`, never hard-coded in Python or in the
  persona template.
- Keep secrets out of the repository. New settings go in `*.env.example` with comments.
- Do not add a dependency unless the standard library cannot do the job. The dashboard
  and tools are deliberately dependency-free.
- Preserve the security model: the model gets only the approval tools, the dashboard
  stays on localhost, and no endpoint executes arbitrary commands.
- User-facing strings are in Italian; code comments and documentation may be in English.

## Tests

Add or update tests for every behavioural change:

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile *.py google-bridge/*.py
node google-bridge/test_code_gs.js
python3 monitor.py --self-test
python3 watchdog.py --self-test
```

If your change needs a real WhatsApp number or Google account, describe the manual test
you performed in the pull request. Mock everything that can be mocked.

## Style

- Python: 4 spaces, type hints where useful, `snake_case`, no bare `except`.
- JavaScript/HTML: 2 spaces, no framework, progressive enhancement.
- Commits: `fix: …`, `feat: …`, `docs: …`, `test: …`, `chore: …`.
- Keep pull requests focused. One concern per PR.

## Pull request checklist

- [ ] Tests pass locally.
- [ ] No secrets, phone numbers, customer names or tokens in the diff.
- [ ] Documentation updated (`README.md`, `README.it.md`, `CHANGELOG.md`).
- [ ] Security implications considered.
- [ ] The change works on mobile-width screens if it touches the dashboard or site.

## License

By contributing you agree that your work is released under the MIT License.
