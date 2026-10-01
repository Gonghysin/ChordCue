import pytest
from pypdf import PdfReader

from chordcue.models import ChartDocument, ChordEvent, KeySection, MusicalKey
from chordcue.render import (ChartWidget, _fixed_font, _wrapped_lines, bar_height,
                             crowded_bar, export_pdf, layout_chart, onset_label, pdf_pages)
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFontMetricsF


@pytest.mark.parametrize("meter", range(1, 13))
def test_fractional_onsets_preserved_in_every_meter(meter):
    doc = ChartDocument(bars=4, meter=meter, forced_key=MusicalKey(0), events=(
        ChordEvent(0, 2, 120, "Cmaj7"), ChordEvent(1, 2, 180, "G/B"),
        ChordEvent(2, 4, 480, "Am")))
    bars = layout_chart(doc)
    assert bars[0].spans[0].text == "·"
    assert bars[1].spans[0].text == "·"
    assert bars[1].spans[1].start == 120/(960*meter)
    assert bars[1].spans[1].end == 180/(960*meter)
    assert bars[1].spans[2].start == 180/(960*meter)
    assert bars[2].spans[0].text == "%"
    assert bars[3].spans[0].text == "—"


def test_transposition_and_numbers_follow_section():
    doc = ChartDocument(bars=2, forced_key=MusicalKey(2), original_key=MusicalKey(5),
        manual_sections=(KeySection(2, MusicalKey(4)),),
        events=(ChordEvent(0, 1, 0, "D/A"), ChordEvent(1, 2, 0, "E")))
    assert layout_chart(doc, mode="c")[0].spans[0].text == "C/G"
    assert layout_chart(doc, mode="original")[0].spans[0].text == "F/C"
    assert layout_chart(doc, mode="current", current_mode="key:7")[0].spans[0].text == "G/D"
    assert layout_chart(doc, notation="numbers")[1].spans[0].text == "1"
    assert layout_chart(doc)[1].section


@pytest.mark.parametrize("mode", ["track", "c", "original", "current"])
@pytest.mark.parametrize("notation", ["chords", "numbers"])
def test_pdf_is_a4_28_bars_per_page(qapp, tmp_path, mode, notation):
    doc = ChartDocument(name="测试曲目 / 中文与降号", bars=57, meter=7,
        events=(ChordEvent(0, 1, 0, "B♭maj7/F"), ChordEvent(1, 57, 120, "Gm7")))
    path = tmp_path / f"{mode}-{notation}.pdf"
    export_pdf(doc, path, notation, mode, current_mode="key:3")
    reader = PdfReader(path)
    assert len(reader.pages) == 3
    assert float(reader.pages[0].mediabox.width) == pytest.approx(595, abs=2)
    assert float(reader.pages[0].mediabox.height) == pytest.approx(842, abs=2)
    assert "57" in reader.pages[2].extract_text()


def test_invalid_export_mode_leaves_no_output(qapp, tmp_path):
    path = tmp_path / "bad.pdf"
    with pytest.raises(ValueError):
        export_pdf(ChartDocument(), path, mode="bad")
    assert not path.exists()


@pytest.mark.parametrize("meter", [7, 12])
def test_nearby_onsets_are_readable_without_quantization(qapp, tmp_path, meter):
    doc = ChartDocument(bars=29, meter=meter, forced_key=MusicalKey(1), events=(
        ChordEvent(0, 2, 120, "D♭maj7/F"), ChordEvent(1, 2, 121, "G♯m7/B")))
    bars = layout_chart(doc)
    assert crowded_bar(bars[1], 127.5)
    assert bar_height(bars[1], 127.5) > 91
    assert [onset_label(span, meter) for span in bars[1].spans] == ["1.1.0", "1.1.120", "1.1.121"]
    path = tmp_path / f"dense-{meter}.pdf"
    export_pdf(doc, path)
    page_text = PdfReader(path).pages[0].extract_text()
    assert "1.1.120" in page_text
    assert "1.1.121" in page_text
    normalized = "".join(page_text.split())
    for span in bars[1].spans:
        assert "".join(span.text.split()) in normalized


def test_dense_row_continues_without_losing_spans(qapp, tmp_path):
    doc = ChartDocument(bars=4, meter=12, events=tuple(
        ChordEvent(i, 1, i, "D♭maj7/F") for i in range(100)))
    bars = layout_chart(doc)
    pages = pdf_pages(bars)
    assert len(pages) > 1
    actual = tuple(span for page in pages for row, _ in page for bar in row
                   if bar.number == 1 for span in bar.spans)
    assert actual == bars[0].spans
    assert all(sum(height for _, height in page) <= 637 for page in pages)
    path = tmp_path / "very-dense.pdf"
    export_pdf(doc, path)
    reader = PdfReader(path)
    assert len(reader.pages) == len(pages)
    assert "1.1.99" in reader.pages[-1].extract_text()


def test_long_chord_wrap_retains_all_characters(qapp):
    text = "D♭maj7(add9,add11,add13)/G♯"
    font = _fixed_font(12, True)
    lines = _wrapped_lines(text, 109.5, font)
    assert len(lines) > 1
    assert "".join(lines) == text
    assert all(QFontMetricsF(font).horizontalAdvance(line) <= 109.5 for line in lines)


def test_screen_dense_row_hit_testing_uses_expanded_height(qtbot):
    doc = ChartDocument(bars=8, meter=12, events=tuple(
        ChordEvent(i, 1, i, "D♭maj7/F") for i in range(5)))
    widget = ChartWidget()
    qtbot.addWidget(widget)
    widget.resize(580, 500)
    widget.set_chart(layout_chart(doc), doc.meter)
    widget.show()
    y = widget.bar_center_y(5)
    assert y > 18 + 108 + 54
    with qtbot.waitSignal(widget.seek_requested) as signal:
        qtbot.mouseClick(widget, Qt.MouseButton.LeftButton, pos=QPoint(40, y))
    assert signal.args[0] == 5


def test_export_layout_failure_preserves_existing_pdf(qapp, tmp_path):
    path = tmp_path / "existing.pdf"
    path.write_bytes(b"original PDF")
    doc = ChartDocument(events=(ChordEvent(0, 1, 0, "C"+"x"*5000),))
    with pytest.raises(ValueError, match="过长"):
        export_pdf(doc, path)
    assert path.read_bytes() == b"original PDF"
    assert list(tmp_path.iterdir()) == [path]


def test_export_writer_failure_preserves_existing_pdf_and_cleans_temp(qapp, tmp_path, monkeypatch):
    import chordcue.render as render
    path = tmp_path / "existing.pdf"
    path.write_bytes(b"original PDF")
    def fail(document, temporary, notation, pages):
        temporary.write_bytes(b"%PDF-incomplete")
        raise OSError("disk full")
    monkeypatch.setattr(render, "_write_pdf", fail)
    with pytest.raises(OSError, match="disk full"):
        export_pdf(ChartDocument(), path)
    assert path.read_bytes() == b"original PDF"
    assert list(tmp_path.iterdir()) == [path]
