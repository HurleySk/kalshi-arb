import tempfile
import os
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import serialization, hashes
from src.auth import KalshiAuth


def _generate_test_key() -> tuple[str, rsa.RSAPrivateKey]:
    """Generate a temporary RSA key pair for testing."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    fd, path = tempfile.mkstemp(suffix=".pem")
    os.write(fd, pem)
    os.close(fd)
    return path, private_key


def test_auth_headers_have_required_keys():
    path, _ = _generate_test_key()
    auth = KalshiAuth(api_key_id="test-key", private_key_path=path)
    headers = auth.build_headers("GET", "/trade-api/v2/events")
    os.unlink(path)

    assert "KALSHI-ACCESS-KEY" in headers
    assert "KALSHI-ACCESS-TIMESTAMP" in headers
    assert "KALSHI-ACCESS-SIGNATURE" in headers
    assert headers["KALSHI-ACCESS-KEY"] == "test-key"


def test_auth_signature_is_verifiable():
    path, private_key = _generate_test_key()
    auth = KalshiAuth(api_key_id="test-key", private_key_path=path)
    headers = auth.build_headers("GET", "/trade-api/v2/events")
    os.unlink(path)

    import base64
    timestamp = headers["KALSHI-ACCESS-TIMESTAMP"]
    sig_bytes = base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])
    msg = f"{timestamp}GET/trade-api/v2/events".encode("utf-8")

    # Verify using the public key — should not raise
    public_key = private_key.public_key()
    public_key.verify(
        sig_bytes,
        msg,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )


def test_auth_strips_query_params_from_signature():
    path, private_key = _generate_test_key()
    auth = KalshiAuth(api_key_id="test-key", private_key_path=path)
    headers = auth.build_headers("GET", "/trade-api/v2/events?limit=10&cursor=abc")
    os.unlink(path)

    import base64
    timestamp = headers["KALSHI-ACCESS-TIMESTAMP"]
    sig_bytes = base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])
    # The signed path should NOT include query params
    msg = f"{timestamp}GET/trade-api/v2/events".encode("utf-8")

    public_key = private_key.public_key()
    public_key.verify(
        sig_bytes,
        msg,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
