import XCTest
@testable import Tasks

final class KioskAndMenuTests: XCTestCase {
    typealias Harness = TaskEngineTests.Harness
    func screen(_ id: String = "menu", labels: [String] = ["Tea", "Coffee", "Back"], payment: Bool = false,
                welcome: Bool = false, expected: String? = nil, observation: VerificationEvidence? = nil) -> ScreenModel {
        let controls = labels.enumerated().map { index, label in
            TaskFixtures.target(id: "\(id)-\(index)", kind: .screenControl, label: label, screenID: id,
                                frameID: "\(id)-frame", expectedDestination: expected)
        }
        return ScreenModel(id: id, frameID: "\(id)-frame", title: id, controls: controls,
                           isPaymentScreen: payment, isWelcomeScreen: welcome, total: payment ? "$7.00" : nil,
                           transitionEvidence: observation)
    }

    func testKioskTapInvalidatesCoordinatesAndRereadsBeforeNextChoice() {
        var h = Harness(); h.start(.kiosk); h.send(.screen(screen()), source: .r3)
        h.send(.choice("Tea"), source: .user); h.acquire()
        let generation = h.engine.state.context.generation
        let guidance = h.engine.state.pendingRequest!
        let effects = h.onTarget()
        XCTAssertEqual(h.engine.state.phase, .readingScreen)
        XCTAssertNil(h.engine.state.target)
        XCTAssertEqual(h.engine.state.context.generation, generation + 1)
        XCTAssertTrue(effects.contains { if case .readScreen = $0 { return true }; return false })
        XCTAssertEqual(h.send(.choice("Coffee"), source: .user), [.rejected(.unsupportedState)])
        XCTAssertEqual(h.send(.guidance(mode: .hand, targetID: "menu-0", status: .onTarget, evidence: nil), context: guidance), [.rejected(.wrongContext)])
        h.send(.screen(screen("drinks")), source: .r3)
        XCTAssertEqual(h.engine.state.phase, .awaitingConfirmation)
        h.confirm()
        XCTAssertEqual(h.engine.state.phase, .choosing)
        XCTAssertEqual(h.engine.state.lastVerification, .userConfirmed)
    }

    func testKioskObservedTransitionAndTwoItemLoop() {
        var h = Harness(); h.start(.kiosk); h.send(.screen(screen()), source: .r3)
        for index in 0..<2 {
            let targetID = h.engine.state.screen!.controls[0].id
            h.send(.choice(h.engine.state.screen!.controls[0].label), source: .user); h.acquire(); h.onTarget()
            let observation = VerificationEvidence(targetID: targetID, actionRequestID: h.engine.state.pendingRequest!.requestID,
                                                   kind: .screenTransition, provenance: .implementedVisualVerifier,
                                                   beforeFrameID: "old-\(index)", afterFrameID: "new-\(index)", beforeMs: 99, afterMs: 100)
            h.send(.screen(screen("next-\(index)", observation: observation)), source: .r3)
            XCTAssertEqual(h.engine.state.phase, .choosing)
            XCTAssertEqual(h.engine.state.lastVerification, .observed)
        }
        h.send(.choice("Tea"), source: .user); h.acquire(); h.onTarget()
        let effects = h.send(.screen(screen("payment", payment: true)), source: .r3)
        XCTAssertEqual(h.engine.state.result?.status, .handoff)
        XCTAssertEqual(h.engine.state.result?.error, .paymentBoundary)
        XCTAssertFalse(effects.contains { if case .successFeedback = $0 { return true }; return false })
        XCTAssertTrue(effects.contains { if case .speak(let text, _) = $0 { return text.contains("$7.00") }; return false })
    }

    func testPaymentAndPurchaseControlsRefusedEvenOnUnflaggedScreen() {
        for label in ["Pay", "Confirm purchase", "Place order", "Submit order", "Checkout", "Buy now"] {
            var h = Harness(); h.start(.kiosk)
            h.send(.screen(screen(labels: [label])), source: .r3)
            let effects = h.send(.choice(label), source: .user)
            XCTAssertEqual(h.engine.state.result?.error, .paymentBoundary)
            XCTAssertFalse(effects.contains { if case .reacquire = $0 { return true }; return false })
        }
        var h = Harness(); h.start(.kiosk)
        let unknown = TaskFixtures.target(id: "unknown", kind: .screenControl, label: "Continue",
                                         safety: .unknownCheckout, screenID: "s", frameID: "f")
        h.send(.screen(.init(id: "s", frameID: "f", title: "s", controls: [unknown])), source: .r3)
        h.send(.choice("Continue"), source: .user)
        XCTAssertEqual(h.engine.state.result?.error, .paymentBoundary)
    }

    func testScreenChangeMidGuidanceStopsAndRejectsOldCoordinates() {
        var h = Harness(); h.start(.kiosk); h.send(.screen(screen()), source: .r3)
        h.send(.choice("Tea"), source: .user); h.acquire()
        let old = h.engine.state.pendingRequest!
        let effects = h.send(.screenChanged)
        XCTAssertEqual(h.engine.state.phase, .readingScreen); XCTAssertNil(h.engine.state.target)
        XCTAssertTrue(effects.contains { if case .allStop = $0 { return true }; return false })
        XCTAssertEqual(h.send(.guidance(mode: .hand, targetID: "menu-0", status: .onTarget, evidence: nil), context: old), [.rejected(.wrongContext)])
        h.send(.screen(screen("updated")), source: .r3)
        XCTAssertEqual(h.engine.state.phase, .choosing)
    }

    func testWrongScreenUsesCurrentIdentifiableBackControl() {
        var h = Harness(); h.start(.kiosk); h.send(.screen(screen(expected: "expected")), source: .r3)
        h.send(.choice("Tea"), source: .user); h.acquire(); h.onTarget()
        let effects = h.send(.screen(screen("unexpected")), source: .r3)
        XCTAssertEqual(h.engine.state.phase, .reacquiring)
        XCTAssertEqual(h.engine.state.target?.id, "unexpected-2")
        XCTAssertEqual(h.engine.state.target?.sourceScreenID, "unexpected")
        XCTAssertTrue(effects.contains { if case .reacquire(_, let target) = $0 { return target.label == "Back" }; return false })
    }

    func testWrongScreenWithoutBackFailsSafely() {
        var h = Harness(); h.start(.kiosk); h.send(.screen(screen(expected: "expected")), source: .r3)
        h.send(.choice("Tea"), source: .user); h.acquire(); h.onTarget()
        h.send(.screen(screen("unexpected", labels: ["Other"])), source: .r3)
        XCTAssertEqual(h.engine.state.result?.error, .screenChanged)
        XCTAssertNil(h.engine.state.target)
    }

    func testWelcomeResetNeedsExplicitRestart() {
        var h = Harness(); h.start(.kiosk); h.send(.screen(screen()), source: .r3)
        h.send(.choice("Tea"), source: .user); h.acquire(); h.onTarget()
        h.send(.screen(screen("welcome", welcome: true)), source: .r3)
        XCTAssertEqual(h.engine.state.result?.error, .kioskReset)
        XCTAssertEqual(h.engine.state.confirmation?.purpose, .restartKiosk)
        h.confirm(); XCTAssertEqual(h.engine.state.phase, .readingScreen)
        h.send(.screen(screen("welcome", welcome: true)), source: .r3)
        XCTAssertEqual(h.engine.state.phase, .choosing)
    }

    func testDuplicateLabelsRequireClarificationAndModelCannotChoose() {
        var h = Harness(); h.start(.kiosk); h.send(.screen(screen(labels: ["Tea", "Tea"])), source: .r3)
        h.send(.choice("Tea"), source: .user)
        XCTAssertEqual(h.engine.state.phase, .choosing); XCTAssertNil(h.engine.state.target)
        XCTAssertEqual(h.send(.choice("menu-0"), source: .model), [.rejected(.wrongSource)])
    }

    func testMalformedScreenRegionsAndFrameBindingRejected() {
        var h = Harness(); h.start(.kiosk)
        let target = TaskFixtures.target(kind: .screenControl, screenID: "other", frameID: "old")
        h.send(.screen(ScreenModel(id: "current", frameID: "new", title: "Current", controls: [target])), source: .r3)
        XCTAssertEqual(h.engine.state.result?.status, .failed)
    }

    func testKioskOptionsChunkRepeatAndMoreDoNotGuide() {
        var h = Harness(); h.start(.kiosk)
        let effects = h.send(.screen(screen(labels: ["One", "Two", "Three", "Four"])), source: .r3)
        XCTAssertTrue(effects.contains { if case .speak(let text, _) = $0 { return text.contains("Three") && !text.contains("Four") }; return false })
        let more = h.send(.menuCommand(.more), source: .user)
        XCTAssertTrue(more.contains { if case .speak(let text, _) = $0 { return text.contains("Four") }; return false })
        h.send(.menuCommand(.repeatOptions), source: .user)
        XCTAssertEqual(h.engine.state.phase, .choosing)
    }

    func testWallMenuReadOnlySectionsQuestionsAndUnreadableDisclosure() {
        var h = Harness(); h.start(.wallMenu)
        let menu = MenuContent(sections: [.init(title: "Drinks", items: ["Tea $3", "Coffee price unreadable"]),
                                          .init(title: "Food", items: ["Soup $6"])], isBlurry: true, isCutOff: true)
        let effects = h.send(.menu(menu), source: .r3)
        XCTAssertEqual(h.engine.state.phase, .discussingMenu)
        XCTAssertFalse(effects.contains { if case .requestGuidance = $0 { return true }; return false })
        XCTAssertTrue(effects.contains { if case .speak(let text, _) = $0 { return text.contains("blurry") }; return false })
        XCTAssertTrue(effects.contains { if case .speak(let text, _) = $0 { return text.contains("cut off") }; return false })
        h.send(.menuCommand(.more), source: .user)
        XCTAssertEqual(h.engine.state.sectionIndex, 1)
        h.send(.menuCommand(.back), source: .user)
        XCTAssertEqual(h.engine.state.sectionIndex, 0)
        h.send(.menuCommand(.question("How much is coffee?")), source: .user)
        XCTAssertEqual(h.engine.state.phase, .answeringMenu)
        let answer = h.send(.menuAnswer("The coffee price is unreadable."), source: .r3)
        XCTAssertEqual(h.engine.state.phase, .discussingMenu)
        XCTAssertTrue(answer.contains(.speak(text: "The coffee price is unreadable.", priority: 4)))
    }
}
