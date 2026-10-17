"""Model/router tests use only mocked Ollama calls; no models or desktop required."""

import json
import unittest
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from ollama import ResponseError

from ai.gemma import GemmaBackend
from ai.pipeline import (
    Cancelled,
    MotePipeline,
    PipelineError,
    recent_history,
)
from integration.actions import WindowsActions
from router import MoteRouter, RouterError
from router.router import _parse_selection


def chunk(content='', thinking=''):
    return SimpleNamespace(message=SimpleNamespace(content=content, thinking=thinking))


def make_pipeline(function='answer_quick', arguments=None, parts=('Hello', ' world'), info=None):
    router = MoteRouter()
    router._request = Mock(return_value={'message': {'content': json.dumps({
        'function': function, 'arguments': arguments or {},
    })}})
    client = Mock()
    client.show.return_value = info or {'capabilities': ['completion', 'vision', 'thinking']}
    client.chat.side_effect = lambda **kwargs: iter([chunk(thinking='private trace'), *[chunk(p) for p in parts]])
    backend = GemmaBackend(client=client, model='test-model')
    return MotePipeline(router=router, backend=backend), client


class PipelineTests(unittest.TestCase):
    def test_normal_question_routed_to_streaming_gemma(self):
        pipeline, client = make_pipeline()
        status, partial = [], []
        result = pipeline.run('Hello?', status=status.append, partial=partial.append)
        self.assertEqual(result.text, 'Hello world')
        self.assertEqual(partial, ['Hello', ' world'])
        self.assertEqual(status, ['Routing…', 'Thinking…', 'Streaming…'])
        self.assertFalse(client.chat.call_args.kwargs['think'])
        self.assertEqual(client.chat.call_args.kwargs['messages'][-1]['content'], 'Hello?')
        self.assertNotIn('private trace', str(result.history))
        self.assertEqual(pipeline.router._request.call_count, 1)

    def test_reasoning_is_requested_only_when_supported(self):
        pipeline, client = make_pipeline('answer_reasoning')
        pipeline.run('Solve this problem')
        self.assertIs(client.chat.call_args.kwargs['think'], True)
        pipeline, client = make_pipeline('answer_reasoning', info={'capabilities': ['completion']})
        status = []
        pipeline.run('Solve this problem', status=status.append)
        self.assertNotIn('think', client.chat.call_args.kwargs)
        self.assertTrue(any('no reasoning control' in s for s in status))

    def test_named_thinking_levels_are_not_invented(self):
        pipeline, client = make_pipeline('answer_reasoning', info={
            'capabilities': ['thinking'], 'thinking': {'values': ['low', 'high'], 'default': 'low'},
        })
        pipeline.run('Solve this problem')
        self.assertNotIn('think', client.chat.call_args.kwargs)

    def test_older_runtime_falls_back_only_on_thinking_feature_error(self):
        pipeline, client = make_pipeline('answer_reasoning')
        client.chat.side_effect = [ResponseError('model does not support thinking', 400), iter([chunk('Answer')])]
        status = []
        self.assertEqual(pipeline.run('Question', status=status.append).text, 'Answer')
        self.assertNotIn('think', client.chat.call_args.kwargs)
        self.assertTrue(any('runtime' in s for s in status))

    def test_screenshot_is_requested_only_for_screen_route_and_not_retained(self):
        pipeline, client = make_pipeline('screen_reasoning')
        desktop = Mock(return_value=b'PNG in memory')
        status = []
        result = pipeline.run('What is on my screen?', desktop=desktop, status=status.append)
        desktop.assert_called_once_with('screenshot', None)
        message = client.chat.call_args.kwargs['messages'][-1]
        self.assertEqual(message['images'], [b'PNG in memory'])
        self.assertTrue(client.chat.call_args.kwargs['think'])
        self.assertIn('Capturing screen…', status)
        self.assertNotIn('images', str(result.history))
        pipeline, _ = make_pipeline()
        desktop.reset_mock()
        pipeline.run('General question', desktop=desktop)
        desktop.assert_not_called()

    def test_vision_unavailable_does_not_capture(self):
        pipeline, _ = make_pipeline('screen_quick', info={'capabilities': ['completion']})
        desktop = Mock()
        with self.assertRaisesRegex(PipelineError, 'cannot read images'):
            pipeline.run('What is on my screen?', desktop=desktop)
        desktop.assert_not_called()

    def test_supported_command_is_dispatched_with_validated_arguments(self):
        pipeline, client = make_pipeline('open_application', {'application': 'notepad'})
        desktop = Mock(return_value='Opened Notepad')
        result = pipeline.run('Open Notepad', desktop=desktop)
        desktop.assert_called_once_with('command', {'name': 'open_application', 'arguments': {'application': 'notepad'}})
        client.chat.assert_not_called()
        self.assertEqual(result.text, 'Opened Notepad')

    def test_invalid_router_decisions_never_reach_executor(self):
        pipeline, client = make_pipeline()
        desktop = Mock()
        invalid = [None, [], {}, {'route': 'shell'},
            {'route': 'answer', 'reasoning': 1, 'screenshot': False, 'command': None},
            {'route': 'command', 'reasoning': True, 'screenshot': False, 'command': None},
            {'route': 'command', 'reasoning': False, 'screenshot': False,
             'command': {'name': 'open_application', 'arguments': {'application': 'arbitrary.exe'}}},
            {'route': 'command', 'reasoning': False, 'screenshot': False,
             'command': {'name': 'shell', 'arguments': {'command': 'bad'}}},
        ]
        for value in invalid:
            with (self.subTest(value=value),
                  patch.object(pipeline.router, 'route', return_value=value),
                  self.assertRaises(PipelineError)):
                pipeline.run('Do it', desktop=desktop)
        desktop.assert_not_called()
        client.chat.assert_not_called()

    def test_malformed_router_json_and_extra_arguments_rejected(self):
        for value in ['not json', '{"function":"answer_quick","arguments":{},"extra":true}',
                      '{"function":"answer_quick","function":"screen_quick","arguments":{}}',
                      '{"function":"copy_text","arguments":{"text":"ok","code":"bad"}}',
                      '{"function":"set_volume","arguments":{"level_percent":true}}']:
            with self.subTest(value=value), self.assertRaises(RouterError):
                _parse_selection(value)

    def test_router_unavailable_is_actionable(self):
        pipeline, _ = make_pipeline()
        with patch('router.router.urllib.request.urlopen', side_effect=OSError('offline')):
            pipeline.router._request = MoteRouter._request.__get__(pipeline.router)
            with self.assertRaisesRegex(PipelineError, 'Cannot reach Ollama'):
                pipeline.run('Hello')

    def test_answer_connection_missing_model_timeout_and_empty_stream(self):
        for error, expected in [(ConnectionError('offline'), 'Cannot reach Ollama'),
                                (httpx.ReadTimeout('slow'), 'timed out'),
                                (ResponseError('missing', 404), 'ollama pull')]:
            pipeline, client = make_pipeline()
            client.show.side_effect = error
            with self.subTest(error=error), self.assertRaisesRegex(PipelineError, expected):
                pipeline.run('Hello')
        pipeline, _ = make_pipeline(parts=())
        with self.assertRaisesRegex(PipelineError, 'no answer'):
            pipeline.run('Hello')

    def test_recent_conversation_reused_and_bounded(self):
        pipeline, client = make_pipeline()
        first = pipeline.run('My name is Alex')
        pipeline.run('What is my name?', history=first.history)
        self.assertIn(first.history[0], client.chat.call_args.kwargs['messages'])
        self.assertIn('Alex', str(pipeline.router._request.call_args))
        history = recent_history([{'role': 'user', 'content': 'a' * 5000}] * 20)
        self.assertLessEqual(len(history), 6)
        self.assertLessEqual(sum(len(m['content']) for m in history), 12000)

    def test_cancelled_request_never_captures_or_executes(self):
        pipeline, _ = make_pipeline('open_application', {'application': 'notepad'})
        cancel = Event()
        def route(*args):
            cancel.set()
            return {'route': 'command', 'reasoning': False, 'screenshot': False,
                    'command': {'name': 'open_application', 'arguments': {'application': 'notepad'}}}
        pipeline.router.route = route
        desktop = Mock()
        with self.assertRaises(Cancelled):
            pipeline.run('Open Notepad', cancel=cancel, desktop=desktop)
        desktop.assert_not_called()

    def test_partial_stream_failure_is_not_retried(self):
        pipeline, client = make_pipeline()
        def stream():
            yield chunk('Partial')
            raise ResponseError('thinking failed', 400)
        client.chat.side_effect = lambda **kwargs: stream()
        parts = []
        with self.assertRaises(PipelineError):
            pipeline.run('Question', partial=parts.append)
        self.assertEqual(parts, ['Partial'])
        client.chat.assert_called_once()

    def test_router_environment_configuration(self):
        with patch.dict('os.environ', {'MOTE_MODEL': 'answer', 'MOTE_ROUTER_MODEL': 'router',
                                      'MOTE_OLLAMA_HOST': 'http://localhost:9999', 'MOTE_TIMEOUT': '12'}):
            router = MoteRouter()
            self.assertEqual((router.model, router.base_url, router.timeout), ('router', 'http://localhost:9999', 12))


class ActionTests(unittest.TestCase):
    def test_execution_boundary_revalidates_arguments(self):
        executor = WindowsActions()
        with self.assertRaises(RouterError):
            executor.execute({'name': 'open_application', 'arguments': {'application': 'notepad', 'args': 'bad'}})

    def test_website_validation_before_launch(self):
        executor = WindowsActions()
        with patch('integration.actions.sys.platform', 'win32'), patch('integration.actions.os.startfile', create=True) as launch:
            for url in ['file:///etc/passwd', 'https://user:pass@example.org', 'https://',
                        'https://example.org\\bad', 'https://example.org:bad']:
                with self.subTest(url=url), self.assertRaises((RouterError, PipelineError)):
                    executor.execute({'name': 'open_website', 'arguments': {'url': url}})
            launch.assert_not_called()
            executor.execute({'name': 'open_website', 'arguments': {'url': 'https://example.org'}})
            launch.assert_called_once_with('https://example.org')

    def test_application_settings_and_clipboard_use_only_allowlisted_values(self):
        executor = WindowsActions()
        clipboard = Mock()
        with patch('integration.actions.sys.platform', 'win32'), patch('integration.actions.os.startfile', create=True) as launch:
            executor.execute({'name': 'open_application', 'arguments': {'application': 'notepad'}})
            launch.assert_called_with('notepad.exe')
            executor.execute({'name': 'open_windows_settings', 'arguments': {'page': 'wifi'}})
            launch.assert_called_with('ms-settings:network-wifi')
            executor.execute({'name': 'copy_text', 'arguments': {'text': 'exact text'}}, clipboard=clipboard)
            clipboard.setText.assert_called_once_with('exact text')

    def test_volume_unsupported_and_windows_guard(self):
        executor = WindowsActions()
        with patch('integration.actions.sys.platform', 'win32'), self.assertRaisesRegex(PipelineError, 'not integrated'):
            executor.execute({'name': 'set_volume', 'arguments': {'level_percent': 50}})
        with patch('integration.actions.sys.platform', 'linux'), self.assertRaisesRegex(PipelineError, 'preview'):
            executor.execute({'name': 'open_application', 'arguments': {'application': 'notepad'}})


if __name__ == '__main__':
    unittest.main()
