import CoreFoundation
import Foundation
import Targets

/// Only safe, local error categories cross this boundary. Convex errorData and errorMessage
/// can contain backend detail and are intentionally never surfaced or logged here.
enum NativeMediaTransportError: Error, Equatable {
    case insecureEndpoint, unauthenticated, accountChanged, invalidRequest
    case unavailable, rejected, invalidResponse, responseTooLarge
}

struct NativeOwnerInvite: Equatable {
    let code: String
    let expiresAtMs: Int64
}

struct NativePendingFriendRequest: Identifiable, Equatable {
    let id: String
    let requesterID: String
    let displayName: String
    let expiresAtMs: Int64
}

struct NativePendingFriendPage: Equatable {
    let requests: [NativePendingFriendRequest]
    let continueCursor: String?
}

private final class NoRedirects: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}

@MainActor
final class ConvexNativeMediaBackend: NativeMediaBackend, R3ScreenReading, R3ObjectReading {
    var onIdentityInvalidated: (() -> Void)?
    private let origin: URL
    private let identity: any NativeMediaIdentityProviding
    private let session: URLSession
    private let objectSession: URLSession
    private let redirects = NoRedirects()
    private static let maxSafeInteger: UInt64 = 9_007_199_254_740_991

    init(origin: URL, identity: any NativeMediaIdentityProviding, session: URLSession? = nil) throws {
        guard origin.scheme?.lowercased() == "https", origin.host != nil,
              origin.user == nil, origin.password == nil,
              origin.query == nil, origin.fragment == nil,
              origin.path.isEmpty || origin.path == "/" else {
            throw NativeMediaTransportError.insecureEndpoint
        }
        self.origin = origin
        self.identity = identity
        if let session {
            self.session = session
            self.objectSession = session
        } else {
            let config = URLSessionConfiguration.ephemeral
            config.requestCachePolicy = .reloadIgnoringLocalAndRemoteCacheData
            config.urlCache = nil
            config.httpCookieStorage = nil
            config.httpShouldSetCookies = false
            config.timeoutIntervalForRequest = 8
            config.timeoutIntervalForResource = 8
            self.session = URLSession(configuration: config)
            config.timeoutIntervalForRequest = 60
            config.timeoutIntervalForResource = 60
            self.objectSession = URLSession(configuration: config)
        }
        identity.onIdentityInvalidated = { [weak self] in self?.onIdentityInvalidated?() }
    }

    func ensureAccount() async throws {
        let user = try object(try await call("users:ensure", kind: "mutation", args: [:]))
        _ = try nonemptyString(user, "_id")
    }

    func synthesizeSpeech(text: String, requestID: String) async throws -> Data? {
        try Task.checkCancellation()
        let text = text.trimmingCharacters(in: .whitespacesAndNewlines)
        // Match the provider's JavaScript length limit without truncating the spoken text.
        guard !text.isEmpty, text.utf16.count <= 300,
              !text.unicodeScalars.contains(where: {
                  $0.value <= 8 || (11...31).contains($0.value) || $0.value == 127
              }) else { return nil }
        try validateNonce(requestID)
        let result = try object(try await call("speech:synthesize", kind: "action",
            args: ["requestId": requestID, "text": text],
            maximumResponseBytes: 6 * 1024 * 1024, requestTimeout: 5))
        try Task.checkCancellation()
        guard try string(result, "requestId") == requestID else {
            throw NativeMediaTransportError.invalidResponse
        }
        guard try bool(result, "ok") else { return nil }
        let mode = try string(result, "mode")
        guard mode != "SIMULATED" else { return nil }
        guard mode == "REAL_API", try string(result, "format") == "wav",
              try safeUInt(result, "sampleRate") == 24_000 else {
            throw NativeMediaTransportError.invalidResponse
        }
        let encoded = try string(result, "audioBase64")
        let maxAudioBytes = 4 * 1024 * 1024
        guard encoded.utf8.count <= ((maxAudioBytes + 2) / 3) * 4,
              let audio = Data(base64Encoded: encoded),
              audio.count > 44, audio.count <= maxAudioBytes,
              audio.base64EncodedString() == encoded,
              audio.prefix(4) == Data("RIFF".utf8),
              audio.subdata(in: 8..<12) == Data("WAVE".utf8) else {
            throw NativeMediaTransportError.invalidResponse
        }
        return audio
    }

    func readScreen(image: Data, frameID: String, capturedAtUTCms: Int64,
                    requestID: String) async throws -> Targets.ScreenModel {
        guard !image.isEmpty, image.count <= 512 * 1024,
              capturedAtUTCms >= 0, capturedAtUTCms <= Int64(Self.maxSafeInteger) else {
            throw NativeMediaTransportError.invalidRequest
        }
        try validateNonce(frameID)
        try validateNonce(requestID)
        try Task.checkCancellation()
        let result = try object(try await call("vision:readScreen", kind: "action", args: [
            "imageBase64": image.base64EncodedString(), "frameId": frameID,
            "capturedAtMs": capturedAtUTCms, "requestId": requestID
        ], maximumResponseBytes: 256 * 1024))
        try Task.checkCancellation()
        guard try string(result, "requestId") == requestID,
              let raw = result["screenModel"], JSONSerialization.isValidJSONObject(raw) else {
            throw NativeMediaTransportError.invalidResponse
        }
        let model: Targets.ScreenModel
        do {
            model = try JSONDecoder().decode(Targets.ScreenModel.self,
                from: JSONSerialization.data(withJSONObject: raw))
        } catch { throw NativeMediaTransportError.invalidResponse }
        guard model.schemaVersion == 3, model.frameId == frameID,
              model.capturedAtMs == capturedAtUTCms else {
            throw NativeMediaTransportError.invalidResponse
        }
        return model
    }

    func findObject(image: Data, query: String, frameID: String,
                    capturedAtUTCms: Int64, requestID: String) async throws -> GeminiObjectRecognition {
        try await findObject(image: image, query: query, frameID: frameID,
                             capturedAtUTCms: capturedAtUTCms, requestID: requestID, answerQuestion: false)
    }

    func findObject(image: Data, query: String, frameID: String,
                    capturedAtUTCms: Int64, requestID: String, answerQuestion: Bool) async throws -> GeminiObjectRecognition {
        try Task.checkCancellation()
        let query = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty, query.utf16.count <= 500,
              !query.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }),
              !image.isEmpty, image.count <= 512 * 1024,
              capturedAtUTCms >= 0, capturedAtUTCms <= Int64(Self.maxSafeInteger) else {
            throw NativeMediaTransportError.invalidRequest
        }
        try validateNonce(frameID)
        try validateNonce(requestID)
        let rawResult: Any
        do {
            rawResult = try await call("vision:findObject", kind: "action", args: [
                "imageBase64": image.base64EncodedString(), "query": query, "answerQuestion": answerQuestion,
                "frameId": frameID, "capturedAtMs": capturedAtUTCms, "requestId": requestID
            ], maximumResponseBytes: 256 * 1024, requestTimeout: 60)
        } catch {
            try Task.checkCancellation()
            throw error
        }
        try Task.checkCancellation()
        let result = try object(rawResult)
        guard try string(result, "requestId") == requestID,
              try string(result, "mode") == "REAL_API",
              let status = GeminiObjectRecognition.Status(rawValue: try string(result, "status")),
              let raw = result["screenModel"], JSONSerialization.isValidJSONObject(raw) else {
            throw NativeMediaTransportError.invalidResponse
        }
        let model: Targets.ScreenModel
        do {
            model = try JSONDecoder().decode(Targets.ScreenModel.self,
                from: JSONSerialization.data(withJSONObject: raw))
        } catch { throw NativeMediaTransportError.invalidResponse }
        guard model.schemaVersion == 3, model.frameId == frameID,
              model.capturedAtMs == capturedAtUTCms, model.source == .visionModel,
              model.evidence?.live == true, model.evidence?.provider == "gemini" else {
            throw NativeMediaTransportError.invalidResponse
        }
        let selectedObjectId: String?
        if status == .selected {
            let id = try string(result, "selectedObjectId")
            guard !id.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                  model.controls.filter({ $0.id == id }).count == 1 else {
                throw NativeMediaTransportError.invalidResponse
            }
            selectedObjectId = id
        } else {
            guard result["selectedObjectId"] == nil || result["selectedObjectId"] is NSNull else {
                throw NativeMediaTransportError.invalidResponse
            }
            selectedObjectId = nil
        }
        let answer = result["answer"] as? String
        if answerQuestion {
            guard status == .notFound, selectedObjectId == nil, model.controls.isEmpty,
                  let answer, !answer.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                  answer.utf16.count <= 2_000,
                  !answer.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }) else {
                throw NativeMediaTransportError.invalidResponse
            }
        }
        return GeminiObjectRecognition(status: status, selectedObjectId: selectedObjectId, screenModel: model, answer: answer)
    }

    func createInvite() async throws -> NativeOwnerInvite {
        // The backend permits 5 minutes through 7 days. A short code minimizes the
        // window during which a forwarded invitation can request owner approval.
        let data = try object(try await call("invites:createInvite", kind: "action",
                                         args: ["lifetimeMs": 30 * 60 * 1_000]))
        let code = try string(data, "code")
        guard code.count == 43, code.utf8.allSatisfy({ byte in
            (byte >= 65 && byte <= 90) || (byte >= 97 && byte <= 122) ||
            (byte >= 48 && byte <= 57) || byte == 45 || byte == 95
        }) else { throw NativeMediaTransportError.invalidResponse }
        _ = try nonemptyString(data, "inviteId")
        return NativeOwnerInvite(code: code, expiresAtMs: try positiveMs(data, "expiresAt"))
    }

    func pendingFriendRequests(cursor: String? = nil) async throws -> NativePendingFriendPage {
        guard cursor == nil || (cursor!.count <= 16_384 && !cursor!.isEmpty) else {
            throw NativeMediaTransportError.invalidRequest
        }
        let value = try await call("friends:pendingInvites", kind: "query",
                                   args: ["paginationOpts": ["numItems": 20, "cursor": cursor as Any? ?? NSNull()]])
        let data = try object(value)
        guard let page = data["page"] as? [Any], page.count <= 20 else {
            throw NativeMediaTransportError.invalidResponse
        }
        let isDone = try bool(data, "isDone")
        let next = try string(data, "continueCursor")
        guard next.count <= 16_384 else { throw NativeMediaTransportError.invalidResponse }
        let requests = try page.compactMap { raw -> NativePendingFriendRequest? in
            let item = try object(raw)
            // An account deleted between pagination and projection is not approvable.
            guard let requesterRaw = item["requester"], !(requesterRaw is NSNull) else { return nil }
            let requester = try object(requesterRaw)
            let displayName = try string(requester, "displayName")
            guard !displayName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                  displayName.count <= 160,
                  !displayName.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }) else {
                throw NativeMediaTransportError.invalidResponse
            }
            return NativePendingFriendRequest(id: try nonemptyString(item, "inviteId"),
                                              requesterID: try nonemptyString(requester, "id"),
                                              displayName: displayName,
                                              expiresAtMs: try positiveMs(item, "expiresAt"))
        }
        return NativePendingFriendPage(requests: requests, continueCursor: isDone ? nil : next)
    }

    func approveFriendRequest(_ request: NativePendingFriendRequest) async throws {
        let data = try object(try await call("friends:approveInvite", kind: "mutation",
                                         args: ["inviteId": request.id]))
        guard try nonemptyString(data, "helperId") == request.requesterID else {
            throw NativeMediaTransportError.invalidResponse
        }
    }

    func start(epoch: UUID) async throws -> NativeMediaBinding {
        let value = try await call("sessions:start", kind: "mutation", args: ["epoch": epoch.uuidString])
        let object = try object(value)
        let id = try nonemptyString(object, "_id")
        let returnedEpoch = try uuid(object, "epoch")
        guard returnedEpoch == epoch else { throw NativeMediaTransportError.invalidResponse }
        return NativeMediaBinding(sessionID: id, epoch: epoch)
    }

    func snapshot(_ binding: NativeMediaBinding, nonce: String) async throws -> NativeMediaSnapshot {
        try validateNonce(nonce)
        let value = try await call("sessions:mediaStatus", kind: "query", args: (try scope(binding)).merging(["nonce": nonce]) { _, new in new })
        let data = try object(value)
        let id = try nonemptyString(data, "sessionId")
        let epoch = try uuid(data, "epoch")
        let echoedNonce = try nonemptyString(data, "nonce")
        guard id == binding.sessionID, epoch == binding.epoch, echoedNonce == nonce else {
            throw NativeMediaTransportError.invalidResponse
        }
        let status: NativeMediaStatus
        switch try string(data, "status") {
        case "active": status = .active
        case "ended": status = .ended
        default: throw NativeMediaTransportError.invalidResponse
        }
        let mediaData = try object(data["media"])
        let media: NativeMediaState
        switch try string(mediaData, "kind") {
        case "unready": media = .unready
        case "provisioning": media = .provisioning(roomName: try nonemptyString(mediaData, "roomId"), generation: try safeUInt(mediaData, "generation"))
        case "ready": media = .ready(roomName: try nonemptyString(mediaData, "roomId"), generation: try safeUInt(mediaData, "generation"),
                                      roomSID: try nonemptyString(mediaData, "roomSid"), ownerConnected: try bool(mediaData, "ownerConnected"))
        case "retired": media = .retired(roomName: try nonemptyString(mediaData, "roomId"), generation: try safeUInt(mediaData, "generation"))
        default: throw NativeMediaTransportError.invalidResponse
        }
        let slot = try object(data["slot"])
        let helper: NativeMediaHelper
        switch try string(slot, "kind") {
        case "empty": helper = .empty
        case "reserved": helper = .reserved(connectionID: try nonemptyString(slot, "connectionId"), leaseVersion: try safeUInt(slot, "leaseVersion"))
        case "connected": helper = .connected(connectionID: try nonemptyString(slot, "connectionId"),
                                               leaseVersion: try safeUInt(slot, "leaseVersion"),
                                               participantSID: try nonemptyString(slot, "participantSid"))
        default: throw NativeMediaTransportError.invalidResponse
        }
        let validForMs = try nonnegativeMs(data, "validForMs")
        guard validForMs <= 20_000 else { throw NativeMediaTransportError.invalidResponse }
        return NativeMediaSnapshot(sessionID: id, epoch: epoch, status: status,
                                   authorizationVersion: try safeUInt(data, "authorizationVersion"),
                                   sharing: try bool(data, "sharing"), cleanupPending: try bool(data, "cleanupPending"),
                                   media: media, helper: helper, validForMs: validForMs, nonce: echoedNonce)
    }

    func heartbeat(_ binding: NativeMediaBinding) async throws {
        _ = try await call("sessions:heartbeat", kind: "mutation", args: scope(binding))
    }

    func provision(_ binding: NativeMediaBinding) async throws -> NativeMediaRoom {
        try room(try await call("livekit:provisionRoom", kind: "action", args: scope(binding)))
    }

    func ownerToken(_ binding: NativeMediaBinding, clientInstanceID: UUID) async throws -> NativeMediaToken {
        let value = try await call("livekit:issueOwnerToken", kind: "action",
                                   args: (try scope(binding)).merging(["clientInstanceId": clientInstanceID.uuidString]) { _, new in new })
        let data = try object(value)
        let room = try room(value)
        let token = try jwtString(data, "token")
        let serverURLString = try nonemptyString(data, "serverUrl")
        guard let serverURL = URL(string: serverURLString),
              ["wss", "https"].contains(serverURL.scheme?.lowercased() ?? ""),
              serverURL.host != nil, serverURL.user == nil, serverURL.password == nil,
              serverURL.query == nil, serverURL.fragment == nil,
              serverURL.path.isEmpty || serverURL.path == "/" else {
            throw NativeMediaTransportError.invalidResponse
        }
        let expiresAtMs = try positiveMs(data, "expiresAt")
        guard expiresAtMs > Int64(Date().timeIntervalSince1970 * 1_000) else {
            throw NativeMediaTransportError.invalidResponse
        }
        return NativeMediaToken(token: token, serverURL: serverURL, room: room,
                                identity: try nonemptyString(data, "identity"), expiresAtMs: expiresAtMs)
    }

    func renew(_ binding: NativeMediaBinding, authorizationVersion: UInt64,
               helper: NativeMediaHelperScope?, nonce: String) async throws -> NativeMediaRenewal {
        try validateNonce(nonce)
        guard authorizationVersion <= Self.maxSafeInteger else { throw NativeMediaTransportError.invalidRequest }
        var args = try scope(binding)
        args["authorizationVersion"] = authorizationVersion
        if let helper {
            guard !helper.connectionID.isEmpty, helper.leaseVersion <= Self.maxSafeInteger else {
                throw NativeMediaTransportError.invalidRequest
            }
            args["helper"] = ["connectionId": helper.connectionID, "leaseVersion": helper.leaseVersion]
        } else {
            args["helper"] = NSNull()
        }
        args["nonce"] = nonce
        let value = try await call("assistance:renewAuthorization", kind: "mutation", args: args)
        let data = try object(value)
        let returnedScope = try object(data["scope"])
        guard try nonemptyString(returnedScope, "sessionId") == binding.sessionID,
              try uuid(returnedScope, "sessionEpoch") == binding.epoch,
              try safeUInt(returnedScope, "authorizationVersion") == authorizationVersion,
              try nonemptyString(data, "nonce") == nonce else { throw NativeMediaTransportError.invalidResponse }
        let returnedHelper: NativeMediaHelperScope?
        if returnedScope["helper"] is NSNull {
            returnedHelper = nil
        } else {
            let rawHelper = try object(returnedScope["helper"])
            returnedHelper = NativeMediaHelperScope(connectionID: try nonemptyString(rawHelper, "connectionId"),
                                                    leaseVersion: try safeUInt(rawHelper, "leaseVersion"))
        }
        guard returnedHelper == helper else { throw NativeMediaTransportError.invalidResponse }
        let validForMs = try positiveMs(data, "validForMs")
        guard validForMs <= 15_000 else { throw NativeMediaTransportError.invalidResponse }
        return NativeMediaRenewal(binding: binding, authorizationVersion: authorizationVersion,
                                  helper: helper, validForMs: validForMs, nonce: nonce)
    }

    func recover(_ binding: NativeMediaBinding) async throws {
        _ = try await call("mediaState:recover", kind: "mutation", args: scope(binding))
    }

    func end(_ binding: NativeMediaBinding) async throws {
        _ = try await call("sessions:end", kind: "mutation", args: scope(binding))
    }

    func setSharing(_ binding: NativeMediaBinding, enabled: Bool) async throws {
        _ = try await call("sessions:setSharing", kind: "mutation", args: (try scope(binding)).merging(["enabled": enabled]) { _, new in new })
    }

    private func scope(_ binding: NativeMediaBinding) throws -> [String: Any] {
        guard !binding.sessionID.isEmpty, binding.sessionID.count <= 512,
              binding.sessionID.rangeOfCharacter(from: .whitespacesAndNewlines) == nil else {
            throw NativeMediaTransportError.invalidRequest
        }
        return ["sessionId": binding.sessionID, "epoch": binding.epoch.uuidString]
    }

    private func call(_ path: String, kind: String, args: [String: Any],
                      maximumResponseBytes: Int = 1_048_576,
                      requestTimeout: TimeInterval = 8) async throws -> Any {
        let before: NativeMediaCredentials
        do { before = try await identity.currentCredentials() }
        catch { throw NativeMediaTransportError.unauthenticated }
        try Task.checkCancellation()
        let segments = before.jwt.split(separator: ".", omittingEmptySubsequences: false)
        guard !before.accountID.isEmpty, before.accountID.count <= 512,
              before.jwt.count <= 16_384, segments.count == 3,
              segments.allSatisfy({ !$0.isEmpty && $0.utf8.allSatisfy({ byte in
                  (byte >= 65 && byte <= 90) || (byte >= 97 && byte <= 122) ||
                  (byte >= 48 && byte <= 57) || byte == 45 || byte == 95
              }) }) else { throw NativeMediaTransportError.unauthenticated }
        let endpoint = origin.appendingPathComponent("api").appendingPathComponent(kind)
        var request = URLRequest(url: endpoint, cachePolicy: .reloadIgnoringLocalAndRemoteCacheData, timeoutInterval: requestTimeout)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(before.jwt)", forHTTPHeaderField: "Authorization")
        request.setValue("no-store", forHTTPHeaderField: "Cache-Control")
        request.httpBody = try JSONSerialization.data(withJSONObject: ["path": path, "format": "convex_encoded_json", "args": [args]])
        // Lease clocks begin at request send in the coordinator, never at response receipt.
        let response: URLResponse
        let payload: Data
        do {
            (payload, response) = try await Self.readResponse(session: path == "vision:findObject" ? objectSession : session, request: request,
                                                             redirects: redirects, maxResponseBytes: maximumResponseBytes)
        } catch {
            let after = try? await identity.currentCredentials()
            if after?.accountID != before.accountID || after?.identityEpoch != before.identityEpoch {
                onIdentityInvalidated?()
                throw NativeMediaTransportError.accountChanged
            }
            if let typed = error as? NativeMediaTransportError { throw typed }
            throw NativeMediaTransportError.unavailable
        }
        let after: NativeMediaCredentials
        do { after = try await identity.currentCredentials() }
        catch {
            onIdentityInvalidated?()
            throw NativeMediaTransportError.accountChanged
        }
        guard before.accountID == after.accountID, before.identityEpoch == after.identityEpoch else {
            onIdentityInvalidated?()
            throw NativeMediaTransportError.accountChanged
        }
        guard let http = response as? HTTPURLResponse, http.url?.scheme?.lowercased() == "https",
              http.url?.host == origin.host, http.url?.port == origin.port,
              http.statusCode == 200 else {
            throw NativeMediaTransportError.rejected
        }
        guard let envelope = try? JSONSerialization.jsonObject(with: payload) as? [String: Any],
              let status = envelope["status"] as? String else { throw NativeMediaTransportError.invalidResponse }
        guard status == "success", let value = envelope["value"] else {
            throw status == "error" ? NativeMediaTransportError.rejected : NativeMediaTransportError.invalidResponse
        }
        return value
    }

    /// URLSession.AsyncBytes yields individual bytes. Keep its bounded accumulation off the
    /// MainActor so an oversized response cannot stall local Stop or the video watchdog.
    nonisolated private static func readResponse(session: URLSession, request: URLRequest,
                                                 redirects: NoRedirects, maxResponseBytes: Int) async throws -> (Data, URLResponse) {
        let (bytes, response) = try await session.bytes(for: request, delegate: redirects)
        defer { bytes.task.cancel() }
        if response.expectedContentLength > maxResponseBytes {
            throw NativeMediaTransportError.responseTooLarge
        }
        var payload = Data()
        payload.reserveCapacity(min(maxResponseBytes, max(0, Int(response.expectedContentLength))))
        for try await byte in bytes {
            if payload.count >= maxResponseBytes { throw NativeMediaTransportError.responseTooLarge }
            payload.append(byte)
        }
        return (payload, response)
    }

    private func room(_ raw: Any) throws -> NativeMediaRoom {
        let data = try object(raw)
        return NativeMediaRoom(roomName: try nonemptyString(data, "roomName"), roomSID: try nonemptyString(data, "roomSid"),
                               generation: try safeUInt(data, "generation"))
    }

    private func object(_ raw: Any?) throws -> [String: Any] {
        guard let value = raw as? [String: Any] else { throw NativeMediaTransportError.invalidResponse }
        return value
    }
    private func string(_ data: [String: Any], _ key: String) throws -> String {
        guard let value = data[key] as? String else { throw NativeMediaTransportError.invalidResponse }
        return value
    }
    private func nonemptyString(_ data: [String: Any], _ key: String) throws -> String {
        let value = try string(data, key)
        guard !value.isEmpty, value.count <= 512,
              value.rangeOfCharacter(from: .whitespacesAndNewlines) == nil else {
            throw NativeMediaTransportError.invalidResponse
        }
        return value
    }
    private func jwtString(_ data: [String: Any], _ key: String) throws -> String {
        let value = try string(data, key)
        let segments = value.split(separator: ".", omittingEmptySubsequences: false)
        guard !value.isEmpty, value.count <= 16_384, segments.count == 3,
              segments.allSatisfy({ !$0.isEmpty && $0.utf8.allSatisfy({ byte in
                  (byte >= 65 && byte <= 90) || (byte >= 97 && byte <= 122) ||
                  (byte >= 48 && byte <= 57) || byte == 45 || byte == 95
              }) }) else { throw NativeMediaTransportError.invalidResponse }
        return value
    }
    private func uuid(_ data: [String: Any], _ key: String) throws -> UUID {
        guard let parsed = UUID(uuidString: try string(data, key)) else { throw NativeMediaTransportError.invalidResponse }
        return parsed
    }
    private func bool(_ data: [String: Any], _ key: String) throws -> Bool {
        guard let number = data[key] as? NSNumber, CFGetTypeID(number) == CFBooleanGetTypeID() else {
            throw NativeMediaTransportError.invalidResponse
        }
        return number.boolValue
    }
    private func safeUInt(_ data: [String: Any], _ key: String) throws -> UInt64 {
        guard let number = data[key] as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(),
              let decimal = Decimal(string: number.stringValue), decimal >= 0,
              decimal <= Decimal(Self.maxSafeInteger),
              let value = UInt64(number.stringValue) else { throw NativeMediaTransportError.invalidResponse }
        return value
    }
    private func positiveMs(_ data: [String: Any], _ key: String) throws -> Int64 {
        let value = try safeUInt(data, key)
        guard value > 0 else { throw NativeMediaTransportError.invalidResponse }
        return Int64(value)
    }
    private func nonnegativeMs(_ data: [String: Any], _ key: String) throws -> Int64 {
        Int64(try safeUInt(data, key))
    }
    private func validateNonce(_ nonce: String) throws {
        guard !nonce.isEmpty, nonce.count <= 256,
              nonce.unicodeScalars.allSatisfy({ CharacterSet(charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_:-").contains($0) }) else {
            throw NativeMediaTransportError.invalidRequest
        }
    }
}

extension ConvexNativeMediaBackend {
    func helperSessions(cursor: String? = nil) async throws -> NativeHelperPage {
        guard cursor == nil || (!cursor!.isEmpty && cursor!.count <= 16_384) else {
            throw NativeMediaTransportError.invalidRequest
        }
        let data = try object(try await call("sessions:listJoinable", kind: "query", args: [
            "paginationOpts": ["numItems": 20, "cursor": cursor as Any? ?? NSNull()]
        ]))
        guard let page = data["page"] as? [Any], page.count <= 20 else {
            throw NativeMediaTransportError.invalidResponse
        }
        let done = try bool(data, "isDone")
        let cursor = try string(data, "continueCursor")
        guard cursor.count <= 16_384, done || !cursor.isEmpty else {
            throw NativeMediaTransportError.invalidResponse
        }
        let sessions = try page.map { value in
            let item = try object(value)
            let name = try string(item, "ownerDisplayName")
            let epoch = try nonemptyString(item, "epoch")
            guard UUID(uuidString: epoch) != nil,
                  !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, name.count <= 160,
                  !name.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }) else {
                throw NativeMediaTransportError.invalidResponse
            }
            return NativeHelperSession(sessionID: try nonemptyString(item, "sessionId"), epoch: epoch, ownerName: name)
        }
        return NativeHelperPage(sessions: sessions, nextCursor: done ? nil : cursor)
    }

    func redeemHelperInvite(_ code: String) async throws -> Bool {
        guard code.count == 43, code.utf8.allSatisfy({ byte in
            (65...90).contains(byte) || (97...122).contains(byte) || (48...57).contains(byte) || byte == 45 || byte == 95
        }) else { throw NativeMediaTransportError.invalidRequest }
        let data = try object(try await call("invites:redeemInvite", kind: "action", args: ["code": code]))
        let state = try string(data, "state")
        guard state == "accepted" || state == "pending" else { throw NativeMediaTransportError.invalidResponse }
        return state == "accepted"
    }

    func reserveHelper(_ session: NativeHelperSession, clientID: String) async throws -> NativeHelperConnection {
        try validateNonce(clientID)
        let data = try object(try await call("assistance:reserveHelper", kind: "mutation",
            args: helperScope(session).merging(["clientInstanceId": clientID]) { _, new in new }))
        guard try string(data, "kind") == "reserved", try string(data, "clientInstanceId") == clientID,
              try positiveMs(data, "expiresAt") > Int64(Date().timeIntervalSince1970 * 1_000) else {
            throw NativeMediaTransportError.invalidResponse
        }
        let version = try safeUInt(data, "leaseVersion")
        guard version > 0 else { throw NativeMediaTransportError.invalidResponse }
        return NativeHelperConnection(session: session, connectionID: try nonemptyString(data, "connectionId"), leaseVersion: version)
    }

    func helperToken(_ session: NativeHelperSession, clientID: String) async throws -> NativeMediaToken {
        try validateNonce(clientID)
        let data = try object(try await call("livekit:issueHelperToken", kind: "action",
            args: helperScope(session).merging(["clientInstanceId": clientID]) { _, new in new }))
        let room = try room(data)
        let token = try jwtString(data, "token")
        guard let url = URL(string: try nonemptyString(data, "serverUrl")),
              ["wss", "https"].contains(url.scheme?.lowercased() ?? ""), url.host != nil,
              url.user == nil, url.password == nil, url.query == nil, url.fragment == nil,
              url.path.isEmpty || url.path == "/", room.generation > 0 else {
            throw NativeMediaTransportError.invalidResponse
        }
        let expiresAt = try positiveMs(data, "expiresAt")
        guard expiresAt > Int64(Date().timeIntervalSince1970 * 1_000) else { throw NativeMediaTransportError.invalidResponse }
        return NativeMediaToken(token: token, serverURL: url, room: room,
                                identity: try nonemptyString(data, "identity"), expiresAtMs: expiresAt)
    }

    func helperStatus(_ connection: NativeHelperConnection, nonce: String) async throws -> NativeHelperStatus? {
        try validateNonce(nonce)
        let data = try object(try await call("assistance:helperStatus", kind: "query",
            args: helperScope(connection).merging(["nonce": nonce]) { _, new in new }))
        guard try string(data, "nonce") == nonce else { throw NativeMediaTransportError.invalidResponse }
        let state = try string(data, "state")
        if state == "closed" { return nil }
        guard let parsed = NativeHelperStatus.State(rawValue: state) else { throw NativeMediaTransportError.invalidResponse }
        let duration = try positiveMs(data, "validForMs")
        let version = try safeUInt(data, "authorizationVersion")
        guard duration <= 15_000, version > 0 else { throw NativeMediaTransportError.invalidResponse }
        return NativeHelperStatus(state: parsed, room: try room(data),
            ownerIdentity: try nonemptyString(data, "ownerIdentity"), ownerSID: try nonemptyString(data, "ownerParticipantSid"),
            helperSID: parsed == .connected ? try nonemptyString(data, "participantSid") : nil,
            authorizationVersion: version, validForMs: duration)
    }

    func helperHeartbeat(_ connection: NativeHelperConnection, authorizationVersion: UInt64) async throws {
        guard authorizationVersion > 0, authorizationVersion <= Self.maxSafeInteger else {
            throw NativeMediaTransportError.invalidRequest
        }
        _ = try await call("assistance:heartbeat", kind: "mutation",
            args: helperScope(connection).merging(["authorizationVersion": authorizationVersion]) { _, new in new })
    }

    func leaveHelper(_ connection: NativeHelperConnection) async throws {
        _ = try await call("assistance:leave", kind: "mutation", args: helperScope(connection))
    }

    private func helperScope(_ session: NativeHelperSession) throws -> [String: Any] {
        guard !session.sessionID.isEmpty, session.sessionID.count <= 512,
              session.sessionID.rangeOfCharacter(from: .whitespacesAndNewlines) == nil,
              UUID(uuidString: session.epoch) != nil else { throw NativeMediaTransportError.invalidRequest }
        return ["sessionId": session.sessionID, "epoch": session.epoch]
    }

    private func helperScope(_ connection: NativeHelperConnection) throws -> [String: Any] {
        guard connection.leaseVersion > 0, connection.leaseVersion <= Self.maxSafeInteger else {
            throw NativeMediaTransportError.invalidRequest
        }
        try validateNonce(connection.connectionID)
        return try helperScope(connection.session).merging([
            "connectionId": connection.connectionID, "leaseVersion": connection.leaseVersion
        ]) { _, new in new }
    }
}
