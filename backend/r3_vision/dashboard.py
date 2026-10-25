"""Optional loopback-only display bridge. Never authorizes physical outputs.

The Tk owner publishes snapshots and consumes commands on its own thread. HTTP
threads never touch the camera, model, audio player or hardware.
"""
from __future__ import annotations

import base64
import copy
import io
import json
import math
import mimetypes
from pathlib import Path
import queue
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, unquote

FRESH_SECONDS = .3


def empty_state(mode='LIVE', quick=False):
    return dict(connected=True, mode=mode, tracking='SEARCHING', detail='Waiting for a current camera frame',
                target=None, frameAgeMs=None, fresh=False, validForMs=0, frame=None, fps=0,
                distanceCm=None, axes=None, guidance=None, depth='Unavailable',
                audio=dict(enabled=False, lastCue=None, playback=None, suppressed=None),
                voice=dict(status='Off', lastHeard=''), wearable=dict(available=False), quickAvailable=quick)


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def snapshot(item, panel, ticket, *, mode='LIVE', quick=False, voice=None, now=None, orchestration=None):
    """Return a serializable snapshot plus private frame/evidence deadlines."""
    now = time.monotonic() if now is None else now
    state = empty_state(mode, quick)
    state['orchestration'] = copy.deepcopy(orchestration) if isinstance(orchestration, dict) else None
    state['voice'] = dict(status=str(getattr(voice, 'status', 'Off'))[:200],
                          lastHeard=str(getattr(voice, 'last_heard', ''))[:500],
                          inputDevice=str(getattr(voice, 'input_device_name', 'Not selected'))[:200])
    if not item or item[0] != ticket or len(item) < 7:
        return state, None, None
    _, image, received, tracking, detail, source_mode, _ = item[:7]
    if not number(received) or not 0 <= now - received < FRESH_SECONDS or image is None:
        return state, received if number(received) else None, None
    state.update(tracking=str(tracking)[:100], detail=str(detail)[:1000], fresh=True)
    if source_mode == 'SIMULATED':
        state['mode'] = 'SIMULATED'
    # Do not mutate the worker's original image; cap bandwidth on the display path.
    preview = image.copy().convert('RGB')
    preview.thumbnail((960, 720))
    buffer = io.BytesIO()
    preview.save(buffer, format='JPEG', quality=75)
    state['frame'] = 'data:image/jpeg;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')
    if not panel or panel.get('ticket') != ticket or panel.get('received') != received:
        return state, received, None
    state['target'] = str(panel.get('target') or '')[:200] or None
    state['fps'] = panel.get('fps') if number(panel.get('fps')) else 0
    audio = panel.get('audio') or {}
    state['audio'] = dict(enabled=bool(audio.get('enabled')), lastCue=audio.get('last_cue'),
                          playback=audio.get('playback'), suppressed=audio.get('suppressed'))
    distance = panel.get('distances')
    expiry = getattr(distance, 'valid_until_monotonic', None)
    axes = getattr(distance, 'axes', ())
    valid = (tracking == 'LOCKED' and panel.get('tracking') == 'LOCKED' and
             number(expiry) and now < expiry and not getattr(distance, 'estimated', False) and
             len(axes) == 3 and {a.axis for a in axes} == {'x', 'y', 'z'} and
             all(number(a.cm) and a.cm >= 0 for a in axes))
    if valid:
        state['axes'] = {a.axis: dict(text=a.text, cm=a.cm, direction=a.direction) for a in axes}
        total = getattr(distance, 'euclidean_cm', None)
        state['distanceCm'] = total if number(total) and total >= 0 else None
        state['depth'] = 'Validated'
        # This is the existing guidance decider's output, never a new browser decision.
        direction = panel.get('direction') or (None, None)
        state['guidance'] = direction[0] if direction[0] else None
        expiry = min(received + FRESH_SECONDS, expiry)
    else:
        expiry = None
    return state, received, expiry


class Dashboard:
    def __init__(self, port=8765, root=None, clock=time.monotonic):
        self.clock = clock
        self.root = (Path(root) if root else Path(__file__).resolve().parents[2] / 'apps/demo-dashboard').resolve()
        self.token = secrets.token_urlsafe(32)
        self.commands = queue.Queue(maxsize=8)
        self.lock = threading.Lock()
        self.value, self.received, self.expiry = empty_state(), None, None
        self.preview_frame = None
        self.preview_received = None
        self.preview_key = None
        self.preview_available = False
        self.preview_mode = 'LIVE'
        self.ticket = 0
        self.blocked_ticket = None
        self.server = ThreadingHTTPServer(('127.0.0.1', port), self.handler())
        self.server.daemon_threads = True
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True, name='visionos-dashboard')

    @property
    def url(self):
        return f'http://127.0.0.1:{self.port}'

    def start(self):
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def publish(self, item, panel, ticket, **kwargs):
        result = snapshot(item, panel, ticket, now=self.clock(), **kwargs)
        with self.lock:
            if self.blocked_ticket == ticket:
                return
            self.blocked_ticket = None
            self.ticket = ticket
            self.value, self.received, self.expiry = result

    def publish_preview(self, item, *, rotation=0, mode='LIVE'):
        """Display the existing capture buffer, never tracking overlays or evidence.

        Keep the last picture at any frame rate. Its receive age and availability
        remain explicit; retaining pixels does not extend any guidance deadline.
        Only the UI publisher calls this method, as with publish().
        """
        now = self.clock()
        if (not item or len(item) < 2 or item[0] is None or
                not number(item[1]) or item[1] > now):
            with self.lock:
                self.preview_available = False
            return
        raw, received = item[:2]
        info = item[2] if len(item) > 2 and isinstance(item[2], dict) else {}
        key = (received, info.get('connectionEpoch'), info.get('sourceFrameID'), raw.size, rotation, mode)
        with self.lock:
            if key == self.preview_key:
                self.preview_available = True
                return
        preview = raw.rotate(-rotation, expand=True) if rotation else raw.copy()
        preview = preview.convert('RGB')
        preview.thumbnail((960, 720))
        buffer = io.BytesIO()
        preview.save(buffer, format='JPEG', quality=75)
        frame = 'data:image/jpeg;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')
        with self.lock:
            self.preview_frame, self.preview_received = frame, received
            self.preview_key, self.preview_mode = key, mode
            self.preview_available = True

    def state(self):
        with self.lock:
            state, received, expiry = copy.deepcopy(self.value), self.received, self.expiry
            preview_received = self.preview_received
            state.update(previewFrame=self.preview_frame, previewAvailable=self.preview_available,
                         previewMode=self.preview_mode)
        now = self.clock()
        state['previewAgeMs'] = (round(max(0, now - preview_received) * 1000, 1)
                                 if preview_received is not None else None)
        age = now - received if received is not None else None
        fresh = state['fresh'] and age is not None and 0 <= age < FRESH_SECONDS
        state['frameAgeMs'] = round(max(0, age) * 1000, 1) if age is not None else None
        state['fresh'] = fresh
        state['validForMs'] = max(0, min(FRESH_SECONDS - age, expiry - now if expiry else FRESH_SECONDS) * 1000) if fresh else 0
        if not fresh:
            state.update(frame=None, target=None, tracking='STALE', detail='Waiting for a current camera frame')
            state['audio']['lastCue'] = None
        if not fresh or expiry is None or now >= expiry:
            state.update(axes=None, guidance=None, distanceCm=None, depth='Unavailable')
        return state

    def invalidate(self):
        with self.lock:
            self.blocked_ticket = self.ticket
            self.value = empty_state(self.value['mode'], self.value['quickAvailable'])
            self.received = self.expiry = None

    def allow_current_ticket(self):
        # A Find request classified as a scene question does not replace a target.
        with self.lock:
            self.blocked_ticket = None

    def enqueue(self, command):
        # Admission and invalidation are atomic: a rejected request cannot blank
        # the display indefinitely, and a full queue must never reject Stop.
        with self.commands.mutex:
            if command['action'] == 'stop':
                self.commands.queue.clear()
            elif len(self.commands.queue) >= self.commands.maxsize:
                return False
            if command['action'] in ('find', 'stop'):
                self.invalidate()
            self.commands.queue.append(command)
            self.commands.not_empty.notify()
        return True

    def handler(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(2)

            def log_message(self, *_):
                pass

            def allowed(self):
                hosts = {f'127.0.0.1:{owner.port}', f'localhost:{owner.port}'}
                host = self.headers.get('Host', '')
                origin = self.headers.get('Origin')
                return (host in hosts and (origin is None or origin == f'http://{host}') and
                        self.headers.get('Sec-Fetch-Site', 'none') in ('none', 'same-origin'))

            def respond(self, status, content, content_type='application/json'):
                if isinstance(content, dict):
                    content = json.dumps(content, allow_nan=False).encode()
                self.send_response(status)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(content)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'")
                self.end_headers()
                self.wfile.write(content)

            def do_GET(self):
                if not self.allowed():
                    return self.respond(403, {'error': 'Local same-origin access required'})
                path = unquote(urlsplit(self.path).path)
                if path == '/api/session':
                    return self.respond(200, {'token': owner.token})
                if path == '/api/state':
                    return self.respond(200, owner.state())
                file = (owner.root / ('index.html' if path == '/' else path.lstrip('/'))).resolve()
                if not file.is_relative_to(owner.root) or not file.is_file():
                    return self.respond(404, {'error': 'Not found'})
                return self.respond(200, file.read_bytes(), mimetypes.guess_type(file.name)[0] or 'application/octet-stream')

            def do_POST(self):
                if not self.allowed() or not secrets.compare_digest(self.headers.get('X-Reflex-Token', ''), owner.token):
                    return self.respond(403, {'error': 'Local session token required'})
                if urlsplit(self.path).path != '/api/command':
                    return self.respond(404, {'error': 'Not found'})
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 2048 or self.headers.get('Transfer-Encoding'):
                        raise ValueError('Invalid body size')
                    command = json.loads(self.rfile.read(length))
                    if not isinstance(command, dict) or command.get('action') not in ('find', 'stop', 'describe', 'quick', 'request', 'recall'):
                        raise ValueError('Invalid action')
                    query = command.get('query', '')
                    if not isinstance(query, str) or len(query) > 500 or any(ord(c) < 32 for c in query):
                        raise ValueError('Invalid query')
                    if command['action'] in ('find', 'request') and not query.strip():
                        raise ValueError('A target request is required')
                    accepted = owner.enqueue(dict(action=command['action'], query=query.strip()))
                    return self.respond(202 if accepted else 429, {'accepted': accepted})
                except (ValueError, TypeError, UnicodeError):
                    return self.respond(400, {'error': 'Invalid command'})

        return Handler
