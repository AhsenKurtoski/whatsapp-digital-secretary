# Audit verificato: funzionalità, sicurezza e test

Data: 22 novembre 2026. Metodo: lettura del codice, esecuzione della suite automatica
(`bash scripts/verify.sh`, `python3 -m unittest discover -s tests`, coverage) e degli
script di self-test. Le voci che richiedono account reali o hardware specifico sono
marcate **da provare**, non "fatte": un test simulato non dimostra una consegna reale.

Legenda: **Sì** = implementato e coperto da test · **Parziale** = esiste una parte, manca
un pezzo dichiarato · **No** = assente · **Da provare** = serve la macchina/account reale.

## 1. Funzionalità richieste

| Funzionalità | Stato | Dove | Cosa manca davvero |
|---|---|---|---|
| Prenotazioni avanzate | **Parziale** | `approval_store.validate_calendar`, `dashboard._execute_calendar`, `bridge_client.create_event` | Solo **creazione** con approvazione. Modifica e annullamento di un evento dalla dashboard non esistono |
| Handoff intelligente | **Sì** | `memory.set_handoff`, `conversations.set_handoff`, pulsante **Prendi il controllo**, endpoint `POST /api/conversations/send` | La ripresa automatica del bot dopo chiusura non è configurabile |
| Orari di lavoro | **Parziale** | `live.py:97` (`faq_answer` legge `hours`), campo `hours` in config | Risposta FAQ sugli orari; **nessuna** risposta automatica "siamo chiusi, ti scriviamo domani" fuori orario |
| Follow-up automatici | **Sì** | `reminders.py`, timer `openclaw-secretary-reminders`, `docs/ALWAYS_ON.md` | Richiede il timer installato (di default c'è) |
| Gestione operatori | **Sì** | `identity.py` ruoli `viewer`/`operator`/`admin`, 403 sulle sezioni admin, audit firmato | — |
| Statistiche commerciali | **Parziale** | `stats.py`: conversazioni, lead, preventivi emessi/approvati, `lead_conversion_rate`, valore approvato, handoff | "Richieste perse" non è un KPI: non c'è uno stato lead "perso/abbandonato" |
| Multi-business | **No** | — | Una installazione = una attività: una sola config, una sola memoria. Servirebbe partizionare config, memoria e coda per `business_id` |
| Notifiche avanzate | **Parziale** | `monitor.py` (urgenze/overdue), `digest.py`, `/api/alerts`, `/api/health/notify` | Classificazione "cliente importante" non configurabile; le urgenze sono euristiche |
| Ricerca conversazioni | **Parziale** | `memory.search` (usata dal bot), lista conversazioni in dashboard | La dashboard non ha full-text per nome/numero/data/contenuto: solo filtro e lista |
| Gestione allegati | **Parziale** | PDF preventivi in uscita (`quote_pdf`, bridge attachments) | In ingresso foto/PDF/audio li riceve OpenClaw ma il progetto non li classifica né li archivia |

## 2. Sicurezza e privacy

| Verifica | Stato | Evidenza |
|---|---|---|
| Isolamento tra clienti | **Parziale** | Memoria cifrata per numero (`memory.py`, test `test_contact_file_is_encrypted_at_rest`), transcript redatti, ogni endpoint dashboard richiede ruolo. Non è un multi-tenant: chi ha ruolo `viewer`/`operator` vede tutte le conversazioni dell'attività |
| Nessun cliente ottiene dati altrui | **Da provare** | Non esiste una superficie pubblica per clienti: il rischio è lato dashboard. Serve un test avversariale su token condivisi e URL manipolati |
| Prompt injection (messaggi e allegati) | **Parziale** | `gateway_policy` valida i tool esposti, `smoke_guard` protegge i test, `test_persona` verifica il tono. **Manca** un corpus di attacchi ("ignora le istruzioni…", PDF con testo nascosto) eseguito in CI |
| Webhook falsificati / replay | **Parziale** | Firma `X-Gina-Signature: sha256=<hmac>` sul corpo esatto (`webhooks.py`), secret per endpoint | La firma **non include un timestamp**: un corpo firmato intercettato può essere riprodotto. Manca una finestra anti-replay |
| Scadenza e revoca sessioni | **Sì** | `SESSION_TTL = 12h`, cookie `HttpOnly; SameSite=Strict`, `revoke_sessions()`, rotazione token, TOTP 2FA (`identity.py`) | — |
| Cifratura memoria, backup, credenziali | **Sì** | Memoria cifrata, backup scrypt + AES-GCM (`backup.py`), `secrets.env` 600, nessun segreto nel repo; test dedicati | La chiave locale vive in `~/.openclaw/secretary` con permessi 600: chi ha la shell dell'utente ha i dati |
| Export e cancellazione GDPR | **Sì** | `gdpr.py --export`, `--delete`, `--retention --apply`, inventario; test `test_delete_contact_removes_everything` | La cancellazione non riscrive i backup già creati: vanno ruotati |
| Dati personali nei log | **Sì** | Audit con numero ma **non** testo (`test_audit_stores_the_number_but_not_the_text`), redazione transcript, log senza chiavi | I log di OpenClaw/Node sono fuori dal controllo del progetto: vanno controllati a mano |
| Provider AI e conservazione documentati | **Sì** | `docs/GDPR.md`: responsabili, dati inviati, retention 180 giorni, base giuridica | — |
| Autorizzazioni operatori | **Sì** | `identity.role_allows`, 403, audit di ogni azione, test `test_conversations_and_handoff_permissions`, `test_admin_only_sections` | — |

## 3. WhatsApp e integrazioni

| Voce | Stato | Note |
|---|---|---|
| WhatsApp Cloud API ufficiale | **No, per scelta** | Il progetto usa OpenClaw su WhatsApp Web. `docs/WHATSAPP_BAN.md` descrive il rischio di restrizione del numero. Passare alla Cloud API è un cambio di architettura, non una configurazione |
| Stabilità connessione WhatsApp Web | **Da provare** | Watchdog + `Restart=always` coprono il processo, non la sessione: serve osservazione reale di 24–48 h |
| Riconnessione automatica | **Parziale** | `watchdog.py` riavvia gateway e dashboard; la nuova scansione QR resta manuale |
| Vocali e trascrizione | **No** | Nessuna trascrizione nel progetto; OpenClaw consegna l'audio, il bot non lo interpreta |
| Immagini e documenti ricevuti | **No** | Nessuna classificazione o archiviazione (vedi "Gestione allegati") |
| Messaggi non consegnati | **Parziale** | `send_message` riporta l'errore di OpenClaw e non scrive in memoria; non c'è coda di retry |
| Webhook: retry e duplicati | **Parziale** | Firma HMAC, log consegne, stato per endpoint (`webhooks.deliveries`); nessun retry automatico né deduplica |
| Limiti di invio e rate limit | **Sì** | `quotas.py` (limiti giornalieri per provider), `rate_limit` dashboard (login, azioni, invio), backoff |
| Gmail con invio reale | **Da provare** | Test con bridge simulato verdi; la consegna reale richiede account e bridge autorizzato |
| Calendar: crea/modifica/elimina | **Parziale / da provare** | Solo creazione con approvazione; modifica ed eliminazione non implementate |

## 4. Test tecnici

| Voce | Stato | Comando / nota |
|---|---|---|
| Tutti i test automatici | **Sì** | `python3 -m unittest discover -s tests` |
| Copertura e regressioni | **Sì** | `bash scripts/coverage.sh`, gate 86% in CI |
| Installazione pulita Linux | **Da provare** | `docs/QUICKSTART.md` su VM nuova; qui i test girano in sandbox, non su una installazione da zero |
| Installazione pulita Windows WSL2 | **Da provare** | `docs/WSL.md` + `scripts/wsl_status.py`; serve il PC dell'utente |
| Macchina 2 GB RAM | **Da provare** | `python3 scripts/lowmem_check.py` + `scripts/footprint.py`; i numeri attuali sono misurati su 16 GB |
| 10 conversazioni contemporanee | **Parziale** | `max_concurrent` limita le chiamate AI; nessun test di carico reale con 10 chat |
| 50 messaggi in sequenza | **Parziale** | Test di coda e dedup esistono; manca la prova su gateway reale |
| Riavvio improvviso | **Parziale** | `Restart=always`, stato su file/SQLite; nessun test di kill -9 durante una scrittura |
| Backup e ripristino su altra macchina | **Parziale** | `backup.py create/verify/restore` testato in sandbox; manca la prova su host diverso |
| Dashboard non raggiungibile pubblicamente | **Sì** | Bind su loopback per default, test `test_dashboard_url_is_loopback_only`; l'esposizione richiede `docs/REMOTE_ACCESS.md` e è opt-in |
| E2E con account WhatsApp e Google dedicati | **Da provare** | `docs/E2E.md`; richiede telefoni e account reali |

## 5. Repository e presentazione

| Voce | Stato |
|---|---|
| README coerente con la versione | Corretto: la riga "Latest release" ora indica la versione in `pyproject.toml` (1.4.0) e linka il changelog, non un tag inesistente |
| README italiano sincronizzato | Sì, entrambi linkano le stesse guide |
| Demo video/GIF | **Manca**: richiede la registrazione sullo schermo dell'utente (`docs/WALKTHROUGH.md`); il repo non contiene il file |
| Case study | **Manca** |
| Diagramma architettura | Presente in `docs/` (testo); nessun diagramma immagine aggiornato |
| Screenshot | Presenti `docs/dashboard-preview*.png`; mancano approvazioni e conversazioni |
| Roadmap | Questo documento, sezione 6 |
| CHANGELOG per versione | Sì, con sezione Unreleased |
| Installazione su macchina pulita | **Da provare** (vedi sezione 4) |
| Licenze dipendenze | MIT per il progetto; dipendenze Python minime, Node/OpenClaw esterno. Manca un controllo automatico licenze in CI |
| About GitHub senza Docker | **Azione manuale**: si cambia solo dalle impostazioni del repository (o con l'integrazione GitHub), non dal codice |

## 6. Prossimi passi, in ordine di rischio

1. **Sicurezza webhook**: aggiungere timestamp firmato + finestra anti-replay (rompe la
   compatibilità con gli endpoint esistenti solo se si rifiuta la vecchia firma: farlo
   con una transizione di 30 giorni).
2. **Test di prompt injection** con un corpus di 10–15 attacchi in CI: è l'unica difesa
   verificabile senza account.
3. **Ricerca conversazioni in dashboard**: full-text su numero, nome, data e testo, con
   endpoint già autorizzato; riusa `memory.search`.
4. **Prenotazioni complete**: modifica e annullamento evento con la stessa coda di
   approvazione della creazione.
5. **Multi-business**: il cambio più grande; richiede `business_id` in config, memoria,
   audit e dashboard. Da pianificare, non improvvisare.
6. **Cloud API WhatsApp**: valutazione separata, con costi e verifica di business Meta.
