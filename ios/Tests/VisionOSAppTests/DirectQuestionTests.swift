import XCTest
import Targets
import Tasks
@testable import VisionOS

@MainActor
private final class DirectQuestionReader: R3ObjectReading, R3QuestionReading {
    var objectCalls = 0
    var questions: [String] = []
    var pending: CheckedContinuation<GeminiObjectRecognition, Error>?
    func findObject(image: Data, query: String, frameID: String,
                    capturedAtUTCms: Int64, requestID: String) async throws -> GeminiObjectRecognition {
        objectCalls += 1
        throw GeminiObjectError.invalidResponse
    }
    func answerQuestion(image: Data, query: String, frameID: String,
                        capturedAtUTCms: Int64, requestID: String) async throws -> GeminiObjectRecognition {
        questions.append(query)
        return try await withCheckedThrowingContinuation { pending = $0 }
    }
    func complete(_ result: GeminiObjectRecognition) {
        pending?.resume(returning: result)
        pending = nil
    }
}

@MainActor
final class DirectQuestionTests: XCTestCase {
    private let context = SessionContext(sessionID: "SIMULATED-question-session", generation: 1)
    private let utc: Int64 = 1_700_000_000_000

    private func harness() -> (GeminiObjectEvidenceClient, DirectQuestionReader, RequestContext) {
        let reader = DirectQuestionReader()
        let client = GeminiObjectEvidenceClient(reader: reader, clock: ManualAppClock())
        client.updateContext(context)
        client.receive(.init(context: context, frameID: "question-frame", capturedAtMs: 1_000,
            expiresAtMs: 1_300, geometryID: "geometry", transform: .identity, preview: nil,
            encodedImage: Data([1]), trackingNormal: true, depthUsable: false), capturedAtUTCms: utc)
        let request = RequestContext(task: .init(sessionID: context.sessionID, taskID: "question", generation: 1),
            requestID: "question-request", issuedAtMs: 1_000, expiresAtMs: 6_000)
        return (client, reader, request)
    }

    private func response(_ answer: String?) -> GeminiObjectRecognition {
        let model = Targets.ScreenModel(frameId: "question-frame", capturedAtMs: utc, panelType: .other,
            source: .visionModel, controls: [], image: .init(widthPx: 640, heightPx: 480),
            evidence: .init(provider: "gemini", model: "synthetic-test-only", live: true))
        return .init(status: .notFound, selectedObjectId: nil, screenModel: model, answer: answer)
    }

    private func waitForQuestion(_ reader: DirectQuestionReader) async throws {
        for _ in 0..<100 {
            if reader.pending != nil { return }
            try await Task.sleep(nanoseconds: 10_000_000)
        }
        XCTFail("Question reader was not called")
        throw R3EvidenceError.missingFrame
    }

    func testInformationalRequestsAreQuestionsAndGuidanceRequestsRemainActions() {
        for question in ["what is in front of me", "What's in front of me?", "where are my keys",
                         "how many bottles are there", "is the bottle empty", "describe the room",
                         "tell me what you see", "Which book is red?", "Can you read this label?"] {
            XCTAssertTrue(ObjectRequestIntent.isQuestion(question), question)
        }
        for action in ["find my keys", "guide my hand to the cup", "can you guide me to the bottle?",
                       "help me pick up the book", "help me find my keys"] {
            XCTAssertFalse(ObjectRequestIntent.isQuestion(action), action)
        }
    }

    func testSceneWithMultipleObjectsAnswersWithoutSelectingATarget() async throws {
        let (client, reader, request) = harness()
        let operation = Task { try await client.answer(request, question: "what is in front of me") }
        try await waitForQuestion(reader)
        reader.complete(response("A blue cup and a red book are in front of you."))
        let answer = try await operation.value
        XCTAssertEqual(answer, "A blue cup and a red book are in front of you.")
        XCTAssertEqual(reader.questions, ["what is in front of me"])
        XCTAssertEqual(reader.objectCalls, 0)
    }

    func testStopDiscardsLateAnswerAndCannotRestartCancelledGeneration() async throws {
        let (client, reader, request) = harness()
        let operation = Task { try await client.answer(request, question: "describe the scene") }
        try await waitForQuestion(reader)
        client.cancel(context: context)
        reader.complete(response("There is a blue cup."))
        do { _ = try await operation.value; XCTFail("Late answer escaped cancellation") }
        catch { XCTAssertTrue(error is CancellationError) }
        client.updateContext(context)
        do { _ = try await client.answer(request, question: "describe the scene"); XCTFail("Cancelled scope restarted") }
        catch { XCTAssertEqual(error as? GeminiObjectError, .answerUnavailable) }
        XCTAssertEqual(reader.questions.count, 1)
    }

    func testFollowupQuestionAndInvalidAnswersAreRejected() async throws {
        for answer in [nil, "", "Which object do you mean?", String(repeating: "x", count: 2_001)] as [String?] {
            let (client, reader, request) = harness()
            let operation = Task { try await client.answer(request, question: "what is in front of me") }
            try await waitForQuestion(reader)
            reader.complete(response(answer))
            do { _ = try await operation.value; XCTFail("Invalid or followup answer accepted") }
            catch { XCTAssertEqual(error as? GeminiObjectError, .answerUnavailable) }
        }
    }
}
