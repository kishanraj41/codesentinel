"""Reject FHIR base URLs that would let the server fetch arbitrary targets."""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urljoin, urlparse

DEFAULT_ALLOWED_HOSTS = ("hapi.fhir.org",)
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
BLOCKED_HOSTS = {
    "169.254.169.254",
    "metadata.google.internal",
    "metadata",
}


class GuardError(ValueError):
    """A FHIR URL failed the allowlist."""


def _allow_any() -> bool:
    return os.environ.get("FHIR_ALLOW_ANY", "").strip().lower() in {"1", "true", "yes", "on"}


def allowed_hosts() -> set[str]:
    extra = os.environ.get("FHIR_ALLOWED_HOSTS", "")
    hosts = {h.strip().lower() for h in extra.split(",") if h.strip()}
    hosts.update(DEFAULT_ALLOWED_HOSTS)
    return hosts


def _ip_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _assert_public_dns(host: str, resolver) -> None:
    try:
        infos = resolver(host, None)
    except socket.gaierror as exc:
        raise GuardError(f"FHIR host {host} could not be resolved") from exc
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError as exc:
            raise GuardError(f"FHIR host {host} resolved to {addr}") from exc
        if _ip_blocked(ip):
            raise GuardError(f"FHIR host {host} resolves to a blocked address")


def validate_fhir_base_url(url: str, resolver=None) -> str:
    """Return the URL if it is safe to fetch. Raise GuardError otherwise.

    Loopback is allowed so the bundled mock FHIR server works.
    Every other host must be on FHIR_ALLOWED_HOSTS unless FHIR_ALLOW_ANY=1,
    and even then link-local, private, and metadata addresses are refused.
    """
    if url is None:
        raise GuardError("FHIR URL is required")
    raw = str(url).strip()
    if not raw or any(ch in raw for ch in ("\r", "\n", "\t", " ")):
        raise GuardError("FHIR URL is empty or contains whitespace")
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise GuardError("FHIR URL must use http or https")
    if parsed.username or parsed.password:
        raise GuardError("Credentials in a FHIR URL are not allowed")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        raise GuardError("FHIR URL is missing a host")
    if host in BLOCKED_HOSTS or host.endswith(".internal") or host.endswith(".local"):
        raise GuardError(f"FHIR host {host} is blocked")

    ip = None
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None

    if host in LOOPBACK_HOSTS or (ip is not None and ip.is_loopback):
        return raw

    if ip is not None and _ip_blocked(ip):
        raise GuardError(f"FHIR host {host} is a blocked address")

    if host in allowed_hosts():
        return raw

    if _allow_any():
        if ip is None:
            _assert_public_dns(host, resolver or socket.getaddrinfo)
        return raw

    raise GuardError(f"FHIR host {host} is not in FHIR_ALLOWED_HOSTS")


def assert_same_host(base_url: str, target_url: str, resolver=None) -> str:
    """Allow a redirect or bundle next-link only when it stays on the same host and port."""
    absolute = urljoin(base_url, target_url)
    checked = validate_fhir_base_url(absolute, resolver=resolver)
    base = urlparse(base_url)
    target = urlparse(checked)
    base_host = (base.hostname or "").lower()
    target_host = (target.hostname or "").lower()
    if base_host != target_host:
        raise GuardError("FHIR redirect left the original host")
    base_port = base.port or (443 if base.scheme == "https" else 80)
    target_port = target.port or (443 if target.scheme == "https" else 80)
    if base_port != target_port or base.scheme != target.scheme:
        raise GuardError("FHIR redirect changed scheme or port")
    return checked


def validate_patient_id(patient_id: str) -> str:
    import re

    if not isinstance(patient_id, str) or not re.fullmatch(r"[A-Za-z0-9\-\.]{1,64}", patient_id):
        raise GuardError("patient_id contains unsupported characters")
    return patient_id
