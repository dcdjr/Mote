"""New tools: ordering, preflight, truthful capabilities, audio and app discovery."""
from contextlib import contextmanager
import json
import unittest
from threading import Event
from unittest.mock import Mock, patch, call

from ai.pipeline import MotePipeline, PipelineError, Cancelled
from ai.runtime import model_options
from integration.actions import WindowsActions
from integration.apps import AppCatalog, InstalledApp
from integration.audio import change_volume
from router.router import MoteRouter, _parse_selection
from router.urls import normalize_url
from test_pipeline import make_pipeline


class PlanTests(unittest.TestCase):
    def test_real_fast_router_dispatches_youtube_then_volume(self):
        pipeline, client = make_pipeline()
        pipeline.router = MoteRouter()
        pipeline.router._request = Mock(side_effect=AssertionError('No inference expected'))
        desktop = Mock(return_value='Done')
        result = pipeline.run('open youtube and set the volume to 50', desktop=desktop)
        self.assertEqual([c.args[1]['name'] for c in desktop.call_args_list if c.args[0] == 'command'],
                         ['open_website', 'set_volume'])
        self.assertIn('1. Done\n2. Done', result.text)
        client.chat.assert_not_called()

    def test_entire_plan_validated_before_any_desktop_call(self):
        pipeline, _ = make_pipeline()
        pipeline.router.route = Mock(return_value={'route': 'command', 'reasoning': False, 'screenshot': False,
            'command': [{'name': 'open_application', 'arguments': {'application': 'notepad'}},
                        {'name': 'set_volume', 'arguments': {'level_percent': 101}}]})
        desktop = Mock()
        with self.assertRaises(PipelineError):
            pipeline.run('Do both', desktop=desktop)
        desktop.assert_not_called()

    def test_preflight_failure_prevents_all_commands(self):
        pipeline, _ = make_pipeline()
        desktop = Mock(side_effect=PipelineError('App name ambiguous'))
        with self.assertRaisesRegex(PipelineError, 'ambiguous'):
            pipeline.run('Open Notepad and set volume to 50', desktop=desktop)
        self.assertEqual(desktop.call_count, 1)
        self.assertEqual(desktop.call_args.args[0], 'prepare_commands')

    def test_failure_reports_previous_success_and_stops_remaining_actions(self):
        pipeline, _ = make_pipeline()
        desktop = Mock(side_effect=[None, 'Opened YouTube', PipelineError('Audio unavailable')])
        result = pipeline.run('open youtube and set volume to 50 and open Notepad', desktop=desktop)
        self.assertIn('1. Opened YouTube', result.text)
        self.assertIn('2. Failed: Audio unavailable', result.text)
        self.assertIn('Remaining 1 action', result.text)
        self.assertEqual(desktop.call_count, 3)
        self.assertEqual(result.history[-1]['content'], result.text)

    def test_cancellation_between_commands_stops_remaining(self):
        pipeline, _ = make_pipeline()
        cancel = Event()
        def desktop(kind, payload):
            if kind == 'command':
                cancel.set()
            return 'Done'
        with self.assertRaises(Cancelled):
            pipeline.run('open youtube and set volume to 50', desktop=desktop, cancel=cancel)

    def test_declined_shortcut_stops_plan(self):
        pipeline, _ = make_pipeline()
        plan = {'commands': [{'function': 'press_hotkey', 'arguments': {'shortcut': 'CTRL+S'}},
                             {'function': 'open_application', 'arguments': {'application': 'notepad'}}]}
        pipeline.router.route = Mock(return_value=_parse_selection(json.dumps(plan)))
        desktop = Mock(side_effect=[None, 'Cancelled. No keyboard input was sent.'])
        result = pipeline.run('Save and open notepad', desktop=desktop)
        self.assertIn('not run after cancellation', result.text)
        self.assertEqual(desktop.call_count, 2)

    def test_screen_classifier_miss_still_attaches_image(self):
        pipeline, client = make_pipeline('answer_quick')
        desktop = Mock(return_value=b'PNG')
        result = pipeline.run('What is on my screen?', desktop=desktop)
        self.assertEqual(client.chat.call_args.kwargs['messages'][-1]['images'], [b'PNG'])
        self.assertNotIn('images', str(result.history))

    def test_answer_model_knows_tools_and_current_screen_status(self):
        pipeline, client = make_pipeline()
        pipeline.run('What can you do?', desktop=Mock())
        system = client.chat.call_args.kwargs['messages'][0]['content']
        for item in ['Mote', 'installed applications', 'HTTP/HTTPS', 'set volume', 'eight supported commands',
                     'screenshot', 'test-model', 'THIS request: False', 'not implemented']:
            self.assertIn(item, system)

    def test_shared_gpu_context_and_keepalive_options(self):
        with patch.dict('os.environ', {'MOTE_GPU_LAYERS': '999', 'MOTE_CONTEXT_LENGTH': '4096', 'MOTE_KEEP_ALIVE': '-1'}):
            pipeline, client = make_pipeline()
            pipeline.run('Hello')
            answer = client.chat.call_args.kwargs
            route = pipeline.router._request.call_args.args[0]
            self.assertEqual(answer['options']['num_ctx'], route['options']['num_ctx'])
            self.assertEqual(answer['options']['num_gpu'], 999)
            self.assertEqual(route['options']['num_gpu'], 999)
            self.assertEqual(answer['keep_alive'], '-1')
            self.assertEqual(route['keep_alive'], '-1')

    def test_invalid_runtime_settings_rejected(self):
        for setting in [{'MOTE_CONTEXT_LENGTH': '0'}, {'MOTE_GPU_LAYERS': '-5'}]:
            with self.subTest(setting=setting), patch.dict('os.environ', setting), self.assertRaises(ValueError):
                model_options()


class CatalogTests(unittest.TestCase):
    def catalog(self, apps):
        discover = Mock(return_value=apps)
        return AppCatalog(discover), discover

    def test_apps_outside_original_list_and_cached_discovery(self):
        catalog, discover = self.catalog([InstalledApp('Visual Studio Code', 'local-vscode.lnk'),
                                         InstalledApp('Mozilla Firefox', 'local-firefox.lnk')])
        self.assertEqual(catalog.resolve('VS Code').name, 'Visual Studio Code')
        self.assertEqual(catalog.resolve('Firefox').name, 'Mozilla Firefox')
        discover.assert_called_once()

    def test_ambiguous_or_missing_apps_are_not_substituted(self):
        catalog, _ = self.catalog([InstalledApp('Visual Studio Code', 'a'), InstalledApp('Visual Studio Community', 'b')])
        with self.assertRaisesRegex(PipelineError, 'Multiple apps'):
            catalog.resolve('Visual Studio')
        with self.assertRaisesRegex(PipelineError, 'Could not find'):
            catalog.resolve('Thunderbird')

    def test_duplicate_shortcut_and_aumid_do_not_create_false_ambiguity(self):
        catalog, _ = self.catalog([InstalledApp('Firefox', 'shortcut'), InstalledApp('Firefox', 'appid', 'aumid')])
        self.assertEqual(catalog.resolve('Firefox').target, 'shortcut')

    def test_executor_uses_discovered_target_not_request_as_path(self):
        catalog, _ = self.catalog([InstalledApp('Thunderbird', 'local-thunderbird.lnk')])
        actions = WindowsActions(catalog=catalog)
        with patch('integration.actions.sys.platform', 'win32'), patch('integration.apps.os.startfile', create=True) as launch:
            actions.execute({'name': 'open_application', 'arguments': {'application': 'Thunderbird'}})
            launch.assert_called_once_with('local-thunderbird.lnk')

    def test_missing_app_fails_preflight_before_launch(self):
        catalog, _ = self.catalog([])
        with patch('integration.actions.sys.platform', 'win32'), self.assertRaisesRegex(PipelineError, 'Could not find'):
            WindowsActions(catalog=catalog).prepare_commands([{'name': 'open_application', 'arguments': {'application': 'NoSuchApp'}}])

    def test_aumid_launch_uses_argument_array(self):
        app = InstalledApp('Store app', 'Package!App', 'aumid')
        with patch('integration.apps.subprocess.Popen') as launch:
            AppCatalog().launch(app)
            self.assertEqual(launch.call_args.args[0][1], 'shell:AppsFolder\\Package!App')
            self.assertNotIn('shell', launch.call_args.kwargs)


class AudioTests(unittest.TestCase):
    def setUp(self):
        self.endpoint = Mock()
        self.endpoint.GetMasterVolumeLevelScalar.return_value = .98
        @contextmanager
        def endpoint():
            yield self.endpoint
        self.patch = patch('integration.audio.endpoint_volume', endpoint)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_percent_uses_scalar_not_decibels_and_unmutes(self):
        self.assertIn('50%', change_volume('set_volume', {'level_percent': 50}))
        self.endpoint.SetMasterVolumeLevelScalar.assert_called_once_with(.5, None)
        self.endpoint.SetMute.assert_called_once_with(False, None)

    def test_relative_volume_clamped_and_mute_supported(self):
        change_volume('adjust_volume', {'operation': 'increase'})
        self.endpoint.SetMasterVolumeLevelScalar.assert_called_with(1.0, None)
        self.endpoint.GetMasterVolumeLevelScalar.return_value = .02
        change_volume('adjust_volume', {'operation': 'decrease'})
        self.endpoint.SetMasterVolumeLevelScalar.assert_called_with(0.0, None)
        change_volume('adjust_volume', {'operation': 'mute'})
        self.endpoint.SetMute.assert_called_with(True, None)
        change_volume('adjust_volume', {'operation': 'unmute'})
        self.endpoint.SetMute.assert_called_with(False, None)


class WebsiteTests(unittest.TestCase):
    def test_any_domain_path_query_unicode_and_localhost(self):
        for value, expected in [('example.org/docs?q=a&b=c', 'https://example.org/docs?q=a&b=c'),
                                ('http://localhost:8000', 'http://localhost:8000'),
                                ('https://例え.jp', 'https://xn--r8jz45g.jp'),
                                ('https://example.org/search?q=why?', 'https://example.org/search?q=why?')]:
            with self.subTest(value=value):
                self.assertEqual(normalize_url(value), expected)

    def test_unsafe_or_incomplete_websites_rejected(self):
        for value in ['file:///C:/Windows', 'javascript:alert(1)', 'example.org\\foo', 'https://user:pw@example.org',
                      'https://example.org:bad', 'https://example.org\n', 'unknownbrand', 'https://bad..org']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_url(value)


class TransportTests(unittest.TestCase):
    def test_real_ollama_client_serializes_captured_image_as_base64(self):
        import base64
        import httpx
        from ollama import Client
        from ai.gemma import GemmaBackend
        requests = []
        def respond(request):
            payload = json.loads(request.content)
            if request.url.path == '/api/show':
                return httpx.Response(200, json={'capabilities': ['completion', 'vision'], 'model_info': {}})
            requests.append(payload)
            return httpx.Response(200, content=json.dumps({'message': {'role': 'assistant', 'content': 'I see an error.'}, 'done': True}) + '\n')
        client = Client(host='http://test.invalid', transport=httpx.MockTransport(respond))
        pipeline = MotePipeline(router=MoteRouter(), backend=GemmaBackend(client=client, model='vision-test'))
        pipeline.router._request = Mock(return_value={'message': {'content': json.dumps({'function': 'answer_quick', 'arguments': {}})}})
        result = pipeline.run('What is on my screen?', desktop=Mock(return_value=b'actual image bytes'))
        self.assertEqual(requests[0]['messages'][-1]['images'], [base64.b64encode(b'actual image bytes').decode()])
        self.assertIn('THIS request: True', requests[0]['messages'][0]['content'])
        self.assertNotIn('images', str(result.history))

    def test_diagnostics_warmup_deduplicates_shared_model_and_reports_gpu(self):
        import io
        from ai.diagnostics import inspect_models
        seen = []
        def respond(request, **kwargs):
            payload = json.loads(request.data) if request.data else None
            seen.append((request.full_url, payload))
            if request.full_url.endswith('/api/ps'):
                value = {'models': [{'name': 'shared:latest', 'size': 1000, 'size_vram': 1000}]}
            elif request.full_url.endswith('/api/show'):
                value = {'capabilities': ['completion', 'vision']}
            else:
                value = {'done': True}
            return io.BytesIO(json.dumps(value).encode())
        with patch.dict('os.environ', {'MOTE_MODEL': 'shared', 'MOTE_ROUTER_MODEL': 'shared'}), patch('ai.diagnostics.urllib.request.urlopen', side_effect=respond):
            report = inspect_models(warmup=True)
        self.assertEqual(sum(url.endswith('/api/chat') for url, _ in seen), 1)
        self.assertEqual(report['models'][0]['gpu_percent'], 100)
        self.assertEqual(report['models'][0]['capabilities'], ['completion', 'vision'])

    def test_non_gui_commands_run_on_worker_without_gui_signal(self):
        from ai.worker import RequestWorker
        pipeline, _ = make_pipeline()
        worker = RequestWorker(pipeline, 1, 'Open app', [], {}, None)
        signal = Mock()
        worker.desktop_requested.connect(signal)
        with patch.object(worker.actions, 'prepare_commands', return_value=None) as prepare, patch.object(worker.actions, 'execute', return_value='Opened') as execute:
            worker._desktop('prepare_commands', [{'name': 'open_application', 'arguments': {'application': 'Firefox'}}])
            result = worker._desktop('command', {'name': 'open_application', 'arguments': {'application': 'Firefox'}})
            self.assertEqual(result, 'Opened')
            prepare.assert_called_once()
            execute.assert_called_once()
            signal.assert_not_called()


class FinalEdgeTests(unittest.TestCase):
    def test_auto_offload_is_explicit_and_cpu_override_is_respected(self):
        with patch.dict('os.environ', {'MOTE_GPU_LAYERS': 'auto'}):
            self.assertEqual(model_options()['num_gpu'], -1)
        with patch.dict('os.environ', {'MOTE_GPU_LAYERS': '0'}):
            self.assertEqual(model_options()['num_gpu'], 0)

    def test_office_names_match_both_registration_styles(self):
        catalog = AppCatalog(lambda: [InstalledApp('Word', 'word.lnk')])
        self.assertEqual(catalog.resolve('Microsoft Word').target, 'word.lnk')
        catalog = AppCatalog(lambda: [InstalledApp('Microsoft Word', 'word.lnk')])
        self.assertEqual(catalog.resolve('Word').target, 'word.lnk')

    def test_browser_suffix_and_file_explorer_names(self):
        router = MoteRouter()
        router._request = Mock(side_effect=AssertionError('Expected fast path'))
        self.assertEqual(router.route('Open the Edge browser')['command']['arguments']['application'], 'edge')
        self.assertEqual(router.route('Open File Explorer')['command']['arguments']['application'], 'file_explorer')


if __name__ == '__main__':
    unittest.main()
