import Foundation

@MainActor
final class UserPreferences: ObservableObject {
    enum Hand: String, CaseIterable { case left, right }
    enum PhonePosition: String, CaseIterable {
        case handheld, mounted
        var label: String { self == .handheld ? "Held in my other hand" : "Mounted" }
    }
    private let defaults: UserDefaults
    @Published var guidingHand: Hand { didSet { defaults.set(guidingHand.rawValue, forKey: "guidingHand") } }
    @Published var phonePosition: PhonePosition { didSet { defaults.set(phonePosition.rawValue, forKey: "phonePosition") } }
    @Published var setupComplete: Bool { didSet { defaults.set(setupComplete, forKey: "setupComplete") } }
    @Published var captions: Bool { didSet { defaults.set(captions, forKey: "captions") } }
    @Published var simulatedCalibrationComplete: Bool { didSet { defaults.set(simulatedCalibrationComplete, forKey: "simulatedCalibrationComplete") } }
    // A live calibration is bound to both confirmed peripheral IDs and firmware versions.
    @Published private(set) var calibratedPairSignature: String?
    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        guidingHand = Hand(rawValue: defaults.string(forKey: "guidingHand") ?? "") ?? .right
        phonePosition = PhonePosition(rawValue: defaults.string(forKey: "phonePosition") ?? "") ?? .handheld
        setupComplete = defaults.bool(forKey: "setupComplete")
        captions = defaults.object(forKey: "captions") == nil ? true : defaults.bool(forKey: "captions")
        simulatedCalibrationComplete = defaults.bool(forKey: "simulatedCalibrationComplete")
        calibratedPairSignature = defaults.string(forKey: "calibratedPairSignature")
    }
    func completeCalibration(pairSignature: String) {
        calibratedPairSignature = pairSignature
        defaults.set(pairSignature, forKey: "calibratedPairSignature")
    }
    func invalidateCalibration() {
        calibratedPairSignature = nil
        defaults.removeObject(forKey: "calibratedPairSignature")
    }
}
