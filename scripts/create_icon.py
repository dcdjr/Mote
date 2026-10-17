"""Render the source vector into a multi-resolution PNG-compressed Windows ICO."""
import struct
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

root = Path(__file__).resolve().parents[1]
renderer = QSvgRenderer(str(root / 'assets/mote.svg'))
frames = []
for size in (16, 24, 32, 48, 64, 128, 256):
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, 'PNG')
    frames.append((size, bytes(buffer.data())))
header = struct.pack('<HHH', 0, 1, len(frames))
entries, payload = b'', b''
offset = 6 + 16 * len(frames)
for size, data in frames:
    entries += struct.pack('<BBBBHHII', size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
    payload += data
    offset += len(data)
(root / 'assets/mote.ico').write_bytes(header + entries + payload)
