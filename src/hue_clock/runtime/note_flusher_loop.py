import threading

from hue_clock.projections.capacities_note.flusher import NoteFlusher

FLUSH_INTERVAL_SECONDS = 60


class FlusherLoop:
    def __init__(self, flusher: NoteFlusher, wake: threading.Event) -> None:
        self.flusher = flusher
        self.wake = wake

    def run(self) -> None:
        while True:
            self.wake.wait(timeout=FLUSH_INTERVAL_SECONDS)
            self.wake.clear()
            self.flusher.flush()

    def start_thread(self) -> threading.Thread:
        thread = threading.Thread(target=self.run, daemon=True, name="note-flusher")
        thread.start()
        return thread
