"""Menu bar UI over the event-sourced tracker.

The embedded daemon (runtime/daemon.py) does the actual work; this UI only
issues commands and reads queries through TrackerRuntime. Launched by hand —
`uv run hue-clock`, or the dockable "Hue Clock.app" built by
scripts/make_app.py. Quit from the menu (or the Dock icon); quitting hands
off to Daemon.shutdown(), which clocks out and flushes.
"""

import datetime as dt
import sys
import threading

import rumps
import rumps.events

from hue_clock.formatting import format_clock, format_duration
from hue_clock.runtime.config import LOG_FILE
from hue_clock.runtime.daemon import start_daemon
from hue_clock.runtime.tracker_runtime import TrackerRuntime


class HueClockApp(rumps.App):
    def __init__(self) -> None:
        super().__init__("Hue Clock", title="⏳", quit_button=rumps.MenuItem("Quit Hue Clock"))
        self.now_item = rumps.MenuItem("starting…")
        self.today_item = rumps.MenuItem("")
        self.last_item = rumps.MenuItem("")
        self.strike_menu = rumps.MenuItem("Strike work time")
        self.menu = [self.now_item, self.today_item, self.last_item, self.strike_menu]
        self.runtime = None
        self.daemon = None
        self.startup_error = None
        rumps.events.before_quit.register(self._clock_out_on_quit)
        threading.Thread(target=self._run_daemon, daemon=True).start()
        rumps.Timer(self._refresh, 15).start()

    def _run_daemon(self) -> None:
        try:
            self.daemon = start_daemon()
            self.runtime = self.daemon.runtime
            self.daemon.listener.run()
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
            title="Strike work time",
            default_text="30",
            ok="Strike",
            cancel=True,
            dimensions=(160, 24),
        )
        response = window.run()
        if response.clicked:
            try:
                minutes = int(response.text.strip())
            except ValueError:
                return
            if minutes > 0 and self.runtime is not None:
                self._spawn_strike(self.runtime.strike_window, minutes)

    def _rebuild_strike_menu(self, now, runtime: TrackerRuntime) -> None:
        """One entry per session today; click to tombstone the whole session.

        Already-struck sessions show ⚫ and are disabled (no callback).
        """
        if self.strike_menu._menu is not None:  # rumps: no NSMenu until first add
            self.strike_menu.clear()
        overviews = runtime.sessions(now)
        if not overviews:
            self.strike_menu.add(rumps.MenuItem("No sessions yet"))
        for index, session in enumerate(overviews):
            end_txt = format_clock(session.ended_at) if session.ended_at else "now"
            label = (
                f"{format_clock(session.started_at)}–{end_txt} · {format_duration(session.seconds)}"
            )
            if session.fully_struck:
                self.strike_menu.add(rumps.MenuItem(f"⚫ {label}"))
            else:
                self.strike_menu.add(
                    rumps.MenuItem(
                        label,
                        callback=lambda _s, i=index: self._spawn_strike(runtime.strike_session, i),
                    )
                )
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
        since = status.since if status is not None and status.is_clocked_in else None
        if since is not None:
            self.title = "🟢"
            self.now_item.title = f"In for {format_duration((now - since).total_seconds())}"
        else:
            self.title = "🔴"
            self.now_item.title = "Clocked out"
        summary = self.runtime.day_summary(now)
        if summary:
            plural = "s" if summary.session_count != 1 else ""
            title = (
                f"Today: {format_duration(summary.worked_seconds)} · "
                f"{summary.session_count} session{plural}"
            )
            if summary.struck_seconds >= 60:
                title += f" · {format_duration(summary.struck_seconds)} struck"
            self.today_item.title = title
        else:
            self.today_item.title = "No sessions today"
        self._rebuild_strike_menu(now, self.runtime)
        self._refresh_queue_item(now, self.runtime)

    def _clock_out_on_quit(self) -> None:
        if self.daemon is not None:
            self.daemon.shutdown()

    def _refresh_queue_item(self, now, runtime: TrackerRuntime) -> None:
        queue = runtime.queue_status(now)
        if queue.has_pending:
            count = f"{queue.pending} line{'s' if queue.pending != 1 else ''} queued"
            stale = queue.is_stale(now)
            self.last_item.title = f"⚠️ {count} — check Capacities" if stale else f"⏳ {count}"
        elif queue.last_confirmed_at is not None:
            self.last_item.title = f"Last logged {format_clock(queue.last_confirmed_at)} ✓"
        else:
            self.last_item.title = "Nothing logged yet"


def main() -> None:
    # Explicit utf-8: a Finder-launched bundle has no locale, and the ascii
    # fallback makes every 🟢/🔴 print raise mid-pipeline.
    log = open(LOG_FILE, "a", buffering=1, encoding="utf-8")  # noqa: SIM115 — redirect target for the process lifetime
    sys.stdout = sys.stderr = log
    print(f"--- Hue Clock menu bar app started {dt.datetime.now().isoformat()}", flush=True)
    app = HueClockApp()
    app._refresh()
    app.run()


if __name__ == "__main__":
    main()
