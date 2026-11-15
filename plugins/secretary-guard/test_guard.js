import assert from "node:assert/strict";
import {
  GUARD_LIMITS,
  isFailedAgentRun,
  markInboundAnswered,
  releaseInboundForRecipient,
  releaseInboundForScope,
  resetGuardState,
  shouldSuppressDuplicate,
  shouldSuppressDuplicateInbound,
} from "./guard.js";

function inbound(text, sender = "+393331112222", session = "whatsapp:owner") {
  return { content: text, senderId: sender, sessionKey: session, channel: "whatsapp" };
}

// Inbound: the first "1" passes, the next five identical copies are silent.
resetGuardState();
assert.equal(shouldSuppressDuplicateInbound(inbound("1"), 1_000), false);
for (let index = 0; index < 5; index += 1) {
  assert.equal(
    shouldSuppressDuplicateInbound(inbound(" 1 "), 1_000 + index),
    true,
    "the same inbound message must be suppressed",
  );
}

// A different sender, body or conversation is not a duplicate.
assert.equal(shouldSuppressDuplicateInbound(inbound("1", "+393339998888"), 1_100), false);
assert.equal(shouldSuppressDuplicateInbound(inbound("2"), 1_100), false);
assert.equal(shouldSuppressDuplicateInbound(inbound("1", "+393331112222", "whatsapp:other"), 1_100), false);

// A slow gateway queue is still pending, so a copy admitted after 60 seconds
// must not leak to the model. The pending window is not extended by duplicates.
resetGuardState();
const queuedAt = 10_000;
assert.equal(shouldSuppressDuplicateInbound(inbound("slow-queue"), queuedAt), false);
assert.equal(
  shouldSuppressDuplicateInbound(inbound("slow-queue"), queuedAt + 60_000),
  true,
  "a still-pending copy must stay suppressed after a slow queue",
);
assert.equal(
  shouldSuppressDuplicateInbound(
    inbound("slow-queue"),
    queuedAt + GUARD_LIMITS.inboundPendingWindowMs - 1,
  ),
  true,
  "the pending window is measured from the first copy",
);
assert.equal(
  shouldSuppressDuplicateInbound(
    inbound("slow-queue"),
    queuedAt + GUARD_LIMITS.inboundPendingWindowMs,
  ),
  false,
  "an expired pending entry must allow a new request",
);

// Missing identity or empty media-only content is never dropped.
assert.equal(shouldSuppressDuplicateInbound({ content: "1" }, 2_000), false);
assert.equal(shouldSuppressDuplicateInbound({ senderId: "+393331112222", content: "  " }, 2_000), false);

// A failed agent run releases the key, so the user's retry reaches the model.
resetGuardState();
assert.equal(shouldSuppressDuplicateInbound(inbound("retry"), 3_000), false);
assert.equal(shouldSuppressDuplicateInbound(inbound("retry"), 3_100), true);
assert.equal(releaseInboundForScope("whatsapp:owner"), 1);
assert.equal(
  shouldSuppressDuplicateInbound(inbound("retry"), 3_200),
  false,
  "retry must pass after a failed run",
);

// A model failure can arrive as success with an empty assistant message.
assert.equal(isFailedAgentRun({
  success: true,
  messages: [{ role: "assistant", content: "ok" }],
}), false);
assert.equal(isFailedAgentRun({
  success: true,
  messages: [{ role: "assistant", content: [{ type: "text", text: "ok" }] }],
}), false);
assert.equal(isFailedAgentRun({
  success: true,
  messages: [{ role: "assistant", content: "  " }],
}), true);
assert.equal(isFailedAgentRun({ success: false }), true);
assert.equal(isFailedAgentRun({ success: true, error: "provider down" }), true);

// A successful run starts the answered grace period at completion time, not at
// the original receipt: a queued duplicate just before or after the answer is
// still silent for the short answered window.
resetGuardState();
assert.equal(shouldSuppressDuplicateInbound(inbound("answered"), 4_000), false);
assert.equal(shouldSuppressDuplicateInbound(inbound("answered"), 50_000), true);
assert.equal(markInboundAnswered("whatsapp:owner", 51_000), 1);
assert.equal(shouldSuppressDuplicateInbound(inbound("answered"), 51_100), true);
assert.equal(
  shouldSuppressDuplicateInbound(
    inbound("answered"),
    51_000 + GUARD_LIMITS.inboundAnsweredWindowMs - 1,
  ),
  true,
);
assert.equal(
  shouldSuppressDuplicateInbound(
    inbound("answered"),
    51_000 + GUARD_LIMITS.inboundAnsweredWindowMs,
  ),
  false,
  "after the answered grace period the user may repeat the text",
);

// A completion marks only the oldest pending message in a conversation: a
// second distinct message still queued must keep its full pending window.
resetGuardState();
assert.equal(shouldSuppressDuplicateInbound(inbound("first"), 60_000), false);
assert.equal(shouldSuppressDuplicateInbound(inbound("second"), 61_000), false);
assert.equal(markInboundAnswered("whatsapp:owner", 62_000), 1);
assert.equal(shouldSuppressDuplicateInbound(inbound("first"), 63_000), true);
assert.equal(
  shouldSuppressDuplicateInbound(inbound("second"), 63_000),
  true,
  "the second message is still pending, not answered",
);
assert.equal(
  shouldSuppressDuplicateInbound(inbound("first"), 62_000 + GUARD_LIMITS.inboundAnsweredWindowMs),
  false,
  "the answered grace period expires independently",
);
assert.equal(
  shouldSuppressDuplicateInbound(
    inbound("second"),
    62_000 + GUARD_LIMITS.inboundAnsweredWindowMs,
  ),
  true,
  "the queued message keeps the longer pending window",
);

// Fallback release by recipient for channels without a session key.
resetGuardState();
assert.equal(shouldSuppressDuplicateInbound(inbound("recipient"), 5_000), false);
assert.equal(releaseInboundForRecipient("+393331112222"), 1);
assert.equal(shouldSuppressDuplicateInbound(inbound("recipient"), 5_100), false);

// Outbound: the existing guard still cancels repeated identical replies.
resetGuardState();
assert.equal(shouldSuppressDuplicate("+393331112222", "Ciao, come posso aiutarti?", 5_000), false);
assert.equal(shouldSuppressDuplicate("+393331112222", "  ciao,   come posso aiutarti? ", 5_100), true);
assert.equal(shouldSuppressDuplicate("+393339998888", "Ciao, come posso aiutarti?", 5_100), false);
assert.equal(
  shouldSuppressDuplicate("+393331112222", "Ciao, come posso aiutarti?", 5_000 + GUARD_LIMITS.outboundWindowMs),
  false,
  "the outbound window must expire",
);

console.log("secretary guard: OK");
console.log(
  `inbound pending ${GUARD_LIMITS.inboundPendingWindowMs} ms · answered ${GUARD_LIMITS.inboundAnsweredWindowMs} ms · outbound ${GUARD_LIMITS.outboundWindowMs} ms`,
);
