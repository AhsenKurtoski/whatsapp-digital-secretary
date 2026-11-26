"""Archivi di backup: il ripristino non deve mai uscire dalla cartella.

Un archivio `.ginabk` può arrivare da un'altra macchina o essere stato
manomesso. Questi test costruiscono archivi ostili (path traversal, link
simbolici) e verificano che `backup.restore` li rifiuti senza scrivere fuori
dalla destinazione.
"""
from __future__ import annotations

import io
import tarfile
import tempfile
import unittest
from pathlib import Path

import backup


def _tar_bytes(entries, symlink: str | None = None) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, text in entries:
            raw = text.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            archive.addfile(info, io.BytesIO(raw))
        if symlink:
            link = tarfile.TarInfo(symlink)
            link.type = tarfile.SYMTYPE
            link.linkname = "/etc/passwd"
            archive.addfile(link)
    return buffer.getvalue()


class TestArchiviOstili(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="gina-backup-test-")
        self.root = Path(self._tmp.name)
        self.base = self.root / "base"
        self.base.mkdir()
        self.out = self.root / "ripristino"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _archive(self, payload: bytes) -> Path:
        path = self.root / "backup.ginabk"
        path.write_bytes(backup.encrypt(payload, backup.passphrase(self.base, create=True)))
        return path

    def test_ripristino_normale(self) -> None:
        archive = self._archive(_tar_bytes([("dati/nota.txt", "ciao")]))
        result = backup.restore(archive, self.out, base=self.base)
        self.assertTrue(result["ok"], result)
        self.assertEqual((self.out / "dati" / "nota.txt").read_text(encoding="utf-8"), "ciao")

    def test_path_traversal_rifiutato(self) -> None:
        archive = self._archive(_tar_bytes([("../fuori.txt", "male")]))
        result = backup.restore(archive, self.out, base=self.base)
        self.assertFalse(result["ok"])
        self.assertIn("percorso non sicuro", result["error"])
        self.assertFalse((self.root / "fuori.txt").exists())

    def test_percorso_assoluto_rifiutato(self) -> None:
        archive = self._archive(_tar_bytes([("/tmp/fuori-assoluto.txt", "male")]))
        result = backup.restore(archive, self.out, base=self.base)
        self.assertFalse(result["ok"])
        self.assertIn("percorso non sicuro", result["error"])
        self.assertFalse(Path("/tmp/fuori-assoluto.txt").exists())

    def test_link_simbolico_rifiutato(self) -> None:
        archive = self._archive(_tar_bytes([("ok.txt", "ciao")], symlink="link-verso-passwd"))
        result = backup.restore(archive, self.out, base=self.base)
        self.assertFalse(result["ok"])
        self.assertIn("membro non consentito", result["error"])
        self.assertFalse((self.out / "link-verso-passwd").exists())

    def test_verify_rifiuta_archivio_ostile(self) -> None:
        archive = self._archive(_tar_bytes([("../fuori.txt", "male")]))
        result = backup.verify(archive, base=self.base)
        self.assertFalse(result["ok"])
        self.assertIn("percorso non sicuro", result["error"])


if __name__ == "__main__":
    unittest.main()
