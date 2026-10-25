"""SIMULATED menu and injected scheduler; no actual audio, OCR, camera or provider calls."""
from dataclasses import replace
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from menu_reader.speech_adapter import CallbackScheduler, MenuSpeechAdapter


def menu():
    return dict(kind='menuCandidate', title='SIMULATED MENU', sections=['BURGERS'],
        hasTitleConflict=False, unstructured=[], parsedPages=[],
        pages=[dict(pageId='SIMULATED-page', words=[dict(id='w1'), dict(id='w2')])],
        items=[dict(id='burger', name='Classic Burger', section='BURGERS',
            price=dict(raw='$8.50', amount='8.50', currency='$'), description=[], printedNotes=[],
            sourceIds=['w1','w2'], reviewed=False)])


class SpeechAdapterTests(unittest.TestCase):
    def setUp(self):
        self.sent, self.canceled, self.now = [], [], [1000]
        self.scheduler = CallbackScheduler(lambda request: self.sent.append(request) or True,
            lambda *context: self.canceled.append(context))
        self.adapter = MenuSpeechAdapter(menu(), 'SIMULATED-session', 7, self.scheduler, lambda: self.now[0])

    def test_typed_request_dispatches_only_explicitly_to_injected_scheduler(self):
        self.adapter.start_reading()
        request = self.adapter.prepare_next()
        self.assertEqual(self.sent, [])
        self.assertEqual(request.priority, 4)
        self.assertEqual(request.generation, 7)
        self.assertEqual(request.source_ids, ('w1','w2'))
        self.assertEqual(request.menu_id, self.adapter.menu_id)
        self.assertTrue(self.adapter.release(request))
        self.assertFalse(self.adapter.release(request))
        self.assertEqual(self.sent, [request])
        self.assertEqual(request.to_dict()['text'], request.text)

    def test_cursor_advances_on_completion_not_enqueue_or_time(self):
        self.adapter.start_reading()
        request = self.adapter.prepare_next()
        self.adapter.release(request)
        self.now[0] += 100
        self.assertEqual(self.adapter.index, 0)
        self.assertFalse(self.adapter.completed('wrong-event'))
        self.assertTrue(self.adapter.completed(request.event_id))
        self.assertEqual(self.adapter.index, 1)
        self.assertNotEqual(self.adapter.prepare_next().event_id, request.event_id)

    def test_pause_cancel_and_context_changes_reject_pending_generations(self):
        for operation in [self.adapter.pause, self.adapter.stop,
                          lambda: self.adapter.update_context('SIMULATED-next-session', 8)]:
            self.adapter.start_reading()
            old = self.adapter.prepare_next()
            self.adapter.release(old)
            operation()
            self.assertFalse(self.adapter.release(old))
            self.assertFalse(self.adapter.completed(old.event_id))
        self.assertTrue(self.canceled)

    def test_resume_restarts_only_interrupted_chunk_and_skip_is_user_controlled(self):
        self.adapter.start_reading()
        first = self.adapter.prepare_next()
        self.adapter.pause(); self.adapter.resume()
        resumed = self.adapter.prepare_next()
        self.assertEqual(resumed.text, first.text)
        self.assertNotEqual(resumed.event_id, first.event_id)
        self.assertEqual(self.sent, [])
        self.adapter.skip()
        self.assertEqual(self.adapter.index, 1)

    def test_followups_use_cached_menu_without_rescan_or_provider(self):
        with (patch('menu_reader.ocr.scan', side_effect=AssertionError('no OCR')),
              patch('urllib.request.urlopen', side_effect=AssertionError('no cloud'))):
            answer, request = self.adapter.prepare_answer('Which is cheapest?')
            self.assertIn('Classic Burger', answer['text'])
            self.assertEqual(request.source_ids, ('w1','w2'))
            answer, request = self.adapter.prepare_answer('Does Classic Burger contain peanuts?')
            self.assertEqual(answer['status'], 'ABSTAIN')
            self.assertIn('does not confirm', request.text)

    def test_no_staff_speech_without_explicit_confirmation_and_release(self):
        self.adapter.prepare_answer('I choose Classic Burger')
        answer, draft = self.adapter.prepare_answer('Help me communicate with the cashier')
        self.assertEqual(draft.audience, 'user')
        self.assertTrue(answer['requiresConfirmation'])
        answer, blocked = self.adapter.prepare_answer('confirm order')
        self.assertIsNone(blocked)
        answer, approved = self.adapter.prepare_answer('confirm order', staff_confirmed=True)
        self.assertEqual(approved.audience, 'staff')
        self.assertTrue(approved.user_confirmed)
        self.assertEqual(approved.source_ids, ('w1','w2'))
        self.assertEqual(self.sent, [])
        self.assertTrue(self.adapter.release(approved))

    def test_expired_or_modified_requests_are_not_released(self):
        self.adapter.start_reading()
        request = self.adapter.prepare_next()
        self.assertFalse(self.adapter.release(replace(request, priority=2)))
        self.now[0] = request.expires_at_ms
        self.assertFalse(self.adapter.release(request))


if __name__=='__main__': unittest.main()
