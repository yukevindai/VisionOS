"""SIMULATED providers and local tools; no device or live-provider acceptance."""
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from r3_vision.orchestration import AgentResult, Coordinator, Route


def until(predicate, timeout=2):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        threading.Event().wait(.005)
    raise AssertionError('Asynchronous operation did not reach expected state')


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.stops, self.spoken, self.calls = [], [], []
        self.router = SimpleNamespace(route=lambda text, dialogue, state: Route('find', 'red cup'))
        self.c = Coordinator(self.router, self.find, self.describe, self.recall,
                             lambda: self.stops.append(True), lambda text, ctx: self.spoken.append((text, ctx)))
        self.addCleanup(self.c.close)

    def find(self, query, ctx):
        self.calls.append(('find', query, ctx))
        return AgentResult('object-finding', 'pending')

    def describe(self, query, ctx):
        self.calls.append(('describe', query, ctx))
        return AgentResult('scene-description', 'complete', 'A cup is visible.', observed_at=10)

    def recall(self, query, ctx):
        self.calls.append(('recall', query, ctx))
        return AgentResult('social-context', 'insufficient_evidence', 'Not enough recent evidence.')

    def test_find_clarify_followup_track_and_loss(self):
        ctx = self.c.submit('Find a cup')
        until(lambda: self.c.snapshot()['status'] == 'pending')
        self.assertEqual(self.calls[0][1], 'a cup')
        self.c.report(ctx, AgentResult('object-finding', 'clarify', 'Red or blue cup?'))
        routing = []
        def route(text, dialogue, state):
            routing.append((text, dialogue, state))
            return Route('find', 'red cup')
        self.router.route = route
        next_ctx = self.c.submit('The red one')
        until(lambda: len(self.calls) == 2)
        self.assertFalse(ctx.is_current())
        self.assertEqual(routing[0][2]['clarification'], 'Red or blue cup?')
        self.assertTrue(self.c.report(next_ctx, AgentResult('object-finding', 'handoff', target={'label': 'red cup'})))
        self.assertTrue(self.c.report_tracking(next_ctx, 'tracking', {'label': 'red cup'}, 12))
        self.assertEqual(self.c.snapshot()['status'], 'tracking')
        self.c.report_tracking(next_ctx, 'lost', observed_at=13)
        self.assertFalse(next_ctx.is_current())
        self.assertEqual(self.c.snapshot()['status'], 'target_lost')
        self.assertIsNone(self.c.snapshot()['target'])

    def test_obvious_describe_recall_and_stop_never_route(self):
        self.router.route = lambda *a: self.fail('Obvious command called coordinator API')
        self.c.submit('Describe surroundings')
        until(lambda: self.c.snapshot()['status'] == 'complete')
        self.c.submit('What did I miss?')
        until(lambda: self.c.snapshot()['status'] == 'insufficient_evidence')
        self.c.submit('STOP!')
        self.assertEqual(self.c.snapshot()['status'], 'stopped')
        self.assertEqual([c[0] for c in self.calls], ['describe', 'recall'])

    def test_stop_while_provider_blocked_rejects_late_result(self):
        entered, release = threading.Event(), threading.Event()
        def route(*args):
            entered.set()
            release.wait(2)
            return Route('find', 'cup')
        self.router.route = route
        self.addCleanup(release.set)
        ctx = self.c.submit('Please help with the thing nearby')
        self.assertTrue(entered.wait(1))
        self.c.submit('stop')
        self.assertFalse(ctx.is_current())
        release.set()
        self.assertFalse(self.c.report(ctx, AgentResult('object-finding', 'handoff', 'late')))
        self.assertFalse(self.spoken)
        self.assertEqual(self.c.snapshot()['status'], 'stopped')

    def test_pending_queue_coalesces_replacements(self):
        entered, release = threading.Event(), threading.Event()
        def route(*args):
            entered.set()
            release.wait(2)
            return Route('find', 'obsolete')
        self.router.route = route
        self.addCleanup(release.set)
        self.c.submit('Interpret my request')
        self.assertTrue(entered.wait(1))
        for i in range(30):
            self.c.submit(f'Find object {i}')
        release.set()
        until(lambda: len(self.calls) == 1)
        self.assertEqual(self.calls[0][1], 'object 29')
        self.assertLessEqual(len(self.c.snapshot()['events']), 64)

    def test_deadline_rejects_result_and_stops_local(self):
        now = [100.]
        self.c.clock = lambda: now[0]
        ctx = self.c.submit('find cup')
        until(lambda: self.c.snapshot()['status'] == 'pending')
        now[0] += 6
        self.assertFalse(self.c.report(ctx, AgentResult('object-finding', 'handoff', 'too late')))
        self.assertEqual(self.c.snapshot()['error'], 'PROVIDER_TIMEOUT')
        self.assertFalse(ctx.is_current())
        self.assertFalse(self.spoken)

    def test_provider_failure_is_sanitized_and_stops_outputs(self):
        def fail(*args):
            raise RuntimeError('secret-provider-key should never appear')
        self.router.route = fail
        self.c.submit('Tell me something about this')
        until(lambda: self.c.snapshot()['status'] == 'error')
        self.assertEqual(self.c.snapshot()['error'], 'RuntimeError')
        self.assertNotIn('secret-provider-key', str(self.c.snapshot()))
        self.assertGreaterEqual(len(self.stops), 2)


if __name__ == '__main__':
    unittest.main()
