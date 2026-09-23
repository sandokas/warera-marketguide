from __future__ import annotations

import os
from decimal import Decimal
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
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        # Only retry reads; writes must never be replayed implicitly.
        attempts = 4 if method.upper() == "GET" else 1
        for attempt in range(attempts):
            elapsed = time.monotonic() - self._last_request
            if elapsed < self.min_interval_seconds:
                time.sleep(self.min_interval_seconds - elapsed)
            response = None
            try:
                response = self.session.request(method, url, params=params, json=json_body, timeout=30)
                response.raise_for_status()
                return response.json(parse_float=Decimal)
            except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
                status = response.status_code if response is not None else None
                transient = isinstance(exc, (requests.Timeout, requests.ConnectionError)) or status in (429, 500, 502, 503, 504)
                if not transient or attempt + 1 == attempts:
                    raise
                delay = min(30.0, 2.0 ** attempt)
                if response is not None:
                    try:
                        delay = min(30.0, max(delay, float(response.headers.get("Retry-After", 0))))
                    except (TypeError, ValueError):
                        pass
                time.sleep(delay)
            finally:
                self._last_request = time.monotonic()
