"""Low-frequency stale snapshot reminders, using credential-free operational state."""
from __future__ import annotations

import threading

from .notify import notify_needed, resolve_webhook_path


class StaleMonitor:
    def __init__(self, root, *, enabled: bool = False, notifier=notify_needed,
                 interval: float = 60):
        self.root, self.enabled = root, enabled
        self.notifier, self.interval = notifier, interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def scan(self) -> list[dict]:
        if not self.enabled:
            return []
        path = resolve_webhook_path(data_dir=self.root.data_dir)
        if path is None or not path.is_file():
            return []  # No notification attempt: do not consume the cooldown.
        results = []
        for tag, state in self.root.initialized_states():
            if self._stop.is_set():
                break
            if state is None:
                continue
            for source, spec in list(state.sources.items()):
                if self._stop.is_set():
                    break
                try:
                    with state.lock:
                        spec = state.sources.get(source)
                        if not spec or not spec["enabled"]:
                            continue
                        status = state.sync.status(
                            source, stale_after=spec.get("stale_after_seconds", 86400))
                        if (status["freshness"] != "stale"
                                or not state.sync.reserve_stale_notification(source)):
                            continue
                    result = self.notifier(reason=f"{tag}/{source}: snapshot_stale",
                                           webhook_path=path)
                except (OSError, ValueError, TypeError, OverflowError):
                    continue  # One unreadable source must not stop other labels.
                notification = "sent" if result == "sent" else "failed"
                results.append({"sender_tag": tag, "source": source, "notification": notification})
        return results

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.scan()
            except (OSError, ValueError, TypeError):
                pass
            self._stop.wait(self.interval)

    def start(self) -> None:
        if self.enabled and self._thread is None:
            self._thread = threading.Thread(target=self._run, name="snapshot-stale-monitor",
                                            daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=15)
