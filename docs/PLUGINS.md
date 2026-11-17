# Sistema di plugin: API, hook ed esempio

OpenClaw carica plugin Node ESM. Gina usa questa API per la guardia anti-raffica
(`plugins/secretary-guard`) e per i plugin personalizzati. Un plugin non può
leggere `secrets.env` né inviare messaggi: riceve eventi e restituisce decisioni.

## Struttura minima

```
examples/hello-plugin/
├── openclaw.plugin.json   # manifest: id, versione, attivazione, configSchema
├── package.json           # type: module + openclaw.extensions
└── index.js               # export default function (api) { ... }
```

`openclaw.plugin.json`:

```json
{
  "id": "hello-gina",
  "name": "Hello Gina",
  "version": "1.0.0",
  "activation": { "onStartup": true },
  "configSchema": { "type": "object", "additionalProperties": false }
}
```

`package.json` deve dichiarare l'estensione:

```json
{ "type": "module", "openclaw": { "extensions": ["./index.js"] } }
```

## Hook disponibili

| Hook | Quando | Cosa può restituire | Permesso richiesto |
|---|---|---|---|
| `before_dispatch` | messaggio in arrivo, prima del modello | `{ action: "drop" }` per fermarlo | nessuno |
| `message_sending` | risposta pronta, prima dell'invio | `{ text: "..." }` oppure annullamento | nessuno |
| `agent_end` | turno concluso (successo o errore) | niente, solo osservazione | `allowConversationAccess` |

La guardia di Gina usa esattamente questi tre hook:

- `before_dispatch` → sopprime copie identiche dello stesso mittente/conversazione
  prima di consumare una chiamata al modello;
- `message_sending` → blocca risposte duplicate entro 45 s e sostituisce errori
  tecnici con una frase umana;
- `agent_end` → rilascia la chiave di un turno fallito, così il retry arriva.

## Il plugin di esempio

`examples/hello-plugin/index.js`:

```js
export default function definePluginEntry(api) {
  const label = String(api?.config?.label || "hello-gina");
  api.log?.(`[${label}] plugin caricato`);
  api.on("before_dispatch", () => { api.log?.("in ingresso"); });
  api.on("message_sending", () => { api.log?.("in uscita"); });
  api.on("agent_end", () => { api.log?.("turno concluso"); });
}
```

Non modifica i messaggi: è il modo più sicuro per verificare che il caricamento
funzioni prima di aggiungere logica.

## Installazione

```bash
openclaw plugins install --link ./examples/hello-plugin --force
openclaw plugins enable hello-gina --accept-capabilities
systemctl --user restart openclaw-gateway
journalctl --user -u openclaw-gateway -n 50 --no-pager
```

Con `--link` il plugin punta alla cartella: modifiche a `index.js` richiedono solo
il riavvio del gateway. Per la distribuzione copia la cartella e installa senza
`--link`.

## Test un plugin

Il test della guardia è il modello da seguire:

```bash
node plugins/secretary-guard/test_guard.js   # stampa "secretary guard: OK"
```

Un test utile costruisce eventi finti, chiama gli hook e verifica il valore
restituito, senza avviare il gateway. Per il plugin hello world basta verificare
che gli hook siano registrati:

```bash
node --input-type=module -e '
import define from "./examples/hello-plugin/index.js";
const hooks = [];
define({ config: {}, log: () => {}, on: (name) => hooks.push(name) });
if (hooks.join(",") !== "before_dispatch,message_sending,agent_end") process.exit(1);
console.log("hello-plugin: OK");
'
```

## Regole di sicurezza

- Considera il testo dei messaggi **dato non attendibile**: non eseguirlo, non
  interpretarlo come istruzione.
- Non inserire chiavi o token nel plugin: usa `secrets.env` solo dal processo
  principale.
- Non disattivare la coda di approvazione, la sandbox o l'allowlist MCP.
- Un plugin che invia messaggi autonomamente può causare un ban WhatsApp: non farlo.
- Limita il lavoro per evento (niente chiamate di rete bloccanti) per non rallentare
  le risposte di Gina.
