import threading

from hue_clock.projections.capacities_note.flusher import NoteFlusher

FLUSH_INTERVAL_SECONDS = 60


class FlusherLoop:
    """Runs a flush pass every 60s, or immediately when woken via wake()."""

    def __init__(self, flusher: NoteFlusher) -> None:
        self.flusher = flusher
        self._wake = threading.Event()

    def wake(self) -> None:
        self._wake.set()

    def run(self) -> None:
        while True:
            self._wake.wait(timeout=FLUSH_INTERVAL_SECONDS)
            self._wake.clear()
            self.flusher.flush()

    def start_thread(self) -> threading.Thread:
        thread = threading.Thread(target=self.run, daemon=True, name="note-flusher")
        thread.start()
        return thread
