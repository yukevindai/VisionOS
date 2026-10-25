import Foundation
import Targets

struct GeminiObjectRecognition: Equatable, Sendable {
    enum Status: String, Sendable {
        case selected = "SELECTED"
        case notFound = "NOT_FOUND"
        case ambiguous = "AMBIGUOUS"
        case providerUnavailable = "PROVIDER_UNAVAILABLE"
    }

    let status: Status
    let selectedObjectId: String?
    let screenModel: Targets.ScreenModel
    var answer: String? = nil
}

@MainActor
protocol R3ObjectReading: AnyObject {
    func findObject(image: Data, query: String, frameID: String,
                    capturedAtUTCms: Int64, requestID: String) async throws -> GeminiObjectRecognition
}

@MainActor
protocol R3QuestionReading: AnyObject {
    func answerQuestion(image: Data, query: String, frameID: String,
                        capturedAtUTCms: Int64, requestID: String) async throws -> GeminiObjectRecognition
}

/// Native client for the proposed authenticated R3 object-reading action.
/// A returned observation is semantic evidence, never permission to move.
@MainActor
final class ConvexObjectClient: R3ObjectReading, R3QuestionReading {
    private let backend: ConvexNativeMediaBackend

    static func live() throws -> ConvexObjectClient {
        let configuration = try LiveServiceConfiguration.load()
        let identity = StoredNativeIdentity(configuration: configuration)
        return ConvexObjectClient(backend: try ConvexNativeMediaBackend(
            origin: configuration.convexURL, identity: identity))
    }

    init(backend: ConvexNativeMediaBackend) { self.backend = backend }

    func findObject(image: Data, query: String, frameID: String,
                    capturedAtUTCms: Int64, requestID: String) async throws -> GeminiObjectRecognition {
        try await backend.findObject(image: image, query: query, frameID: frameID,
                                     capturedAtUTCms: capturedAtUTCms, requestID: requestID)
    }
    func answerQuestion(image: Data, query: String, frameID: String,
                        capturedAtUTCms: Int64, requestID: String) async throws -> GeminiObjectRecognition {
        try await backend.findObject(image: image, query: query, frameID: frameID,
                                     capturedAtUTCms: capturedAtUTCms, requestID: requestID, answerQuestion: true)
    }

}
