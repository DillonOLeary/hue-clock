import contextlib
import json
import ssl
import urllib.request

BRIDGE_SSL_CTX = ssl._create_unverified_context()  # self-signed cert, LAN-only
STREAM_LIVENESS_TIMEOUT_S = 90


class HueBridge:
    def __init__(self, ip: str, app_key: str) -> None:
        self.ip = ip
        self.app_key = app_key

    def get(self, path):
        req = urllib.request.Request(
            f"https://{self.ip}{path}", headers={"hue-application-key": self.app_key}
        )
        with urllib.request.urlopen(req, timeout=15, context=BRIDGE_SSL_CTX) as resp:
            return json.loads(resp.read())

    def lights(self):
        return self.get("/clip/v2/resource/light")["data"]

    def find_light(self, name):
        matches = [l for l in self.lights() if l["metadata"]["name"] == name]
        if not matches:
            names = ", ".join(sorted(l["metadata"]["name"] for l in self.lights()))
            raise SystemExit(f"No light named {name!r}. Available: {names}")
        return matches[0]

    def light_is_on(self, light_id) -> bool:
        return self.get(f"/clip/v2/resource/light/{light_id}")["data"][0]["on"]["on"]

    def open_event_stream(self):
        """Opened before reconciling state so no event can slip between the
        reconcile read and the subscription. The read timeout is the liveness
        check: silence past it means the socket died (e.g. dropped during a
        Mac sleep) and the run loop should reconnect and reconcile.
        """
        req = urllib.request.Request(
            f"https://{self.ip}/eventstream/clip/v2",
            headers={"hue-application-key": self.app_key, "Accept": "text/event-stream"},
        )
        return urllib.request.urlopen(
            req, timeout=STREAM_LIVENESS_TIMEOUT_S, context=BRIDGE_SSL_CTX
        )

    @staticmethod
    def iter_events(stream):
        with stream:
            for raw in stream:
                line = raw.decode("utf-8", errors="replace").strip()
                if line.startswith("data:"):
                    with contextlib.suppress(json.JSONDecodeError):
                        yield json.loads(line[5:].strip())
