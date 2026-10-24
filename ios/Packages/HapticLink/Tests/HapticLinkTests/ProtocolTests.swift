import XCTest
@testable import HapticLink

final class ProtocolTests: XCTestCase {
    func testExactTenByteLittleEndianCommandAndRoundTrip() throws {
        let command = try HapticCommand(version: 3, sequence: 0x1234, generation: 0xabcd,
                                        patternID: 6, intensity: 88, rateHz: 9, ttlMs: 600)
        XCTAssertEqual(command.data, Data([3, 0x34, 0x12, 0xcd, 0xab, 6, 88, 9, 0x58, 2]))
        XCTAssertEqual(command.data.count, 10)
        XCTAssertEqual(try HapticCommand(data: command.data, expectedVersion: 3), command)
    }

    func testCommandRejectsMalformedVersionAndReservedPatternIDs() throws {
        for length in [0, 5, 9, 11, 100] {
            XCTAssertThrowsError(try HapticCommand(data: Data(repeating: 0, count: length), expectedVersion: 1))
        }
        XCTAssertThrowsError(try HapticCommand(data: Data([2, 0, 0, 0, 0, 1, 1, 1, 1, 0]), expectedVersion: 1))
        for id: UInt8 in [10, 100, 254] {
            XCTAssertThrowsError(try HapticCommand(version: 1, sequence: 0, generation: 0, patternID: id, intensity: 0, rateHz: 0, ttlMs: 0))
        }
        for id: UInt8 in [0, 1, 9, 255] {
            XCTAssertNoThrow(try HapticCommand(version: 1, sequence: .max, generation: .max, patternID: id, intensity: .max, rateHz: .max, ttlMs: .max))
        }
    }

    func testFiveByteStatusDistinguishesFirmwareFromPacketVersion() throws {
        let status = try BandStatus(data: Data([83, 9, 0x34, 0x12, 0xff]), supportedFirmwareVersions: [9])
        XCTAssertEqual(status.batteryPct, 83); XCTAssertEqual(status.firmwareVersion, 9)
        XCTAssertEqual(status.lastSequence, 0x1234); XCTAssertEqual(status.faults, 255)
        for length in [0, 4, 6, 10] {
            XCTAssertThrowsError(try BandStatus(data: Data(repeating: 0, count: length), supportedFirmwareVersions: [0]))
        }
        XCTAssertThrowsError(try BandStatus(data: Data([101, 9, 0, 0, 0]), supportedFirmwareVersions: [9]))
        XCTAssertThrowsError(try BandStatus(data: Data([10, 9, 0, 0, 0]), supportedFirmwareVersions: [1]))
    }

    func testOpaqueContextConversionRejectsTruncationAndEmptyIDs() throws {
        let context = try HapticContext(sessionID: "session/string", taskID: "task/string", generation: 65_535)
        XCTAssertEqual(context.generation, .max)
        XCTAssertThrowsError(try HapticContext(sessionID: "s", taskID: "t", generation: 65_536))
        XCTAssertThrowsError(try HapticContext(sessionID: "s", taskID: "t", generation: UInt64.max))
        XCTAssertThrowsError(try HapticContext(sessionID: "", taskID: "t", generation: 1))
    }
}
