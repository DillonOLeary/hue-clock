"""Minimal client for the Capacities API (the new one, not the deprecated beta).

Docs: https://developers.capacities.io
Tokens are created in the Capacities app (Settings > Capacities API), are bound
to a single space, and need api:read / api:write scopes.
"""

import json
import urllib.error
import urllib.request
from urllib.parse import urlencode

from tenacity import retry, retry_if_exception, stop_after_attempt, wait_incrementing

BASE_URL = "https://api.capacities.io"
API_VERSION = "1.0.0"
RETRYABLE_HTTP_CODES = frozenset({429, 500, 502, 503, 504})

Json = dict | list | None


def _is_transient(error: BaseException) -> bool:
    # HTTPError subclasses URLError, so check it first: a non-retryable status
    # must not fall through to the connection-error branch.
    if isinstance(error, urllib.error.HTTPError):
        return error.code in RETRYABLE_HTTP_CODES
    return isinstance(error, urllib.error.URLError)


@retry(
    retry=retry_if_exception(_is_transient),
    stop=stop_after_attempt(3),
    wait=wait_incrementing(start=3, increment=3),
    reraise=True,
)
def _send(req) -> Json:
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else None


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
            result = _send(req)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            raise RuntimeError(f"{method} {path} -> HTTP {e.code}: {detail}") from e
        return result

    def append_daily_note(self, markdown, date=None, no_timestamp=True) -> Json:
        body = {"markdown": markdown, "noTimeStamp": no_timestamp}
        if date:
            body["date"] = f"{date}T00:00:00.000Z"
        return self._request("POST", "/blocks/daily-note/append", body)
