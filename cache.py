#!/usr/bin/env python3
"""Cache TTL per le risposte frequenti di Gina.

Riduce latenza e costo quando più clienti fanno la stessa domanda (orari,
zona, servizi). La chiave è normalizzata: maiuscole, accenti e punteggiatura
non contano. La cache è per-processo, opzionalmente persistita, con limite di
dimensione e scadenza: non cresce senza controllo e non serve dati vecchi.

Uso:
    python3 cache.py --stats
    python3 cache.py --self-test
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
import threading
import time
import unicodedata
from pathlib import Path

DEFAULT_TTL = 900.0
DEFAULT_MAX = 500


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def key_for(text: str, namespace: str = "reply") -> str:
    return hashlib.sha256(f"{namespace}:{normalize(text)}".encode()).hexdigest()


class TTLCache:
    """Piccola cache LRU+TTL, sicura fra thread, con statistiche reali."""

    def __init__(self, path: str | Path | None = None, max_entries: int = DEFAULT_MAX,
                 default_ttl: float = DEFAULT_TTL):
        self.path = Path(path) if path else None
        self.max_entries = max(1, int(max_entries))
        self.default_ttl = max(1.0, float(default_ttl))
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {}
        self.hits = 0
        self.misses = 0
        self.stores = 0
        self.evictions = 0
        self._load()

    def _load(self) -> None:
        if not self.path:
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        items = raw.get("items") if isinstance(raw, dict) else None
        if not isinstance(items, dict):
            return
        now = time.time()
        for key, item in items.items():
            if isinstance(item, dict) and float(item.get("expires", 0)) > now:
                self._data[key] = item
        self.hits = int(raw.get("hits") or 0)
        self.misses = int(raw.get("misses") or 0)
        self.stores = int(raw.get("stores") or 0)

    def _save_locked(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            payload = {
                "items": self._data,
                "hits": self.hits,
                "misses": self.misses,
                "stores": self.stores,
            }
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def get(self, text: str, namespace: str = "reply") -> str | None:
        key = key_for(text, namespace)
        now = time.time()
        with self._lock:
            item = self._data.get(key)
            if not item or float(item.get("expires", 0)) <= now:
                self._data.pop(key, None)
                self.misses += 1
                return None
            self.hits += 1
            item["used"] = now
            return str(item.get("value") or "")

    def set(self, text: str, value: str, ttl: float | None = None, namespace: str = "reply") -> None:
        key = key_for(text, namespace)
        now = time.time()
        with self._lock:
            self._data[key] = {
                "value": str(value)[:4000],
                "expires": now + float(ttl or self.default_ttl),
                "used": now,
            }
            self.stores += 1
            while len(self._data) > self.max_entries:
                oldest = min(self._data, key=lambda k: float(self._data[k].get("used", 0)))
                self._data.pop(oldest, None)
                self.evictions += 1
            self._save_locked()

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._save_locked()

    def stats(self) -> dict:
        with self._lock:
            now = time.time()
            live = sum(1 for item in self._data.values() if float(item.get("expires", 0)) > now)
            total = self.hits + self.misses
            return {
                "entries": live,
                "max_entries": self.max_entries,
                "hits": self.hits,
                "misses": self.misses,
                "stores": self.stores,
                "evictions": self.evictions,
                "hit_rate": round(self.hits / total, 4) if total else 0.0,
            }


_CACHE: TTLCache | None = None
_CACHE_LOCK = threading.Lock()


def shared(path: str | Path | None = None) -> TTLCache:
    """Cache condivisa dal processo della dashboard."""
    global _CACHE
    with _CACHE_LOCK:
        if _CACHE is None:
            _CACHE = TTLCache(path or (Path(tempfile.gettempdir()) / "gina-cache.json"))
        return _CACHE


def self_test() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "cache.json"
        cache = TTLCache(path, max_entries=3, default_ttl=60)
        cache.set("Che orari avete?", "Lun–Ven 9–18")
        assert cache.get("che ORARI avete??") == "Lun–Ven 9–18", cache.stats()
        assert cache.get("domanda diversa") is None
        stats = cache.stats()
        assert stats["hits"] == 1 and stats["misses"] == 1, stats

        cache.set("a", "1", ttl=-1)  # scaduto subito
        assert cache.get("a") is None

        for index in range(5):
            cache.set(f"voce {index}", f"valore {index}", ttl=600)
        assert cache.stats()["entries"] == 3, cache.stats()

        reloaded = TTLCache(path, max_entries=3)
        assert reloaded.get("voce 4") == "valore 4", reloaded.stats()
        print("SELF-TEST OK")
        print(json.dumps(cache.stats(), ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(description="Cache risposte frequenti")
    parser.add_argument("--stats", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    path = Path(os.environ.get("SECRETARY_CACHE", str(Path.home() / ".openclaw" / "secretary" / "cache.json")))
    print(json.dumps(TTLCache(path).stats(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
