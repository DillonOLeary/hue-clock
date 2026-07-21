#!/usr/bin/env python3
"""Menu bar wrapper around the Hue clock listener.

Shows 🟢 + elapsed time while clocked in, 🔴 while out. The embedded listener
does the actual work (see listener.py); this UI just reads the shared state
file. Launched at login via launchd (see launchd/com.dillonoleary.hue-clock.plist);
quit from the menu. The single-instance lock in the listener prevents
double-logging if a second copy is started.
"""
import datetime as dt
import sys
import threading
from pathlib import Path

import rumps

from hue_clock import listener as hcl

LOG_PATH = Path.home() / "Library" / "Logs" / "hue-clock.log"


class HueClockApp(rumps.App):
    def __init__(self):
        super().__init__("Hue Clock", title="⏳", quit_button=rumps.MenuItem("Quit Hue Clock"))
        self.now_item = rumps.MenuItem("starting…")
        self.today_item = rumps.MenuItem("")
        self.last_item = rumps.MenuItem("")
        self.strike_menu = rumps.MenuItem("Strike work time")
        self.menu = [self.now_item, self.today_item, self.last_item, self.strike_menu]
        self.listener_error = None
        threading.Thread(target=self._listen, daemon=True).start()
        rumps.Timer(self._refresh, 15).start()

    def _listen(self):
        try:
            hcl.cmd_run()
        except SystemExit as e:
            self.listener_error = str(e)
            print(f"listener exited: {e}", flush=True)
        except Exception as e:
            self.listener_error = str(e)
            print(f"listener crashed: {e}", flush=True)

    def _spawn_strike(self, fn, *args):
        def run():
            try:
                fn(*args)
                hcl.flush_pending(hcl.ACTIVE["sync"], hcl.ACTIVE["state"])
            except Exception as e:
                print(f"strike failed: {e}", flush=True)
            self._refresh()
        threading.Thread(target=run, daemon=True).start()

    def _strike_custom(self, _sender):
        window = rumps.Window(
            message="Minutes to strike, counting back from now:",
            title="Strike work time", default_text="30",
            ok="Strike", cancel=True, dimensions=(160, 24),
        )
        response = window.run()
        if response.clicked:
            try:
                minutes = int(response.text.strip())
            except ValueError:
                return
            if minutes > 0:
                self._spawn_strike(hcl.strike_window, minutes)

    def _rebuild_strike_menu(self):
        """One entry per session today; click to tombstone the whole session.
        Already-struck sessions show ⚫ and are disabled (no callback)."""
        if self.strike_menu._menu is not None:  # rumps: no NSMenu until first add
            self.strike_menu.clear()
        labels = hcl.session_labels()
        if not labels:
            self.strike_menu.add(rumps.MenuItem("No sessions yet"))
        for label, index, struck in labels:
            if struck:
                self.strike_menu.add(rumps.MenuItem(f"⚫ {label}"))
            else:
                self.strike_menu.add(rumps.MenuItem(
                    label,
                    callback=lambda _s, i=index: self._spawn_strike(hcl.strike_session, i),
                ))
        self.strike_menu.add(rumps.separator)
        self.strike_menu.add(rumps.MenuItem("Custom…", callback=self._strike_custom))

    def _refresh(self, _timer=None):
        if self.listener_error:
            self.title = "⚠️"
            self.now_item.title = f"Listener stopped: {self.listener_error[:80]}"
            return
        state = hcl.ClockState()
        now = dt.datetime.now()
        # Emoji-only title: keeps the status item ~25pt wide so it fits in the
        # sliver of menu bar left of the notch. Details live in the menu.
        if state.lamp_on and state.since:
            self.title = "🟢"
            elapsed = hcl.fmt_duration((now - state.since).total_seconds())
            self.now_item.title = f"In for {elapsed}"
        elif state.lamp_on is None:
            self.title = "⏳"
            self.now_item.title = "starting…"
        else:
            self.title = "🔴"
            self.now_item.title = "Clocked out"
        summary = state.day_summary(now)
        if summary:
            title = (f"Today: {hcl.fmt_duration(summary['worked_s'])} · "
                     f"{summary['count']} session{'s' if summary['count'] != 1 else ''}")
            if summary["struck_s"] >= 60:
                title += f" · {hcl.fmt_duration(summary['struck_s'])} struck"
            self.today_item.title = title
        else:
            self.today_item.title = "No sessions today"
        self._rebuild_strike_menu()
        pending = len(state.data.get("pending") or [])
        last = state.data.get("last_append")
        if pending:
            plural = "s" if pending != 1 else ""
            self.last_item.title = f"⚠️ {pending} line{plural} queued — restart Capacities"
        elif last:
            at = hcl.fmt_clock(dt.datetime.fromisoformat(last["at"]))
            self.last_item.title = f"Last logged {at} ✓"
        else:
            self.last_item.title = "Nothing logged yet"


def main():
    log = open(LOG_PATH, "a", buffering=1)
    sys.stdout = sys.stderr = log
    print(f"--- Hue Clock menu bar app started {dt.datetime.now().isoformat()}", flush=True)
    app = HueClockApp()
    app._refresh()
    app.run()


if __name__ == "__main__":
    main()
