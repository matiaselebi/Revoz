"""API keys en el almacén seguro del sistema (Windows Credential Manager). Nunca en disco ni en logs."""

from __future__ import annotations

import keyring
from keyring.errors import PasswordDeleteError

SERVICE = "revoz"


def save(provider: str, key: str) -> None:
    provider, key = provider.strip(), key.strip()
    if not provider or not key:
        raise ValueError("Proveedor y clave son obligatorios")
    if len(key) > 1024 or any(c.isspace() for c in key):
        raise ValueError("La clave tiene un formato inválido")
    keyring.set_password(SERVICE, provider, key)


def has(provider: str) -> bool:
    return keyring.get_password(SERVICE, provider.strip()) is not None


def get(provider: str) -> str | None:
    return keyring.get_password(SERVICE, provider.strip())


def delete(provider: str) -> None:
    try:
        keyring.delete_password(SERVICE, provider.strip())
    except PasswordDeleteError:
        pass
