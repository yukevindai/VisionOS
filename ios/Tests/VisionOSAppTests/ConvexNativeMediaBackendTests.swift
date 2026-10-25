import Foundation
import XCTest
import Targets
@testable import VisionOS

@MainActor
private final class MediaTestIdentity: NativeMediaIdentityProviding {
    var onIdentityInvalidated: (() -> Void)?
    var credentials = NativeMediaCredentials(jwt: "header.payload.signature", accountID: "owner-A", identityEpoch: UUID())
    var changeOnNextRead = false
    var onSecondRead: (() -> Void)?
    private var readCount = 0
    func currentCredentials() async throws -> NativeMediaCredentials {
        readCount += 1
        if readCount == 2 { onSecondRead?() }
        if changeOnNextRead && readCount == 2 {
            changeOnNextRead = false
            switchAccount()
        }
        return credentials
    }
    func switchAccount() {
        credentials = NativeMediaCredentials(jwt: "other.payload.signature", accountID: "owner-B", identityEpoch: UUID())
        onIdentityInvalidated?()
    }
}

private final class MediaURLProtocol: URLProtocol {
    static var handler: ((URLRequest) throws -> (HTTPURLResponse, Data))?
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        do {
            let (response, body) = try Self.handler!(request)
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: body)
            client?.urlProtocolDidFinishLoading(self)
        } catch { client?.urlProtocol(self, didFailWithError: error) }
    }
    override func stopLoading() {}
}

private func mediaResponse(_ request: URLRequest, value: Any, status: Int = 200) throws -> (HTTPURLResponse, Data) {
    let body = try JSONSerialization.data(withJSONObject: ["status": "success", "value": value])
    return (HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: nil)!, body)
}

private func requestBody(_ request: URLRequest) throws -> Data {
    if let body = request.httpBody { return body }
    guard let stream = request.httpBodyStream else { throw NativeMediaTransportError.invalidRequest }
    stream.open()
    defer { stream.close() }
    var body = Data()
    var bytes = [UInt8](repeating: 0, count: 4_096)
    while stream.hasBytesAvailable {
        let count = stream.read(&bytes, maxLength: bytes.count)
        if count <= 0 { break }
        body.append(contentsOf: bytes.prefix(count))
    }
    return body
}

@MainActor
final class ConvexNativeMediaBackendTests: XCTestCase {
    private func makeBackend(_ identity: MediaTestIdentity) throws -> ConvexNativeMediaBackend {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [MediaURLProtocol.self]
        return try ConvexNativeMediaBackend(origin: URL(string: "https://example.convex.cloud")!,
                                            identity: identity, session: URLSession(configuration: config))
    }

    func testAuthenticatedConvexRequestUsesExpectedPathAndShape() async throws {
        let identity = MediaTestIdentity()
        let backend = try makeBackend(identity)
        let epoch = UUID()
        MediaURLProtocol.handler = { request in
            XCTAssertEqual(request.url?.path, "/api/mutation")
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer header.payload.signature")
            XCTAssertEqual(request.value(forHTTPHeaderField: "Cache-Control"), "no-store")
            let body = try XCTUnwrap(JSONSerialization.jsonObject(with: requestBody(request)) as? [String: Any])
            XCTAssertEqual(body["path"] as? String, "sessions:start")
            XCTAssertEqual(body["format"] as? String, "convex_encoded_json")
            let args = try XCTUnwrap((body["args"] as? [[String: Any]])?.first)
            XCTAssertEqual(args["epoch"] as? String, epoch.uuidString)
            return try mediaResponse(request, value: ["_id": "opaque-convex-session", "epoch": epoch.uuidString])
        }
        let binding = try await backend.start(epoch: epoch)
        XCTAssertEqual(binding.sessionID, "opaque-convex-session")
    }

    func testSnapshotRejectsForgedTypesAndMismatchedScope() async throws {
        let identity = MediaTestIdentity()
        let backend = try makeBackend(identity)
        let binding = NativeMediaBinding(sessionID: "opaque-id", epoch: UUID())
        var value: [String: Any] = ["sessionId": binding.sessionID, "epoch": binding.epoch.uuidString,
                                    "status": "active", "authorizationVersion": 1, "sharing": false,
                                    "cleanupPending": false, "media": ["kind": "unready"],
                                    "slot": ["kind": "empty"], "validForMs": 1000, "nonce": "nonce-1"]
        MediaURLProtocol.handler = { request in try mediaResponse(request, value: value) }
        let valid = try await backend.snapshot(binding, nonce: "nonce-1")
        XCTAssertEqual(valid.media, .unready)
        value["sharing"] = 1
        do { _ = try await backend.snapshot(binding, nonce: "nonce-1"); XCTFail("numeric boolean accepted") }
        catch { XCTAssertEqual(error as? NativeMediaTransportError, .invalidResponse) }
        value["sharing"] = false
        value["sessionId"] = "forged-id"
        do { _ = try await backend.snapshot(binding, nonce: "nonce-1"); XCTFail("wrong session accepted") }
        catch { XCTAssertEqual(error as? NativeMediaTransportError, .invalidResponse) }
        value["sessionId"] = binding.sessionID
        value["slot"] = ["kind": "connected", "connectionId": "helper:opaque-id:1", "leaseVersion": 1]
        do { _ = try await backend.snapshot(binding, nonce: "nonce-1"); XCTFail("unverified participant accepted") }
        catch { XCTAssertEqual(error as? NativeMediaTransportError, .invalidResponse) }
    }

    func testInvalidSafeIntegerAndExpiredLeaseRejected() async throws {
        let identity = MediaTestIdentity()
        let backend = try makeBackend(identity)
        let binding = NativeMediaBinding(sessionID: "opaque-id", epoch: UUID())
        var value: [String: Any] = ["sessionId": binding.sessionID, "epoch": binding.epoch.uuidString,
                                    "status": "active", "authorizationVersion": 9_007_199_254_740_992 as NSNumber,
                                    "sharing": true, "cleanupPending": false,
                                    "media": ["kind": "unready"], "slot": ["kind": "empty"],
                                    "validForMs": 1000, "nonce": "nonce-1"]
        MediaURLProtocol.handler = { request in try mediaResponse(request, value: value) }
        do { _ = try await backend.snapshot(binding, nonce: "nonce-1"); XCTFail("unsafe version accepted") }
        catch { XCTAssertEqual(error as? NativeMediaTransportError, .invalidResponse) }
        value["authorizationVersion"] = 1
        value["validForMs"] = 0
        let expired = try await backend.snapshot(binding, nonce: "nonce-1")
        XCTAssertEqual(expired.validForMs, 0)
        MediaURLProtocol.handler = { request in
            try mediaResponse(request, value: ["scope": ["sessionId": binding.sessionID,
                                                         "sessionEpoch": binding.epoch.uuidString,
                                                         "authorizationVersion": 1,
                                                         "helper": NSNull()],
                                               "validForMs": 0, "nonce": "nonce-2"])
        }
        do { _ = try await backend.renew(binding, authorizationVersion: 1, helper: nil, nonce: "nonce-2"); XCTFail("zero grant accepted") }
        catch { XCTAssertEqual(error as? NativeMediaTransportError, .invalidResponse) }
    }

    func testAccountChangeDuringRequestInvalidatesResponseAndNotifiesImmediately() async throws {
        let identity = MediaTestIdentity()
        let backend = try makeBackend(identity)
        var notified = false
        backend.onIdentityInvalidated = { notified = true }
        identity.changeOnNextRead = true
        MediaURLProtocol.handler = { request in try mediaResponse(request, value: ["_id": "opaque-id", "epoch": UUID().uuidString]) }
        do { _ = try await backend.ensureAccount(); XCTFail("cross-account response accepted") }
        catch { XCTAssertEqual(error as? NativeMediaTransportError, .accountChanged) }
        XCTAssertTrue(notified)
    }

    func testRejectsInsecureEndpointAndRedirect() async throws {
        let identity = MediaTestIdentity()
        XCTAssertThrowsError(try ConvexNativeMediaBackend(origin: URL(string: "http://example.convex.cloud")!, identity: identity))
        let backend = try makeBackend(identity)
        MediaURLProtocol.handler = { request in
            (HTTPURLResponse(url: request.url!, statusCode: 302, httpVersion: nil,
                             headerFields: ["Location": "https://other.example/api/mutation"])!, Data())
        }
        do { try await backend.ensureAccount(); XCTFail("redirect accepted") }
        catch { XCTAssertEqual(error as? NativeMediaTransportError, .rejected) }
    }

    func testOversizedResponseAndRenewalScopeMismatchFailClosed() async throws {
        let identity = MediaTestIdentity()
        let backend = try makeBackend(identity)
        let binding = NativeMediaBinding(sessionID: "opaque-id", epoch: UUID())
        MediaURLProtocol.handler = { request in
            let body = Data(repeating: 65, count: 1_048_577)
            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!, body)
        }
        do { try await backend.heartbeat(binding); XCTFail("oversized response accepted") }
        catch { XCTAssertEqual(error as? NativeMediaTransportError, .responseTooLarge) }
        MediaURLProtocol.handler = { request in
            try mediaResponse(request, value: ["scope": ["sessionId": binding.sessionID,
                                                         "sessionEpoch": binding.epoch.uuidString,
                                                         "authorizationVersion": 2,
                                                         "helper": NSNull()],
                                               "validForMs": 10_000, "nonce": "nonce-3"])
        }
        do { _ = try await backend.renew(binding, authorizationVersion: 1, helper: nil, nonce: "nonce-3"); XCTFail("forged version accepted") }
        catch { XCTAssertEqual(error as? NativeMediaTransportError, .invalidResponse) }
    }

    func testOwnerTokenRejectsUnsafeMediaEndpoint() async throws {
        let identity = MediaTestIdentity()
        let backend = try makeBackend(identity)
        let binding = NativeMediaBinding(sessionID: "opaque-id", epoch: UUID())
        MediaURLProtocol.handler = { request in
            try mediaResponse(request, value: ["token": "header.payload.signature",
                                               "serverUrl": "wss://example.livekit.cloud/other?access=secret",
                                               "roomName": "room-A", "roomSid": "sid-A",
                                               "generation": 1, "identity": "owner-A",
                                               "expiresAt": Int64(Date().timeIntervalSince1970 * 1_000) + 30_000])
        }
        do { _ = try await backend.ownerToken(binding, clientInstanceID: UUID()); XCTFail("unsafe URL accepted") }
        catch { XCTAssertEqual(error as? NativeMediaTransportError, .invalidResponse) }
    }

    func testVisionUsesExistingAuthenticatedTransportAndCorrelatesFrame() async throws {
        let backend = try makeBackend(MediaTestIdentity())
        let screen = Targets.ScreenModel(frameId: "frame-1", capturedAtMs: 1234,
            panelType: .elevatorPanel, source: .visionModel, controls: [],
            evidence: .init(provider: "gemini", model: "test-only", live: true))
        let raw = try JSONSerialization.jsonObject(with: JSONEncoder().encode(screen))
        MediaURLProtocol.handler = { request in
            XCTAssertEqual(request.url?.path, "/api/action")
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer header.payload.signature")
            let body = try XCTUnwrap(JSONSerialization.jsonObject(with: requestBody(request)) as? [String: Any])
            XCTAssertEqual(body["path"] as? String, "vision:readScreen")
            let args = try XCTUnwrap((body["args"] as? [[String: Any]])?.first)
            XCTAssertEqual(args["frameId"] as? String, "frame-1")
            XCTAssertEqual(args["imageBase64"] as? String, Data([1, 2, 3]).base64EncodedString())
            return try mediaResponse(request, value: ["requestId": "request-1", "screenModel": raw])
        }
        let result = try await backend.readScreen(image: Data([1, 2, 3]), frameID: "frame-1",
            capturedAtUTCms: 1234, requestID: "request-1")
        XCTAssertEqual(result, screen)
        MediaURLProtocol.handler = { request in
            try mediaResponse(request, value: ["requestId": "request-1", "screenModel": raw])
        }
        do {
            _ = try await backend.readScreen(image: Data([1]), frameID: "frame-other",
                capturedAtUTCms: 1234, requestID: "request-1")
            XCTFail("Mismatched frame accepted")
        } catch { XCTAssertEqual(error as? NativeMediaTransportError, .invalidResponse) }
    }

    func testVisionRejectsOversizedImageBeforeSendingAndBoundsResponse() async throws {
        let backend = try makeBackend(MediaTestIdentity())
        MediaURLProtocol.handler = { request in
            XCTFail("Invalid image reached network")
            return try mediaResponse(request, value: [:])
        }
        do {
            _ = try await backend.readScreen(image: Data(repeating: 1, count: 512 * 1024 + 1),
                frameID: "frame-1", capturedAtUTCms: 1234, requestID: "request-1")
            XCTFail("Oversized image accepted")
        } catch { XCTAssertEqual(error as? NativeMediaTransportError, .invalidRequest) }
        MediaURLProtocol.handler = { request in
            (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
             Data(repeating: 65, count: 256 * 1024 + 1))
        }
        do {
            _ = try await backend.readScreen(image: Data([1]), frameID: "frame-1",
                capturedAtUTCms: 1234, requestID: "request-1")
            XCTFail("Oversized reply accepted")
        } catch { XCTAssertEqual(error as? NativeMediaTransportError, .responseTooLarge) }
    }

    private func objectReply() throws -> [String: Any] {
        // Synthetic transport fixture claiming live provenance to exercise validation only.
        let model = Targets.ScreenModel(frameId: "object-frame", capturedAtMs: 1234,
            panelType: .other, source: .visionModel, controls: [
                .init(id: "keys", label: "silver keys", type: .other,
                      region: .init(x: 0.2, y: 0.3, width: 0.1, height: 0.2), legibility: .clear)
            ], image: .init(widthPx: 640, heightPx: 480),
            evidence: .init(provider: "gemini", model: "test-only", live: true))
        return ["requestId": "object-request", "mode": "REAL_API", "status": "SELECTED", "selectedObjectId": "keys",
                "screenModel": try JSONSerialization.jsonObject(with: JSONEncoder().encode(model))]
    }

    func testQuestionClientRequestsDirectAnswerWithoutObjectSelection() async throws {
        let backend = try makeBackend(MediaTestIdentity())
        var value = try objectReply()
        value["status"] = "NOT_FOUND"; value["selectedObjectId"] = NSNull()
        value["answer"] = "A laptop and a bottle are on the table."
        var model = try XCTUnwrap(value["screenModel"] as? [String: Any])
        model["controls"] = []; value["screenModel"] = model
        let reply = value
        MediaURLProtocol.handler = { request in
            let body = try XCTUnwrap(JSONSerialization.jsonObject(with: requestBody(request)) as? [String: Any])
            let args = try XCTUnwrap((body["args"] as? [[String: Any]])?.first)
            XCTAssertEqual(args["answerQuestion"] as? Bool, true)
            XCTAssertEqual(args["query"] as? String, "What's in front of me?")
            return try mediaResponse(request, value: reply)
        }
        let result = try await ConvexObjectClient(backend: backend).answerQuestion(
            image: Data([1]), query: "What's in front of me?", frameID: "object-frame",
            capturedAtUTCms: 1234, requestID: "object-request")
        XCTAssertEqual(result.answer, "A laptop and a bottle are on the table.")
        XCTAssertNil(result.selectedObjectId)
        XCTAssertTrue(result.screenModel.controls.isEmpty)
    }

    func testObjectClientForwardsTrimmedQueryAndFrameThroughAuthenticatedAction() async throws {
        let backend = try makeBackend(MediaTestIdentity())
        let value = try objectReply()
        MediaURLProtocol.handler = { request in
            XCTAssertEqual(request.url?.path, "/api/action")
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer header.payload.signature")
            XCTAssertEqual(request.timeoutInterval, 60)
            let body = try XCTUnwrap(JSONSerialization.jsonObject(with: requestBody(request)) as? [String: Any])
            XCTAssertEqual(body["path"] as? String, "vision:findObject")
            let args = try XCTUnwrap((body["args"] as? [[String: Any]])?.first)
            XCTAssertEqual(args["query"] as? String, "find my silver keys")
            XCTAssertEqual(args["frameId"] as? String, "object-frame")
            XCTAssertEqual(args["capturedAtMs"] as? Int, 1234)
            XCTAssertEqual(args["requestId"] as? String, "object-request")
            XCTAssertEqual(args["imageBase64"] as? String, Data([1, 2, 3]).base64EncodedString())
            return try mediaResponse(request, value: value)
        }
        let result = try await ConvexObjectClient(backend: backend).findObject(
            image: Data([1, 2, 3]), query: "  find my silver keys  ", frameID: "object-frame",
            capturedAtUTCms: 1234, requestID: "object-request")
        XCTAssertEqual(result.status, .selected)
        XCTAssertEqual(result.selectedObjectId, "keys")
        XCTAssertEqual(result.screenModel.controls.first?.label, "silver keys")
        XCTAssertNil(result.screenModel.controls.first?.confidence)
        XCTAssertNil(result.screenModel.controls.first?.safety)
    }

    func testObjectRejectsInvalidQueriesImagesAndFrameScopeBeforeNetwork() async throws {
        let backend = try makeBackend(MediaTestIdentity())
        MediaURLProtocol.handler = { request in
            XCTFail("Invalid object read reached network")
            return try mediaResponse(request, value: [:])
        }
        let invalid: [(Data, String, String, Int64, String)] = [
            (Data([1]), "   ", "frame", 1, "request"),
            (Data([1]), String(repeating: "a", count: 501), "frame", 1, "request"),
            (Data([1]), String(repeating: "🔑", count: 251), "frame", 1, "request"),
            (Data([1]), "find\nkeys", "frame", 1, "request"),
            (Data(), "keys", "frame", 1, "request"),
            (Data(repeating: 1, count: 512 * 1024 + 1), "keys", "frame", 1, "request"),
            (Data([1]), "keys", "", 1, "request"),
            (Data([1]), "keys", "frame", -1, "request"),
            (Data([1]), "keys", "frame", 9_007_199_254_740_992, "request"),
            (Data([1]), "keys", "frame", 1, "")
        ]
        for (image, query, frame, time, request) in invalid {
            do {
                _ = try await backend.findObject(image: image, query: query, frameID: frame,
                    capturedAtUTCms: time, requestID: request)
                XCTFail("Invalid object request accepted")
            } catch { XCTAssertEqual(error as? NativeMediaTransportError, .invalidRequest) }
        }
    }

    func testObjectRejectsMismatchedAndSimulatedOrUnknownProviderResponses() async throws {
        let backend = try makeBackend(MediaTestIdentity())
        let valid = try objectReply()
        var invalid: [[String: Any]] = []
        for (key, replacement) in [("requestId", "other"), ("mode", "SIMULATED"), ("mode", "unknown")] {
            var value = valid
            value[key] = replacement
            invalid.append(value)
        }
        for (key, replacement) in [
            ("frameId", "other" as Any), ("capturedAtMs", 1235 as Any),
            ("schemaVersion", 2 as Any), ("source", "SIMULATED" as Any),
            ("evidence", ["provider": "gemini", "model": "test-only", "live": false] as Any),
            ("evidence", ["provider": "other", "model": "test-only", "live": true] as Any),
            ("evidence", NSNull() as Any)
        ] {
            var value = valid
            var model = try XCTUnwrap(valid["screenModel"] as? [String: Any])
            model[key] = replacement
            value["screenModel"] = model
            invalid.append(value)
        }
        for value in invalid {
            MediaURLProtocol.handler = { request in try mediaResponse(request, value: value) }
            do {
                _ = try await backend.findObject(image: Data([1]), query: "keys", frameID: "object-frame",
                    capturedAtUTCms: 1234, requestID: "object-request")
                XCTFail("Invalid object evidence accepted")
            } catch { XCTAssertEqual(error as? NativeMediaTransportError, .invalidResponse) }
        }
    }

    func testObjectSelectionStatusRequiresAnUnambiguousCorrelatedID() async throws {
        let backend = try makeBackend(MediaTestIdentity())
        let valid = try objectReply()
        var invalid: [[String: Any]] = []
        for (key, replacement) in [
            ("status", "selected" as Any), ("status", "UNRECOGNIZED" as Any),
            ("status", 1 as Any), ("status", NSNull() as Any),
            ("selectedObjectId", "missing" as Any), ("selectedObjectId", "" as Any),
            ("selectedObjectId", NSNull() as Any), ("selectedObjectId", 1 as Any),
            ("status", "NOT_FOUND" as Any), ("status", "AMBIGUOUS" as Any),
            ("status", "PROVIDER_UNAVAILABLE" as Any)
        ] {
            var value = valid
            value[key] = replacement
            invalid.append(value)
        }
        var duplicate = valid
        var model = try XCTUnwrap(valid["screenModel"] as? [String: Any])
        let controls = try XCTUnwrap(model["controls"] as? [[String: Any]])
        model["controls"] = controls + controls
        duplicate["screenModel"] = model
        invalid.append(duplicate)
        for value in invalid {
            MediaURLProtocol.handler = { request in try mediaResponse(request, value: value) }
            do {
                _ = try await backend.findObject(image: Data([1]), query: "what is this", frameID: "object-frame",
                    capturedAtUTCms: 1234, requestID: "object-request")
                XCTFail("Invalid semantic selection accepted")
            } catch { XCTAssertEqual(error as? NativeMediaTransportError, .invalidResponse) }
        }
        for status in [GeminiObjectRecognition.Status.notFound, .ambiguous, .providerUnavailable] {
            var value = valid
            value["status"] = status.rawValue
            value["selectedObjectId"] = NSNull()
            MediaURLProtocol.handler = { request in try mediaResponse(request, value: value) }
            let result = try await backend.findObject(image: Data([1]), query: "what is this", frameID: "object-frame",
                capturedAtUTCms: 1234, requestID: "object-request")
            XCTAssertEqual(result.status, status)
            XCTAssertNil(result.selectedObjectId)
        }
    }

    func testObjectRejectsMissingAuthenticationAndAccountSwitch() async throws {
        let identity = MediaTestIdentity()
        identity.credentials = NativeMediaCredentials(jwt: "", accountID: "", identityEpoch: UUID())
        let backend = try makeBackend(identity)
        MediaURLProtocol.handler = { request in
            XCTFail("Unauthenticated object request reached network")
            return try mediaResponse(request, value: [:])
        }
        do {
            _ = try await backend.findObject(image: Data([1]), query: "keys", frameID: "object-frame",
                capturedAtUTCms: 1234, requestID: "object-request")
            XCTFail("Missing authentication accepted")
        } catch { XCTAssertEqual(error as? NativeMediaTransportError, .unauthenticated) }

        let changedIdentity = MediaTestIdentity()
        changedIdentity.changeOnNextRead = true
        let changedBackend = try makeBackend(changedIdentity)
        var invalidated = false
        changedBackend.onIdentityInvalidated = { invalidated = true }
        let value = try objectReply()
        MediaURLProtocol.handler = { request in try mediaResponse(request, value: value) }
        do {
            _ = try await changedBackend.findObject(image: Data([1]), query: "keys", frameID: "object-frame",
                capturedAtUTCms: 1234, requestID: "object-request")
            XCTFail("Cross-account object evidence accepted")
        } catch { XCTAssertEqual(error as? NativeMediaTransportError, .accountChanged) }
        XCTAssertTrue(invalidated)
    }

    func testObjectBoundsResponseAndSurfacesProviderTimeout() async throws {
        let backend = try makeBackend(MediaTestIdentity())
        MediaURLProtocol.handler = { request in
            (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
             Data(repeating: 65, count: 256 * 1024 + 1))
        }
        do {
            _ = try await backend.findObject(image: Data([1]), query: "keys", frameID: "object-frame",
                capturedAtUTCms: 1234, requestID: "object-request")
            XCTFail("Oversized object reply accepted")
        } catch { XCTAssertEqual(error as? NativeMediaTransportError, .responseTooLarge) }
        MediaURLProtocol.handler = { _ in throw URLError(.timedOut) }
        do {
            _ = try await backend.findObject(image: Data([1]), query: "keys", frameID: "object-frame",
                capturedAtUTCms: 1234, requestID: "object-request")
            XCTFail("Timed out object reply accepted")
        } catch { XCTAssertEqual(error as? NativeMediaTransportError, .unavailable) }
    }

    func testCancelledObjectReadDoesNotStartNetwork() async throws {
        let backend = try makeBackend(MediaTestIdentity())
        MediaURLProtocol.handler = { request in
            XCTFail("Cancelled object read reached network")
            return try mediaResponse(request, value: [:])
        }
        let task = Task {
            try await backend.findObject(image: Data([1]), query: "keys", frameID: "object-frame",
                capturedAtUTCms: 1234, requestID: "object-request")
        }
        task.cancel()
        do {
            _ = try await task.value
            XCTFail("Cancelled object read completed")
        } catch { XCTAssertTrue(error is CancellationError) }
    }

    func testObjectDiscardsResponseCancelledDuringCredentialRecheck() async throws {
        let identity = MediaTestIdentity()
        let backend = try makeBackend(identity)
        let value = try objectReply()
        MediaURLProtocol.handler = { request in try mediaResponse(request, value: value) }
        var task: Task<GeminiObjectRecognition, Error>?
        identity.onSecondRead = { task?.cancel() }
        task = Task {
            try await backend.findObject(image: Data([1]), query: "keys", frameID: "object-frame",
                capturedAtUTCms: 1234, requestID: "object-request")
        }
        do {
            _ = try await task!.value
            XCTFail("Cancelled in-flight object evidence accepted")
        } catch { XCTAssertTrue(error is CancellationError) }
    }

    private func speechWav() -> Data {
        // SIMULATED one-sample mono PCM fixture, never sent to a provider.
        Data([82, 73, 70, 70, 38, 0, 0, 0, 87, 65, 86, 69,
              102, 109, 116, 32, 16, 0, 0, 0, 1, 0, 1, 0,
              192, 93, 0, 0, 128, 187, 0, 0, 2, 0, 16, 0,
              100, 97, 116, 97, 2, 0, 0, 0, 0, 0])
    }

    private func speechReply() -> [String: Any] {
        ["ok": true, "requestId": "speech-1", "mode": "REAL_API", "format": "wav",
         "sampleRate": 24_000, "audioBase64": speechWav().base64EncodedString()]
    }

    func testSpeechUsesAuthenticatedActionAndReturnsCorrelatedWav() async throws {
        let backend = try makeBackend(MediaTestIdentity())
        let value = speechReply()
        MediaURLProtocol.handler = { request in
            XCTAssertEqual(request.url?.path, "/api/action")
            XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer header.payload.signature")
            XCTAssertEqual(request.timeoutInterval, 5)
            let body = try XCTUnwrap(JSONSerialization.jsonObject(with: requestBody(request)) as? [String: Any])
            XCTAssertEqual(body["path"] as? String, "speech:synthesize")
            let args = try XCTUnwrap((body["args"] as? [[String: Any]])?.first)
            XCTAssertEqual(args["requestId"] as? String, "speech-1")
            XCTAssertEqual(args["text"] as? String, "Read the options")
            return try mediaResponse(request, value: value)
        }
        let audio = try await ConvexSpeechClient(backend: backend).synthesize(
            text: "Read the options", requestID: "speech-1")
        XCTAssertEqual(audio, speechWav())
    }

    func testSpeechRequestsLocalFallbackForProviderErrorAndSimulatedSilence() async throws {
        let backend = try makeBackend(MediaTestIdentity())
        var simulated = speechReply()
        simulated["mode"] = "SIMULATED"
        let failed: [String: Any] = ["ok": false, "requestId": "speech-1", "errorCode": "PROVIDER_TIMEOUT"]
        for value in [simulated, failed] {
            MediaURLProtocol.handler = { request in try mediaResponse(request, value: value) }
            let audio = try await backend.synthesizeSpeech(text: "Read the options", requestID: "speech-1")
            XCTAssertNil(audio)
        }
    }

    func testSpeechRejectsMismatchedMalformedAndUnsupportedResponses() async throws {
        let backend = try makeBackend(MediaTestIdentity())
        for (field, invalid) in [("requestId", "other" as Any), ("ok", 1), ("mode", "UNKNOWN"),
                                  ("format", "mp3"), ("sampleRate", 44_100), ("sampleRate", true),
                                  ("audioBase64", "not base64!"),
                                  ("audioBase64", Data(repeating: 0, count: 60).base64EncodedString()),
                                  ("audioBase64", speechWav().base64EncodedString() + "\n")] {
            var value = speechReply()
            value[field] = invalid
            MediaURLProtocol.handler = { request in try mediaResponse(request, value: value) }
            do {
                _ = try await backend.synthesizeSpeech(text: "Options", requestID: "speech-1")
                XCTFail("Invalid \(field) accepted")
            } catch { XCTAssertEqual(error as? NativeMediaTransportError, .invalidResponse) }
        }
    }

    func testSpeechKeepsLongOrInvalidTextLocalWithoutTruncationOrNetwork() async throws {
        let backend = try makeBackend(MediaTestIdentity())
        MediaURLProtocol.handler = { request in
            XCTFail("Invalid text reached network")
            return try mediaResponse(request, value: [:])
        }
        for text in ["   ", String(repeating: "x", count: 301),
                     String(repeating: "😀", count: 151), "invalid\u{0000}text"] {
            let audio = try await backend.synthesizeSpeech(text: text, requestID: "speech-1")
            XCTAssertNil(audio)
        }
    }

    func testSpeechRejectsMissingAuthenticationAndAccountChange() async throws {
        let identity = MediaTestIdentity()
        let backend = try makeBackend(identity)
        let original = identity.credentials
        identity.credentials = NativeMediaCredentials(jwt: "", accountID: "", identityEpoch: UUID())
        MediaURLProtocol.handler = { request in
            XCTFail("Unauthenticated request reached network")
            return try mediaResponse(request, value: [:])
        }
        do {
            _ = try await backend.synthesizeSpeech(text: "Options", requestID: "speech-1")
            XCTFail("Missing authentication accepted")
        } catch { XCTAssertEqual(error as? NativeMediaTransportError, .unauthenticated) }

        identity.credentials = original
        let changedIdentity = MediaTestIdentity()
        changedIdentity.changeOnNextRead = true
        let changedBackend = try makeBackend(changedIdentity)
        let value = speechReply()
        MediaURLProtocol.handler = { request in try mediaResponse(request, value: value) }
        do {
            _ = try await changedBackend.synthesizeSpeech(text: "Options", requestID: "speech-1")
            XCTFail("Cross-account speech accepted")
        } catch { XCTAssertEqual(error as? NativeMediaTransportError, .accountChanged) }
    }

    func testSpeechBoundsResponseAndDecodedAudio() async throws {
        let backend = try makeBackend(MediaTestIdentity())
        MediaURLProtocol.handler = { request in
            let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil,
                                           headerFields: ["Content-Length": "6291457"])!
            return (response, Data())
        }
        do {
            _ = try await backend.synthesizeSpeech(text: "Options", requestID: "speech-1")
            XCTFail("Oversized response accepted")
        } catch { XCTAssertEqual(error as? NativeMediaTransportError, .responseTooLarge) }
        var value = speechReply()
        var oversized = speechWav()
        oversized.append(Data(repeating: 0, count: 4 * 1024 * 1024 + 1 - oversized.count))
        value["audioBase64"] = oversized.base64EncodedString()
        MediaURLProtocol.handler = { request in try mediaResponse(request, value: value) }
        do {
            _ = try await backend.synthesizeSpeech(text: "Options", requestID: "speech-1")
            XCTFail("Oversized audio accepted")
        } catch { XCTAssertEqual(error as? NativeMediaTransportError, .invalidResponse) }
    }

    func testSpeechSurfacesHttpAndNetworkFailuresForLocalFallback() async throws {
        let backend = try makeBackend(MediaTestIdentity())
        MediaURLProtocol.handler = { request in try mediaResponse(request, value: [:], status: 401) }
        do {
            _ = try await backend.synthesizeSpeech(text: "Options", requestID: "speech-1")
            XCTFail("HTTP failure accepted")
        } catch { XCTAssertEqual(error as? NativeMediaTransportError, .rejected) }
        MediaURLProtocol.handler = { _ in throw URLError(.timedOut) }
        do {
            _ = try await backend.synthesizeSpeech(text: "Options", requestID: "speech-1")
            XCTFail("Network failure accepted")
        } catch { XCTAssertEqual(error as? NativeMediaTransportError, .unavailable) }
    }

    func testCancelledSpeechDoesNotStartHttpRequest() async throws {
        let backend = try makeBackend(MediaTestIdentity())
        MediaURLProtocol.handler = { request in
            XCTFail("Cancelled speech reached network")
            return try mediaResponse(request, value: [:])
        }
        let task = Task { try await backend.synthesizeSpeech(text: "Options", requestID: "speech-1") }
        task.cancel()
        do {
            _ = try await task.value
            XCTFail("Cancelled speech completed")
        } catch { XCTAssertTrue(error is CancellationError) }
    }
}
