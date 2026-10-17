"""A cursor-relative popup with a local echo for interaction testing."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class AssistantPopup(QWidget):
    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("Mote")
        self.resize(380, 260)

        title = QLabel("Mote · interaction prototype")
        hide_button = QPushButton("Hide")
        hide_button.clicked.connect(self.hide)
        quit_button = QPushButton("Quit")
        quit_button.clicked.connect(QApplication.instance().quit)
        header = QHBoxLayout()
        header.addWidget(title, 1)
        header.addWidget(hide_button)
        header.addWidget(quit_button)

        self.prompt = QLineEdit()
        self.prompt.setPlaceholderText("Type something and press Enter…")
        self.prompt.returnPressed.connect(self.submit)
        submit_button = QPushButton("Submit")
        submit_button.clicked.connect(self.submit)
        entry = QHBoxLayout()
        entry.addWidget(self.prompt, 1)
        entry.addWidget(submit_button)

        self.response = QPlainTextEdit()
        self.response.setReadOnly(True)
        self.response.setPlaceholderText("Response area · local echo only; AI is not connected.")

        layout = QVBoxLayout(self)
        layout.addLayout(header)
        layout.addLayout(entry)
        layout.addWidget(self.response)
        layout.addWidget(QLabel("Ctrl+Shift+Space to open · Esc to hide"))
        self.escape = QShortcut(QKeySequence("Escape"), self)
        self.escape.activated.connect(self.hide)

    def open_at_cursor(self) -> None:
        cursor = QCursor.pos()
        screen = QApplication.screenAt(cursor) or QApplication.primaryScreen()
        bounds = screen.availableGeometry()
        self.resize(min(380, bounds.width()), min(260, bounds.height()))
        x = cursor.x() + 12
        y = cursor.y() + 12
        if x + self.width() > bounds.right() + 1:
            x = cursor.x() - self.width() - 12
        if y + self.height() > bounds.bottom() + 1:
            y = cursor.y() - self.height() - 12
        self.move(
            max(bounds.left(), min(x, bounds.right() - self.width() + 1)),
            max(bounds.top(), min(y, bounds.bottom() - self.height() + 1)),
        )
        self.show()
        self.raise_()
        self.activateWindow()
        self.prompt.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def submit(self) -> None:
        text = self.prompt.text().strip()
        if text:
            self.response.setPlainText(f"Local echo:\n{text}\n\nAI inference is not connected in this prototype.")
