import Foundation
import Tasks

/// Proposed adapter seams. Implementations must authenticate connections, validate reference
/// payloads/track ownership and derive scope from server identity, never from JSON role fields.
/// There is deliberately no remote Confirm, Resume, success, audio or arbitrary tool event.
enum FriendHelperEvent {
    case proposal(authenticatedScope: FriendSelectionScope, FriendSelectionProposal)
    case pause(authenticatedScope: FriendSelectionScope)
    case left(authenticatedScope: FriendSelectionScope)
}

@MainActor
protocol FriendAssistanceTransport: AnyObject {
    var onHelperEvent: ((FriendHelperEvent) -> Void)? { get set }
    var onAuthorizationLost: ((SharingAuthorizationScope) -> Void)? { get set }
    var onBackendUnavailable: ((SharingAuthorizationScope) -> Void)? { get set }
    func renew(_ request: SharingLeaseRenewal) async throws -> SharingLeaseGrant
    func resolve(_ proposal: FriendSelectionProposal) async throws -> FriendSelectionResolution
    /// Return only the authenticated worker's exact acknowledgement, not a local send receipt.
    func synchronizeAccepted(_ binding: FriendSelectionAccepted) async throws -> FriendSelectionAccepted
    func cancelSelection(_ proposal: FriendSelectionProposal)
    func evictHelper(_ scope: SharingAuthorizationScope) async
}

struct FriendSelectionPrompt: Equatable {
    let proposal: FriendSelectionProposal
    let promptID: String
    let target: ResolvedTarget
}

enum FriendLocalInput {
    case selection(FriendOwnerConfirmation)
    case taskConfirmation(SessionContext, promptID: String, answer: ConfirmationAnswer,
                          provenance: FriendConfirmationProvenance)
    case command(SessionContext, LocalCommand)
    case intent(SessionContext, TaskRequest, provenance: FriendConfirmationProvenance)
    case choice(SessionContext, requestID: String, value: String, provenance: FriendConfirmationProvenance)
    case menuCommand(SessionContext, requestID: String, MenuCommand, provenance: FriendConfirmationProvenance)
    case calibrationAnswer(SessionContext, String, provenance: FriendConfirmationProvenance)
}

/// R1/local media adapter, independently injected from network transport. The implementation
/// supplies actual playback completion and owner speech provenance after local echo gating.
/// Camera/audio ownership remains R1's. No implementation is silently synthesized in live mode.
@MainActor
protocol FriendAssistanceLocalClient: AnyObject {
    var onOwnerInput: ((FriendLocalInput) -> Void)? { get set }
    func playPrompt(_ prompt: FriendSelectionPrompt,
                    completion: @escaping @MainActor (FriendPromptPlaybackProvenance) -> Void)
    func cancelPrompt(_ prompt: FriendSelectionPrompt)
    /// Close locally and flush buffered helper audio before returning. Reopen at the live edge.
    /// Enabling requires verified helper isolation or a fresh private room after an uncertain
    /// eviction. Never republish to a room that may retain a revoked participant.
    func setSharingAllowed(_ allowed: Bool, scope: SharingAuthorizationScope)
    func isSharedMediaLive(scope: SharingAuthorizationScope) -> Bool
    func preemptHelperAudio()
}

@MainActor
struct FriendAssistanceIntegration {
    let transport: any FriendAssistanceTransport
    let local: any FriendAssistanceLocalClient
}
