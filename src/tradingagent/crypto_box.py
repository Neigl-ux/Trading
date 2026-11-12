"""Verschlüsselung der exportierten Daten für die Handy-App.

Verfahren (identisch mit der Web Crypto API im Browser):
- Schlüssel: PBKDF2-HMAC-SHA256 aus deinem Passwort, 600.000 Runden, zufälliges Salt
- Verschlüsselung: AES-256-GCM, für jede Datei ein neuer Zufallswert (IV)

Ohne das Passwort sind die Dateien auf GitHub Pages nicht lesbar. Das
Passwort steht nur als GitHub-Secret APP_PASSPHRASE und in deinem Kopf.
"""

from __future__ import annotations

import base64
import gzip
import json
import os
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

ITERATIONS = 600_000
MIN_PASSPHRASE_LENGTH = 12


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text)


def derive_key(passphrase: str, salt: bytes, iterations: int = ITERATIONS) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations)
    return kdf.derive(passphrase.encode("utf-8"))


class Box:
    """Verschlüsselt JSON-Daten mit einem Schlüssel pro Export-Lauf."""

    def __init__(self, passphrase: str, *, salt: bytes | None = None, iterations: int = ITERATIONS):
        if len(passphrase) < MIN_PASSPHRASE_LENGTH:
            raise ValueError(f"Das Passwort muss mindestens {MIN_PASSPHRASE_LENGTH} Zeichen lang sein.")
        self.salt = salt or os.urandom(16)
        self.iterations = iterations
        self._aes = AESGCM(derive_key(passphrase, self.salt, iterations))

    def kdf_header(self) -> dict[str, Any]:
        return {"name": "PBKDF2", "hash": "SHA-256", "iter": self.iterations, "salt": _b64(self.salt)}

    def seal(self, payload: Any) -> dict[str, Any]:
        """Erst gzip (spart etwa drei Viertel), dann verschlüsseln. Komprimierte Daten tragen "z": 1."""
        iv = os.urandom(12)
        plaintext = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        packed = gzip.compress(plaintext, compresslevel=6, mtime=0)
        return {"v": 1, "enc": True, "z": 1, "iv": _b64(iv), "ct": _b64(self._aes.encrypt(iv, packed, None))}


def open_sealed(passphrase: str, kdf: dict[str, Any], sealed: dict[str, Any]) -> Any:
    """Entschlüsseln (für Tests und Kontrolle; die App macht das im Browser)."""
    key = derive_key(passphrase, _unb64(kdf["salt"]), int(kdf["iter"]))
    plaintext = AESGCM(key).decrypt(_unb64(sealed["iv"]), _unb64(sealed["ct"]), None)
    if sealed.get("z"):
        plaintext = gzip.decompress(plaintext)
    return json.loads(plaintext)
