# Bluetooth integration checklist

This checklist connects the planned Bluetooth band connection to the existing R2 CoreBluetooth seams. BLE support, a GATT service, firmware behavior, and actuator type remain unconfirmed. In this checkout, `contracts/`, `firmware/`, and `hardware/` contain only `.gitkeep`; `ios/Packages/Perception/` contains only the laptop research prototype, and `ios/Packages/Guidance/` and `ios/Packages/Targets/` are placeholders without native implementations. No H1 contract values or adapter APIs are confirmed here.

For the broader safety gates and evidence acceptance cases, see [adapter acceptance](ADAPTER_ACCEPTANCE.md) and the [team build specification](../../docs/Cal_Hacks_13_Build_Spec_v3.md). This checklist does not replace T01–T14 or a live-device pass.

## H1 inputs required before enabling production BLE

Record these in H1-owned contract/firmware documentation and map only confirmed values into `H1Contract`:

- BLE-capable board and actuator per band; verified physical left/right identity method and advertisement data. `HG-L`/`HG-R` names alone are hints, not proof.
- Service, command, and status characteristic UUIDs; GATT properties and discovery behavior; command protocol version and supported firmware revision set.
- Exact 10-byte command and 5-byte status layouts, endianness, status fault-bit meanings, sequence acceptance rules, and how firmware reports battery/reboot/fault state.
- Parameters for ALL_STOP (ID 0), heartbeat (ID 255), and patterns 1–9: intensity, rate, TTL, physical meaning, timing/envelope, and supported revisions. R2's `H1Contract` initializer enforces a 600 ms TTL for pattern 6. Confirm whether stopped notifications 8/9 are supported and their semantics.
- Firmware-enforced TTL, 500 ms heartbeat timeout, disconnect stop, duty limit, and maximum continuous on-time behavior. Specify how each is validated on hardware.
- Identity verifier inputs and pairing/reconnect policy; confirmed mapping of two distinct peripheral IDs to left and right.
- A generation synchronization/reset handshake that establishes a generation and next unused sequence per band, is bound to the current challenge and peripheral IDs, and defines behavior after reboot, reconnect, app relaunch, and firmware faults. Include the persistence/reset policy needed to seed the app generation. A five-byte status packet alone does not acknowledge generation.
- Contract revision/source and a repeatable procedure for measuring BLE write dispatch separately from actuator onset.

Do not fill missing fields with test fixture values. Until all required inputs and the synchronization handshake are confirmed, leave `LiveIntegrationBundle.bands` omitted and keep wristbands unavailable.

## R2 integration sequence

1. Review H1's actual contract and firmware against `HapticLink`'s existing codec and guards. The current R2 projection is in `ios/Packages/HapticLink/Sources/HapticLink/Protocol.swift`; its real CoreBluetooth transport is `CoreBluetoothBandTransport.swift`. Resolve any incompatibility with H1 before mapping values; do not silently reinterpret packet fields.
2. Construct `H1Contract` using the confirmed UUIDs, protocol/revision set, stop and heartbeat parameters, all nine pattern parameters, identity/generation policy references, and stopped-notification support. The initializer rejects incomplete pattern tables and a pattern-6 TTL other than 600 ms.
3. Construct `CoreBluetoothBandTransport` with that configuration, saved known peripheral IDs, the confirmed `identityVerifier`, and an agreed reconnect policy. The iOS Bluetooth purpose string is already present in `ios/App/Info.plist`.
4. Inject the configuration and transport through `LiveBandIntegrationBundle` in `AppDependencies`. Pair each discovered identity through explicit user confirmation using `BandService.assign`; never derive its side from the advertised name. Confirm the app reaches ready only after service/characteristic validation, status notification, and H1 synchronization.
5. Bind the H1 handshake callback through `configureBandSynchronization`. Capture the controller's current `synchronizationChallengeID`; after validating both assigned peripheral identities and the H1 handshake, submit `GenerationSynchronizationProof` via `BandService.acceptSynchronization`. Reject late or mismatched proofs. Keep `generationSeed` consistent with the same confirmed persistent/reset policy and never default firmware generation to zero.
6. Connect only R1 scheduler-approved releases to `BandService.dispatch`. Map each released pattern to the exact confirmed H1 parameters and current task context. Keep `allStop` as the direct safety path. A transport write means locally queued to CoreBluetooth; record firmware acknowledgment and physical onset separately when H1 provides those signals.
7. Verify pairing/reconnect, calibration, freshness, bilateral stop, stale-generation rejection, disconnect and firmware failsafe on actual bands using T01–T03, T11–T14, and relevant R2-A–F cases. Record hardware, firmware, app revision, test setup, dispatch latency, actuator-onset latency, and outcome. Simulator/package results are not device acceptance.

## Current status

The R2 implementation has CoreBluetooth discovery/connection, GATT validation, status parsing, command serialization, safety gating, and typed synchronization-proof seams. Production H1 configuration is unavailable. No firmware contract, native external adapters, live BLE session, actuator onset, or device acceptance has been confirmed in this checkout.
