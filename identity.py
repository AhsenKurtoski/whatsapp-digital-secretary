#!/usr/bin/env python3
"""Utenti della dashboard con ruoli e audit per utente.

Tre ruoli: `admin` (tutto, inclusi utenti e webhook), `operator` (approva,
invia, gestisce handoff e preventivi), `viewer` (solo lettura). I token sono
salvati solo come SHA-256 e mostrati una volta sola alla creazione; il vecchio
token della dashboard resta valido come admin "owner".

Uso:
    python3 identity.py --list
    python3 identity.py --add "Anna" --role operator
    python3 identity.py --revoke "Anna"
    python3 identity.py --self-test
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import struct
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import config

ROLES = {"viewer": 1, "operator": 2, "admin": 3}
ROLE_LABELS = {"viewer": "solo lettura", "operator": "operatore", "admin": "admin"}
MAX_USERS = 20
# La rotazione non deve rompere la catena: ai segmenti numerati si aggiunge il
# file vivo, e `verify_audit` li controlla in ordine cronologico.
AUDIT_MAX_BYTES = 1_000_000
# Il pannello Eventi non deve poter chiedere l'intero archivio con ?limit=10^9.
MAX_RECENT_AUDIT = 500
AUDIT_SEGMENT = re.compile(r"^user_audit\.(\d+)\.jsonl$")


def _base(base: Path | None) -> Path:
    folder = Path(base or config.BASE)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def users_path(base: Path | None = None) -> Path:
    return _base(base) / "users.json"


def audit_path(base: Path | None = None) -> Path:
    return _base(base) / "user_audit.jsonl"


def twofactor_path(base: Path | None = None) -> Path:
    return _base(base) / "twofactor.json"


def _hash(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def _load(base: Path | None = None) -> dict:
    path = users_path(base)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    users = data.get("users")
    data["users"] = users if isinstance(users, list) else []
    return data


def _save(data: dict, base: Path | None = None) -> None:
    path = users_path(base)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _iso() -> str:
    return datetime.now(config.resolve_timezone()).isoformat(timespec="seconds")


def create_user(name: str, role: str, token: str | None = None, base: Path | None = None) -> dict:
    name = str(name or "").strip()[:60]
    role = str(role or "").strip().lower()
    if not name:
        raise ValueError("nome utente mancante")
    if role not in ROLES:
        raise ValueError("ruolo non valido: usa admin, operator o viewer")
    data = _load(base)
    if any(str(u.get("name", "")).lower() == name.lower() for u in data["users"]):
        raise ValueError("utente già esistente")
    if len(data["users"]) >= MAX_USERS:
        raise ValueError(f"massimo {MAX_USERS} utenti")
    raw = str(token or secrets.token_urlsafe(24)).strip()
    if len(raw) < 16:
        raise ValueError("token troppo corto (minimo 16 caratteri)")
    entry = {
        "name": name,
        "role": role,
        "token": _hash(raw),
        "created_at": _iso(),
        "active": True,
    }
    data["users"].append(entry)
    _save(data, base)
    result = {k: v for k, v in entry.items() if k != "token"}
    result["token"] = raw  # mostrato una sola volta
    return result


def list_users(base: Path | None = None) -> list[dict]:
    return [
        {k: v for k, v in user.items() if k != "token"}
        for user in _load(base)["users"]
    ]


def revoke_user(name: str, base: Path | None = None) -> bool:
    data = _load(base)
    changed = False
    for user in data["users"]:
        if str(user.get("name", "")).lower() == str(name or "").strip().lower() and user.get("active", True):
            user["active"] = False
            user["revoked_at"] = _iso()
            changed = True
    if changed:
        _save(data, base)
    return changed


def authenticate(token: str, base: Path | None = None) -> dict | None:
    """Restituisce l'identità attiva che possiede il token, o None."""
    raw = str(token or "").strip()
    if not raw:
        return None
    digest = _hash(raw)
    found = None
    for user in _load(base)["users"]:
        if user.get("active") and hmac.compare_digest(str(user.get("token") or ""), digest):
            found = {"name": user.get("name", ""), "role": user.get("role", "viewer")}
    return found


def role_allows(role: str, required: str) -> bool:
    return ROLES.get(str(role or ""), 0) >= ROLES.get(str(required or "viewer"), 99)


def _audit_hash(entry: dict, prev: str) -> str:
    """Hash della voce legato alla precedente: cancellare una riga rompe la catena."""
    payload = "|".join([
        prev,
        str(entry.get("ts") or ""),
        str(entry.get("user") or ""),
        str(entry.get("action") or ""),
        str(entry.get("detail") or ""),
        "1" if entry.get("ok") else "0",
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def audit_segments(base: Path | None = None) -> list[Path]:
    """Segmenti ruotati di `user_audit`, dal più vecchio al più recente."""
    folder = Path(base or config.BASE)
    try:
        found = []
        for path in folder.iterdir():
            match = AUDIT_SEGMENT.match(path.name)
            if match:
                found.append((int(match.group(1)), path))
    except OSError:
        return []
    return [path for _, path in sorted(found)]


def _tail_hash(path: Path) -> str:
    """Ultimo hash del file, oppure stringa vuota se non ce ne sono."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        try:
            item = json.loads(line)
        except Exception:
            continue
        if isinstance(item, dict) and item.get("hash"):
            return str(item["hash"])
    return ""


def _last_audit_hash(path: Path) -> str:
    """Continua la catena dall'ultimo segmento quando il file vivo è nuovo."""
    current = _tail_hash(path)
    if current:
        return current
    for segment in reversed(audit_segments(path.parent)):
        tail = _tail_hash(segment)
        if tail:
            return tail
    return "genesis"


def rotate_audit(base: Path | None = None) -> Path | None:
    """Sposta il log corrente in `user_audit.NNNN.jsonl` senza perdere voci.

    Il file nuovo riparte dall'hash dell'ultima voce ruotata, quindi la catena
    resta continua attraverso i segmenti. Se un segmento viene cancellato la
    verifica fallisce alla prima voce successiva: il taglio è dimostrabile.
    """
    path = audit_path(base)
    if not _tail_hash(path):
        return None
    numbers = []
    for item in audit_segments(path.parent):
        match = AUDIT_SEGMENT.match(item.name)
        if match:
            numbers.append(int(match.group(1)))
    target = path.with_name(f"user_audit.{max(numbers, default=0) + 1:04d}.jsonl")
    os.replace(path, target)
    os.chmod(target, 0o600)
    return target


def audit(actor: str, action: str, detail: str = "", ok: bool = True,
          base: Path | None = None) -> dict:
    entry = {
        "ts": _iso(),
        "user": str(actor or "owner")[:60],
        "action": str(action or "")[:80],
        "detail": str(detail or "")[:300],
        "ok": bool(ok),
    }
    try:
        path = audit_path(base)
        prev = _last_audit_hash(path)
        entry["prev"] = prev
        entry["hash"] = _audit_hash(entry, prev)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        os.chmod(path, 0o600)
        if path.stat().st_size > AUDIT_MAX_BYTES:
            rotate_audit(path.parent)
    except OSError:
        pass
    return entry


def verify_audit(base: Path | None = None) -> dict:
    """Ricalcola la catena su segmenti ruotati e file vivo, in ordine.

    Una riga cancellata o modificata, un segmento mancante o un file fuori
    ordine fanno fallire la verifica.
    """
    segments = audit_segments(base)
    # Contatori locali: un dict misto int/None rendeva il tipo dei valori
    # ``int | None`` e mypy rifiutava ``report["entries"] += 1``.
    entries = 0
    chained = 0

    def report(broken_at: int | None = None, broken_in: str | None = None) -> dict:
        return {"ok": broken_at is None, "entries": entries, "chained": chained,
                "broken_at": broken_at, "broken_in": broken_in, "segments": len(segments)}

    def broken(index: int, name: str) -> dict:
        return report(index, name)

    prev = "genesis"
    for path in segments + [audit_path(base)]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            continue
        except OSError:
            return broken(0, path.name)
        lines = [line for line in text.splitlines() if line.strip()]
        for index, line in enumerate(lines):
            entries += 1
            try:
                item = json.loads(line)
            except Exception:
                return broken(index, path.name)
            if not isinstance(item, dict):
                return broken(index, path.name)
            if not item.get("hash"):
                # Righe precedenti alla catena: ammesse solo come prefisso.
                continue
            if str(item.get("prev") or "") != prev:
                return broken(index, path.name)
            expected = _audit_hash(item, prev)
            if not hmac.compare_digest(str(item.get("hash")), expected):
                return broken(index, path.name)
            prev = expected
            chained += 1
    return report()


def totp_secret() -> str:
    """Segreto base32 compatibile con le app di autenticazione."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def totp_code(secret: str, at: float | None = None, step: int = 30) -> str:
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    counter = int((time.time() if at is None else at) // step)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{code % 1_000_000:06d}"


def totp_verify(secret: str, code: str, at: float | None = None) -> bool:
    clean = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(clean) != 6:
        return False
    moment = time.time() if at is None else at
    for delta in (-1, 0, 1):
        if hmac.compare_digest(totp_code(secret, moment + delta * 30), clean):
            return True
    return False


def totp_status(base: Path | None = None) -> dict:
    try:
        data = json.loads(twofactor_path(base).read_text(encoding="utf-8"))
    except Exception:
        data = {}
    return {"enabled": bool(data.get("enabled") and data.get("secret")), "secret_set": bool(data.get("secret"))}


def enable_totp(label: str = "Gina dashboard", base: Path | None = None) -> dict:
    secret = totp_secret()
    path = twofactor_path(base)
    path.write_text(json.dumps({"enabled": True, "secret": secret, "created_at": _iso()}) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    label_enc = str(label or "Gina dashboard").replace(" ", "%20")
    uri = f"otpauth://totp/{label_enc}?secret={secret}&issuer=Gina&digits=6&period=30"
    return {"enabled": True, "secret": secret, "otpauth": uri}


def disable_totp(base: Path | None = None) -> bool:
    try:
        twofactor_path(base).unlink()
        return True
    except FileNotFoundError:
        return False


def check_totp(code: str, base: Path | None = None) -> bool:
    """Verifica il codice se il 2FA è attivo; senza 2FA non blocca il login."""
    status = totp_status(base)
    if not status["enabled"]:
        return True
    try:
        secret = json.loads(twofactor_path(base).read_text(encoding="utf-8"))["secret"]
    except Exception:
        return False
    return totp_verify(str(secret), code)


def recent_audit(limit: int = 100, base: Path | None = None) -> list[dict]:
    """Ultime voci di audit, dalla più recente, anche dentro i segmenti ruotati.

    Legge dal file vivo all'indietro nei segmenti e si ferma appena ha le `limit`
    voci: il pannello Eventi non rilegge e non ri-analizza tutto l'archivio a ogni
    richiesta. L'ordine restituito è dal più recente al più vecchio.
    """
    wanted = max(1, min(int(limit), MAX_RECENT_AUDIT))
    rows: list[dict] = []
    # File vivo (il più recente) e poi segmenti dalla numerazione più alta.
    for path in [audit_path(base), *reversed(audit_segments(base))]:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except Exception:
                continue
            if isinstance(item, dict):
                rows.append(item)
                if len(rows) >= wanted:
                    return rows
    return rows


def self_test() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        owner = create_user("Owner", "admin", token="owner-token-0123456789", base=base)
        assert owner["role"] == "admin"
        assert owner["token"] == "owner-token-0123456789", "il token va mostrato una volta"
        assert "token" not in list_users(base)[0], "il token non va mai riletto"
        saved = json.loads(users_path(base).read_text(encoding="utf-8"))
        assert saved["users"][0]["token"] != owner["token"], "il token deve essere hashato"
        assert len(saved["users"][0]["token"]) == 64

        who = authenticate("owner-token-0123456789", base)
        assert who == {"name": "Owner", "role": "admin"}, who
        assert authenticate("token-sbagliato-0000", base) is None

        viewer = create_user("Lettura", "viewer", base=base)
        assert viewer["role"] == "viewer"
        assert not role_allows("viewer", "operator")
        assert role_allows("admin", "operator")
        assert role_allows("operator", "viewer")

        try:
            create_user("Owner", "viewer", base=base)
            raise AssertionError("utente duplicato accettato")
        except ValueError:
            pass
        try:
            create_user("X", "superuser", base=base)
            raise AssertionError("ruolo invalido accettato")
        except ValueError:
            pass

        audit("Owner", "approve", "apr-1", base=base)
        audit("Owner", "send", "apr-2", base=base)
        rows = recent_audit(10, base)
        assert rows[0]["action"] == "send" and rows[0]["user"] == "Owner", rows
        chain = verify_audit(base)
        assert chain["ok"] and chain["chained"] == 2, chain

        secret = totp_secret()
        assert len(secret) >= 24, secret
        assert totp_verify(secret, totp_code(secret)), "il codice corrente deve valere"
        wrong = "000000" if totp_code(secret) != "000000" else "111111"
        assert not totp_verify(secret, wrong), "un codice sbagliato non deve valere"
        assert check_totp("", base=base), "senza 2FA il login non chiede il codice"
        enrolled = enable_totp(base=base)
        assert totp_status(base)["enabled"] and enrolled["otpauth"].startswith("otpauth://totp/"), enrolled
        assert check_totp(totp_code(enrolled["secret"]), base=base)
        assert not check_totp(wrong, base=base), "con 2FA attivo il codice sbagliato deve fallire"
        assert disable_totp(base) is True and totp_status(base)["enabled"] is False

        # Rotazione: segmenti numerati, nessuna voce persa, catena ancora valida.
        global AUDIT_MAX_BYTES
        rotation_base = base / "rotazione"
        rotation_base.mkdir()
        original_limit = AUDIT_MAX_BYTES
        AUDIT_MAX_BYTES = 240
        try:
            for index in range(6):
                audit("Owner", f"rot-{index}", "dettaglio " * 8, base=rotation_base)
        finally:
            AUDIT_MAX_BYTES = original_limit
        rotated = verify_audit(rotation_base)
        assert rotated["ok"] and rotated["entries"] == 6, rotated
        assert rotated["segments"] >= 2, rotated
        audit_segments(rotation_base)[0].unlink()
        assert verify_audit(rotation_base)["ok"] is False, "un segmento mancante rompe la catena"

        broken = audit_path(base)
        lines = broken.read_text(encoding="utf-8").splitlines()
        broken.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
        assert verify_audit(base)["ok"] is True, "togliere la coda non rompe le voci precedenti"
        broken.write_text("\n".join([lines[0], lines[1].replace("apr-2", "apr-9")]) + "\n", encoding="utf-8")
        assert verify_audit(base)["ok"] is False, "modificare una voce deve rompere la catena"

        assert revoke_user("Lettura", base) is True
        assert authenticate(viewer["token"], base) is None
        assert revoke_user("Lettura", base) is False
    print("SELF-TEST OK")


def main() -> int:
    parser = argparse.ArgumentParser(description="Utenti dashboard con ruoli")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--add", default="")
    parser.add_argument("--role", default="operator")
    parser.add_argument("--revoke", default="")
    parser.add_argument("--enable-2fa", action="store_true")
    parser.add_argument("--disable-2fa", action="store_true")
    parser.add_argument("--verify-audit", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if args.enable_2fa:
        info = enable_totp()
        print(json.dumps({"enabled": True, "otpauth": info["otpauth"]}, ensure_ascii=False, indent=2))
        print("Inquadra il QR dall'URL otpauth e conserva il segreto fuori dal PC.", file=sys.stderr)
        return 0
    if args.disable_2fa:
        print("2FA disattivato" if disable_totp() else "2FA non era attivo")
        return 0
    if args.verify_audit:
        report = verify_audit()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1
    try:
        if args.add:
            created = create_user(args.add, args.role)
            print(json.dumps(created, ensure_ascii=False))
            print("Salva questo token ora: non sarà più mostrato.", file=sys.stderr)
            return 0
        if args.revoke:
            print("revocato" if revoke_user(args.revoke) else "utente non trovato")
            return 0
    except ValueError as exc:
        print(f"identity: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(list_users(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
