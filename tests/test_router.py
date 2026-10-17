"""Router contracts and explicit-request fast paths. No model or desktop actions."""
import json
import unittest
from unittest.mock import Mock

from router.router import MoteRouter, RouterError, _parse_selection


def selection(function, **arguments):
    return {'function': function, 'arguments': arguments}


class RouterTests(unittest.TestCase):
    def router(self, output=None):
        router = MoteRouter()
        router._request = Mock(return_value={'message': {'content': json.dumps(
            output or selection('answer_quick'))}})
        return router

    def test_existing_answer_handlers(self):
        for name, reasoning, screenshot in [('answer_quick', False, False),
                ('answer_reasoning', True, False), ('screen_quick', False, True),
                ('screen_reasoning', True, True)]:
            with self.subTest(name=name):
                decision = self.router(selection(name)).route('A general request')
                self.assertEqual((decision['reasoning'], decision['screenshot']), (reasoning, screenshot))

    def test_installed_app_name_is_not_limited_to_old_enum(self):
        result = _parse_selection(json.dumps(selection('open_application', application='Visual Studio Code')))
        self.assertEqual(result['command']['arguments']['application'], 'Visual Studio Code')

    def test_multi_command_selection(self):
        commands = [selection('open_website', url='https://youtube.com'),
                    selection('set_volume', level_percent=50)]
        result = _parse_selection(json.dumps({'commands': commands}))
        self.assertEqual(result['route'], 'command')
        self.assertEqual([c['name'] for c in result['command']], ['open_website', 'set_volume'])

    def test_explicit_app_uses_fast_path_without_inference(self):
        router = self.router()
        result = router.route('Open Visual Studio Code')
        router._request.assert_not_called()
        self.assertEqual(result['command']['arguments']['application'], 'Visual Studio Code')

    def test_explicit_domain_uses_fast_path_without_inference(self):
        router = self.router()
        result = router.route('Open example.org/docs?q=hello&lang=en')
        router._request.assert_not_called()
        self.assertEqual(result['command']['arguments']['url'], 'https://example.org/docs?q=hello&lang=en')

    def test_youtube_and_volume_fast_path(self):
        router = self.router()
        result = router.route('open youtube and set the volume to 50')
        router._request.assert_not_called()
        self.assertEqual([c['name'] for c in result['command']], ['open_website', 'set_volume'])
        self.assertEqual(result['command'][1]['arguments']['level_percent'], 50)

    def test_explicit_screen_request_overrides_missed_classifier_flag(self):
        router = self.router(selection('answer_quick'))
        result = router.route('What is on my screen?')
        self.assertTrue(result['screenshot'])

    def test_supplied_text_does_not_force_capture(self):
        router = self.router(selection('answer_reasoning'))
        self.assertFalse(router.route('Explain this error: division by zero')['screenshot'])

    def test_attached_image_does_not_request_new_capture(self):
        router = self.router(selection('answer_quick'))
        self.assertFalse(router.route('What is on my screen?', {'image_attached': True})['screenshot'])

    def test_capability_question_remains_an_answer(self):
        self.assertEqual(self.router().route('What can you do?')['route'], 'answer')

    def test_partial_multi_command_is_never_fast_executed(self):
        router = self.router()
        result = router.route('open youtube and set the volume to')
        router._request.assert_called_once()
        self.assertEqual(result['route'], 'answer')

    def test_questions_and_negations_are_not_fast_commands(self):
        for prompt in ['How do I open Notepad?', "Don't open Notepad", 'Can you explain how to open YouTube?',
                       'Open Notepad if the download finishes', 'Open YouTube and explain recursion']:
            with self.subTest(prompt=prompt):
                router = self.router()
                self.assertEqual(router.route(prompt)['route'], 'answer')
                router._request.assert_called_once()

    def test_invalid_plan_rejected(self):
        for commands in [[], [selection('shell', code='bad')], [selection('answer_quick')],
                         [selection('set_volume', level_percent=101)],
                         [selection('open_application', application='notepad')] * 9]:
            with self.subTest(commands=commands), self.assertRaises(RouterError):
                _parse_selection(json.dumps({'commands': commands}))

    def test_schema_validation_rejects_extra_and_duplicate_fields(self):
        for text in ['{"function":"answer_quick","arguments":{},"extra":1}',
                     '{"function":"answer_quick","function":"screen_quick","arguments":{}}',
                     '{"function":"set_volume","arguments":{"level_percent":true}}']:
            with self.subTest(text=text), self.assertRaises(RouterError):
                _parse_selection(text)

    def test_budget_enforced_before_fast_path(self):
        router = self.router()
        with self.assertRaises(ValueError):
            router.route('Open ' + 'a' * 5000)
        router._request.assert_not_called()


class RouterEdgeTests(unittest.TestCase):
    def test_domain_from_model_is_normalized_and_full_url_accepts_letter_s(self):
        for url, expected in [('stackoverflow.com/questions', 'https://stackoverflow.com/questions'),
                              ('https://docs.python.org/3/', 'https://docs.python.org/3/')]:
            with self.subTest(url=url):
                result = _parse_selection(json.dumps(selection('open_website', url=url)))
                self.assertEqual(result['command']['arguments']['url'], expected)

    def test_url_punctuation_query_and_fragment_are_preserved(self):
        router = MoteRouter()
        router._request = Mock(side_effect=AssertionError('Expected fast route'))
        url = 'https://example.org/search?q=why?'
        self.assertEqual(router.route('Open ' + url)['command']['arguments']['url'], url)

    def test_copy_text_is_not_split_into_commands(self):
        router = MoteRouter()
        text = 'open YouTube and set volume to 50'
        result = router.route('Copy this text: ' + text)
        self.assertEqual(result['command'], {'name': 'copy_text', 'arguments': {'text': text}})

    def test_screenshot_howto_does_not_force_capture(self):
        router = MoteRouter()
        router._request = Mock(return_value={'message': {'content': json.dumps(selection('answer_quick'))}})
        self.assertFalse(router.route('How do I take a screenshot?')['screenshot'])

    def test_model_only_bypasses_deterministic_commands(self):
        router = MoteRouter(fast_paths=False)
        router._request = Mock(return_value={'message': {'content': json.dumps(selection('open_application', application='Thunderbird'))}})
        self.assertEqual(router.route('Open Thunderbird')['route'], 'command')
        router._request.assert_called_once()

    def test_truncated_native_tool_and_thinking_responses_rejected(self):
        for response in [{'message': {'content': '{}'}, 'done_reason': 'length'},
                         {'message': {'content': '{}', 'thinking': 'private'}},
                         {'message': {'content': '{}', 'tool_calls': [{'function': {}}]}}]:
            with self.subTest(response=response):
                router = MoteRouter()
                router._request = Mock(return_value=response)
                with self.assertRaises(RouterError):
                    router.route('A general question')


class ScreenAndAppNameTests(unittest.TestCase):
    def test_troubleshooting_howto_still_captures_current_screen(self):
        router = MoteRouter()
        router._request = Mock(return_value={'message': {'content': json.dumps(selection('answer_quick'))}})
        decision = router.route('How do I fix the error on my screen?')
        self.assertTrue(decision['screenshot'])
        self.assertTrue(decision['reasoning'])

    def test_explicit_app_qualifier_disambiguates_domain_like_app_name(self):
        router = MoteRouter()
        router._request = Mock(side_effect=AssertionError('Expected fast path'))
        decision = router.route('Open the application Paint.NET')
        self.assertEqual(decision['command'], {'name': 'open_application', 'arguments': {'application': 'Paint.NET'}})


if __name__ == '__main__':
    unittest.main()
