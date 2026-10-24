"""Render ghostport/resources/icons/app.svg to a multi-size Windows .ico."""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SVG = ROOT / "ghostport" / "resources" / "icons" / "app.svg"
ICO = ROOT / "ghostport" / "resources" / "icons" / "app.ico"
SIZES = (16, 24, 32, 48, 64, 128, 256)


def _png_from_svg(svg: Path, size: int) -> bytes:
    from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QRectF
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    renderer = QSvgRenderer(str(svg))
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    blob = QByteArray()
    buf = QBuffer(blob)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buf, "PNG"):
        raise RuntimeError(f"Could not encode {size}x{size} PNG")
    return bytes(blob)


def write_ico(path: Path, pngs: list[tuple[int, bytes]]) -> None:
    count = len(pngs)
    offset = 6 + 16 * count
    header = struct.pack("<HHH", 0, 1, count)
    entries = bytearray()
    payload = bytearray()
    for size, png in pngs:
        w = 0 if size >= 256 else size
        h = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(png), offset)
        payload += png
        offset += len(png)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + bytes(entries) + bytes(payload))


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    if not SVG.is_file():
        raise SystemExit(f"missing {SVG}")
    QApplication.instance() or QApplication(["ghostport-icon"])
    pngs = [(size, _png_from_svg(SVG, size)) for size in SIZES]
    write_ico(ICO, pngs)
    print(f"Wrote {ICO} ({ICO.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
