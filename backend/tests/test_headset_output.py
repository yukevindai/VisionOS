"""Bluetooth headset microphone -> play on the same headset's Hands-Free output. SIMULATED device lists."""
import unittest
from types import SimpleNamespace

from r3_vision.audio_guidance import headset_output


def sd(devices, default_in, default_out):
    return SimpleNamespace(query_devices=lambda: devices, default=SimpleNamespace(device=(default_in, default_out)))


def dev(name, inputs, outputs, host=0):
    return {'name': name, 'max_input_channels': inputs, 'max_output_channels': outputs, 'hostapi': host}


DEVICES = [dev('Microphone Array (Realtek(R) Audio)', 2, 0), dev('Speaker (Realtek(R) Audio)', 0, 2),
           dev('Headphones (AirPods Pro)', 0, 2), dev('Headset (AirPods Pro Hands-Free)', 1, 0),
           dev('Headset (AirPods Pro Hands-Free)', 0, 1), dev('Headset (AirPods Pro Hands-Free)', 0, 1, host=1)]


class HeadsetOutputTests(unittest.TestCase):
    def test_airpods_microphone_selects_airpods_hands_free_output(self):
        self.assertEqual(headset_output(sd(DEVICES, 3, 2)), 4)  # same host API as the default output

    def test_laptop_microphone_keeps_the_default_output(self):
        self.assertIsNone(headset_output(sd(DEVICES, 0, 2)))

    def test_no_matching_output_or_broken_device_list(self):
        self.assertIsNone(headset_output(sd(DEVICES[:4], 3, 2)))
        broken = SimpleNamespace(query_devices=lambda: (_ for _ in ()).throw(RuntimeError()),
                                 default=SimpleNamespace(device=(0, 1)))
        self.assertIsNone(headset_output(broken))


if __name__ == '__main__':
    unittest.main()
