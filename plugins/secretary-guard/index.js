import { appendFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import {
  fingerprint,
  isFailedAgentRun,
  markInboundAnswered,
  releaseInboundForRecipient,
  releaseInboundForScope,
  shouldSuppressDuplicate,
  shouldSuppressDuplicateInbound,
} from "./guard.js";

const FALLBACK_TEXT =
  "Ciao! Sto ricevendo molte richieste in questo momento. Ti rispondo appena possibile con tutti i dettagli. 🙏";

// Solo segnali tecnici o di errore: il testo normale del bot non viene toccato.
const TECHNICAL_PATTERNS = [
  /⚠️/u,
  /\bthe ai service\b/i,
  /\bshort break\b/i,
  /\brate[\s-]?limit/i,
  /\b429\b|\b503\b/,
  /RESOURCE_EXHAUSTED/,
  /\bquota\b.*\b(api|model|free|tier)\b|\b(api|model|free|tier)\b.*\bquota\b/i,
  /\bcontext (overflow|length|window)\b/i,
  /\bmodel (backup|fallback)\b|\bmodello (backup|fallback)\b/i,
  /\bfallback (model|provider)\b/i,
  /\bprovider (error|returned|unavailable|failed)\b/i,
  /\bapi[ _-]?key\b|\bchiave api\b/i,
  /\bbilling (error|problem)\b/i,
  /\binternal (server )?error\b/i,
  /\bECONN(RESET|REFUSED)\b/,
  /\bETIMEDOUT\b/,
  /\b(?:groq|gemini|qwen|openai|anthropic|mistral|deepseek)\b/i,
  /\bopenclaw\b/i,
];

// Nel log non finisce mai il testo del cliente: solo un'impronta e la lunghezza,
// abbastanza per capire un loop senza conservare conversazioni private.

function guardLog(line) {
  try {
    // OPENCLAW_STATE_DIR permette di verificare il plugin in un gateway di test
    // isolato senza scrivere nel profilo reale dell'utente.
    const stateDir = process.env.OPENCLAW_STATE_DIR;
    const logDir = stateDir ? stateDir : join(homedir(), ".openclaw", "secretary");
    const logPath = join(logDir, "guard.log");
    appendFileSync(logPath, `${new Date().toISOString()} ${line}\n`, { encoding: "utf8", mode: 0o600 });
  } catch {
    // un log non scrivibile non deve bloccare la risposta al cliente
  }
}

export function scrubMessage(text) {
  if (typeof text !== "string" || !text.trim()) return text;
  if (!TECHNICAL_PATTERNS.some((re) => re.test(text))) return text;
  guardLog(`replaced hash=${fingerprint(text)} len=${text.length}`);
  return FALLBACK_TEXT;
}

export default definePluginEntry({
  id: "secretary-guard",
  name: "Secretary Guard",
  description:
    "Nasconde errori tecnici, ferma le risposte duplicate e scarta le raffiche di messaggi identici prima del modello.",
  register(api) {
    // Inbound claim: OpenClaw chiama questo hook prima del dispatch al modello.
    // Il primo messaggio passa; le copie identiche immediate vengono chiuse in
    // silenzio con { handled: true }, quindi non consumano token e non generano
    // una seconda risposta. Se la versione di OpenClaw non espone l'hook, il
    // plugin continua a funzionare con la sola guardia outbound.
    try {
      api.on("before_dispatch", (event, ctx) => {
        // Alcuni canali mettono l'identità nel contesto invece che nell'evento.
        const inbound = {
          ...event,
          senderId: event?.senderId || ctx?.senderId,
          sessionKey: event?.sessionKey || ctx?.sessionKey,
        };
        if (!shouldSuppressDuplicateInbound(inbound)) return;
        const body = String(inbound.content ?? inbound.body ?? "");
        guardLog(`duplicate_inbound_suppressed hash=${fingerprint(body)} len=${body.length}`);
        return { handled: true };
      });
    } catch {
      guardLog("before_dispatch_unavailable");
    }

    // Se il turno fallisce, il prossimo messaggio identico deve poter partire
    // di nuovo: senza questo rilascio la prima copia verrebbe soppressa come
    // se il bot avesse già risposto.
    try {
      api.on("agent_end", (event, ctx) => {
        const scope = ctx?.sessionKey || event?.sessionKey;
        const failed = isFailedAgentRun(event);
        if (!scope) return;
        if (failed) {
          const released = releaseInboundForScope(scope);
          if (released) guardLog(`inbound_released_after_agent_failure scope=${fingerprint(scope)} count=${released}`);
        } else if (event?.success === true) {
          markInboundAnswered(scope);
        }
      });
    } catch {
      guardLog("agent_end_unavailable");
    }

    api.on("message_sending", (event) => {
      const content = event?.content;
      const cleaned = scrubMessage(content);
      if (cleaned !== content) {
        // Un errore tecnico è stato sostituito con il messaggio di cortesia:
        // il turno non ha risposto davvero, quindi un retry deve passare.
        releaseInboundForScope(event?.sessionKey);
        releaseInboundForRecipient(event?.to);
        return { content: cleaned };
      }
      if (shouldSuppressDuplicate(event?.to, cleaned)) {
        guardLog(`duplicate_suppressed hash=${fingerprint(cleaned)} len=${String(cleaned).length}`);
        return { cancel: true, cancelReason: "duplicate_reply" };
      }
    });

    // Osservazione finale della consegna: copre anche i canali che falliscono
    // dopo aver prodotto una risposta ma prima di recapitarla.
    try {
      api.on("message_sent", (event, ctx) => {
        const scope = ctx?.sessionKey || event?.sessionKey;
        if (event?.success === false) {
          releaseInboundForScope(scope);
          releaseInboundForRecipient(event?.to);
        }
        // agent_end is the authoritative success signal: marking here too would
        // advance a second pending message that is still waiting in the queue.
      });
    } catch {
      guardLog("message_sent_unavailable");
    }
  },
});
