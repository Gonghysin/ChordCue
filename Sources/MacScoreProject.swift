import Foundation
import CoreFoundation

// The same project v1/v2/v3 envelope as windows/chordcue/project.py. ScoreIR remains
// authoritative; explicit source chords are a display adapter, not saved notes.
struct MacScoreProject {
    var score: ScoreIR?
    var selectedPartId: String?
    private var envelope: [String: Any]
    static let maximumBytes = 32 * 1024 * 1024
    var name: String { envelope["name"] as? String ?? "未命名" }
    var bars: Int { envelope["bars"] as? Int ?? 16 }
    var bpm: Double { (envelope["bpm"] as? NSNumber)?.doubleValue ?? 120 }
    var meter: Int { envelope["meter"] as? Int ?? 4 }
    private(set) var timingChanges: [TimingChange] = []
    var timingRows: [TimingChange] {
        if let score { return MacTiming.rows(for: score) }
        return timingChanges.isEmpty ? [TimingChange(bar: 1, bpm: bpm, numerator: meter, denominator: 4)] : timingChanges
    }
    func playbackScore() throws -> ScoreIR {
        if let score { return score }
        return try MacTiming.manualScore(name: name, bars: bars, changes: timingRows, events: envelope["events"] as? [[String: Any]] ?? [])
    }
    func applyingTiming(_ changes: [TimingChange]) throws -> MacScoreProject {
        try TimingChange.validate(changes, bars: bars)
        var candidate = self
        if let score {
            if changes == MacTiming.rows(for: score) { return self }
            candidate.score = try MacTiming.applying(changes, to: score)
        }
        else { candidate.timingChanges = changes }
        candidate.envelope["bpm"] = min(300, max(20, changes[0].bpm))
        let quarters = try changes[0].capacity.value
        candidate.envelope["meter"] = min(12, max(1, Int(quarters.rounded(.toNearestOrEven))))
        _ = try candidate.encodedValidated()
        return candidate
    }
    var loopRange: (start: Int, endExclusive: Int)? {
        guard let loop = envelope["loop"] as? [String: Any], let start = loop["startBar"] as? Int, let end = loop["endBarExclusive"] as? Int else { return nil }
        return (start, end)
    }
    mutating func setLoop(start: Int?, endExclusive: Int? = nil) throws {
        let previous = envelope["loop"]
        if let start, let endExclusive { envelope["loop"] = ["startBar": start, "endBarExclusive": endExclusive] }
        else { envelope["loop"] = NSNull() }
        do { try validateEnvelope() } catch { envelope["loop"] = previous; throw error }
    }
    var legacyChords: [ChordEvent] {
        (envelope["events"] as? [[String: Any]] ?? []).map { event in
            let tick = event["tick"] as? Int ?? 0
            return ChordEvent(id: event["id"] as? Int ?? 0,
                              position: SongPosition(bar: event["bar"] as? Int ?? 1, beat: tick / 960 + 1,
                                                     division: (tick % 960) / 240 + 1, tick: tick % 240), symbol: event["symbol"] as? String ?? "")
        }.sorted { $0.position < $1.position }
    }
    init(score: ScoreIR, selectedPartId: String? = nil) throws {
        try score.validate()
        let selected = selectedPartId ?? score.parts.first?.id
        self.score = score; self.selectedPartId = selected
        envelope = ["schemaVersion": 2, "name": score.title, "bars": score.measures.count,
                    "bpm": min(300, max(20, score.tempoChanges.first?.bpm ?? 120)), "meter": 4, "events": [],
                    "forcedKey": NSNull(), "manualSections": [], "detectChanges": true,
                    "originalKey": NSNull(), "loop": NSNull()]
        _ = try encodedValidated()
    }
    init(name: String, bars: Int, bpm: Double = 120, meter: Int = 4, chords: [ChordEvent] = []) throws {
        self.score = nil; self.selectedPartId = nil
        let events: [[String: Any]] = try chords.map {
            guard $0.position.beat >= 1, $0.position.beat <= 4096,
                  (1...4).contains($0.position.division), (0...239).contains($0.position.tick) else {
                throw Self.invalid("手工和弦四分音符位置无效")
            }
            return ["id": $0.id, "bar": $0.position.bar,
                    "tick": ($0.position.beat - 1) * 960 + ($0.position.division - 1) * 240 + $0.position.tick, "symbol": $0.symbol]
        }
        envelope = ["schemaVersion": 1, "name": name, "bars": bars, "bpm": bpm, "meter": meter,
                    "events": events,
                    "forcedKey": NSNull(), "manualSections": [], "detectChanges": true, "originalKey": NSNull(), "loop": NSNull()]
        _ = try encodedValidated()
    }
    static func load(_ url: URL) throws -> MacScoreProject {
        let handle = try FileHandle(forReadingFrom: url); defer { try? handle.close() }
        let data = try handle.read(upToCount: maximumBytes + 1) ?? Data()
        return try decodeValidated(data)
    }
    static func decodeValidated(_ data: Data) throws -> MacScoreProject {
        try MacProjectJSON.check(data)
        guard var raw = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { throw invalid("项目必须是 JSON 对象") }
        let required: Set<String> = ["schemaVersion", "name", "bars", "bpm", "meter", "events", "forcedKey", "manualSections", "detectChanges", "originalKey", "loop"]
        guard required.isSubset(of: Set(raw.keys)) else { throw invalid("项目缺少必要字段") }
        let version = try integer(raw["schemaVersion"], range: 1...3)
        var score: ScoreIR?, selected: String?
        var changes: [TimingChange] = []
        if version == 2 {
            guard let source = raw["score"], !(source is NSNull), raw.keys.contains("selectedPartId") else { throw invalid("v2 项目缺少完整谱源") }
            score = try ScoreIR.decodeValidated(JSONSerialization.data(withJSONObject: source))
            if !(raw["selectedPartId"] is NSNull) { guard let id = raw["selectedPartId"] as? String else { throw invalid("声部选择无效") }; selected = id }
        } else if raw.keys.contains("score") || raw.keys.contains("selectedPartId") { throw invalid("手工项目不能包含谱源字段") }
        if version == 3 {
            guard let rows = raw["timingChanges"] as? [[String: Any]], !rows.isEmpty, rows.count <= 10_000 else { throw invalid("v3 项目缺少时间线或超过 10000 行") }
            changes = try rows.map { row in
                guard Set(row.keys) == Set(["bar", "bpm", "numerator", "denominator"]),
                      let bpm = row["bpm"] as? NSNumber, CFGetTypeID(bpm) != CFBooleanGetTypeID() else { throw invalid("时间线字段无效") }
                return TimingChange(bar: try integer(row["bar"], range: 1...10_000), bpm: bpm.doubleValue,
                                    numerator: try integer(row["numerator"], range: 1...64), denominator: try integer(row["denominator"], range: 1...64))
            }
        } else if raw.keys.contains("timingChanges") { throw invalid("v1/v2 项目不能包含手工时间线") }
        raw.removeValue(forKey: "score"); raw.removeValue(forKey: "selectedPartId"); raw.removeValue(forKey: "timingChanges")
        let result = MacScoreProject(score: score, selectedPartId: selected, envelope: raw, timingChanges: changes)
        try result.validateEnvelope()
        return result
    }
    private init(score: ScoreIR?, selectedPartId: String?, envelope: [String: Any], timingChanges: [TimingChange]) {
        self.score = score; self.selectedPartId = selectedPartId; self.envelope = envelope; self.timingChanges = timingChanges
    }
    func encodedValidated() throws -> Data {
        try validateEnvelope()
        var raw = envelope
        raw["schemaVersion"] = score != nil ? 2 : timingChanges.isEmpty ? 1 : 3
        if !timingChanges.isEmpty {
            raw["timingChanges"] = timingChanges.map { ["bar": $0.bar, "bpm": $0.bpm, "numerator": $0.numerator, "denominator": $0.denominator] }
        }
        if let score {
            raw["score"] = try JSONSerialization.jsonObject(with: score.encodedValidated())
            raw["selectedPartId"] = selectedPartId as Any? ?? NSNull()
        }
        let data = try JSONSerialization.data(withJSONObject: raw, options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes])
        try MacProjectJSON.check(data)
        return data
    }
    func save(_ url: URL) throws {
        let data = try encodedValidated()
        if FileManager.default.fileExists(atPath: url.path) {
            do { _ = try Self.load(url) } catch { throw Self.invalid("目标文件无法安全覆盖，请另存为新文件：\(error)") }
        }
        try data.write(to: url, options: .atomic)
    }
    private func validateEnvelope() throws {
        _ = try Self.text(envelope["name"], limit: Self.maximumBytes)
        let bars = try Self.integer(envelope["bars"], range: 1...100_000)
        let meter = try Self.integer(envelope["meter"], range: 1...12)
        guard let bpm = envelope["bpm"] as? NSNumber, CFGetTypeID(bpm) != CFBooleanGetTypeID(), bpm.doubleValue.isFinite,
              (20...300).contains(bpm.doubleValue) else { throw Self.invalid("项目速度无效") }
        guard let detect = envelope["detectChanges"] as? NSNumber, CFGetTypeID(detect) == CFBooleanGetTypeID(),
              let events = envelope["events"] as? [[String: Any]], events.count <= 100_000,
              let sections = envelope["manualSections"] as? [[String: Any]], sections.count <= 100_000 else { throw Self.invalid("项目列表或设置无效") }
        var ids = Set<Int>(), onsets = Set<String>()
        let timing: [TimingChange]? = try (timingChanges.isEmpty ? nil : TimingChange.expanded(timingChanges, bars: bars))
        for event in events {
            let id = try Self.integer(event["id"], range: 0...Int.max - 1), bar = try Self.integer(event["bar"], range: 1...bars)
            let tick = try Self.integer(event["tick"], range: 0...2_147_483_647)
            let capacity: QuarterFraction
            if let timing { capacity = try timing[bar - 1].capacity }
            else { capacity = try QuarterFraction(numerator: meter) }
            guard (try QuarterFraction(numerator: tick, denominator: 960)).isBefore(capacity) else { throw Self.invalid("第 \(bar) 小节和弦位置超出预设拍号") }
            _ = try Self.text(event["symbol"], limit: Self.maximumBytes)
            guard ids.insert(id).inserted, onsets.insert("\(bar):\(tick)").inserted else { throw Self.invalid("和弦位置或标识重复") }
        }
        var sectionBars = Set<Int>()
        for section in sections {
            let bar = try Self.integer(section["bar"], range: 1...bars)
            guard sectionBars.insert(bar).inserted, !(section["key"] is NSNull) else { throw Self.invalid("转调段落重复或缺少调性") }
            try Self.key(section["key"])
        }
        try Self.key(envelope["forcedKey"]); try Self.key(envelope["originalKey"])
        if !(envelope["loop"] is NSNull) {
            guard let loop = envelope["loop"] as? [String: Any] else { throw Self.invalid("循环无效") }
            let start = try Self.integer(loop["startBar"], range: 1...bars)
            _ = try Self.integer(loop["endBarExclusive"], range: (start + 1)...(bars + 1))
        }
        if let score {
            guard timingChanges.isEmpty else { throw Self.invalid("导入谱不能包含手工时间线") }
            try score.validate()
            guard bars == score.measures.count, selectedPartId == nil || score.parts.contains(where: { $0.id == selectedPartId }) else { throw Self.invalid("项目谱源或声部选择不一致") }
        } else if selectedPartId != nil { throw Self.invalid("项目无谱源却保留声部选择") }
    }
    private static func key(_ value: Any?) throws {
        if value is NSNull { return }
        guard let key = value as? [String: Any], let minor = key["minor"] as? NSNumber,
              CFGetTypeID(minor) == CFBooleanGetTypeID() else { throw invalid("调性无效") }
        _ = try integer(key["root"], range: 0...11)
    }
    private static func integer(_ value: Any?, range: ClosedRange<Int>) throws -> Int {
        guard let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(), number.doubleValue.isFinite,
              number.doubleValue >= Double(range.lowerBound), number.doubleValue < Double(Int.max), number.doubleValue <= Double(range.upperBound),
              number.doubleValue.rounded() == number.doubleValue else { throw invalid("项目整数无效") }; return number.intValue
    }
    private static func text(_ value: Any?, limit: Int) throws -> String {
        guard let text = value as? String, !text.trimmingCharacters(in: .whitespaces).isEmpty, text.count <= limit,
              !text.unicodeScalars.contains(where: { $0.value < 32 || [127, 133, 0x2028, 0x2029].contains($0.value) }) else { throw invalid("项目文本无效") }; return text
    }
    private static func invalid(_ text: String) -> ScoreValidationError { .invalid(text) }
}

// Preflight before Foundation parsing: bounded nesting, node count and duplicate
// keys at every object depth, including escaped spellings of the same key.
enum MacProjectJSON {
    static func check(_ data: Data) throws {
        guard data.count <= MacScoreProject.maximumBytes else { throw ScoreValidationError.invalid("项目 JSON 超过 32 MiB 限制") }
        let bytes = Array(data); var stack: [Set<String>?] = [], index = 0, nodes = 0
        while index < bytes.count {
            let byte = bytes[index]
            if byte == 34 {
                let start = index; index += 1
                while index < bytes.count { if bytes[index] == 92 { index += 2; continue }; if bytes[index] == 34 { break }; index += 1 }
                guard index < bytes.count else { throw ScoreValidationError.invalid("项目 JSON 字符串不完整") }
                let end = index + 1; var next = end
                while next < bytes.count && [9, 10, 13, 32].contains(bytes[next]) { next += 1 }
                if next < bytes.count && bytes[next] == 58, var keys = stack.last ?? nil {
                    let quoted = Data([91] + Array(bytes[start..<end]) + [93])
                    guard let key = (try JSONSerialization.jsonObject(with: quoted) as? [String])?.first, keys.insert(key).inserted else { throw ScoreValidationError.invalid("项目 JSON 字段重复") }
                    stack[stack.count - 1] = keys
                }
                nodes += 1
            } else if byte == 123 || byte == 91 { stack.append(byte == 123 ? Set<String>() : nil); nodes += 1 }
            else if byte == 125 || byte == 93 { guard !stack.isEmpty else { throw ScoreValidationError.invalid("项目 JSON 结构无效") }; stack.removeLast() }
            else if byte == 44 || byte == 58 { nodes += 1 }
            guard stack.count <= 64, nodes <= 2_000_000 else { throw ScoreValidationError.invalid("项目 JSON 超过复杂度限制") }
            index += 1
        }
    }
}
