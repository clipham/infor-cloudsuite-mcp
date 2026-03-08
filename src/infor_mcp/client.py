"""
Infor ION API HTTP Client

Async HTTP client that wraps all interactions with the ION API Gateway.
Handles authentication headers, retry logic, rate limiting, pagination,
and transforms raw Landmark REST responses into clean formats for the LLM.
"""

import json
import logging
from typing import Any, Optional
from urllib.parse import urlencode

import httpx

from infor_mcp.auth import IONAuthManager, IONAuthError

logger = logging.getLogger("infor_mcp.client")

# Landmark REST API returns these content types
CONTENT_TYPES = {
    "json": "application/json",
    "xml": "application/xml",
    "csv": "text/csv",
}

# Maximum retries for transient failures
MAX_RETRIES = 3
RETRY_STATUSES = {429, 500, 502, 503, 504}


class IONClient:
    """
    Async HTTP client for Infor ION API Gateway.

    Wraps all API calls with:
    - Automatic OAuth token management
    - Retry with exponential backoff for transient errors
    - Rate limit handling (429 responses)
    - Response parsing and error mapping
    - Pagination support for large result sets

    Usage:
        auth = IONAuthManager("config/.ionapi")
        client = IONClient(auth, data_area="fsm")
        result = await client.get("/soap/classes/APInvoice/lists/_generic", {
            "_setName": "SymbolicKey",
            "_fields": "_all",
            "_limit": "20"
        })
    """

    def __init__(
        self,
        auth: IONAuthManager,
        data_area: str = "fsm",
        timeout: float = 60.0,
    ):
        self.auth = auth
        self.data_area = data_area
        self.timeout = timeout
        self._http: Optional[httpx.AsyncClient] = None

    async def _get_http(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=15.0),
                follow_redirects=True,
            )
        return self._http

    def _build_url(self, path: str) -> str:
        """
        Build the full URL for a Landmark REST API call.

        The URL structure is:
            {base_url}/{tenant_id}/{data_area}{path}

        Example:
            https://mingle-ionapi.region.inforcloudsuite.com/TENANT_PRD/fsm/soap/classes/APInvoice/lists/_generic
        """
        base = self.auth.base_url
        tenant = self.auth.tenant_id

        # Ensure path starts with /
        if not path.startswith("/"):
            path = f"/{path}"

        return f"{base}/{tenant}/{self.data_area}{path}"

    async def get(
        self,
        path: str,
        params: Optional[dict[str, str]] = None,
        raw: bool = False,
    ) -> str:
        """
        Make an authenticated GET request to the ION API.

        Args:
            path: API path (e.g. "/soap/classes/APInvoice/lists/_generic")
            params: Query parameters
            raw: If True, return raw response text without processing

        Returns:
            JSON string of the response, formatted for LLM consumption.

        Raises:
            IONAPIError: If the request fails after retries.
        """
        url = self._build_url(path)
        token = await self.auth.get_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": CONTENT_TYPES["json"],
        }

        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                http = await self._get_http()
                response = await http.get(url, params=params, headers=headers)

                # Handle auth expiry mid-request
                if response.status_code == 401:
                    logger.info("Token expired mid-request, re-authenticating")
                    token = await self.auth.get_token()
                    headers["Authorization"] = f"Bearer {token}"
                    continue

                # Handle rate limiting
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 2 ** attempt))
                    logger.warning(f"Rate limited, waiting {retry_after}s (attempt {attempt + 1})")
                    import asyncio
                    await asyncio.sleep(retry_after)
                    continue

                # Retry on transient server errors
                if response.status_code in RETRY_STATUSES:
                    logger.warning(
                        f"Transient error {response.status_code} (attempt {attempt + 1})"
                    )
                    import asyncio
                    await asyncio.sleep(2 ** attempt)
                    continue

                # Handle client errors
                if response.status_code >= 400:
                    return self._format_error(response)

                # Success
                if raw:
                    return response.text

                return self._format_response(response)

            except httpx.TimeoutException as e:
                last_error = e
                logger.warning(f"Request timeout (attempt {attempt + 1}): {e}")
                import asyncio
                await asyncio.sleep(2 ** attempt)
            except httpx.ConnectError as e:
                last_error = e
                logger.error(f"Connection error: {e}")
                raise IONAPIError(
                    f"Cannot connect to ION API Gateway at {self.auth.base_url}. "
                    f"Verify the base URL in your .ionapi file and network connectivity."
                ) from e

        raise IONAPIError(
            f"Request failed after {MAX_RETRIES} attempts. Last error: {last_error}"
        )

    async def post(
        self,
        path: str,
        data: Optional[dict] = None,
        params: Optional[dict[str, str]] = None,
    ) -> str:
        """
        Make an authenticated POST request to the ION API.

        Used for batch operations and future write operations.
        """
        url = self._build_url(path)
        token = await self.auth.get_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": CONTENT_TYPES["json"],
            "Content-Type": CONTENT_TYPES["json"],
        }

        http = await self._get_http()
        response = await http.post(
            url,
            json=data,
            params=params,
            headers=headers,
        )

        if response.status_code == 401:
            token = await self.auth.get_token()
            headers["Authorization"] = f"Bearer {token}"
            response = await http.post(url, json=data, params=params, headers=headers)

        if response.status_code >= 400:
            return self._format_error(response)

        return self._format_response(response)

    def _format_response(self, response: httpx.Response) -> str:
        """
        Format API response for LLM consumption.

        Cleans up Landmark's verbose JSON responses into something more
        readable and useful for the AI to reason about.
        """
        content_type = response.headers.get("content-type", "")

        if "json" in content_type or "javascript" in content_type:
            try:
                data = response.json()
                return json.dumps(data, indent=2, default=str)
            except json.JSONDecodeError:
                return response.text
        elif "xml" in content_type:
            # Return XML as-is — the LLM can parse it
            return response.text
        else:
            return response.text

    def _format_error(self, response: httpx.Response) -> str:
        """Format error responses with helpful context."""
        status = response.status_code
        body = response.text[:1000]

        error_messages = {
            400: "Bad request — check the business class name, field names, and filter syntax.",
            401: "Authentication failed — the OAuth token may have expired or the service account lacks permissions.",
            403: "Access denied — the service account doesn't have permission for this business class or action.",
            404: "Not found — the business class, action, or record doesn't exist. Use list_business_classes to discover available classes.",
            405: "Method not allowed — this operation isn't supported on this endpoint.",
            500: "Internal server error on the Infor side. The request may be malformed or the server is experiencing issues.",
        }

        hint = error_messages.get(status, "Unexpected error from ION API Gateway.")

        return json.dumps({
            "error": True,
            "status": status,
            "hint": hint,
            "detail": body,
        }, indent=2)

    async def close(self) -> None:
        """Clean up HTTP client resources."""
        if self._http and not self._http.is_closed:
            await self._http.aclose()


class IONAPIError(Exception):
    """Raised when an ION API call fails."""

    pass
