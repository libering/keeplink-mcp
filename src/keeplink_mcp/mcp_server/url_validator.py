"""URL validation and normalization for archive requests.

Rejects URLs with invalid schemes or malformed hostnames at the earliest stage,
following fail-fast principles to avoid wasting queue resources.
"""

from urllib.parse import urlparse


class ValidationError(ValueError):
    """URL validation failure with descriptive message."""


def validate_url(raw: str) -> str:
    """Validate and normalize a URL.

    Performs three checks in order:
    1. Strip surrounding whitespace
    2. Verify scheme is http or https
    3. Verify hostname is non-empty and contains at least one dot

    Args:
        raw: Raw URL string from user input.

    Returns:
        Normalized URL string (stripped, scheme-verified, host-verified).

    Raises:
        ValidationError: If URL scheme is not http/https or hostname is malformed.
    """
    url = raw.strip()

    parsed = urlparse(url)

    # Scheme check — only http and https are acceptable
    if parsed.scheme not in ("http", "https"):
        raise ValidationError("scheme must be http or https")

    # Hostname check — must be non-empty and contain at least one dot
    hostname = parsed.hostname or ""
    if not hostname or "." not in hostname:
        raise ValidationError("malformed hostname")

    return url
