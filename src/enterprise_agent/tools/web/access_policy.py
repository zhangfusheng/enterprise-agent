import ipaddress
import socket
from urllib.parse import urljoin, urlparse


class WebAccessDenied(PermissionError):
    pass


def _is_blocked_ip(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return any((address.is_private, address.is_loopback, address.is_link_local, address.is_reserved, address.is_multicast, address.is_unspecified))


def validate_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise WebAccessDenied("only http(s) URLs with a hostname are allowed")
    hostname = parsed.hostname.rstrip(".")
    if hostname.lower() in {"localhost", "metadata.google.internal", "instance-data.ec2.internal"}:
        raise WebAccessDenied("local and cloud metadata hosts are blocked")
    try:
        addresses = {str(item[4][0]) for item in socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise WebAccessDenied("hostname cannot be resolved") from exc
    if any(_is_blocked_ip(address) for address in addresses):
        raise WebAccessDenied("private, loopback, link-local, reserved or metadata address is blocked")


def validate_redirect(base_url: str, location: str) -> str:
    target = urljoin(base_url, location)
    validate_public_url(target)
    return target

