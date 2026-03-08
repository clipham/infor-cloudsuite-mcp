"""
Infor ION API Gateway OAuth 2.0 Token Manager

Handles the full authentication lifecycle for backend service applications:
- Parses .ionapi credentials files downloaded from ION API Gateway
- Authenticates using Resource Owner grant with service account credentials
- Manages token refresh and re-authentication on expiry
- Thread-safe token access for concurrent MCP tool calls

The .ionapi file is downloaded from:
  Infor OS > ION API > Authorized Apps > [Your App] > Download Credentials

It contains all OAuth endpoints, client credentials, and service account keys.
"""

import json
import time
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

import httpx

logger = logging.getLogger("infor_mcp.auth")


@dataclass
class IONAPIConfig:
    """Parsed .ionapi credentials file."""

    tenant_id: str  # ti
    connection_name: str  # cn
    device_type: str  # dt
    client_id: str  # ci
    client_secret: str  # cs
    base_url: str  # iu - ION API base URL
    auth_server_url: str  # pu - SSO/auth server base
    auth_endpoint: str  # oa - authorization.oauth2
    token_endpoint: str  # ot - token.oauth2
    revoke_endpoint: str  # or - revoke_token.oauth2
    service_account_access_key: str  # saak
    service_account_secret_key: str  # sask
    event_url: Optional[str] = None  # ev

    @classmethod
    def from_file(cls, path: str | Path) -> "IONAPIConfig":
        """Parse a .ionapi credentials JSON file."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(
                f"ION API credentials file not found: {path}\n"
                "Download from: Infor OS > ION API > Authorized Apps > Download Credentials"
            )

        with open(path) as f:
            data = json.load(f)

        required_keys = ["ti", "ci", "cs", "iu", "pu", "ot", "saak", "sask"]
        missing = [k for k in required_keys if k not in data]
        if missing:
            raise ValueError(
                f"Invalid .ionapi file - missing keys: {missing}. "
                "Ensure you downloaded credentials for a Backend Service type app."
            )

        return cls(
            tenant_id=data["ti"],
            connection_name=data.get("cn", ""),
            device_type=data.get("dt", ""),
            client_id=data["ci"],
            client_secret=data["cs"],
            base_url=data["iu"].rstrip("/"),
            auth_server_url=data["pu"].rstrip("/"),
            auth_endpoint=data.get("oa", "authorization.oauth2"),
            token_endpoint=data["ot"],
            revoke_endpoint=data.get("or", "revoke_token.oauth2"),
            service_account_access_key=data["saak"],
            service_account_secret_key=data["sask"],
            event_url=data.get("ev"),
        )

    @property
    def token_url(self) -> str:
        """Full URL for token requests."""
        return f"{self.auth_server_url}/{self.token_endpoint}"

    @property
    def revoke_url(self) -> str:
        """Full URL for token revocation."""
        return f"{self.auth_server_url}/{self.revoke_endpoint}"


@dataclass
class TokenState:
    """Current OAuth token state."""

    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    expires_at: float = 0.0
    token_type: str = "Bearer"

    @property
    def is_valid(self) -> bool:
        """Check if access token is still valid (with 60s buffer)."""
        return self.access_token is not None and time.time() < (self.expires_at - 60)

    @property
    def has_refresh(self) -> bool:
        """Check if we have a refresh token available."""
        return self.refresh_token is not None


class IONAuthManager:
    """
    Manages OAuth 2.0 authentication against the Infor ION API Gateway.

    Usage:
        auth = IONAuthManager("config/.ionapi")
        token = await auth.get_token()
        headers = auth.get_auth_headers()
    """

    def __init__(self, ionapi_path: str | Path):
        self.config = IONAPIConfig.from_file(ionapi_path)
        self._token = TokenState()
        self._http: Optional[httpx.AsyncClient] = None

    @property
    def base_url(self) -> str:
        """ION API base URL for making API calls."""
        return self.config.base_url

    @property
    def tenant_id(self) -> str:
        """Tenant identifier."""
        return self.config.tenant_id

    async def _get_http(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=10.0),
                follow_redirects=True,
            )
        return self._http

    async def get_token(self) -> str:
        """
        Get a valid access token, refreshing or re-authenticating as needed.

        Returns:
            Valid Bearer access token string.

        Raises:
            httpx.HTTPStatusError: If authentication fails.
            IONAuthError: If unable to obtain a token.
        """
        # Return existing token if still valid
        if self._token.is_valid:
            return self._token.access_token

        # Try refresh first if we have a refresh token
        if self._token.has_refresh:
            try:
                return await self._refresh_token()
            except Exception as e:
                logger.warning(f"Token refresh failed, re-authenticating: {e}")

        # Full authentication
        return await self._authenticate()

    def get_auth_headers(self) -> dict[str, str]:
        """
        Get authorization headers for API requests.

        Note: Call get_token() first to ensure token is valid.
        """
        if not self._token.access_token:
            raise RuntimeError("No access token available. Call get_token() first.")
        return {
            "Authorization": f"{self._token.token_type} {self._token.access_token}",
        }

    async def _authenticate(self) -> str:
        """
        Authenticate using Resource Owner grant with service account credentials.

        This is the primary auth flow for backend services. Uses the saak/sask
        from the .ionapi file as username/password with the OAuth client credentials.
        """
        logger.info(f"Authenticating to ION API Gateway (tenant: {self.config.tenant_id})")

        http = await self._get_http()
        response = await http.post(
            self.config.token_url,
            data={
                "grant_type": "password",
                "username": self.config.service_account_access_key,
                "password": self.config.service_account_secret_key,
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        if response.status_code != 200:
            error_detail = response.text[:500]
            logger.error(f"Authentication failed ({response.status_code}): {error_detail}")
            raise IONAuthError(
                f"ION API authentication failed with status {response.status_code}. "
                f"Verify your .ionapi credentials and service account are active. "
                f"Detail: {error_detail}"
            )

        data = response.json()
        self._update_token(data)
        logger.info("Successfully authenticated to ION API Gateway")
        return self._token.access_token

    async def _refresh_token(self) -> str:
        """Refresh the access token using the refresh token."""
        logger.debug("Refreshing ION API access token")

        http = await self._get_http()
        response = await http.post(
            self.config.token_url,
            data={
                "grant_type": "refresh_token",
                "refresh_token": self._token.refresh_token,
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        if response.status_code != 200:
            logger.warning(f"Token refresh failed ({response.status_code})")
            # Clear refresh token so we fall back to full auth
            self._token.refresh_token = None
            raise IONAuthError("Token refresh failed")

        data = response.json()
        self._update_token(data)
        logger.debug("Successfully refreshed access token")
        return self._token.access_token

    def _update_token(self, data: dict) -> None:
        """Update internal token state from OAuth response."""
        self._token.access_token = data["access_token"]
        self._token.refresh_token = data.get("refresh_token", self._token.refresh_token)
        self._token.token_type = data.get("token_type", "Bearer")
        self._token.expires_at = time.time() + data.get("expires_in", 7200)

    async def revoke(self) -> None:
        """Revoke the current tokens. Call on shutdown to prevent orphan grants."""
        if not self._token.refresh_token:
            return

        try:
            http = await self._get_http()
            await http.post(
                self.config.revoke_url,
                data={
                    "token": self._token.refresh_token,
                    "token_type_hint": "refresh_token",
                },
                auth=(self.config.client_id, self.config.client_secret),
            )
            logger.info("Revoked ION API tokens")
        except Exception as e:
            logger.warning(f"Token revocation failed (non-critical): {e}")
        finally:
            self._token = TokenState()

    async def close(self) -> None:
        """Clean up resources. Call on server shutdown."""
        await self.revoke()
        if self._http and not self._http.is_closed:
            await self._http.aclose()


class IONAuthError(Exception):
    """Raised when ION API authentication fails."""

    pass
