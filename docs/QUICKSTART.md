# Guida rapida (per chi non è tecnico)

Questa guida porta dalla prima installazione alla prima risposta reale su WhatsApp in
circa **15 minuti**, senza gergo. Se qualcosa non torna, la sezione
[Risoluzione problemi](../README.it.md#risoluzione-problemi) del README ha i messaggi
d'errore più comuni con il comando esatto che li ripara.

> Serve un computer acceso con connessione stabile. Non serve Docker, non serve un
> server a pagamento, non serve una carta di credito.

## 1. Cosa ti serve

| Cosa | Perché |
|---|---|
| Un computer (Windows con WSL2, Linux o macOS) | La segreteria gira sul tuo computer, non su un servizio esterno |
| Un numero WhatsApp dedicato al bot | Consigliato: il bot non deve usare il tuo numero personale |
| Il telefono di quel numero, per scansionare il QR | Solo la prima volta, poi la sessione resta salvata |
| Una chiave AI gratuita (Gemini e/o Groq) | Per scrivere le risposte; le chiavi restano sul tuo computer |

## 2. Preparare il computer

### Windows

Segui [`docs/WSL.md`](WSL.md): installa WSL2 con Ubuntu, apri il terminale Ubuntu e
lavora **dentro la cartella di Linux** (non in `/mnt/c`, che è lento e rompe i permessi).
Quando hai finito, torna qui al punto 3 e usa gli stessi comandi dentro Ubuntu.

### Linux o macOS

Apri il terminale. Se non hai `git`, installalo (`sudo apt install git` su Ubuntu).

## 3. Scaricare e configurare

```bash
git clone https://github.com/AhsenKurtoski/whatsapp-digital-secretary.git
cd whatsapp-digital-secretary
python3 setup.py
```

`setup.py` fa domande semplici: nome dell'attività, città, orari, servizi, tono del bot,
numero del titolare. Le risposte finiscono in `config.json` e puoi cambiarle quando vuoi
rieseguendo lo stesso comando. Non serve toccare il codice.

## 4. Inserire le chiavi AI

Crea il file privato dei segreti e aprilo con l'editor:

```bash
install -m 600 secrets.env.example ~/.openclaw/secretary/secrets.env
${EDITOR:-nano} ~/.openclaw/secretary/secrets.env
```

Incolla la chiave Gemini (da Google AI Studio) e, se vuoi, una chiave Groq come riserva.
Salva e chiudi: `Ctrl+O`, `Invio`, `Ctrl+X` in nano. **Non incollare mai queste chiavi in
chat, email o GitHub.**

## 5. Installare e accendere

```bash
bash scripts/install.sh
```

Lo script installa la dashboard, i controlli automatici e i timer, poi riavvia il
gateway. Alla fine stampa il percorso del token locale.

## 6. Collegare WhatsApp

Apri la dashboard su <http://127.0.0.1:8787>, incolla il token che trovi in
`~/.openclaw/secretary/dashboard_token`, poi segui il login del canale WhatsApp di
OpenClaw e scansiona il QR con il telefono del bot. Se il QR non compare, esegui
`python3 ~/.openclaw/secretary/doctor.py --short`: ti dice il comando esatto.

## 7. Prima prova vera

1. Da un **altro** telefono scrivi al numero del bot: «Ciao, vorrei un preventivo».
2. La risposta arriva su WhatsApp in pochi secondi.
3. In dashboard apri **Conversazioni**: vedi il transcript con i dati personali
   mascherati.
4. Premi **Prendi il controllo**, scrivi nella casella e premi **Invia**: il messaggio
   parte dal numero del bot e Gina resta in silenzio.
5. Premi **Restituisci a Gina** per tornare alla risposta automatica.

## 8. Se vuoi che risponda sempre (anche di notte)

La segreteria risponde finché il computer è acceso e sveglio. Per il 24/7 gratuito e per
la sola finestra **10:00–20:00** vedi [`docs/ALWAYS_ON.md`](ALWAYS_ON.md).

## 9. Cosa controllare ogni tanto

- Dashboard → **Health**: semaforo verde su gateway, WhatsApp, timer e Google.
- `python3 ~/.openclaw/secretary/doctor.py --short`: cosa manca e come ripararlo.
- Dashboard → **Sicurezza**: verifica integrità del registro e sessioni attive.

## 10. Aggiornare la segreteria

Una copia installata non si aggiorna da sola. Quando esce una versione nuova (l'ultima è
la **1.4.0**), apri il terminale nella cartella del repository e riesegui l'installer:

```bash
cd ~/whatsapp-digital-secretary
git pull --ff-only
grep '^version' pyproject.toml     # deve dire 1.4.0
bash scripts/install.sh
python3 doctor.py
```

I dati dell'attività (`~/.config/digital-secretary/config.json`) e le chiavi
(`~/.openclaw/secretary/secrets.env`) non vengono toccati. Se `doctor.py` non segnala
`FAIL`, la segreteria è aggiornata. I dettagli e la verifica della copia installata sono
nel [README](../README.it.md#aggiornare-uninstallazione-esistente).

## Errori tipici e soluzione in una riga

| Messaggio | Cosa fare |
|---|---|
| `Impossibile trovare il percorso specificato` su Windows | Sei in PowerShell: entra in WSL con `wsl`, poi `cd ~/whatsapp-digital-secretary` |
| `not a git repository` | Sei nella cartella sbagliata: `cd ~/whatsapp-digital-secretary` |
| `ModuleNotFoundError: cryptography` | `sudo apt install python3-cryptography` |
| Il bot non risponde | `python3 ~/.openclaw/secretary/doctor.py --short`, poi `systemctl --user restart openclaw-gateway` |
| WhatsApp scollegato | Riscansiona il QR dal telefono del bot (WhatsApp Web può invalidare la sessione) |

Per capire cosa fa il progetto prima di installarlo, `python3 demo.py` apre una dashboard
dimostrativa su `http://127.0.0.1:8799` senza inviare nulla e senza toccare la tua
installazione.
