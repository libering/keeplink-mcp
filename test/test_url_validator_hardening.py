# Feature: url-validator-hardening
"""Example tests for URL validator hardening (length, userinfo, restricted hosts).

Property tests are appended below the marker at the end of this file.
"""

import socket

import pytest

from keeplink_mcp.mcp_server.url_validator import (
    MAX_URL_LENGTH,
    MSG_HOSTNAME,
    MSG_PRIVATE_HOST,
    MSG_SCHEME,
    MSG_TOO_LONG,
    MSG_USERINFO,
    ValidationError,
    _reject_private_host,
    validate_url,
)


def _url_of_length(n: int, prefix: str = "http://example.com/") -> str:
    """Build a URL whose length is exactly n using path padding."""
    return prefix + "a" * (n - len(prefix))


# ---------------------------------------------------------------------------
# Rejections: restricted IPv4 literals (Requirements 2.1, 2.2, 2.6)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "0.0.0.0",
        "224.0.0.1",
        "240.0.0.1",
    ],
)
@pytest.mark.parametrize("scheme", ["http", "https"])
def test_restricted_ipv4_rejected(ip: str, scheme: str) -> None:
    with pytest.raises(ValidationError) as exc:
        validate_url(f"{scheme}://{ip}/path")
    assert str(exc.value) == MSG_PRIVATE_HOST


# ---------------------------------------------------------------------------
# Rejections: restricted IPv6 literals (Requirements 2.3, 2.4)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "host",
    ["::1", "fe80::1", "fc00::1", "::", "ff02::1", "::ffff:127.0.0.1"],
)
def test_restricted_ipv6_rejected_by_helper(host: str) -> None:
    with pytest.raises(ValidationError) as exc:
        _reject_private_host(host)
    assert str(exc.value) == MSG_PRIVATE_HOST


def test_ipv6_loopback_still_rejected_by_validate_url() -> None:
    # Rejected by the pre-existing dot check; the end result must stay an error.
    with pytest.raises(ValidationError):
        validate_url("http://[::1]/")


def test_ipv4_mapped_ipv6_rejected_by_validate_url() -> None:
    # Contains dots, so it passes the hostname check and must hit the host check.
    with pytest.raises(ValidationError) as exc:
        validate_url("http://[::ffff:127.0.0.1]/")
    assert str(exc.value) == MSG_PRIVATE_HOST


# ---------------------------------------------------------------------------
# Rejections: localhost variants (Requirement 2.1)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "url",
    ["http://LOCALHOST./", "http://a.localhost/", "https://localhost./x"],
)
def test_localhost_variants_rejected(url: str) -> None:
    with pytest.raises(ValidationError) as exc:
        validate_url(url)
    assert str(exc.value) == MSG_PRIVATE_HOST


# ---------------------------------------------------------------------------
# Rejections: userinfo (Requirements 1.1-1.3)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "url",
    [
        "http://user:pass@example.com/",
        "https://user@example.com/",
        "http://:pass@example.com/",
    ],
)
def test_userinfo_rejected(url: str) -> None:
    with pytest.raises(ValidationError) as exc:
        validate_url(url)
    assert str(exc.value) == MSG_USERINFO


# ---------------------------------------------------------------------------
# Length boundary (Requirements 3.1-3.4)
# ---------------------------------------------------------------------------

def test_length_2048_accepted() -> None:
    url = _url_of_length(MAX_URL_LENGTH)
    assert len(url) == 2048
    assert validate_url(url) == url


def test_length_2049_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        validate_url(_url_of_length(MAX_URL_LENGTH + 1))
    assert str(exc.value) == MSG_TOO_LONG


def test_surrounding_whitespace_not_counted() -> None:
    url = _url_of_length(MAX_URL_LENGTH)
    assert validate_url("  \t" + url + " \n ") == url


# ---------------------------------------------------------------------------
# Acceptances (Requirements 2.5, 4.4-4.6)
# ---------------------------------------------------------------------------

def test_public_ip_accepted() -> None:
    assert validate_url("http://93.184.216.34/") == "http://93.184.216.34/"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/user@host",
        "https://example.com/path?email=a@b.com",
        "https://example.com/#frag@x",
        "https://example.com/page?token=abc123&x=1",
    ],
)
def test_at_sign_and_token_query_accepted(url: str) -> None:
    assert validate_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://sub.example.org/a/b?c=d",
        "https://example.co.uk:8443/x",
    ],
)
def test_existing_valid_urls_returned_unchanged(url: str) -> None:
    assert validate_url(url) == url


# ---------------------------------------------------------------------------
# Ordering and fixed messages (Requirements 3.4, 4.7, 5.1)
# ---------------------------------------------------------------------------

def test_overlong_ftp_reports_length_error_first() -> None:
    url = _url_of_length(MAX_URL_LENGTH + 1, prefix="ftp://example.com/")
    with pytest.raises(ValidationError) as exc:
        validate_url(url)
    assert str(exc.value) == MSG_TOO_LONG


def test_existing_scheme_and_hostname_messages_preserved() -> None:
    with pytest.raises(ValidationError) as exc:
        validate_url("ftp://example.com/")
    assert str(exc.value) == MSG_SCHEME
    with pytest.raises(ValidationError) as exc:
        validate_url("http://localhost/")
    assert str(exc.value) == MSG_HOSTNAME


def test_userinfo_message_does_not_leak_credentials() -> None:
    url = "https://alice:dummypw@example.com/"
    with pytest.raises(ValidationError) as exc:
        validate_url(url)
    msg = str(exc.value)
    assert "alice" not in msg
    assert "dummypw" not in msg
    assert "example.com" not in msg


def test_private_host_message_does_not_leak_hostname() -> None:
    with pytest.raises(ValidationError) as exc:
        validate_url("http://192.168.1.1/")
    assert "192.168.1.1" not in str(exc.value)


# ---------------------------------------------------------------------------
# No DNS resolution (Requirement 6.3)
# ---------------------------------------------------------------------------

def test_no_dns_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("DNS resolution must not happen")

    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    assert validate_url("https://example.com/") == "https://example.com/"
    with pytest.raises(ValidationError):
        validate_url("http://a.localhost/")
    with pytest.raises(ValidationError):
        validate_url("http://10.0.0.1/")


# ---------------------------------------------------------------------------
# Property tests are appended below (tasks 2.2, 2.3)
# ---------------------------------------------------------------------------
# --- PROPERTY TESTS BELOW ---

import ipaddress  # noqa: E402

from hypothesis import assume, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

# Independent CIDR list (NOT derived from the implementation). Mirrors the
# ranges ipaddress classifies as private/loopback/link-local/reserved/
# unspecified/multicast, including the documentation/benchmark special-use
# blocks that ipaddress marks is_private.
_RESTRICTED_CIDRS = [
    ipaddress.ip_network(c)
    for c in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.2.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",  # also covers 255.255.255.255
    )
]
# 192.0.0.0/24 (IETF protocol assignments) is mixed: ipaddress marks it
# private except 192.0.0.9/.10 (global). It is excluded from BOTH properties
# so the tests do not depend on that Python-version-specific detail.
_AMBIGUOUS_CIDRS = [ipaddress.ip_network("192.0.0.0/24")]

_path_chars = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789-_/", min_size=0, max_size=30
)
_schemes = st.sampled_from(["http", "https"])


def _in_any(addr: ipaddress.IPv4Address, nets: list) -> bool:
    return any(addr in net for net in nets)


@st.composite
def _restricted_ipv4(draw: st.DrawFn) -> ipaddress.IPv4Address:
    net = draw(st.sampled_from(_RESTRICTED_CIDRS))
    value = draw(
        st.integers(
            min_value=int(net.network_address),
            max_value=int(net.broadcast_address),
        )
    )
    return ipaddress.IPv4Address(value)


# Feature: url-validator-hardening, Property 1: 受限 IPv4 字面值一律被拒絕
# **Validates: Requirements 2.2, 2.6**
@settings(max_examples=100)
@given(scheme=_schemes, ip=_restricted_ipv4(), path=_path_chars)
def test_property_restricted_ipv4_always_rejected(
    scheme: str, ip: ipaddress.IPv4Address, path: str
) -> None:
    with pytest.raises(ValidationError) as exc:
        validate_url(f"{scheme}://{ip}/{path}")
    assert str(exc.value) == MSG_PRIVATE_HOST


# Feature: url-validator-hardening, Property 2: 公網 IPv4 字面值通過
# **Validates: Requirements 2.5, 4.4**
@settings(max_examples=100)
@given(
    scheme=_schemes,
    # Octet-wise generation: first octet 1..223 avoids 0/8, multicast and
    # reserved space up front, so assume() below discards only a small share.
    octets=st.tuples(
        st.integers(min_value=1, max_value=223),
        st.integers(min_value=0, max_value=255),
        st.integers(min_value=0, max_value=255),
        st.integers(min_value=0, max_value=255),
    ),
    path=_path_chars,
    pad=st.sampled_from(["", " ", "\t", "\n  "]),
)
def test_property_public_ipv4_accepted(
    scheme: str, octets: tuple[int, int, int, int], path: str, pad: str
) -> None:
    ip = ipaddress.IPv4Address(bytes(octets))
    assume(not _in_any(ip, _RESTRICTED_CIDRS + _AMBIGUOUS_CIDRS))
    url = f"{scheme}://{ip}/{path}"
    assert validate_url(pad + url + pad) == url


# ---------------------------------------------------------------------------
# Properties 3-5 (task 2.3)
# ---------------------------------------------------------------------------

_FIXED_MESSAGES = {
    MSG_TOO_LONG,
    MSG_SCHEME,
    MSG_HOSTNAME,
    MSG_USERINFO,
    MSG_PRIVATE_HOST,
}

_public_hosts = st.one_of(
    st.sampled_from(["example.com", "sub.example.org", "www.python.net"]),
    st.from_regex(r"[a-z]{3,10}\.(com|org|net)", fullmatch=True),
)

# Excludes / ? # @ : and whitespace by construction (alphanumerics + "-_.~").
_cred_text = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.~",
    min_size=4,
    max_size=20,
).filter(lambda s: all(s not in m for m in _FIXED_MESSAGES))

_whitespace = st.text(alphabet=" \t\n", min_size=0, max_size=5)


# Feature: url-validator-hardening, Property 3: 帶 userinfo 的 URL 被拒絕且訊息不洩漏帳密
# **Validates: Requirements 1.1, 1.2, 1.3, 5.1**
@settings(max_examples=100)
@given(
    scheme=_schemes,
    host=_public_hosts,
    username=_cred_text,
    password=st.one_of(st.none(), st.just(""), _cred_text),
    path=_path_chars,
)
def test_property_userinfo_rejected_without_leak(
    scheme: str, host: str, username: str, password: str | None, path: str
) -> None:
    userinfo = username if password is None else f"{username}:{password}"
    url = f"{scheme}://{userinfo}@{host}/{path}"
    with pytest.raises(ValidationError) as exc:
        validate_url(url)
    msg = str(exc.value)
    assert msg == MSG_USERINFO
    assert username not in msg
    if password:
        assert password not in msg
    assert url not in msg


# Feature: url-validator-hardening, Property 4: 長度邊界
# **Validates: Requirements 3.1, 3.2, 3.3, 3.4**
@settings(max_examples=100)
@given(
    n=st.one_of(
        st.integers(min_value=2000, max_value=2100),
        st.sampled_from([2047, 2048, 2049]),
    ),
    scheme=_schemes,
    lead=_whitespace,
    trail=_whitespace,
)
def test_property_length_boundary(
    n: int, scheme: str, lead: str, trail: str
) -> None:
    url = _url_of_length(n, prefix=f"{scheme}://example.com/")
    assert len(url) == n
    padded = lead + url + trail
    if n <= MAX_URL_LENGTH:
        assert validate_url(padded) == url
    else:
        with pytest.raises(ValidationError) as exc:
            validate_url(padded)
        assert str(exc.value) == MSG_TOO_LONG


@st.composite
def _rejected_input(draw: st.DrawFn) -> tuple[str, str]:
    """Return (url, expected fixed message) for a rejected input category."""
    kind = draw(
        st.sampled_from(["long", "scheme", "hostname", "userinfo", "ip", "local"])
    )
    host = draw(_public_hosts)
    path = draw(_path_chars)
    if kind == "long":
        extra = draw(st.integers(min_value=1, max_value=50))
        scheme = draw(st.sampled_from(["http", "https", "ftp", "file"]))
        return _url_of_length(MAX_URL_LENGTH + extra, f"{scheme}://{host}/"), MSG_TOO_LONG
    if kind == "scheme":
        scheme = draw(st.sampled_from(["ftp", "file", "javascript", "ws"]))
        return f"{scheme}://{host}/{path}", MSG_SCHEME
    if kind == "hostname":
        label = draw(st.from_regex(r"[a-z]{3,10}", fullmatch=True))
        return f"{draw(_schemes)}://{label}/{path}", MSG_HOSTNAME
    if kind == "userinfo":
        user = draw(_cred_text)
        return f"{draw(_schemes)}://{user}@{host}/{path}", MSG_USERINFO
    if kind == "ip":
        return f"{draw(_schemes)}://{draw(_restricted_ipv4())}/{path}", MSG_PRIVATE_HOST
    sub = draw(st.sampled_from(["", "a.", "x1."]))
    return f"{draw(_schemes)}://{sub}localhost/{path}" if sub else (
        f"{draw(_schemes)}://localhost./{path}"
    ), MSG_PRIVATE_HOST


# Feature: url-validator-hardening, Property 5: 所有拒絕訊息皆為固定字串
# **Validates: Requirements 4.7, 5.1**
@settings(max_examples=100)
@given(case=_rejected_input())
def test_property_rejection_messages_are_fixed(case: tuple[str, str]) -> None:
    url, expected = case
    with pytest.raises(ValidationError) as exc:
        validate_url(url)
    msg = str(exc.value)
    assert msg in _FIXED_MESSAGES
    assert msg == expected
