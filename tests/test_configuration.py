import pytest

from app.core.config import Settings, validate_settings


def test_production_refuses_development_authentication():
    with pytest.raises(RuntimeError, match="Production must use OIDC"):
        validate_settings(
            Settings(app_env="production", auth_mode="development_headers", allow_dev_auth=True)
        )


def test_production_oidc_configuration_is_accepted():
    validate_settings(
        Settings(
            app_env="production",
            auth_mode="oidc",
            allow_dev_auth=False,
            oidc_jwks_url="http://keycloak/realms/market-assistant/certs",
        )
    )


def test_unknown_auth_mode_and_wildcard_cors_are_rejected():
    with pytest.raises(RuntimeError, match="AUTH_MODE"):
        validate_settings(Settings(auth_mode="trusted_headers"))
    with pytest.raises(RuntimeError, match="CORS"):
        validate_settings(Settings(cors_origins="*"))


def test_http_connector_requires_explicit_base_url():
    with pytest.raises(RuntimeError, match="BUSINESS_CONNECTOR_BASE_URL"):
        validate_settings(Settings(business_connector_mode="http", business_connector_base_url=""))
