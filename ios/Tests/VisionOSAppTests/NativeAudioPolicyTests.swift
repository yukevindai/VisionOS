import Foundation
import XCTest
@testable import VisionOS

final class NativeAudioPolicyTests: XCTestCase {
    func testCancelledAudioDoesNotMuteOwnerInputUntilFormerClipEnd() {
        XCTAssertEqual(NativeAudioTiming.echoExpiryAfterCancellation(currentExpiry: 60_000, nowMs: 1_000), 1_500)
        XCTAssertEqual(NativeAudioTiming.echoExpiryAfterCancellation(currentExpiry: 1_200, nowMs: 1_000), 1_200)
        XCTAssertEqual(NativeAudioTiming.echoExpiryAfterCancellation(currentExpiry: 900, nowMs: 1_000), 900)
        XCTAssertEqual(NativeAudioTiming.echoExpiryAfterCancellation(currentExpiry: Int64.max, nowMs: Int64.max - 5), Int64.max)
        XCTAssertEqual(NativeAudioTiming.echoExpiryAfterCancellation(currentExpiry: 900, nowMs: -1), 900)
    }
    func testGuidanceCannotPreemptStopOrHazard() {
        var state = NativeAudioOutputState()
        let stop = state.begin(priority: 0, kind: .speech)
        XCTAssertFalse(state.permits(priority: 2))
        XCTAssertTrue(state.isCurrent(stop))
        XCTAssertFalse(state.permits(priority: -1))
        XCTAssertFalse(state.permits(priority: 5))
    }
    func testExpiredOldCueCannotClearNewStop() {
        var state = NativeAudioOutputState()
        let cue = state.begin(priority: 2, kind: .guidance)
        XCTAssertTrue(state.permits(priority: 0))
        state.cancel()
        let stop = state.begin(priority: 0, kind: .speech)
        XCTAssertFalse(state.finish(cue))
        XCTAssertTrue(state.isCurrent(stop))
        XCTAssertFalse(state.permits(priority: 4))
        XCTAssertTrue(state.finish(stop))
        XCTAssertTrue(state.permits(priority: 4))
    }
    func testSamePriorityReplacementInvalidatesOldDelegateCompletion() {
        var state = NativeAudioOutputState()
        let old = state.begin(priority: 2, kind: .guidance)
        state.cancel()
        let fresh = state.begin(priority: 2, kind: .guidance)
        XCTAssertFalse(state.finish(old))
        XCTAssertTrue(state.isCurrent(fresh))
        state.cancel()
        XCTAssertFalse(state.isCurrent(fresh))
    }
    func testGuidanceDeadlineRejectsStaleFutureOverflowAndNegativeClock() {
        XCTAssertNil(NativeAudioTiming.guidanceExpiry(requested: 100, nowMs: 100))
        XCTAssertNil(NativeAudioTiming.guidanceExpiry(requested: 99, nowMs: 100))
        XCTAssertNil(NativeAudioTiming.guidanceExpiry(requested: 100, nowMs: -1))
        XCTAssertEqual(NativeAudioTiming.guidanceExpiry(requested: 1_000, nowMs: 100), 280)
        XCTAssertEqual(NativeAudioTiming.guidanceExpiry(requested: 150, nowMs: 100), 150)
        XCTAssertEqual(NativeAudioTiming.guidanceExpiry(requested: Int64.max, nowMs: Int64.max - 1), Int64.max)
        XCTAssertNil(NativeAudioTiming.guidanceExpiry(requested: Int64.max, nowMs: Int64.max))
    }
    func testAudioDurationNeverConvertsNonfiniteOrUnboundedValues() {
        for seconds in [Double.nan, Double.infinity, -Double.infinity, -1, 0, 61, Double.greatestFiniteMagnitude] {
            XCTAssertNil(NativeAudioTiming.durationMilliseconds(seconds: seconds))
        }
        XCTAssertEqual(NativeAudioTiming.durationMilliseconds(seconds: 0.0001), 1)
        XCTAssertEqual(NativeAudioTiming.durationMilliseconds(seconds: 60), 60_000)
        XCTAssertNil(NativeAudioTiming.deadline(nowMs: -1, durationMs: 10))
        XCTAssertNil(NativeAudioTiming.deadline(nowMs: 1, durationMs: -10))
        XCTAssertEqual(NativeAudioTiming.deadline(nowMs: Int64.max - 5, durationMs: 10), Int64.max)
    }
    func testStopRecognizedWithinUtteranceWithoutEchoSuppressionRule() {
        XCTAssertEqual(NativeVoiceInput.command("Please STOP!"), .stop)
        XCTAssertEqual(NativeVoiceInput.command("Stop moving my hand"), .stop)
        XCTAssertEqual(NativeVoiceInput.command("pause assistance"), .pause)
        XCTAssertEqual(NativeVoiceInput.command("resume assistance"), .resume)
        XCTAssertNil(NativeVoiceInput.command("find the stoplight"))
    }
}
