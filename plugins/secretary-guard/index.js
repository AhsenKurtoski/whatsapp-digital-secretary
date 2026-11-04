import { appendFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

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

// Anti-raffica: se lo stesso testo esce verso lo stesso destinatario due volte
// entro questa finestra, la seconda copia viene annullata. Le risposte vere
// hanno quasi sempre testo diverso, mentre un loop di duplicati viene fermato.
const DUPLICATE_WINDOW_MS = 45_000;
const MAX_TRACKED = 200;
const recentReplies = new Map();

function normalize(text) {
  return String(text || "").replace(/\s+/g, " ").trim().toLowerCase();
}

export function shouldSuppressDuplicate(to, text, now = Date.now()) {
  const recipient = String(to || "").trim().toLowerCase();
  const body = normalize(text);
  if (!recipient || !body) return false;
  const previous = recentReplies.get(recipient);
  if (previous && previous.body === body && now - previous.at < DUPLICATE_WINDOW_MS) {
    previous.hits += 1;
    return true;
  }
  recentReplies.set(recipient, { body, at: now, hits: 0 });
  if (recentReplies.size > MAX_TRACKED) {
    const oldest = [...recentReplies.entries()].sort((a, b) => a[1].at - b[1].at)[0];
    if (oldest) recentReplies.delete(oldest[0]);
  }
  return false;
}

function guardLog(line) {
  try {
    const logPath = join(homedir(), ".openclaw", "secretary", "guard.log");
    appendFileSync(logPath, `${new Date().toISOString()} ${line}\n`, { encoding: "utf8", mode: 0o600 });
  } catch {
    // un log non scrivibile non deve bloccare la risposta al cliente
  }
}

export function scrubMessage(text) {
  if (typeof text !== "string" || !text.trim()) return text;
  if (!TECHNICAL_PATTERNS.some((re) => re.test(text))) return text;
  guardLog(`replaced=${JSON.stringify(text.slice(0, 240))}`);
  return FALLBACK_TEXT;
}

export default definePluginEntry({
  id: "secretary-guard",
  name: "Secretary Guard",
  description:
    "Nasconde errori tecnici e nomi dei provider nei messaggi in uscita e ferma le risposte duplicate.",
  register(api) {
    api.on("message_sending", (event) => {
      const content = event?.content;
      const cleaned = scrubMessage(content);
      if (cleaned !== content) return { content: cleaned };
      if (shouldSuppressDuplicate(event?.to, cleaned)) {
        guardLog(`duplicate_suppressed to=${JSON.stringify(String(event?.to || "").slice(0, 32))} text=${JSON.stringify(String(cleaned).slice(0, 120))}`);
        return { cancel: true, cancelReason: "duplicate_reply" };
      }
    });
  },
});
