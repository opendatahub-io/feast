import logging
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)


class FeastClient:
    """HTTP client that proxies requests to the Feast feature server."""

    def __init__(self, base_url: str, timeout: float = 30.0):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def _headers(self, token: Optional[str] = None) -> Dict[str, str]:
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def request(
        self,
        method: str,
        path: str,
        *,
        token: Optional[str] = None,
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, str]] = None,
    ) -> Any:
        url = f"{self._base_url}{path}"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.request(
                method,
                url,
                headers=self._headers(token),
                json=json,
                params=params,
            )
            if response.status_code >= 500:
                response.raise_for_status()
            if response.headers.get("content-type", "").startswith("application/json"):
                return response.json()
            return response.text
