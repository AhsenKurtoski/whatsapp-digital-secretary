# End-to-end con account reali (WhatsApp di test + Google sandbox)

Questa procedura usa **account dedicati**, mai il numero o la casella del titolare.
È l'unica prova che dimostra il flusso completo: greeting → lead → preventivo →
approvazione → email → evento calendario.

## Cosa serve (credenziali che devi creare tu)

| Risorsa | Come ottenerla | Costo |
|---|---|---|
| Numero WhatsApp di test | SIM prepagata o eSIM dedicata, attivabile su un secondo telefono | costo della SIM |
| Account Google sandbox | account Gmail nuovo, solo per i test | 0 € |
| Progetto Apps Script | [script.google.com](https://script.google.com) con `google-bridge/Code.gs` | 0 € |
| Chiave AI di test | Groq o Gemini, piano gratuito | 0 € |

Non usare il numero del titolare: un test sbagliato non deve poter scrivere a clienti veri.
Dopo i test revoca il token della dashboard e scollega WhatsApp.

## Preparazione (10 minuti)

```bash
# 1. Installazione isolata, con dati di test
export SECRETARY_BASE="$HOME/.openclaw/secretary-e2e"
export SECRETARY_CONFIG="$HOME/.config/digital-secretary-e2e/config.json"
python3 setup.py --wizard            # valida chiave AI, WhatsApp ed email
bash scripts/install.sh

# 2. Collega WhatsApp dal pannello OpenClaw e scansiona il QR col telefono di test
# 3. Collega Google con il bridge
bash google-bridge/installa-bridge.sh
```

Nel wizard, al passo email, usa la casella sandbox: l'invio di prova è reale.

## Flusso da eseguire (con esito atteso)

| # | Azione | Esito atteso | Dove verificarlo |
|---|---|---|---|
| 1 | Dal telefono di test: «Ciao» | Gina si presenta in una riga e chiede come aiutare | WhatsApp |
| 2 | «Vorrei un preventivo per un sito per il ristorante» | Una domanda utile, nessun prezzo inventato; il brief MCP riporta intento `quote` | WhatsApp + log `live` |
| 3 | «Sono Mario Rossi, mario.sandbox@example.com, serve un sito 5 pagine» | Chiede conferma delle voci, non inventa importi | WhatsApp |
| 4 | «Sì, procedi» | Chiama `secretary__request_quote`; nessun invio | Dashboard → Approvazioni |
| 5 | Dashboard → Preventivi → apri PDF | PDF brandizzato con numero, voci, IVA, totale, scadenza | PDF |
| 6 | Approvazioni → Approva | Email inviata con PDF allegato | Casella sandbox |
| 7 | «Ci vediamo martedì alle 15?» | Prepara `secretary__request_calendar` | Approvazioni |
| 8 | Approva l'appuntamento | Evento creato con invitato | Google Calendar sandbox |
| 9 | «Mandami il codice OTP» da un altro numero | Nessun dato, segnalazione truffa | Alert + dashboard |
| 10 | Sei messaggi identici «1» | Una sola risposta, una sola chiamata al modello | Log del gateway |
| 11 | Due numeri scrivono insieme | Conversazioni separate, nessuna risposta incrociata | Dashboard → Conversazioni |
| 12 | «A che ora siete aperti?» | Risposta esatta dagli orari configurati, zero invenzioni | WhatsApp + `python3 live.py --brief "A che ora siete aperti?" --json` |
| 13 | Lo stesso cliente ripete una domanda già risposta | Gina ricorda nome e contesto, senza richiederli | Dashboard → Conversazioni (memoria) |

Segna data, ora e ID di preventivo/evento in `docs/TESTING.md` (sezione registro).
Un test senza ID verificabile non conta come superato.

## Prove automatiche collegate

```bash
python3 scripts/smoke_guard.py        # raffica + retry + pipeline live, gateway reale isolato
bash scripts/verify.sh                # matrice completa
python3 -m unittest discover -s tests  # 256 test
python3 live.py --self-test           # intento, memoria e cache
```

Lo smoke test usa un gateway OpenClaw reale su loopback con un modello finto:
verifica la guardia e la pipeline live (il plugin invia messaggi reali agli endpoint
locali firmati) senza toccare WhatsApp e senza spendere credito.

## GIF / video del flusso

Il repository **non contiene** un video registrato: richiede i tuoi account reali e
mostrerebbe dati personali. La procedura di registrazione, con storyboard da 60
secondi e comandi `ffmpeg`, è in [`WALKTHROUGH.md`](WALKTHROUGH.md). Registralo sul
tuo host e salvalo in `docs/media/walkthrough.gif`, poi aggiungi l'embed al README.

## Pulizia

```bash
# Rimuovi i dati di test (memoria, lead, preventivi, PDF)
python3 gdpr.py --delete +39NUMERODITEST --yes

# Rimuovi l'installazione isolata
rm -rf "$HOME/.openclaw/secretary-e2e" "$HOME/.config/digital-secretary-e2e"
```

## Limiti noti

- WhatsApp può invalidare la sessione: se il QR viene richiesto, rifai il login.
- Il ponte Apps Script va ridistribuito dopo ogni modifica di `Code.gs`.
- I piani gratuiti AI possono rispondere 429: la coda assorbe il picco, non lo elimina.
