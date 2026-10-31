# Bridge Google senza dominio

Questo bridge risolve un problema preciso: l'app OAuth in **Testing** fa scadere il
refresh token Gmail/Calendar dopo 7 giorni, ma pubblicarla in produzione richiede un
dominio verificato. Il bridge usa **Google Apps Script**, che è gratis, non richiede
dominio, non richiede la pubblicazione dell'app OAuth e resta autorizzato finché non
revochi l'accesso.

## Come funziona

```
Dashboard locale
   │  POST JSON firmato HMAC-SHA256
   ▼
Apps Script web app (eseguita con l'account che gestisce l'attività)
   ├── GmailApp.createDraft(...).send()
   └── CalendarApp.getDefaultCalendar().createEvent(...)
```

- La dashboard chiama solo due azioni: `sendEmail` e `createEvent`.
- Il segreto HMAC è sul PC (`~/.openclaw/secretary/google_bridge.json`, permessi 0600)
  e nelle Script Properties/`Config.gs` dell'Apps Script privato.
- La firma copre il corpo esatto; il timestamp è valido per 5 minuti.
- Il web app è raggiungibile solo con l'URL `/exec` e la firma. Non espone un proxy
  generico, token OAuth o la password Google.
- Le azioni restano soggette all'approvazione della dashboard: il bot non ha un tool
  per inviare o creare direttamente.

## Attivazione (una volta)

Dopo aver installato l'aggiornamento della segreteria:

```bash
bash ~/.openclaw/secretary/google-bridge/installa-bridge.sh
```

Lo script:

1. installa il progetto in `~/.openclaw/secretary/google-bridge`;
2. avvia `clasp login` se non hai ancora un accesso: scegli l'account Google dell'attività;
3. crea un progetto Apps Script **privato** chiamato `Digital Secretary Bridge`;
4. genera un segreto casuale in `Config.gs` (non lo stampa);
5. carica `Code.gs` e `appsscript.json`, crea il deployment web e salva l'URL in
   `~/.openclaw/secretary/google_bridge.json`;
6. verifica il bridge.

La prima esecuzione del web app può chiedere di autorizzare Gmail e Calendar.
Apri l'URL `/exec` mostrato dallo script, premi **Consenti**, poi rilancia lo script
oppure premi **Verifica bridge** nella dashboard.

L'installer riavvia da solo la dashboard. Nella dashboard,
**Connettori → Permanente senza dominio** deve mostrare `configurato`;
**Verifica bridge** deve rispondere `ok`. Da quel momento Gmail e Calendar passano
dal bridge, non più dal token OAuth in Testing.

## Se qualcosa non va

- `clasp login` non si apre: esegui `npx --yes @google/clasp login` e apri l'URL a mano.
- Deployment senza URL: `npx --yes @google/clasp deployments` e copia l'URL `/exec`;
  poi `python3 bridge_client.py --config ~/.openclaw/secretary/google_bridge.json setup --url "URL"`.
- `unauthorized`: il segreto locale e quello di Apps Script non coincidono. Rigenera
  `Config.gs`, rifai `clasp push` e un nuovo deployment; il vecchio deployment resta
  attivo finché non lo elimini.
- `server_error`: controlla **Esecuzioni** nel progetto Apps Script; non incollare
  contenuti di email nei log pubblici.
- `expired`: l'orologio del PC è troppo avanti/indietro; correggi l'ora.

## Sicurezza e limiti

- Non committare `Config.gs`, `.clasp.json` o `google_bridge.json`.
- Se l'URL o il segreto vengono esposti, ruota il segreto e crea un nuovo deployment:
  elimina il vecchio deployment dal progetto Apps Script.
- Il web app è configurato `executeAs: USER_DEPLOYING` e `access: ANYONE_ANONYMOUS`.
  La protezione è la firma HMAC, non l'URL.
- Le quote Apps Script/Gmail/Calendar si applicano all'account Google; per un uso
  personale sono ampiamente sufficienti.
- Per revocare tutto: elimina il deployment Apps Script e rimuovi l'accesso da
  `myaccount.google.com/permissions`.
- Apps Script è un servizio Google: i dati passano da Google come per Gmail/Calendar.
  Non è un servizio terzo diverso da Google.

## Test locali

```bash
python3 test_bridge.py       # protocollo client + scrittura config 0600
node test_code_gs.js         # handler Apps Script con servizi Google simulati
```
