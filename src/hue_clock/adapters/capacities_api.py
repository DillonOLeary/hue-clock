"""Minimal client for the Capacities API (the new one, not the deprecated beta).

Docs: https://developers.capacities.io
Tokens are created in the Capacities app (Settings > Capacities API), are bound
to a single space, and need api:read / api:write scopes.

Deliberately retry-free: the outbox is the one retry authority — an error keeps
the line queued and the flusher's next pass (≤60s) resends it.
"""

import json
import urllib.error
import urllib.request
from urllib.parse import urlencode

BASE_URL = "https://api.capacities.io"
API_VERSION = "1.0.0"

Json = dict | list | None


class CapacitiesClient:
    def __init__(self, token: str) -> None:
        self.token = token

    def _request(self, method, path, body=None, params=None) -> Json:
        url = f"{BASE_URL}{path}"
        if params:
            url += "?" + urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "X-Capacities-Api-Version": API_VERSION,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            raise RuntimeError(f"{method} {path} -> HTTP {e.code}: {detail}") from e
        return json.loads(raw) if raw else None

    def append_daily_note(self, markdown, date=None, no_timestamp=True) -> Json:
        body = {"markdown": markdown, "noTimeStamp": no_timestamp}
        if date:
            body["date"] = f"{date}T00:00:00.000Z"
        return self._request("POST", "/blocks/daily-note/append", body)
