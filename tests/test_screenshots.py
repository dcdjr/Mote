"""Screen requests must reach capture even when the classifier misses them."""
import json
import unittest
from unittest.mock import Mock

from router.router import MoteRouter
from services.attachments import PreparedAttachment, PreparedRequest
from test_pipeline import make_pipeline


class ScreenshotRoutingTests(unittest.TestCase):
    def test_screen_requests_do_not_depend_on_classifier(self):
        prompts = [
            'Take a screenshot', 'Please take a screenshot and explain what you see',
            'Can you take a screenshot?', 'Could you grab a screenshot for me?',
            'Screenshot my desktop', 'Look at my screen', 'Can you see my screen?',
            'Do you see the error on my screen?', 'Are you able to look at my screen and explain the error?',
            'What is on my screen?', 'What am I looking at?', 'Where should I click?',
            'Take a screenshot and compare it with this code: print(1)',
        ]
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                router = MoteRouter()
                router._request = Mock(side_effect=AssertionError('Screen request should bypass classifier'))
                decision = router.route(prompt)
                self.assertEqual(decision['route'], 'answer')
                self.assertTrue(decision['screenshot'])
                router._request.assert_not_called()

    def test_explicit_new_capture_with_an_attached_image(self):
        router = MoteRouter()
        router._request = Mock(return_value={'message': {'content': json.dumps({'function': 'answer_quick', 'arguments': {}})}})
        self.assertTrue(router.route('Take a new screenshot and compare it with this image', {'image_attached': True})['screenshot'])

    def test_tutorial_capability_and_negative_requests_do_not_force_capture(self):
        for prompt in ['How do I take a screenshot?', 'Can you take screenshots?',
                       "Don't take a screenshot", 'Explain recursion without taking a screenshot',
                       'Explain this error: division by zero', 'What can you do?']:
            with self.subTest(prompt=prompt):
                router = MoteRouter()
                router._request = Mock(return_value={'message': {'content': json.dumps({'function': 'answer_quick', 'arguments': {}})}})
                self.assertFalse(router.route(prompt)['screenshot'])

    def test_pipeline_corrects_a_wrong_command_decision(self):
        pipeline, client = make_pipeline()
        pipeline.router.route = Mock(return_value={'route': 'command', 'reasoning': False,
            'screenshot': False, 'command': {'name': 'copy_text', 'arguments': {'text': 'wrong'}}})
        desktop = Mock(return_value=b'screen bytes')
        pipeline.run('Take a screenshot', desktop=desktop)
        desktop.assert_called_once_with('screenshot', None)
        self.assertEqual(client.chat.call_args.kwargs['messages'][-1]['images'], [b'screen bytes'])

    def test_screen_attachment_status_and_answer_instructions(self):
        pipeline, client = make_pipeline('screen_quick')
        statuses = []
        pipeline.run('Take a screenshot', desktop=Mock(return_value=b'screen'), status=statuses.append)
        self.assertIn('Screenshot attached', statuses)
        system = client.chat.call_args.kwargs['messages'][0]['content']
        self.assertIn('A screenshot is attached to THIS request: True', system)
        self.assertIn('inspect', system)

    def test_attachment_context_does_not_hide_an_explicit_capture(self):
        attachment = PreparedAttachment('id', 'example.png', '.png', 'image/png', image=b'attached')
        pipeline, client = make_pipeline('answer_quick')
        desktop = Mock(return_value=b'current screen')
        pipeline.run(PreparedRequest('Take a screenshot and compare it with this image', (attachment,)), desktop=desktop)
        self.assertEqual(client.chat.call_args.kwargs['messages'][-1]['images'], [b'attached', b'current screen'])

    def test_literal_copy_and_supplied_code_cannot_request_capture(self):
        for prompt in ['Copy this text: take a screenshot', "Explain this code: print('take a screenshot')",
                       'Explain this code: `take a screenshot`']:
            with self.subTest(prompt=prompt):
                router = MoteRouter()
                router._request = Mock(return_value={'message': {'content': json.dumps({'function': 'answer_quick', 'arguments': {}})}})
                self.assertFalse(router.route(prompt)['screenshot'])

    def test_empty_capture_fails_instead_of_sending_text_only(self):
        pipeline, client = make_pipeline('screen_quick')
        from ai.pipeline import PipelineError
        with self.assertRaisesRegex(PipelineError, 'returned no image'):
            pipeline.run('Take a screenshot', desktop=Mock(return_value=b''))
        client.chat.assert_not_called()


    def test_capture_opt_out_overrides_an_incorrect_screen_route(self):
        pipeline, client = make_pipeline('screen_quick')
        desktop = Mock()
        pipeline.run("Don't take a screenshot; explain recursion", desktop=desktop)
        desktop.assert_not_called()
        self.assertNotIn('images', client.chat.call_args.kwargs['messages'][-1])



class NativeExclusionTests(unittest.TestCase):
    def test_windows_exclusion_restores_previous_affinity(self):
        from unittest.mock import patch
        from integration.capture import exclude_from_capture, restore_capture_exclusion
        api = Mock()
        def previous(hwnd, pointer):
            pointer._obj.value = 1
            return True
        api.GetWindowDisplayAffinity.side_effect = previous
        api.SetWindowDisplayAffinity.return_value = True
        window = Mock()
        window.winId.return_value = 123
        with patch('integration.capture._windows_capture_api', return_value=api):
            token = exclude_from_capture(window)
        api.SetWindowDisplayAffinity.assert_called_once_with(123, 0x11)
        restore_capture_exclusion(token)
        api.SetWindowDisplayAffinity.assert_called_with(123, 1)

    def test_native_exclusion_failure_allows_transparency_fallback(self):
        from unittest.mock import patch
        from integration.capture import exclude_from_capture
        api = Mock()
        api.SetWindowDisplayAffinity.return_value = False
        window = Mock()
        window.winId.return_value = 123
        with patch('integration.capture._windows_capture_api', return_value=api):
            self.assertIsNone(exclude_from_capture(window))


if __name__ == '__main__':
    unittest.main()
