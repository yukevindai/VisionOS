# Physical iPhone launch and depth capture — October 24, 2026

Reflex Debug was built, signed, installed and launched on a physical iPhone 18 Pro
running iOS 27.2 (24B5099f), using Xcode 27.1 (27A9275). Developer Mode was enabled;
CoreDevice reported its developer image compatible and usable. This supersedes the
older installation blocker only for this phone/toolchain combination.

## Launch failure and fix

The first installation succeeded, but launch immediately failed with
`Library not loaded: @rpath/LiveKitWebRTC.framework/LiveKitWebRTC`.
Both LiveKitWebRTC and RustLiveKitUniFFI were already embedded in the app's
Frameworks directory. The executable lacked the framework search path.

The project generator now sets app `LD_RUNPATH_SEARCH_PATHS` to include inherited
values and `@executable_path/Frameworks` in Debug and Release. Regenerated the
committed Xcode project. No dependency version, source behavior, shared scheme,
personal signing team or service configuration was changed.

## Actual results

- Signed Debug iPhone build passed; installation and continued process execution
  succeeded after the fix.
- The user started `--native-device-check`, granted camera access and confirmed
  the preview and counters were working.
- The real device log reached 270 processed frames with 267 depth frames.
  Right-hand and measured-fingertip counters were both zero at that checkpoint.
  This verifies live camera/ARKit scene-depth delivery, not hand tracking,
  depth accuracy, orientation correctness or guidance acceptance.
- Unsigned Release iPhone build passed. Mach-O inspection confirmed
  `@executable_path/Frameworks` in both Debug and Release outputs.
- Swift packages passed: Guidance 11, Targets 20, Tasks 92, HapticLink 44
  (167 total). These are logic tests, not hardware tests.
- Re-running the project generator produced identical project bytes.

Local logs: `/tmp/reflex-iphone-sensor-console.log` (initial launch failure),
`/tmp/reflex-iphone-device-fixed-build.log`,
`/tmp/reflex-iphone-sensor-fixed-console.log`,
`/tmp/reflex-device-fix-release.log`, and
`/tmp/reflex-device-fix-{Guidance,Targets,Tasks,HapticLink}.log`.

App/UI simulator tests and accessibility audits were not rerun for this build-setting
fix. The three previously documented contrast audit failures remain unresolved.
No live microphone/Stop, hand guidance, object accuracy, friend-video, cloud speech,
or wristband acceptance is claimed. The normal app is launched separately after
the sensor check; opening its home screen does not establish those feature passes.
