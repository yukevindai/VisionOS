"""Synthetic display bridge tests, not a hardware validation."""
import json
import base64
import io
from pathlib import Path
from types import SimpleNamespace as NS
import tempfile
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from PIL import Image
from r3_vision.dashboard import Dashboard, snapshot


def evidence(ticket=1):
    item = (ticket, Image.new('RGB', (20, 10)), 10., 'LOCKED', 'test', 'SIMULATED', 0)
    axes = tuple(NS(axis=a, text=f'{a} 10 cm', cm=10., direction='LEFT') for a in 'xyz')
    distances = NS(axes=axes, estimated=False, euclidean_cm=17.3, valid_until_monotonic=10.2)
    panel = dict(ticket=ticket, received=10., tracking='LOCKED', target='SIMULATED can', fps=30,
                 distances=distances, direction=('LEFT', '3D'), audio=dict(enabled=True, last_cue='LEFT'))
    return item, panel


class SnapshotTests(unittest.TestCase):
    def test_valid(self):
        value, received, expiry = snapshot(*evidence(), 1, now=10.1)
        self.assertEqual(value['mode'], 'SIMULATED')
        self.assertEqual(value['guidance'], 'LEFT')
        self.assertEqual(value['distanceCm'], 17.3)
        self.assertEqual(set(value['axes']), set('xyz'))
        self.assertTrue(value['frame'].startswith('data:image/jpeg;base64,'))
        self.assertFalse(value['wearable']['available'])
        self.assertEqual(expiry, 10.2)

    def test_wrong_ticket_future_and_stale(self):
        for ticket, now in [(2,10.1),(1,9.),(1,10.31)]:
            value, _, _ = snapshot(*evidence(), ticket, now=now)
            self.assertFalse(value['fresh'])
            self.assertIsNone(value['frame'])
            self.assertIsNone(value['guidance'])

    def test_panel_must_match_frame_and_ticket(self):
        for key, val in [('ticket', 2), ('received', 10.01)]:
            item, panel = evidence()
            panel[key] = val
            value, _, _ = snapshot(item, panel, 1, now=10.1)
            self.assertIsNotNone(value['frame'])
            self.assertIsNone(value['axes'])
            self.assertIsNone(value['target'])

    def test_expired_or_estimated_depth(self):
        for estimated, expiry in [(True, 10.2), (False, 10.05), (False, None)]:
            item, panel = evidence()
            panel['distances'].estimated = estimated
            panel['distances'].valid_until_monotonic = expiry
            value, _, _ = snapshot(item, panel, 1, now=10.1)
            self.assertIsNone(value['guidance'])
            self.assertEqual(value['depth'], 'Unavailable')


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        Path(self.temp.name, 'index.html').write_text('<h1>test</h1>')
        self.now = 10.1
        self.server = Dashboard(0, self.temp.name, clock=lambda: self.now)
        self.server.start()
    def tearDown(self):
        self.server.close()
        self.temp.cleanup()
    def request(self, path, data=None, headers=None):
        request = Request(self.server.url + path, data=json.dumps(data).encode() if data is not None else None,
                          headers=headers or {})
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, response.read()
        except HTTPError as error:
            return error.code, error.read()
    def test_static_and_session(self):
        self.assertEqual(self.request('/')[0], 200)
        status, data = self.request('/api/session')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)['token'], self.server.token)
    def test_host_origin_fetch_site(self):
        for headers in [{'Host':'evil.example'},{'Origin':'https://evil.example'},{'Sec-Fetch-Site':'cross-site'}]:
            self.assertEqual(self.request('/api/session', headers=headers)[0], 403)
    def test_token_required(self):
        self.assertEqual(self.request('/api/command', {'action':'stop'})[0], 403)
        self.assertEqual(self.request('/api/command', {'action':'stop'}, {'X-Reflex-Token':self.server.token})[0], 202)
        self.assertEqual(self.server.commands.get_nowait()['action'], 'stop')
    def test_invalid_body(self):
        for cmd in [{'action':'execute'},{'action':'find','query':''},{'action':'find','query':'x'*501},
                    {'action':'find','query':'bad\nquery'},[]]:
            self.assertEqual(self.request('/api/command', cmd, {'X-Reflex-Token':self.server.token})[0], 400)
    def test_path_traversal(self):
        self.assertEqual(self.request('/%2e%2e/AGENTS.md')[0], 404)
    def test_stale_without_publish(self):
        self.server.publish(*evidence(), 1)
        self.now = 10.21
        self.assertIsNone(json.loads(self.request('/api/state')[1])['axes'])
        self.now = 10.31
        state = json.loads(self.request('/api/state')[1])
        self.assertFalse(state['fresh'])
        self.assertIsNone(state['frame'])
        self.assertIsNone(state['target'])
    def test_preview_survives_arbitrarily_slow_frames_without_guidance(self):
        raw = Image.new('RGB', (20, 10), 'blue')
        self.server.publish_preview((raw, 10., {}), mode='SIMULATED')
        self.server.publish(*evidence(), 1)
        first = self.server.state()['previewFrame']
        for now in (10.21, 10.31, 12., 70., 3610.):
            self.now = now
            state = json.loads(self.request('/api/state')[1])
            self.assertEqual(state['previewFrame'], first)
            self.assertEqual(state['previewAgeMs'], round((now - 10.) * 1000, 1))
            self.assertEqual(state['previewMode'], 'SIMULATED')
            self.assertIsNone(state['axes'])
            self.assertIsNone(state['guidance'])
        self.server.publish_preview((Image.new('RGB', (20, 10), 'red'), self.now, {}))
        self.assertNotEqual(self.server.state()['previewFrame'], first)
        self.assertEqual(self.server.state()['previewAgeMs'], 0)
    def test_preview_is_clean_rotated_and_cached(self):
        raw = Image.new('RGB', (20, 10), 'blue')
        item = (raw, 10., {'sourceFrameID': 1, 'connectionEpoch': 1})
        self.server.publish_preview(item, rotation=90)
        state = self.server.state()
        with Image.open(io.BytesIO(base64.b64decode(state['previewFrame'].split(',')[1]))) as image:
            self.assertEqual(image.size, (10, 20))
            self.assertGreater(image.getpixel((0, 0))[2], 240)
        self.assertEqual(raw.size, (20, 10))
        with patch.object(Image.Image, 'save', side_effect=AssertionError('duplicate encoding')):
            self.server.publish_preview(item, rotation=90)
        self.assertFalse(state['fresh'])
        self.assertIsNone(state['frame'])
    def test_preview_survives_stop_and_task_replacement(self):
        self.server.publish_preview((Image.new('RGB', (20, 10)), 10., {}))
        self.server.publish(*evidence(), 1)
        first = self.server.state()['previewFrame']
        for action in ('stop', 'find'):
            self.server.enqueue({'action': action, 'query': 'new target'})
            self.server.publish(*evidence(), 1)
            state = self.server.state()
            self.assertEqual(state['previewFrame'], first)
            self.assertIsNone(state['frame'])
            self.assertIsNone(state['guidance'])
    def test_unavailable_preview_retains_last_picture_with_honest_status(self):
        self.server.publish_preview((Image.new('RGB', (20, 10)), 10., {}))
        first = self.server.state()['previewFrame']
        for item in (None, (None, 10.), (Image.new('RGB', (20, 10)), 11.),
                     (Image.new('RGB', (20, 10)), float('nan'))):
            self.server.publish_preview(item)
            state = self.server.state()
            self.assertFalse(state['previewAvailable'])
            self.assertEqual(state['previewFrame'], first)
            self.assertEqual(state['previewAgeMs'], 100)
    def test_replacement_blocks_old_ticket(self):
        self.server.publish(*evidence(), 1)
        self.server.enqueue({'action':'find','query':'new target'})
        self.server.publish(*evidence(), 1)
        self.assertIsNone(self.server.state()['frame'])
        self.server.publish(*evidence(ticket=2), 2)
        self.assertIsNotNone(self.server.state()['frame'])
    def test_rejected_find_keeps_current_snapshot(self):
        self.server.publish(*evidence(), 1)
        for _ in range(8):
            self.server.enqueue({'action':'describe','query':''})
        self.assertFalse(self.server.enqueue({'action':'find','query':'new target'}))
        self.assertIsNotNone(self.server.state()['frame'])
    def test_stop_preempts_bounded_queue(self):
        for _ in range(8):
            self.assertTrue(self.server.enqueue({'action':'describe','query':''}))
        self.assertFalse(self.server.enqueue({'action':'describe','query':''}))
        self.assertTrue(self.server.enqueue({'action':'stop','query':''}))
        self.assertEqual(self.server.commands.qsize(), 1)
        self.assertEqual(self.server.commands.get_nowait()['action'], 'stop')

if __name__ == '__main__': unittest.main()
