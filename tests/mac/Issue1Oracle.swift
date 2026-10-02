import Foundation

// Core project and LAN fixture runner: no SwiftUI, WebKit, or network listener.
// Minimal legacy boundary types keep it usable with swiftc on macOS/Linux.
struct SongPosition: Comparable {
    let bar: Int, beat: Int, division: Int, tick: Int
    static func < (a: SongPosition, b: SongPosition) -> Bool { [a.bar, a.beat, a.division, a.tick].lexicographicallyPrecedes([b.bar, b.beat, b.division, b.tick]) }
}
struct ChordEvent { let id: Int; let position: SongPosition; let symbol: String }

@main struct Issue1Oracle {
    static func require(_ condition: @autoclosure () throws -> Bool, _ text: String) throws {
        if try condition() == false { throw ScoreValidationError.invalid("Fixture failed: " + text) }
    }
    static func rejected(_ action: () throws -> Void) throws {
        do { try action() } catch { return }; throw ScoreValidationError.invalid("Expected invalid input to be rejected")
    }
    static func main() throws {
        let score = try ScoreIR.decodeValidated(Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1])))
        let project = try MacScoreProject(score: score)
        let encoded = try project.encodedValidated()
        let decoded = try MacScoreProject.decodeValidated(encoded)
        try require(decoded.score == score, "complete score round trip")
        try require(decoded.legacyChords.isEmpty, "notes are not synthesized as chords")
        var raw = try JSONSerialization.jsonObject(with: encoded) as! [String: Any]
        raw["selectedPartId"] = "does-not-exist"
        try rejected { _ = try MacScoreProject.decodeValidated(JSONSerialization.data(withJSONObject: raw)) }
        let v1 = Data(#"{"schemaVersion":1,"name":"Legacy","bars":16,"bpm":120,"meter":4,"events":[],"forcedKey":null,"manualSections":[],"detectChanges":true,"originalKey":null,"loop":null}"#.utf8)
        let old = try MacScoreProject.decodeValidated(v1)
        let oldRaw = try JSONSerialization.jsonObject(with: old.encodedValidated()) as! [String: Any]
        try require(oldRaw["schemaVersion"] as? Int == 1 && oldRaw["score"] == nil, "v1 schema retained")
        try rejected { _ = try MacScoreProject.decodeValidated(Data(#"{"name":"a","name":"b"}"#.utf8)) }
        try rejected { _ = try MacScoreProject.decodeValidated(Data(#"{"key":{"a":1,"\u0061":2}}"#.utf8)) }
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("chordcue-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: folder) }
        let destination = folder.appendingPathComponent("project.json")
        try project.save(destination); try require(try MacScoreProject.load(destination).score == score, "atomic saved project")
        let unsupported = Data(#"{"schemaVersion":99}"#.utf8)
        try unsupported.write(to: destination)
        try rejected { try project.save(destination) }
        try require(try Data(contentsOf: destination) == unsupported, "future destination preserved")

        var now = 100.0
        let registry = LANDeviceRegistry(clock: { now })
        registry.setParts(score, revision: 7)
        let first = try registry.register(["clientId": "first", "label": "Phone A"], browser: "Fixture")
        let second = try registry.register(["clientId": "second", "label": "Phone B"], browser: "Fixture")
        let key = first["resumeKey"] as! String
        try require(first["protocolVersion"] as? Int == 2 && first["scoreRevision"] as? Int == 7, "protocol v2 registration")
        try rejected { _ = try registry.register(["clientId": "first", "resumeKey": "wrong"], browser: "Fixture") }
        let connection = UUID(); try registry.attach("first", key: key, connection: connection)
        try registry.assign("first", partId: score.parts[0].id, view: "metronome")
        let assignment = registry.assignment("first")!
        try require(registry.assignment("second")!["view"] as? String == second["view"] as? String, "independent assignments")
        _ = try registry.telemetry(["clientId": "first", "resumeKey": key, "appliedAssignmentRevision": assignment["assignmentRevision"]!,
                                    "scoreRevision": 6, "rttMs": 8.0, "clockOffsetMs": -2.0, "syncStatus": "synchronized"])
        try require(!registry.summaries()[0].applied, "stale score ACK ignored")
        _ = try registry.telemetry(["clientId": "first", "resumeKey": key, "appliedAssignmentRevision": assignment["assignmentRevision"]!,
                                    "scoreRevision": 7, "rttMs": 8.0, "clockOffsetMs": -2.0, "syncStatus": "synchronized"])
        try require(registry.summaries()[0].applied && registry.summaries()[0].connected, "exact assignment ACK applied")
        try rejected { _ = try registry.telemetry(["clientId": "first", "resumeKey": key, "rttMs": -1]) }
        try require(first["serverCapabilities"] as? [String] == ["clockDiagnosticsV1"], "clock diagnostics negotiated")
        try require(registry.summaries()[0].clockStatus == "unavailable", "legacy telemetry retained")
        _ = try registry.telemetry(["clientId": "first", "resumeKey": key, "rttMs": 18.0, "clockOffsetMs": 300_000_000.0,
                                    "clockDiagnostics": ["version": 1, "status": "valid", "jitterMs": 0.5, "probeAgeMs": 2.0]])
        try require(registry.summaries()[0].clockStatus == "valid" && registry.summaries()[0].clockJitterMs == 0.5,
                    "valid diagnostics distinct from origin mapping")
        try rejected { _ = try registry.telemetry(["clientId": "first", "resumeKey": key, "rttMs": 18.0, "clockOffsetMs": 300_000_000.0,
                                                   "clockDiagnostics": ["version": 1, "status": "valid", "jitterMs": 0.5, "probeAgeMs": 10_000.0]]) }
        now += 7; try require(!registry.summaries()[0].connected && registry.summaries()[0].syncStatus == "stale", "health expires")
        registry.disconnect("first", connection: UUID()); now = 100
        try require(registry.summaries()[0].connected, "old connection cannot disconnect replacement")
        registry.disconnect("first", connection: connection); try require(!registry.summaries()[0].connected, "matching disconnect")
        let body = Data(#"{"clientId":"first"}"#.utf8)
        let header = Data("POST /join/token/register HTTP/1.1\r\nHost: localhost:1234\r\nContent-Length: \(body.count)\r\n\r\n".utf8)
        try require(try LANRequest.parse(header)?.path == nil, "fragmented POST waits")
        var complete = header; complete.append(body)
        let request = try LANRequest.parse(complete)!
        try require(try LANRequest.jsonObject(request.body)["clientId"] as? String == "first", "POST JSON parsed")
        try rejected { _ = try LANRequest.jsonObject(Data(#"{"clientId":"a","\u0063lientId":"b"}"#.utf8)) }
        try rejected { _ = try LANRequest.parse(Data("GET / HTTP/1.1\r\nHost: a\r\nHost: b\r\n\r\n".utf8)) }
        try rejected { _ = try LANRequest.parse(Data("GET / HTTP/1.1\r\nHost: a\r\nContent-Length: 1\r\n\r\nx".utf8)) }
        print("Mac project + LAN protocol fixtures passed")
    }
}
