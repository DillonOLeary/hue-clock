"""Minimal client for the Capacities API (the new one, not the deprecated beta).

Docs: https://developers.capacities.io
Tokens are created in the Capacities app (Settings > Capacities API), are bound
to a single space, and need api:read / api:write scopes.
"""
import json
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode

BASE_URL = "https://api.capacities.io"
API_VERSION = "0.1.0"


class CapacitiesClient:
    def __init__(self, token: str):
        self.token = token

    def _request(self, method, path, body=None, params=None):
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
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    raw = resp.read()
                return json.loads(raw) if raw else None
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(3 * (attempt + 1))
                    continue
                detail = e.read().decode(errors="replace")[:500]
                raise RuntimeError(f"{method} {path} -> HTTP {e.code}: {detail}") from e
            except urllib.error.URLError:
                if attempt < 2:
                    time.sleep(3)
                    continue
                raise

    def space(self):
        return self._request("GET", "/space")

    def structures(self):
        return self._request("GET", "/space/structures")["structures"]

    def search(self, query, structure_ids=None, limit=None):
        body = {"query": query}
        if structure_ids:
            body["structureIds"] = structure_ids
        if limit:
            body["limit"] = limit
        return self._request("POST", "/objects/search", body)["results"]

    def get_object(self, object_id):
        return self._request("GET", "/object", params={"id": object_id})

    def append_daily_note(self, markdown, date=None, no_timestamp=True):
        body = {"markdown": markdown, "noTimeStamp": no_timestamp}
        if date:
            body["date"] = f"{date}T00:00:00.000Z"
        return self._request("POST", "/blocks/daily-note/append", body)

    def create_object_from_markdown(self, structure_id, markdown):
        return self._request(
            "POST", "/object/markdown", {"structureId": structure_id, "markdown": markdown}
        )

    def delete_block(self, object_id, block_id):
        return self._request(
            "DELETE", "/block", params={"objectId": object_id, "blockId": block_id}
        )
