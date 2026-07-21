#!/usr/bin/env python3
"""Clock in/out by lamp: subscribe to the Hue Bridge event stream and log
focus-lamp transitions to the Capacities daily note.

Design: the lamp is the source of truth ("on" = clocked in). The Hue app maps
the Smart Button to toggle the lamp; this listener never controls anything, it
only projects lamp state into Capacities. Toggling via button, app, or Siri
logs identically. Each transition appends one compact line — append-only,
event-sourcing style: breaks ride on 🟢 lines, session length and running day
total (Σ) on 🔴 lines. Nothing in Capacities is ever overwritten; live totals
come from the local state file (menu bar app / `status` command).

The bridge keeps no event history — if this process is down, missed
transitions are detected on startup (state file vs. live lamp state) and
logged as a reconciliation entry, but their true timestamps are lost. Run it
on an always-on machine.

Config in .env next to this script:
  HUE_BRIDGE_IP=10.11.83.17
  HUE_APP_KEY=<from link-button registration>
  FOCUS_LIGHT_NAME=<exact light name in the Hue app>

Usage:
  hue_clock_listener.py lights   # list lights (find the focus lamp's name)
  hue_clock_listener.py status   # print current clock state and today's totals
  hue_clock_listener.py run      # start the listener (foreground)
"""
import datetime as dt
import fcntl
import json
import os
import ssl
import sys
import threading
import time
import urllib.request
from pathlib import Path

from capacities_client import CapacitiesClient, _load_env

STATE_FILE = Path.home() / ".local" / "state" / "hue_clock.json"
LOCK_FILE = Path.home() / ".local" / "state" / "hue_clock.lock"
RECONNECT_DELAY_S = 5

# Shared with the menu bar app (same process): its strike actions mutate the
# running listener's state through ACTIVE, serialized by these locks.
_mutex = threading.Lock()
_flush_mutex = threading.Lock()
ACTIVE = {"state": None, "sync": None}

# The bridge serves a self-signed cert; this is a LAN-only connection.
SSL_CTX = ssl._create_unverified_context()


def fmt_duration(seconds):
    h, m = int(seconds) // 3600, (int(seconds) % 3600) // 60
    return f"{h}h {m:02d}m" if h else f"{m}m"


def fmt_clock(when):
    return when.strftime("%-I:%M%p").lower()[:-1]  # "9:12a" / "5:40p"


def _merge_intervals(intervals):
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _overlap_seconds(a, b):
    total = 0.0
    for a_start, a_end in a:
        for b_start, b_end in b:
            lo, hi = max(a_start, b_start), min(a_end, b_end)
            if hi > lo:
                total += (hi - lo).total_seconds()
    return total


def acquire_single_instance_lock():
    """Prevent two listeners from double-logging. Returns the held lock file."""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise SystemExit("another hue_clock listener is already running — exiting")
    lock.write(str(os.getpid()))
    lock.flush()
    return lock


class Bridge:
    def __init__(self):
        _load_env()
        self.ip = os.environ.get("HUE_BRIDGE_IP")
        self.key = os.environ.get("HUE_APP_KEY")
        if not self.ip or not self.key:
            raise SystemExit("HUE_BRIDGE_IP / HUE_APP_KEY not set in .env")

    def get(self, path):
        req = urllib.request.Request(
            f"https://{self.ip}{path}", headers={"hue-application-key": self.key}
        )
        with urllib.request.urlopen(req, timeout=15, context=SSL_CTX) as resp:
            return json.loads(resp.read())

    def lights(self):
        return self.get("/clip/v2/resource/light")["data"]

    def find_light(self, name):
        matches = [l for l in self.lights() if l["metadata"]["name"] == name]
        if not matches:
            names = ", ".join(sorted(l["metadata"]["name"] for l in self.lights()))
            raise SystemExit(f"No light named {name!r}. Available: {names}")
        return matches[0]

    def open_event_stream(self):
        """Open the CLIP v2 SSE connection. Done before reconciling state so
        no event can slip between the reconcile read and the subscription.

        The read timeout is the liveness check: the bridge sends keepalives
        every few seconds, so 90s of silence means the socket died (e.g. the
        bridge dropped us while the Mac slept — the local side never notices).
        The timeout raises, and the run loop reconnects and reconciles."""
        req = urllib.request.Request(
            f"https://{self.ip}/eventstream/clip/v2",
            headers={"hue-application-key": self.key, "Accept": "text/event-stream"},
        )
        return urllib.request.urlopen(req, timeout=90, context=SSL_CTX)

    @staticmethod
    def iter_events(stream):
        """Yield decoded event payloads until the connection drops."""
        with stream:
            for raw in stream:
                line = raw.decode("utf-8", errors="replace").strip()
                if line.startswith("data:"):
                    try:
                        yield json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        pass


class ClockState:
    """Persisted lamp state + today's sessions, so restarts can detect missed
    transitions and the daily summary survives crashes."""

    def __init__(self):
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.data = {}
        if STATE_FILE.exists():
            try:
                self.data = json.loads(STATE_FILE.read_text())
            except json.JSONDecodeError:
                pass

    def save(self):
        STATE_FILE.write_text(json.dumps(self.data, indent=1))

    @property
    def lamp_on(self):
        return self.data.get("lamp_on")

    @property
    def since(self):
        raw = self.data.get("since")
        return dt.datetime.fromisoformat(raw) if raw else None

    def set_lamp(self, lamp_on, when):
        self.data["lamp_on"] = lamp_on
        self.data["since"] = when.isoformat()
        self.save()

    def ensure_day(self, when):
        """Reset sessions/summary at the first event of a new local day. If a
        session is open across midnight, restart it at 00:00 of the new day."""
        day = when.date().isoformat()
        if self.data.get("date") != day:
            self.data["date"] = day
            self.data["sessions"] = []
            self.data["strikes"] = []
            if self.lamp_on:
                midnight = dt.datetime.combine(when.date(), dt.time.min)
                self.data["sessions"].append({"in": midnight.isoformat(), "out": None})
            self.save()

    def open_session(self, when):
        self.ensure_day(when)
        self.data.setdefault("sessions", []).append({"in": when.isoformat(), "out": None})
        self.save()

    def close_session(self, when):
        self.ensure_day(when)
        sessions = self.data.setdefault("sessions", [])
        if sessions and sessions[-1]["out"] is None:
            sessions[-1]["out"] = when.isoformat()
            self.save()

    def strike_intervals(self):
        """Today's tombstoned windows, merged so overlaps never double-count."""
        return _merge_intervals([
            (dt.datetime.fromisoformat(s["start"]), dt.datetime.fromisoformat(s["end"]))
            for s in self.data.get("strikes", [])
        ])

    def day_summary(self, now):
        """Totals for today: worked (minus struck overlap), sessions count,
        away-within-workday, struck, span."""
        sessions = [
            (dt.datetime.fromisoformat(s["in"]),
             dt.datetime.fromisoformat(s["out"]) if s["out"] else None)
            for s in self.data.get("sessions", [])
        ]
        if not sessions:
            return None
        realized = [(start, end or now) for start, end in sessions]
        struck = _overlap_seconds(realized, self.strike_intervals())
        worked = sum((end - start).total_seconds() for start, end in realized) - struck
        away = sum(
            (sessions[i + 1][0] - sessions[i][1]).total_seconds()
            for i in range(len(sessions) - 1)
            if sessions[i][1]
        )
        return {
            "worked_s": worked,
            "away_s": away,
            "struck_s": struck,
            "count": len(sessions),
            "first_in": sessions[0][0],
            "last_end": sessions[-1][1],  # None while clocked in
        }


class CapacitiesSync:
    """Appends clock lines to the daily note. Append-only by design: every
    projection (break, session length, running total) rides on the event line
    that made it knowable, so nothing in Capacities is ever overwritten."""

    def __init__(self, state):
        self.client = CapacitiesClient()
        self.state = state

    def append_line(self, line, date_str):
        """Append and verify it stuck. A wedged Capacities desktop app (seen
        after the app sits open on the note across a Mac sleep) can clobber
        server-side appends seconds after they return 200 — so read the note
        back and retry once; raise if it keeps vanishing so the line stays in
        the pending queue for the next flush."""
        for attempt in range(2):
            self.client.append_daily_note(line, date=date_str)
            time.sleep(4 * (attempt + 1))
            if self._line_present(line, date_str):
                return
            print(f"append vanished (attempt {attempt + 1}/2): {line}", flush=True)
        raise RuntimeError("appends keep vanishing — restart the Capacities app")

    def _line_present(self, line, date_str):
        try:
            for result in self.client.search(date_str, structure_ids=["RootDailyNote"], limit=5):
                if date_str not in result.get("title", ""):
                    continue
                obj = self.client.get_object(result["id"])
                stack = [b for blocks in (obj.get("blocks") or {}).values() for b in blocks]
                while stack:
                    block = stack.pop()
                    text = "".join(t.get("text", "") for t in block.get("tokens") or [])
                    if text.strip() == line.strip():
                        return True
                    stack.extend(block.get("blocks") or [])
                return False
        except Exception:
            return True  # verification unavailable — assume ok, never duplicate
        return False


def log_transition(sync, state, lamp_on, when, note=""):
    """Record one lamp transition as a compact append-only line:
      🟢 9:12a                      clock in
      🟢 1:32p · 52m break          clock in after a gap
      🔴 12:40p · 3h 28m            clock out (session length)
      🔴 5:40p · 4h 08m · Σ 7h 36m  clock out (+ running day total)
    """
    with _mutex:
        state.ensure_day(when)
        sessions = state.data.get("sessions", [])
        if lamp_on:
            line = f"🟢 {fmt_clock(when)}"
            if sessions and sessions[-1]["out"]:
                gap = (when - dt.datetime.fromisoformat(sessions[-1]["out"])).total_seconds()
                if gap >= 60:
                    line += f" · {fmt_duration(gap)} break"
            state.open_session(when)
        else:
            line = f"🔴 {fmt_clock(when)}"
            if state.lamp_on and state.since:
                line += f" · {fmt_duration((when - state.since).total_seconds())}"
            state.close_session(when)
            closed = [
                (dt.datetime.fromisoformat(s["in"]), dt.datetime.fromisoformat(s["out"]))
                for s in state.data.get("sessions", []) if s["out"]
            ]
            if len(closed) > 1:
                total = sum((end - start).total_seconds() for start, end in closed)
                total -= _overlap_seconds(closed, state.strike_intervals())
                line += f" · Σ {fmt_duration(total)}"
        if note:
            line += f" *({note})*"
        state.set_lamp(lamp_on, when)
        # Event-sourcing: the line is committed to the local queue first (with
        # its date, so late flushes land on the right daily note), then flushed.
        state.data.setdefault("pending", []).append(
            {"line": line, "date": when.date().isoformat()}
        )
        state.save()
    print(f"[{when.isoformat(timespec='seconds')}] {line}", flush=True)
    flush_pending(sync, state)


def flush_pending(sync, state):
    """Drain queued lines oldest-first, stopping at the first failure so note
    order is preserved. Whatever fails stays queued and is retried on the next
    event, reconnect, or listener restart. Serialized so the listener thread
    and menu bar actions never double-append."""
    with _flush_mutex:
        pending = state.data.get("pending") or []
        while pending:
            try:
                sync.append_line(pending[0]["line"], pending[0]["date"])
            except Exception as e:
                state.data["last_append"] = {"ok": False, "at": dt.datetime.now().isoformat()}
                state.save()
                print(f"capacities write failed — {len(pending)} line(s) queued: {e}", flush=True)
                return
            pending.pop(0)
            state.data["last_append"] = {"ok": True, "at": dt.datetime.now().isoformat()}
            state.save()


def _record_strike(start, end):
    """Tombstone [start, end]: append a ⚫ event line through the same queue
    as clock lines; totals subtract only the overlap with clocked-in time."""
    state = ACTIVE["state"]
    if state is None:
        raise RuntimeError("listener not running yet")
    now = dt.datetime.now()
    with _mutex:
        state.ensure_day(now)
        state.data.setdefault("strikes", []).append(
            {"start": start.isoformat(), "end": end.isoformat()}
        )
        line = (f"⚫ {fmt_clock(start)}–{fmt_clock(end)}"
                f" · −{fmt_duration((end - start).total_seconds())}")
        state.data.setdefault("pending", []).append(
            {"line": line, "date": now.date().isoformat()}
        )
        state.save()
    print(f"[{now.isoformat(timespec='seconds')}] {line}", flush=True)
    return line


def strike_window(minutes):
    """Menu bar action: tombstone the trailing `minutes`."""
    now = dt.datetime.now()
    return _record_strike(now - dt.timedelta(minutes=minutes), now)


def strike_session(index):
    """Menu bar action: tombstone an entire session (by position in today's list)."""
    state = ACTIVE["state"]
    if state is None:
        raise RuntimeError("listener not running yet")
    sessions = state.data.get("sessions", [])
    if not 0 <= index < len(sessions):
        raise RuntimeError(f"no session #{index}")
    session = sessions[index]
    start = dt.datetime.fromisoformat(session["in"])
    end = (dt.datetime.fromisoformat(session["out"]) if session["out"]
           else dt.datetime.now())
    return _record_strike(start, end)


def session_labels(now=None):
    """(label, index, fully_struck) for today's sessions — the strike menu."""
    state = ACTIVE["state"]
    if state is None:
        return []
    now = now or dt.datetime.now()
    struck_ivs = state.strike_intervals()
    labels = []
    for i, s in enumerate(state.data.get("sessions", [])):
        start = dt.datetime.fromisoformat(s["in"])
        end = dt.datetime.fromisoformat(s["out"]) if s["out"] else None
        duration = ((end or now) - start).total_seconds()
        covered = _overlap_seconds([(start, end or now)], struck_ivs)
        fully = covered > 0 and covered >= duration - 1
        end_txt = fmt_clock(end) if end else "now"
        labels.append(
            (f"{fmt_clock(start)}–{end_txt} · {fmt_duration(duration)}", i, fully)
        )
    return labels


def reconcile(sync, state, bridge, light_id):
    """On startup/reconnect: if the lamp changed while we weren't listening,
    log it now with an 'approx' marker (the true timestamp is unrecoverable)."""
    live_on = bridge.get(f"/clip/v2/resource/light/{light_id}")["data"][0]["on"]["on"]
    if state.lamp_on is None:
        state.ensure_day(dt.datetime.now())
        state.set_lamp(live_on, dt.datetime.now())
        print(f"initialized: lamp is {'on' if live_on else 'off'}", flush=True)
    elif live_on != state.lamp_on:
        log_transition(sync, state, live_on, dt.datetime.now())


def cmd_lights():
    for light in Bridge().lights():
        on = "on " if light["on"]["on"] else "off"
        print(f"[{on}] {light['metadata']['name']}  ({light['id']})")


def cmd_status():
    state = ClockState()
    now = dt.datetime.now()
    if state.lamp_on:
        print(f"🟢 clocked in since {fmt_clock(state.since)} "
              f"({fmt_duration((now - state.since).total_seconds())})")
    else:
        print("🔴 clocked out")
    summary = state.day_summary(now)
    if summary:
        struck = (f", struck {fmt_duration(summary['struck_s'])}"
                  if summary["struck_s"] >= 60 else "")
        print(f"today: worked {fmt_duration(summary['worked_s'])} over "
              f"{summary['count']} session(s), away {fmt_duration(summary['away_s'])}{struck}")


def cmd_run():
    lock = acquire_single_instance_lock()  # noqa: F841 — held for process lifetime
    bridge = Bridge()
    light_name = os.environ.get("FOCUS_LIGHT_NAME")
    if not light_name:
        raise SystemExit("FOCUS_LIGHT_NAME not set in .env (see `lights` command)")
    light_id = bridge.find_light(light_name)["id"]
    state = ClockState()
    sync = CapacitiesSync(state)
    ACTIVE["state"], ACTIVE["sync"] = state, sync
    print(f"watching light {light_name!r} ({light_id}) on bridge {bridge.ip}", flush=True)

    while True:
        try:
            stream = bridge.open_event_stream()
            reconcile(sync, state, bridge, light_id)
            flush_pending(sync, state)
            for events in bridge.iter_events(stream):
                for event in events:
                    for item in event.get("data", []):
                        if item.get("id") != light_id or "on" not in item:
                            continue
                        lamp_on = item["on"]["on"]
                        if lamp_on != state.lamp_on:
                            log_transition(sync, state, lamp_on, dt.datetime.now())
        except KeyboardInterrupt:
            return
        except Exception as e:
            # A read timeout is the expected liveness check on an idle stream
            # (this bridge rarely sends keepalives) — reconnect immediately to
            # keep the blind window near zero. Real errors get a cooldown.
            if "timed out" in str(e).lower():
                continue
            print(f"stream error, reconnecting in {RECONNECT_DELAY_S}s: {e}", flush=True)
            time.sleep(RECONNECT_DELAY_S)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "lights":
        cmd_lights()
    elif cmd == "status":
        cmd_status()
    elif cmd == "run":
        cmd_run()
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
