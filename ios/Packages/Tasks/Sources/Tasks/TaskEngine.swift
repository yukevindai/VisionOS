import Foundation
import Targets

/// Pure, deterministic task reducer. Execute returned effects in order on a serialized coordinator.
/// Revalidate fresh R1 evidence again at actual guidance dispatch; async queues must not extend cue lifetime.
public struct TaskEngine: Sendable {
    public private(set) var state: TaskState
    public let configuration: TaskConfiguration
    public let guidingHand: GuidingHand
    private var sequence: UInt64 = 0
    private var seenIDs: [String] = []
    private var lastNow: Int64 = 0
    private var lastEventMs: Int64?
    private var approachCompleted = false
    private var resumeConfirmationNeeded = false
    private var actionContext: RequestContext?
    private var actionTarget: ResolvedTarget?
    private var previousScreenID: String?
    private var hasLeftWelcome = false
    private var recoveringBack = false
    /// The semantic identity accepted by the owner. Every resume and retry still requests
    /// fresh R1 reacquisition of this target; its old frame is never physical evidence.
    private var acceptedFriendTarget: ResolvedTarget?

    public init(sessionID: String, initialGeneration: UInt64 = 0, guidingHand: GuidingHand = .right, configuration: TaskConfiguration = .init()) {
        state = TaskState(context: TaskContext(sessionID: sessionID, taskID: "", generation: initialGeneration))
        self.guidingHand = guidingHand; self.configuration = configuration
    }

    /// Gate queued dispatch immediately before handing the request to R1. R1 must also enforce
    /// freshness continuously during its guidance loop. Unknown depth never authorizes approach.
    public func isGuidanceDispatchAllowed(context: RequestContext, evidence: PhysicalEvidence, nowMs: Int64) -> Bool {
        guard context == state.pendingRequest, context.task == state.context,
              nowMs >= lastNow, nowMs >= context.issuedAtMs, nowMs < context.expiresAtMs,
              state.phase == .guiding || state.phase == .approaching else { return false }
        return physicalFailure(evidence, mode: state.phase == .approaching ? .approach : .hand, now: nowMs) == nil
    }

    public mutating func reduce(_ event: TaskEvent, nowMs: Int64) -> [TaskEffect] {
        guard nowMs >= lastNow, nowMs >= 0 else {
            // Cancellation remains available even when the caller's clock is invalid.
            if case .stop(let reason) = event { return stop(reason) }
            return [.rejected(.stale)]
        }
        lastNow = nowMs
        switch event {
        case .start(let request): return start(request, now: nowMs)
        case .stop(let reason): return stop(reason)
        case .resume:
            guard state.phase == .paused, state.request != nil else { return [.rejected(.unsupportedState)] }
            // Explicit resume starts reacquisition, never replays a previous guidance request.
            resumeConfirmationNeeded = state.lastStop != .userPause
            return begin(now: nowMs)
        case .tick:
            guard let pending = state.pendingRequest, nowMs >= pending.expiresAtMs else { return [] }
            if state.phase == .awaitingConfirmation { return fail(.actionUnconfirmed, "The confirmation expired. Please ask again.") }
            if [.guiding, .approaching, .awaitingPassage, .reacquiring].contains(state.phase) {
                return stop(.trackingLost) + [.speak(text: "Fresh guidance is unavailable. Assistance is paused.", priority: 1)]
            }
            if state.phase == .verifying { return askActionConfirmation(now: nowMs) }
            if [.choosing, .discussingMenu].contains(state.phase) { return stop(.userPause) }
            return fail(.providerTimeout, "The request timed out. Please try again or ask someone nearby for help.")
        case .receive(let envelope, let response):
            if let rejection = validate(envelope, now: nowMs) { return [.rejected(rejection)] }
            let effects = receive(response, source: envelope.source, now: nowMs)
            if effects.count == 1, case .rejected = effects[0] { return effects }
            // Rejected responses do not consume an event or move this request's watermark.
            // A transition may have opened a new request with its own ordering history.
            if state.pendingRequest == envelope.context { lastEventMs = envelope.monotonicMs }
            seenIDs.append(envelope.eventID)
            if seenIDs.count > 256 { seenIDs.removeFirst(seenIDs.count - 256) }
            return effects
        }
    }

    /// Installs a trusted local owner-accepted binding after `.stop(.replaced)` has cancelled
    /// old work and delivered ALL_STOP, and after the worker has acknowledged this exact
    /// binding. The stop already advanced the generation; this entry must not advance it again.
    /// A rejected binding leaves the stopped engine unchanged. Kiosk controls require a
    /// current TaskScreen and are deliberately unsupported by this entry.
    public mutating func beginAcceptedSelection(_ binding: FriendSelectionAccepted,
                                                nowMs: Int64) -> [TaskEffect] {
        let old = state.context
        guard nowMs >= lastNow, nowMs >= 0,
              state.phase == .paused, state.lastStop == .replaced,
              old.sessionID == binding.scope.sessionID,
              old.taskID == binding.oldTaskID,
              binding.oldTaskGeneration < FriendSelectionPolicy.maximumSafeGeneration,
              old.generation == binding.oldTaskGeneration + 1,
              binding.newTaskGeneration == old.generation,
              binding.proposalGeneration > 0,
              !binding.selectionID.isEmpty, !binding.promptID.isEmpty,
              binding.newTaskID == "friend:\(binding.scope.sessionID):\(binding.scope.sessionEpoch):\(binding.proposalGeneration)",
              binding.newTaskID != binding.oldTaskID else { return [.rejected(.wrongContext)] }

        let kind: TaskKind
        switch binding.target.kind {
        case .button: kind = .button
        case .object: kind = .object
        case .handle, .door: kind = .door
        case .opener: kind = binding.target.doorAction == nil ? .button : .door
        case .screenControl: return [.rejected(.unsupportedState)]
        }
        let query = binding.target.label
        guard !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              query.count <= 2_000 else { return [.rejected(.invalidRequest)] }

        // Stage the existing target validator so an unsafe or malformed binding cannot
        // consume the accepted generation or turn a stopped task into a failed new task.
        var staged = self
        staged.state = TaskState(context: TaskContext(sessionID: old.sessionID,
                                                      taskID: binding.newTaskID,
                                                      generation: binding.newTaskGeneration))
        staged.state.request = TaskRequest(id: binding.newTaskID, kind: kind, query: query)
        staged.resetInternals()
        staged.acceptedFriendTarget = binding.target
        let effects = staged.acceptTarget(binding.target, now: nowMs)
        guard effects.contains(where: {
            if case .reacquire = $0 { return true }
            if case .askConfirmation(let prompt) = $0 { return prompt.purpose == .doorAction }
            return false
        }) else { return [.rejected(.unsafeTarget)] }
        staged.lastNow = nowMs
        self = staged
        return effects
    }

    /// Prepare exactly one accepted binding by invalidating the old task before the worker
    /// ACK wait. The caller must execute these Stop effects locally before sending that wait.
    /// Unlike an ordinary repeated Stop, a fresh accepted binding after an ACK timeout must
    /// advance the generation again; the old generation in this binding makes replay fail.
    public mutating func prepareAcceptedSelection(_ binding: FriendSelectionAccepted,
                                                  nowMs: Int64) -> [TaskEffect] {
        let old = state.context
        guard nowMs >= lastNow, nowMs >= 0 else { return [.rejected(.stale)] }
        guard old.sessionID == binding.scope.sessionID,
              old.taskID == binding.oldTaskID,
              old.generation == binding.oldTaskGeneration,
              old.generation < FriendSelectionPolicy.maximumSafeGeneration,
              binding.newTaskGeneration == old.generation + 1,
              binding.proposalGeneration > 0,
              !binding.selectionID.isEmpty, !binding.promptID.isEmpty,
              binding.newTaskID == "friend:\(binding.scope.sessionID):\(binding.scope.sessionEpoch):\(binding.proposalGeneration)",
              binding.newTaskID != binding.oldTaskID else { return [.rejected(.wrongContext)] }
        lastNow = nowMs
        return stop(.replaced, force: true)
    }

    private func validate(_ envelope: EventEnvelope, now: Int64) -> Rejection? {
        guard envelope.schemaVersion == 3, !envelope.eventID.isEmpty,
              envelope.monotonicMs >= 0, envelope.expiresAtMs > envelope.monotonicMs else { return .malformed }
        guard envelope.context.task == state.context else { return .wrongContext }
        guard !seenIDs.contains(envelope.eventID) else { return .duplicate }
        guard let pending = state.pendingRequest, pending == envelope.context else { return .wrongRequest }
        guard now < pending.expiresAtMs, now < envelope.expiresAtMs,
              envelope.monotonicMs <= now, envelope.monotonicMs >= pending.issuedAtMs,
              envelope.monotonicMs >= (lastEventMs ?? pending.issuedAtMs) else { return .stale }
        return nil
    }

    private mutating func start(_ request: TaskRequest, now: Int64) -> [TaskEffect] {
        guard !request.id.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              !request.query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              request.query.count <= 2_000, !state.context.sessionID.isEmpty,
              state.context.generation < UInt64.max - 1 else { return [.rejected(.invalidRequest)] }
        var effects: [TaskEffect] = []
        if state.request != nil { effects = invalidate(reason: .replaced) }
        let generation = state.context.generation + 1
        state = TaskState(context: TaskContext(sessionID: state.context.sessionID, taskID: request.id, generation: generation))
        state.request = request
        resetInternals()
        return effects + begin(now: now)
    }

    private mutating func resetInternals() {
        approachCompleted = false; actionContext = nil; actionTarget = nil; previousScreenID = nil
        hasLeftWelcome = false; recoveringBack = false; resumeConfirmationNeeded = false
        seenIDs.removeAll(keepingCapacity: true)
        acceptedFriendTarget = nil
    }

    private mutating func begin(now: Int64) -> [TaskEffect] {
        guard let request = state.request else { return [.rejected(.unsupportedState)] }
        guard state.context.generation < UInt64.max else {
            return fail(.lowConfidence, "The task safety counter is exhausted. End assistance and start a new session.")
        }
        state.confirmation = nil; state.target = nil; state.result = nil; actionContext = nil; actionTarget = nil
        switch request.kind {
        case .wallMenu:
            state.phase = .readingMenu
            return [.readMenu(makeRequest(now: now), query: request.query)]
        case .kiosk:
            state.phase = .readingScreen
            return [.readScreen(makeRequest(now: now))]
        default:
            if let acceptedFriendTarget { return acceptTarget(acceptedFriendTarget, now: now) }
            state.phase = .resolving
            return [.resolveTarget(makeRequest(now: now), query: request.query)]
        }
    }

    private mutating func makeRequest(now: Int64, confirmation: Bool = false) -> RequestContext {
        sequence += 1
        let duration = confirmation ? configuration.confirmationTimeoutMs : configuration.requestTimeoutMs
        let expiry = now.addingReportingOverflow(duration)
        let context = RequestContext(task: state.context, requestID: "\(state.context.taskID):\(state.context.generation):\(sequence)",
                                     issuedAtMs: now, expiresAtMs: expiry.overflow ? Int64.max : expiry.partialValue)
        state.pendingRequest = context
        lastEventMs = nil
        return context
    }

    private mutating func invalidate(reason: StopReason) -> [TaskEffect] {
        let old = state.context
        // Saturation keeps the stop path available even at exhaustion. start refuses further work.
        state.context = TaskContext(sessionID: old.sessionID, taskID: old.taskID,
                                    generation: old.generation == UInt64.max ? UInt64.max : old.generation + 1)
        state.pendingRequest = nil; state.confirmation = nil; state.target = nil
        state.lastStop = reason; approachCompleted = false
        return [.cancelWork(old), .allStop(state.context)]
    }

    private mutating func stop(_ reason: StopReason, force: Bool = false) -> [TaskEffect] {
        if state.phase == .paused, reason != .cancelled, reason != .ended,
           !force && !(reason == .replaced && state.lastStop != .replaced) { return [] }
        if state.phase == .cancelled && !force { return [] }
        var effects = invalidate(reason: reason)
        actionContext = nil; actionTarget = nil
        state.phase = (reason == .cancelled || reason == .ended) ? .cancelled : .paused
        effects.append(.speak(text: "Stop", priority: 0))
        if reason == .targetLost || reason == .handLost {
            effects.append(.speak(text: "The target or your hand is out of view. Reposition the camera, then ask to resume.", priority: 1))
        }
        if state.phase == .cancelled, state.request != nil {
            let result = TaskResult(context: state.context, status: .cancelled, verification: .unverified, error: nil)
            state.result = result; effects.append(.finished(result))
        }
        return effects
    }

    private mutating func receive(_ response: TaskResponse, source: EventSource, now: Int64) -> [TaskEffect] {
        switch response {
        case .target(let target):
            guard [.r3, .model, .r1].contains(source) else { return [.rejected(.wrongSource)] }
            guard state.phase == .resolving else { return [.rejected(.unsupportedState)] }
            return acceptTarget(target, now: now)
        case .physical(let evidence):
            guard source == .r1 else { return [.rejected(.wrongSource)] }
            guard state.phase == .reacquiring else { return [.rejected(.unsupportedState)] }
            return acquired(evidence, now: now)
        case .guidance(let mode, let targetID, let status, let evidence):
            guard source == .r1 else { return [.rejected(.wrongSource)] }
            guard state.target?.id == targetID else { return [.rejected(.wrongTarget)] }
            guard (mode == .approach && state.phase == .approaching) || (mode == .hand && state.phase == .guiding) else {
                return [.rejected(.unsupportedState)]
            }
            if status == .lost { return stop(.targetLost) }
            if status == .failed { return fail(.lowConfidence, "Guidance could not continue. Please ask someone nearby for help.") }
            guard let evidence else { return stop(.trackingLost) + [.rejected(.missingEvidence)] }
            if let error = physicalFailure(evidence, mode: mode, now: now) { return stop(error) }
            if status == .guiding { return [] }
            if mode == .approach {
                if state.request?.kind == .approach { return complete(.observed) }
                approachCompleted = true
                return reacquire(now: now)
            }
            return requestAction(now: now)
        case .verification(let outcome):
            guard source == .r1 || source == .r3 else { return [.rejected(.wrongSource)] }
            guard state.phase == .verifying else { return [.rejected(.unsupportedState)] }
            switch outcome {
            case .observed(let evidence):
                guard validVerification(evidence, now: now) else { return askActionConfirmation(now: now) }
                return actionSucceeded(.observed, now: now)
            case .unavailable, .uncertain: return askActionConfirmation(now: now)
            case .failed: return retry(now: now)
            case .doorBlocked:
                guard state.request?.kind == .door else { return [.rejected(.malformed)] }
                return stop(.hazard) + [.speak(text: "The door may be locked, heavy, or blocked. Please ask for help.", priority: 0)]
            }
        case .screen(let screen):
            guard source == .r3 || source == .r1 else { return [.rejected(.wrongSource)] }
            guard state.phase == .readingScreen else { return [.rejected(.unsupportedState)] }
            return acceptScreen(screen, now: now)
        case .screenChanged:
            guard source == .r1 || source == .r3, state.request?.kind == .kiosk else { return [.rejected(.wrongSource)] }
            var effects = invalidate(reason: .geometryChanged)
            actionContext = nil; actionTarget = nil
            state.phase = .readingScreen
            effects.append(.speak(text: "The screen changed. I will read the new layout.", priority: 1))
            effects.append(.readScreen(makeRequest(now: now)))
            return effects
        case .choice(let choice):
            guard source == .user else { return [.rejected(.wrongSource)] }
            guard state.phase == .choosing else { return [.rejected(.unsupportedState)] }
            return choose(choice, now: now)
        case .confirmation(let promptID, let answer):
            guard source == .user else { return [.rejected(.wrongSource)] }
            guard state.phase == .awaitingConfirmation, let prompt = state.confirmation,
                  prompt.promptID == promptID else { return [.rejected(.wrongRequest)] }
            return confirm(prompt.purpose, answer: answer, now: now)
        case .passageAuthorized(let evidence):
            guard source == .r1 else { return [.rejected(.wrongSource)] }
            guard state.phase == .awaitingPassage, state.lastVerification != .unverified else { return [.rejected(.unsupportedState)] }
            if let error = physicalFailure(evidence, mode: .approach, now: now) { return stop(error) }
            // R1 has already validated/released its passage cue; this reducer never says "walk through".
            return complete(state.lastVerification)
        case .menu(let menu):
            guard source == .r3, state.phase == .readingMenu else { return [.rejected(.wrongSource)] }
            state.menu = menu; state.sectionIndex = 0; state.phase = .discussingMenu
            var effects: [TaskEffect] = []
            if menu.isBlurry { effects.append(.speak(text: "Some menu text is blurry. I can only read the clear items.", priority: 4)) }
            if menu.isCutOff { effects.append(.speak(text: "Part of the menu is cut off. Reposition the camera to read the rest.", priority: 4)) }
            return effects + speakMenu(now: now)
        case .menuAnswer(let answer):
            guard source == .r3, state.phase == .answeringMenu else { return [.rejected(.wrongSource)] }
            state.phase = .discussingMenu
            return [.speak(text: answer, priority: 4), .awaitMenuCommand(makeRequest(now: now, confirmation: true))]
        case .menuCommand(let command):
            guard source == .user else { return [.rejected(.wrongSource)] }
            return menuCommand(command, now: now)
        case .unavailable(let error): return fail(error, "That information is unavailable. Please try again or ask someone nearby for help.")
        }
    }

    private mutating func acceptTarget(_ target: ResolvedTarget, now: Int64) -> [TaskEffect] {
        guard !target.id.isEmpty, !target.frameID.isEmpty, target.region.isValid else { return fail(.lowConfidence, "The target location is invalid. Please try again.") }
        guard !target.label.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              target.safety != .ambiguous else { return fail(.labelAmbiguous, "The label is unclear. Please specify a clearly labeled target.") }
        if isFinancial(target) { return paymentBoundary(total: state.screen?.total) }
        let unassessedObject = target.safety == .unassessedObject
        guard target.safety == .ordinary || (unassessedObject && state.request?.kind == .object && target.kind == .object && !target.requiresApproach),
              !target.isHingeSide else { return fail(.labelAmbiguous, "I cannot safely guide to that control. Please ask for help.") }
        let allowed: [TargetKind]
        switch state.request?.kind {
        case .button: allowed = [.button, .opener]
        case .object: allowed = [.object]
        case .kiosk: allowed = [.screenControl]
        case .door: allowed = [.handle, .opener, .door]
        case .approach: allowed = [.button, .object, .screenControl, .handle, .opener, .door]
        default: return [.rejected(.unsupportedState)]
        }
        guard allowed.contains(target.kind) else { return fail(.lowConfidence, "The target does not match the requested task.") }
        if state.request?.kind == .kiosk {
            guard target.sourceScreenID == state.screen?.id, target.frameID == state.screen?.frameID else { return [.rejected(.wrongTarget)] }
        }
        state.target = target
        var effects: [TaskEffect] = []
        if let caution = target.caution, !caution.isEmpty { effects.append(.speak(text: caution, priority: 1)) }
        if unassessedObject {
            return effects + ask(.objectGuidance, "I found \(target.label), but cannot assess whether it is safe to touch. Is this the intended object, safe to touch and within reach? Keep your hand and camera steady, then say yes or no.", now: now)
        }
        if state.request?.kind == .door {
            state.selectedDoorAction = target.doorAction
            if target.doorAction == nil || ((target.doorAction == .push || target.doorAction == .pull) && !target.doorActionEvidence) {
                state.selectedDoorAction = nil
                return effects + ask(.doorAction, "Does the door say push or pull, or does it use a lever or opener?", now: now)
            }
        }
        return effects + reacquire(now: now)
    }

    private func isFinancial(_ target: ResolvedTarget) -> Bool {
        if [.payment, .purchaseAuthorization, .unknownCheckout].contains(target.safety) { return true }
        let words = target.label.lowercased().components(separatedBy: CharacterSet.alphanumerics.inverted).filter { !$0.isEmpty }
        // Defense in depth. R3 still must classify arbitrary/localized financial controls conservatively.
        return words.contains("pay") || words.contains("payment") || words.contains("purchase") || words.contains("checkout") || words.contains("buy") || target.label.lowercased().contains("place order") || target.label.lowercased().contains("submit order")
    }

    private mutating func reacquire(now: Int64) -> [TaskEffect] {
        guard let target = state.target else { return [.rejected(.wrongTarget)] }
        state.phase = .reacquiring
        return [.reacquire(makeRequest(now: now), target: target)]
    }

    private func physicalFailure(_ evidence: PhysicalEvidence, mode: GuidanceMode, now: Int64, requiresHand: Bool = true) -> StopReason? {
        guard evidence.targetID == state.target?.id, !evidence.frameID.isEmpty,
              evidence.observedAtMs >= 0, evidence.observedAtMs <= now,
              now - evidence.observedAtMs <= configuration.freshnessMs else { return .trackingLost }
        if evidence.hazardPresent || evidence.suspectedStep { return .hazard }
        if !evidence.trackingNormal || !evidence.outputReady { return .trackingLost }
        if !evidence.targetVisible { return .targetLost }
        if state.target?.safety == .unassessedObject && !evidence.depthUsable { return .trackingLost }
        if mode == .hand && requiresHand && !evidence.handVisible { return .handLost }
        if mode == .approach {
            guard evidence.depthUsable, evidence.freeSpaceObserved, let distance = evidence.distanceM,
                  distance.isFinite, distance >= 0, distance <= 5 else { return .hazard }
        }
        return nil
    }

    private mutating func acquired(_ evidence: PhysicalEvidence, now: Int64) -> [TaskEffect] {
        guard let target = state.target else { return [.rejected(.wrongTarget)] }
        let mode: GuidanceMode = (state.request?.kind == .approach || (target.requiresApproach && !approachCompleted)) ? .approach : .hand
        let automaticDoor = state.request?.kind == .door && state.selectedDoorAction == .automatic
        if let error = physicalFailure(evidence, mode: mode, now: now, requiresHand: !automaticDoor) { return stop(error) }
        if resumeConfirmationNeeded {
            return ask(.resumeGuidance, "Tracking is ready. Would you like to resume guidance?", now: now)
        }
        if automaticDoor && mode == .hand {
            return requestAction(now: now)
        }
        state.phase = mode == .approach ? .approaching : .guiding
        var effects: [TaskEffect] = []
        if mode == .hand && !evidence.depthUsable {
            effects.append(.speak(text: "Depth is uncertain. Guidance can only align in the image plane; confirm the result yourself.", priority: 2))
        }
        effects.append(.requestGuidance(makeRequest(now: now, confirmation: true), mode: mode, target: target, hand: guidingHand))
        return effects
    }

    private var action: ActionKind {
        switch state.request?.kind { case .object: return .grasp; case .kiosk: return .tap; case .door: return .openDoor; default: return .press }
    }
    private var instruction: String {
        switch action {
        case .press: return "Press the button now."
        case .grasp: return "Grasp the object now."
        case .tap: return "Lightly tap the control now."
        case .openDoor:
            switch state.selectedDoorAction {
            case .automatic: return "Wait for the automatic door to open."
            case .opener: return "Press the door opener now."
            case .push: return "Push the door now."
            case .pull: return "Pull the door now."
            case .lever: return "Operate the lever now."
            case nil: return "Please identify the door action."
            }
        }
    }

    private mutating func requestAction(now: Int64) -> [TaskEffect] {
        guard let target = state.target else { return [.rejected(.wrongTarget)] }
        actionTarget = target
        var effects: [TaskEffect] = [.speak(text: instruction, priority: 2)]
        if state.request?.kind == .kiosk {
            effects = invalidate(reason: .geometryChanged) + effects
            // Remaining actionTarget is historical verification context only.
            state.phase = .readingScreen
            previousScreenID = state.screen?.id
            let context = makeRequest(now: now)
            actionContext = context
            effects.append(.readScreen(context))
        } else {
            state.phase = .verifying
            let context = makeRequest(now: now)
            actionContext = context
            effects.append(.requestVerification(context, action: action, target: target))
        }
        return effects
    }

    private func validVerification(_ evidence: VerificationEvidence, now: Int64) -> Bool {
        guard let actionContext, let actionTarget, evidence.targetID == actionTarget.id,
              evidence.actionRequestID == actionContext.requestID,
              evidence.provenance != .modelInterpretation, evidence.isCertain,
              !evidence.beforeFrameID.isEmpty, !evidence.afterFrameID.isEmpty,
              evidence.beforeFrameID != evidence.afterFrameID,
              evidence.beforeMs >= 0, evidence.beforeMs <= actionContext.issuedAtMs,
              evidence.afterMs >= actionContext.issuedAtMs, evidence.afterMs > evidence.beforeMs,
              evidence.afterMs <= now else { return false }
        switch (action, evidence.kind) {
        case (.press, .buttonIndicatorChanged), (.grasp, .objectHandCoMotion), (.tap, .screenTransition), (.openDoor, .doorOpening): return true
        default: return false
        }
    }

    private mutating func askActionConfirmation(now: Int64) -> [TaskEffect] {
        let question: String
        switch action {
        case .press: question = "Did the button activate?"
        case .grasp: question = "Are you holding the requested object?"
        case .tap: question = "Did the requested screen open after your tap?"
        case .openDoor: question = "Is the door open? This does not confirm that the path is clear."
        }
        return ask(.actionResult, question, now: now)
    }

    private mutating func ask(_ purpose: ConfirmationPurpose, _ question: String, now: Int64) -> [TaskEffect] {
        state.phase = .awaitingConfirmation
        let confirmation = PendingConfirmation(context: makeRequest(now: now, confirmation: true), purpose: purpose, question: question)
        state.confirmation = confirmation
        return [.askConfirmation(confirmation)]
    }

    private mutating func confirm(_ purpose: ConfirmationPurpose, answer: ConfirmationAnswer, now: Int64) -> [TaskEffect] {
        switch purpose {
        case .objectGuidance:
            if answer == .no { return stop(.cancelled) }
            guard answer == .yes else { return [.rejected(.malformed)] }
            guard state.request?.kind == .object, let target = state.target,
                  target.kind == .object, target.safety == .unassessedObject,
                  !target.requiresApproach else { return [.rejected(.wrongTarget)] }
            state.confirmation = nil
            return reacquire(now: now)
        case .doorAction:
            guard case .doorAction(let selected) = answer else { return [.rejected(.malformed)] }
            state.confirmation = nil; state.selectedDoorAction = selected
            return reacquire(now: now)
        case .resumeGuidance:
            if answer == .no { return stop(.userPause) }
            guard answer == .yes else { return [.rejected(.malformed)] }
            state.confirmation = nil; resumeConfirmationNeeded = false
            return reacquire(now: now) // User deliberation may have made the previous frame stale.
        case .restartKiosk:
            if answer == .no { return stop(.cancelled) }
            guard answer == .yes else { return [.rejected(.malformed)] }
            state.confirmation = nil; hasLeftWelcome = false; previousScreenID = nil; recoveringBack = false
            return begin(now: now)
        case .actionResult:
            guard answer == .yes || answer == .no else { return [.rejected(.malformed)] }
            state.confirmation = nil
            if answer == .no { return retry(now: now) }
            return actionSucceeded(.userConfirmed, now: now)
        }
    }

    private mutating func actionSucceeded(_ verification: VerificationKind, now: Int64) -> [TaskEffect] {
        state.lastVerification = verification; state.retries = 0
        if state.request?.kind == .door {
            guard let target = state.target else { return [.rejected(.wrongTarget)] }
            state.phase = .awaitingPassage
            return [.speak(text: verification == .userConfirmed ? "You confirmed the door is open. Checking the path." : "The door opening was observed. Checking the path.", priority: 2),
                    .requestPassage(makeRequest(now: now), target: target)]
        }
        if state.request?.kind == .kiosk {
            actionContext = nil; actionTarget = nil; recoveringBack = false
            return presentOptions(now: now)
        }
        return complete(verification)
    }

    private mutating func retry(now: Int64) -> [TaskEffect] {
        guard state.retries < configuration.maximumRetries else {
            return fail(.actionUnconfirmed, "I could not confirm the result after \(configuration.maximumRetries) retries. Please ask someone nearby for help.")
        }
        state.retries += 1
        let effects = invalidate(reason: .targetLost)
        actionContext = nil; actionTarget = nil
        return effects + [.speak(text: "The action was not confirmed. I will locate the target again.", priority: 2)] + begin(now: now)
    }

    private mutating func complete(_ verification: VerificationKind) -> [TaskEffect] {
        guard verification != .unverified else { return [.rejected(.missingEvidence)] }
        let result = TaskResult(context: state.context, status: .completed, verification: verification, error: nil)
        state.result = result; state.phase = .completed; state.lastVerification = verification
        state.pendingRequest = nil; state.confirmation = nil; state.target = nil
        return [.successFeedback(result), .speak(text: verification == .userConfirmed ? "You confirmed the task is complete." : "The result was observed. Task complete.", priority: 4), .finished(result)]
    }

    private mutating func fail(_ error: TaskError, _ text: String) -> [TaskEffect] {
        let effects = invalidate(reason: .cancelled)
        state.phase = .failed
        let result = TaskResult(context: state.context, status: .failed, verification: .unverified, error: error)
        state.result = result
        return effects + [.speak(text: text, priority: 1), .finished(result)]
    }

    private mutating func paymentBoundary(total: String?) -> [TaskEffect] {
        let effects = invalidate(reason: .cancelled)
        state.phase = .completed
        let result = TaskResult(context: state.context, status: .handoff, verification: .unverified, error: .paymentBoundary)
        state.result = result
        var text = "Payment or purchase authorization is next. I have stopped guidance. Please continue independently or ask staff for help."
        if let total, !total.isEmpty { text = "The displayed total is \(total). " + text }
        return effects + [.speak(text: text, priority: 1), .finished(result)]
    }

    private mutating func acceptScreen(_ screen: TaskScreen, now: Int64) -> [TaskEffect] {
        guard !screen.id.isEmpty, !screen.frameID.isEmpty, screen.controls.count <= 100,
              Set(screen.controls.map(\.id)).count == screen.controls.count,
              screen.controls.allSatisfy({ !$0.id.isEmpty && $0.region.isValid && $0.frameID == screen.frameID && $0.sourceScreenID == screen.id }) else {
            return fail(.lowConfidence, "The screen could not be read reliably. Please try again.")
        }
        state.screen = screen
        if screen.isPaymentScreen { return paymentBoundary(total: screen.total) }
        if screen.isWelcomeScreen && hasLeftWelcome {
            var effects = invalidate(reason: .geometryChanged)
            state.result = TaskResult(context: state.context, status: .failed, verification: .unverified, error: .kioskReset)
            actionContext = nil; actionTarget = nil
            effects.append(.speak(text: "The kiosk returned to its welcome screen. The order may have reset.", priority: 1))
            effects += ask(.restartKiosk, "Would you like to restart the kiosk task?", now: now)
            return effects
        }
        if !screen.isWelcomeScreen { hasLeftWelcome = true }
        if let oldTarget = actionTarget {
            if let expected = oldTarget.expectedDestination, expected != screen.id, screen.id != previousScreenID {
                return recoverBack(screen, now: now)
            }
            if let evidence = screen.transitionEvidence, evidence.afterFrameID == screen.frameID,
               validVerification(evidence, now: now), screen.id != previousScreenID {
                return actionSucceeded(.observed, now: now)
            }
            return askActionConfirmation(now: now)
        }
        return presentOptions(now: now)
    }

    private mutating func presentOptions(now: Int64) -> [TaskEffect] {
        state.target = nil; state.confirmation = nil; state.phase = .choosing; state.sectionIndex = 0
        return speakOptions(now: now)
    }

    private mutating func speakOptions(now: Int64) -> [TaskEffect] {
        guard let screen = state.screen else { return [.rejected(.unsupportedState)] }
        let start = min(state.sectionIndex * 3, screen.controls.count)
        let labels = screen.controls.dropFirst(start).prefix(3).map(\.label)
        let text = labels.isEmpty ? "No clearly readable controls are available. Please ask staff for help." : labels.joined(separator: ". ") + ". Say your choice, more, repeat, or back."
        return [.speak(text: text, priority: 4), .awaitChoice(makeRequest(now: now, confirmation: true))]
    }

    private mutating func choose(_ label: String, now: Int64) -> [TaskEffect] {
        guard let screen = state.screen, !label.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return [.rejected(.invalidRequest)] }
        let direct = screen.controls.filter { $0.id == label }
        let proposal = Targets.ScreenModel(frameId: screen.frameID, capturedAtMs: 0,
            panelType: .orderingKiosk, source: .unknown,
            controls: screen.controls.map {
                InteractiveElement(id: $0.id, label: $0.label, type: .screenControl, region: $0.region)
            }, isPaymentScreen: screen.isPaymentScreen)
        let selection = TargetSelector().select(label, in: proposal)
        let selectedID: String?
        if let identified = direct.first, direct.count == 1 {
            selectedID = selection.status == .notFound || selection.target?.targetId == identified.id ? identified.id : nil
        } else {
            selectedID = selection.target?.targetId
        }
        guard let selectedID, let target = screen.controls.first(where: { $0.id == selectedID }) else {
            return [.speak(text: "That choice is ambiguous. Please say the exact control label.", priority: 4), .awaitChoice(makeRequest(now: now, confirmation: true))]
        }
        return acceptTarget(target, now: now)
    }

    private mutating func recoverBack(_ screen: TaskScreen, now: Int64) -> [TaskEffect] {
        let back = screen.controls.filter { $0.label.lowercased() == "back" && $0.safety == .ordinary }
        guard !recoveringBack, back.count == 1, let target = back.first else {
            return fail(.screenChanged, "An unexpected screen opened and a safe Back control is unavailable. Please ask staff for help.")
        }
        var effects = invalidate(reason: .geometryChanged)
        recoveringBack = true; actionContext = nil; actionTarget = nil
        effects.append(.speak(text: "\(screen.title) opened instead. I will locate its Back control.", priority: 2))
        return effects + acceptTarget(target, now: now)
    }

    private mutating func menuCommand(_ command: MenuCommand, now: Int64) -> [TaskEffect] {
        guard state.phase == .choosing || state.phase == .discussingMenu else { return [.rejected(.unsupportedState)] }
        if state.phase == .choosing {
            switch command {
            case .repeatOptions: return speakOptions(now: now)
            case .more:
                let pages = max(1, ((state.screen?.controls.count ?? 0) + 2) / 3)
                state.sectionIndex = (state.sectionIndex + 1) % pages
                return speakOptions(now: now)
            case .back: return choose("Back", now: now)
            case .question: return [.speak(text: "Please choose a listed control, or ask me to repeat the options.", priority: 4), .awaitChoice(makeRequest(now: now, confirmation: true))]
            }
        }
        guard let menu = state.menu else { return [.rejected(.unsupportedState)] }
        switch command {
        case .repeatOptions: return speakMenu(now: now)
        case .more:
            state.sectionIndex = min(state.sectionIndex + 1, max(0, menu.sections.count - 1))
            return speakMenu(now: now)
        case .back:
            state.sectionIndex = max(0, state.sectionIndex - 1)
            return speakMenu(now: now)
        case .question(let question):
            guard !question.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, question.count <= 2_000 else { return [.rejected(.invalidRequest)] }
            state.phase = .answeringMenu
            return [.answerMenuQuestion(makeRequest(now: now), question: question, menu: menu)]
        }
    }

    private mutating func speakMenu(now: Int64) -> [TaskEffect] {
        guard let menu = state.menu else { return [.rejected(.unsupportedState)] }
        let text: String
        if menu.sections.isEmpty { text = "No menu text is readable. Please reposition the camera." }
        else {
            let section = menu.sections[state.sectionIndex]
            text = section.title + ". " + section.items.joined(separator: ". ")
        }
        return [.speak(text: text, priority: 4), .awaitMenuCommand(makeRequest(now: now, confirmation: true))]
    }
}
