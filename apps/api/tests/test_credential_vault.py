import base64
import json

import pytest

from app.infrastructure.credential_vault import (
    CredentialEncryptionError,
    EncryptedCredentials,
    decrypt_credentials,
    encrypt_credentials,
)


@pytest.fixture
def encryption_key():
    return base64.urlsafe_b64encode(b"0123456789abcdef0123456789abcdef").decode("ascii")


def test_credentials_encrypt_and_decrypt_with_tenant_binding(encryption_key):
    original = {"api_key": "key-value", "api_secret": "secret-value"}
    encrypted = encrypt_credentials(
        original,
        encryption_key=encryption_key,
        key_version="v1",
        tenant_id="tenant-a",
        connector_id="connector-1",
    )
    assert encrypted.ciphertext != json.dumps(original).encode()
    assert decrypt_credentials(
        encrypted,
        encryption_key=encryption_key,
        tenant_id="tenant-a",
        connector_id="connector-1",
    ) == original


def test_credentials_cannot_be_decrypted_for_another_tenant(encryption_key):
    encrypted = encrypt_credentials(
        {"api_key": "key-value"},
        encryption_key=encryption_key,
        key_version="v1",
        tenant_id="tenant-a",
        connector_id="connector-1",
    )
    with pytest.raises(CredentialEncryptionError):
        decrypt_credentials(
            encrypted,
            encryption_key=encryption_key,
            tenant_id="tenant-b",
            connector_id="connector-1",
        )


def test_credentials_reject_tampered_ciphertext(encryption_key):
    encrypted = encrypt_credentials(
        {"api_key": "key-value"},
        encryption_key=encryption_key,
        key_version="v1",
        tenant_id="tenant-a",
        connector_id="connector-1",
    )
    tampered = EncryptedCredentials(
        ciphertext=encrypted.ciphertext[:-1] + bytes([encrypted.ciphertext[-1] ^ 1]),
        nonce=encrypted.nonce,
        key_version=encrypted.key_version,
    )
    with pytest.raises(CredentialEncryptionError):
        decrypt_credentials(
            tampered,
            encryption_key=encryption_key,
            tenant_id="tenant-a",
            connector_id="connector-1",
        )


def test_invalid_encryption_key_is_rejected():
    with pytest.raises(CredentialEncryptionError):
        encrypt_credentials(
            {"api_key": "key-value"},
            encryption_key="not-a-valid-key",
            key_version="v1",
            tenant_id="tenant-a",
            connector_id="connector-1",
        )
