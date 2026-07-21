"""Menu bar UI over the event-sourced tracker.

The embedded daemon (runtime/daemon.py) does the actual work; this UI only
issues commands and reads queries through TrackerRuntime. Launched by hand —
`uv run hue-clock`, or the dockable "Hue Clock.app" built by
scripts/make_app.py. Quit from the menu (or the Dock icon).
"""
import datetime as dt
import sys
import threading

import rumps

from hue_clock.formatting import format_clock, format_duration
from hue_clock.runtime.config import LOG_FILE
from hue_clock.runtime.daemon import start_daemon


class HueClockApp(rumps.App):
    def __init__(self) -> None:
        super().__init__("Hue Clock", title="⏳", quit_button=rumps.MenuItem("Quit Hue Clock"))
        self.now_item = rumps.MenuItem("starting…")
        self.today_item = rumps.MenuItem("")
        self.last_item = rumps.MenuItem("")
        self.strike_menu = rumps.MenuItem("Strike work time")
        self.menu = [self.now_item, self.today_item, self.last_item, self.strike_menu]
        self.runtime = None
        self.startup_error = None
        threading.Thread(target=self._run_daemon, daemon=True).start()
        rumps.Timer(self._refresh, 15).start()

    def _run_daemon(self) -> None:
        try:
            self._lock, self.runtime, listener = start_daemon()
            listener.run()
        except SystemExit as e:
            self.startup_error = str(e)
            print(f"listener exited: {e}", flush=True)
        except Exception as e:
            self.startup_error = str(e)
            print(f"listener crashed: {e}", flush=True)

    def _spawn_strike(self, fn, *args) -> None:
        def run() -> None:
            try:
                fn(*args)
            except Exception as e:
                print(f"strike failed: {e}", flush=True)
            self._refresh()
        threading.Thread(target=run, daemon=True).start()

    def _strike_custom(self, _sender) -> None:
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
                self._spawn_strike(self.runtime.strike_window, minutes)

    def _rebuild_strike_menu(self, now) -> None:
        """One entry per session today; click to tombstone the whole session.
        Already-struck sessions show ⚫ and are disabled (no callback).
        """
        if self.strike_menu._menu is not None:  # rumps: no NSMenu until first add
            self.strike_menu.clear()
        overviews = self.runtime.sessions(now)
        if not overviews:
            self.strike_menu.add(rumps.MenuItem("No sessions yet"))
        for index, session in enumerate(overviews):
            end_txt = format_clock(session.ended_at) if session.ended_at else "now"
            label = (f"{format_clock(session.started_at)}–{end_txt}"
                     f" · {format_duration(session.seconds)}")
            if session.fully_struck:
                self.strike_menu.add(rumps.MenuItem(f"⚫ {label}"))
            else:
                self.strike_menu.add(rumps.MenuItem(
                    label,
                    callback=lambda _s, i=index: self._spawn_strike(self.runtime.strike_session, i),
                ))
        self.strike_menu.add(rumps.separator)
        self.strike_menu.add(rumps.MenuItem("Custom…", callback=self._strike_custom))

    def _refresh(self, _timer=None) -> None:
        if self.startup_error:
            self.title = "⚠️"
            self.now_item.title = f"Listener stopped: {self.startup_error[:80]}"
            return
        if self.runtime is None:
            return
        now = dt.datetime.now()
        self.runtime.advance_to(now)
        # Emoji-only title: keeps the status item ~25pt wide so it fits in the
        # sliver of menu bar left of the notch. Details live in the menu.
        status = self.runtime.clock_status(now)
        if status is not None and status.is_clocked_in:
            self.title = "🟢"
            elapsed = format_duration((now - status.since).total_seconds())
            self.now_item.title = f"In for {elapsed}"
        else:
            self.title = "🔴"
            self.now_item.title = "Clocked out"
        summary = self.runtime.day_summary(now)
        if summary:
            plural = "s" if summary.session_count != 1 else ""
            title = (f"Today: {format_duration(summary.worked_seconds)} · "
                     f"{summary.session_count} session{plural}")
            if summary.struck_seconds >= 60:
                title += f" · {format_duration(summary.struck_seconds)} struck"
            self.today_item.title = title
        else:
            self.today_item.title = "No sessions today"
        self._rebuild_strike_menu(now)
        self._refresh_queue_item(now)

    def _refresh_queue_item(self, now) -> None:
        queue = self.runtime.queue_status(now)
        if queue.has_pending:
            count = f"{queue.pending} line{'s' if queue.pending != 1 else ''} queued"
            if not queue.head_sent:
                self.last_item.title = f"⚠️ {count} — appends failing"
            elif queue.head_resends >= 2:
                self.last_item.title = f"⚠️ {count} — try restarting Capacities"
            else:
                self.last_item.title = f"⏳ {count} — awaiting confirmation"
        elif queue.last_confirmed_at is not None:
            self.last_item.title = f"Last logged {format_clock(queue.last_confirmed_at)} ✓"
        else:
            self.last_item.title = "Nothing logged yet"


def main() -> None:
    log = open(LOG_FILE, "a", buffering=1)
    sys.stdout = sys.stderr = log
    print(f"--- Hue Clock menu bar app started {dt.datetime.now().isoformat()}", flush=True)
    app = HueClockApp()
    app._refresh()
    app.run()


if __name__ == "__main__":
    main()
