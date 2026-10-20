"""Verify actual certificate assertions, not just configuration flags."""

import json
from base64 import urlsafe_b64encode
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs

import httpx
import jwt
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from backend.identity import LOCAL_ORG
from backend.mailbox.graph import GraphClient, GraphError, configured, credential_fields

TENANT = "11111111-1111-4111-8111-111111111111"
CLIENT = "22222222-2222-4222-8222-222222222222"
TOKEN_URL = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token"


@pytest.fixture
def certificate(tmp_path, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test only")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "public.pem", tmp_path / "private.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    monkeypatch.setenv("RIVET_MS_CERTIFICATE_PATH", str(cert_path))
    monkeypatch.setenv("RIVET_MS_PRIVATE_KEY_PATH", str(key_path))
    monkeypatch.setenv("RIVET_MS_TENANT_ID", TENANT)
    monkeypatch.setenv("RIVET_MS_CLIENT_ID", CLIENT)
    monkeypatch.setenv(
        "RIVET_MS_MAILBOX_ALLOWLIST", json.dumps({LOCAL_ORG: ["pm@example.com"]})
    )
    monkeypatch.delenv("RIVET_MS_CLIENT_SECRET", raising=False)
    return cert, key_path, cert_path


def test_graph_sends_verifiable_assertion_and_caches_token(certificate):
    cert, _, _ = certificate
    calls = []

    def handle(request):
        assert str(request.url) == TOKEN_URL
        form = parse_qs(request.content.decode())
        assert "client_secret" not in form
        assert form["client_id"] == [CLIENT]
        assert form["scope"] == ["https://graph.microsoft.com/.default"]
        assert form["client_assertion_type"] == [
            "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
        ]
        assertion = form["client_assertion"][0]
        claims = jwt.decode(
            assertion, cert.public_key(), algorithms=["PS256"], audience=TOKEN_URL
        )
        assert claims["iss"] == claims["sub"] == CLIENT
        assert claims["exp"] - claims["iat"] == 300
        assert claims["nbf"] == claims["iat"]
        header = jwt.get_unverified_header(assertion)
        assert (
            header["x5t#S256"]
            == urlsafe_b64encode(cert.fingerprint(hashes.SHA256()))
            .rstrip(b"=")
            .decode()
        )
        calls.append(claims["jti"])
        return httpx.Response(
            200, json={"access_token": "test-only-token", "expires_in": 3600}
        )

    assert configured()
    client = GraphClient(
        LOCAL_ORG, "pm@example.com", TENANT, transport=httpx.MockTransport(handle)
    )
    try:
        assert client.headers()["Authorization"] == "Bearer test-only-token"
        client.headers()
        assert len(calls) == 1
        client.expires_at = 0
        client.headers()
        assert len(set(calls)) == 2
    finally:
        client.close()


@pytest.mark.parametrize(
    "failure", ["missing_path", "missing_file", "corrupt", "mismatched", "expired"]
)
def test_bad_certificate_fails_closed_without_secret_fallback(
    certificate, monkeypatch, failure
):
    cert, key_path, cert_path = certificate
    monkeypatch.setenv("RIVET_MS_CLIENT_SECRET", "must-not-be-used-or-logged")
    if failure == "missing_path":
        monkeypatch.delenv("RIVET_MS_PRIVATE_KEY_PATH")
        assert not configured()
    elif failure == "missing_file":
        key_path.unlink()
    elif failure == "corrupt":
        key_path.write_text("private material must stay out of errors")
    else:
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        if failure == "mismatched":
            key_path.write_bytes(
                other.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )
        else:
            key = serialization.load_pem_private_key(
                key_path.read_bytes(), password=None
            )
            old = (
                x509.CertificateBuilder()
                .subject_name(cert.subject)
                .issuer_name(cert.issuer)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(datetime.now(UTC) - timedelta(days=2))
                .not_valid_after(datetime.now(UTC) - timedelta(days=1))
                .sign(key, hashes.SHA256())
            )
            cert_path.write_bytes(old.public_bytes(serialization.Encoding.PEM))
    with pytest.raises(GraphError) as exc:
        credential_fields(CLIENT, TOKEN_URL)
    assert exc.value.code == "setup_required"
    assert (
        str(exc.value)
        == "Microsoft application certificate is missing, invalid, or expired."
    )


def test_secret_mode_remains_available_only_without_certificate_paths(monkeypatch):
    monkeypatch.delenv("RIVET_MS_CERTIFICATE_PATH", raising=False)
    monkeypatch.delenv("RIVET_MS_PRIVATE_KEY_PATH", raising=False)
    monkeypatch.setenv("RIVET_MS_CLIENT_SECRET", "test-secret")
    assert credential_fields(CLIENT, TOKEN_URL) == {"client_secret": "test-secret"}
