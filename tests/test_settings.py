"""Release settings checks: no native input, real screens, or downloaded models."""
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import ctypes
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from test_pipeline import make_pipeline

from ai.models import discover_models, eligibility, parameter_count
from ai.worker import PipelineController
from config import Preferences
from integration.hotkey import WindowsHotkey, parse_shortcut
from ui.popup import GLASS_RADIUS, AssistantPopup
from ui.settings import SettingsPanel


class ModelEligibilityTests(unittest.TestCase):
    def test_only_verified_vision_and_total_count_under_limit_are_eligible(self):
        info = {'capabilities':['completion','vision'], 'model_info':{'general.parameter_count':5_100_000_000}}
        self.assertTrue(eligibility('gemma4:e2b', info)[0])
        for name, data in [('gemma4:e4b',info), ('llama3.2-vision:11b',{'capabilities':['vision'],'details':{'parameter_size':'11B'}}),
                           ('phi3.5:3.8b',{'capabilities':['completion'],'details':{'parameter_size':'3.8B'}}),
                           ('unknown',{'capabilities':['vision'],'size':2_000_000_000}),
                           ('ambiguous',{'capabilities':['vision'],'details':{'parameter_size':'4B active'}})]:
            with self.subTest(name=name):
                self.assertFalse(eligibility(name,data)[0])
        for name in ('qwen3-vl:2b','qwen3-vl:4b','qwen2.5vl:3b','gemma3:4b','custom-phi-vision','custom-llama-vision'):
            self.assertTrue(eligibility(name,{'capabilities':['vision'],'details':{'parameter_size':'4B'}})[0])

    def test_total_metadata_wins_over_effective_label(self):
        info = {'capabilities':['vision'],'details':{'parameter_size':'4B'},'model_info':{'general.parameter_count':8_000_000_000}}
        self.assertFalse(eligibility('alias', info)[0])
        self.assertIsNone(parameter_count({'size':4_000_000_000}))

    def test_discovery_inspects_each_model_and_excludes_failed_metadata(self):
        client = Mock()
        client.list.return_value = {'models':[{'model':'gemma3:4b'},{'model':'broken'}]}
        client.show.side_effect = [{'capabilities':['vision'],'details':{'parameter_size':'4B'}},OSError('offline')]
        rows = discover_models(client)
        self.assertEqual(client.show.call_count,2)
        self.assertEqual([r['eligible'] for r in rows],[True,False])

    def test_switch_defers_until_current_worker_boundary_and_clears_cache(self):
        pipeline, _ = make_pipeline()
        controller = PipelineController(pipeline)
        original = pipeline.backend
        controller._worker = Mock()
        controller.set_model('gemma3:4b')
        self.assertIs(pipeline.backend,original)
        controller._worker = None
        controller._apply_model()
        self.assertEqual(pipeline.backend.model,'gemma3:4b')
        self.assertIs(pipeline.backend.client,original.client)
        self.assertIsNone(pipeline.backend._info)
        self.assertIsNone(controller._pending_model)
        controller.deleteLater()


class ShortcutTests(unittest.TestCase):
    def test_valid_and_invalid_shortcuts(self):
        self.assertEqual(parse_shortcut('Ctrl+Shift+Space')[1:],(0x4006,0x20))
        self.assertEqual(parse_shortcut('Alt+F8')[2],0x77)
        for text in ('Ctrl','Ctrl+Shift','A','Shift+A','Ctrl+Q','Ctrl+F12','Ctrl+A, Ctrl+B','Esc'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_shortcut(text)

    def make_hotkey(self):
        api = Mock()
        api.RegisterHotKey.return_value = 1
        api.UnregisterHotKey.return_value = 1
        with patch('integration.hotkey.sys.platform','win32'), patch.object(ctypes,'WinDLL',return_value=api,create=True):
            hotkey = WindowsHotkey(lambda:None)
        app = Mock()
        hotkey.start(app)
        return hotkey,api

    def test_reassignment_registers_new_before_releasing_previous(self):
        hotkey,api = self.make_hotkey()
        api.reset_mock()
        hotkey.reassign('Ctrl+Alt+M')
        self.assertEqual([c[0] for c in api.method_calls],['RegisterHotKey','UnregisterHotKey'])
        self.assertEqual(hotkey.shortcut,'Ctrl+Alt+M')
        hotkey.stop()
        api.UnregisterHotKey.assert_called_with(None,2)

    def test_conflict_preserves_previous_binding(self):
        hotkey,api = self.make_hotkey()
        api.reset_mock()
        api.RegisterHotKey.return_value = 0
        with self.assertRaises(RuntimeError):
            hotkey.reassign('Ctrl+Alt+M')
        api.UnregisterHotKey.assert_not_called()
        self.assertEqual(hotkey.shortcut,'Ctrl+Shift+Space')


class SettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'settings.ini'
        self.preferences = Preferences(self.path)
        self.motion = patch('ui.popup._motion_enabled', return_value=False)
        self.motion.start()
        pipeline,_ = make_pipeline()
        self.popup = AssistantPopup(pipeline=pipeline)
        self.popup.configure_preferences(self.preferences)
        self.panel = SettingsPanel(self.popup,self.preferences)

    def tearDown(self):
        self.panel.shutdown()
        self.panel.hide()
        self.panel.deleteLater()
        self.popup.hide()
        self.popup.voice.shutdown()
        self.popup.controller.shutdown()
        self.popup.deleteLater()
        self.app.processEvents()
        self.motion.stop()
        self.temp.cleanup()

    def test_persistence_and_safe_opacity_limits(self):
        self.preferences.set('model','gemma3:4b')
        self.preferences.set('opacity',1)
        self.preferences.set('shortcut','Ctrl+Alt+M')
        self.preferences.set('follow',False)
        restored=Preferences(self.path)
        self.assertEqual(restored.model,'gemma3:4b')
        self.assertEqual(restored.opacity,60)
        self.assertEqual(restored.shortcut,'Ctrl+Alt+M')
        self.assertFalse(restored.follow)

    def test_transparency_changes_surface_alpha_not_window_opacity(self):
        self.popup.setWindowOpacity(.42)
        self.panel.opacity.setValue(70)
        self.assertAlmostEqual(self.popup.windowOpacity(),.42,delta=.01)
        self.assertIn('rgba(49, 53, 60, 167)',self.popup.styleSheet())
        self.assertEqual(Preferences(self.path).opacity,70)
        self.assertGreaterEqual(self.popup.styleSheet().count(f'border-radius: {GLASS_RADIUS}px;'),2)
        self.popup.setWindowOpacity(1)
        self.assertIn('rgba(49, 53, 60, 167)',self.popup.styleSheet())

    def test_ineligible_models_are_not_selectable_and_switch_is_persisted(self):
        self.panel.show_models([{'name':'gemma3:4b','eligible':True,'detail':'Vision'},
                               {'name':'llama3.2-vision:11b','eligible':False,'detail':'Too large'}])
        self.assertEqual(self.panel.models.count(),2)
        self.panel.models.setCurrentIndex(1)
        self.panel.apply_model()
        self.assertEqual(self.popup.controller.pipeline.backend.model,'gemma3:4b')
        self.assertEqual(Preferences(self.path).model,'gemma3:4b')

    def test_panel_is_bounded_closes_and_follow_setting_stays_synced(self):
        with patch.object(self.panel,'refresh'):
            self.panel.open_near()
        self.app.processEvents()
        self.assertTrue(self.panel.screen().availableGeometry().contains(self.panel.geometry()))
        self.panel.follow.setChecked(False)
        self.assertFalse(self.popup.follow_enabled)
        self.assertFalse(Preferences(self.path).follow)
        self.panel.dismiss()
        QTest.qWait(5)
        self.assertFalse(self.panel.isVisible())

    def test_settings_hotkey_failure_does_not_persist_bad_binding(self):
        self.panel.hotkey = Mock(shortcut='Ctrl+Shift+Space')
        self.panel.hotkey.reassign.side_effect=RuntimeError('Conflict')
        self.panel.shortcut.setKeySequence('Ctrl+Alt+M')
        self.panel.apply_shortcut()
        self.assertEqual(self.preferences.shortcut,'Ctrl+Shift+Space')
        self.assertIn('Conflict',self.panel.status.text())

class PackagingTests(unittest.TestCase):
    def test_smoke_contract_imports_all_dynamic_audio_and_document_dependencies(self):
        from main import verify_runtime_dependencies
        module = Mock()
        module.QTextToSpeech.availableEngines.return_value = ['sapi']
        with patch('main.sys.platform','win32'), patch('importlib.import_module',return_value=module) as load:
            verify_runtime_dependencies()
        names = [c.args[0] for c in load.call_args_list]
        for name in ('av','ctranslate2','faster_whisper','numpy','pypdf','sounddevice','pycaw.pycaw','comtypes','PySide6.QtTextToSpeech'):
            self.assertIn(name,names)

    def test_icon_has_required_resolutions_and_packaging_specs_compile(self):
        import struct
        root=Path(__file__).resolve().parents[1]
        icon=(root/'assets/mote.ico').read_bytes()
        reserved,kind,count=struct.unpack_from('<HHH',icon)
        self.assertEqual((reserved,kind),(0,1))
        sizes=[icon[6+16*i] or 256 for i in range(count)]
        for required in (16,32,48,256):
            self.assertIn(required,sizes)
        compile((root/'packaging/Mote.spec').read_text(),'Mote.spec','exec')


if __name__ == '__main__':
    unittest.main()
