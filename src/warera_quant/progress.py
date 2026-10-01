"""Console-independent stage timing and bounded progress notifications."""
from contextlib import contextmanager
from threading import Event, RLock, Thread
from time import monotonic


class ProgressReporter:
    def __init__(self, emit=None, *, verbose=False, interval=5.0):
        self.emit = emit
        self.verbose = verbose
        self.interval = interval
        self.started = monotonic()
        self.stack = []
        self.timings = []
        self.lock = RLock()
        self.last_detail = 0.0

    def detail(self, message, *, periodic=False):
        with self.lock:
            if self.stack:
                self.stack[-1][2] = message
            if self.emit and self.verbose and (not periodic or monotonic() - self.last_detail >= self.interval):
                self.emit(message)
                self.last_detail = monotonic()

    def _heartbeat(self, stop):
        while not stop.wait(self.interval):
            with self.lock:
                if self.stack and self.emit:
                    name, started, detail = self.stack[-1]
                    self.emit(f"{name}: still working; {monotonic() - started:.1f}s elapsed"
                              + (f"; {detail}" if detail else ""))

    @contextmanager
    def stage(self, name):
        if not self.emit:
            yield
            return
        started = monotonic()
        with self.lock:
            outer = not self.stack
            self.stack.append([name, started, ""])
            if outer or self.verbose:
                self.emit(f"{name}: started")
        stop = Event()
        worker = Thread(target=self._heartbeat, args=(stop,), daemon=True) if outer else None
        if worker:
            worker.start()
        status = "completed"
        try:
            yield
        except BaseException:
            status = "failed"
            raise
        finally:
            stop.set()
            if worker:
                worker.join()
            with self.lock:
                self.stack.pop()
                elapsed = monotonic() - started
                if outer:
                    self.timings.append((name, elapsed))
                if outer or self.verbose or status == "failed":
                    self.emit(f"{name}: {status} in {elapsed:.1f}s; preparation elapsed {monotonic() - self.started:.1f}s")

    def call(self, name, function, *args, **kwargs):
        with self.stage(name):
            result = function(*args, **kwargs)
            if isinstance(result, (list, tuple)):
                self.detail(f"{name}: {len(result):,} results")
            return result

    def summary(self):
        if self.emit and self.timings:
            self.emit("Report preparation timings: " + "; ".join(
                f"{name} {elapsed:.1f}s" for name, elapsed in self.timings))
