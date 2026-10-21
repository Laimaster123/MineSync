import pytest

from blocksync.net.auth import MAX_REQUEST_AGE_SECONDS, AuthError, RequestVerifier, sign_request
from blocksync.net.protocol import HEADER_DEVICE, HEADER_SIGNATURE

KEY = b"k" * 32
NOW = 1_700_000_000.0


def make_verifier() -> RequestVerifier:
    return RequestVerifier(lambda device: KEY if device == "mac" else None, clock=lambda: NOW)


def test_valid_request_passes() -> None:
    headers = sign_request("mac", KEY, "GET", "/api/v1/worlds", now=NOW)
    assert make_verifier().verify("GET", "/api/v1/worlds", headers, b"") == "mac"


def test_encoded_and_plain_path_give_same_signature() -> None:
    headers = sign_request("mac", KEY, "GET", "/api/v1/worlds/Мой мир/manifest", now=NOW)
    encoded = "/api/v1/worlds/%D0%9C%D0%BE%D0%B9%20%D0%BC%D0%B8%D1%80/manifest"
    assert make_verifier().verify("GET", encoded, headers, b"") == "mac"


def test_missing_headers_rejected() -> None:
    with pytest.raises(AuthError):
        make_verifier().verify("GET", "/api/v1/worlds", {}, b"")


def test_unknown_device_rejected() -> None:
    headers = sign_request("stranger", KEY, "GET", "/api/v1/worlds", now=NOW)
    with pytest.raises(AuthError, match="не сопряжено"):
        make_verifier().verify("GET", "/api/v1/worlds", headers, b"")


def test_wrong_key_rejected() -> None:
    headers = sign_request("mac", b"x" * 32, "GET", "/api/v1/worlds", now=NOW)
    with pytest.raises(AuthError, match="подпись"):
        make_verifier().verify("GET", "/api/v1/worlds", headers, b"")


@pytest.mark.parametrize(
    "method, path, body",
    [("POST", "/api/v1/worlds", b""), ("GET", "/api/v1/other", b""), ("GET", "/api/v1/worlds", b"x")],
)
def test_changed_request_rejected(method: str, path: str, body: bytes) -> None:
    headers = sign_request("mac", KEY, "GET", "/api/v1/worlds", now=NOW)
    with pytest.raises(AuthError):
        make_verifier().verify(method, path, headers, body)


def test_forged_device_header_rejected() -> None:
    headers = sign_request("mac", KEY, "GET", "/api/v1/worlds", now=NOW)
    headers[HEADER_DEVICE] = "pc"
    verifier = RequestVerifier(lambda device: KEY, clock=lambda: NOW)
    with pytest.raises(AuthError):
        verifier.verify("GET", "/api/v1/worlds", headers, b"")


def test_bad_signature_rejected() -> None:
    headers = sign_request("mac", KEY, "GET", "/api/v1/worlds", now=NOW)
    headers[HEADER_SIGNATURE] = "0" * 64
    with pytest.raises(AuthError):
        make_verifier().verify("GET", "/api/v1/worlds", headers, b"")


def test_replayed_request_rejected() -> None:
    verifier = make_verifier()
    headers = sign_request("mac", KEY, "GET", "/api/v1/worlds", now=NOW)
    verifier.verify("GET", "/api/v1/worlds", headers, b"")
    with pytest.raises(AuthError, match="повторный"):
        verifier.verify("GET", "/api/v1/worlds", headers, b"")


def test_old_request_rejected() -> None:
    headers = sign_request("mac", KEY, "GET", "/api/v1/worlds", now=NOW - MAX_REQUEST_AGE_SECONDS - 1)
    with pytest.raises(AuthError, match="устарел"):
        make_verifier().verify("GET", "/api/v1/worlds", headers, b"")
