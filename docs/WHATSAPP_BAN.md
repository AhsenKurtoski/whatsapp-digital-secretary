# Se WhatsApp banna il numero: piano di ripristino

Un ban può arrivare senza preavviso. Non è un guasto del software: è una
decisione di WhatsApp. Questo piano riduce i tempi e protegge i clienti.

## Riconoscere il problema

- Il gateway resta connesso ma ogni invio fallisce con errore di autorizzazione.
- L'interfaccia OpenClaw mostra la sessione come non valida / logged out.
- `python3 healthcheck.py --json` segnala WhatsApp non sano.
- Il QR viene richiesto e, dopo la scansione, WhatsApp rifiuta il numero.

Non cancellare subito la sessione: serve come prova per il ricorso.

## Minuti 0–30: contenimento

```bash
# 1. Ferma l'invio automatico: il bot non deve ritentare a vuoto
systemctl --user stop openclaw-gateway

# 2. Fotografa lo stato (non inviare nulla)
python3 healthcheck.py --json > ~/incidente-whatsapp.json
cp -a ~/.openclaw/agents ~/backup-agents-incidente
```

3. Disattiva i webhook che potrebbero generare messaggi automatici.
4. Non usare strumenti non ufficiali per "sbloccare": peggiorano la situazione.

## Ore 0–24: ricorso e canale alternativo

1. Apri un ricorso dal supporto WhatsApp Business (o dall'app) allegando:
   numero, data/ora del blocco, messaggio d'errore, uso previsto (segreteria
   aziendale con consenso dei clienti), volume tipico.
2. Attiva un canale alternativo per i clienti: email, telefono, modulo sul sito.
3. Se hai un secondo numero già verificato, collegalo come **numero di emergenza**
   e aggiorna il link `wa.me` sul sito.
4. Avvisa i clienti con un messaggio breve e onesto (template sotto).

## Ripristino su un numero nuovo

```bash
# 1. Backup prima di toccare la sessione
python3 backup.py --create
python3 backup.py --verify ~/.openclaw/secretary/backups/<archivio>.ginabk

# 2. Scollega la sessione vecchia (dall'interfaccia OpenClaw)
#    e cancella SOLO la sessione WhatsApp, non tutto il database:
systemctl --user stop openclaw-gateway
python3 - <<'PY'
import config, sqlite3, pathlib
db = pathlib.Path(config.db_path())
print("database:", db, "esiste:", db.exists())
PY

# 3. Aggiorna il numero del bot nella configurazione
python3 setup.py --non-interactive --owner-number +39NUOVONUMERO

# 4. Riavvia e scansiona il QR col telefono nuovo
systemctl --user start openclaw-gateway
```

Poi verifica con un messaggio reale da un terzo telefono e con
`python3 doctor.py --short`.

## Comunicazione ai clienti (template)

```text
Ciao, ti scrivo da un nuovo numero: il precedente è temporaneamente non
disponibile. Sono sempre io, [Nome] di [Attività]. Puoi continuare a scrivermi
qui: rispondo come prima. Per urgenze: [email] · [telefono].
```

Invia il template solo a chi ha già un rapporto con l'attività e senza allegati
o link sospetti. Non importare rubriche acquistate.

## Prevenzione

- Usa un numero dedicato all'attività, non il personale.
- Non inviare messaggi a chi non ha scritto per primo; nessun broadcast di massa.
- Rispetta le finestre di debounce/queue già configurate: sei messaggi identici
  diventano una risposta, non sei.
- Evita link abbreviati e allegati non richiesti nei primi contatti.
- Tieni aggiornato il consenso: chi chiede di non essere contattato va escluso.
- Backup giornaliero e copia della sessione fuori dall'host.
- Monitora `healthcheck` e gli alert: un cambiamento di stato è il primo segnale.

## Cosa NON fare

- Non cambiare numero senza aver esportato prima memoria e preventivi.
- Non cancellare il database OpenClaw: contiene i transcript e i backup servono.
- Non automatizzare ricorsi o verifiche con script.
- Non riutilizzare lo stesso numero per test di massa.
