"""Runs the indexer in a background thread so the map stays usable during a scan."""

from __future__ import annotations

import logging
import threading

from .indexer import ScanReport, Settings, index_folder

log = logging.getLogger("photomap")


class ScanManager:
    def __init__(self):
        self._lock = threading.Lock()
        self.running = False
        self.done = 0
        self.total = 0
        self.report: ScanReport | None = None
        self.error: str | None = None
        self.version = 0  # goes up after every finished scan, so the map knows to reload

    def status(self) -> dict:
        with self._lock:
            return {"running": self.running, "done": self.done, "total": self.total,
                    "error": self.error, "version": self.version}

    def start(self, settings: Settings) -> bool:
        """Start a scan unless one is already running. Returns True if started."""
        with self._lock:
            if self.running:
                return False
            self.running, self.done, self.total, self.error = True, 0, 0, None
        threading.Thread(target=self._run, args=(settings,), daemon=True, name="photo-scan").start()
        return True

    def _progress(self, done, total):
        with self._lock:
            self.done, self.total = done, total

    def _run(self, settings: Settings):
        try:
            report = index_folder(settings, self._progress)
            with self._lock:
                self.report = report
        except Exception as e:  # shown in the app header
            log.exception("Scan failed")
            with self._lock:
                self.error = f"{type(e).__name__}: {e}"
        finally:
            with self._lock:
                self.running = False
                self.version += 1
