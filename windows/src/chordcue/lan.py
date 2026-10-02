"""Read-only LAN HTTP/SSE service, created only by an explicit ``start``.

One request is accepted per connection; only SSE connections remain open.  The
small asyncio protocol enforces the *total* request-header and connection limits
before parsing a request, including connections that have not sent any bytes.
All socket state belongs to one non-daemon thread. GUI publication retains one
immutable pending snapshot, and each SSE peer retains only its latest snapshot.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import ipaddress
import json
import re
import secrets
import socket
import sys
import threading
import time
from typing import Callable, Iterable, cast
from urllib.parse import quote

from .models import ChartDocument, KeySection, MusicalKey, event_position
from .paths import resource_path
from .theory import number


_LOCAL_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8",
    "169.254.0.0/16", "::1/128", "fe80::/10", "fc00::/7",
))
_SECURITY_HEADERS = (
    "Cache-Control: no-store\r\n"
    "X-Content-Type-Options: nosniff\r\n"
    "Referrer-Policy: no-referrer\r\n"
    "Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'\r\n"
)


def is_local_address(value: str) -> bool:
    """Explicit LAN ranges; documentation, CGNAT and reserved IPs are excluded."""
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return any(address.version == network.version and address in network
               for network in _LOCAL_NETWORKS)


def _windows_addresses() -> list[str]:
    """Enumerate active adapter unicast addresses using Windows IP Helper."""
    import ctypes

    uint32 = ctypes.c_uint32

    class SocketAddress(ctypes.Structure):
        _fields_ = [("address", ctypes.c_void_p), ("length", ctypes.c_int)]

    class Unicast(ctypes.Structure):
        pass

    Unicast._fields_ = [
        ("alignment", ctypes.c_uint64), ("next", ctypes.POINTER(Unicast)),
        ("socket", SocketAddress),
    ]

    class Adapter(ctypes.Structure):
        pass

    Adapter._fields_ = [
        ("alignment", ctypes.c_uint64), ("next", ctypes.POINTER(Adapter)),
        ("name", ctypes.c_char_p), ("unicast", ctypes.POINTER(Unicast)),
        ("anycast", ctypes.c_void_p), ("multicast", ctypes.c_void_p),
        ("dns_server", ctypes.c_void_p), ("dns_suffix", ctypes.c_wchar_p),
        ("description", ctypes.c_wchar_p), ("friendly_name", ctypes.c_wchar_p),
        ("physical_address", ctypes.c_ubyte * 8), ("physical_length", uint32),
        ("flags", uint32), ("mtu", uint32), ("interface_type", uint32),
        ("status", ctypes.c_int), ("ipv6_index", uint32), ("zones", uint32 * 16),
    ]
    get_adapters = ctypes.WinDLL("iphlpapi.dll").GetAdaptersAddresses
    get_adapters.argtypes = [uint32, uint32, ctypes.c_void_p, ctypes.c_void_p,
                            ctypes.POINTER(uint32)]
    get_adapters.restype = uint32
    size = uint32(16384)
    for _ in range(3):
        buffer = ctypes.create_string_buffer(size.value)
        error = get_adapters(socket.AF_UNSPEC, 2 | 4 | 8, None, buffer,
                             ctypes.byref(size))
        if error != 111:  # ERROR_BUFFER_OVERFLOW: adapters changed; retry sizing.
            break
    if error == 232:  # ERROR_NO_DATA
        return []
    if error:
        raise OSError(error, "GetAdaptersAddresses failed")
    result = []
    adapter = ctypes.cast(buffer, ctypes.POINTER(Adapter))
    while adapter:
        current = adapter.contents
        if current.status == 1 and current.interface_type != 24:  # Up, non-loopback
            unicast = current.unicast
            while unicast:
                entry = unicast.contents
                raw = ctypes.string_at(entry.socket.address, entry.socket.length)
                family = int.from_bytes(raw[:2], "little")
                if family == socket.AF_INET and len(raw) >= 8:
                    result.append(socket.inet_ntop(socket.AF_INET, raw[4:8]))
                elif family == socket.AF_INET6 and len(raw) >= 28:
                    address = socket.inet_ntop(socket.AF_INET6, raw[8:24])
                    scope = int.from_bytes(raw[24:28], "little")
                    if scope:
                        address += f"%{scope}"
                    result.append(address)
                unicast = entry.next
        adapter = current.next
    return result


def local_addresses() -> list[str]:
    """Return LAN addresses without relying on Darwin interface naming."""
    if sys.platform == "win32":
        candidates = _windows_addresses()
    else:
        # Development/test fallback. Windows uses complete adapter enumeration.
        candidates = [row[4][0] for row in socket.getaddrinfo(
            socket.gethostname(), None, type=socket.SOCK_STREAM)]
    result = list(dict.fromkeys(address for address in candidates
                               if is_local_address(address)
                               and not ipaddress.ip_address(address.split("%", 1)[0]).is_loopback))
    result.sort(key=lambda value: (ipaddress.ip_address(value.split("%", 1)[0]).is_link_local,
                                   ":" in value, value))
    return result or ["127.0.0.1"]


def chart_payload(document: ChartDocument, sections: Iterable[KeySection],
                  revision: int = 1) -> dict:
    """Serialize the legacy chart shape, extended with explicit project bars."""
    document.validate()
    _revision(revision)
    ordered = sorted(sections, key=lambda section: section.first_bar)
    seen = set()
    for section in ordered:
        section.validate()
        if section.first_bar > document.bars or section.first_bar in seen:
            raise ValueError("invalid or duplicate chart section bar")
        seen.add(section.first_bar)
    events = []
    key = MusicalKey(0)
    section_index = 0
    for event in sorted(document.events, key=lambda item: (item.bar, item.tick)):
        while section_index < len(ordered) and ordered[section_index].first_bar <= event.bar:
            key = ordered[section_index].key
            section_index += 1
        events.append({"id": event.id, **event_position(event), "symbol": event.symbol,
                       "number": number(event.symbol, key)})
    return {"revision": revision, "name": document.name, "bars": document.bars,
            "meter": f"{document.meter}/4", "events": events,
            "sections": [{"bar": section.first_bar, "root": section.key.root,
                          "minor": section.key.is_minor,
                          "family": section.key.major_family_root} for section in ordered]}


def _revision(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("revision must be a nonnegative integer")
    return value


@dataclass(frozen=True)
class _Limits:
    connections: int = 40
    streams: int = 24
    header_bytes: int = 8192
    request_seconds: float = 5.0
    write_seconds: float = 5.0
    heartbeat_seconds: float = 1.0
    chart_bytes: int = 2 * 1024 * 1024
    transport_bytes: int = 64 * 1024
    write_high: int = 64 * 1024
    socket_send_bytes: int = 64 * 1024


@dataclass(frozen=True)
class _Snapshot:
    chart: bytes
    transport: bytes


class LANServer:
    """Synchronous desktop facade over a bounded background asyncio server.

    ``start`` and ``stop`` wait for socket lifecycle completion and propagate
    failures. ``publish`` only serializes/enqueues; it never waits for clients.
    Keyword configuration exists for deterministic loopback protocol tests.
    """

    def __init__(self, *, host: str | None = None, port: int = 0,
                 _limits: _Limits | None = None,
                 _addresses: Callable[[], list[str]] = local_addresses,
                 _peer_filter: Callable[[str], bool] = is_local_address) -> None:
        self._host, self._port = host, port
        self._limits = _limits or _Limits()
        self._addresses, self._peer_filter = _addresses, _peer_filter
        self._lifecycle = threading.RLock()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._shutdown: asyncio.Event | None = None
        self._ready = threading.Event()
        self._running = False
        self._failure: BaseException | None = None
        self._links: list[str] = []
        self._pending: _Snapshot | None = None
        self._scheduled = False
        self._current: _Snapshot | None = None
        self._peers: set[_Peer] = set()
        self._viewers = 0
        self._connections = 0
        self._assets: dict[str, tuple[str, bytes]] = {}
        self._token = self._session = ""

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._running

    @property
    def viewers(self) -> int:
        with self._lock:
            return self._viewers

    @property
    def connections(self) -> int:
        with self._lock:
            return self._connections

    def start(self) -> list[str]:
        with self._lifecycle:
            with self._lock:
                if self._running:
                    self._raise_failure()
                    return self._links.copy()
            if self._thread is not None:
                self.stop()
            self._assets = {
                "": ("text/html; charset=utf-8", resource_path("Broadcast.html").read_bytes()),
                "Metronome.js": ("text/javascript; charset=utf-8",
                                 resource_path("Metronome.js").read_bytes()),
            }
            addresses = self._addresses()
            self._token, self._session = secrets.token_hex(16), secrets.token_hex(16)
            self._ready.clear()
            with self._lock:
                self._failure = None
                self._pending = self._current = None
                self._scheduled = False
            self._thread = threading.Thread(target=self._thread_main, args=(addresses,),
                                            name="ChordCue-LAN", daemon=False)
            self._thread.start()
            # No DNS or resource reads occur in this thread's startup section.
            self._ready.wait()
            with self._lock:
                failure = self._failure
                links = self._links.copy()
            if failure:
                self._thread.join()
                self._thread = None
                raise RuntimeError(f"LAN server could not start: {failure}") from failure
            return links

    def publish(self, chart: dict, transport: dict) -> None:
        with self._lock:
            self._raise_failure()
            if not self._running:
                return
            session = self._session
        if _revision(chart.get("revision")) != _revision(transport.get("revision")):
            raise ValueError("chart and transport revisions must match")
        snapshot = _Snapshot(self._event("chart", chart, self._limits.chart_bytes),
                             self._event("transport", transport, self._limits.transport_bytes))
        with self._lock:
            self._raise_failure()
            if not self._running or session != self._session:
                return
            self._pending = snapshot
            if not self._scheduled:
                self._scheduled = True
                assert self._loop is not None
                self._loop.call_soon_threadsafe(self._consume)

    def stop(self) -> None:
        with self._lifecycle:
            thread = self._thread
            if thread is None:
                return
            with self._lock:
                self._running = False
                loop, shutdown = self._loop, self._shutdown
                self._pending = None
            if thread.is_alive() and loop is not None and shutdown is not None:
                try:
                    loop.call_soon_threadsafe(shutdown.set)
                except RuntimeError:
                    pass  # The loop already finished and its failure is reported below.
            thread.join(timeout=5)
            if thread.is_alive():
                raise RuntimeError("LAN server thread did not shut down")
            self._thread = None
            self._assets.clear()
            with self._lock:
                self._raise_failure()

    def _raise_failure(self) -> None:
        if self._failure is not None:
            raise RuntimeError(f"LAN server failed: {self._failure}") from self._failure

    @staticmethod
    def _event(name: str, value: dict, maximum: int) -> bytes:
        data = json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")
        if len(data) > maximum:
            raise ValueError(f"{name} payload exceeds {maximum} bytes")
        return b"event: " + name.encode("ascii") + b"\ndata: " + data + b"\n\n"

    def _thread_main(self, addresses: list[str]) -> None:
        try:
            asyncio.run(self._serve(addresses))
        except BaseException as error:
            with self._lock:
                self._failure = error
        finally:
            with self._lock:
                self._running = False
                self._pending = self._current = None
                self._scheduled = False
                self._loop = self._shutdown = None
                self._links = []
                self._viewers = self._connections = 0
            self._ready.set()

    async def _serve(self, addresses: list[str]) -> None:
        loop = asyncio.get_running_loop()
        shutdown = asyncio.Event()
        with self._lock:
            self._loop, self._shutdown = loop, shutdown

        def failed(_loop: asyncio.AbstractEventLoop, context: dict) -> None:
            with self._lock:
                self._failure = context.get("exception") or RuntimeError(context["message"])
            shutdown.set()

        loop.set_exception_handler(failed)
        sock = self._listener_socket()
        try:
            server = await loop.create_server(lambda: _Peer(self), sock=sock)
        except BaseException:
            sock.close()
            raise
        port = sock.getsockname()[1]
        family = sock.family
        addresses = [value for value in addresses if is_local_address(value)
                     and (family == socket.AF_INET6 or ":" not in value)]
        if not addresses:
            addresses = ["127.0.0.1"]
        links = []
        for address in addresses:
            authority = f"[{quote(address, safe=':')}]" if ":" in address else address
            links.append(f"http://{authority}:{port}/join/{self._token}/")
        with self._lock:
            self._links = links
            self._running = True
        self._ready.set()
        heartbeat = asyncio.create_task(self._heartbeat())

        def heartbeat_done(task: asyncio.Task) -> None:
            if not task.cancelled() and (error := task.exception()) is not None:
                failed(loop, {"exception": error})

        heartbeat.add_done_callback(heartbeat_done)
        try:
            await shutdown.wait()
        finally:
            server.close()
            heartbeat.cancel()
            for peer in tuple(self._peers):
                peer.close()
            await server.wait_closed()
            await asyncio.gather(heartbeat, return_exceptions=True)
            await asyncio.sleep(0)  # Deliver connection_lost before the loop exits.
            self._peers.clear()

    def _listener_socket(self) -> socket.socket:
        if self._host is None:
            try:
                sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
                sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            except OSError:
                if "sock" in locals():
                    sock.close()
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            host = "::" if sock.family == socket.AF_INET6 else "0.0.0.0"
        else:
            sock = socket.socket(socket.AF_INET6 if ":" in self._host else socket.AF_INET,
                                 socket.SOCK_STREAM)
            host = self._host
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sock.bind((host, self._port))
            sock.setblocking(False)
        except BaseException:
            sock.close()
            raise
        return sock

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(self._limits.heartbeat_seconds)
            for peer in tuple(self._peers):
                peer.heartbeat()

    def _consume(self) -> None:
        with self._lock:
            snapshot, self._pending = self._pending, None
            self._scheduled = False
            running = self._running
        if snapshot is None or not running:
            return
        self._current = snapshot
        for peer in tuple(self._peers):
            if peer.streaming:
                peer.offer(snapshot)

    def _counts(self) -> None:
        with self._lock:
            self._connections = len(self._peers)
            self._viewers = sum(peer.streaming for peer in self._peers)


class _Peer(asyncio.Protocol):
    def __init__(self, owner: LANServer) -> None:
        self.owner = owner
        self.transport: asyncio.Transport | None = None
        self.streaming = False
        self._responded = False
        self.closed = False
        self.paused = False
        self._request = bytearray()
        self._request_timer: asyncio.TimerHandle | None = None
        self._write_timer: asyncio.TimerHandle | None = None
        self._pending: _Snapshot | None = None
        self._sent_chart: bytes | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = cast(asyncio.Transport, transport)
        remote = transport.get_extra_info("peername")
        if (not self.owner.enabled or not remote or not self.owner._peer_filter(remote[0])
                or len(self.owner._peers) >= self.owner._limits.connections):
            self.close()
            return
        self.owner._peers.add(self)
        self.owner._counts()
        self.transport.set_write_buffer_limits(high=self.owner._limits.write_high,
                                              low=self.owner._limits.write_high // 4)
        self.transport.get_extra_info("socket").setsockopt(
            socket.SOL_SOCKET, socket.SO_SNDBUF, self.owner._limits.socket_send_bytes)
        self._request_timer = asyncio.get_running_loop().call_later(
            self.owner._limits.request_seconds, self.close)

    def data_received(self, data: bytes) -> None:
        if self.closed:
            return
        if self._responded:
            self.close()  # No bodies, pipelining or bidirectional controls.
            return
        if len(self._request) + len(data) > self.owner._limits.header_bytes:
            self.close()
            return
        self._request.extend(data)
        if b"\r\n\r\n" not in self._request:
            return
        received = time.perf_counter_ns() / 1e6
        header, trailing = bytes(self._request).split(b"\r\n\r\n", 1)
        self._request.clear()
        assert self._request_timer is not None
        self._request_timer.cancel()
        self._request_timer = None
        self._responded = True
        if trailing:
            self._reply("400 Bad Request")
            return
        try:
            lines = header.decode("ascii").split("\r\n")
            method, path, version = lines[0].split(" ")
            if version not in ("HTTP/1.0", "HTTP/1.1"):
                raise ValueError("version")
            if (not path.startswith("/") or path.startswith("//")
                    or any(ord(c) < 33 or ord(c) > 126 for c in path)
                    or "#" in path or "\\" in path or re.search(r"%(?![0-9a-fA-F]{2})", path)):
                raise ValueError("request target")
            fields = {}
            for line in lines[1:]:
                name, value = line.split(":", 1)
                if not name or any(c not in "!#$%&'*+-.^_`|~0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
                                   for c in name):
                    raise ValueError("header name")
                if any((ord(c) < 32 and c != "\t") or ord(c) == 127 for c in value):
                    raise ValueError("header value")
                lowered = name.lower()
                if lowered in fields:
                    raise ValueError("duplicate header")
                fields[lowered] = value.strip()
            if version == "HTTP/1.1" and not fields.get("host"):
                raise ValueError("missing host")
            if "transfer-encoding" in fields or fields.get("content-length", "0") != "0":
                raise ValueError("body")
        except (UnicodeError, ValueError):
            self._reply("400 Bad Request")
            return
        if method != "GET":
            self._reply("405 Method Not Allowed", extra="Allow: GET\r\n")
            return
        path = path.split("?", 1)[0]
        base = f"/join/{self.owner._token}/"
        if not path.startswith(base):
            self._reply("404 Not Found")
            return
        route = path[len(base):]
        if route in self.owner._assets:
            content_type, body = self.owner._assets[route]
            self._reply("200 OK", body, content_type)
        elif route == "clock":
            body = json.dumps({"received": received, "sent": time.perf_counter_ns() / 1e6,
                               "session": self.owner._session}).encode("ascii")
            self._reply("200 OK", body, "application/json")
        elif route == "events":
            if sum(peer.streaming for peer in self.owner._peers) >= self.owner._limits.streams:
                self._reply("503 Service Unavailable")
                return
            self.streaming = True
            self.owner._counts()
            assert self.transport is not None
            self.transport.write(("HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                                  + _SECURITY_HEADERS + "Connection: keep-alive\r\n\r\n"
                                  + "retry: 1000\n\n").encode("ascii"))
            if self.owner._current is not None:
                self.offer(self.owner._current)
        else:
            self._reply("404 Not Found")

    def _reply(self, status: str, body: bytes = b"",
               content_type: str = "text/plain; charset=utf-8", extra: str = "") -> None:
        assert self.transport is not None
        self.transport.write((f"HTTP/1.1 {status}\r\nContent-Type: {content_type}\r\n"
                              f"Content-Length: {len(body)}\r\n" + _SECURITY_HEADERS + extra
                              + "Connection: close\r\n\r\n").encode("ascii") + body)
        # Bound a stalled non-SSE response as well as request reception.
        if self._write_timer is None:
            self._write_timer = asyncio.get_running_loop().call_later(
                self.owner._limits.write_seconds, self.close)
        self.transport.close()

    def offer(self, snapshot: _Snapshot) -> None:
        if not self.closed:
            self._pending = snapshot
            self._flush()

    def _flush(self) -> None:
        if self.closed or self.paused or self._pending is None:
            return
        assert self.transport is not None
        snapshot = self._pending
        if self._sent_chart != snapshot.chart:
            self._sent_chart = snapshot.chart
            self.transport.write(snapshot.chart)
            if self.paused or self.closed:
                return
        self._pending = None
        self.transport.write(snapshot.transport)

    def heartbeat(self) -> None:
        if self.streaming and not self.closed and not self.paused and self._pending is None:
            assert self.transport is not None
            self.transport.write(b": heartbeat\n\n")

    def pause_writing(self) -> None:
        self.paused = True
        if self._write_timer is None:
            self._write_timer = asyncio.get_running_loop().call_later(
                self.owner._limits.write_seconds, self.close)

    def resume_writing(self) -> None:
        self.paused = False
        if self._write_timer is not None:
            self._write_timer.cancel()
            self._write_timer = None
        self._flush()

    def close(self) -> None:
        self.closed = True
        for timer in (self._request_timer, self._write_timer):
            if timer is not None:
                timer.cancel()
        self._request_timer = self._write_timer = None
        self._pending = self._sent_chart = None
        self._request.clear()
        self.owner._peers.discard(self)
        self.owner._counts()
        if self.transport is not None:
            self.transport.abort()

    def connection_lost(self, exc: Exception | None) -> None:
        self.close()

    def eof_received(self) -> bool:
        self.close()
        return False
