"""A cursor-relative floating input bar with a local echo for testing."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)


class AssistantPopup(QWidget):
    WIDTH = 420
    INPUT_HEIGHT = 56
    RESPONSE_HEIGHT = 180
    PANEL_GAP = 8

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("Mote")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFont(QFont("Segoe UI", 11))
        self._expanded = False
        self.setStyleSheet("""
            QFrame#inputBar {
                background-color: #242628;
                border: 1px solid #414448;
                border-radius: 28px;
            }
            QLineEdit {
                background: transparent;
                border: none;
                color: #f0f0f0;
                selection-background-color: #475b70;
                placeholder-text-color: #93979c;
                padding: 0;
            }
            QPlainTextEdit#response {
                background-color: #242628;
                color: #d9dcdf;
                border: 1px solid #414448;
                border-radius: 20px;
                padding: 16px;
                selection-background-color: #475b70;
            }
            QMenu {
                background-color: #242628;
                color: #e5e7eb;
                border: 1px solid #414448;
                padding: 4px;
            }
            QMenu::item { padding: 7px 16px; }
            QMenu::item:selected { background-color: #3a3e43; }
        """)

        self.input_bar = QFrame()
        self.input_bar.setObjectName("inputBar")
        self.input_bar.setFixedHeight(self.INPUT_HEIGHT)
        self.prompt = QLineEdit()
        self.prompt.setPlaceholderText("Ask Mote anything...")
        self.prompt.setAccessibleName("Ask Mote anything")
        self.prompt.setToolTip("Enter to send · Esc to hide · Ctrl+Q to quit")
        self.prompt.returnPressed.connect(self.submit)
        entry = QHBoxLayout(self.input_bar)
        entry.setContentsMargins(22, 0, 22, 0)
        entry.addWidget(self.prompt)

        self.response = QPlainTextEdit()
        self.response.setObjectName("response")
        self.response.setReadOnly(True)
        self.response.setAccessibleName("Local echo response")
        self.response.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(self.PANEL_GAP)
        layout.addWidget(self.input_bar)
        layout.addWidget(self.response, 1)
        self.resize(self.WIDTH, self.INPUT_HEIGHT)

        self.escape = QShortcut(QKeySequence("Escape"), self)
        self.escape.activated.connect(self.hide)
        self.quit_shortcut = QShortcut(QKeySequence("Ctrl+Q"), self)
        self.quit_shortcut.activated.connect(QApplication.instance().quit)

        # Add window actions to the editors' standard right-click menus.
        for widget in (self, self.input_bar, self.prompt, self.response):
            widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            widget.customContextMenuRequested.connect(
                lambda position, target=widget: self._show_menu(target, position)
            )

    def _show_menu(self, target, position) -> None:
        menu = target.createStandardContextMenu() if target in (self.prompt, self.response) else QMenu(self)
        if menu.actions():
            menu.addSeparator()
        menu.addAction("Hide Mote\tEsc", self.hide)
        menu.addAction("Quit Mote\tCtrl+Q", QApplication.instance().quit)
        menu.exec(target.mapToGlobal(position))
        menu.deleteLater()

    def _fit_to_screen(self, screen) -> None:
        bounds = screen.availableGeometry()
        height = self.INPUT_HEIGHT
        if self._expanded:
            height += self.PANEL_GAP + self.RESPONSE_HEIGHT
        self.resize(min(self.WIDTH, bounds.width()), min(height, bounds.height()))

    def _clamp_to_screen(self, screen) -> None:
        bounds = screen.availableGeometry()
        self.move(
            max(bounds.left(), min(self.x(), bounds.right() - self.width() + 1)),
            max(bounds.top(), min(self.y(), bounds.bottom() - self.height() + 1)),
        )

    def open_at_cursor(self) -> None:
        cursor = QCursor.pos()
        screen = QApplication.screenAt(cursor) or QApplication.primaryScreen()
        self._fit_to_screen(screen)
        bounds = screen.availableGeometry()
        x = cursor.x() + 12
        y = cursor.y() + 12
        if x + self.width() > bounds.right() + 1:
            x = cursor.x() - self.width() - 12
        if y + self.height() > bounds.bottom() + 1:
            y = cursor.y() - self.height() - 12
        self.move(x, y)
        self._clamp_to_screen(screen)
        self.show()
        self.raise_()
        self.activateWindow()
        self.prompt.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def submit(self) -> None:
        text = self.prompt.text().strip()
        if text:
            self.response.setPlainText(f"Local echo:\n{text}\n\nAI inference is not connected in this prototype.")
            self._expanded = True
            self.response.show()
            screen = self.screen() or QApplication.primaryScreen()
            self._fit_to_screen(screen)
            self._clamp_to_screen(screen)
