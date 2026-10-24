import Foundation
import Targets
import Tasks

enum R3EvidenceError: Error, Equatable, LocalizedError {
    case malformedResponse, mismatchedFrame, providerUnavailable, lowConfidence, unsafeControl
    case unsupportedTask, missingFrame, staleFrame, wrongContext

    var errorDescription: String? {
        switch self {
        case .malformedResponse: return "The scene response could not be read reliably."
        case .mismatchedFrame: return "The scene response belongs to a different image. Try again."
        case .providerUnavailable: return "Scene interpretation is unavailable. Try again later."
        case .lowConfidence: return "The control is not certain enough for physical guidance."
        case .unsafeControl: return "This control has not been established as safe for guidance."
        case .unsupportedTask: return "This scene reader does not support that task."
        case .missingFrame: return "A current camera image is required."
        case .staleFrame: return "The camera image or request has expired. Try again."
        case .wrongContext: return "The assistance session changed. Start the request again."
        }
    }
}

enum R3TargetMapping {
    static func validateRecognition(_ model: Targets.ScreenModel, frameID: String, capturedAtUTCms: Int64) throws {
        guard model.frameId == frameID, model.capturedAtMs == capturedAtUTCms else {
            throw R3EvidenceError.mismatchedFrame
        }
        guard model.schemaVersion == 3, !frameID.isEmpty, capturedAtUTCms >= 0,
              model.source == .visionModel, model.evidence?.live == true,
              model.coordinateSystem == .normalizedImageTopLeft,
              model.controls.count <= 100,
              model.controls.allSatisfy({ !$0.id.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }),
              Set(model.controls.map(\.id)).count == model.controls.count else {
            throw R3EvidenceError.malformedResponse
        }
        guard model.availability != .unavailable else { throw R3EvidenceError.providerUnavailable }
        guard let image = model.image, image.widthPx > 0, image.heightPx > 0,
              image.orientationApplied, (1...8).contains(image.exifOrientation) else {
            throw R3EvidenceError.malformedResponse
        }
    }

    static func resolve(_ query: String, kind: TaskKind, model: Targets.ScreenModel,
                        transform: FrameTransform) throws -> ResolvedTarget {
        guard kind == .button || kind == .kiosk else { throw R3EvidenceError.unsupportedTask }
        let result = TargetSelector().select(query, in: model)
        guard result.status == .selected, let selected = result.target,
              let element = model.controls.first(where: { $0.id == selected.targetId }) else {
            if result.status == .providerUnavailable { throw R3EvidenceError.providerUnavailable }
            throw R3EvidenceError.lowConfidence
        }
        let target = try target(element, model: model, transform: transform)
        guard (kind == .button && target.kind == .button) ||
                (kind == .kiosk && target.kind == .screenControl) else {
            throw R3EvidenceError.unsupportedTask
        }
        return target
    }

    static func taskScreen(_ model: Targets.ScreenModel, transform: FrameTransform) throws -> TaskScreen {
        guard model.panelType == .orderingKiosk || model.panelType == .atmScreen else {
            throw R3EvidenceError.unsupportedTask
        }
        // Payment handoff does not require a selectable control or authorize any movement.
        if model.isPaymentScreen {
            return TaskScreen(id: model.frameId, frameID: model.frameId, title: "Payment",
                              controls: [], isPaymentScreen: true)
        }
        let controls = try model.controls.map { try target($0, model: model, transform: transform) }
        guard controls.allSatisfy({ $0.kind == .screenControl }) else { throw R3EvidenceError.unsupportedTask }
        return TaskScreen(id: model.frameId, frameID: model.frameId, title: "Screen", controls: controls)
    }

    static func menu(_ model: Targets.ScreenModel) throws -> MenuContent {
        guard model.panelType == .wallMenu || model.panelType == .orderingKiosk else {
            throw R3EvidenceError.unsupportedTask
        }
        let readable = model.controls.filter {
            $0.legibility == .clear && !$0.label.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        }
        var names = model.sections
        for item in readable {
            let name = item.section ?? "Menu"
            if !names.contains(name) { names.append(name) }
        }
        return MenuContent(sections: names.compactMap { name in
            let items = readable.filter { ($0.section ?? "Menu") == name }.map { item in
                item.label + (item.price.map { ", " + $0 } ?? "")
            }
            return items.isEmpty ? nil : MenuSection(title: name, items: items)
        }, isBlurry: readable.count != model.controls.count, isCutOff: model.availability == .partial)
    }

    private static func target(_ element: InteractiveElement, model: Targets.ScreenModel,
                               transform: FrameTransform) throws -> ResolvedTarget {
        guard let confidence = element.confidence, confidence.isFinite, (0.6...1).contains(confidence),
              element.legibility == .clear,
              !element.label.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              let box = element.region, box.isValid, transform.isValid else {
            throw R3EvidenceError.lowConfidence
        }
        guard !model.isPaymentScreen, element.safety == .ordinary else { throw R3EvidenceError.unsafeControl }
        let kind: TargetKind
        switch element.type {
        case .button: kind = .button
        case .screenControl, .menuItem: kind = .screenControl
        default: throw R3EvidenceError.unsupportedTask
        }
        let region = ImageRegion(x: transform.originX + box.x * transform.scaleX,
                                 y: transform.originY + box.y * transform.scaleY,
                                 width: box.width * transform.scaleX, height: box.height * transform.scaleY)
        guard region.isValid else { throw R3EvidenceError.malformedResponse }
        return ResolvedTarget(id: element.id, kind: kind, label: element.label, region: region,
                              frameID: model.frameId, safety: .ordinary,
                              sourceScreenID: kind == .screenControl ? model.frameId : nil)
    }
}
