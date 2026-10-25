# HapticLink

Swift 5.9 package for iOS 17 / macOS 13. Foundation logic is independent of CoreBluetooth; the real central is compiled when CoreBluetooth is available. Only this package writes wristband command bytes. All patterns except `ALL_STOP` must arrive as a release from R1's output scheduler, including calibration and success notifications. The package does not open a camera, play audio, interpret model requests, or choose guidance directions.

## Current integration availability

The repository supplies no confirmed H1 UUIDs, packet version, intensity/rate parameters, identity format, or generation reset/handshake policy. `HapticLinkConfiguration.unavailableH1` explicitly disables live discovery/output. There are no production UUID or firmware defaults. A Bluetooth connection and the baseline status packet cannot establish generation synchronization: status has no generation acknowledgment. `GenerationSynchronizationProof` is the adapter seam for H1's agreed handshake, not an implementation or claim that such a handshake exists.

The hardware is described as Bluetooth, but its BLE/GATT compatibility still needs H1 confirmation. The implemented CoreBluetooth transport follows the specifications' proposed GATT contract; that is not evidence of compatibility with the actual board.

All test hardware values are local synthetic fixtures. Passing these tests establishes app logic behavior; actuator onset, real Bluetooth behavior, independent 500 ms watchdog, TTL enforcement, disconnect silence, and 2 s continuous-on limit still require H1/device validation.

## Arduino 101 sketch compatibility

`Arduino101ReleaseClient` is a separate compatibility path for the supplied CurieBLE `ServoController` sketch. It scans for service `19B10000-E8F2-537E-4F6C-D104768A1214`, checks command characteristic `19B10001-E8F2-537E-4F6C-D104768A1214` has the GATT `write` property, and sends UTF-8 commands with `.withResponse`. Settings offers `reset` and independent servo hold toggles. Turning a toggle on sends `up0` (or its direction equivalent); turning it off sends `up80` (or its equivalent). Multiple servos may be active together. The app queues writes with responses; a servo remains at its commanded position until release, but the app auto-releases after 1.8 seconds and firmware independently auto-releases after 2 seconds. BLE disconnect releases all servos. These are manual hardware controls and are not connected to live R1 guidance cues or H1 wristband packets. The sketch's legacy `*80` release command uses its configured 70-degree release angle.

## App composition

Construct and use the controller on the main actor. Missing live integrations remain explicitly unavailable:

```swift
import HapticLink

@MainActor
func makeBands() throws -> HapticLinkController {
    let link = try HapticLinkController(
        configuration: .unavailableH1,
        transport: UnavailableBandTransport(),
        metadataStore: UserDefaultsPairingMetadataStore()
    )
    link.onEvent = { event in
        switch event {
        case .discovered(let band): print("Discovered identity", band.id)
        case .state(let id, let state): print(id, state)
        case .availability(let state): print(state)
        default: break
        }
        // Read link.statuses/pairings in app view bindings. Never infer side from nameHint.
    }
    link.onSafetyPause = { reason in
        // Invalidate the Tasks generation, cancel scheduled R1/R3 work, and request R1 Stop speech.
        // HapticLink has already cleared local output and attempted ALL_STOP.
    }
    link.start()
    return link
}

// UI Pause/End, local spoken Stop, hazard and interruptions share this direct path:
// let report = link.allStop(reason: "user_pause")
// Inspect report.sides: sentToTransport / pendingBackpressure / unavailable.
// Sent means local CoreBluetooth enqueue, never firmware acknowledgment or actuator onset.
```

After H1 confirms the contract, construct `H1Contract` with all three UUIDs, supported firmware revisions, all nine pattern parameter envelopes, stop/heartbeat parameters, and identity/generation policy descriptions. `CoreBluetoothBandTransport(configuration:knownPeripheralIDs:reconnectPolicy:identityVerifier:)` requires an H1 identity verifier; `HG-L`/`HG-R` names alone do not prove identity. Supply previously confirmed peripheral IDs from pairing metadata, and provide the Bluetooth purpose string in the app target. No background Bluetooth mode is assumed.

`start()` binds event callbacks, starts discovery and automatic 20 ms maintenance ticks, and reconnects saved identities when Bluetooth powers on. Heartbeat dispatch targets 200 ms; this is app scheduling, not a real-time guarantee. Explicit side confirmation calls `assign(_:to:confirmedByUser:)`, which persists the pairing and connects that peripheral. Replacing a side requires `forget(_:)` first. Reconnecting rediscovers/validates services, write-without-response support, notification subscription, and a correctly sized supported-version status before readiness. It never replays a cached cue. Reconnect is bounded to three retries after the initial attempt, with configurable delay and connection deadline. `shutdown()` stops, disconnects and releases callbacks/timers; a subsequent `start()` rebinds them and still requires a new handshake/context.

Confirming the same assigned band while it is ready or connecting leaves its connection intact, including the characteristic needed for immediate Stop. Repeated connect requests preserve an active retry budget; confirming a disconnected or failed pairing may explicitly retry.

## Generation and scheduler adapter

1. Complete H1's agreed synchronization procedure for all assigned bands, binding the async request to `link.synchronizationChallengeID` and current peripheral identities.
2. Return `GenerationSynchronizationProof(source: .confirmedH1Handshake(contractReference), challengeID: capturedChallenge, peripheralIDs: ..., generation: ..., nextSequences: ...)`. A relaunch, disconnect, firmware fault/reboot indication, pairing change or shutdown invalidates the challenge. Late proofs are rejected. Fixture proofs work only with a transport explicitly marked simulated.
3. Convert app context exactly with `try HapticContext(sessionID: appSessionString, taskID: appTaskString, generation: appUInt64)`. Values above UInt16 fail; no truncation, wrapping or invented independent generation is allowed.
4. Call `beginGeneration(context)` only for an eligible scheduler release in that context. Context and calibration mode cannot change within an active generation; stop first. A direct `allStop` fences the current generation; Tasks must issue a strictly higher generation before another cue, including finite success/stopped notifications. An accepted handshake floor cannot later decrease in the same process, including before the first output. Sequence UInt16.max is reserved for stop, and exhaustion disables subsequent output. No reset policy is inferred from connection or app relaunch.
5. Translate a released R1 envelope into `ScheduledHapticRelease(id:context:kind:evidenceTime:releasedAt:expiresAt:commands:)` and call `submit`. The command dictionary expresses the exact participating sides selected by R1. Monotonic timestamps must share the device clock (`ProcessInfo.systemUptime` by default); never pass wall/server timestamps. Only use agreed H1 intensity/rate values. Non-stop command IDs are 1...9; raw stop/heartbeat releases are rejected.

Movement patterns 1...5 require evidence at most 300 ms old at **each band dispatch**. The encoded TTL is clamped to the remaining evidence and scheduler-release window. Backpressure retains one latest release, replacing superseded work and expiring stale work. A bilateral cue waits for both sides to become writable; a write failure stops the successful half. A directional change stops any previously active nonparticipating side first. A bounded deduplication window rejects duplicates, older release times, wrong context/generation, future timestamps, and excess scheduler rates.

Sequences are reserved before calling the transport. A synchronous fault or Stop during a write cancels the rest of that release and its heartbeats without reusing the command sequence. A failed write can consume a sequence; exhaustion still fails closed.

`finiteNotification` admits patterns 6/7 with their complete agreed duration. Pattern 6 stays 600 ms; it does not authorize continued movement or extend movement evidence freshness. `stoppedNotification` admits 8/9 only after output is stopped and H1 explicitly confirms those semantics. Unsupported firmware keeps those notifications silent. All remain immediately cancellable. Heartbeats apply only to an unexpired eligible movement or finite notification; they never extend output eligibility or re-trigger an expired command.

`beginGeneration(context, calibration: true)` suspends normal physical releases and admits scheduler-released `calibration` envelopes for all nine patterns. The app owns the accessible explanation and all explicit side/meaning responses (two side checks plus nine pattern checks), repeats misunderstandings, and calls `confirmCalibration(for:firmwareVersion:confirmedByUser:)` only after the full sequence. Normal output requires matching completed calibration. Re-pairing or firmware revision changes invalidate the affected calibration. A simulated transport must remain visibly/audibly labelled SIMULATED by the app.

## Safety and persistence

`ALL_STOP` clears the pending release and all heartbeat eligibility before attempting both bands. It bypasses the scheduler, honors transport backpressure, and remains ahead of new releases. Repeating an unchanged stop is idempotent. No stop packet is claimed when version/generation/sequence or transport readiness is unavailable; independent firmware expiry is the fallback. With no confirmed packet version/generation, the package clears app output but cannot invent a safe wire stop. A disconnect during bilateral guidance stops the remaining side, invalidates synchronization and invokes `onSafetyPause`; one-band remapping is never automatic.

Status is exactly five bytes: battery, firmware revision, little-endian last sequence, faults. Unknown firmware revisions, malformed lengths/battery, any nonzero fault, and sequence regression fail closed. Fault bit assignments and battery thresholds are not guessed. Sequence regression is a reboot/stale-status indication; baseline status cannot reliably detect every silent same-revision reboot, so H1's session policy must provide the remaining guarantee.

Pairing stores persist only peripheral identity, confirmed side and explicit calibration firmware/completion. They never persist active tasks or reset generations on launch. `diagnostics` retains 256 structured events with monotonic time, optional context and side; no scene/audio/transcript/token content is stored. The app may expose explicit developer export. Discovery is capped at 64 identities. Pending output is bounded to one latest release plus one priority stop per assigned band.

## Validation

Run `swift test` inside this package. Tests inject a controllable clock and simulated transport, without sleeps, and cover byte layout/parsing, freshness/TTL, backpressure/supersession, priority stop, partial bilateral failures, heartbeats, finite notification duration, disconnect/reconnect gating, authorization/power loss, exact generation conversion/exhaustion, late handshake rejection, firmware reboot indications, calibration/side persistence and lifecycle cleanup.

Validated with Apple Swift 6.3.3 in Swift 5 language mode on macOS, including compilation of the actual CoreBluetooth central. Physical BLE/firmware behavior and latency remain unmeasured. The implementation uses Apple's [write readiness property](https://developer.apple.com/documentation/corebluetooth/cbperipheral/cansendwritewithoutresponse) and [readiness delegate callback](https://developer.apple.com/documentation/corebluetooth/cbperipheraldelegate/peripheralisready(tosendwritewithoutresponse:)). Delegates are explicitly delivered on the main queue and bridge into the main actor with Swift 5.9's `MainActor.assumeIsolated`.
