# Arduino 101 ServoController

This sketch is the firmware partner for Reflex's Arduino 101 text-command test. It retains the provided GATT UUIDs, local name, and command spellings.

## Commands

- `reset`: return all servos to the configured 70° release angle.
- `up0`, `down0`, `left0`, `right0`: move one servo to 0°. Starting a new direction releases the previous servo first.
- `up80`, `down80`, `left80`, `right80`: release the matching active servo. These legacy names currently mean 70°, because `RELEASE_ANGLE` remains 70 to preserve the supplied hardware setup.

The app sends each manual test pulse for 180 ms, followed by the matching release command. The firmware independently returns the active servo to 70° after at most 2,000 ms and releases all servos when BLE disconnects. Repeated press packets for the active servo do not restart its deadline. The 2-second limit follows `docs/R2_IOS_IMPLEMENTATION_SPEC.md`; it cannot be raised to six seconds without changing the product safety limit.

The automatic release is a firmware behavior, not a live-device validation result. Compile/upload this sketch to the Arduino 101 and verify the movement, timeout, and disconnect behaviors with the mechanism secured before using it near a person.
