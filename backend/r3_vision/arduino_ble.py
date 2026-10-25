"""Optional laptop bridge for the repository's Arduino 101 text protocol.

This is a single four-servo demo controller, not the two-band binary protocol.
Bleak is loaded only when enabled. BLE acknowledgments do not prove movement.
"""
from __future__ import annotations

import asyncio
import math
import threading
import time

SERVICE_UUID = '19b10000-e8f2-537e-4f6c-d104768a1214'
COMMAND_UUID = '19b10001-e8f2-537e-4f6c-d104768a1214'
DIRECTIONS = frozenset(('up', 'down', 'left', 'right'))
MAX_AGE = 0.300
PULSE_SECONDS = 0.200
COOLDOWN_SECONDS = 0.300
WRITE_TIMEOUT = 0.250
_now = time.monotonic


class ArduinoBLE:
    """Nonblocking latest-intent bridge; start once, close to stop permanently."""

    def __init__(self, address: str | None = None):
        self.address = address
        self.status = 'disabled'
        self.connected = False
        self._lock = threading.Lock()
        self._desired: tuple[str, float] | None = None
        self._stop_revision = 0
        self._seen_stop_revision = 0
        self._closed = threading.Event()
        self._thread: threading.Thread | None = None
        self._active: str | None = None
        self._deadline = 0.0
        self._next_press = 0.0

    def start(self):
        if self._thread is not None or self._closed.is_set():
            return
        self.status = 'connecting'
        self._thread = threading.Thread(target=self._worker, name='arduino-ble', daemon=True)
        self._thread.start()

    def submit(self, direction: str | None, observed_at: float):
        """Accept only scheduler-approved image-plane directions and capture time."""
        now = _now()
        if (direction not in DIRECTIONS or not math.isfinite(observed_at)
                or not 0 <= now - observed_at <= MAX_AGE):
            self.stop()
            return
        with self._lock:
            if self.connected and not self._closed.is_set():
                self._desired = (direction, observed_at)

    def stop(self):
        # A sticky revision ensures a new frame cannot overwrite a pending stop.
        with self._lock:
            self._desired = None
            self._stop_revision += 1

    def close(self):
        self.stop()
        self._closed.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.5)

    async def _write(self, client, command: str):
        await asyncio.wait_for(
            client.write_gatt_char(COMMAND_UUID, command.encode('ascii'), response=True),
            timeout=WRITE_TIMEOUT,
        )

    async def _tick(self, client):
        now = _now()
        with self._lock:
            desired = self._desired
            revision = self._stop_revision
        direction = None
        if desired and 0 <= now - desired[1] <= MAX_AGE:
            direction = desired[0]
        if revision != self._seen_stop_revision:
            self._seen_stop_revision = revision
            await self._write(client, 'reset')
            self._active = None
            self._next_press = _now() + COOLDOWN_SECONDS
            return
        if self._active and (direction != self._active or now >= self._deadline):
            await self._write(client, 'reset' if direction != self._active else self._active + '80')
            self._active = None
            self._next_press = _now() + COOLDOWN_SECONDS
            return
        if direction and self._active is None and now >= self._next_press:
            # Recheck immediately before dispatch; never queue movement commands.
            with self._lock:
                valid = (revision == self._stop_revision and desired == self._desired
                         and not self._closed.is_set())
            if valid:
                self._active = direction
                self._deadline = now + PULSE_SECONDS
                await self._write(client, direction + '0')

    def _worker(self):
        try:
            asyncio.run(self._run())
        except Exception as exc:
            self.status = f'failed: {exc}'
            print(f'[Arduino BLE] {self.status}. Restart with --arduino-ble to retry.', flush=True)
        finally:
            self.connected = False
            self.stop()

    async def _run(self):
        try:
            from bleak import BleakClient, BleakScanner
        except ImportError as exc:
            raise RuntimeError('Install optional dependency: python -m pip install "bleak>=0.22,<4"') from exc
        device = self.address
        if not device:
            self.status = 'scanning'
            found = await BleakScanner.discover(timeout=5.0, return_adv=True,
                                               service_uuids=[SERVICE_UUID])
            matches = [d for d, adv in found.values()
                       if SERVICE_UUID in [u.lower() for u in adv.service_uuids]]
            if len(matches) != 1:
                raise RuntimeError(f'Found {len(matches)} Arduino service matches; '
                                   'use --arduino-address with the intended device address/UUID')
            device = matches[0]
        if self._closed.is_set():
            return
        async with BleakClient(device, timeout=10.0) as client:
            service = client.services.get_service(SERVICE_UUID)
            characteristic = service.get_characteristic(COMMAND_UUID) if service else None
            if not characteristic or 'write' not in characteristic.properties:
                raise RuntimeError('Arduino service/command characteristic missing or not writable')
            try:
                await self._write(client, 'reset')
                with self._lock:
                    self._desired = None  # Never replay evidence collected during connection.
                    self._seen_stop_revision = self._stop_revision
                    self.connected = not self._closed.is_set()
                self.status = 'connected'
                print('[Arduino BLE] Connected; text-command servo bridge ready.', flush=True)
                while not self._closed.is_set():
                    if not client.is_connected:
                        raise RuntimeError('Arduino disconnected; no automatic reconnection')
                    await self._tick(client)
                    await asyncio.sleep(0.02)
            finally:
                self.connected = False
                self.stop()
                if client.is_connected:
                    try:
                        await self._write(client, 'reset')
                    except Exception:
                        pass  # Firmware independently releases on disconnect / 2 s timeout.
                self._active = None
                self.status = 'disconnected'
