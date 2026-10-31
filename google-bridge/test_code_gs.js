#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const vm = require("vm");

const SECRET = "test-" + "x".repeat(48);
const sent = [];
const events = [];

const context = {
  console,
  Date,
  JSON,
  String,
  Number,
  RegExp,
  Error,
  isFinite,
  PropertiesService: {
    getScriptProperties: () => ({ getProperty: () => SECRET }),
  },
  Utilities: {
    computeHmacSha256Signature: (raw, secret) =>
      Array.from(crypto.createHmac("sha256", secret).update(raw, "utf8").digest()),
  },
  ContentService: {
    MimeType: { JSON: "application/json" },
    createTextOutput: (text) => ({
      _text: text,
      setMimeType() { return this; },
      getContent() { return this._text; },
    }),
  },
  GmailApp: {
    createDraft: (to, subject, body, options) => {
      sent.push({ to, subject, body, options });
      return {
        send: () => ({
          getId: () => "msg-test-1",
          getThread: () => ({ getId: () => "thread-test-1" }),
        }),
      };
    },
  },
  CalendarApp: {
    getDefaultCalendar: () => ({
      createEvent: (summary, start, end, options) => {
        events.push({ summary, start, end, options });
        return {
          getId: () => "evt-test-1",
          getHtmlLink: () => "https://calendar.google.com/event?eid=test",
        };
      },
    }),
  },
};

const code = fs.readFileSync(path.join(__dirname, "Code.gs"), "utf8");
vm.createContext(context);
vm.runInContext(code + "\nthis.__bridge = { doGet, doPost };", context);
const bridge = context.__bridge;

function envelope(payload, ts = Math.floor(Date.now() / 1000), secret = SECRET) {
  const signed = JSON.stringify(payload);
  const sig = crypto.createHmac("sha256", secret).update(signed, "utf8").digest("hex");
  return JSON.stringify({ data: signed, sig });
}

function post(body) {
  const response = bridge.doPost({ postData: { contents: body } });
  return JSON.parse(response.getContent());
}

const email = post(envelope({
  ts: Math.floor(Date.now() / 1000),
  action: "sendEmail",
  payload: { to: "cliente@example.com", subject: "Ciao", body: "Test", html: "<p>Test</p>" },
}));
if (!email.ok || email.result.id !== "msg-test-1") throw new Error("sendEmail failed");
if (sent[0].to !== "cliente@example.com" || sent[0].options.htmlBody !== "<p>Test</p>") throw new Error("email payload wrong");

const event = post(envelope({
  ts: Math.floor(Date.now() / 1000),
  action: "createEvent",
  payload: {
    summary: "Call",
    start: "2026-10-28T10:00:00+01:00",
    end: "2026-10-28T10:30:00+01:00",
    attendees: ["cliente@example.com"],
  },
}));
if (!event.ok || event.result.id !== "evt-test-1") throw new Error("createEvent failed");
if (events[0].options.guests !== "cliente@example.com") throw new Error("guest missing");

const bad = post(envelope({
  ts: Math.floor(Date.now() / 1000),
  action: "sendEmail",
  payload: { to: "cliente@example.com", subject: "x", body: "x" },
}, Math.floor(Date.now() / 1000), "wrong-" + "y".repeat(48)));
if (bad.ok || bad.error !== "unauthorized") throw new Error("bad signature accepted");

const expired = post(envelope({
  ts: Math.floor(Date.now() / 1000) - 999,
  action: "sendEmail",
  payload: { to: "cliente@example.com", subject: "x", body: "x" },
}));
if (expired.ok || expired.error !== "expired") throw new Error("expired request accepted");

const health = JSON.parse(bridge.doGet({}).getContent());
if (!health.ok || health.service !== "digital-secretary-bridge") throw new Error("health failed");

console.log("Code.gs handlers: OK");
console.log("sent:", sent.length, "events:", events.length);
