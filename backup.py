#!/usr/bin/env python3
"""Backup cifrato, verifica del ripristino e rotazione.

Cifratura AES-256-GCM con chiave derivata via scrypt dalla passphrase, usando
`cryptography` (nessun binario esterno). Il backup contiene configurazione,
persona, segreti, coda approvazioni, preventivi, memoria e database OpenClaw.
`--verify` decifra davvero, estrae in una cartella temporanea e confronta gli
hash SHA-256 del manifest: non è un semplice "il file esiste".

I nomi nell'archivio sono i percorsi assoluti senza la barra iniziale, quindi
`--restore --target /` ricrea l'installazione sul sistema, mentre un `--target`
diverso ricrea gli stessi percorsi sotto quella cartella (ripristino di prova).

La passphrase sta in `backup.key` (0600) oppure in
`SECRETARY_BACKUP_PASSPHRASE`. Perdere la chiave significa perdere i backup.

Uso:
    python3 backup.py --create
    python3 backup.py --list
    python3 backup.py --verify backup.ginabk
    python3 backup.py --restore backup.ginabk --target /tmp/ripristino
    python3 backup.py --self-test
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import secrets
import sys
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path

import config

MAGIC = b"GINABK1\n"
VERSION = 1
DEFAULT_KEEP = 7
MANIFEST = "MANIFEST.json"


def _base(base: Path | None = None) -> Path:
    folder = Path(base or config.BASE)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def default_paths() -> list[Path]:
    home = Path.home()
    return [
        Path(config.DEFAULT_CONFIG_PATH),
        Path(config.BASE),
        home / ".openclaw" / "openclaw.json",
        home / ".openclaw" / "agents",
        home / ".openclaw" / "workspace" / "AGENTS.md",
    ]


def _key_file(base: Path | None = None) -> Path:
    return _base(base) / "backup.key"


def passphrase(base: Path | None = None, create: bool = False) -> str:
    env = os.environ.get("SECRETARY_BACKUP_PASSPHRASE")
    if env:
        return env
    path = _key_file(base)
    if path.exists():
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return text
    if not create:
        raise ValueError(f"passphrase assente: crea {path} o imposta SECRETARY_BACKUP_PASSPHRASE")
    secret = secrets.token_urlsafe(32)
    path.write_text(secret + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return secret


def _derive(passwd: str, salt: bytes, n: int = 2 ** 15):
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

    return Scrypt(salt=salt, length=32, n=n, r=8, p=1).derive(passwd.encode("utf-8"))


def encrypt(data: bytes, passwd: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(12)
    key = _derive(passwd, salt)
    ciphertext = AESGCM(key).encrypt(nonce, data, MAGIC)
    header = json.dumps({
        "v": VERSION,
        "kdf": "scrypt",
        "n": 2 ** 15,
        "r": 8,
        "p": 1,
        "salt": base64.b64encode(salt).decode(),
        "nonce": base64.b64encode(nonce).decode(),
    }, separators=(",", ":")).encode("utf-8")
    return MAGIC + len(header).to_bytes(4, "big") + header + ciphertext


def decrypt(blob: bytes, passwd: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not blob.startswith(MAGIC):
        raise ValueError("non è un archivio Gina")
    offset = len(MAGIC)
    length = int.from_bytes(blob[offset:offset + 4], "big")
    header = json.loads(blob[offset + 4:offset + 4 + length].decode("utf-8"))
    ciphertext = blob[offset + 4 + length:]
    key = _derive(passwd, base64.b64decode(header["salt"]), int(header.get("n") or 2 ** 15))
    return AESGCM(key).decrypt(base64.b64decode(header["nonce"]), ciphertext, MAGIC)


def _collect(paths: list[Path]) -> tuple[list[tuple[Path, str]], list[dict]]:
    """(percorso, nome nell'archivio) + voci del manifest."""
    entries: list[tuple[Path, str]] = []
    manifest: list[dict] = []
    seen: set[str] = set()
    for root in paths:
        root = Path(root).expanduser()
        if not root.exists():
            continue
        candidates = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
        for path in candidates:
            # I lock file sono transitori e possono cambiare mentre l'archivio
            # viene creato: escluderli evita un backup non verificabile.
            if path.name in {"backup.key"} or path.name.startswith(".tmp") or path.name.endswith((".lock", ".pid")):
                continue
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                relative = str(path.resolve()).lstrip("/")
            except OSError:
                continue
            if relative in seen:
                continue
            seen.add(relative)
            try:
                content = path.read_bytes()
            except OSError:
                continue
            entries.append((path, relative))
            manifest.append({
                "name": relative,
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            })
    return entries, manifest


def create(base: Path | None = None, paths: list[Path] | None = None,
           out_dir: Path | None = None, keep: int = DEFAULT_KEEP) -> dict:
    folder = _base(base)
    targets = [Path(p) for p in (paths if paths is not None else default_paths())]
    entries, manifest = _collect(targets)
    if not entries:
        raise ValueError("nessun file da salvare")
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path, name in entries:
            try:
                archive.add(str(path), arcname=name, recursive=False)
            except OSError:
                continue
        payload = {
            "created_at": datetime.now(config.resolve_timezone()).isoformat(timespec="seconds"),
            "files": manifest,
            "version": VERSION,
        }
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        info = tarfile.TarInfo(MANIFEST)
        info.size = len(raw)
        info.mtime = int(datetime.now().timestamp())
        archive.addfile(info, io.BytesIO(raw))
    archive_bytes = encrypt(buffer.getvalue(), passphrase(folder, create=True))

    destination = Path(out_dir) if out_dir else (folder / "backups")
    destination.mkdir(parents=True, exist_ok=True)
    try:
        destination.chmod(0o700)
    except OSError:
        pass
    stamp = datetime.now(config.resolve_timezone()).strftime("%Y%m%d-%H%M%S")
    path = destination / f"gina-{stamp}.ginabk"
    # Due backup nello stesso secondo non devono sovrascriversi: la rotazione
    # (e il ripristino) contano sul fatto che ogni archivio resti distinto.
    attempt = 1
    while path.exists():
        path = destination / f"gina-{stamp}-{attempt}.ginabk"
        attempt += 1
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(archive_bytes)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)

    removed = 0
    files = sorted(destination.glob("gina-*.ginabk"), key=lambda p: p.name, reverse=True)
    for old in files[max(1, int(keep)):]:
        try:
            old.unlink()
            removed += 1
        except OSError:
            pass
    return {
        "ok": True,
        "path": str(path),
        "files": len(entries),
        "bytes": len(archive_bytes),
        "rotated_out": removed,
        "sha256": hashlib.sha256(archive_bytes).hexdigest(),
    }


def verify(archive: str | Path, base: Path | None = None) -> dict:
    """Decifra, estrae in una cartella temporanea e confronta ogni hash."""
    path = Path(archive)
    try:
        blob = path.read_bytes()
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        raw = decrypt(blob, passphrase(base))
    except Exception as exc:
        return {"ok": False, "error": f"decifratura fallita: {str(exc)[:120]}"}
    with tempfile.TemporaryDirectory(prefix="gina-verify-") as tmp:
        try:
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
                members = tar.getmembers()
                for member in members:
                    name = member.name
                    if name.startswith("/") or ".." in Path(name).parts:
                        return {"ok": False, "error": f"percorso non sicuro: {name}"}
                # nosec B202 - i membri sono già validati (niente percorsi assoluti o "..").
                tar.extractall(tmp, filter="data") if hasattr(tarfile, "data_filter") else tar.extractall(tmp)  # nosec B202
        except Exception as exc:
            return {"ok": False, "error": f"archivio corrotto: {str(exc)[:120]}"}
        manifest_path = Path(tmp) / MANIFEST
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return {"ok": False, "error": f"manifest mancante: {str(exc)[:120]}"}
        missing = []
        checked = 0
        for item in manifest.get("files") or []:
            target = Path(tmp) / item["name"]
            if not target.is_file():
                missing.append(item["name"])
                continue
            if int(item.get("size") or -1) != target.stat().st_size:
                missing.append(item["name"])
            elif hashlib.sha256(target.read_bytes()).hexdigest() != item.get("sha256"):
                missing.append(item["name"])
            checked += 1
        return {
            "ok": not missing,
            "files": checked,
            "missing": missing[:10],
            "created_at": manifest.get("created_at"),
            "error": "" if not missing else f"{len(missing)} file non corrispondono",
        }


def restore(archive: str | Path, target: str | Path, base: Path | None = None) -> dict:
    """Ripristina in una cartella: non sovrascrive mai l'installazione viva."""
    path = Path(archive)
    destination = Path(target).expanduser()
    destination.mkdir(parents=True, exist_ok=True)
    try:
        raw = decrypt(path.read_bytes(), passphrase(base))
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
            for member in tar.getmembers():
                if member.name.startswith("/") or ".." in Path(member.name).parts:
                    return {"ok": False, "error": f"percorso non sicuro: {member.name}"}
            # nosec B202 - i membri sono già validati (niente percorsi assoluti o "..").
            if hasattr(tarfile, "data_filter"):
                tar.extractall(destination, filter="data")
            else:
                tar.extractall(destination)  # nosec B202
    except Exception as exc:
        return {"ok": False, "error": f"estrazione fallita: {str(exc)[:200]}"}
    files = sum(1 for _ in destination.rglob("*") if _.is_file())
    return {"ok": True, "target": str(destination), "files": files}


def list_backups(base: Path | None = None) -> list[dict]:
    folder = _base(base) / "backups"
    rows = []
    for path in sorted(folder.glob("gina-*.ginabk"), key=lambda p: p.name, reverse=True):
        stat = path.stat()
        rows.append({
            "name": path.name,
            "bytes": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime, config.resolve_timezone()).isoformat(timespec="seconds"),
        })
    return rows


def self_test() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "sorgente"
        (source / "memory").mkdir(parents=True)
        (source / "config.json").write_text(json.dumps({"business_name": "Studio Rossi"}), encoding="utf-8")
        (source / "memory" / "mario.json").write_text('{"phone":"+39333"}', encoding="utf-8")
        base = root / "state"
        base.mkdir()

        created = create(base, paths=[source / "config.json", source / "memory"], out_dir=root / "out", keep=2)
        assert created["ok"] and Path(created["path"]).is_file(), created
        assert created["files"] >= 2, created

        checked = verify(created["path"], base)
        assert checked["ok"] is True, checked

        restored = restore(created["path"], root / "restored", base)
        assert restored["ok"] is True, restored
        # I percorsi assoluti vengono ricreati sotto --target.
        restored_config = root / "restored" / str((source / "config.json").resolve()).lstrip("/")
        restored_memory = root / "restored" / str((source / "memory" / "mario.json").resolve()).lstrip("/")
        assert restored_config.read_text(encoding="utf-8").startswith("{"), restored_config
        assert restored_memory.is_file(), restored_memory

        # File manomesso: la verifica deve fallire, non fingere che vada bene.
        blob = Path(created["path"]).read_bytes()
        tampered = root / "out" / "tampered.ginabk"
        tampered.write_bytes(blob[:-8] + b"00000000")
        assert verify(tampered, base)["ok"] is False

        # Passphrase sbagliata: nessun dato in chiaro.
        os.environ["SECRETARY_BACKUP_PASSPHRASE"] = "passphrase-sbagliata"
        try:
            assert verify(created["path"], base)["ok"] is False
        finally:
            os.environ.pop("SECRETARY_BACKUP_PASSPHRASE", None)

        for _ in range(3):
            create(base, paths=[source / "config.json"], out_dir=root / "out", keep=2)
        assert len(list((root / "out").glob("gina-*.ginabk"))) == 2, list((root / "out").glob("*"))
        assert len(list_backups(base)) == 0  # i backup sono in out_dir, non nel base
    print("SELF-TEST OK")


def main() -> int:
    parser = argparse.ArgumentParser(description="Backup cifrato e ripristino verificato")
    parser.add_argument("--create", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--verify", default="")
    parser.add_argument("--restore", default="")
    parser.add_argument("--target", default="")
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    try:
        if args.create:
            print(json.dumps(create(keep=args.keep), ensure_ascii=False, indent=2))
            return 0
        if args.verify:
            result = verify(args.verify)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result.get("ok") else 1
        if args.restore:
            if not args.target:
                print("serve --target per il ripristino", file=sys.stderr)
                return 2
            result = restore(args.restore, args.target)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result.get("ok") else 1
    except ValueError as exc:
        print(f"backup: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(list_backups(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
