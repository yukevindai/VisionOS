import Foundation
import XCTest
@testable import VisionOS

@MainActor
private final class HelperTestIdentity: NativeMediaIdentityProviding {
    var onIdentityInvalidated: (() -> Void)?
    func currentCredentials() async throws -> NativeMediaCredentials {
        NativeMediaCredentials(jwt: "header.payload.signature", accountID: "helper-A", identityEpoch: epoch)
    }
    private let epoch = UUID()
}

private final class HelperURLProtocol: URLProtocol {
    static var handler: ((URLRequest) throws -> Any)?
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        do {
            let value = try Self.handler!(request)
            let data = try JSONSerialization.data(withJSONObject: ["status": "success", "value": value])
            let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch { client?.urlProtocol(self, didFailWithError: error) }
    }
    override func stopLoading() {}
}

@MainActor
final class NativeHelperTests: XCTestCase {
    private let epoch = "17b44c37-1541-4bc8-9ac8-9e0d63c92a3d"
    private func backend() throws -> ConvexNativeMediaBackend {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [HelperURLProtocol.self]
        return try ConvexNativeMediaBackend(origin: URL(string: "https://example.convex.cloud")!,
            identity: HelperTestIdentity(), session: URLSession(configuration: configuration))
    }
    private var connection: NativeHelperConnection {
        NativeHelperConnection(session: NativeHelperSession(sessionID: "session-1", epoch: epoch, ownerName: "Alex"),
                               connectionID: "helper:session-1:1", leaseVersion: 1)
    }

    func testEmptyDiscoveryPagePreservesNextCursor() async throws {
        let client = try backend()
        HelperURLProtocol.handler = { _ in ["page": [], "isDone": false, "continueCursor": "next-page"] }
        let page = try await client.helperSessions()
        XCTAssertTrue(page.sessions.isEmpty)
        XCTAssertEqual(page.nextCursor, "next-page")
    }

    func testDiscoveryKeepsExactServerEpochAndNames() async throws {
        let client = try backend()
        let expectedEpoch = epoch
        HelperURLProtocol.handler = { _ in
            ["page": [["sessionId": "session-1", "epoch": expectedEpoch, "ownerDisplayName": "Alex Chen"]],
             "isDone": true, "continueCursor": ""]
        }
        let page = try await client.helperSessions()
        XCTAssertEqual(page.sessions.first?.epoch, expectedEpoch)
        XCTAssertEqual(page.sessions.first?.ownerName, "Alex Chen")
        XCTAssertNil(page.nextCursor)
    }

    func testMalformedDiscoveryCannotSupplyRows() async throws {
        let client = try backend()
        HelperURLProtocol.handler = { _ in
            ["page": [["sessionId": "session-1", "epoch": "invalid", "ownerDisplayName": "Alex"]],
             "isDone": true, "continueCursor": ""]
        }
        do { _ = try await client.helperSessions(); XCTFail("Malformed epoch was accepted") }
        catch { XCTAssertEqual(error as? NativeMediaTransportError, .invalidResponse) }
    }

    func testWrongStatusNonceDoesNotGrantAccess() async throws {
        let client = try backend()
        HelperURLProtocol.handler = { _ in ["state": "closed", "nonce": "wrong", "validForMs": 0] }
        do { _ = try await client.helperStatus(connection, nonce: "expected"); XCTFail("Wrong nonce was accepted") }
        catch { XCTAssertEqual(error as? NativeMediaTransportError, .invalidResponse) }
    }

    func testClosedStatusReturnsNoAuthority() async throws {
        let client = try backend()
        HelperURLProtocol.handler = { _ in ["state": "closed", "nonce": "expected", "validForMs": 0] }
        let status = try await client.helperStatus(connection, nonce: "expected")
        XCTAssertNil(status)
    }

    func testMediaGrantRequiresExactRoomAndHelperSID() {
        let room = NativeMediaRoom(roomName: "room-a", roomSID: "RM_A", generation: 2)
        let token = NativeMediaToken(token: "header.payload.signature", serverURL: URL(string: "wss://example.test")!,
                                     room: room, identity: "helper:session-1:1", expiresAtMs: 9_000)
        let status = NativeHelperStatus(state: .connected, room: room, ownerIdentity: "owner:a", ownerSID: "PA_A",
                                       helperSID: "PA_HELPER", authorizationVersion: 3, validForMs: 10_000)
        XCTAssertTrue(status.matches(token: token, localSID: "PA_HELPER"))
        XCTAssertFalse(status.matches(token: token, localSID: "PA_OTHER"))
        let changedRoom = NativeMediaToken(token: token.token, serverURL: token.serverURL,
            room: NativeMediaRoom(roomName: "room-a", roomSID: "RM_A", generation: 3), identity: token.identity, expiresAtMs: token.expiresAtMs)
        XCTAssertFalse(status.matches(token: changedRoom, localSID: "PA_HELPER"))
        let expired = NativeHelperStatus(state: .connected, room: room, ownerIdentity: "owner:a", ownerSID: "PA_A",
                                        helperSID: "PA_HELPER", authorizationVersion: 3, validForMs: 0)
        XCTAssertFalse(expired.matches(token: token, localSID: "PA_HELPER"))
    }
}
