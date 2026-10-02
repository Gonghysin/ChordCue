"""Actual TCP/HTTP/SSE tests; no Qt application or external network required."""

from concurrent.futures import Future
import asyncio
from dataclasses import replace
import http.client
import json
import socket
import struct
import threading
import time
from urllib.parse import urlsplit

import pytest

from chordcue import lan
from chordcue.lan import LANServer, _Limits, chart_payload, is_local_address
from chordcue.models import ChartDocument, ChordEvent, KeySection, MusicalKey, document_with_score
from chordcue.transport import StandaloneTransport
from score_fixtures import large_score


def wait_for(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate(), "condition did not become true"


def request(url, route="", method="GET"):
    parsed = urlsplit(url)
    client = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=3)
    try:
        client.request(method, parsed.path + route)
        response = client.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        client.close()


class Stream:
    def __init__(self, url, receive_buffer=None):
        parsed = urlsplit(url)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.settimeout(3)
        if receive_buffer:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, receive_buffer)
        self.socket.connect((parsed.hostname, parsed.port))
        self.socket.sendall(f"GET {parsed.path}events HTTP/1.1\r\nHost: localhost\r\n\r\n".encode())
        self.file = self.socket.makefile("rb")
        self.status = int(self.file.readline().split()[1])
        self.headers = {}
        while (line := self.file.readline()) != b"\r\n":
            assert line, "connection closed while reading headers"
            name, value = line.decode().strip().split(":", 1)
            self.headers[name] = value.strip()
        if self.status == 200:
            assert self.block() == b"retry: 1000"

    def block(self):
        lines = []
        while True:
            line = self.file.readline()
            if not line:
                raise EOFError("SSE disconnected")
            if line in (b"\n", b"\r\n"):
                return b"\n".join(lines)
            lines.append(line.rstrip(b"\r\n"))

    def event(self):
        while True:
            block = self.block()
            if block.startswith(b"event: "):
                kind, data = block.split(b"\n", 1)
                return kind[7:].decode(), json.loads(data[6:])

    def close(self):
        self.file.close()
        self.socket.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


@pytest.fixture
def server():
    instance = LANServer(host="127.0.0.1", _addresses=lambda: ["127.0.0.1"])
    links = instance.start()
    try:
        yield instance, links[0]
    finally:
        instance.stop()


def state(revision=1):
    document = ChartDocument(name="局域网原创测试", bars=32,
                             events=(ChordEvent(1, 1, 0, "C"),))
    return (chart_payload(document, (KeySection(1, MusicalKey(0)),), revision),
            StandaloneTransport(document).snapshot(revision))


def raw_socket(url):
    parsed = urlsplit(url)
    return socket.create_connection((parsed.hostname, parsed.port), timeout=3)


def closed(sock):
    try:
        return sock.recv(1) == b""
    except (ConnectionResetError, ConnectionAbortedError):
        return True


def inspect_peers(server):
    """Read loop-owned diagnostics on the loop, never race its peer set."""
    result = Future()

    def inspect():
        result.set_result([
            {"paused": peer.paused,
             "pending": peer._pending,
             "buffer": peer.transport.get_write_buffer_size()}
            for peer in server._peers if peer.streaming
        ])

    server._loop.call_soon_threadsafe(inspect)
    return result.result(3)


def test_chart_shape_sorting_fractional_position_keys_and_project_tail():
    document = ChartDocument(name="原创练习", bars=37, meter=3,
                             events=(ChordEvent(2, 5, 1199, "Am"),
                                     ChordEvent(1, 1, 0, "C")))
    result = chart_payload(document, [KeySection(5, MusicalKey(9, True)),
                                      KeySection(1, MusicalKey(0))], 8)
    assert result["revision"] == 8
    assert result["bars"] == 37
    assert result["meter"] == "3/4"
    assert result["events"][0]["number"] == "1"
    assert result["events"][1] == {
        "id": 2, "bar": 5, "beat": 2, "division": 1, "tick": 239,
        "symbol": "Am", "number": "6m",
    }
    assert result["sections"] == [
        {"bar": 1, "root": 0, "minor": False, "family": 0},
        {"bar": 5, "root": 9, "minor": True, "family": 0},
    ]
    with pytest.raises(ValueError):
        chart_payload(document, [], True)
    with pytest.raises(ValueError):
        chart_payload(document, [KeySection(38, MusicalKey(0))])


@pytest.mark.parametrize("address", [
    "10.0.0.1", "172.16.0.1", "172.31.255.255", "192.168.1.1", "127.42.1.1",
    "169.254.1.1", "::1", "fe80::1%12", "febf::1234", "fc00::1", "fdff::1",
    "::ffff:192.168.1.1", "::ffff:127.0.0.1",
])
def test_explicit_private_ranges(address):
    assert is_local_address(address)


@pytest.mark.parametrize("address", [
    "0.0.0.0", "100.64.0.1", "192.0.0.1", "192.0.2.1", "198.51.100.1",
    "203.0.113.1", "8.8.8.8", "172.15.255.255", "172.32.0.1", "224.0.0.1",
    "240.0.0.1", "255.255.255.255", "::", "2001:db8::1", "fec0::1", "ff02::1",
    "::ffff:8.8.8.8", "not-an-address", "10.1.1.999",
])
def test_nonprivate_and_reserved_ranges_rejected(address):
    assert not is_local_address(address)


def test_constructor_and_disabled_publish_do_not_open_sockets(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("network activity before start")

    monkeypatch.setattr(socket, "socket", forbidden)
    instance = LANServer()
    instance.publish(*state())
    instance.stop()
    assert not instance.enabled


def test_http_resources_clock_and_security_headers(server):
    instance, url = server
    assert instance.start() == [url]
    for route, content_type, expected in [
        ("", "text/html; charset=utf-8", b"ChordCue"),
        ("Metronome.js", "text/javascript; charset=utf-8", b"ChordCueMetronome"),
    ]:
        status, headers, body = request(url, route)
        assert status == 200 and expected in body
        assert headers["Content-Type"] == content_type
        assert headers["Cache-Control"] == "no-store"
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["Referrer-Policy"] == "no-referrer"
        assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
        assert "Access-Control-Allow-Origin" not in headers
        assert headers["Connection"] == "close"
    before = time.perf_counter_ns() / 1e6
    status, headers, body = request(url, "clock?cache=1")
    after = time.perf_counter_ns() / 1e6
    value = json.loads(body)
    assert status == 200 and headers["Content-Type"] == "application/json"
    assert before <= value["received"] <= value["sent"] <= after
    assert len(value["session"]) == 32


def test_wrong_tokens_unknown_routes_and_readonly_methods(server):
    _, url = server
    wrong = url.replace("/join/", "/join/wrong")
    for route in ("", "events", "clock", "Metronome.js"):
        assert request(wrong, route)[0] == 404
    assert request(url, "../clock")[0] == 404
    assert request(url, "unknown")[0] == 404
    for method in ("POST", "PUT", "DELETE", "OPTIONS", "HEAD"):
        status, headers, body = request(url, method=method)
        assert status == 405 and headers["Allow"] == "GET" and not body


@pytest.mark.parametrize("request_template", [
    "GET {path}clock HTTP/2.0\r\nHost: test\r\n\r\n",
    "GET {path}clock HTTP/1.1\r\n\r\n",
    "GET {path}clock HTTP/1.1\r\nHost: test\r\nHost: duplicate\r\n\r\n",
    "GET {path}clock HTTP/1.1\r\nHost: test\r\nContent-Length: 1\r\n\r\nx",
    "GET {path}clock HTTP/1.1\r\nHost: test\r\nTransfer-Encoding: chunked\r\n\r\n",
    "GET {path}clock HTTP/1.1\r\nHost: test\r\nContent-Length: 0\r\nContent-Length: 1\r\n\r\n",
    "GET {path}clock HTTP/1.1\r\nHost: test\r\n folded: value\r\n\r\n",
    "GET http://localhost{path}clock HTTP/1.1\r\nHost: test\r\n\r\n",
    "GET /{path}clock HTTP/1.1\r\nHost: test\r\n\r\n",
    "GET {path}clock#fragment HTTP/1.1\r\nHost: test\r\n\r\n",
    "GET {path}clock?x=%zz HTTP/1.1\r\nHost: test\r\n\r\n",
    "GET {path}clock?x=\x00 HTTP/1.1\r\nHost: test\r\n\r\n",
    "GET {path}clock HTTP/1.1\r\nHost: test\r\nX-Bad: \x7f\r\n\r\n",
    "GET {path}clock HTTP/1.1\r\nHost: test\r\n\r\nGET {path}events HTTP/1.1\r\nHost: test\r\n\r\n",
])
def test_malformed_and_ambiguous_requests_rejected(server, request_template):
    instance, url = server
    with raw_socket(url) as client:
        client.sendall(request_template.format(path=urlsplit(url).path).encode())
        assert client.recv(4096).startswith(b"HTTP/1.1 400")
    assert request(url, "clock")[0] == 200
    assert instance.enabled


def test_sse_initial_reconnect_latest_snapshot_and_revision_order(server):
    instance, url = server
    chart, transport = state()
    instance.publish(chart, transport)
    chart["name"] = "caller mutated after publication"
    transport["bar"] = 99
    with Stream(url) as stream:
        assert stream.headers["Content-Type"] == "text/event-stream"
        assert stream.headers["Referrer-Policy"] == "no-referrer"
        kind, initial_chart = stream.event()
        assert kind == "chart" and initial_chart["name"] == "局域网原创测试"
        assert stream.event()[1]["bar"] == 1
        chart, transport = state()
        transport["bar"] = 2
        instance.publish(chart, transport)
        assert stream.event() == ("transport", transport)
        chart, transport = state(2)
        chart["meter"] = transport["meter"] = "3/4"
        instance.publish(chart, transport)
        assert stream.event() == ("chart", chart)
        assert stream.event() == ("transport", transport)
    wait_for(lambda: instance.viewers == 0)
    with Stream(url) as reconnect:
        assert reconnect.event() == ("chart", chart)
        assert reconnect.event() == ("transport", transport)
    wait_for(lambda: instance.connections == 0)


def test_prepared_score_serializes_once_and_reconnect_gets_latest_revision(server, monkeypatch):
    instance, url = server
    encode = instance._event
    chart_encodes = []

    def counted(name, value, maximum):
        if name == "chart":
            chart_encodes.append(value["revision"])
        return encode(name, value, maximum)

    monkeypatch.setattr(instance, "_event", counted)
    chart, transport = state()
    for bar in range(1, 11):
        transport = {**transport, "bar": bar}
        instance.publish_prepared(chart, transport)
    assert chart_encodes == [1]
    with Stream(url) as stream:
        assert stream.event() == ("chart", chart)
        assert stream.event() == ("transport", transport)
    following, sample = state(2)
    instance.publish_prepared(following, sample)
    assert chart_encodes == [1, 2]
    with Stream(url) as reconnect:
        assert reconnect.event() == ("chart", following)
        assert reconnect.event() == ("transport", sample)


def test_valid_score_over_old_budget_streams_complete_and_reconnects(server):
    instance, url = server
    document = document_with_score(large_score())
    chart = chart_payload(document, (), 5)
    playback = StandaloneTransport(document)
    chart["route"] = playback.plan.to_dict()
    sample = playback.snapshot(5, include_route=False)
    assert len(json.dumps(chart, separators=(",", ":")).encode()) > 2 * 1024 * 1024
    try:
        instance.publish_prepared(chart, sample)
        with Stream(url) as stream:
            assert stream.event() == ("chart", chart)
            assert stream.event() == ("transport", sample)
            for position in (1, 2, 3):
                latest = {**sample, "bar": position}
                instance.publish_prepared(chart, latest)
                assert stream.event() == ("transport", latest)
        with Stream(url) as reconnect:
            assert reconnect.event() == ("chart", chart)
            assert reconnect.event() == ("transport", latest)
        instance.stop()
        assert instance._prepared_chart is instance._prepared_event is instance._prepared_identity is None
    finally:
        playback.close()


def test_chunked_frame_cannot_be_interleaved_and_coalesces_latest_transport():
    async def check():
        owner = LANServer(_limits=replace(_Limits(), write_high=32, write_seconds=0.2))
        owner._devices.set_parts({"p1": frozenset(("staff", "tab"))}, 1)
        owner._devices.register({"clientId": "chunk-test"}, "test")
        peer = lan._Peer(owner)
        owner._peers.add(peer)
        peer.streaming = True
        peer.client_id = "chunk-test"
        writes = []

        class Buffer:
            def write(self, data):
                writes.append(bytes(data))
                if len(writes) == 1:
                    peer.pause_writing()

            def abort(self):
                pass

        peer.transport = Buffer()
        chart, sample = state()
        wire = owner._event("chart", chart, owner._limits.chart_bytes)
        snapshot = lan._Snapshot(wire, owner._event("transport", sample, 65536), b"identity")
        peer.offer(snapshot)
        await asyncio.sleep(0)
        assert peer._writing_chart is not None
        owner._devices.assign("chunk-test", "p1", "tab")
        updated = lan._Snapshot(wire, owner._event("transport", {**sample, "bar": 8}, 65536), b"identity")
        peer.offer(updated)
        peer.send_assignment()
        peer.heartbeat()
        assert len(writes) == 1
        peer.resume_writing()
        for _ in range(100):
            if peer._pending is None:
                break
            await asyncio.sleep(0)
        blocks = b"".join(writes).split(b"\n\n")
        assert blocks[0] == wire.rstrip(b"\n")
        assert blocks[1].startswith(b"event: assignment\ndata: ")
        assert json.loads(blocks[1].split(b"data: ", 1)[1])["view"] == "tab"
        assert json.loads(blocks[2].split(b"data: ", 1)[1])["bar"] == 8
        assert peer._writing_chart is None and peer._sent_chart_identity == b"identity"
        assert max(map(len, writes[:-2])) <= 32
        peer.close()

    asyncio.run(check())


def test_inflight_old_frame_budget_releases_slow_history():
    owner = LANServer(_limits=replace(_Limits(), inflight_chart_bytes=100))
    first, second, incoming = [lan._Peer(owner) for _ in range(3)]
    owner._peers.update((first, second, incoming))
    first._writing_chart, second._writing_chart = b"a" * 40, b"b" * 40
    first._chart_started, second._chart_started = 1, 2
    assert owner._reserve_chart(incoming, b"c" * 40)
    assert first.closed and first._writing_chart is None
    assert not second.closed


def test_progressing_chart_may_take_longer_than_one_stall_deadline():
    async def check():
        limits = replace(_Limits(), write_high=32, write_seconds=0.08)
        owner = LANServer(_limits=limits)
        peer = lan._Peer(owner)
        owner._peers.add(peer)
        writes = []

        class Buffer:
            def write(self, data):
                writes.append(bytes(data))
                peer.pause_writing()
                asyncio.get_running_loop().call_later(0.004, peer.resume_writing)

            def abort(self):
                pass

        peer.transport = Buffer()
        chart, sample = state()
        chart["name"] = "x" * 2000
        wire = owner._event("chart", chart, limits.chart_bytes)
        started = time.monotonic()
        peer.offer(lan._Snapshot(wire, owner._event("transport", sample, 65536), b"slow-progress"))
        for _ in range(500):
            if peer._pending is None or peer.closed:
                break
            await asyncio.sleep(0.005)
        assert time.monotonic() - started > limits.write_seconds
        assert not peer.closed and peer._pending is None
        assert b"".join(writes).startswith(wire)
        assert max(map(len, writes[:-1])) <= 32
        peer.close()

    asyncio.run(check())


def test_score_chart_does_not_duplicate_legacy_display_adapter():
    score = large_score()
    document = replace(document_with_score(score), events=(ChordEvent(1, 1, 0, "C"),))
    chart = chart_payload(document, (KeySection(1, MusicalKey(0)),))
    assert chart["events"] == chart["sections"] == []
    assert chart["score"] == score.to_dict()


def test_score_and_route_budgets_are_independent_of_total_chart_limit(monkeypatch):
    monkeypatch.setattr(lan, "MAX_SCORE_BYTES", 128)
    maximum = _Limits().chart_bytes
    with pytest.raises(ValueError, match="score payload exceeds"):
        LANServer._event("chart", {"score": {"detail": "x" * 128}}, maximum)
    with pytest.raises(ValueError, match="route payload exceeds"):
        LANServer._event("chart", {"score": {}, "route": {"detail": "x" * 128}}, maximum)
    assert b'"route"' in LANServer._event("chart", {"score": {"small": 1}, "route": {"small": 2}}, maximum)


def test_sse_heartbeat_does_not_replace_pending_state():
    instance = LANServer(host="127.0.0.1", _addresses=lambda: ["127.0.0.1"],
                         _limits=replace(_Limits(), heartbeat_seconds=0.03))
    url = instance.start()[0]
    try:
        with Stream(url) as stream:
            assert stream.block() == b": heartbeat"
            instance.publish(*state())
            assert stream.event()[0] == "chart"
            assert stream.event()[0] == "transport"
    finally:
        instance.stop()


def test_sse_rejects_followup_request(server):
    instance, url = server
    with Stream(url) as stream:
        stream.socket.sendall(b"GET / HTTP/1.1\r\nHost: test\r\n\r\n")
        with pytest.raises((EOFError, ConnectionResetError, ConnectionAbortedError)):
            stream.block()
    wait_for(lambda: instance.connections == 0)


def test_publish_rejects_mismatch_nonfinite_and_oversize_payload(server, monkeypatch):
    instance, _ = server
    chart, transport = state()
    with pytest.raises(ValueError, match="revisions must match"):
        instance.publish(chart, {**transport, "revision": 2})
    with pytest.raises(ValueError):
        instance.publish(chart, {**transport, "sampleTime": float("nan")})
    monkeypatch.setattr(instance, "_limits", replace(instance._limits, chart_bytes=4096))
    with pytest.raises(ValueError, match="payload exceeds"):
        instance.publish({**chart, "name": "x" * 4097}, transport)
    with pytest.raises(ValueError, match="payload exceeds"):
        instance.publish(chart, {**transport, "extra": "x" * (_Limits().transport_bytes + 1)})


def test_exact_total_header_limit(server):
    _, url = server
    prefix = f"GET {urlsplit(url).path}clock HTTP/1.1\r\nHost: test\r\nX-Pad: ".encode()
    suffix = b"\r\n\r\n"
    with raw_socket(url) as client:
        client.sendall(prefix + b"a" * (8192 - len(prefix) - len(suffix)) + suffix)
        assert client.recv(4096).startswith(b"HTTP/1.1 200")
    with raw_socket(url) as client:
        client.sendall(prefix + b"a" * (8193 - len(prefix) - len(suffix)) + suffix)
        assert closed(client)


def test_slow_request_timeout_is_total_not_reset_per_fragment():
    instance = LANServer(host="127.0.0.1", _addresses=lambda: ["127.0.0.1"],
                         _limits=replace(_Limits(), request_seconds=0.2))
    url = instance.start()[0]
    try:
        with raw_socket(url) as client:
            client.sendall(b"GET /")
            time.sleep(0.1)
            client.sendall(b"join/")
            started = time.monotonic()
            assert closed(client)
            assert time.monotonic() - started < 0.18
        wait_for(lambda: instance.connections == 0)
    finally:
        instance.stop()


def test_default_connection_limit_includes_incomplete_requests(server):
    instance, url = server
    clients = []
    try:
        for _ in range(40):
            clients.append(raw_socket(url))
        wait_for(lambda: instance.connections == 40)
        with raw_socket(url) as overflow:
            assert closed(overflow)
        clients.pop().close()
        wait_for(lambda: instance.connections == 39)
        assert request(url, "clock")[0] == 200
    finally:
        for client in clients:
            client.close()


def test_default_sse_limit_and_slot_recovery(server):
    instance, url = server
    clients = []
    try:
        for _ in range(24):
            clients.append(Stream(url))
        assert instance.viewers == 24
        with Stream(url) as overflow:
            assert overflow.status == 503
        assert request(url, "clock")[0] == 200
        clients.pop().close()
        wait_for(lambda: instance.viewers == 23)
        with Stream(url) as replacement:
            assert replacement.status == 200
    finally:
        for client in clients:
            client.close()


def test_peer_filter_checked_before_http_parsing():
    seen = []

    def reject(address):
        seen.append(address)
        return is_local_address("198.51.100.1")

    instance = LANServer(host="127.0.0.1", _addresses=lambda: ["127.0.0.1"],
                         _peer_filter=reject)
    url = instance.start()[0]
    try:
        with raw_socket(url) as client:
            assert closed(client)
        assert seen == ["127.0.0.1"]
        assert instance.connections == 0
    finally:
        instance.stop()


def test_slow_client_is_bounded_coalesces_latest_and_disconnects():
    limits = replace(_Limits(), write_high=1024, socket_send_bytes=4096,
                     write_seconds=1.2, heartbeat_seconds=0.05)
    instance = LANServer(host="127.0.0.1", _addresses=lambda: ["127.0.0.1"], _limits=limits)
    url = instance.start()[0]
    try:
        with Stream(url, receive_buffer=1024):
            chart, transport = state()
            chart["name"] = "x" * 600_000
            # Windows loopback can absorb several writes despite SO_RCVBUF;
            # fill the real TCP window before testing persistent backpressure.
            for revision in range(1, 101):
                instance.publish({**chart, "revision": revision},
                                 {**transport, "revision": revision})
                peers = inspect_peers(instance)
                if peers and peers[0]["paused"]:
                    time.sleep(0.03)
                    peers = inspect_peers(instance)
                    if peers and peers[0]["paused"]:
                        break
            assert peers and peers[0]["paused"]
            for revision in range(101, 121):
                instance.publish({**chart, "revision": revision},
                                 {**transport, "revision": revision})

            def has_latest():
                peers = inspect_peers(instance)
                return bool(peers and peers[0]["pending"]
                            and b'"revision":120,' in peers[0]["pending"].transport)

            wait_for(has_latest)
            peer = inspect_peers(instance)[0]
            assert peer["paused"]
            assert peer["buffer"] <= 2 * 602_000 + limits.write_high
            # A stalled peer does not block the clock route or a healthy SSE viewer.
            assert request(url, "clock")[0] == 200
            instance.publish(*state(121))
            with Stream(url) as fast:
                assert fast.event()[1]["revision"] == 121
                assert fast.event()[1]["revision"] == 121
            wait_for(lambda: instance.viewers == 0)
    finally:
        instance.stop()


def test_new_session_shutdown_disconnects_all_and_releases_thread(server):
    instance, url = server
    session = json.loads(request(url, "clock")[2])["session"]
    pending = raw_socket(url)
    stream = Stream(url)
    thread = instance._thread
    try:
        instance.stop()
        assert not instance.enabled and not thread.is_alive()
        assert instance.connections == instance.viewers == 0
        assert closed(pending)
        with pytest.raises((EOFError, ConnectionResetError, ConnectionAbortedError)):
            stream.block()
        instance.stop()
        with pytest.raises(OSError):
            raw_socket(url)
        restarted = instance.start()[0]
        assert urlsplit(restarted).path != urlsplit(url).path
        assert json.loads(request(restarted, "clock")[2])["session"] != session
        assert request(restarted.replace(urlsplit(restarted).path, urlsplit(url).path), "clock")[0] == 404
        # No chart/state from the previous session is sent on a fresh start.
        assert instance._current is None
    finally:
        pending.close()
        stream.close()


def test_bind_failure_propagates_without_thread_leak():
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        instance = LANServer(host="127.0.0.1", port=occupied.getsockname()[1],
                             _addresses=lambda: ["127.0.0.1"])
        before = {thread.ident for thread in threading.enumerate()}
        with pytest.raises(RuntimeError, match="could not start"):
            instance.start()
        assert not instance.enabled and instance._thread is None
        assert {thread.ident for thread in threading.enumerate()} == before
        instance.stop()


def test_missing_assets_fail_before_listener_creation(monkeypatch, tmp_path):
    monkeypatch.setattr(lan, "resource_path", lambda name: tmp_path / name)
    instance = LANServer()
    with pytest.raises(FileNotFoundError):
        instance.start()
    assert not instance.enabled and instance._thread is None


def test_default_listener_accepts_ipv4_and_ipv6_loopback_when_supported():
    addresses = ["127.0.0.1", "::1"] if socket.has_ipv6 else ["127.0.0.1"]
    instance = LANServer(_addresses=lambda: addresses)
    urls = instance.start()
    try:
        for url in urls:
            assert request(url, "clock")[0] == 200
        assert any("127.0.0.1" in url for url in urls)
    finally:
        instance.stop()


def test_runtime_failure_reaches_gui_caller_and_still_releases_thread():
    instance = LANServer(host="127.0.0.1", _addresses=lambda: ["127.0.0.1"])
    url = instance.start()[0]
    thread = instance._thread

    def fail():
        raise RuntimeError("injected event-loop failure")

    instance._loop.call_soon_threadsafe(fail)
    wait_for(lambda: not instance.enabled)
    with pytest.raises(RuntimeError, match="injected event-loop failure"):
        instance.publish(*state())
    with pytest.raises(RuntimeError, match="injected event-loop failure"):
        instance.stop()
    assert not thread.is_alive() and instance._thread is None
    with pytest.raises(OSError):
        raw_socket(url)
    # The acknowledged failure must not prevent an explicit new session.
    restarted = instance.start()[0]
    try:
        assert request(restarted, "clock")[0] == 200
    finally:
        instance.stop()


def on_loop(server, callback):
    """Run fault injection/transport inspection on the owning event loop."""
    result = Future()

    def run():
        try:
            result.set_result(callback())
        except BaseException as error:
            result.set_exception(error)

    server._loop.call_soon_threadsafe(run)
    return result.result(3)


@pytest.mark.skipif(lan.sys.platform != "win32", reason="Windows proactor close callback")
def test_proactor_socket_shutdown_failure_releases_peer_and_preserves_other_streams(server):
    instance, url = server
    instance.publish(*state())
    with Stream(url) as healthy, Stream(url) as victim:
        for stream in (healthy, victim):
            assert stream.event()[0] == "chart"
            assert stream.event()[0] == "transport"
        victim_port = victim.socket.getsockname()[1]

        def interrupt_shutdown():
            peer = next(peer for peer in instance._peers
                        if peer.transport.get_extra_info("peername")[1] == victim_port)
            transport = peer.transport
            assert isinstance(instance._loop, asyncio.ProactorEventLoop)
            listener, original = transport._server, transport._sock
            # Cancel its real I/O first, then make CPython's native socket.shutdown
            # raise WSAENOTCONN. This exercises the actual Handle error context and
            # the same interrupted close tail as an intermittent WSAECONNRESET.
            disconnected = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            transport.abort()
            transport._sock = disconnected
            original.close()
            return transport, listener, original, disconnected

        transport, listener, original, disconnected = on_loop(instance, interrupt_shutdown)
        try:
            details = on_loop(instance, lambda: (
                transport._sock, transport._server, transport._called_connection_lost,
                len(listener._clients), instance.connections, instance._failure))
            assert details == (None, None, True, 1, 1, None)
            assert original.fileno() == disconnected.fileno() == -1
            # The extra close already queued by abort/connection_lost is harmless.
            on_loop(instance, lambda: transport._call_connection_lost(None))
            assert instance.enabled and instance.viewers == 1
            instance.publish(*state(2))
            assert healthy.event()[1]["revision"] == 2
            assert healthy.event()[1]["revision"] == 2
            with Stream(url) as reconnected:
                assert reconnected.event()[1]["revision"] == 2
                assert reconnected.event()[1]["revision"] == 2
                instance.publish(*state(3))
                for stream in (healthy, reconnected):
                    assert stream.event()[1]["revision"] == 3
                    assert stream.event()[1]["revision"] == 3
            thread = instance._thread
            instance.stop()
            assert not thread.is_alive() and instance._thread is None
            assert len(listener._clients) == 0
        finally:
            # Also permit the pre-fix implementation to finish shutdown when this
            # regression fails, rather than leaving its non-daemon thread blocked.
            disconnected.close()
            if not transport._called_connection_lost and instance._loop is not None:
                on_loop(instance, lambda: transport._call_connection_lost(None))


def test_real_tcp_resets_leave_other_streams_live_and_allow_reconnect(server):
    instance, url = server
    instance.publish(*state())
    with Stream(url) as healthy:
        assert healthy.event()[0] == "chart"
        assert healthy.event()[0] == "transport"
        for revision in range(2, 12):
            reset = Stream(url)
            assert reset.event()[1]["revision"] == revision - 1
            assert reset.event()[1]["revision"] == revision - 1
            reset.file.close()
            layout = "HH" if lan.sys.platform == "win32" else "ii"
            reset.socket.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                                    struct.pack(layout, 1, 0))
            reset.socket.close()  # Abort with an actual TCP RST instead of FIN.
            wait_for(lambda: instance.viewers == 1)
            instance.publish(*state(revision))
            assert healthy.event()[1]["revision"] == revision
            assert healthy.event()[1]["revision"] == revision
            assert instance.enabled and instance._failure is None
        with Stream(url) as reconnected:
            assert reconnected.event()[1]["revision"] == 11
            assert reconnected.event()[1]["revision"] == 11
        thread = instance._thread
        instance.stop()
        assert not thread.is_alive() and instance.connections == instance.viewers == 0


@pytest.mark.skipif(lan.sys.platform != "win32", reason="Windows proactor close callback")
def test_proactor_shutdown_error_does_not_hide_protocol_failure():
    instance = LANServer(host="127.0.0.1", _addresses=lambda: ["127.0.0.1"])
    url = instance.start()[0]
    thread = instance._thread
    with Stream(url) as stream:
        port = stream.socket.getsockname()[1]

        def interrupt_shutdown():
            peer = next(peer for peer in instance._peers
                        if peer.transport.get_extra_info("peername")[1] == port)
            transport = peer.transport
            def broken_protocol(exc):
                raise RuntimeError("protocol close bug")

            peer.connection_lost = broken_protocol
            original = transport._sock
            disconnected = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            transport.abort()
            transport._sock = disconnected
            original.close()
            return transport, transport._server, original, disconnected

        transport, listener, original, disconnected = on_loop(instance, interrupt_shutdown)
        wait_for(lambda: not instance.enabled)
        failure = instance._failure
        assert isinstance(failure, RuntimeError)
        assert str(failure) == "protocol close bug"
        assert original.fileno() == disconnected.fileno() == -1
        assert transport._sock is None and transport._server is None
        assert transport._called_connection_lost and len(listener._clients) == 0
        with pytest.raises(RuntimeError, match="protocol close bug") as raised:
            instance.publish(*state())
        assert raised.value.__cause__ is failure
        with pytest.raises(RuntimeError, match="protocol close bug") as raised:
            instance.stop()
        assert raised.value.__cause__ is failure
        assert not thread.is_alive() and instance._thread is None


@pytest.mark.parametrize("source", ["callback", "task", "foreign-close", "protocol"])
def test_connection_errors_outside_owned_socket_close_remain_fatal(source):
    instance = LANServer(host="127.0.0.1", _addresses=lambda: ["127.0.0.1"])
    instance.start()
    thread = instance._thread

    def fail():
        raise ConnectionResetError(f"fatal {source}")

    async def fail_task():
        fail()

    if source == "task":
        instance._loop.call_soon_threadsafe(lambda: asyncio.create_task(fail_task()))
    elif source in ("foreign-close", "protocol"):
        # Same error class plus plausible context keys must not make a task or
        # unrelated protocol's error recoverable without the owned close Handle.
        context = {"exception": ConnectionResetError(f"fatal {source}"),
                   "protocol": asyncio.Protocol(), "transport": object()}
        instance._loop.call_soon_threadsafe(instance._loop.call_exception_handler, context)
    else:
        instance._loop.call_soon_threadsafe(fail)
    wait_for(lambda: not instance.enabled)
    with pytest.raises(RuntimeError, match=f"fatal {source}"):
        instance.publish(*state())
    with pytest.raises(RuntimeError, match=f"fatal {source}"):
        instance.stop()
    assert not thread.is_alive() and instance._thread is None


@pytest.mark.skipif(lan.sys.platform != "win32", reason="Windows adapter enumeration")
def test_windows_adapter_enumeration_returns_only_explicit_lan_addresses():
    addresses = lan.local_addresses()
    assert addresses and all(is_local_address(address) for address in addresses)
