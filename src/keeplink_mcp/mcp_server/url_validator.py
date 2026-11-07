"""URL validation and normalization for archive requests.

Rejects URLs with invalid schemes or malformed hostnames at the earliest stage,
following fail-fast principles to avoid wasting queue resources.
"""

import ipaddress
from urllib.parse import ParseResult, urlparse

MAX_URL_LENGTH = 2048

# Fixed messages: never interpolate input, so credentials cannot leak into
# API responses or logs.
MSG_TOO_LONG = f"URL too long (max {MAX_URL_LENGTH} characters)"
MSG_SCHEME = "scheme must be http or https"
MSG_HOSTNAME = "malformed hostname"
MSG_USERINFO = "credentials not allowed in URL"
MSG_PRIVATE_HOST = "private or local host not allowed"


class ValidationError(ValueError):
    """URL validation failure with descriptive message."""


def _reject_too_long(url: str) -> None:
    """Raise ValidationError(MSG_TOO_LONG) if the stripped URL exceeds MAX_URL_LENGTH.

    Args:
        url: Already-stripped URL string.
    """
    if len(url) > MAX_URL_LENGTH:
        raise ValidationError(MSG_TOO_LONG)


def _reject_userinfo(parsed: ParseResult) -> None:
    """Raise ValidationError(MSG_USERINFO) if the URL carries username or password.

    Args:
        parsed: Result of urlparse; only netloc userinfo is considered, so '@'
            in path, query or fragment is not rejected.
    """
    if parsed.username is not None or parsed.password is not None:
        raise ValidationError(MSG_USERINFO)


def _reject_private_host(hostname: str) -> None:
    """Raise ValidationError(MSG_PRIVATE_HOST) for localhost or restricted IP literals.

    Pure string parsing; performs no DNS resolution or network I/O.

    Args:
        hostname: parsed.hostname (lowercased, IPv6 brackets removed).
    """
    # Trailing dot ("localhost.") is a valid FQDN and would otherwise bypass the check.
    host = hostname.rstrip(".")
    if host == "localhost" or host.endswith(".localhost"):
        raise ValidationError(MSG_PRIVATE_HOST)
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return  # Ordinary domain name; no DNS resolution by design.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if _is_restricted_ip(ip):
        raise ValidationError(MSG_PRIVATE_HOST)


def _is_restricted_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True if ip is loopback, private, link-local, reserved, unspecified or multicast.

    Args:
        ip: Parsed IP address (IPv4-mapped IPv6 should be unwrapped by the caller).
    """
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_unspecified
        or ip.is_multicast
    )


def validate_url(raw: str) -> str:
    """Validate and normalize a URL.

    Performs checks in order (fail-fast):
    1. Strip surrounding whitespace
    2. Reject URLs longer than MAX_URL_LENGTH
    3. Verify scheme is http or https
    4. Verify hostname is non-empty and contains at least one dot
    5. Reject userinfo (username/password)
    6. Reject localhost and restricted IP literals

    Args:
        raw: Raw URL string from user input.

    Returns:
        Normalized URL string (stripped, scheme-verified, host-verified).

    Raises:
        ValidationError: If URL scheme is not http/https or hostname is malformed.
    """
    url = raw.strip()
    _reject_too_long(url)

    parsed = urlparse(url)

    # Scheme check — only http and https are acceptable
    if parsed.scheme not in ("http", "https"):
        raise ValidationError(MSG_SCHEME)

    # Hostname check — must be non-empty and contain at least one dot
    hostname = parsed.hostname or ""
    if not hostname or "." not in hostname:
        raise ValidationError(MSG_HOSTNAME)

    _reject_userinfo(parsed)
    _reject_private_host(hostname)
    return url
