from __future__ import annotations

import os
import time
from typing import Any

import requests
from dotenv import load_dotenv


def _normalize_base_url(base_url: str) -> str:
    url = base_url.rstrip("/")
    if url == "https://api2.warera.io":
        return f"{url}/trpc"
    return url


class WarEraApiClient:
    """Small API client wired for X-Api-Key authentication.

    Endpoint names are intentionally configurable because WarEra/Arcana endpoint
    shapes may change or differ from public docs.
    """

    def __init__(self, base_url: str | None = None, api_key: str | None = None, min_interval_seconds: float = 1.0):
        load_dotenv()
        self.base_url = _normalize_base_url(base_url or os.getenv("WARERA_API_BASE_URL") or "https://api2.warera.io/trpc")
        self.api_key = api_key or os.getenv("WARERA_API_KEY")
        if not self.api_key:
            raise RuntimeError("Missing WARERA_API_KEY. Set it as an environment variable or in .env.")
        self.min_interval_seconds = min_interval_seconds
        self._last_request = 0.0
        self.session = requests.Session()
        self.session.headers.update({"X-Api-Key": self.api_key})

    def get_json(self, endpoint: str, *, params: dict[str, Any] | None = None) -> Any:
        return self.request_json("GET", endpoint, params=params)

    def request_json(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.min_interval_seconds:
            time.sleep(self.min_interval_seconds - elapsed)

        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        response = self.session.request(method, url, params=params, json=json_body, timeout=30)
        self._last_request = time.monotonic()
        response.raise_for_status()
        return response.json()
