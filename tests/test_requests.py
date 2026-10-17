"""Qt request lifecycle checks with blocking fakes, never desktop input or inference."""

import os
import unittest
from threading import Event
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from test_pipeline import make_pipeline

from ai.pipeline import Result
from ai.worker import DesktopCall
from integration.desktop import capture_screen
from ui.popup import AssistantPopup, PopupState


class ControlledPipeline:
    def __init__(self):
        self.started = Event()
        self.release = Event()
        self.cancel = None

    def run(self, prompt, **kwargs):
        self.cancel = kwargs['cancel']
        kwargs['status']('Thinking…')
        if prompt == 'old':
            self.started.set()
            self.release.wait(3)
            # Deliberately send stale data even after cancellation to test guards.
            kwargs['partial']('old stale answer')
            return Result('old stale answer', [{'role': 'user', 'content': 'old'}])
        kwargs['partial']('new answer')
        return Result('new answer', [{'role': 'user', 'content': 'new'}])


class RequestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.motion = patch('ui.popup._motion_enabled', return_value=False)
        self.motion.start()
        self.pipeline = ControlledPipeline()
        self.popup = AssistantPopup(pipeline=self.pipeline)
        self.popup.open_at_cursor()
        self.popup.follow_button.setChecked(False)

    def tearDown(self):
        self.pipeline.release.set()
        self.popup.hide()
        self.popup.controller.shutdown()
        self.app.processEvents()
        self.popup.deleteLater()
        self.app.processEvents()
        self.motion.stop()

    def wait_for(self, condition):
        for _ in range(200):
            self.app.processEvents()
            if condition():
                return
            QTest.qWait(10)
        self.fail('Timed out waiting for Qt worker/response')

    def start_old(self):
        self.popup.prompt.setText('old')
        self.popup.submit()
        self.wait_for(self.pipeline.started.is_set)
        return self.popup._request_id

    def test_gui_remains_responsive_during_request(self):
        self.start_old()
        ticks = []
        QTimer.singleShot(0, lambda: ticks.append(True))
        self.wait_for(lambda: bool(ticks))
        self.assertEqual(self.popup.request_state, 'Thinking')
        self.assertTrue(self.popup.isVisible())

    def test_dismiss_and_reopen_during_request_cancels_old_updates(self):
        self.start_old()
        self.popup.dismiss()
        self.assertTrue(self.pipeline.cancel.is_set())
        self.assertFalse(self.popup.isVisible())
        self.popup.open_at_cursor()
        self.pipeline.release.set()
        self.wait_for(lambda: self.popup.controller._worker is None)
        self.assertNotIn('old stale', self.popup.response.toPlainText())
        self.assertTrue(self.popup.isVisible())
        self.assertEqual(self.popup.request_state, 'Idle')

    def test_newer_request_blocks_stale_text_status_and_history(self):
        old_id = self.start_old()
        self.popup.prompt.setText('new')
        self.popup.submit()
        new_id = self.popup._request_id
        self.assertNotEqual(old_id, new_id)
        self.popup._pipeline_partial(old_id, 'stale direct signal')
        self.popup._pipeline_failed(old_id, 'stale error')
        self.popup._pipeline_status(old_id, 'Error')
        self.pipeline.release.set()
        self.wait_for(lambda: self.popup.response.toPlainText() == 'new answer')
        self.wait_for(lambda: self.popup.controller._worker is None)
        self.assertEqual(self.popup.controller.history, [{'role': 'user', 'content': 'new'}])
        self.assertEqual(self.popup.request_state, 'Idle')

    def test_close_event_cancels_safely(self):
        self.start_old()
        self.popup.close()
        self.assertFalse(self.popup.isVisible())
        self.assertTrue(self.pipeline.cancel.is_set())

    def test_chunk_updates_do_not_restart_expansion(self):
        p = self.popup
        p._request_id = 123
        p.state = PopupState.EXPANDED
        p._expanded = True
        p.thinking.hide()
        with patch.object(p, '_expand_to') as expand:
            for _ in range(50):
                p._pipeline_partial(123, 'token ')
                QTest.qWait(2)
            self.wait_for(lambda: len(p.response.toPlainText()) > 0)
            self.assertFalse(p._stream_done)
            expand.assert_not_called()
            p._pipeline_completed(123, 'token ' * 50)
            self.wait_for(lambda: p.response.toPlainText() == 'token ' * 50)
            self.assertLessEqual(expand.call_count, 1)

    def test_screenshot_bridge_hides_popup_and_restores_without_cancel(self):
        pipeline, client = make_pipeline('screen_quick')
        self.popup.controller.pipeline = pipeline
        seen_opacity = []
        def capture(screen):
            seen_opacity.append(self.popup.windowOpacity())
            return b'image bytes'
        with patch('integration.desktop.capture_screen', side_effect=capture) as grab:
            self.popup.prompt.setText('Describe my screen')
            self.popup.submit()
            self.wait_for(lambda: self.popup.response.toPlainText() == 'Hello world')
            grab.assert_called_once()
        self.assertEqual(seen_opacity, [0.0])
        self.assertEqual(self.popup.windowOpacity(), 1.0)
        self.assertFalse(self.popup.desktop.busy)
        self.assertEqual(client.chat.call_args.kwargs['messages'][-1]['images'], [b'image bytes'])

    def test_cancelled_capture_never_grabs_screen_or_reopens_window(self):
        call = DesktopCall('screenshot', None, self.popup.desktop.context(), Event())
        with patch('integration.desktop.capture_screen') as grab:
            self.popup.desktop.handle(1, call)
            call.cancel.set()
            self.popup.dismiss()
            self.wait_for(call.done.is_set)
            grab.assert_not_called()
        self.assertFalse(self.popup.isVisible())

    def test_capture_failure_restores_opacity_and_reports_error(self):
        call = DesktopCall('screenshot', None, self.popup.desktop.context(), Event())
        with patch('integration.desktop.capture_screen', side_effect=RuntimeError('permission denied')):
            self.popup.desktop.handle(1, call)
            self.wait_for(call.done.is_set)
        self.assertIsInstance(call.error, RuntimeError)
        self.assertEqual(self.popup.windowOpacity(), 1.0)
        self.assertFalse(self.popup.desktop.busy)

    def test_cancelled_capture_cannot_restore_popup_during_new_capture(self):
        context = self.popup.desktop.context()
        first = DesktopCall('screenshot', None, context, Event())
        second = DesktopCall('screenshot', None, context, Event())
        opacity = []
        def grab(screen):
            opacity.append(self.popup.windowOpacity())
            return b'new capture'
        with patch('integration.desktop.capture_screen', side_effect=grab) as capture:
            self.popup.desktop.handle(1, first)
            self.popup.desktop.cancel_pending()
            self.assertTrue(first.done.is_set())
            self.assertTrue(first.cancel.is_set())
            self.popup.desktop.handle(2, second)
            self.wait_for(second.done.is_set)
            capture.assert_called_once()
        self.assertEqual(opacity, [0.0])
        self.assertEqual(second.value, b'new capture')

    def test_capture_encodes_and_resizes_in_memory(self):
        screen = Mock()
        image = QPixmap(1920, 1080)
        image.fill(QColor('navy'))
        screen.grabWindow.return_value = image
        data = capture_screen(screen)
        self.assertTrue(data.startswith(b'\x89PNG'))
        decoded = QImage.fromData(data)
        self.assertEqual((decoded.width(), decoded.height()), (1600, 900))

    def test_shutdown_waits_asynchronously_and_cancels_worker(self):
        self.start_old()
        self.popup.controller.stopped.disconnect()
        stopped = []
        self.popup.controller.stopped.connect(lambda: stopped.append(True))
        self.popup._request_quit()
        self.assertTrue(self.pipeline.cancel.is_set())
        self.assertFalse(stopped)
        ticks = []
        QTimer.singleShot(0, lambda: ticks.append(True))
        self.wait_for(lambda: bool(ticks))
        self.pipeline.release.set()
        self.wait_for(lambda: bool(stopped))
        self.assertIsNone(self.popup.controller._worker)

    def test_shortcut_confirmation_defaults_to_cancel(self):
        call = DesktopCall('command', {'name': 'press_hotkey', 'arguments': {'shortcut': 'ALT+F4'}},
                           {'target': {'hwnd': 1, 'pid': 2, 'title': 'Test window'}}, Event())
        with patch('integration.desktop.send_hotkey') as send, patch('integration.desktop.activate_target') as activate:
            self.popup.desktop.handle(1, call)
            dialog = self.popup.desktop._dialog
            self.assertEqual(dialog.defaultButton(), dialog.button(QMessageBox.StandardButton.Cancel))
            dialog.reject()
            self.wait_for(call.done.is_set)
            send.assert_not_called()
            activate.assert_not_called()
        self.assertIn('Cancelled', call.value)

    def test_approved_shortcut_checks_cancel_before_send(self):
        call = DesktopCall('command', {'name': 'press_hotkey', 'arguments': {'shortcut': 'CTRL+V'}},
                           {'target': {'hwnd': 1, 'pid': 2, 'title': 'Test window'}}, Event())
        with patch('integration.desktop.send_hotkey') as send, patch('integration.desktop.activate_target'):
            self.popup.desktop.handle(1, call)
            self.popup.desktop._dialog.done(QMessageBox.StandardButton.Yes)
            call.cancel.set()
            self.wait_for(call.done.is_set)
            send.assert_not_called()

    def test_simulated_monitors_with_negative_origins_and_scaling(self):
        p = self.popup
        from PySide6.QtCore import QRect
        bounds = QRect(-1280, -200, 1280, 720)
        screen = Mock()
        screen.availableGeometry.return_value = bounds
        screen.devicePixelRatio.return_value = 1.5
        p._expanded = True
        with patch('ui.popup.QApplication.screenAt', return_value=screen):
            for point in (bounds.topLeft(), bounds.topRight(), bounds.bottomLeft(), bounds.bottomRight()):
                with patch('ui.popup.QCursor.pos', return_value=point):
                    p._position_at_cursor()
                    self.assertTrue(bounds.contains(p.geometry()))


if __name__ == '__main__':
    unittest.main()
