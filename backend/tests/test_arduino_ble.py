"""SIMULATED BLE protocol tests; no physical hardware acceptance implied."""
import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from r3_vision.arduino_ble import ArduinoBLE, COMMAND_UUID


class Client:
    def __init__(self):
        self.commands = []

    async def write_gatt_char(self, uuid, data, response):
        assert uuid == COMMAND_UUID and response is True
        self.commands.append(data.decode())


class ArduinoTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bridge = ArduinoBLE()
        self.bridge.connected = True
        self.client = Client()
        self.clock = patch('r3_vision.arduino_ble._now', return_value=10.0)
        self.now = self.clock.start()
        self.addCleanup(self.clock.stop)

    async def tick(self, timestamp):
        self.now.return_value = timestamp
        await self.bridge._tick(self.client)

    async def test_latest_intent_and_exact_protocol(self):
        self.bridge.submit('left', 10.0)
        self.bridge.submit('right', 10.0)
        await self.tick(10.0)
        self.assertEqual(self.client.commands, ['right0'])
        await self.tick(10.21)
        self.assertEqual(self.client.commands, ['right0', 'right80'])
        self.bridge.submit('right', 10.21)
        await self.tick(10.22)
        self.assertEqual(len(self.client.commands), 2)

    async def test_stale_or_future_evidence_never_presses(self):
        for timestamp in (9.0, 11.0, float('nan')):
            self.bridge.submit('up', timestamp)
            await self.tick(10.0)
        self.assertEqual(self.client.commands, ['reset'] * 3)

    async def test_stalled_camera_releases_without_more_submissions(self):
        self.bridge.submit('up', 10.0)
        await self.tick(10.0)
        await self.tick(10.31)
        self.assertEqual(self.client.commands, ['up0', 'reset'])

    async def test_stop_cannot_be_overwritten_by_next_frame(self):
        self.bridge.submit('up', 10.0)
        await self.tick(10.0)
        self.bridge.stop()
        self.bridge.submit('down', 10.0)
        await self.tick(10.01)
        self.assertEqual(self.client.commands, ['up0', 'reset'])

    async def test_direction_change_resets_before_new_press(self):
        self.bridge.submit('up', 10.0)
        await self.tick(10.0)
        self.bridge.submit('down', 10.0)
        await self.tick(10.01)
        self.assertEqual(self.client.commands, ['up0', 'reset'])

    async def test_unknown_direction_is_stop(self):
        self.bridge.submit('forward', 10.0)
        await self.tick(10.0)
        self.assertEqual(self.client.commands, ['reset'])

    async def test_disconnected_submissions_are_not_replayed(self):
        self.bridge.connected = False
        self.bridge.submit('left', 10.0)
        self.bridge.connected = True
        await self.tick(10.0)
        self.assertEqual(self.client.commands, [])

    async def test_write_failure_propagates_to_fail_closed_worker(self):
        class Broken:
            async def write_gatt_char(self, *args, **kwargs):
                raise OSError('link lost')
        self.bridge.submit('left', 10.0)
        with self.assertRaises(OSError):
            await self.bridge._tick(Broken())

    async def test_hung_write_has_bounded_timeout(self):
        class Hung:
            async def write_gatt_char(self, *args, **kwargs):
                await asyncio.Event().wait()
        with patch('r3_vision.arduino_ble.WRITE_TIMEOUT', 0.01):
            with self.assertRaises(TimeoutError):
                await self.bridge._write(Hung(), 'reset')

    async def test_ambiguous_scan_refuses_connection(self):
        from r3_vision.arduino_ble import SERVICE_UUID
        class Scanner:
            @staticmethod
            async def discover(**kwargs):
                adv = SimpleNamespace(service_uuids=[SERVICE_UUID])
                return {'a': ('device-a', adv), 'b': ('device-b', adv)}
        module = SimpleNamespace(BleakClient=None, BleakScanner=Scanner)
        with patch.dict(sys.modules, {'bleak': module}):
            with self.assertRaisesRegex(RuntimeError, 'Found 2'):
                await self.bridge._run()

    async def test_connection_clears_old_intent_and_resets_on_exit(self):
        from r3_vision.arduino_ble import SERVICE_UUID
        client = Client()
        class Connection:
            is_connected = True
            services = SimpleNamespace(get_service=lambda uuid: SimpleNamespace(
                get_characteristic=lambda uuid: SimpleNamespace(properties=['write'])))
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def write_gatt_char(self, *args, **kwargs):
                await client.write_gatt_char(*args, **kwargs)
        self.bridge.address = 'chosen-address'
        self.bridge.submit('up', 10.0)
        async def close_after_connect(connection):
            self.assertIsNone(self.bridge._desired)
            self.assertTrue(self.bridge.connected)
            self.bridge._closed.set()
        module = SimpleNamespace(BleakClient=lambda *args, **kwargs: Connection(), BleakScanner=None)
        with patch.dict(sys.modules, {'bleak': module}), patch.object(self.bridge, '_tick', close_after_connect):
            await self.bridge._run()
        self.assertEqual(client.commands, ['reset', 'reset'])
        self.assertFalse(self.bridge.connected)


if __name__ == '__main__':
    unittest.main()
