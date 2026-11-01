from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt import PyJWKClientError

from app.core.config import settings
from app.core.security import OidcTokenValidator


@pytest.fixture
def signing_material():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, private.public_key()


def token(private, **overrides):
    now = datetime.now(UTC)
    claims = {
        "sub": "subject-1",
        "tenant_id": "tenant_demo",
        "iss": settings.oidc_issuer,
        "aud": settings.oidc_audience,
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    claims.update(overrides)
    return jwt.encode(claims, private, algorithm="RS256", headers={"kid": "test-key"})


def validator_for(public_key):
    validator = OidcTokenValidator()
    validator._jwks_client = SimpleNamespace(
        get_signing_key_from_jwt=lambda _token: SimpleNamespace(key=public_key)
    )
    return validator


def test_valid_rs256_token_is_accepted(signing_material):
    private, public = signing_material
    assert validator_for(public).validate(token(private))["sub"] == "subject-1"


@pytest.mark.parametrize(
    ("override", "error"),
    [
        ({"iss": "https://evil.invalid/realms/fake"}, jwt.InvalidIssuerError),
        ({"aud": "other-api"}, jwt.InvalidAudienceError),
        ({"exp": datetime.now(UTC) - timedelta(seconds=1)}, jwt.ExpiredSignatureError),
    ],
)
def test_invalid_standard_claims_are_rejected(signing_material, override, error):
    private, public = signing_material
    with pytest.raises(error):
        validator_for(public).validate(token(private, **override))


def test_wrong_signature_is_rejected(signing_material):
    private, _public = signing_material
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(jwt.InvalidSignatureError):
        validator_for(other.public_key()).validate(token(private))


def test_unknown_kid_refreshes_jwks_once(signing_material, monkeypatch):
    private, public = signing_material
    validator = OidcTokenValidator()
    clients = [
        SimpleNamespace(
            get_signing_key_from_jwt=lambda _token: (_ for _ in ()).throw(
                PyJWKClientError("unknown kid")
            )
        ),
        SimpleNamespace(
            get_signing_key_from_jwt=lambda _token: SimpleNamespace(key=public)
        ),
    ]
    monkeypatch.setattr(validator, "_client", lambda: clients.pop(0))
    assert validator.validate(token(private))["tenant_id"] == "tenant_demo"
