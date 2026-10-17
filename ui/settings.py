"""Compact floating settings; model metadata is fetched off the GUI thread."""
from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt
from PySide6.QtGui import QCursor, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ai.models import ModelScan
from config import VERSION
from integration.hotkey import parse_shortcut


class SettingsPanel(QWidget):
    def __init__(self, popup, preferences, hotkey=None):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.popup, self.preferences, self.hotkey = popup, preferences, hotkey
        self._scan = None
        self._closing = False
        self.setWindowTitle('Mote settings')
        self.setWindowIcon(QApplication.windowIcon())
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFont(QFont('Segoe UI', 10))
        self.setStyleSheet('''
            QFrame#settingsSurface { background: rgba(31,35,42,248); border: 1px solid rgba(230,240,255,40); border-radius:28px; }
            QLabel {color:#d9dfe8; background:transparent;}
            QPushButton, QComboBox, QKeySequenceEdit { color:#e0e6ef; background:#353c47; border:1px solid #4b5564; border-radius:8px; padding:6px; }
            QPushButton:hover {background:#465264;} QPushButton:disabled {color:#7d8590;}
            QComboBox QAbstractItemView {background:#272e38; color:#e0e6ef; selection-background-color:#465264;}
            QCheckBox {color:#d9dfe8;} QPlainTextEdit {color:#b9c5d4; background:#252b34; border:0; border-radius:8px; padding:6px;}
            QKeySequenceEdit QLineEdit {color:#e0e6ef; background:transparent; border:0;}
            QScrollArea#settingsScroll, QWidget#settingsContent, QWidget#settingsViewport {background:transparent; border:0;}
            QScrollBar:vertical {background:#252b34; width:8px; margin:0;}
            QScrollBar::handle:vertical {background:#596575; border-radius:4px; min-height:24px;}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {height:0;}
            QSlider::groove:horizontal {height:4px; background:#4b5564; border-radius:2px;}
            QSlider::handle:horizontal {background:#bed0e5; width:14px; margin:-5px 0; border-radius:7px;}
        ''')
        root = QVBoxLayout(self)
        root.setContentsMargins(14,14,14,14)
        surface = QFrame()
        surface.setObjectName('settingsSurface')
        root.addWidget(surface)
        shadow = QGraphicsDropShadowEffect(surface)
        shadow.setBlurRadius(20)
        shadow.setOffset(0,3)
        surface.setGraphicsEffect(shadow)
        frame_layout = QVBoxLayout(surface)
        frame_layout.setContentsMargins(10,10,10,10)
        scroll = QScrollArea()
        scroll.setObjectName('settingsScroll')
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.viewport().setObjectName('settingsViewport')
        scroll.viewport().setAutoFillBackground(False)
        content = QWidget()
        content.setObjectName('settingsContent')
        scroll.setWidget(content)
        content.setAutoFillBackground(False)
        frame_layout.addWidget(scroll)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(20,16,20,18)
        layout.setSpacing(10)
        heading = QHBoxLayout()
        heading.addWidget(QLabel(f'<b>Mote</b> <span style="color:#9caabd">Settings · {VERSION}</span>'), 1)
        close = QPushButton('×')
        close.setAccessibleName('Close settings')
        close.setFixedSize(28,28)
        close.clicked.connect(self.dismiss)
        heading.addWidget(close)
        layout.addLayout(heading)
        layout.addWidget(QLabel('<b>General</b>'))
        shortcut_row = QHBoxLayout()
        self.shortcut = QKeySequenceEdit(QKeySequence(preferences.shortcut))
        self.shortcut.setMaximumSequenceLength(1)
        self.shortcut.setAccessibleName('Record global shortcut')
        self.shortcut.setToolTip('Click and press Ctrl, Alt, or Win with a letter, number, or navigation key')
        shortcut_row.addWidget(self.shortcut, 1)
        apply_shortcut = QPushButton('Set shortcut')
        apply_shortcut.clicked.connect(self.apply_shortcut)
        shortcut_row.addWidget(apply_shortcut)
        layout.addLayout(shortcut_row)
        self.follow = QCheckBox('Follow cursor')
        self.follow.setChecked(popup.follow_enabled)
        self.follow.toggled.connect(popup.follow_button.setChecked)
        popup.follow_button.toggled.connect(self.follow.setChecked)
        layout.addWidget(self.follow)
        opacity_row = QHBoxLayout()
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(60,100)
        self.opacity.setValue(preferences.opacity)
        self.opacity.setAccessibleName('Glass opacity')
        self.opacity_label = QLabel(f'Glass {preferences.opacity}%')
        self.opacity.valueChanged.connect(self.change_opacity)
        self.opacity.sliderReleased.connect(self.save_opacity)
        opacity_row.addWidget(self.opacity, 1)
        opacity_row.addWidget(self.opacity_label)
        layout.addLayout(opacity_row)
        layout.addWidget(QLabel('<b>AI model</b> · installed vision models, ≤6B total'))
        self.models = QComboBox()
        self.models.setAccessibleName('Answer model')
        self.models.addItem('Refresh to inspect installed models', None)
        layout.addWidget(self.models)
        model_buttons = QHBoxLayout()
        self.refresh_button = QPushButton('Refresh')
        self.refresh_button.clicked.connect(self.refresh)
        self.apply_model_button = QPushButton('Use model')
        self.apply_model_button.setEnabled(False)
        self.apply_model_button.clicked.connect(self.apply_model)
        self.models.currentIndexChanged.connect(lambda: self.apply_model_button.setEnabled(self.models.currentData() is not None))
        model_buttons.addWidget(self.refresh_button)
        model_buttons.addWidget(self.apply_model_button)
        layout.addLayout(model_buttons)
        self.model_details = QPlainTextEdit()
        self.model_details.setReadOnly(True)
        self.model_details.setFixedHeight(76)
        self.model_details.setAccessibleName('Installed model availability')
        layout.addWidget(self.model_details)
        self.status = QLabel('Refresh checks Ollama and installed model capabilities.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        help_label = QLabel('Need Ollama? <a href="https://ollama.com/download/windows" style="color:#bed0e5">Install Ollama</a><br>'
                            'Then run <b>ollama pull gemma4:e2b</b> and Refresh.<br>'
                            'If that runtime lacks vision: <b>ollama pull gemma3:4b</b>.')
        help_label.setWordWrap(True)
        help_label.setOpenExternalLinks(True)
        layout.addWidget(help_label)
        router = getattr(popup.controller.pipeline, 'router', None)
        self.about = QLabel(f'Local assistant · {preferences.model}\nRouter: {getattr(router, "model", "gemma4:e2b")} (kept for structured routing)\nVision when verified · attachments · voice input · read aloud')
        self.about.setWordWrap(True)
        layout.addWidget(self.about)
        exit_button = QPushButton('Exit Mote')
        exit_button.clicked.connect(popup._request_quit)
        layout.addWidget(exit_button)
        self.fade = QPropertyAnimation(self, b'windowOpacity', self)
        self.fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.fade.finished.connect(lambda: self.hide() if self._closing else None)
        self.escape = QShortcut(QKeySequence('Escape'), self)
        self.escape.activated.connect(self.dismiss)
        self.quit_shortcut = QShortcut(QKeySequence('Ctrl+Q'), self)
        self.quit_shortcut.activated.connect(popup._request_quit)
        QApplication.instance().aboutToQuit.connect(self.shutdown)
        self.resize(440,620)

    def open_near(self):
        self._closing = False
        self.fade.stop()
        bounds = (QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()).availableGeometry()
        self.resize(min(440,bounds.width()), min(660,bounds.height()))
        anchor = self.popup.geometry() if self.popup.isVisible() else None
        x = anchor.right() + 8 if anchor else QCursor.pos().x() + 20
        y = anchor.top() if anchor else QCursor.pos().y() + 20
        self.move(max(bounds.left(),min(x,bounds.right()-self.width()+1)), max(bounds.top(),min(y,bounds.bottom()-self.height()+1)))
        self.setWindowOpacity(0 if self.popup.motion_enabled else 1)
        self.show()
        self.raise_()
        self.activateWindow()
        self.fade.setStartValue(self.windowOpacity())
        self.fade.setEndValue(1)
        self.fade.setDuration(160 if self.popup.motion_enabled else 0)
        self.fade.start()
        if self._scan is None and self.models.count() == 1 and self.models.currentData() is None:
            self.refresh()

    def dismiss(self):
        self.save_opacity()
        self._closing = True
        self.fade.stop()
        self.fade.setStartValue(self.windowOpacity())
        self.fade.setEndValue(0)
        self.fade.setDuration(120 if self.popup.motion_enabled else 0)
        self.fade.start()

    def apply_shortcut(self):
        try:
            canonical, _, _ = parse_shortcut(self.shortcut.keySequence().toString(QKeySequence.SequenceFormat.PortableText))
            previous = self.hotkey.shortcut if self.hotkey is not None else self.preferences.shortcut
            if self.hotkey is not None:
                canonical = self.hotkey.reassign(canonical)
            try:
                self.preferences.set('shortcut',canonical)
            except OSError:
                if self.hotkey is not None:
                    self.hotkey.reassign(previous)
                raise
            self.status.setText(f'Shortcut saved: {canonical}' + (' (Windows registration is disabled in preview).' if self.hotkey is None else ''))
        except (ValueError, RuntimeError, OSError) as error:
            self.status.setText(str(error))

    def change_opacity(self, value):
        self.opacity_label.setText(f'Glass {value}%')
        self.popup.set_glass_opacity(value)
        # Keyboard and wheel changes do not necessarily emit sliderReleased.
        if not self.opacity.isSliderDown():
            self.save_opacity()

    def save_opacity(self):
        try:
            self.preferences.set('opacity',self.opacity.value())
        except OSError as error:
            self.status.setText(str(error))

    def refresh(self):
        if self._scan is not None:
            return
        self.status.setText('Checking Ollama…')
        self.models.setEnabled(False)
        self.apply_model_button.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self._scan = ModelScan(self)
        self._scan.ready.connect(self.show_models)
        self._scan.failed.connect(self.status.setText)
        self._scan.finished.connect(self._scan_finished)
        self._scan.start()

    def show_models(self, rows):
        self.models.setEnabled(True)
        self.models.clear()
        self.models.addItem('Choose a verified installed model', None)
        for row in rows:
            if row['eligible']:
                self.models.addItem(row['name'], row['name'])
        index = self.models.findData(self.preferences.model)
        self.models.setCurrentIndex(max(0,index))
        self.model_details.setPlainText('\n'.join(f"{r['name']}: {r['detail']}" for r in rows) or 'No models installed.')
        count = sum(r['eligible'] for r in rows)
        self.status.setText(f'Ollama connected · {count} eligible model(s).' +
            (f' {self.preferences.model} is missing or ineligible; install/select a verified vision model.' if index < 0 else ''))

    def apply_model(self):
        model = self.models.currentData()
        if model is None:
            return
        try:
            self.preferences.set('model',model)
            self.popup.controller.set_model(model)
            self.preferences.set('setup_seen', True)
            self.status.setText(f'Using {model} for the next request. The current request can finish safely.')
            self.about.setText(f'Mote {VERSION} · {model}\nVerified vision · ≤6B total · local Ollama\nRouter unchanged · attachments · voice input · read aloud')
        except (ValueError, OSError) as error:
            self.status.setText(str(error))

    def _scan_finished(self):
        self._scan.wait()
        self._scan.deleteLater()
        self._scan = None
        self.refresh_button.setEnabled(True)
        self.popup._maybe_quit()

    @property
    def busy(self):
        return self._scan is not None

    def request_stop(self):
        if self._scan is not None:
            self._scan.cancel.set()

    def shutdown(self):
        if self._scan is not None:
            self._scan.cancel.set()
            self._scan.wait()
