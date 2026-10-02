"""Rasterize the project's original SVG into a Windows multi-resolution icon."""
import argparse
from pathlib import Path
import struct

from PySide6.QtCore import QBuffer, QIODevice, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


def make_icon(source: Path, destination: Path) -> None:
    renderer = QSvgRenderer(str(source))
    if not renderer.isValid():
        raise ValueError(f"Invalid SVG icon: {source}")
    sizes = (16, 24, 32, 48, 64, 128, 256)
    images = []
    for size in sizes:
        image = QImage(size, size, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(buffer, "PNG"):
            raise RuntimeError("Qt failed to encode the application icon")
        images.append(bytes(buffer.data()))
    offset = 6 + 16 * len(images)
    entries = []
    for size, data in zip(sizes, images, strict=True):
        entries.append(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0,
                                   1, 32, len(data), offset))
        offset += len(data)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(struct.pack("<HHH", 0, 1, len(images)) +
                            b"".join(entries) + b"".join(images))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    app = QGuiApplication([])
    make_icon(Path(__file__).resolve().parents[2] / "Resources/ChordCue.svg", args.destination)
