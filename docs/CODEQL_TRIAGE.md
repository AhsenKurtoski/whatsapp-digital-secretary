# Triage degli alert CodeQL

Questo file registra la revisione **manuale** di ogni alert di code scanning.
Un alert non si chiude perché dà fastidio: si chiude perché è stato letto, il
codice è stato controllato e la decisione è scritta qui.

Regole:

- **Corretto** = il codice è stato cambiato e c'è un test che lo dimostra.
- **Falso positivo** = il pattern segnalato non è raggiungibile o è già
  neutralizzato; va indicato *dove* viene neutralizzato.
- **Scelta voluta** = il comportamento è necessario al prodotto (es. mostrare
  un segreto una volta sola); va indicato il perché e il limite.
- **Solo test** = il codice segnalato vive nella suite di test, non nel runtime.

Aggiornare questa tabella quando si chiude o si apre un alert. La revisione è
riferita al commit `acd6261` (main) e allo stato degli alert al 26/11/2026.

## Corretti

| Alert | Regola | Posizione | Correzione |
|---|---|---|---|
| #16, #18, #20, #21 | `py/tarslip` | `backup.py` (`verify`, `restore`) | `_safe_extract()`: niente `extractall`, validazione di ogni membro, rifiuto di percorsi assoluti, `..`, link simbolici, hard link, dispositivi e FIFO. Test in `tests/test_backup_archive.py`. |

## Falsi positivi

| Alert | Regola | Posizione | Perché |
|---|---|---|---|
| #24 | `py/command-line-injection` | `conversations.py:257` | `subprocess.run(argv, ...)` riceve una **lista** e non usa `shell=True`: non esiste interpretazione da shell. Gli argomenti sono numero validato (E.164) e testo senza caratteri di controllo. |
| #19 | `py/command-line-injection` | `dashboard.py:196` | `run(cmd: list[str])` è un helper generico; i chiamanti passano solo liste di letterali (`systemctl`, `python3`, percorsi interni). Nessuna shell, nessun input utente nella posizione del comando. |
| #8, #9 | `py/clear-text-logging-sensitive-data` | `healthcheck.py:1426`, `1428` | L'output contiene `status`, `label` e `detail` ("token presente", permessi). Il campo `token` nella struttura è **metadato**, non il valore del token: il docstring del modulo dice "non stampa mai token, chiavi o contenuti dei clienti". |
| #10 | `py/clear-text-logging-sensitive-data` | `setup.py:197` | Stampa il **percorso** di `secrets.env` ("Segreti: …"), non il contenuto. |
| #12 | `py/polynomial-redos` | `lead_intel.py:93` | `redact()` fa `str(text)[:MAX_TEXT]` **prima** di ogni regex. |
| #13 | `py/polynomial-redos` | `lead_intel.py:149` | `classify()` tronca a `MAX_TEXT` prima delle regex e `_scam()` ora tronca a sua volta (difesa in profondità). |
| #14, #15 | `py/polynomial-redos` | `live.py:129` | `is_generic()` esce con `len(answer) > MAX_ANSWER` prima di `PERSONAL_RE.search`. |

## Scelte volute

| Alert | Regola | Posizione | Perché |
|---|---|---|---|
| #5 | `py/clear-text-storage-sensitive-data` | `backup.py:83` | `backup.key` **è** la passphrase dei backup, in un file `0600`. Cifrarla con sé stessa non ha senso: la chiave va conservata fuori dal disco, come dice `SECURITY.md`. |
| #6 | `py/clear-text-storage-sensitive-data` | `dashboard.py:787` | `secrets.env` è il file delle chiavi API dei provider, `0600`, scritto solo dalla dashboard locale e letto dai processi della segreteria. |
| #22 | `py/clear-text-storage-sensitive-data` | `identity.py:347` | Il seed TOTP deve essere leggibile per generare i codici a 6 cifre; il file è `0600`. |
| #23 | `py/clear-text-logging-sensitive-data` | `identity.py:500` | All'attivazione il comando stampa una volta l'URL `otpauth://` (contiene il seed) perché l'utente possa inquadrare il QR. È l'unico modo per consegnarlo; non viene mai riscritto nei log. |
| #11 | `py/clear-text-logging-sensitive-data` | `webhooks.py:313` | Il secret del webhook viene mostrato **una volta** alla creazione ("Salva il secret ora"): non è recuperabile dopo, ed è l'unico momento in cui può essere consegnato al proprietario. |
| #1 | `js/file-access-to-http` | `plugins/secretary-guard/index.js:79` | Il plugin legge `dashboard_token` (`0600`) e lo manda all'URL della dashboard locale (`GINA_LIVE_URL`, default `http://127.0.0.1:8787`) per autenticarsi. È esattamente il suo scopo. |

## Solo test

| Alert | Regola | Posizione | Perché |
|---|---|---|---|
| #2, #3, #4 | `py/bad-tag-filter` | `tests/test_dashboard_i18n.py` | `HTMLParser` usato per controllare le stringhe delle traduzioni nei test. |
| #7 | `py/clear-text-storage-sensitive-data` | `tests/test_runtime.py:63` | Un segreto temporaneo creato dal test dentro una `TemporaryDirectory`. |
| #17 | `py/incomplete-url-substring-sanitization` | `site_build.py:342` | È dentro `self_test()`: asserzioni sul sito generato, non codice di produzione. |

## Come verificare che il triage resti valido

```bash
# La suite copre i percorsi citati (backup ostile, redact, is_generic, guard).
.venv/bin/python -m unittest discover -s tests
# gitleaks non deve vedere segreti veri nella storia.
gitleaks detect --source . --config .gitleaks.toml --redact --no-banner
```

Se un alert riappare dopo una modifica, non si aggiorna la tabella: si guarda
il codice nuovo.
