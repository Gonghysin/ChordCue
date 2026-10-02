"""Clock tests never wait for wall time or import Qt."""

from dataclasses import replace
import math

import pytest

from chordcue.models import ChartDocument, ChordEvent, LoopRange, PPQ
from chordcue.transport import HostAdapter, StandaloneTransport, TransportController


class FakeClock:
    def __init__(self):
        self.ns = 12_345_678_000_000
        self.reads = 0

    def __call__(self):
        self.reads += 1
        return self.ns

    def advance(self, ns):
        self.ns += ns


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def transport(clock):
    return StandaloneTransport(ChartDocument(), clock)


def position(sample):
    meter = int(sample["meter"].split("/")[0])
    return (
        (sample["bar"] - 1) * meter
        + sample["beat"] - 1
        + (sample["division"] - 1) / 4
        + sample["tick"] / PPQ
    )


def assert_phase(sample, expected):
    # Wire samples are floored to one tick; all internal phase remains intact.
    assert expected - 1 / PPQ - 1e-10 < position(sample) <= expected + 1e-10


def begin(transport, clock):
    transport.play()
    clock.advance(400_000_000)


def test_initial_state_legacy_wire_and_read_only_protocol(transport, clock):
    sample = transport.snapshot(revision=42)
    assert sample == {
        "revision": 42,
        "sampleTime": clock.ns / 1_000_000,
        "readMs": 0.0,
        "valid": True,
        "precise": True,
        "rate": 2.0,
        "bar": 1,
        "beat": 1,
        "division": 1,
        "tick": 0,
        "bpm": 120.0,
        "meter": "4/4",
        "playing": False,
        "discontinuity": 0,
        "playback": {"endBeat": 64, "loop": None},
    }
    assert isinstance(transport, HostAdapter)
    assert isinstance(transport, TransportController)

    class ReadOnlyHost:
        document = ChartDocument()

        def snapshot(self, revision=1):
            return sample

    assert isinstance(ReadOnlyHost(), HostAdapter)
    assert not isinstance(ReadOnlyHost(), TransportController)
    reads = clock.reads
    transport.snapshot()
    assert clock.reads == reads + 1


def test_play_prepares_400ms_without_postdating_sample(transport, clock):
    now_ms = clock.ns / 1_000_000
    transport.play()
    sample = transport.snapshot()
    assert sample["playing"]
    assert sample["sampleTime"] == now_ms
    assert sample["playback"]["startTime"] == now_ms + 400
    assert position(sample) == 0
    epoch = sample["discontinuity"]
    clock.advance(399_999_999)
    assert position(transport.snapshot()) == 0
    clock.advance(1)
    assert position(transport.snapshot()) == 0
    clock.advance(125_000_000)
    assert position(transport.snapshot()) == 0.25
    assert transport.snapshot()["discontinuity"] == epoch


def test_pause_during_preparation_and_resume_preserve_position(transport, clock):
    transport.seek(3, 2.125)
    transport.play()
    clock.advance(200_000_000)
    transport.pause()
    paused = transport.snapshot()
    assert position(paused) == 9.125
    assert not paused["playing"]
    assert "startTime" not in paused["playback"]
    clock.advance(1_000_000_000)
    assert position(transport.snapshot()) == 9.125
    transport.play()
    resumed = transport.snapshot()
    assert resumed["discontinuity"] > paused["discontinuity"]
    assert resumed["playback"]["startTime"] == resumed["sampleTime"] + 400
    clock.advance(650_000_000)
    assert position(transport.snapshot()) == 9.625


def test_pause_running_then_stop_always_returns_to_project_start(transport, clock):
    begin(transport, clock)
    clock.advance(987_654_321)
    transport.pause()
    paused = transport.snapshot()
    clock.advance(999_999_999)
    assert position(transport.snapshot()) == position(paused)
    transport.set_loop(LoopRange(3, 5))
    transport.stop()
    assert position(transport.snapshot()) == 0
    assert not transport.snapshot()["playing"]
    transport.play()
    assert position(transport.snapshot()) == 8


def test_seek_keeps_fractional_position_and_increments_epoch(transport, clock):
    begin(transport, clock)
    clock.advance(1_000_000_000)
    old = transport.snapshot()
    transport.seek(2, 3.375)
    sample = transport.snapshot()
    assert (sample["bar"], sample["beat"], sample["division"], sample["tick"]) == (2, 3, 2, 120)
    assert sample["playing"]
    assert sample["discontinuity"] > old["discontinuity"]
    assert sample["playback"]["startTime"] == sample["sampleTime"]
    clock.advance(125_000_000)
    assert position(transport.snapshot()) == 6.625


@pytest.mark.parametrize("bar,beat", [
    (True, 1), (1.5, 1), (0, 1), (18, 1),
    (1, True), (1, "1"), (1, 0), (1, 5),
    (1, math.nan), (1, math.inf), (1, -math.inf), (1, 10**1000),
    (17, 1.01),
])
def test_invalid_seek_is_atomic(transport, bar, beat):
    before = transport.snapshot()
    with pytest.raises(ValueError):
        transport.seek(bar, beat)
    assert transport.snapshot() == before


def test_exclusive_end_stops_exactly_and_play_restarts(clock):
    transport = StandaloneTransport(ChartDocument(bars=2, meter=3), clock)
    begin(transport, clock)
    epoch = transport.snapshot()["discontinuity"]
    clock.advance(3_000_000_000)
    sample = transport.snapshot()
    assert (sample["bar"], sample["beat"], sample["division"], sample["tick"]) == (3, 1, 1, 0)
    assert not sample["playing"]
    assert sample["playback"] == {"endBeat": 6, "loop": None}
    assert sample["discontinuity"] == epoch
    clock.advance(9_000_000_000)
    assert position(transport.snapshot()) == 6
    transport.play()
    restarted = transport.snapshot()
    assert position(restarted) == 0
    assert restarted["playing"]
    assert restarted["playback"]["startTime"] == restarted["sampleTime"] + 400
    transport.seek(3)
    assert not transport.snapshot()["playing"]
    transport.play()
    assert position(transport.snapshot()) == 0


def test_hundred_loops_are_half_open_and_never_change_epoch(clock):
    transport = StandaloneTransport(ChartDocument(loop=LoopRange(3, 5)), clock)
    begin(transport, clock)
    epoch = transport.snapshot()["discontinuity"]
    for iteration in range(1, 101):
        clock.advance(3_999_999_999)
        just_before = transport.snapshot()
        assert 8 <= position(just_before) < 16
        assert just_before["playback"]["loop"]["iteration"] == iteration - 1
        clock.advance(1)
        boundary = transport.snapshot()
        assert position(boundary) == 8
        assert boundary["playback"]["loop"] == {
            "startBeat": 8, "endBeat": 16, "iteration": iteration,
        }
        assert boundary["discontinuity"] == epoch
        assert boundary["playing"]
    clock.advance(123_456_789)
    assert_phase(transport.snapshot(), 8.246913578)


def test_sparse_and_frequent_sampling_have_identical_phase(clock):
    document = ChartDocument(bpm=137.3, meter=7, loop=LoopRange(2, 7))
    sparse = StandaloneTransport(document, clock)
    frequent = StandaloneTransport(document, clock)
    sparse.play()
    frequent.play()
    clock.advance(400_000_000)
    for _ in range(5000):
        clock.advance(17_123_457)
        frequent.snapshot()
    assert frequent.snapshot() == sparse.snapshot()


def test_bpm_change_retains_subtick_phase_without_new_preparation(transport, clock):
    begin(transport, clock)
    clock.advance(123_456_789)
    expected = 0.246913578
    epoch = transport.snapshot()["discontinuity"]
    for index in range(500):
        bpm = 97.1 if index % 2 == 0 else 173.7
        transport.set_bpm(bpm)
        clock.advance(17_431)
        expected += 17_431 * bpm / 60_000_000_000
    sample = transport.snapshot()
    assert_phase(sample, expected)
    assert sample["discontinuity"] == epoch + 500
    assert sample["playing"]
    assert transport.document.bpm == 173.7


@pytest.mark.parametrize("bpm", [20, 300])
def test_bpm_limits_are_inclusive(clock, bpm):
    transport = StandaloneTransport(ChartDocument(bpm=bpm), clock)
    begin(transport, clock)
    clock.advance(1_000_000_000)
    assert_phase(transport.snapshot(), bpm / 60)


@pytest.mark.parametrize("bpm", [19.99, 300.01, 0, -1, True, math.nan, math.inf])
def test_invalid_bpm_is_atomic(transport, bpm):
    before = transport.snapshot()
    document = transport.document
    with pytest.raises(ValueError):
        transport.set_bpm(bpm)
    assert transport.document is document
    assert transport.snapshot() == before


@pytest.mark.parametrize("change", [
    lambda t: t.seek(3, 1.25),
    lambda t: t.set_bpm(180),
    lambda t: t.set_loop(LoopRange(3, 5)),
])
def test_edits_during_preparation_keep_original_start(transport, clock, change):
    transport.play()
    original = transport.snapshot()
    clock.advance(250_000_000)
    change(transport)
    changed = transport.snapshot()
    assert changed["playback"]["startTime"] == original["playback"]["startTime"]
    assert changed["discontinuity"] == original["discontinuity"] + 1
    initial = position(changed)
    clock.advance(150_000_000)
    assert position(transport.snapshot()) == initial
    clock.advance(500_000_000)
    assert_phase(transport.snapshot(), initial + transport.document.bpm / 120)


def test_loop_edit_and_seek_reset_iteration_cancel_old_epoch(clock):
    transport = StandaloneTransport(ChartDocument(loop=LoopRange(3, 5)), clock)
    begin(transport, clock)
    clock.advance(8_125_000_000)
    old = transport.snapshot()
    assert old["playback"]["loop"]["iteration"] == 2
    transport.seek(5)  # Exclusive loop endpoint wraps to loop start.
    sought = transport.snapshot()
    assert position(sought) == 8
    assert sought["playback"]["loop"]["iteration"] == 0
    assert sought["discontinuity"] == old["discontinuity"] + 1
    transport.set_loop(LoopRange(4, 5))
    edited = transport.snapshot()
    assert position(edited) == 12
    assert edited["discontinuity"] == sought["discontinuity"] + 1
    clock.advance(125_000_000)
    transport.set_loop(None)
    removed = transport.snapshot()
    assert position(removed) == 12.25
    assert removed["playback"]["loop"] is None
    clock.advance(125_000_000)
    assert position(transport.snapshot()) == 12.5


def test_invalid_loop_keeps_current_state(transport):
    before = transport.snapshot()
    with pytest.raises(ValueError):
        transport.set_loop(LoopRange(1, 18))
    assert transport.snapshot() == before


@pytest.mark.parametrize("meter", range(1, 13))
def test_supported_quarter_note_meters(clock, meter):
    transport = StandaloneTransport(ChartDocument(meter=meter), clock)
    begin(transport, clock)
    clock.advance(meter * 500_000_000)
    sample = transport.snapshot()
    assert (sample["bar"], sample["beat"]) == (2, 1)
    assert sample["meter"] == f"{meter}/4"


def test_meter_change_preserves_event_and_playhead_bar_tick(clock):
    event = ChordEvent(1, 3, 2000, "Cm7")
    document = ChartDocument(events=(event,), loop=LoopRange(2, 4))
    transport = StandaloneTransport(document, clock)
    transport.seek(3, 2.125)
    transport.set_meter(3)
    sample = transport.snapshot()
    assert (sample["bar"], sample["beat"], sample["division"], sample["tick"]) == (3, 2, 1, 120)
    assert sample["playback"]["loop"]["startBeat"] == 3
    assert sample["playback"]["loop"]["endBeat"] == 9
    assert transport.document.events == (event,)
    assert transport.document.loop == document.loop
    assert transport.document is not document


def test_meter_change_rejects_invalidated_events_or_playhead(clock):
    transport = StandaloneTransport(ChartDocument(events=(ChordEvent(1, 1, 3000, "C"),)), clock)
    before = transport.snapshot()
    with pytest.raises(ValueError):
        transport.set_meter(3)
    assert transport.snapshot() == before
    transport.set_document(ChartDocument())
    transport.seek(2, 4.125)
    before = transport.snapshot()
    with pytest.raises(ValueError):
        transport.set_meter(3)
    assert transport.snapshot() == before


def test_meter_change_is_stopped_only_and_preserves_project_end(transport, clock):
    transport.play()
    with pytest.raises(ValueError, match="stopped"):
        transport.set_meter(3)
    clock.advance(1_000_000_000)
    with pytest.raises(ValueError, match="stopped"):
        transport.set_meter(3)
    clock.advance(99_000_000_000)
    assert not transport.snapshot()["playing"]
    transport.set_meter(3)
    assert position(transport.snapshot()) == 48
    assert transport.snapshot()["bar"] == 17


@pytest.mark.parametrize("meter", [True, 0, 13, 3.5, "4"])
def test_invalid_meter_is_atomic(transport, meter):
    before = transport.snapshot()
    with pytest.raises(ValueError):
        transport.set_meter(meter)
    assert transport.snapshot() == before


def test_set_document_stops_and_replaces_without_automatic_start(transport, clock):
    begin(transport, clock)
    clock.advance(1_000_000_000)
    before = transport.snapshot()
    new_document = replace(transport.document, name="Another", meter=5, bpm=93)
    transport.set_document(new_document)
    sample = transport.snapshot()
    assert transport.document is new_document
    assert not sample["playing"]
    assert position(sample) == 0
    assert sample["discontinuity"] == before["discontinuity"] + 1
    clock.advance(10_000_000_000)
    assert position(transport.snapshot()) == 0


def test_close_is_idempotent_and_disarms_playback(transport, clock):
    begin(transport, clock)
    clock.advance(1_000_000_000)
    transport.close()
    sample = transport.snapshot()
    assert not sample["playing"]
    assert not sample["valid"]
    assert position(sample) == 2
    transport.close()
    assert transport.snapshot() == sample
    with pytest.raises(RuntimeError, match="closed"):
        transport.play()
