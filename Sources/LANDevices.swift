import Foundation
import CoreFoundation

struct LANDeviceSummary: Identifiable {
    let id: String, label: String, browser: String, view: String, syncStatus: String
    let partId: String?
    let capabilities: [String]
    let connected: Bool, applied: Bool
    let assignmentRevision: Int, scoreRevision: Int
    let rttMs: Double?, clockOffsetMs: Double?, audioOutputDelayMs: Double?, freshnessMs: Double?
    let clockStatus: String
    let clockJitterMs: Double?, clockProbeAgeMs: Double?
}

// Confined to LANBroadcast's socket queue; credentials never enter host summaries.
final class LANDeviceRegistry {
    static let views: Set<String> = ["chords", "numbers", "staff", "tab", "metronome"]
    enum Failure: Error { case invalid, forbidden, tooLarge }
    private struct Client {
        var id: String, key: String, label: String, browser: String
        var capabilities: [String]
        var partId: String?
        var view = "chords", sync = "calibrating"
        var revision = 1, appliedRevision = 0, scoreRevision = 0
        var connectionId: UUID?
        var lastSeen: Double, rateWindow = 0.0
        var rateCount = 0
        var rtt: Double?, offset: Double?, delay: Double?, freshness: Double?
        var clockStatus = "unavailable"
        var clockJitter: Double?, clockProbeAge: Double?
    }
    private var clients: [String: Client] = [:]
    private var orderedIDs: [String] = []
    private var parts: [(String, Set<String>)] = []
    private var scoreRevision = 0
    private let clock: () -> Double
    init(clock: @escaping () -> Double = { ProcessInfo.processInfo.systemUptime }) { self.clock = clock }
    func setParts(_ score: ScoreIR?, revision: Int) {
        parts = score?.parts.map { part in
            var views: Set<String> = ["staff", "metronome"]
            if !part.chords.isEmpty { views.formUnion(["chords", "numbers"]) }
            if part.staves.contains(where: { !$0.tuning.isEmpty && $0.events.contains(where: { $0.notes.contains(where: { $0.string != nil }) }) }) { views.insert("tab") }
            return (part.id, views)
        } ?? []
        scoreRevision = revision
        for id in orderedIDs {
            guard var item = clients[id] else { continue }
            if !parts.contains(where: { $0.0 == item.partId && $0.1.contains(item.view) }) {
                item.partId = parts.first?.0; item.view = defaultView(item.partId, item.capabilities); item.revision += 1
            }
            item.scoreRevision = revision; item.appliedRevision = 0; clients[id] = item
        }
    }
    private func defaultView(_ part: String?, _ caps: [String]) -> String {
        let available = parts.first(where: { $0.0 == part })?.1 ?? Self.views
        return ["chords", "staff", "metronome"].first { available.contains($0) && caps.contains($0) } ?? "metronome"
    }
    private func authenticate(_ id: String, _ key: Any?) throws -> Client {
        guard let item = clients[id], let supplied = key as? String, supplied.utf8.count == item.key.utf8.count else { throw Failure.forbidden }
        var difference: UInt8 = 0
        for (a, b) in zip(supplied.utf8, item.key.utf8) { difference |= a ^ b }
        guard difference == 0 else { throw Failure.forbidden }; return item
    }
    private static func text(_ value: Any?, limit: Int = 128) throws -> String {
        guard let text = value as? String, !text.isEmpty, text.count <= limit,
              !text.unicodeScalars.contains(where: { $0.value < 32 }) else { throw Failure.invalid }; return text
    }
    private static func integer(_ value: Any?) throws -> Int {
        guard let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(), number.doubleValue.isFinite,
              number.doubleValue >= 0, number.doubleValue < Double(Int.max), number.doubleValue.rounded() == number.doubleValue else { throw Failure.invalid }
        return number.intValue
    }
    private static func metric(_ value: Any?, limit: Double, nonnegative: Bool = false) throws -> Double? {
        if value == nil || value is NSNull { return nil }
        guard let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(), number.doubleValue.isFinite,
              abs(number.doubleValue) <= limit, !nonnegative || number.doubleValue >= 0 else { throw Failure.invalid }; return number.doubleValue
    }
    func register(_ payload: [String: Any], browser: String) throws -> [String: Any] {
        guard Set(payload.keys).isSubset(of: ["clientId", "resumeKey", "label", "capabilities"]) else { throw Failure.invalid }
        let id = try Self.text(payload["clientId"])
        guard id.range(of: "^[A-Za-z0-9_.:-]{1,128}$", options: .regularExpression) != nil,
              let caps = (payload["capabilities"] ?? Array(Self.views).sorted()) as? [String], !caps.isEmpty, caps.count <= 5,
              caps.allSatisfy({ Self.views.contains($0) }) else { throw Failure.invalid }
        expire(); var item: Client
        if clients[id] != nil { item = try authenticate(id, payload["resumeKey"]); item.capabilities = caps; item.browser = String(browser.prefix(256)) }
        else {
            guard clients.count < 256 else { throw Failure.invalid }
            let key = (UUID().uuidString + UUID().uuidString).replacingOccurrences(of: "-", with: "").lowercased()
            item = Client(id: id, key: key, label: try Self.text(payload["label"] ?? "浏览器 " + String(id.suffix(6))),
                          browser: String(browser.prefix(256)), capabilities: caps, partId: parts.first?.0,
                          view: defaultView(parts.first?.0, caps), scoreRevision: scoreRevision, lastSeen: clock())
            orderedIDs.append(id)
        }
        item.lastSeen = clock(); clients[id] = item
        var result = assignment(item); result["resumeKey"] = item.key; result["serverCapabilities"] = ["clockDiagnosticsV1"]; return result
    }
    func attach(_ id: String, key: String, connection: UUID) throws {
        var item = try authenticate(id, key); item.connectionId = connection; item.lastSeen = clock(); clients[id] = item
    }
    func disconnect(_ id: String, connection: UUID) {
        guard var item = clients[id], item.connectionId == connection else { return }
        item.connectionId = nil; item.sync = "disconnected"; clients[id] = item
    }
    func telemetry(_ payload: [String: Any]) throws -> [String: Any] {
        guard Set(payload.keys).isSubset(of: ["clientId", "resumeKey", "appliedAssignmentRevision", "scoreRevision", "rttMs", "clockOffsetMs", "audioOutputDelayMs", "freshnessMs", "syncStatus", "clockDiagnostics"]) else { throw Failure.invalid }
        let id = try Self.text(payload["clientId"]); var item = try authenticate(id, payload["resumeKey"]); let now = clock()
        if now - item.rateWindow >= 1 { item.rateWindow = now; item.rateCount = 0 }
        item.rateCount += 1; clients[id] = item; guard item.rateCount <= 10 else { throw Failure.invalid }
        let rtt = try Self.metric(payload["rttMs"], limit: 60_000, nonnegative: true)
        let freshness = try Self.metric(payload["freshnessMs"], limit: 3_600_000, nonnegative: true)
        let offset = try Self.metric(payload["clockOffsetMs"], limit: 1e12), delay = try Self.metric(payload["audioOutputDelayMs"], limit: 10_000)
        let applied = try Self.integer(payload["appliedAssignmentRevision"] ?? 0), score = try Self.integer(payload["scoreRevision"] ?? 0)
        let sync = payload["syncStatus"] ?? "calibrating"
        guard var state = sync as? String, ["calibrating", "synchronized", "stale", "disabled"].contains(state) else { throw Failure.invalid }
        var clockStatus = "unavailable"
        var jitter: Double? = nil, probeAge: Double? = nil
        if let raw = payload["clockDiagnostics"] {
            guard let diagnostics = raw as? [String: Any], Set(diagnostics.keys) == Set(["version", "status", "jitterMs", "probeAgeMs"]),
                  try Self.integer(diagnostics["version"]) == 1, let status = diagnostics["status"] as? String,
                  ["valid", "calibrating", "stale"].contains(status) else { throw Failure.invalid }
            clockStatus = status
            jitter = try Self.metric(diagnostics["jitterMs"], limit: 1e12, nonnegative: true)
            probeAge = try Self.metric(diagnostics["probeAgeMs"], limit: 3_600_000, nonnegative: true)
            if status == "valid" { guard let age = probeAge, age < 10_000, offset != nil, rtt != nil else { throw Failure.invalid } }
            else if state == "synchronized" { state = status == "calibrating" ? "calibrating" : "stale" }
        }
        if applied == item.revision && score == item.scoreRevision { item.appliedRevision = applied }
        item.rtt = rtt; item.offset = offset; item.delay = delay; item.freshness = freshness
        item.clockStatus = clockStatus; item.clockJitter = jitter; item.clockProbeAge = probeAge
        item.sync = state; item.lastSeen = now; clients[id] = item; return assignment(item)
    }
    func assign(_ id: String, partId: String?, view: String, label: String? = nil) throws {
        guard var item = clients[id], Self.views.contains(view), item.capabilities.contains(view) else { throw Failure.invalid }
        guard parts.isEmpty ? partId == nil : parts.contains(where: { $0.0 == partId && $0.1.contains(view) }) else { throw Failure.invalid }
        if let label { item.label = try Self.text(label) }
        if item.partId != partId || item.view != view { item.partId = partId; item.view = view; item.revision += 1; item.appliedRevision = 0 }; clients[id] = item
    }
    func assignment(_ id: String) -> [String: Any]? { clients[id].map { assignment($0) } }
    private func assignment(_ item: Client) -> [String: Any] {
        ["protocolVersion": 2, "clientId": item.id, "label": item.label, "partId": item.partId as Any? ?? NSNull(),
         "view": item.view, "assignmentRevision": item.revision, "scoreRevision": item.scoreRevision]
    }
    func summaries() -> [LANDeviceSummary] {
        expire(); let now = clock()
        return orderedIDs.compactMap { id in
            guard let item = clients[id] else { return nil }; let fresh = now - item.lastSeen < 6
            return LANDeviceSummary(id: id, label: item.label, browser: item.browser, view: item.view, syncStatus: fresh ? item.sync : "stale",
                                    partId: item.partId, capabilities: item.capabilities, connected: item.connectionId != nil && fresh,
                                    applied: item.appliedRevision == item.revision, assignmentRevision: item.revision, scoreRevision: item.scoreRevision,
                                    rttMs: item.rtt, clockOffsetMs: item.offset, audioOutputDelayMs: item.delay, freshnessMs: item.freshness,
                                    clockStatus: fresh ? item.clockStatus : "stale", clockJitterMs: item.clockJitter,
                                    clockProbeAgeMs: item.clockProbeAge.map { $0 + max(0, now - item.lastSeen) * 1000 })
        }
    }
    private func expire() {
        let now = clock()
        orderedIDs.removeAll { id in
            guard let item = clients[id] else { return true }
            if item.connectionId == nil && now - item.lastSeen > 86_400 { clients.removeValue(forKey: id); return true }; return false
        }
    }
}

struct LANRequest {
    let method: String, path: String
    let headers: [String: String], query: [String: String]
    let body: Data, received: Double
    static func parse(_ data: Data) throws -> LANRequest? {
        guard let boundary = data.range(of: Data("\r\n\r\n".utf8)) else {
            guard data.count <= 8192 else { throw LANDeviceRegistry.Failure.invalid }; return nil
        }
        guard boundary.upperBound <= 8192, let header = String(data: data.prefix(boundary.lowerBound), encoding: .ascii) else { throw LANDeviceRegistry.Failure.invalid }
        let lines = header.components(separatedBy: "\r\n"), first = lines[0].components(separatedBy: " ")
        guard first.count == 3, ["HTTP/1.0", "HTTP/1.1"].contains(first[2]), first[1].hasPrefix("/"), !first[1].hasPrefix("//"),
              !first[1].contains("#"), !first[1].contains("\\"), first[1].unicodeScalars.allSatisfy({ (33...126).contains($0.value) }),
              first[1].range(of: "%(?![0-9a-fA-F]{2})", options: .regularExpression) == nil else { throw LANDeviceRegistry.Failure.invalid }
        var headers: [String: String] = [:]
        for line in lines.dropFirst() {
            guard let colon = line.firstIndex(of: ":") else { throw LANDeviceRegistry.Failure.invalid }
            let name = String(line[..<colon]).lowercased(), raw = String(line[line.index(after: colon)...])
            guard name.range(of: "^[!#$%&'*+.^_`|~0-9a-z-]+$", options: .regularExpression) != nil, headers[name] == nil,
                  !raw.unicodeScalars.contains(where: { $0.value < 32 && $0.value != 9 || $0.value == 127 }) else { throw LANDeviceRegistry.Failure.invalid }
            headers[name] = raw.trimmingCharacters(in: .whitespaces)
        }
        let lengthText = headers["content-length"] ?? "0"
        guard headers["transfer-encoding"] == nil, first[2] != "HTTP/1.1" || !(headers["host"] ?? "").isEmpty,
              lengthText.range(of: "^(0|[1-9][0-9]{0,8})$", options: .regularExpression) != nil,
              let length = Int(lengthText) else { throw LANDeviceRegistry.Failure.invalid }
        guard length <= 8192 else { throw LANDeviceRegistry.Failure.tooLarge }
        guard first[0] == "POST" ? length > 0 : length == 0 else { throw LANDeviceRegistry.Failure.invalid }
        let body = data.suffix(from: boundary.upperBound); guard body.count <= length else { throw LANDeviceRegistry.Failure.invalid }
        if body.count < length { return nil }
        guard let target = URLComponents(string: "http://localhost" + first[1]) else { throw LANDeviceRegistry.Failure.invalid }
        var query: [String: String] = [:]
        for item in target.queryItems ?? [] { guard query[item.name] == nil else { throw LANDeviceRegistry.Failure.invalid }; query[item.name] = item.value ?? "" }
        return LANRequest(method: first[0], path: target.percentEncodedPath, headers: headers, query: query, body: Data(body), received: ProcessInfo.processInfo.systemUptime * 1000)
    }
    static func jsonObject(_ data: Data) throws -> [String: Any] {
        guard data.count <= 8192, let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { throw LANDeviceRegistry.Failure.invalid }
        let bytes = Array(data); var index = 0, depth = 0, keys = Set<String>()
        while index < bytes.count {
            let byte = bytes[index]
            if byte == 34 {
                let start = index; index += 1
                while index < bytes.count { if bytes[index] == 92 { index += 2; continue }; if bytes[index] == 34 { break }; index += 1 }
                guard index < bytes.count else { throw LANDeviceRegistry.Failure.invalid }
                let end = index + 1; var next = end
                while next < bytes.count && [9, 10, 13, 32].contains(bytes[next]) { next += 1 }
                if depth == 1 && next < bytes.count && bytes[next] == 58 {
                    let quoted = Data([91] + Array(bytes[start..<end]) + [93])
                    guard let key = (try JSONSerialization.jsonObject(with: quoted) as? [String])?.first, keys.insert(key).inserted else { throw LANDeviceRegistry.Failure.invalid }
                }
            } else if byte == 123 || byte == 91 { depth += 1; guard depth <= 16 else { throw LANDeviceRegistry.Failure.invalid } }
            else if byte == 125 || byte == 93 { depth -= 1 }
            index += 1
        }
        return object
    }
}
