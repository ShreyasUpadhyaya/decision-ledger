"""Encryption layer for ruleset content at-rest using Fernet symmetric encryption."""
from typing import Optional
import json
from cryptography.fernet import Fernet
from cryptography.fernet import InvalidToken

from .config import settings

_fernet: Optional[Fernet] = None


def get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        key = settings.decision_ledger_encryption_key
        if not key:
            raise ValueError("DECISION_LEDGER_ENCRYPTION_KEY environment variable is required")
        _fernet = Fernet(key.encode('utf-8'))
    return _fernet


def encrypt_content(content: dict) -> str:
    """JSON-serialize then Fernet-encrypt. Returns base64 ciphertext string."""
    f = get_fernet()
    serialized = json.dumps(content, sort_keys=True)
    encrypted_bytes = f.encrypt(serialized.encode('utf-8'))
    return encrypted_bytes.decode('utf-8')


def decrypt_content(ciphertext: str) -> dict:
    """Fernet-decrypt then JSON-deserialize. Returns original dict."""
    f = get_fernet()
    try:
        decrypted_bytes = f.decrypt(ciphertext.encode('utf-8'))
        return json.loads(decrypted_bytes.decode('utf-8'))
    except InvalidToken as e:
        raise ValueError("Failed to decrypt content: Invalid token or incorrect key") from e
