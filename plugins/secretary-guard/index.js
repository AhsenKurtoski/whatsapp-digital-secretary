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

export function scrubMessage(text) {
  if (typeof text !== "string" || !text.trim()) return text;
  if (!TECHNICAL_PATTERNS.some((re) => re.test(text))) return text;
  try {
    const logPath = join(homedir(), ".openclaw", "secretary", "guard.log");
    const line = `${new Date().toISOString()} replaced=${JSON.stringify(text.slice(0, 240))}\n`;
    appendFileSync(logPath, line, { encoding: "utf8", mode: 0o600 });
  } catch {
    // un log non scrivibile non deve bloccare la risposta al cliente
  }
  return FALLBACK_TEXT;
}

export default definePluginEntry({
  id: "secretary-guard",
  name: "Secretary Guard",
  description: "Nasconde errori tecnici e nomi dei provider nei messaggi in uscita.",
  register(api) {
    api.on("message_sending", (event) => {
      const content = event?.content;
      const cleaned = scrubMessage(content);
      if (cleaned !== content) return { content: cleaned };
    });
  },
});
