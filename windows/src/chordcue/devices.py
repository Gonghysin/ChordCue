"""Bounded browser registrations and independently acknowledged view assignments."""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
import secrets
import threading
import time

VIEWS = frozenset(("chords", "numbers", "staff", "tab", "metronome"))
ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")


def _text(value: object, maximum: int = 128) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise ValueError("invalid device text")
    return value


def _metric(value: object, maximum: float) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError("invalid device metric")
    if not math.isfinite(value) or abs(value) > maximum:
        raise ValueError("invalid device metric")
    return float(value)


@dataclass
class _Client:
    id: str
    key: str
    label: str
    browser: str
    capabilities: tuple[str, ...]
    part_id: str | None = None
    view: str = "chords"
    revision: int = 1
    applied_revision: int = 0
    score_revision: int = 0
    connected: bool = False
    connection_id: str | None = None
    last_seen: float = field(default_factory=time.monotonic)
    rtt: float | None = None
    offset: float | None = None
    audio_delay: float | None = None
    freshness: float | None = None
    sync: str = "calibrating"
    clock_status: str = "unavailable"
    clock_jitter: float | None = None
    clock_probe_age: float | None = None
    rate_window: float = 0.0
    rate_count: int = 0


class DeviceRegistry:
    def __init__(self, *, clock=time.monotonic, maximum=256):
        self._clock = clock
        self._maximum = maximum
        self._lock = threading.RLock()
        self._clients: dict[str, _Client] = {}
        self._parts: dict[str, frozenset[str]] = {}
        self._score_revision = 0

    def set_parts(self, parts: dict[str, frozenset[str]], revision: int) -> None:
        with self._lock:
            self._parts = parts.copy()
            self._score_revision = revision
            for item in self._clients.values():
                if item.part_id not in parts or item.view not in parts.get(item.part_id, frozenset()):
                    item.part_id = next(iter(parts), None)
                    item.view = self._default_view(item.part_id, item.capabilities)
                    item.revision += 1
                item.score_revision = revision
                item.applied_revision = 0

    def _default_view(self, part_id, capabilities):
        available = self._parts.get(part_id, VIEWS)
        return next((view for view in ("chords", "staff", "metronome")
                     if view in available and view in capabilities), "metronome")

    def register(self, payload: dict, browser: str) -> dict:
        if set(payload) - {"clientId", "resumeKey", "label", "capabilities"}:
            raise ValueError("unknown registration field")
        client_id = _text(payload.get("clientId"))
        if ID.fullmatch(client_id) is None:
            raise ValueError("invalid client ID")
        capabilities = payload.get("capabilities", list(VIEWS))
        if (not isinstance(capabilities, list) or not capabilities or len(capabilities) > 5
                or any(not isinstance(v, str) or v not in VIEWS for v in capabilities)):
            raise ValueError("invalid capabilities")
        with self._lock:
            self._expire()
            item = self._clients.get(client_id)
            if item:
                self._authenticate(item, payload.get("resumeKey"))
                item.capabilities = tuple(capabilities)
                item.browser = browser[:256]
            else:
                if len(self._clients) >= self._maximum:
                    raise ValueError("device registry is full")
                item = _Client(client_id, secrets.token_hex(32),
                               _text(payload.get("label", "浏览器 " + client_id[-6:])),
                               browser[:256], tuple(capabilities))
                item.part_id = next(iter(self._parts), None)
                item.view = self._default_view(item.part_id, item.capabilities)
                item.score_revision = self._score_revision
                self._clients[client_id] = item
            item.last_seen = self._clock()
            result = self._assignment(item)
            result["resumeKey"] = item.key
            result["serverCapabilities"] = ["clockDiagnosticsV1"]
            return result

    def _authenticate(self, item, key):
        if not isinstance(key, str) or not secrets.compare_digest(item.key, key):
            raise PermissionError("invalid device recovery credential")

    def authenticate(self, client_id, key):
        with self._lock:
            item = self._clients.get(client_id)
            if item is None:
                raise PermissionError("unknown device")
            self._authenticate(item, key)
            return item

    def attach(self, client_id, key, connection_id):
        with self._lock:
            item = self.authenticate(client_id, key)
            item.connected = True
            item.connection_id = connection_id
            item.last_seen = self._clock()

    def disconnect(self, client_id, connection_id):
        with self._lock:
            item = self._clients.get(client_id)
            if item and item.connection_id == connection_id:
                item.connected = False
                item.connection_id = None
                item.sync = "disconnected"

    def stop(self):
        with self._lock:
            for item in self._clients.values():
                item.connected = False
                item.connection_id = None
                item.sync = "disconnected"

    def telemetry(self, payload):
        allowed = {"clientId", "resumeKey", "appliedAssignmentRevision", "scoreRevision",
                   "rttMs", "clockOffsetMs", "audioOutputDelayMs", "freshnessMs", "syncStatus", "clockDiagnostics"}
        if set(payload) - allowed:
            raise ValueError("unknown telemetry field")
        with self._lock:
            item = self.authenticate(payload.get("clientId"), payload.get("resumeKey"))
            now = self._clock()
            if now - item.rate_window >= 1:
                item.rate_window, item.rate_count = now, 0
            item.rate_count += 1
            if item.rate_count > 10:
                raise ValueError("device telemetry rate exceeded")
            rtt = _metric(payload.get("rttMs"), 60000)
            freshness = _metric(payload.get("freshnessMs"), 3600000)
            if (rtt is not None and rtt < 0) or (freshness is not None and freshness < 0):
                raise ValueError("negative latency")
            sync = payload.get("syncStatus", "calibrating")
            if sync not in ("calibrating", "synchronized", "stale", "disabled"):
                raise ValueError("invalid synchronization status")
            applied, score = payload.get("appliedAssignmentRevision", 0), payload.get("scoreRevision", 0)
            if type(applied) is not int or type(score) is not int or min(applied, score) < 0:
                raise ValueError("invalid assignment ACK")
            offset = _metric(payload.get("clockOffsetMs"), 10**12)
            audio_delay = _metric(payload.get("audioOutputDelayMs"), 10000)
            status, jitter, probe_age = "unavailable", None, None
            if "clockDiagnostics" in payload:
                diagnostics = payload["clockDiagnostics"]
                if (not isinstance(diagnostics, dict)
                        or set(diagnostics) != {"version", "status", "jitterMs", "probeAgeMs"}
                        or type(diagnostics["version"]) is not int or diagnostics["version"] != 1
                        or diagnostics["status"] not in ("valid", "calibrating", "stale")):
                    raise ValueError("invalid clock diagnostics")
                status = diagnostics["status"]
                jitter = _metric(diagnostics["jitterMs"], 10**12)
                probe_age = _metric(diagnostics["probeAgeMs"], 3600000)
                if ((jitter is not None and jitter < 0) or (probe_age is not None and probe_age < 0)
                        or status == "valid" and (probe_age is None or probe_age >= 10000 or offset is None or rtt is None)):
                    raise ValueError("invalid clock diagnostics")
                if status != "valid" and sync == "synchronized":
                    sync = "calibrating" if status == "calibrating" else "stale"
            if applied == item.revision and score == item.score_revision:
                item.applied_revision = applied
            item.rtt, item.freshness = rtt, freshness
            item.offset = offset
            item.audio_delay = audio_delay
            item.clock_status, item.clock_jitter, item.clock_probe_age = status, jitter, probe_age
            item.sync, item.last_seen = sync, now
            return self._assignment(item)

    def assign(self, client_id, part_id, view, label=None):
        with self._lock:
            item = self._clients.get(client_id)
            if item is None:
                raise ValueError("unknown device")
            if view not in VIEWS or view not in item.capabilities:
                raise ValueError("view unavailable on client")
            if self._parts and (part_id not in self._parts or view not in self._parts[part_id]):
                raise ValueError("view unavailable for part")
            if not self._parts and part_id is not None:
                raise ValueError("unknown part")
            if label is not None:
                item.label = _text(label)
            if item.part_id != part_id or item.view != view:
                item.part_id, item.view = part_id, view
                item.revision += 1
                item.applied_revision = 0
            return self._assignment(item)

    def assignment(self, client_id):
        with self._lock:
            item = self._clients.get(client_id)
            return self._assignment(item) if item else None

    @staticmethod
    def _assignment(item):
        return {"protocolVersion": 2, "clientId": item.id, "label": item.label,
                "partId": item.part_id, "view": item.view,
                "assignmentRevision": item.revision, "scoreRevision": item.score_revision}

    def summaries(self):
        with self._lock:
            self._expire()
            now = self._clock()
            return [{**self._assignment(item), "browser": item.browser,
                     "capabilities": list(item.capabilities),
                     "connected": item.connected and now - item.last_seen < 6,
                     "lastSeenSeconds": max(0, now-item.last_seen), "rttMs": item.rtt,
                     "clockOffsetMs": item.offset, "audioOutputDelayMs": item.audio_delay,
                     "clockStatus": item.clock_status if now-item.last_seen < 6 else "stale",
                     "clockJitterMs": item.clock_jitter,
                     "clockProbeAgeMs": (item.clock_probe_age + max(0, now-item.last_seen)*1000
                                         if item.clock_probe_age is not None else None),
                     "freshnessMs": item.freshness,
                     "syncStatus": item.sync if now-item.last_seen < 6 else "stale",
                     "applied": item.applied_revision == item.revision}
                    for item in self._clients.values()]

    def _expire(self):
        now = self._clock()
        for key in tuple(self._clients):
            if not self._clients[key].connected and now-self._clients[key].last_seen > 86400:
                del self._clients[key]
