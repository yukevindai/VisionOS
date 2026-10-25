# Arduino 101 ServoController

This sketch is the firmware partner for Reflex's Arduino 101 text-command test. It retains the provided GATT UUIDs, local name, and command spellings.

## Commands

- `reset`: return all servos to the configured 70° release angle.
- `up0`, `down0`, `left0`, `right0`: move the matching servo to 0°. Multiple servos can be held at once, each with an independent timeout.
- `up80`, `down80`, `left80`, `right80`: release the matching active servo. These legacy names currently mean 70°, because `RELEASE_ANGLE` remains 70 to preserve the supplied hardware setup.

The app sends a press command when its direction toggle turns on and sends the matching release command when it turns off. A servo stays at 0° until that release arrives; the app sends an automatic release by 1,800 ms, and firmware has an independent 2,000 ms cutoff. Each servo has its own timer; repeated press packets do not restart that servo's deadline. All servos release when BLE disconnects. Multiple servos can be held concurrently. The 2-second limit follows `docs/R2_IOS_IMPLEMENTATION_SPEC.md`; it cannot be raised to six seconds without changing the product safety limit.

The automatic release is a firmware behavior, not a live-device validation result. Compile/upload this sketch to the Arduino 101 and verify the movement, timeout, and disconnect behaviors with the mechanism secured before using it near a person.
