# GDPR: dati, basi giuridiche, retention e diritti

Gina è self-hosted: i dati restano sul computer del titolare, che ne è il
**titolare del trattamento**. Questo documento descrive cosa viene conservato,
dove, per quanto tempo e come si esercitano i diritti.

## Ruoli

| Soggetto | Ruolo GDPR | Note |
|---|---|---|
| Il titolare dell'attività | Titolare del trattamento | decide finalità e mezzi |
| Chi installa/gestisce il server | Responsabile se esterno | serve un DPA (template sotto) |
| Groq / Google (Gemini, Gmail, Calendar) | Responsabili/sub-responsabili | ricevono solo i dati necessari alla richiesta |
| Il cliente finale | Interessato | può chiedere accesso e cancellazione |

## Basi giuridiche

| Trattamento | Base giuridica | Riferimento |
|---|---|---|
| Rispondere a un messaggio WhatsApp | misure precontrattuali su richiesta dell'interessato | art. 6(1)(b) |
| Preparare un preventivo | misure precontrattuali | art. 6(1)(b) |
| Inviare email/eventi dopo approvazione | esecuzione del contratto o consenso implicito alla richiesta | art. 6(1)(b) |
| Conservare lo storico per assistenza | interesse legittimo del titolare | art. 6(1)(f) |
| Difesa da frodi e abusi | interesse legittimo | art. 6(1)(f) |
| Invio di comunicazioni commerciali | consenso esplicito, separato | art. 6(1)(a) |

Non c'è profilazione pubblicitaria e non c'è trasferimento a terzi per marketing.

## Dove finiscono i dati

| Dato | Dove | Chi lo vede |
|---|---|---|
| Messaggi WhatsApp e transcript | SQLite OpenClaw (`~/.openclaw/agents/...`) sul tuo computer | titolare e operatori autorizzati |
| Memoria per cliente (lingua, fatti, intenti) | `~/.openclaw/secretary/memory/` | dashboard locale |
| Lead, audit, alert | `~/.openclaw/secretary/*.jsonl` | dashboard locale |
| Preventivi e PDF | `~/.openclaw/secretary/quotes/` | dashboard locale, cliente via email |
| Email e calendario | account Google del titolare | titolare |
| Testo dei messaggi per la risposta | provider AI configurato (Groq/Gemini), via API | il provider, secondo i suoi termini |

Il provider AI riceve il testo necessario a generare la risposta. Non viene usato
da questo progetto per addestrare modelli, ma **leggi i termini del provider**:
per dati molto sensibili disattiva il provider o usa un modello locale.

## Retention

- Predefinita: **180 giorni** (`data_retention_days` in `config.json`).
- I backup cifrati ruotano a 7 copie (configurabile).
- I log tecnici ruotano a 512 KB / 2000 righe.
- La memoria per cliente viene eliminata dal job di retention o su richiesta.

```bash
python3 gdpr.py --retention 180            # anteprima, nessuna modifica
python3 gdpr.py --retention 180 --apply    # applica
```

Aggiungi il job al timer notturno (es. `systemd` o cron):

```cron
30 3 * * * cd ~/whatsapp-digital-secretary && python3 gdpr.py --retention 180 --apply
```

## Diritti dell'interessato

```bash
# Accesso / portabilità: esporta tutto in JSON
python3 gdpr.py --export +393331112222 --out mario.json

# Cancellazione (diritto all'oblio): memoria, lead, audit, richieste e PDF
python3 gdpr.py --delete +393331112222 --yes

# Inventario dei dati conservati
python3 gdpr.py --report
```

Dalla dashboard (admin) le stesse azioni sono in `POST /api/gdpr/export` e
`POST /api/gdpr/delete` con `confirm: "DELETE"`; ogni operazione è registrata
nell'audit per utente.

**Cosa non viene cancellato automaticamente**: i messaggi nel database OpenClaw
e la sessione WhatsApp. Per una cancellazione completa elimina anche la sessione
del contatto da OpenClaw o usa la rotazione del numero (vedi
[`WHATSAPP_BAN.md`](WHATSAPP_BAN.md)); il comando `gdpr.py` ti dice quali file
restano e dove sono.

## Misure di sicurezza

- Dashboard solo su `127.0.0.1`, token locale, cookie HttpOnly, CSRF.
- Token utente salvati come SHA-256, ruoli admin/operatore/lettura.
- Backup cifrati AES-256-GCM (scrypt), chiave separata in `backup.key` (0600).
- Audit per utente di approvazioni, handoff, utenti, webhook e cancellazioni.
- Webhook firmati HMAC-SHA256, solo HTTPS.
- Nessun dato cliente nei log applicativi: i transcript sono redatti.

## DPA (template)

```text
ACCORDO SUL TRATTAMENTO DEI DATI PERSONALI (art. 28 GDPR)

Titolare: ______________________ (l'Attività)
Responsabile: __________________ (fornitore/consulente)

1. Oggetto: installazione, manutenzione e assistenza della segreteria digitale
   self-hosted descritta nel repository.
2. Durata: fino alla revoca dell'incarico.
3. Natura e finalità: trattamento dei dati dei clienti per risposte WhatsApp,
   preventivi, email e appuntamenti.
4. Categorie di interessati: clienti e potenziali clienti.
5. Dati trattati: nome, numero di telefono, email, contenuto dei messaggi,
   richieste commerciali.
6. Obblighi del responsabile:
   a. trattare i dati solo su istruzione documentata del titolare;
   b. garantire riservatezza delle persone autorizzate;
   c. adottare misure tecniche e organizzative adeguate (art. 32);
   d. notificare senza ritardo violazioni dei dati personali;
   e. assistere il titolare nell'esercizio dei diritti degli interessati;
   f. cancellare o restituire i dati alla fine del servizio;
   g. non trasferire i dati a terzi senza autorizzazione scritta.
7. Sub-responsabili: provider AI e Google Workspace, elencati nell'allegato.
8. Trasferimenti extra-UE: indicare garanzie (SCC) se applicabili.
9. Audit: il titolare può chiedere evidenze delle misure adottate.

Firma titolare: ______________  Firma responsabile: ______________
Data: ______________
```

## Cosa fare in caso di violazione

1. Contieni l'accesso (revoca token, scollega WhatsApp, ripristina un backup pulito).
2. Valuta rischio e categorie di dati coinvolti.
3. Notifica al Garante entro 72 ore se il rischio è rilevante.
4. Informa gli interessati se il rischio è elevato.
5. Registra l'incidente e le misure adottate.
