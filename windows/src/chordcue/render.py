"""One fractional-tick layout and painter for the screen and A4 PDF."""
from dataclasses import dataclass
import os
from pathlib import Path
import tempfile

from PySide6.QtCore import QLineF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPageSize, QPdfWriter, QPen
from PySide6.QtWidgets import QWidget

from .models import ChartDocument, ChordEvent, MusicalKey, PPQ
from .theory import analyze_sections, display_chord, number, transpose


@dataclass(frozen=True)
class Span:
    start: float
    end: float
    text: str
    carry: bool = False


@dataclass(frozen=True)
class Bar:
    number: int
    section: str
    spans: tuple[Span, ...]


def _fixed_font(pixels=12, bold=False):
    font = QFont("Microsoft YaHei UI")
    font.setPixelSize(pixels)
    font.setBold(bold)
    return font


def _wrapped_lines(text, width, font):
    """Wrap even a long unbroken chord, retaining every Unicode character."""
    metrics = QFontMetricsF(font)
    result, line = [], ""
    for character in text:
        if line and metrics.horizontalAdvance(line + character) > width:
            result.append(line)
            line = ""
        line += character
    result.append(line)
    return tuple(result)


def crowded_bar(bar: Bar, width: float) -> bool:
    """Use labeled entries whenever duration-proportional text would be tiny."""
    metrics = QFontMetricsF(_fixed_font(12, True))
    return any(metrics.horizontalAdvance(span.text) + 6 > (width-14)*(span.end-span.start)
               for span in bar.spans)


def onset_label(span: Span, meter: int) -> str:
    # Recover the exact source tick from its normalized position; no beat rounding.
    tick = round(span.start * meter * PPQ)
    beat, remainder = divmod(tick, PPQ)
    division, tick = divmod(remainder, PPQ//4)
    return f"{beat+1}.{division+1}.{tick}"


def bar_height(bar: Bar, width: float, minimum=91) -> float:
    if not crowded_bar(bar, width):
        return minimum
    return max(minimum, 60 + sum(12 + 16*len(_wrapped_lines(span.text, width-18, _fixed_font(12, True)))
                                for span in bar.spans))


def chart_rows(bars, width, minimum=91):
    """Four bars per row; only crowded rows grow beyond the normal height."""
    return tuple((tuple(bars[i:i+4]), max(bar_height(bar, width, minimum) for bar in bars[i:i+4]))
                 for i in range(0, len(bars), 4))


def pdf_pages(bars):
    """Normal pages have 7 rows / 28 bars; dense rows consume their real height."""
    pages = []
    current: list[tuple[tuple[Bar, ...], float]] = []
    height = 0.0
    for row, row_height in chart_rows(bars, 127.5):
        if row_height > 637:
            # Exceptionally dense bars are continued vertically, never scaled to illegibility.
            if current:
                pages.append(tuple(current))
                current, height = [], 0.0
            # Keep the four-bar row; partition spans into fitting continuation fragments.
            remaining = [list(bar.spans) for bar in row]
            continuation = False
            while any(remaining):
                fragment = []
                for bar, spans in zip(row, remaining):
                    taken: list[Span] = []
                    used = 60
                    while spans:
                        entry_height = 12+16*len(_wrapped_lines(spans[0].text, 109.5, _fixed_font(12, True)))
                        if taken and used+entry_height > 637:
                            break
                        if entry_height+60 > 637:
                            raise ValueError("单个和弦名称过长，无法在一页内完整排版；请缩短该名称。")
                        taken.append(spans.pop(0))
                        used += entry_height
                    fragment.append(Bar(bar.number, "续" if continuation else bar.section, tuple(taken)))
                fragment_height = max(bar_height(bar, 127.5) for bar in fragment)
                pages.append(((tuple(fragment), fragment_height),))
                continuation = True
            continue
        if current and height+row_height > 637:
            pages.append(tuple(current))
            current, height = [], 0.0
        current.append((row, row_height))
        height += row_height
    if current:
        pages.append(tuple(current))
    return tuple(pages)


def display_shift(document, sections, mode="track", current_mode="track"):
    mode = current_mode if mode == "current" else mode
    root = sections[0].key.major_family_root
    if mode == "track":
        return 0
    if mode.lower() == "c":
        return -root
    if mode == "original":
        return document.original_key.major_family_root - root if document.original_key else 0
    if mode.startswith("key:"):
        pitch = int(mode[4:])
        if 0 <= pitch < 12:
            return pitch - root
    raise ValueError("Unknown display key mode")


def layout_chart(document: ChartDocument, notation="chords", mode="track", *,
                 current_mode="track", sections=None) -> tuple[Bar, ...]:
    document.validate()
    if notation not in ("chords", "numbers"):
        raise ValueError("Unknown chart notation")
    sections = sections or analyze_sections(document)
    shift = display_shift(document, sections, mode, current_mode)
    events = sorted(document.events, key=lambda e: (e.bar, e.tick))
    grouped: dict[int, list[ChordEvent]] = {}
    for event in events:
        grouped.setdefault(event.bar, []).append(event)
    previous = False
    bars = []
    for bar in range(1, document.bars + 1):
        key_section = next(s for s in reversed(sections) if s.first_bar <= bar)
        key = key_section.key
        shifted = MusicalKey((key.root + shift) % 12, key.is_minor)
        label = shifted.label if key_section.first_bar == bar else ""
        local = grouped.get(bar, [])
        spans = []
        if not local:
            spans.append(Span(0, 1, "%" if previous else "·", True))
        else:
            total = document.meter * PPQ
            if local[0].tick:
                spans.append(Span(0, local[0].tick / total, "—" if previous else "·", True))
            for i, event in enumerate(local):
                end = local[i + 1].tick if i + 1 < len(local) else total
                text = (number(event.symbol, key) if notation == "numbers" else
                        display_chord(transpose(event.symbol, shift,
                                      prefer_flats=shifted.major_family_root in (1, 3, 5, 8, 10))))
                spans.append(Span(event.tick / total, end / total, text))
            previous = True
        bars.append(Bar(bar, label, tuple(spans)))
    return tuple(bars)


def _text(painter, rect, text, size, color="#243247", bold=False, align=Qt.AlignmentFlag.AlignCenter):
    font = QFont("Microsoft YaHei UI")
    font.setPointSizeF(size)
    font.setBold(bold)
    while size > 5 and QFontMetricsF(font).horizontalAdvance(text) > max(1, rect.width() - 4):
        size -= .5
        font.setPointSizeF(size)
    painter.setFont(font)
    painter.setPen(QColor(color))
    painter.save()
    painter.setClipRect(rect)
    painter.drawText(rect, align, text)
    painter.restore()


def paint_bar(painter, rect: QRectF, bar: Bar, meter: int, active=False, phase=None):
    painter.fillRect(rect, QColor("#eaf3ff" if active else "#fffefa"))
    painter.setPen(QPen(QColor("#bdcde0" if active else "#d9dcd9"), .7))
    painter.drawRect(rect)
    _text(painter, QRectF(rect.x()+8, rect.y()+6, 25, 20), str(bar.number), 9,
          "#3472b8" if active else "#8793a0", align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    _text(painter, QRectF(rect.x()+34, rect.y()+6, rect.width()-43, 20), bar.section, 8,
          "#2877c7", align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    left, width = rect.x()+7, rect.width()-14
    for beat in range(meter):
        x = left + width * beat / meter
        painter.setPen(QPen(QColor("#dfe5e8"), .7))
        painter.drawLine(QLineF(x, rect.bottom()-15, x, rect.bottom()-10))
    dense = crowded_bar(bar, rect.width())
    entry_y = rect.y()+29
    for span in bar.spans:
        x = left + width * span.start
        w = width * (span.end-span.start)
        if dense:
            painter.setFont(_fixed_font(9))
            painter.setPen(QColor("#718092"))
            painter.drawText(QRectF(left+2, entry_y, width-4, 12), Qt.AlignmentFlag.AlignLeft,
                             onset_label(span, meter))
            entry_y += 12
            painter.setFont(_fixed_font(12, not span.carry))
            painter.setPen(QColor("#929da6" if span.carry else "#243247"))
            for line in _wrapped_lines(span.text, width-4, _fixed_font(12, True)):
                painter.drawText(QRectF(left+2, entry_y, width-4, 16), Qt.AlignmentFlag.AlignLeft, line)
                entry_y += 16
            painter.setPen(QPen(QColor("#438bda"), .8))
            painter.drawLine(QLineF(x, rect.bottom()-22, x, rect.bottom()-10))
            continue
        if span.start:
            painter.setPen(QPen(QColor("#bbc7d3"), .7))
            painter.drawLine(QLineF(x, rect.y()+34, x, rect.bottom()-24))
        _text(painter, QRectF(x+1, rect.y()+31, w-2, rect.height()-53), span.text,
              16 if w > 65 else 12, "#929da6" if span.carry else "#243247", not span.carry)
    if phase is not None:
        painter.fillRect(QRectF(left, rect.bottom()-4, width * min(1, max(0, phase)), 3), QColor("#438bda"))


class ChartWidget(QWidget):
    seek_requested = Signal(int, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.bars: tuple[Bar, ...] = ()
        self._rows: tuple[tuple[tuple[Bar, ...], float], ...] = ()
        self.meter = 4
        self.active_bar = 0
        self.phase = 0
        self.setMinimumWidth(580)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName("和弦谱，点击小节定位播放位置")

    def set_chart(self, bars, meter):
        self.bars, self.meter = bars, meter
        self._resize_rows()
        self.update()

    def _resize_rows(self):
        self._rows = chart_rows(self.bars, (self.width()-36)/4, 108)
        self.setMinimumHeight(int(sum(height for _, height in self._rows)+36))

    def resizeEvent(self, event):
        self._resize_rows()
        super().resizeEvent(event)

    def bar_center_y(self, bar):
        y = 18.0
        for row, height in self._rows:
            if any(item.number == bar for item in row):
                return int(y+height/2)
            y += height
        return int(y)

    def set_position(self, payload):
        self.active_bar = payload["bar"]
        self.phase = (payload["beat"]-1+(payload["division"]-1)/4+payload["tick"]/PPQ)/self.meter
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#f5f3ed"))
        width = (self.width()-36)/4
        y = 18.0
        for row, height in self._rows:
            for i, bar in enumerate(row):
                rect = QRectF(18+i*width, y, width, height)
                if rect.intersects(QRectF(event.rect())):
                    paint_bar(painter, rect, bar, self.meter, bar.number == self.active_bar,
                              self.phase if bar.number == self.active_bar else None)
            y += height

    def mousePressEvent(self, event):
        width = (self.width()-36)/4
        x, y = event.position().x()-18, event.position().y()-18
        if x < 0 or y < 0 or x >= width*4:
            return
        bar = len(self.bars)+1
        for row, height in self._rows:
            if y < height:
                column = int(x//width)
                if column < len(row):
                    bar = row[column].number
                break
            y -= height
        if bar <= len(self.bars):
            fraction = min(.9999, max(0, (x%width-7)/(width-14)))
            self.seek_requested.emit(bar, 1+fraction*self.meter)


def export_pdf(document, path: str | Path, notation="chords", mode="track", *, current_mode="track", sections=None):
    bars = layout_chart(document, notation, mode, current_mode=current_mode, sections=sections)
    pages = pdf_pages(bars)
    destination = Path(path).absolute()
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        _write_pdf(document, temporary, notation, pages)
        with temporary.open("rb") as completed:
            if completed.read(5) != b"%PDF-":
                raise OSError("PDF 文件未完整写入。")
            completed.seek(max(0, temporary.stat().st_size-1024))
            if b"%%EOF" not in completed.read():
                raise OSError("PDF 文件未完整写入。")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _write_pdf(document, path, notation, pages):
    """Close every Qt file handle before the caller's atomic Windows replacement."""
    writer = QPdfWriter(str(path))
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setResolution(72)
    writer.setTitle(document.name)
    writer.setCreator("ChordCue")
    painter = QPainter(writer)
    if not painter.isActive():
        del painter
        del writer
        raise OSError("无法创建 PDF，请检查保存位置。")
    try:
        for page, rows in enumerate(pages):
            if page and not writer.newPage():
                raise OSError("无法创建 PDF 页面。")
            painter.save()
            painter.scale(writer.width()/540, writer.height()/780)
            _text(painter, QRectF(15, 8, 430, 38), document.name, 22, bold=True,
                  align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            _text(painter, QRectF(445, 15, 80, 25), f"{page+1} / {len(pages)}", 10)
            subtitle = f"{'级数谱' if notation == 'numbers' else '和弦谱'}  ·  {document.meter}/4  ·  ♩ = {document.bpm:g}"
            _text(painter, QRectF(15, 49, 500, 24), subtitle, 10, "#69798c",
                  align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            y = 89.0
            for row, height in rows:
                for column, bar in enumerate(row):
                    paint_bar(painter, QRectF(15+column*127.5, y, 127.5, height), bar, document.meter)
                y += height
            _text(painter, QRectF(15, 745, 510, 20), "ChordCue · % 延续小节 / — 延续和弦 / · 空拍 · 密集和弦标注：拍.分拍.tick", 8, "#8994a0")
            painter.restore()
    finally:
        painter.end()
        del painter
        del writer
    if not Path(path).exists() or Path(path).stat().st_size < 100:
        raise OSError("PDF 文件未完整写入。")
