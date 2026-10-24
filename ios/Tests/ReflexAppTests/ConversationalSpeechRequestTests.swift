import XCTest
@testable import Reflex

@MainActor
private final class HeldSpeechProvider: ConversationalSpeechSynthesizing {
    var pending: [String: CheckedContinuation<Data?, Error>] = [:]
    var onStart: ((String) -> Void)?
    var onReturn: (() -> Void)?
    func synthesize(text: String, requestID: String) async throws -> Data? {
        defer { onReturn?() }
        return try await withCheckedThrowingContinuation { continuation in
            pending[text] = continuation
            onStart?(text)
        }
    }
    func complete(_ text: String, result: Result<Data?, Error>) {
        pending.removeValue(forKey: text)?.resume(with: result)
    }
}

@MainActor
final class ConversationalSpeechRequestTests: XCTestCase {
    func testOverdueProviderFallsBackEvenBeforeDeadlineCallbackIsServiced() async {
        let clock = ManualAppClock(), provider = HeldSpeechProvider()
        let request = ConversationalSpeechRequest(provider: provider, clock: clock)
        let started = expectation(description: "provider started")
        provider.onStart = { _ in started.fulfill() }
        let completed = expectation(description: "expired response uses fallback")
        request.start(text: "late") { audio in
            XCTAssertNil(audio)
            completed.fulfill()
        }
        await fulfillment(of: [started], timeout: 1)
        clock.nowMs += 5_000 // Model the actor not having serviced scheduled callbacks yet.
        provider.complete("late", result: .success(Data([1])))
        await fulfillment(of: [completed], timeout: 1)
        XCTAssertFalse(request.isPending)
        XCTAssertEqual(clock.pendingCount, 0)
    }
    func testTimeoutFallsBackOnceWithoutWaitingForUncooperativeProvider() async {
        let clock = ManualAppClock(), provider = HeldSpeechProvider()
        let request = ConversationalSpeechRequest(provider: provider, clock: clock)
        let started = expectation(description: "provider started")
        provider.onStart = { _ in started.fulfill() }
        var outputs: [Data?] = []
        request.start(text: "menu") { outputs.append($0) }
        XCTAssertTrue(request.isPending)
        await fulfillment(of: [started], timeout: 1)
        clock.advance(4_999)
        XCTAssertTrue(outputs.isEmpty)
        clock.advance(1)
        XCTAssertFalse(request.isPending)
        XCTAssertEqual(outputs.count, 1)
        XCTAssertNil(outputs[0])
        XCTAssertEqual(clock.pendingCount, 0)
        let returned = expectation(description: "late provider returned")
        provider.onReturn = { returned.fulfill() }
        provider.complete("menu", result: .success(Data([1])))
        await fulfillment(of: [returned], timeout: 1)
        XCTAssertEqual(outputs.count, 1)
    }

    func testCancellationDropsLateSuccessAndDoesNotFallBack() async {
        let clock = ManualAppClock(), provider = HeldSpeechProvider()
        let request = ConversationalSpeechRequest(provider: provider, clock: clock)
        let started = expectation(description: "provider started")
        provider.onStart = { _ in started.fulfill() }
        var count = 0
        request.start(text: "cancelled") { _ in count += 1 }
        await fulfillment(of: [started], timeout: 1)
        request.cancel()
        XCTAssertFalse(request.isPending)
        clock.advance(6_000)
        let returned = expectation(description: "cancelled provider returned")
        provider.onReturn = { returned.fulfill() }
        provider.complete("cancelled", result: .success(Data([1])))
        await fulfillment(of: [returned], timeout: 1)
        XCTAssertEqual(count, 0)
        XCTAssertEqual(clock.pendingCount, 0)
    }

    func testNewUtteranceReplacesOldEvenWhenOldProviderFailsLate() async {
        let clock = ManualAppClock(), provider = HeldSpeechProvider()
        let request = ConversationalSpeechRequest(provider: provider, clock: clock)
        let first = expectation(description: "first started")
        provider.onStart = { _ in first.fulfill() }
        var outputs: [Data?] = []
        request.start(text: "old") { outputs.append($0) }
        await fulfillment(of: [first], timeout: 1)
        let next = expectation(description: "new started")
        provider.onStart = { _ in next.fulfill() }
        request.start(text: "new") { outputs.append($0) }
        await fulfillment(of: [next], timeout: 1)
        let returned = expectation(description: "both returned")
        returned.expectedFulfillmentCount = 2
        provider.onReturn = { returned.fulfill() }
        provider.complete("old", result: .failure(URLError(.cancelled)))
        provider.complete("new", result: .success(Data([2])))
        await fulfillment(of: [returned], timeout: 1)
        XCTAssertEqual(outputs, [Data([2])])
        XCTAssertFalse(request.isPending)
        clock.advance(6_000)
        XCTAssertEqual(outputs.count, 1)
    }

    func testProviderErrorAndSimulatedNilEachFallBackOnce() async {
        for result in [Result<Data?, Error>.success(nil), .failure(URLError(.notConnectedToInternet))] {
            let clock = ManualAppClock(), provider = HeldSpeechProvider()
            let request = ConversationalSpeechRequest(provider: provider, clock: clock)
            let started = expectation(description: "provider started")
            provider.onStart = { _ in started.fulfill() }
            let completed = expectation(description: "fallback")
            request.start(text: "options") { audio in
                XCTAssertNil(audio)
                completed.fulfill()
            }
            await fulfillment(of: [started], timeout: 1)
            provider.complete("options", result: result)
            await fulfillment(of: [completed], timeout: 1)
            XCTAssertEqual(clock.pendingCount, 0)
        }
    }

    func testSynchronousCancelBeforeProviderStartsDoesNotCallProvider() async {
        let clock = ManualAppClock(), provider = HeldSpeechProvider()
        let request = ConversationalSpeechRequest(provider: provider, clock: clock)
        provider.onStart = { _ in XCTFail("cancelled request reached provider") }
        request.start(text: "stop") { _ in XCTFail("cancelled request spoke") }
        request.cancel()
        await Task.yield()
        XCTAssertTrue(provider.pending.isEmpty)
        XCTAssertEqual(clock.pendingCount, 0)
    }
}
