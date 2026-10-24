import Foundation

enum LiveServiceConfigurationError: Error, Equatable, LocalizedError {
    case missing(String)
    case invalid(String)

    var errorDescription: String? {
        switch self {
        case .missing(let key): return "Online services are unavailable: \(key) is missing from the app configuration."
        case .invalid(let key): return "Online services are unavailable: \(key) is invalid in the app configuration."
        }
    }
}

/// Public client settings only. Provider signing keys and other server secrets never belong here.
struct LiveServiceConfiguration {
    let convexURL: URL
    let auth0Issuer: URL
    let auth0Domain: String
    let auth0NativeClientID: String
    let auth0CallbackURL: URL

    static func load(from bundle: Bundle = .main) throws -> LiveServiceConfiguration {
        let values = bundle.infoDictionary ?? [:]
        let registeredSchemes = (values["CFBundleURLTypes"] as? [[String: Any]] ?? [])
            .flatMap { $0["CFBundleURLSchemes"] as? [String] ?? [] }
        return try LiveServiceConfiguration(values: values, registeredSchemes: Set(registeredSchemes))
    }

    // Kept independent of Bundle for deterministic validation tests.
    init(values: [String: Any], registeredSchemes: Set<String>) throws {
        func required(_ key: String) throws -> String {
            guard let raw = values[key] as? String,
                  !raw.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                throw LiveServiceConfigurationError.missing(key)
            }
            let value = raw.trimmingCharacters(in: .whitespacesAndNewlines)
            guard value == raw, !value.contains("$("), !value.contains("YOUR_"),
                  !value.contains("TBD") else {
                throw LiveServiceConfigurationError.invalid(key)
            }
            return value
        }

        func origin(_ key: String, trailingSlash: Bool) throws -> URL {
            let value = try required(key)
            guard let url = URL(string: value), let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
                  components.scheme?.lowercased() == "https", components.host != nil,
                  components.user == nil, components.password == nil,
                  components.query == nil, components.fragment == nil,
                  components.path.isEmpty || components.path == "/",
                  (!trailingSlash || value.hasSuffix("/")) else {
                throw LiveServiceConfigurationError.invalid(key)
            }
            return url
        }

        convexURL = try origin("ReflexConvexURL", trailingSlash: false)
        auth0Issuer = try origin("ReflexAuth0Issuer", trailingSlash: true)
        guard auth0Issuer.port == nil, let host = auth0Issuer.host else {
            throw LiveServiceConfigurationError.invalid("ReflexAuth0Issuer")
        }
        auth0Domain = host

        let clientID = try required("ReflexAuth0NativeClientID")
        guard clientID.count <= 256, clientID.utf8.allSatisfy({
            ($0 >= 65 && $0 <= 90) || ($0 >= 97 && $0 <= 122) ||
            ($0 >= 48 && $0 <= 57) || $0 == 45 || $0 == 95
        }) else {
            throw LiveServiceConfigurationError.invalid("ReflexAuth0NativeClientID")
        }
        auth0NativeClientID = clientID

        let callback = try required("ReflexAuth0CallbackURL")
        guard let url = URL(string: callback),
              let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
              let scheme = components.scheme?.lowercased(),
              scheme != "http", scheme != "https", registeredSchemes.contains(scheme),
              components.host != nil, components.user == nil, components.password == nil,
              components.query == nil, components.fragment == nil,
              !components.path.isEmpty else {
            throw LiveServiceConfigurationError.invalid("ReflexAuth0CallbackURL")
        }
        auth0CallbackURL = url
    }
}
