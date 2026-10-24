import Foundation
import XCTest
import Targets
@testable import Reflex

@MainActor
private final class MediaTestIdentity: NativeMediaIdentityProviding {
    var onIdentityInvalidated: (() -> Void)?
    var credentials = NativeMediaCredentials(jwt: "header.payload.signature", accountID: "owner-A", identityEpoch: UUID())
    var changeOnNextRead = false
    private var readCount = 0
    func currentCredentials() async throws -> NativeMediaCredentials {
        readCount += 1
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
}
