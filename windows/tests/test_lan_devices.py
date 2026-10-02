"""Real loopback requests for authenticated, independent browser assignments."""
import http.client
import json
from urllib.parse import urlencode, urlsplit

import pytest

from chordcue.devices import DeviceRegistry
from chordcue.lan import LANServer, chart_payload
from chordcue.models import document_with_score
from chordcue.score_models import ScoreIR
from chordcue.transport import StandaloneTransport


@pytest.fixture
def device_server():
    from pathlib import Path
    score = ScoreIR.from_dict(json.loads((Path(__file__).parent / "fixtures" /
                                         "issue1-original.score.json").read_text(encoding="utf-8")))
    document = document_with_score(score)
    server = LANServer(host="127.0.0.1", _addresses=lambda: ["127.0.0.1"])
    url = server.start()[0]
    server.publish(chart_payload(document, (), 1), StandaloneTransport(document).snapshot(1))
    try:
        yield server, url
    finally:
        server.stop()


def post(url, route, payload, *, origin=True, raw=None):
    parsed = urlsplit(url)
    body = raw or json.dumps(payload).encode()
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=3)
    headers = {"Content-Type": "application/json"}
    if origin:
        headers["Origin"] = "http://" + parsed.netloc
    try:
        connection.request("POST", parsed.path + route, body, headers)
        response = connection.getresponse()
        data = response.read()
        return response.status, json.loads(data) if data else None
    finally:
        connection.close()


def test_three_devices_independent_assignment_ack_and_reconnect(device_server):
    server, url = device_server
    registrations = []
    for index in range(3):
        status, registration = post(url, "register", {"clientId": f"original-client-{index}"})
        assert status == 200
        registrations.append(registration)
    for record, part, view in zip(registrations, ("p1", "p2", "p3"), ("tab", "staff", "metronome")):
        server.assign_device(record["clientId"], part, view)
    before = server.devices
    result = server.assign_device(registrations[0]["clientId"], "p2", "staff", "吉他手平板")
    after = server.devices
    assert [{k: v for k, v in item.items() if k != "lastSeenSeconds"} for item in after[1:]] == [
        {k: v for k, v in item.items() if k != "lastSeenSeconds"} for item in before[1:]]
    assert result["assignmentRevision"] > before[0]["assignmentRevision"]
    record = registrations[0]
    status, ack = post(url, "telemetry", {"clientId": record["clientId"], "resumeKey": record["resumeKey"],
                                         "appliedAssignmentRevision": result["assignmentRevision"],
                                         "scoreRevision": 1, "rttMs": 12.5, "clockOffsetMs": 4,
                                         "freshnessMs": 10, "syncStatus": "synchronized"})
    assert status == 200 and ack["view"] == "staff"
    assert server.devices[0]["applied"]
    status, returning = post(url, "register", {"clientId": record["clientId"], "resumeKey": record["resumeKey"]})
    assert status == 200
    assert returning["label"] == "吉他手平板" and returning["partId"] == "p2"
    assert returning["assignmentRevision"] == result["assignmentRevision"]


def test_remote_writes_require_same_origin_and_recovery_credential(device_server):
    server, url = device_server
    assert post(url, "register", {"clientId": "visitor"}, origin=False)[0] == 403
    _, record = post(url, "register", {"clientId": "visitor"})
    assert post(url, "register", {"clientId": "visitor"})[0] == 403
    assert post(url, "telemetry", {"clientId": "visitor", "resumeKey": "wrong"})[0] == 403
    assert post(url, "telemetry", {"clientId": "visitor", "resumeKey": record["resumeKey"],
                                   "partId": "p2", "view": "tab"})[0] == 400
    assert post(url, "register", {}, raw=b'{"clientId":"a","clientId":"b"}')[0] == 400
    assert post(url, "register", {}, raw=b'[' * 2000 + b']' * 2000)[0] == 400
    assert all(item["clientId"] != "a" for item in server.devices)


def test_old_or_future_ack_cannot_confirm_new_assignment(device_server):
    server, url = device_server
    _, record = post(url, "register", {"clientId": "ack-client"})
    assignment = server.assign_device("ack-client", "p1", "tab")
    for revision in (0, assignment["assignmentRevision"] - 1, assignment["assignmentRevision"] + 1):
        assert post(url, "telemetry", {"clientId": "ack-client", "resumeKey": record["resumeKey"],
                                       "appliedAssignmentRevision": revision, "scoreRevision": 1})[0] == 200
        assert not server.devices[0]["applied"]


def test_registry_health_and_connection_generation_are_host_observed():
    now = [10.0]
    registry = DeviceRegistry(clock=lambda: now[0])
    record = registry.register({"clientId": "browser"}, "Test Browser")
    registry.attach("browser", record["resumeKey"], "old")
    registry.attach("browser", record["resumeKey"], "new")
    registry.disconnect("browser", "old")
    assert registry.summaries()[0]["connected"]
    now[0] += 7
    assert not registry.summaries()[0]["connected"]
    assert registry.summaries()[0]["syncStatus"] == "stale"
    registry.disconnect("browser", "new")
    assert registry.summaries()[0]["syncStatus"] == "stale"


def test_authenticated_stream_receives_assignment_before_transport(device_server):
    _, url = device_server
    _, record = post(url, "register", {"clientId": "stream-client"})
    parsed = urlsplit(url)
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=3)
    route = parsed.path + "events?" + urlencode({"clientId": record["clientId"], "resumeKey": record["resumeKey"]})
    try:
        connection.request("GET", route)
        response = connection.getresponse()
        assert response.status == 200
        kinds = []
        while len(kinds) < 3:
            line = response.readline()
            if line.startswith(b"event: "):
                kinds.append(line.strip()[7:].decode())
        assert kinds == ["chart", "assignment", "transport"]
    finally:
        connection.close()


def test_new_host_accepts_old_telemetry_and_negotiates_clock_diagnostics(device_server):
    server, url = device_server
    _, record = post(url, "register", {"clientId": "clock-client"})
    assert record["serverCapabilities"] == ["clockDiagnosticsV1"]
    auth = {"clientId": record["clientId"], "resumeKey": record["resumeKey"]}
    assert post(url, "telemetry", {**auth, "rttMs": 18, "clockOffsetMs": 3e8,
                                   "syncStatus": "synchronized"})[0] == 200
    assert server.devices[0]["clockStatus"] == "unavailable"
    diagnostics = {"version": 1, "status": "valid", "jitterMs": 0.25, "probeAgeMs": 20}
    assert post(url, "telemetry", {**auth, "rttMs": 18, "clockOffsetMs": 3e8,
                                   "clockDiagnostics": diagnostics})[0] == 200
    summary = server.devices[0]
    assert summary["clockOffsetMs"] == 3e8 and summary["rttMs"] == 18
    assert summary["clockStatus"] == "valid" and summary["clockJitterMs"] == 0.25
    assert summary["clockProbeAgeMs"] >= 20


@pytest.mark.parametrize("change", [
    {"version": True}, {"version": 2}, {"probeAgeMs": -1}, {"probeAgeMs": None},
    {"probeAgeMs": 10000}, {"jitterMs": -1}, {"unknown": 1}, {"status": "synchronized"},
])
def test_clock_diagnostics_reject_invalid_or_expired_valid_calibration(change):
    registry = DeviceRegistry(clock=lambda: 10)
    record = registry.register({"clientId": "clock-client"}, "Test")
    payload = {"clientId": "clock-client", "resumeKey": record["resumeKey"], "rttMs": 18,
               "clockOffsetMs": 3e8, "clockDiagnostics": {"version": 1, "status": "valid",
                                                           "jitterMs": None, "probeAgeMs": 1, **change}}
    with pytest.raises(ValueError):
        registry.telemetry(payload)


def test_clock_calibration_age_advances_and_stale_diagnostics_cannot_claim_sync():
    now = [10.0]
    registry = DeviceRegistry(clock=lambda: now[0])
    record = registry.register({"clientId": "clock-client"}, "Test")
    auth = {"clientId": "clock-client", "resumeKey": record["resumeKey"]}
    registry.telemetry({**auth, "rttMs": 18, "clockOffsetMs": 3e8,
                        "clockDiagnostics": {"version": 1, "status": "valid", "jitterMs": None, "probeAgeMs": 1}})
    now[0] += 0.25
    assert registry.summaries()[0]["clockProbeAgeMs"] == 251
    registry.telemetry({**auth, "syncStatus": "synchronized",
                        "clockDiagnostics": {"version": 1, "status": "stale", "jitterMs": None, "probeAgeMs": 10001}})
    assert registry.summaries()[0]["syncStatus"] == "stale"


def test_device_panel_shows_calibration_without_presenting_origin_as_error(qtbot):
    from chordcue.score_ui import DevicePanel
    registry = DeviceRegistry(clock=lambda: 10)
    record = registry.register({"clientId": "clock-client"}, "Test")
    registry.telemetry({"clientId": "clock-client", "resumeKey": record["resumeKey"],
                        "rttMs": 18, "clockOffsetMs": 313276352,
                        "clockDiagnostics": {"version": 1, "status": "valid", "jitterMs": 0.5, "probeAgeMs": 1}})
    panel = DevicePanel(lambda *args: None, lambda error: None)
    qtbot.addWidget(panel)
    panel.update_devices(registry.summaries(), [])
    assert "时钟偏移" not in panel.horizontalHeaderItem(5).text()
    assert "有效" in panel.item(0, 5).text() and "0.5 ms" in panel.item(0, 5).text()
    assert "313276352" not in panel.item(0, 5).text()
    assert "313276352" in panel.item(0, 5).toolTip()
