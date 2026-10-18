"""Public build settings. Private deployment values are supplied at build time."""
from __future__ import annotations

import base64
import ipaddress
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Distribution:
    relay: str = ""
    accounts_url: str = ""
    updates_url: str = ""
    update_public_key: str = ""
    publisher: str = ""
    support_url: str = ""
    privacy_url: str = ""
    terms_url: str = ""


def validate_public_profile(raw: dict) -> Distribution:
    profile = Distribution(**raw)
    host, sep, port = profile.relay.rpartition(":")
    if not sep or not port.isascii() or not port.isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError("Relay pubblico non valido: usare nome DNS:porta.")
    if not host or host.endswith((".invalid", ".example", ".test", ".localhost")) or "." not in host:
        raise ValueError("Specificare il dominio pubblico reale del relay.")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("Il relay pubblico deve usare un nome DNS.")
    for value in (profile.accounts_url, profile.updates_url, profile.support_url,
                  profile.privacy_url, profile.terms_url):
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or
                parsed.password or parsed.fragment):
            raise ValueError("Tutti gli URL pubblici devono usare HTTPS senza credenziali o frammenti.")
    if len(base64.b64decode(profile.update_public_key, validate=True)) != 32:
        raise ValueError("Chiave pubblica Ed25519 non valida.")
    if not profile.publisher.strip() or any(marker in profile.publisher.upper() for marker in ("TODO", "DIMOSTRATIV", " DEMO")):
        raise ValueError("Identificare il titolare del prodotto.")
    return profile


try:
    from remotedesk._distribution import PROFILE, PUBLIC
except ImportError:
    PROFILE = {}
    PUBLIC = False

CURRENT = validate_public_profile(PROFILE) if PUBLIC else Distribution(**PROFILE)
