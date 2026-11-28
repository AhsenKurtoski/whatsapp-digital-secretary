# Segreteria su Windows con WSL2

La segreteria gira su **Linux con systemd utente**: usa `bash`, `systemctl --user` e i
timer. Su Windows nativo quei comandi non esistono, quindi la strada supportata è
**WSL2 con Ubuntu**. Questa guida porta da un PC Windows senza nulla a un bot che
risponde su WhatsApp, con la dashboard su `http://127.0.0.1:8787`.

> Il supporto Docker è stato rimosso: se cerchi un container, non c'è più. WSL2 è
> l'unico modo supportato per usare Windows.

## Prima di tutto: c'è già un'installazione?

Apri **PowerShell** e chiedi quali distro hai:

```powershell
wsl -l -v
```

Poi, dal repo (dentro WSL), lo script di stato risponde da solo: legge distro, cartella
del repo, dipendenze, installazione, gateway e tool del bot, e stampa **il prossimo
comando da eseguire**:

```bash
python3 scripts/wsl_status.py
```

Esempio di risposta quando manca tutto tranne WSL:

```
✅ Python 3: presente
❌ cryptography: mancante
✅ Repo: non trovato
❌ Installazione in ~/.openclaw/secretary: assente
🟡 Gateway: unknown

Prossimo comando (wsl):
  git clone https://github.com/AhsenKurtoski/whatsapp-digital-secretary.git && cd whatsapp-digital-secretary
  motivo: Cartella del repo non trovata nella home della distro.
```

Con `--json` ottieni lo stesso stato in formato macchina; con `--distro Debian` scegli
quale distro interrogare. Se hai Python anche su Windows puoi lanciarlo da PowerShell
scaricando il solo script:

```powershell
curl.exe -fsSL https://raw.githubusercontent.com/AhsenKurtoski/whatsapp-digital-secretary/main/scripts/wsl_status.py -o "$env:TEMP\wsl_status.py"
py "$env:TEMP\wsl_status.py"
```

## 1. Installa WSL2 e Ubuntu

Serve Windows 10 (21H2 o successivo) o Windows 11, con la virtualizzazione attiva nel
BIOS. In **PowerShell come amministratore**:

```powershell
wsl --install -d Ubuntu
```

Riavvia il PC quando richiesto. Al primo avvio di **Ubuntu** (menu Start) crea utente e
password Linux: sono separati dall'account Windows e li userai per `sudo`.

Se WSL c'è già ma è vecchio:

```powershell
wsl --update
wsl -l -v          # deve mostrare VERSION 2
```

## 2. Attiva systemd nella distro

Dentro Ubuntu:

```bash
systemctl --user status
```

Se risponde `System has not been booted with systemd`, abilitalo:

```bash
sudo tee /etc/wsl.conf >/dev/null <<'EOF'
[boot]
systemd=true
EOF
```

Poi, da PowerShell, riavvia la distro e rientra:

```powershell
wsl --shutdown
wsl -d Ubuntu
```

`systemctl --user status` ora deve elencare i servizi utente.

## 3. Installa i pacchetti di base

Sempre dentro Ubuntu:

```bash
sudo apt update
sudo apt install -y git python3 python3-pip python3-cryptography curl
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt install -y nodejs
node --version     # 22.x
```

`python3-cryptography` è obbligatorio: serve alla memoria clienti e ai backup cifrati
(AES-256-GCM). Se manca, `scripts/install.sh` e `doctor.py` si fermano subito con il
comando da eseguire.

Installa **OpenClaw 2026.4.22+** seguendo la guida ufficiale (<https://openclaw.ai>) e
completa `openclaw onboard`, poi verifica:

```bash
openclaw --version
```

## 4. Clona il repo nella home di Linux (non in `/mnt/c`)

```bash
cd ~
git clone https://github.com/AhsenKurtoski/whatsapp-digital-secretary.git
cd whatsapp-digital-secretary
```

Evita `C:\Users\...` (`/mnt/c/...`): è lento, i permessi sono diversi e `systemd` non
può eseguire gli script da lì. Tieni il progetto in `~/whatsapp-digital-secretary`.
I file di Windows restano visibili da `/mnt/c`, ma il progetto deve stare nel
filesystem Linux.

## 5. Configura l'attività

```bash
python3 setup.py
```

Ti chiede solo dati dell'attività (nome, numero del titolare, servizi, prezzi, orari,
timezone) e scrive `~/.config/digital-secretary/config.json`.

Poi crea il file dei segreti e inserisci le chiavi AI:

```bash
install -m 600 secrets.env.example ~/.openclaw/secretary/secrets.env
nano ~/.openclaw/secretary/secrets.env
```

Le chiavi non vanno mai nel repo, in chat o nei log.

## 6. Installa la segreteria

```bash
bash scripts/install.sh
```

L'installer copia dashboard, plugin e timer in `~/.openclaw/secretary`, registra i tool
MCP, riavvia il gateway e **pretende** che il riavvio riesca. Alla fine stampa:

- `bot tools: request_calendar,request_email,request_quote,…` → il bot vede il comando
  per il preventivo PDF;
- la diagnosi di `doctor.py` senza `❌`.

Se il riavvio del gateway fallisce, l'installazione esce con errore e ti dice cosa
controllare: non finge di essere riuscita.

## 7. Collega WhatsApp

Apri la **control UI di OpenClaw** (l'URL lo stampa `openclaw` all'avvio) oppure esegui
il comando di login del canale della tua versione di OpenClaw, come indicato nella sua
guida: la sintassi cambia tra versioni, quindi non è fissata qui. Scansiona il QR con il
telefono del bot. La sessione vive in `~/.openclaw` (dentro WSL): non copiarla, non
committarla.

## 8. Verifica

```bash
python3 scripts/wsl_status.py        # deve proporre solo "openclaw mcp probe secretary --json"
python3 ~/.openclaw/secretary/doctor.py --short
openclaw mcp probe secretary --json  # elenca request_quote
```

Poi la prova vera: da un **secondo telefono** chiedi un preventivo con la tua email. Il
bot deve preparare la richiesta (non dire che il comando non esiste) e la richiesta
compare in **dashboard → Approvazioni** con il PDF; dopo l'approvazione parte l'email.

Apri la dashboard da Windows su <http://127.0.0.1:8787>: WSL2 inoltra `localhost`. Il
token è in `~/.openclaw/secretary/dashboard_token` (dentro Ubuntu:
`cat ~/.openclaw/secretary/dashboard_token`).

## 9. Restare accesi

```bash
sudo loginctl enable-linger "$USER"
```

Così i servizi utente partono senza login. Attenzione però: **WSL2 si ferma quando
Windows va in sospensione, si spegne o esegue `wsl --shutdown`**, e in quel momento il
bot non risponde. Per un servizio davvero 24/7 usa una macchina Linux accesa (VPS o mini
PC): vedi [ALWAYS_ON.md](ALWAYS_ON.md).

## Aggiornare un'installazione che esiste già

Da Ubuntu (non da PowerShell, e non in `/mnt/c`):

```bash
cd ~/whatsapp-digital-secretary
git status --short
git pull --ff-only
grep '^version' pyproject.toml     # deve dire 1.4.0
bash scripts/install.sh
python3 doctor.py
```

L'installer riavvia dashboard e gateway e non tocca configurazione né segreti. Per capire
se sei ancora sulla vecchia versione:

```bash
git describe --tags          # v1.1.1 = vecchia; la release corrente è v1.4.0
sha256sum ~/.openclaw/secretary/dashboard.py dashboard.py
```

Se `git pull` risponde `not a git repository`, la cartella è quella di Windows: il repo
vive nella home di Linux (`~/whatsapp-digital-secretary`).

## Problemi frequenti

| Sintomo | Causa e rimedio |
| --- | --- |
| `cd whatsapp-digital-secretary` → percorso inesistente, `git pull` → `not a git repository` | Sei in `C:\Users\...` (Windows nativo): il repo è dentro WSL. Apri Ubuntu e lavora in `~/whatsapp-digital-secretary` |
| `bash: scripts/install.sh: No such file or directory` | Stessa cosa: l'installer va eseguito dentro WSL, non in CMD/PowerShell |
| `System has not been booted with systemd` | Manca `[boot] systemd=true` in `/etc/wsl.conf`, oppure non hai fatto `wsl --shutdown` dopo averlo scritto |
| `error: externally-managed-environment` (pip) | Usa `sudo apt install python3-cryptography` oppure una venv (`python3 -m venv .venv && . .venv/bin/activate`) |
| Il bot dice che non può generare il PDF | Il gateway non vede i tool: `python3 ~/.openclaw/secretary/doctor.py --short` (controllo **Tool visibili al gateway**), poi `python3 ~/.openclaw/secretary/approval_setup.py && systemctl --user restart openclaw-gateway` |
| La dashboard non si apre da Windows | Apri `http://127.0.0.1:8787` in un browser **Windows** (non dentro WSL); controlla `systemctl --user status openclaw-dashboard` e che la porta non sia occupata |
| Tutto lento, `git status` infinito | Il repo è su `/mnt/c`: spostalo in `~/whatsapp-digital-secretary` |
| Dopo il riavvio di Windows il bot non risponde | WSL non è più in esecuzione: riapri Ubuntu (o configura l'avvio automatico) e controlla `systemctl --user status openclaw-gateway` |

## Comandi utili

| Cosa | Comando |
| --- | --- |
| Stato completo + prossimo passo | `python3 scripts/wsl_status.py` |
| Diagnosi installazione | `python3 ~/.openclaw/secretary/doctor.py --short` |
| Tool visti dal bot | `openclaw mcp probe secretary --json` |
| Riavvia il gateway | `systemctl --user restart openclaw-gateway` |
| Log del gateway | `journalctl --user -u openclaw-gateway -n 100 --no-pager` |
| Test automatici del progetto | `bash scripts/verify.sh` |
