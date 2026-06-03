"""OIDC adapter — defers full SSO configuration to the operator.

This module provides the interface and a stub implementation.
Wire a real OIDC provider (e.g. Keycloak) by setting:
  OIDC_ISSUER, OIDC_CLIENT_ID, OIDC_CLIENT_SECRET

When those settings are empty, the OIDC flow returns NotImplementedError
so local-account auth remains the only path.  This is safe for Phase 0.

Phase 1+ will flesh out the full discovery + token exchange flow using Authlib.
"""

from wolf_server.config import get_settings

_settings = get_settings()


def oidc_is_configured() -> bool:
    """Return True if the operator has configured an OIDC provider."""
    return bool(_settings.oidc_issuer and _settings.oidc_client_id and _settings.oidc_client_secret)


def get_authorization_url(redirect_uri: str, state: str) -> str:
    """Return the IdP authorization URL for the OIDC flow.

    Raises NotImplementedError when OIDC is not configured.
    """
    if not oidc_is_configured():
        msg = "OIDC is not configured. Set OIDC_ISSUER, OIDC_CLIENT_ID, OIDC_CLIENT_SECRET."
        raise NotImplementedError(msg)
    # Phase 1+: use Authlib to build the authorization URL.
    # from authlib.integrations.httpx_client import AsyncOAuth2Client
    # ...
    raise NotImplementedError("OIDC authorization URL not yet implemented")


async def exchange_code(code: str, redirect_uri: str) -> dict[str, object]:
    """Exchange an authorization code for tokens.

    Raises NotImplementedError when OIDC is not configured.
    Phase 1+: implement the token exchange and user-info lookup.
    """
    if not oidc_is_configured():
        msg = "OIDC is not configured."
        raise NotImplementedError(msg)
    raise NotImplementedError("OIDC token exchange not yet implemented")
