import { createHash } from "node:crypto";

// Outbound: the same text to the same recipient is suppressed for 45 seconds.
const OUTBOUND_DUPLICATE_WINDOW_MS = 45_000;
// Inbound: an admitted message stays "pending" while the gateway runs or
// queues its first copy. Pending duplicates are silent for up to two minutes,
// so a slow queue cannot leak a second model call. Once the first turn has been
// answered, an identical copy is suppressed for a short grace period; after
// that the user can deliberately send the same text again.
const INBOUND_PENDING_WINDOW_MS = 120_000;
const INBOUND_ANSWERED_WINDOW_MS = 15_000;
const MAX_OUTBOUND_TRACKED = 200;
const MAX_INBOUND_TRACKED = 500;

const recentReplies = new Map();
const recentInbound = new Map();

export function normalize(text) {
  return String(text || "").replace(/\s+/g, " ").trim().toLowerCase();
}

export function fingerprint(text) {
  return createHash("sha256").update(String(text)).digest("hex").slice(0, 16);
}

function messageText(message) {
  if (typeof message?.content === "string") return message.content;
  if (Array.isArray(message?.content)) {
    return message.content
      .map((part) => typeof part === "string" ? part : part?.text || "")
      .join(" ");
  }
  return "";
}

/**
 * A model failure can surface as a "successful" webchat turn with an empty
 * assistant message. Treat that as a failed run so the pending key is released
 * and the user's retry is not mistaken for a duplicate.
 */
export function isFailedAgentRun(event) {
  const messages = Array.isArray(event?.messages) ? event.messages : [];
  const last = messages[messages.length - 1];
  const lastRole = String(last?.role || "").toLowerCase();
  return event?.success === false
    || Boolean(event?.error)
    || (lastRole === "assistant" && !messageText(last).trim());
}

function trimOldest(map, max, timestampOf) {
  if (map.size <= max) return;
  const oldest = [...map.entries()].sort((a, b) => timestampOf(a[1]) - timestampOf(b[1]))[0];
  if (oldest) map.delete(oldest[0]);
}

export function shouldSuppressDuplicate(to, text, now = Date.now()) {
  const recipient = String(to || "").trim().toLowerCase();
  const body = normalize(text);
  if (!recipient || !body) return false;
  const previous = recentReplies.get(recipient);
  if (previous && previous.body === body && now - previous.at < OUTBOUND_DUPLICATE_WINDOW_MS) {
    previous.hits += 1;
    return true;
  }
  recentReplies.set(recipient, { body, at: now, hits: 0 });
  trimOldest(recentReplies, MAX_OUTBOUND_TRACKED, (value) => value.at);
  return false;
}

/**
 * Return true for a duplicate inbound message that must not reach the agent.
 *
 * The key includes sender, conversation and a hash of the normalized body.
 * A missing sender is deliberately never suppressed: it is safer to answer
 * than to drop a message from an unknown identity.
 */
export function shouldSuppressDuplicateInbound(event, now = Date.now()) {
  const sender = String(event?.senderId || event?.sender || "").trim().toLowerCase();
  const body = normalize(event?.content ?? event?.body);
  if (!sender || !body) return false;
  const scope = String(event?.sessionKey || event?.threadId || event?.channel || "").trim().toLowerCase();
  const key = `${scope}|${sender}|${fingerprint(body)}`;
  const previous = recentInbound.get(key);
  if (previous) {
    const answeredAt = previous.answeredAt ?? previous.at;
    const window = previous.state === "answered"
      ? INBOUND_ANSWERED_WINDOW_MS
      : INBOUND_PENDING_WINDOW_MS;
    const since = previous.state === "answered" ? answeredAt : previous.at;
    if (now - since < window) return true;
  }
  recentInbound.set(key, { at: now, state: "pending", scope, sender });
  trimOldest(recentInbound, MAX_INBOUND_TRACKED, (value) => value.at);
  return false;
}

/** Release pending entries for a session after a failed agent run. */
export function releaseInboundForScope(scope) {
  const normalized = String(scope || "").trim().toLowerCase();
  if (!normalized) return 0;
  let released = 0;
  for (const [key, entry] of recentInbound) {
    if (entry.scope === normalized) {
      recentInbound.delete(key);
      released += 1;
    }
  }
  return released;
}

/** Fallback release for channels that only expose the recipient on failures. */
export function releaseInboundForRecipient(recipient) {
  const normalized = String(recipient || "").trim().toLowerCase();
  if (!normalized) return 0;
  let released = 0;
  for (const [key, entry] of recentInbound) {
    if (entry.sender === normalized) {
      recentInbound.delete(key);
      released += 1;
    }
  }
  return released;
}

/** Mark the oldest pending turn and start its short answered grace period. */
export function markInboundAnswered(scope, now = Date.now()) {
  const normalized = String(scope || "").trim().toLowerCase();
  if (!normalized) return 0;
  let targetKey = "";
  let target = null;
  for (const [key, entry] of recentInbound) {
    if (entry.scope !== normalized || entry.state === "answered") continue;
    if (!target || entry.at < target.at) {
      targetKey = key;
      target = entry;
    }
  }
  if (!target) return 0;
  target.state = "answered";
  target.answeredAt = now;
  recentInbound.set(targetKey, target);
  return 1;
}

export function resetGuardState() {
  recentReplies.clear();
  recentInbound.clear();
}

export const GUARD_LIMITS = Object.freeze({
  outboundWindowMs: OUTBOUND_DUPLICATE_WINDOW_MS,
  inboundPendingWindowMs: INBOUND_PENDING_WINDOW_MS,
  inboundAnsweredWindowMs: INBOUND_ANSWERED_WINDOW_MS,
  maxOutboundTracked: MAX_OUTBOUND_TRACKED,
  maxInboundTracked: MAX_INBOUND_TRACKED,
});
