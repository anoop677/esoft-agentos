"""Authenticated encryption for connector credentials.

The encryption key is supplied out-of-band through CONNECTOR_ENCRYPTION_KEY.
Only ciphertext is persisted. AAD binds each ciphertext to its tenant, connector,
and key version to prevent cross-tenant or cross-connector substitution.
"""
import base64
import json
import secrets
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class CredentialEncryptionError(ValueError):
    """Raised when connector credentials cannot be safely encrypted or decrypted."""


@dataclass(frozen=True)
class EncryptedCredentials:
    ciphertext: bytes
    nonce: bytes
    key_version: str


def _aad(tenant_id: str, connector_id: str, key_version: str) -> bytes:
    return f"agentos-connector-secret:v1:{tenant_id}:{connector_id}:{key_version}".encode()


def _cipher(encryption_key: str) -> AESGCM:
    try:
        key = base64.urlsafe_b64decode(encryption_key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise CredentialEncryptionError("Connector encryption key must be URL-safe base64") from exc
    if len(key) != 32:
        raise CredentialEncryptionError("Connector encryption key must decode to exactly 32 bytes")
    return AESGCM(key)


def encrypt_credentials(
    credentials: dict[str, str],
    *,
    encryption_key: str,
    key_version: str,
    tenant_id: str,
    connector_id: str,
) -> EncryptedCredentials:
    if not key_version or len(key_version) > 32:
        raise CredentialEncryptionError("Invalid connector encryption key version")
    nonce = secrets.token_bytes(12)
    plaintext = json.dumps(credentials, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ciphertext = _cipher(encryption_key).encrypt(
        nonce, plaintext, _aad(tenant_id, connector_id, key_version)
    )
    return EncryptedCredentials(ciphertext=ciphertext, nonce=nonce, key_version=key_version)


def decrypt_credentials(
    encrypted: EncryptedCredentials,
    *,
    encryption_key: str,
    tenant_id: str,
    connector_id: str,
) -> dict[str, str]:
    try:
        plaintext = _cipher(encryption_key).decrypt(
            encrypted.nonce,
            encrypted.ciphertext,
            _aad(tenant_id, connector_id, encrypted.key_version),
        )
        values = json.loads(plaintext)
    except (InvalidTag, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CredentialEncryptionError("Connector credentials failed authentication") from exc
    if not isinstance(values, dict) or any(
        not isinstance(key, str) or not isinstance(value, str) for key, value in values.items()
    ):
        raise CredentialEncryptionError("Decrypted connector credential payload is invalid")
    return values
