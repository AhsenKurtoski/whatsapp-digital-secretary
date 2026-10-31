/**
 * Digital Secretary — Google Workspace bridge
 *
 * Web app Apps Script che esegue SOLO due azioni, dopo la firma HMAC:
 *  - sendEmail
 *  - createEvent
 *
 * Non è un proxy generico e non espone token o password.
 * Il segreto viene letto da Script Properties oppure da Config.gs.
 */

const BRIDGE_VERSION = '1.0.0';
const MAX_BODY_BYTES = 200000;
const MAX_SKEW_SECONDS = 300;
const OWNER_EMAIL = (typeof BRIDGE_OWNER_EMAIL !== 'undefined') ? String(BRIDGE_OWNER_EMAIL) : '';
const BRIDGE_NAME = (typeof BRIDGE_BRAND !== 'undefined' && BRIDGE_BRAND) ? String(BRIDGE_BRAND) : 'Segreteria Digitale';

function bridgeSecret() {
  const fromProps = PropertiesService.getScriptProperties().getProperty('BRIDGE_SECRET');
  if (fromProps) return String(fromProps);
  return (typeof BRIDGE_SECRET !== 'undefined' && BRIDGE_SECRET) ? String(BRIDGE_SECRET) : '';
}

function jsonResponse(value) {
  return ContentService
    .createTextOutput(JSON.stringify(value))
    .setMimeType(ContentService.MimeType.JSON);
}

function doGet(e) {
  return jsonResponse({
    ok: true,
    service: 'digital-secretary-bridge',
    version: BRIDGE_VERSION,
    configured: bridgeSecret().length >= 32
  });
}

function doPost(e) {
  try {
    const raw = (e && e.postData && e.postData.contents) ? String(e.postData.contents) : '';

    if (!raw || raw.length > MAX_BODY_BYTES) {
      return jsonResponse({ ok: false, error: 'invalid_body' });
    }

    // The outer envelope preserves the exact signed string across redirects.
    let envelope;
    try {
      envelope = JSON.parse(raw);
    } catch (err) {
      return jsonResponse({ ok: false, error: 'invalid_envelope' });
    }
    const signed = (envelope && typeof envelope.data === 'string') ? envelope.data : '';
    const signature = (envelope && typeof envelope.sig === 'string') ? envelope.sig : '';
    if (!signed || !verifySignature(signed, signature)) {
      return jsonResponse({ ok: false, error: 'unauthorized' });
    }

    let data;
    try {
      data = JSON.parse(signed);
    } catch (err) {
      return jsonResponse({ ok: false, error: 'invalid_json' });
    }

    const ts = Number(data.ts || 0);
    if (!isFinite(ts) || Math.abs((Date.now() / 1000) - ts) > MAX_SKEW_SECONDS) {
      return jsonResponse({ ok: false, error: 'expired' });
    }

    const action = String(data.action || '');
    const payload = data.payload && typeof data.payload === 'object' ? data.payload : {};

    if (action === 'health') {
      return jsonResponse({ ok: true, service: 'digital-secretary-bridge', version: BRIDGE_VERSION });
    }
    if (action === 'sendEmail') {
      return jsonResponse({ ok: true, result: sendEmail(payload) });
    }
    if (action === 'createEvent') {
      return jsonResponse({ ok: true, result: createEvent(payload) });
    }
    return jsonResponse({ ok: false, error: 'unknown_action' });
  } catch (err) {
    return jsonResponse({ ok: false, error: 'server_error', detail: String(err).slice(0, 240) });
  }
}

function verifySignature(raw, signature) {
  const secret = bridgeSecret();
  if (!secret || secret.length < 32 || !signature) return false;
  const expected = bytesToHex(Utilities.computeHmacSha256Signature(raw, secret));
  if (expected.length !== signature.length) return false;
  let diff = 0;
  for (let i = 0; i < expected.length; i++) {
    diff |= expected.charCodeAt(i) ^ signature.charCodeAt(i);
  }
  return diff === 0;
}

function bytesToHex(bytes) {
  let out = '';
  for (let i = 0; i < bytes.length; i++) {
    const value = (bytes[i] + 256) % 256;
    out += (value < 16 ? '0' : '') + value.toString(16);
  }
  return out;
}

function cleanText(value, maxLength) {
  return String(value || '').replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, '').trim().slice(0, maxLength);
}

function parseRecipients(value) {
  const seen = {};
  const out = [];
  const parts = String(value || '').split(/[;,]/);
  for (let i = 0; i < parts.length; i++) {
    const email = parts[i].trim().toLowerCase();
    if (!email || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email) || seen[email]) continue;
    seen[email] = true;
    out.push(email);
    if (out.length >= 10) break;
  }
  return out;
}

function sendEmail(payload) {
  const recipients = parseRecipients(payload.to);
  if (!recipients.length) throw new Error('destinatario non valido');

  const subject = cleanText(payload.subject, 250) || ('Messaggio da ' + BRIDGE_NAME);
  const body = cleanText(payload.body, 100000);
  const html = payload.html ? String(payload.html).slice(0, 150000) : '';
  const options = { name: BRIDGE_NAME };
  if (OWNER_EMAIL) options.replyTo = OWNER_EMAIL;
  if (html) options.htmlBody = html;

  const draft = GmailApp.createDraft(recipients.join(','), subject, body, options);
  const message = draft.send();
  return {
    id: message.getId(),
    threadId: message.getThread().getId(),
    to: recipients.join(','),
    subject: subject
  };
}

function createEvent(payload) {
  const summary = cleanText(payload.summary, 250);
  if (!summary) throw new Error('titolo mancante');

  const start = new Date(String(payload.start || ''));
  const end = new Date(String(payload.end || ''));
  if (isNaN(start.getTime()) || isNaN(end.getTime()) || end.getTime() <= start.getTime()) {
    throw new Error('orario non valido');
  }

  const guests = parseRecipients((payload.attendees || []).join(','));
  const options = {
    description: cleanText(payload.description, 20000),
    location: cleanText(payload.location, 500),
    sendInvites: false
  };
  if (guests.length) options.guests = guests.join(',');

  const calendar = CalendarApp.getDefaultCalendar();
  const event = calendar.createEvent(summary, start, end, options);
  return {
    id: event.getId(),
    htmlLink: event.getHtmlLink(),
    summary: summary,
    start: start.toISOString(),
    end: end.toISOString(),
    attendees: guests
  };
}
