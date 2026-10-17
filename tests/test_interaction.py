"""Run with python -m unittest discover -s tests (no desktop input generated)."""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ai.pipeline import Result
from ui.popup import AssistantPopup, PopupState


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
