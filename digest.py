#!/usr/bin/env python3
"""Riepilogo serale della segreteria digitale: contatti, messaggi e alert."""
import argparse
import json
import os
import subprocess
from datetime import datetime

import config

HOME = os.path.expanduser("~")
CFG = config.load()
BASE = str(config.BASE)
NODE = config.node_bin()
CLI = config.openclaw_cli()
OWNER = str(CFG.get("owner_number") or "")
ROME = config.resolve_timezone()


def read_jsonl(path):
    items = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    items.append(json.loads(line))
                except Exception:
                    continue
    except FileNotFoundError:
        pass
    return items


def day_of(item):
    raw = item.get("ts") or ""
    try:
        return datetime.fromisoformat(raw).astimezone(ROME).date()
    except Exception:
        return None


def health_section(path):
    """Righe del riepilogo Health, lette dall'ultima scansione di healthcheck.py.

    Ritorna ``(lines, problems)``: ``problems`` è True se ci sono controlli
    critici o in avviso, così il digest viene inviato anche in una giornata
    senza nuovi contatti.
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return [], False
    if not isinstance(data, dict) or not data:
        return [], False
    checks = data.get("checks") or []
    counts = data.get("counts") or {}
    issues = [c for c in checks if str(c.get("status")) in ("critical", "warn")]
    overall = str(data.get("overall") or "unknown")
    icon = "🔴" if overall == "critical" else ("🟡" if overall == "warn" else "✅")
    lines = [
        "",
        f"🩺 Health: {icon} {counts.get('critical', 0)} critici · {counts.get('warn', 0)} avvisi · {counts.get('ok', 0)} ok",
    ]
    if issues:
        issues.sort(key=lambda c: 0 if c.get("status") == "critical" else 1)
        for item in issues[:3]:
            mark = "🔴" if item.get("status") == "critical" else "🟡"
            detail = " ".join(str(item.get("detail") or "").split())
            if len(detail) > 150:
                detail = detail[:149].rstrip() + "…"
            lines.append(f"{mark} {item.get('label') or item.get('id')}: {detail}")
        lines.append("→ Dashboard → Health per il report completo.")
    else:
        lines.append("Nessun problema rilevato.")
    expiring = data.get("expiring") or []
    if expiring:
        lines.append("")
        lines.append("⏳ Scadenze entro 7 giorni")
        for token in expiring[:4]:
            days = token.get("days_left")
            if token.get("expired"):
                when = "già scaduto"
            elif isinstance(days, (int, float)) and days < 1:
                when = "tra poche ore"
            elif isinstance(days, (int, float)):
                when = f"tra {int(days)} giorni"
            else:
                when = "in scadenza"
            lines.append(f"• {token.get('label') or token.get('id')}: {when} ({token.get('expires') or '—'})")
    return lines, bool(issues) or bool(expiring)


def send_whatsapp(text, dry=False):
    if dry:
        print("DRY SEND:\n" + text)
        return True
    cmd = [NODE, CLI, "message", "send", "--channel", "whatsapp", "--target", OWNER,
           "--message", text, "--json"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    return r.returncode == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="")
    ap.add_argument("--leads", default=os.path.join(BASE, "leads.jsonl"))
    ap.add_argument("--alerts", default=os.path.join(BASE, "alerts.jsonl"))
    ap.add_argument("--state", default=os.path.join(BASE, "digest_state.json"))
    ap.add_argument("--health", default=os.path.join(BASE, "health_state.json"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    target = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else datetime.now(ROME).date()
    leads = [x for x in read_jsonl(args.leads) if day_of(x) == target]
    alerts = [x for x in read_jsonl(args.alerts) if day_of(x) == target]
    health_lines, health_problems = health_section(args.health)
    if not leads and not health_problems:
        print(json.dumps({"date": str(target), "leads": 0, "sent": False}))
        return

    senders = {x.get("sender") for x in leads if x.get("sender")}
    scams = sum(1 for x in alerts if x.get("kind") == "scam")
    hot = sum(1 for x in alerts if x.get("kind") == "hot")
    new = sum(1 for x in alerts if x.get("kind") == "new")

    lines = [f"📋 Riepilogo segreteria — {target.strftime('%d/%m/%Y')}"]
    if leads:
        lines += [
            f"Nuovi contatti: {len(senders)}",
            f"Messaggi ricevuti: {len(leads)}",
            f"Alert truffe: {scams}",
            f"Richieste di call: {hot}",
        ]
        if new:
            lines.append(f"Nuovi contatti segnalati: {new}")
        top = sorted(leads, key=lambda x: int(x.get("score") or 0), reverse=True)[:3]
        if top and int(top[0].get("score") or 0) > 0:
            lines.append("")
            lines.append("Lead più caldi:")
            for item in top:
                snippet = " ".join(str(item.get("text", "")).split())[:90]
                lines.append(f"• {item.get('score', 0)}/100 {item.get('intent', 'info')} · {item.get('sender', 'sconosciuto')}: \"{snippet}\"")
        lines.append("")
        lines.append("Ultimi contatti:")
        for item in leads[-5:]:
            snippet = " ".join(str(item.get("text", "")).split())[:110]
            lines.append(f"• {item.get('sender', 'sconosciuto')}: \"{snippet}\"")
    else:
        lines.append("Nessun nuovo contatto oggi.")
    lines += health_lines
    text = "\n".join(lines)[:1800]

    state = {}
    try:
        with open(args.state, encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        pass
    already = state.get("last_sent") == str(target)
    if already and not args.force:
        print(json.dumps({"date": str(target), "leads": len(leads), "sent": False, "reason": "already_sent"}))
        return

    ok = send_whatsapp(text, dry=args.dry_run)
    if ok and not args.dry_run:
        os.makedirs(os.path.dirname(args.state), exist_ok=True)
        with open(args.state, "w", encoding="utf-8") as f:
            json.dump({"last_sent": str(target)}, f)
    print(json.dumps({"date": str(target), "leads": len(leads), "senders": len(senders), "scams": scams, "hot": hot, "health": health_problems, "sent": ok}))


if __name__ == "__main__":
    main()
