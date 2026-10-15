"""Low-level Coinos HTTP requests."""

import asyncio
import json
import urllib.error
import urllib.request

from settings import COINOS_TOKEN, COINOS_API_BASE


class CoinosNotFound(Exception):
    pass


class CoinosHTTPError(RuntimeError):
    """Keep Coinos response details out of logs while preserving routing diagnosis."""

    def __init__(self, status_code, response_body=""):
        self.status_code = status_code
        self.response_body = response_body
        super().__init__(f"Coinos HTTP error: {status_code}")



def _coinos_request_sync(method, path, payload=None):
    if not COINOS_TOKEN:
        raise RuntimeError("COINOS_TOKEN is not configured")

    body = None
    headers = {
        "Authorization": f"Bearer {COINOS_TOKEN}",
        "Accept": "application/json",
    }
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(
        f"{COINOS_API_BASE}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        timeout = 60 if method == "POST" and path == "/payments" else 15
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise CoinosNotFound() from None
        try:
            error_body = exc.read(4096).decode("utf-8", errors="replace").strip()
        except Exception:
            error_body = ""
        raise CoinosHTTPError(exc.code, error_body) from None
    except urllib.error.URLError as exc:
        raise RuntimeError("Coinos network error") from exc



async def coinos_request(method, path, payload=None):
    return await asyncio.to_thread(_coinos_request_sync, method, path, payload)

