#!/usr/bin/env python3
"""Minimal client for the Capacities API (the new one, not the deprecated beta).

Docs: https://developers.capacities.io
Tokens are created in the Capacities app (Settings > Capacities API), are bound
to a single space, and need api:read / api:write scopes. The token is read from
the CAPACITIES_API_TOKEN env var or a .env file next to this script.
"""
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

BASE_URL = "https://api.capacities.io"
API_VERSION = "0.1.0"


def _load_env():
    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip())


class CapacitiesClient:
    def __init__(self, token=None):
        _load_env()
        self.token = token or os.environ.get("CAPACITIES_API_TOKEN")
        if not self.token:
            raise SystemExit("CAPACITIES_API_TOKEN is not set (env var or .env file)")

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
        """Append markdown to a daily note. date is 'YYYY-MM-DD' (defaults to today)."""
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

    def update_text_block(self, object_id, block_id, text, italic=False):
        block = {
            "type": "TextBlock",
            "tokens": [{"type": "TextToken", "text": text, "style": {"italic": italic}}],
        }
        return self._request(
            "PATCH", "/blocks/block", {"id": object_id, "blockId": block_id, "block": block}
        )


if __name__ == "__main__":
    client = CapacitiesClient()
    print(json.dumps(client.space(), indent=2))
