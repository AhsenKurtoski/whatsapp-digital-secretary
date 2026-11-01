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
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    target = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else datetime.now(ROME).date()
    leads = [x for x in read_jsonl(args.leads) if day_of(x) == target]
    alerts = [x for x in read_jsonl(args.alerts) if day_of(x) == target]
    if not leads:
        print(json.dumps({"date": str(target), "leads": 0, "sent": False}))
        return

    senders = {x.get("sender") for x in leads if x.get("sender")}
    scams = sum(1 for x in alerts if x.get("kind") == "scam")
    hot = sum(1 for x in alerts if x.get("kind") == "hot")
    new = sum(1 for x in alerts if x.get("kind") == "new")

    lines = [
        f"📋 Riepilogo segreteria — {target.strftime('%d/%m/%Y')}",
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
    text = "\n".join(lines)

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
    print(json.dumps({"date": str(target), "leads": len(leads), "senders": len(senders), "scams": scams, "hot": hot, "sent": ok}))


if __name__ == "__main__":
    main()
