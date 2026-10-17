"""Run with python -m unittest discover -s tests (no desktop input generated)."""

import os
import unittest
from contextlib import contextmanager
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ai.pipeline import Result
from ui.popup import AssistantPopup, PopupState


@contextmanager
def mock_clipboard():
    with patch('ui.popup.QApplication.clipboard') as clipboard:
        clipboard.return_value.text.return_value = ''
        def set_text(text):
            clipboard.return_value.text.return_value = text
        clipboard.return_value.setText.side_effect = set_text
        yield clipboard


class InteractionPipeline:
    def run(self, prompt, **kwargs):
        kwargs["status"]("Thinking…")
        kwargs["partial"](f"Test response: {prompt}")
        return Result(f"Test response: {prompt}", [])


class FollowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.motion = patch("ui.popup._motion_enabled", return_value=False)
        self.motion.start()
        self.cursor = QPoint(20, 20)
        self.cursor_patch = patch("ui.popup.QCursor.pos", side_effect=lambda: self.cursor)
        self.cursor_patch.start()
        self.now = 100.0
        self.clock = patch("ui.popup.time.monotonic", side_effect=lambda: self.now)
        self.clock.start()
        self.popup = AssistantPopup(pipeline=InteractionPipeline())
        self.popup.open_at_cursor()
        self.popup._follow_timer.stop()  # Tick deterministically, never move the OS cursor.

    def tearDown(self):
        self.popup.hide()
        self.popup.controller.shutdown()
        self.popup.deleteLater()
        self.app.processEvents()
        self.clock.stop()
        self.cursor_patch.stop()
        self.motion.stop()

    def tick(self, count=1):
        for _ in range(count):
            self.now += 0.016
            self.popup._follow_cursor()

    def test_smooth_follow_and_settle_with_keyboard_focus(self):
        p = self.popup
        p.motion_enabled = True
        p.prompt.setFocus()
        start = p.pos()
        self.cursor = QPoint(600, 20)
        self.tick()
        self.assertNotEqual(p.pos(), start)
        self.assertLess((p.pos() - start).manhattanLength(), 100)
        self.tick(100)
        settled = p.pos()
        self.tick(20)
        self.assertEqual(p.pos(), settled)
        self.assertTrue(p._follow_paused)

    def test_proximity_hysteresis_and_grace(self):
        p = self.popup
        p.move(200, 200)
        self.cursor = QPoint(220, 220)
        self.tick()
        origin = p.pos()
        self.assertTrue(p._follow_paused)
        self.cursor = QPoint(160, 220)  # Outside 45px entry, inside 65px release.
        self.tick(30)
        self.assertEqual(p.pos(), origin)
        self.cursor = QPoint(20, 20)
        self.tick(10)
        self.assertEqual(p.pos(), origin)
        self.tick(15)
        self.assertNotEqual(p.pos(), origin)

    def test_toggle_preserves_position_and_session_setting(self):
        p = self.popup
        p.follow_button.click()
        position = p.pos()
        self.cursor = QPoint(600, 20)
        self.tick(30)
        self.assertEqual(p.pos(), position)
        self.assertFalse(p._follow_timer.isActive())
        p.open_at_cursor()
        self.assertEqual(p.pos(), position)
        p.dismiss()
        p.open_at_cursor()
        self.assertFalse(p.follow_button.isChecked())
        self.assertFalse(p.follow_enabled)
        position = p.pos()
        p.follow_button.click()
        self.assertEqual(p.pos(), position)
        self.assertTrue(p._follow_timer.isActive())

    def test_mouse_drag_pauses_even_outside_surface(self):
        p = self.popup
        self.cursor = QPoint(600, 20)
        position = p.pos()
        with patch("ui.popup.QApplication.mouseButtons", return_value=Qt.MouseButton.LeftButton):
            self.tick(30)
        self.assertEqual(p.pos(), position)
        self.tick(10)
        self.assertEqual(p.pos(), position)
        self.tick(15)
        self.assertNotEqual(p.pos(), position)

    def test_rapid_toggle_and_close_during_opening(self):
        p = self.popup
        p.motion_enabled = True
        p.dismiss()
        for _ in range(12):
            p.follow_button.click()
            self.assertFalse(p._follow_timer.isActive())
        QTest.qWait(180)
        p.open_at_cursor()
        for _ in range(12):
            p.follow_button.click()
        p.dismiss()
        self.assertFalse(p._follow_timer.isActive())
        QTest.qWait(180)
        self.assertFalse(p.isVisible())
        self.assertTrue(p.follow_enabled)

    def test_follow_yields_to_expansion_and_stops_on_close(self):
        p = self.popup
        p.motion_enabled = True
        p.prompt.setText("Expansion while following")
        p.submit()
        self.cursor = QPoint(600, 20)
        position = p.pos()
        self.tick(10)
        self.assertEqual(p.state, PopupState.EXPANDING)
        self.assertEqual(p.pos(), position)
        QTest.qWait(1100)
        self.assertIn("Expansion while following", p.response.toPlainText())
        self.tick()
        self.assertNotEqual(p.pos(), position)
        p.dismiss()
        self.assertFalse(p._follow_timer.isActive())
        QTest.qWait(180)
        self.assertEqual(p.state, PopupState.CLOSED)

    def test_destination_stays_within_screen_at_all_corners(self):
        p = self.popup
        bounds = p.screen().availableGeometry()
        for point in (bounds.topLeft(), bounds.topRight(), bounds.bottomLeft(), bounds.bottomRight()):
            with self.subTest(point=point):
                p._follow_paused = False
                p.move(bounds.center())
                p._follow_position = None
                self.cursor = point
                self.tick()
                self.assertTrue(bounds.contains(p.geometry()))

    def test_motion_accelerates_gently_and_settles_without_repeated_moves(self):
        p = self.popup
        p.motion_enabled = True
        p.move(50, 50)
        self.cursor = QPoint(600, 400)
        distances = []
        for _ in range(20):
            previous = p.pos()
            self.tick()
            distances.append((p.pos() - previous).manhattanLength())
        self.assertGreater(distances[0], 0)
        self.assertLess(distances[0], distances[1])
        self.assertLess(distances[1], max(distances))
        self.assertLess(distances[-1], max(distances))
        self.tick(150)
        with patch.object(p, 'setGeometry', wraps=p.setGeometry) as move:
            self.tick(50)
            move.assert_not_called()

    def test_motion_is_consistent_across_timer_rates(self):
        p = self.popup
        p.motion_enabled = True
        screen = Mock()
        screen.availableGeometry.return_value = QRect(0, 0, 1920, 1200)
        self.cursor = QPoint(900, 600)
        positions = []
        with (patch('ui.popup.QApplication.screenAt', return_value=screen),
              patch('ui.popup.QApplication.screens', return_value=[screen])):
            for interval in (0.008, 0.016, 0.032):
                p.move(50, 50)
                p._reset_follow_motion()
                p._follow_paused = False
                p._placement_bounds = None
                p._follow_last_tick = self.now
                for _ in range(round(0.096 / interval)):
                    self.now += interval
                    p._follow_cursor()
                positions.append(p.pos())
        for position in positions[1:]:
            self.assertLessEqual((position - positions[0]).manhattanLength(), 1)

    def test_side_hysteresis_prevents_repeated_flips_near_all_edges(self):
        p = self.popup
        bounds = QRect(0, 0, 1600, 1000)
        width, height = p.width(), p.height()
        x_threshold = bounds.right() + 1 - width - p.CURSOR_OFFSET
        y_threshold = bounds.bottom() + 1 - height - p.CURSOR_OFFSET
        p._cursor_destination(QPoint(x_threshold - 20, y_threshold - 20), bounds, width, height, reset=True)
        self.assertEqual(p._placement_sides, (1, 1))
        for offset in (-12, 12, -8, 8) * 5:
            goal = p._cursor_destination(QPoint(x_threshold + offset, y_threshold + offset), bounds, width, height)
            self.assertEqual(p._placement_sides, (1, 1))
            self.assertTrue(bounds.contains(QRect(goal.toPoint(), p.size())))
        p._cursor_destination(QPoint(1500, 970), bounds, width, height)
        self.assertEqual(p._placement_sides, (-1, -1))
        for offset in (-12, 12, -8, 8) * 5:
            p._cursor_destination(QPoint(x_threshold + offset, y_threshold + offset), bounds, width, height)
            self.assertEqual(p._placement_sides, (-1, -1))
        p._cursor_destination(QPoint(40, 40), bounds, width, height)
        self.assertEqual(p._placement_sides, (1, 1))

    def test_small_pointer_noise_does_not_retarget_motion(self):
        p = self.popup
        p.motion_enabled = True
        p.move(50, 50)
        self.cursor = QPoint(600, 400)
        self.tick()
        goal = QPointF(p._follow_goal)
        self.cursor = QPoint(601, 401)
        self.tick()
        self.assertEqual(p._follow_goal, goal)
        self.cursor = QPoint(610, 410)
        self.tick()
        self.assertNotEqual(p._follow_goal, goal)

    def test_follow_resumes_gently_after_toggle_and_capture_pause(self):
        p = self.popup
        p.motion_enabled = True
        p.follow_button.setChecked(False)
        self.cursor = QPoint(600, 400)
        origin = p.pos()
        self.tick(20)
        self.assertEqual(p.pos(), origin)
        p.follow_button.setChecked(True)
        p._follow_timer.stop()
        self.tick()
        self.assertLess((p.pos() - origin).manhattanLength(), 20)
        p.desktop.busy = True
        origin = p.pos()
        self.tick(100)
        self.assertEqual(p.pos(), origin)
        self.assertIsNone(p._follow_position)
        p.desktop.busy = False
        self.tick()
        self.assertLess((p.pos() - origin).manhattanLength(), 20)

    def test_cross_monitor_follow_uses_logical_coordinates_and_stays_visible(self):
        p = self.popup
        p.motion_enabled = True
        for bounds in ((QRect(-1280, -200, 1280, 720), QRect(0, 0, 1920, 1080)),
                       (QRect(0, 0, 800, 600), QRect(1600, 400, 800, 600))):
            with self.subTest(bounds=bounds):
                screens = [Mock(), Mock()]
                for screen, area, scale in zip(screens, bounds, (1.5, 2)):
                    screen.availableGeometry.return_value = area
                    screen.devicePixelRatio.return_value = scale
                p.move(bounds[0].topLeft() + QPoint(40, 40))
                p._reset_follow_motion()
                p._placement_bounds = None
                p._follow_paused = False
                self.cursor = bounds[1].bottomRight() - QPoint(20, 20)
                with (patch('ui.popup.QApplication.screenAt', return_value=screens[1]),
                      patch('ui.popup.QApplication.screens', return_value=screens)):
                    origin = p.pos()
                    self.tick()
                    self.assertLess((p.pos() - origin).manhattanLength(), 100)
                    for _ in range(250):
                        self.tick()
                        self.assertTrue(any(area.intersects(p.geometry()) for area in bounds))
                self.assertTrue(bounds[1].contains(p.geometry()))
                self.assertEqual(p.width(), p.WIDTH + 2 * p.SHADOW_MARGIN)

    def finish_response(self, text, *, streamed=False, request_id=100):
        p = self.popup
        p._cancel_response()
        p._copy_available = False
        p.response.clear()
        p._request_id = request_id
        p._expand_to(p.RESPONSE_HEIGHT)
        p.thinking.show()
        if streamed:
            for part in (text[:len(text) // 2], text[len(text) // 2:]):
                p._pipeline_partial(request_id, part)
                self.assertFalse(p.copy_button.isEnabled())
        p._pipeline_completed(request_id, text)
        self.app.processEvents()

    def test_copy_is_disabled_until_a_completed_response(self):
        p = self.popup
        self.assertFalse(p.copy_button.isEnabled())
        p._request_id = 100
        p._expand_to(260)
        p.thinking.show()
        p._pipeline_status(100, 'Thinking…')
        self.assertFalse(p.copy_button.isEnabled())
        p._pipeline_partial(100, 'An unfinished answer')
        self.assertFalse(p.copy_button.isEnabled())
        p._pipeline_failed(100, 'Ollama is unavailable')
        self.assertFalse(p.copy_button.isEnabled())

    def test_copy_streamed_answer_only_and_brief_checkmark_feedback(self):
        p = self.popup
        p.prompt.setText('The user prompt must not be copied')
        self.finish_response('The displayed answer.\nOnly this text.', streamed=True)
        self.assertTrue(p.copy_button.isEnabled())
        self.assertEqual(p.copy_button.accessibleName(), 'Copy response')
        with mock_clipboard() as clipboard:
            p.copy_button.click()
            clipboard.return_value.setText.assert_called_once_with('The displayed answer.\nOnly this text.')
        self.assertTrue(p.copy_button.copied)
        self.assertEqual(p.copy_button.toolTip(), 'Copied')
        self.assertEqual(p.copy_notice.text(), 'Copied')
        p._copy_timer.setInterval(10)
        p._copy_timer.start()
        QTest.qWait(30)
        self.assertFalse(p.copy_button.copied)
        self.assertEqual(p.copy_notice.text(), '')
        self.assertEqual(p.copy_button.toolTip(), 'Copy response')

    def test_copy_tool_result_and_reset_for_new_response(self):
        p = self.popup
        self.finish_response('Asked Windows to open notepad.')
        with mock_clipboard() as clipboard:
            p.copy_button.click()
            self.assertTrue(p.copy_button.copied)
            p.prompt.setText('A new request')
            p.submit()
            self.assertFalse(p.copy_button.isEnabled())
            self.assertFalse(p.copy_button.copied)
            self.assertFalse(p._copy_timer.isActive())
            p._pipeline_partial(100, 'A stale response')
            self.assertFalse(p.copy_button.isEnabled())
            # Await the queued worker/stream callbacks rather than relying on a
            # fixed 100ms scheduling window while audio libraries are loaded.
            for _ in range(100):
                if p.copy_button.isEnabled():
                    break
                QTest.qWait(10)
            self.assertTrue(p.copy_button.isEnabled())
            p.copy_button.click()
            self.assertEqual(clipboard.return_value.setText.call_args.args[0], 'Test response: A new request')

    def test_copy_does_not_cover_scrollbar_or_change_selection_and_scroll(self):
        p = self.popup
        text = 'A long response line.\n' * 100
        self.finish_response(text)
        scrollbar = p.response.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum() // 2)
        selection = p.response.textCursor()
        selection.setPosition(10)
        selection.setPosition(24, QTextCursor.MoveMode.KeepAnchor)
        p.response.setTextCursor(selection)
        selected = p.response.textCursor().selectedText()
        scroll_position = scrollbar.value()
        self.assertFalse(p.copy_button.geometry().intersects(p.response.viewport().geometry()))
        scrollbar_rect = QRect(scrollbar.mapTo(p.response, QPoint()), scrollbar.size())
        self.assertFalse(p.copy_button.geometry().intersects(scrollbar_rect))
        with mock_clipboard() as clipboard:
            p.copy_button.click()
            clipboard.return_value.setText.assert_called_once_with(text)
        self.assertEqual(p.response.textCursor().selectedText(), selected)
        self.assertEqual(scrollbar.value(), scroll_position)

    def test_copy_receives_pointer_click_without_header_overlay(self):
        p = self.popup
        self.finish_response('Copy this complete answer.', streamed=True)
        self.app.processEvents()
        point = p.copy_button.mapTo(p, p.copy_button.rect().center())
        self.assertIs(p.childAt(point), p.copy_button)
        with mock_clipboard() as clipboard:
            QTest.mouseClick(p.windowHandle(), Qt.MouseButton.LeftButton, pos=point)
            clipboard.return_value.setText.assert_called_once_with('Copy this complete answer.')
        self.assertTrue(p.copy_button.copied)

    def test_copy_retries_busy_clipboard_and_confirms_success(self):
        p = self.popup
        self.finish_response('Clipboard retry test')
        with patch('ui.popup.QApplication.clipboard') as clipboard:
            clipboard.return_value.text.side_effect = ['', 'Clipboard retry test']
            p.copy_button.click()
            self.assertFalse(p.copy_button.copied)
            QTest.qWait(100)
            self.assertEqual(clipboard.return_value.setText.call_count, 2)
            self.assertEqual(p.copy_notice.text(), 'Copied')
            self.assertTrue(p.copy_button.copied)

    def test_copy_failure_is_visible_and_retry_stops_when_response_changes(self):
        p = self.popup
        self.finish_response('Clipboard unavailable test')
        with patch('ui.popup.QApplication.clipboard') as clipboard:
            clipboard.return_value.text.return_value = ''
            p.copy_button.click()
            QTest.qWait(150)
            self.assertEqual(clipboard.return_value.setText.call_count, 3)
            self.assertFalse(p.copy_button.copied)
            self.assertEqual(p.copy_notice.text(), 'Copy failed')
            p.copy_button.click()
            p.response.clear()
            attempts = clipboard.return_value.setText.call_count
            QTest.qWait(100)
            self.assertEqual(clipboard.return_value.setText.call_count, attempts)
            self.assertEqual(p.copy_notice.text(), '')

    def test_opening_hotkey_toggles_and_escape_dismisses(self):
        p = self.popup
        p.toggle_at_cursor()
        self.assertFalse(p.isVisible())
        p.toggle_at_cursor()
        self.assertTrue(p.isVisible())
        self.app.setActiveWindow(p)
        p.prompt.setFocus()
        self.app.processEvents()
        QTest.keyClick(p.prompt, Qt.Key.Key_Escape)
        self.assertFalse(p.isVisible())

    def test_focused_quit_binding(self):
        p = self.popup
        self.assertEqual([key.toString() for key in p.quit_shortcut.keys()], ["Ctrl+Q"])  # noqa: SIM118 - Qt API
        self.assertEqual(p.quit_shortcut.context(), Qt.ShortcutContext.WindowShortcut)


if __name__ == "__main__":
    unittest.main()
