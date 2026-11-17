// Plugin "hello world" per Gina.
//
// Mostra i tre hook usati dal progetto:
//   before_dispatch  -> può fermare un messaggio prima del modello;
//   message_sending  -> può modificare o annullare una risposta in uscita;
//   agent_end        -> osserva la fine del turno (nessuna modifica).
//
// Non invia nulla, non legge file e non chiama servizi esterni: è il punto di
// partenza sicuro per un plugin personalizzato.
export default function definePluginEntry(api) {
  const label = String(api?.config?.label || "hello-gina");
  let inbound = 0;
  let outbound = 0;

  api.log?.(`[${label}] plugin caricato`);

  api.on("before_dispatch", (event) => {
    inbound += 1;
    api.log?.(`[${label}] messaggio in ingresso #${inbound}`);
    // `return { action: "drop" }` fermerebbe il messaggio: qui non lo facciamo.
  });

  api.on("message_sending", (event) => {
    outbound += 1;
    api.log?.(`[${label}] risposta in uscita #${outbound}`);
    // Per modificare il testo: `return { text: ... }`.
  });

  api.on("agent_end", () => {
    api.log?.(`[${label}] turno concluso (in ${inbound}, out ${outbound})`);
  });
}
