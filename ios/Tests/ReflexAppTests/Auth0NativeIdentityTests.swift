import Foundation
import XCTest
@testable import Reflex

final class Auth0NativeIdentityTests: XCTestCase {
    private let values: [String: Any] = [
        "ReflexConvexURL": "https://sample.convex.cloud",
        "ReflexAuth0Issuer": "https://sample.auth0.com/",
        "ReflexAuth0NativeClientID": "Native_Client_1",
        "ReflexAuth0CallbackURL": "com.example.reflex://sample.auth0.com/ios/com.example.reflex/callback",
    ]

    func testValidPublicConfiguration() throws {
        let config = try LiveServiceConfiguration(
            values: values, registeredSchemes: ["com.example.reflex"]
        )
        XCTAssertEqual(config.convexURL.absoluteString, "https://sample.convex.cloud")
        XCTAssertEqual(config.auth0Issuer.absoluteString, "https://sample.auth0.com/")
        XCTAssertEqual(config.auth0Domain, "sample.auth0.com")
        XCTAssertEqual(config.auth0NativeClientID, "Native_Client_1")
    }

    func testMissingAndPlaceholderValuesFailClearly() throws {
        var missing = values
        missing.removeValue(forKey: "ReflexAuth0NativeClientID")
        XCTAssertThrowsError(try LiveServiceConfiguration(
            values: missing, registeredSchemes: ["com.example.reflex"]
        )) { error in
            XCTAssertEqual(error as? LiveServiceConfigurationError,
                           .missing("ReflexAuth0NativeClientID"))
        }

        var placeholder = values
        placeholder["ReflexAuth0NativeClientID"] = "YOUR_NATIVE_CLIENT_ID"
        XCTAssertThrowsError(try LiveServiceConfiguration(
            values: placeholder, registeredSchemes: ["com.example.reflex"]
        )) { error in
            XCTAssertEqual(error as? LiveServiceConfigurationError,
                           .invalid("ReflexAuth0NativeClientID"))
        }
    }

    func testCallbackSchemeMustBeRegistered() throws {
        XCTAssertThrowsError(try LiveServiceConfiguration(
            values: values, registeredSchemes: []
        )) { error in
            XCTAssertEqual(error as? LiveServiceConfigurationError,
                           .invalid("ReflexAuth0CallbackURL"))
        }
    }

    func testIssuerMustMatchExactHTTPSOrigin() throws {
        var invalid = values
        invalid["ReflexAuth0Issuer"] = "http://sample.auth0.com/"
        XCTAssertThrowsError(try LiveServiceConfiguration(
            values: invalid, registeredSchemes: ["com.example.reflex"]
        )) { error in
            XCTAssertEqual(error as? LiveServiceConfigurationError,
                           .invalid("ReflexAuth0Issuer"))
        }
    }
}
