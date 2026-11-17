# Disaster recovery: backup cifrati e ripristino verificato

Obiettivi: **RPO 24 ore** (un backup al giorno), **RTO 30 minuti** su un host
nuovo. Il ripristino non è dichiarato "ok" finché `backup.py --verify` non
conferma ogni hash SHA-256.

## Cosa entra nel backup

- `~/.config/digital-secretary/` (configurazione, chiavi in `secrets.env`)
- `~/.openclaw/secretary/` (persona, memoria, coda approvazioni, preventivi, PDF)
- `~/.openclaw/openclaw.json` (canali, policy anti-raffica, MCP)
- `~/.openclaw/agents/` (database e sessioni degli agenti, incluso WhatsApp)
- `~/.openclaw/workspace/AGENTS.md` (persona attiva)

`backup.key` è escluso di proposito: senza quella chiave il backup è inutile,
quindi va conservata **separatamente** (password manager o supporto offline).

## Creare e verificare

```bash
# Crea il backup cifrato (AES-256-GCM, scrypt) e ruota a 7 copie
python3 backup.py --create

# Verifica reale: decifra, estrae in una cartella temporanea, confronta gli hash
python3 backup.py --verify ~/.openclaw/secretary/backups/gina-*.ginabk

# Elenca i backup
python3 backup.py --list
```

Il comando stampa `ok: true` solo se tutti i file del manifest esistono con la
stessa dimensione **e** lo stesso SHA-256. Un archivio manomesso o una passphrase
sbagliata restituiscono `ok: false`.

### Automazione giornaliera (systemd)

```ini
# ~/.config/systemd/user/openclaw-backup.service
[Unit]
Description=Backup cifrato Gina
[Service]
Type=oneshot
WorkingDirectory=%h/whatsapp-digital-secretary
ExecStart=/usr/bin/python3 backup.py --create
ExecStartPost=/usr/bin/python3 backup.py --verify %h/.openclaw/secretary/backups/gina-latest.ginabk
```

Per un nome stabile, crea un symlink dopo il backup o usa lo script in
`scripts/backup-daily.sh` (crea, verifica e aggiorna `gina-latest.ginabk`).

```bash
systemctl --user enable --now openclaw-backup.timer
```

Copia i backup fuori dalla macchina (disco esterno o storage cifrato): un backup
sullo stesso disco non protegge da guasto o ransomware.

## Ripristino su un host nuovo

```bash
# 1. Prepara l'host (Python 3.11+, Node 22+, OpenClaw)
git clone https://github.com/AhsenKurtoski/whatsapp-digital-secretary.git
cd whatsapp-digital-secretary

# 2. Metti la passphrase nella posizione attesa (o usa la variabile d'ambiente)
install -m 600 /dev/null ~/.openclaw/secretary/backup.key
printf '%s\n' 'LA-TUA-PASSPHRASE' > ~/.openclaw/secretary/backup.key

# 3. Verifica PRIMA di toccare l'installazione viva
python3 backup.py --verify /percorso/gina-20261117-030000.ginabk

# 4. Ripristina sul filesystem (i percorsi assoluti vengono ricreati)
python3 backup.py --restore /percorso/gina-20261117-030000.ginabk --target /

# 5. Riallinea e avvia
bash scripts/install.sh
sudo loginctl enable-linger "$USER"
python3 ~/.openclaw/secretary/doctor.py --short
```

Il ripristino di prova su una cartella diversa è sicuro e non sovrascrive nulla:

```bash
python3 backup.py --restore archivio.ginabk --target /tmp/prova-ripristino
find /tmp/prova-ripristino -name config.json | head
```

## Drill trimestrale (obbligatorio)

1. Crea un backup nuovo.
2. Ripristinalo in `/tmp/drill` e verifica gli hash.
3. Avvia la dashboard con `SECRETARY_BASE` e `SECRETARY_CONFIG` puntati alla copia.
4. Apri un preventivo, scarica il PDF, controlla la coda approvazioni.
5. Annota data, durata e differenze in `docs/TESTING.md`.

Un backup mai ripristinato non è un backup.

## Se hai perso la chiave

Non esiste recupero: la cifratura è autenticata e senza passphrase i dati sono
illeggibili. Conserva una copia della passphrase in un password manager e una
copia cartacea in luogo sicuro. Se l'hai persa, ruota tutto: nuova installazione,
nuovo login WhatsApp, nuova chiave.

## Cosa non è coperto

- Messaggi WhatsApp scambiati **dopo** l'ultimo backup (fino a 24 ore).
- Token OAuth Google scaduti: dopo il ripristino riautorizza con il bridge.
- Segreti di terze parti non presenti in `secrets.env` (es. token webhook esterni).
