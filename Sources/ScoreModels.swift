import Foundation

// Vendor-independent source score. All offsets/durations are exact quarter notes.
// Repeat occurrences are compiled separately and never replace source measures.
enum ScoreValidationError: Error, CustomStringConvertible {
    case invalid(String)
    var description: String { if case .invalid(let message) = self { return message }; return "Invalid score" }
}

private func scoreRequire(_ condition: Bool, _ message: String) throws {
    if !condition { throw ScoreValidationError.invalid(message) }
}

private func scoreID(_ value: String) throws {
    try scoreRequire(value.range(of: "\\A[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\\z", options: .regularExpression) != nil,
                     "Invalid stable score ID")
}

private func scoreText(_ value: String, _ maximum: Int = 1024, empty: Bool = false) throws {
    try scoreRequire(value.unicodeScalars.count <= maximum && (empty || !value.trimmingCharacters(in: .whitespaces).isEmpty)
        && !value.unicodeScalars.contains { $0.value < 32 || (127..<160).contains($0.value)
            || $0.value == 0x2028 || $0.value == 0x2029 }, "Invalid score text")
}

struct QuarterFraction: Codable, Hashable, Sendable {
    let numerator: Int
    let denominator: Int
    init(numerator: Int, denominator: Int = 1) throws {
        try scoreRequire((-2_147_483_647...2_147_483_647).contains(numerator)
                         && (1...1_000_000).contains(denominator), "Invalid quarter-note fraction")
        var a = abs(numerator), b = denominator
        while b != 0 { let remainder = a % b; a = b; b = remainder }
        self.numerator = numerator / a
        self.denominator = denominator / a
    }
    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        try self.init(numerator: container.decode(Int.self, forKey: .numerator),
                      denominator: container.decode(Int.self, forKey: .denominator))
    }
    var value: Double { Double(numerator) / Double(denominator) }
    func validate(positive: Bool = false) throws {
        try scoreRequire((0...2_147_483_647).contains(numerator) && (1...1_000_000).contains(denominator)
                         && numerator <= 4096 * denominator && (!positive || numerator > 0),
                         "Invalid nonnegative quarter-note position/duration")
    }
    func isBefore(_ other: QuarterFraction, inclusive: Bool = false) -> Bool {
        let a = Int64(numerator) * Int64(other.denominator)
        let b = Int64(other.numerator) * Int64(denominator)
        return inclusive ? a <= b : a < b
    }
    static func sumFits(_ a: QuarterFraction, _ b: QuarterFraction, _ end: QuarterFraction) -> Bool {
        // Decimal represents these bounded integer products exactly (<= 22 digits).
        let lhs = Decimal(a.numerator) * Decimal(b.denominator) * Decimal(end.denominator)
            + Decimal(b.numerator) * Decimal(a.denominator) * Decimal(end.denominator)
        let rhs = Decimal(end.numerator) * Decimal(a.denominator) * Decimal(b.denominator)
        return lhs <= rhs
    }
}

struct ScoreMeter: Codable, Equatable, Sendable {
    let numerator: Int
    let denominator: Int
    func validate() throws {
        try scoreRequire((1...64).contains(numerator) && [1, 2, 4, 8, 16, 32, 64].contains(denominator),
                         "Invalid score meter")
    }
}

struct ScoreSource: Codable, Equatable, Sendable {
    let format: String
    let fileName: String?
    let sha256: String?
    func validate() throws {
        try scoreRequire(["musicxml", "guitarpro", "manual"].contains(format), "Unknown score format")
        if let name = fileName {
            try scoreText(name)
            try scoreRequire(!name.contains("/") && !name.contains("\\"), "Source filename must be a basename")
        }
        if let hash = sha256 {
            try scoreRequire(hash.range(of: "\\A[0-9a-f]{64}\\z", options: .regularExpression) != nil,
                             "Invalid source SHA-256")
        }
    }
}

struct ScoreMarker: Codable, Equatable, Sendable {
    let id: String
    let kind: String
    let label: String
    let offset: QuarterFraction
    func validate() throws {
        try scoreID(id); try scoreText(label, empty: true); try offset.validate()
        try scoreRequire(["section", "rehearsal", "segno", "coda", "fine"].contains(kind), "Unknown marker kind")
    }
}

struct ScoreNavigation: Codable, Equatable, Sendable {
    let kind: String
    let targetMarkerId: String?
    let offset: QuarterFraction
    func validate() throws {
        try scoreRequire(["dc", "ds", "toCoda", "fine"].contains(kind), "Unknown navigation kind")
        if let target = targetMarkerId { try scoreID(target) }
        try offset.validate()
    }
}

struct ScoreMeasure: Codable, Equatable, Sendable {
    let id: String
    let number: String
    let duration: QuarterFraction
    let meter: ScoreMeter
    let repeatStart: Bool
    let repeatEnd: Int?
    let endingNumbers: [Int]
    let markers: [ScoreMarker]
    let navigation: [ScoreNavigation]
    func validate() throws {
        try scoreID(id); try scoreText(number, 128); try duration.validate(positive: true); try meter.validate()
        if let count = repeatEnd { try scoreRequire((2...32).contains(count), "Invalid repeat traversal count") }
        try scoreRequire(endingNumbers.count <= 32 && endingNumbers.allSatisfy { (1...32).contains($0) }
            && Set(endingNumbers).count == endingNumbers.count, "Invalid ending numbers")
        try scoreRequire(markers.count <= 128 && navigation.count <= 16, "Too many measure markers/jumps")
        for marker in markers {
            try marker.validate()
            try scoreRequire(marker.offset.isBefore(duration, inclusive: true), "Marker outside source measure")
        }
        for jump in navigation {
            try jump.validate()
            try scoreRequire(jump.offset.isBefore(duration, inclusive: true), "Jump outside source measure")
        }
    }
}

struct ScoreBendPoint: Codable, Equatable, Sendable {
    let position: Double
    let semitones: Double
    func validate() throws {
        try scoreRequire(position.isFinite && (0...1).contains(position) && semitones.isFinite && (-24...24).contains(semitones), "Invalid technique curve point")
    }
}

struct ScoreTechnique: Codable, Equatable, Sendable {
    let kind: String
    let targetNoteId: String?
    let value: Double?
    let direction: String?
    let curve: [ScoreBendPoint]
    static let kinds = ["hammerOn", "pullOff", "slide", "bend", "vibrato", "harmonic", "palmMute", "deadNote", "letRing", "staccato", "accent", "heavyAccent", "pick", "tremoloPicking", "trill", "tap", "slap", "pop", "ghost", "brush", "whammy"]
    static let eventKinds = ["vibrato", "pick", "tremoloPicking", "tap", "slap", "pop", "brush", "whammy"]
    static let directions = ["slide": ["shift", "legato", "inAbove", "inBelow", "outUp", "outDown"], "bend": ["bend", "prebend", "release", "prebendRelease"], "whammy": ["bend", "prebend", "release", "prebendRelease"], "vibrato": ["slight", "wide"], "harmonic": ["natural", "artificial", "tap", "pinch", "semi", "feedback"], "pick": ["up", "down"], "brush": ["up", "down", "arpeggioUp", "arpeggioDown"]]
    func validate() throws {
        try scoreRequire(Self.kinds.contains(kind) && curve.count <= 64, "Invalid technique kind/complexity")
        if let target = targetNoteId {
            try scoreID(target)
            try scoreRequire(["hammerOn", "pullOff"].contains(kind) || (kind == "slide" && ["shift", "legato"].contains(direction ?? "")), "Unexpected technique target")
        }
        if let allowed = Self.directions[kind] { try scoreRequire(allowed.contains(direction ?? ""), "Invalid technique direction") }
        else { try scoreRequire(direction == nil, "Unexpected technique direction") }
        if ["bend", "whammy"].contains(kind) {
            try scoreRequire(curve.count >= 2 && curve.first?.position == 0 && curve.last?.position == 1, "Curve must span 0..1")
            var last = 0.0
            for point in curve { try point.validate(); try scoreRequire(point.position >= last, "Curve positions decrease"); last = point.position }
        } else { try scoreRequire(curve.isEmpty, "Unexpected technique curve") }
        if let amount = value {
            try scoreRequire(amount.isFinite, "Nonfinite technique value")
            let valid: Bool
            switch kind {
            case "harmonic": valid = (0...99).contains(amount)
            case "trill": valid = (0...127).contains(amount) && amount.rounded(.towardZero) == amount
            case "tremoloPicking": valid = [8.0, 16, 32, 64, 128, 256].contains(amount)
            case "brush": valid = (0...16).contains(amount)
            default: valid = false
            }
            try scoreRequire(valid, "Invalid technique value for kind")
        } else { try scoreRequire(kind != "tremoloPicking", "Tremolo requires subdivision") }
    }
    static func validateList(_ list: [ScoreTechnique], event: Bool = false) throws {
        try scoreRequire(list.count <= 32, "Technique count exceeded")
        var keys: Set<String> = []
        for item in list {
            try item.validate()
            try scoreRequire(keys.insert(item.kind + ":" + (item.direction ?? "")).inserted, "Duplicate technique")
            if event { try scoreRequire(eventKinds.contains(item.kind), "Note technique belongs to event") }
            else { try scoreRequire(!["brush", "whammy", "tremoloPicking"].contains(item.kind), "Event technique belongs to note") }
        }
    }
}

struct ScoreNote: Codable, Equatable, Sendable {
    let id: String
    let pitch: Int?
    let string: Int?
    let fret: Int?
    let tieStart: Bool
    let tieStop: Bool
    let unpitched: Bool
    let accidental: String?
    var writtenPitch: Int? = nil
    var techniques: [ScoreTechnique] = []
    func validate() throws {
        try scoreID(id)
        if let midi = pitch { try scoreRequire((0...127).contains(midi), "Invalid MIDI pitch") }
        try scoreRequire((string == nil) == (fret == nil), "TAB string/fret must be supplied together")
        if let number = string, let fret = fret {
            try scoreRequire((1...24).contains(number) && (0...99).contains(fret), "Invalid TAB position")
        }
        try scoreRequire(pitch != nil || string != nil || unpitched, "Note has no pitch/TAB position")
        if let spelling = accidental { try scoreText(spelling, 64) }
        if let midi = writtenPitch { try scoreRequire((0...127).contains(midi), "Invalid written MIDI pitch") }
        try ScoreTechnique.validateList(techniques)
    }
}

struct ScoreEvent: Codable, Equatable, Sendable {
    let id: String
    let measureId: String
    let offset: QuarterFraction
    let duration: QuarterFraction
    let voice: Int
    let isRest: Bool
    let grace: Bool
    let notes: [ScoreNote]
    var techniques: [ScoreTechnique] = []
    func validate() throws {
        try scoreID(id); try scoreID(measureId); try offset.validate(); try duration.validate(positive: !grace)
        try scoreRequire((1...16).contains(voice) && notes.count <= 128, "Invalid voice/note count")
        try scoreRequire(isRest == notes.isEmpty && !(grace && isRest), "Invalid rest/note event")
        for note in notes { try note.validate() }
        try ScoreTechnique.validateList(techniques, event: true)
    }
}

struct ScoreStaff: Codable, Equatable, Sendable {
    let id: String
    let name: String
    let kind: String
    let clef: String?
    let tuning: [Int]
    let capo: Int
    let events: [ScoreEvent]
    func validate() throws {
        try scoreID(id); try scoreText(name, empty: true)
        try scoreRequire(["standard", "tab", "percussion"].contains(kind), "Unknown staff kind")
        if let clef = clef { try scoreText(clef, 64) }
        try scoreRequire(tuning.count <= 24 && tuning.allSatisfy { (0...127).contains($0) }
            && (0...24).contains(capo) && events.count <= 500_000, "Invalid staff tuning/capo/event count")
        for event in events { try event.validate() }
    }
}

struct ScoreChord: Codable, Equatable, Sendable {
    let id: String
    let measureId: String
    let offset: QuarterFraction
    let text: String
    let staffId: String?
    func validate() throws {
        try scoreID(id); try scoreID(measureId); try offset.validate(); try scoreText(text)
        if let staff = staffId { try scoreID(staff) }
    }
}

struct ScorePart: Codable, Equatable, Sendable {
    let id: String
    let name: String
    let instrument: String?
    let staves: [ScoreStaff]
    let chords: [ScoreChord]
    func validate() throws {
        try scoreID(id); try scoreText(name)
        if let instrument = instrument { try scoreText(instrument) }
        try scoreRequire((1...16).contains(staves.count) && chords.count <= 100_000, "Invalid part complexity")
        for staff in staves { try staff.validate() }
        for chord in chords { try chord.validate() }
    }
}

struct ScoreTempoChange: Codable, Equatable, Sendable {
    let measureId: String
    let offset: QuarterFraction
    let bpm: Double
    func validate() throws {
        try scoreID(measureId); try offset.validate()
        try scoreRequire(bpm.isFinite && (1...1000).contains(bpm), "Invalid quarter-note tempo")
    }
}

struct ScoreKeyChange: Codable, Equatable, Sendable {
    let measureId: String
    let offset: QuarterFraction
    let fifths: Int
    let mode: String
    func validate() throws {
        try scoreID(measureId); try offset.validate()
        try scoreRequire((-7...7).contains(fifths) && ["major", "minor", "unknown"].contains(mode), "Invalid key signature")
    }
}

struct ScoreWarning: Codable, Equatable, Sendable {
    let code: String
    let message: String
    let severity: String
    let measureId: String?
    let partId: String?
    func validate() throws {
        try scoreID(code); try scoreText(message, 2048)
        try scoreRequire(["info", "warning"].contains(severity), "Invalid warning severity")
        if let measure = measureId { try scoreID(measure) }
        if let part = partId { try scoreID(part) }
    }
}

struct ScoreIR: Codable, Equatable, Sendable {
    let id: String
    let title: String
    let source: ScoreSource
    let measures: [ScoreMeasure]
    let parts: [ScorePart]
    let tempoChanges: [ScoreTempoChange]
    let keyChanges: [ScoreKeyChange]
    let warnings: [ScoreWarning]
    let formatVersion: Int

    static func decodeValidated(_ data: Data) throws -> ScoreIR {
        let canonical = try StrictScoreJSON.migrate(data)
        let score = try JSONDecoder().decode(ScoreIR.self, from: canonical)
        try score.validate()
        return score
    }

    func encodedValidated() throws -> Data {
        try validate()
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        let data = try encoder.encode(self)
        // Synthesized Codable omits nil optional fields. The shared canonical
        // contract explicitly retains nulls, including in nested objects.
        let raw = try JSONSerialization.jsonObject(with: data)
        let canonical = try StrictScoreJSON.addNulls(raw, type: "ScoreIR")
        let result = try JSONSerialization.data(withJSONObject: canonical, options: [.sortedKeys])
        try StrictScoreJSON.validate(result)
        return result
    }

    func validate() throws {
        try scoreID(id); try scoreText(title); try source.validate()
        try scoreRequire(formatVersion == 2 && (1...10_000).contains(measures.count)
            && (1...64).contains(parts.count) && tempoChanges.count <= 100_000
            && keyChanges.count <= 100_000 && warnings.count <= 10_000, "Invalid score version/complexity")
        var ids: Set<String> = [id]
        func unique(_ identifier: String) throws {
            try scoreRequire(ids.insert(identifier).inserted, "Duplicate score ID: \(identifier)")
        }
        var measureMap: [String: ScoreMeasure] = [:]
        var markerIds: Set<String> = []
        for measure in measures {
            try measure.validate(); try unique(measure.id)
            measureMap[measure.id] = measure
            for marker in measure.markers { try unique(marker.id); markerIds.insert(marker.id) }
        }
        func position(_ measureId: String, _ offset: QuarterFraction, atEnd: Bool = false) throws -> QuarterFraction {
            guard let measure = measureMap[measureId] else {
                throw ScoreValidationError.invalid("Unknown source measure reference")
            }
            try scoreRequire(offset.isBefore(measure.duration, inclusive: atEnd), "Position outside source measure")
            return measure.duration
        }
        var eventCount = 0, noteCount = 0, chordCount = 0, techniqueCount = 0
        let measureOrder = Dictionary(uniqueKeysWithValues: measures.enumerated().map { ($0.element.id, $0.offset) })
        var noteLocations: [String: (staff: String, voice: Int, measure: Int, offset: QuarterFraction, string: Int?, grace: Bool, index: Int)] = [:]
        var links: [(String, String)] = []
        for part in parts {
            try part.validate(); try unique(part.id)
            let staffIds = Set(part.staves.map(\.id))
            for staff in part.staves {
                try unique(staff.id)
                var onsets: Set<String> = []
                eventCount += staff.events.count
                for (eventIndex, event) in staff.events.enumerated() {
                    try unique(event.id)
                    let end = try position(event.measureId, event.offset, atEnd: event.grace)
                    try scoreRequire(QuarterFraction.sumFits(event.offset, event.duration, end),
                                     "Event duration outside source measure")
                    let onset = "\(event.measureId):\(event.offset.numerator)/\(event.offset.denominator):\(event.voice)"
                    if !event.grace { try scoreRequire(onsets.insert(onset).inserted, "Duplicate event onset in staff voice") }
                    noteCount += event.notes.count
                    techniqueCount += event.techniques.count
                    for note in event.notes {
                        try unique(note.id)
                        techniqueCount += note.techniques.count
                        noteLocations[note.id] = (staff.id, event.voice, measureOrder[event.measureId]!, event.offset, note.string, event.grace, eventIndex)
                        for item in note.techniques { if let target = item.targetNoteId { links.append((note.id, target)) } }
                        if let string = note.string, !staff.tuning.isEmpty {
                            try scoreRequire(string <= staff.tuning.count, "TAB string outside tuning")
                        }
                    }
                }
            }
            chordCount += part.chords.count
            for chord in part.chords {
                try unique(chord.id)
                _ = try position(chord.measureId, chord.offset)
                if let staff = chord.staffId { try scoreRequire(staffIds.contains(staff), "Chord references another part's staff") }
            }
        }
        try scoreRequire(eventCount <= 500_000 && noteCount <= 1_000_000 && chordCount <= 100_000, "Total score complexity exceeded")
        try scoreRequire(techniqueCount <= 200_000, "Total technique complexity exceeded")
        for (origin, target) in links {
            guard let a = noteLocations[origin], let b = noteLocations[target] else { throw ScoreValidationError.invalid("Unknown technique target note") }
            let later = a.measure < b.measure || (a.measure == b.measure && (a.offset.isBefore(b.offset) || (a.grace && a.offset == b.offset && a.index < b.index)))
            try scoreRequire(origin != target && a.staff == b.staff && a.voice == b.voice && later, "Technique target must be later in same staff voice")
            try scoreRequire(a.string == nil || b.string == nil || a.string == b.string, "Linked technique changes string")
        }
        var tempoOnsets: Set<String> = [], keyOnsets: Set<String> = []
        for tempo in tempoChanges {
            try tempo.validate(); _ = try position(tempo.measureId, tempo.offset, atEnd: true)
            try scoreRequire(tempoOnsets.insert("\(tempo.measureId):\(tempo.offset.numerator)/\(tempo.offset.denominator)").inserted,
                             "Duplicate tempo onset")
        }
        for key in keyChanges {
            try key.validate(); _ = try position(key.measureId, key.offset, atEnd: true)
            try scoreRequire(keyOnsets.insert("\(key.measureId):\(key.offset.numerator)/\(key.offset.denominator)").inserted,
                             "Duplicate key onset")
        }
        let partIds = Set(parts.map(\.id))
        for warning in warnings {
            try warning.validate()
            if let measure = warning.measureId { try scoreRequire(measureMap[measure] != nil, "Unknown warning measure") }
            if let part = warning.partId { try scoreRequire(partIds.contains(part), "Unknown warning part") }
        }
        for measure in measures {
            for jump in measure.navigation {
                if let target = jump.targetMarkerId { try scoreRequire(markerIds.contains(target), "Unknown navigation target") }
                if ["ds", "toCoda"].contains(jump.kind) && jump.targetMarkerId == nil {
                    try scoreRequire(warnings.contains { $0.measureId == measure.id }, "Unresolved navigation requires measure warning")
                }
            }
        }
    }
}

// Strict JSON boundary: duplicates/unknown or missing fields cannot disappear in
// Foundation's dictionary conversion. Bound byte count, nesting and node work.
private enum StrictScoreJSON {
    static let fields: [String: [String: String]] = [
        "QuarterFraction": ["numerator":"", "denominator":""],
        "ScoreMeter": ["numerator":"", "denominator":""],
        "ScoreSource": ["format":"", "fileName":"", "sha256":""],
        "ScoreMarker": ["id":"", "kind":"", "label":"", "offset":"QuarterFraction"],
        "ScoreNavigation": ["kind":"", "targetMarkerId":"", "offset":"QuarterFraction"],
        "ScoreMeasure": ["id":"", "number":"", "duration":"QuarterFraction", "meter":"ScoreMeter",
            "repeatStart":"", "repeatEnd":"", "endingNumbers":"", "markers":"ScoreMarker[]", "navigation":"ScoreNavigation[]"],
        "ScoreBendPoint": ["position":"", "semitones":""],
        "ScoreTechnique": ["kind":"", "targetNoteId":"", "value":"", "direction":"", "curve":"ScoreBendPoint[]"],
        "ScoreNote": ["id":"", "pitch":"", "string":"", "fret":"", "tieStart":"", "tieStop":"", "unpitched":"", "accidental":"", "writtenPitch":"", "techniques":"ScoreTechnique[]"],
        "ScoreEvent": ["id":"", "measureId":"", "offset":"QuarterFraction", "duration":"QuarterFraction",
            "voice":"", "isRest":"", "grace":"", "notes":"ScoreNote[]", "techniques":"ScoreTechnique[]"],
        "ScoreStaff": ["id":"", "name":"", "kind":"", "clef":"", "tuning":"", "capo":"", "events":"ScoreEvent[]"],
        "ScoreChord": ["id":"", "measureId":"", "offset":"QuarterFraction", "text":"", "staffId":""],
        "ScorePart": ["id":"", "name":"", "instrument":"", "staves":"ScoreStaff[]", "chords":"ScoreChord[]"],
        "ScoreTempoChange": ["measureId":"", "offset":"QuarterFraction", "bpm":""],
        "ScoreKeyChange": ["measureId":"", "offset":"QuarterFraction", "fifths":"", "mode":""],
        "ScoreWarning": ["code":"", "message":"", "severity":"", "measureId":"", "partId":""],
        "ScoreIR": ["id":"", "title":"", "source":"ScoreSource", "measures":"ScoreMeasure[]", "parts":"ScorePart[]",
            "tempoChanges":"ScoreTempoChange[]", "keyChanges":"ScoreKeyChange[]", "warnings":"ScoreWarning[]", "formatVersion":""]
    ]
    static let optional: [String: Set<String>] = [
        "ScoreSource": ["fileName", "sha256"], "ScoreNavigation": ["targetMarkerId"],
        "ScoreMeasure": ["repeatEnd"], "ScoreNote": ["pitch", "string", "fret", "accidental", "writtenPitch"],
        "ScoreTechnique": ["targetNoteId", "value", "direction"],
        "ScoreStaff": ["clef"], "ScoreChord": ["staffId"], "ScorePart": ["instrument"],
        "ScoreWarning": ["measureId", "partId"]
    ]
    static func validate(_ data: Data) throws {
        try scoreRequire(data.count <= 32 * 1024 * 1024, "Score JSON exceeds 32 MiB")
        var scanner = Scanner(bytes: Array(data))
        try scanner.value(depth: 0)
        scanner.whitespace()
        try scoreRequire(scanner.index == scanner.bytes.count, "Trailing JSON data")
        let raw = try JSONSerialization.jsonObject(with: data)
        try shape(raw, type: "ScoreIR")
    }
    static func migrate(_ data: Data) throws -> Data {
        try scoreRequire(data.count <= 32 * 1024 * 1024, "Score JSON exceeds 32 MiB")
        var scanner = Scanner(bytes: Array(data)); try scanner.value(depth: 0); scanner.whitespace()
        try scoreRequire(scanner.index == scanner.bytes.count, "Trailing JSON data")
        guard var raw = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { throw ScoreValidationError.invalid("Score object required") }
        let version = raw["formatVersion"] as? Int
        try scoreRequire(version == 1 || version == 2, "Unsupported score formatVersion")
        if version == 1 {
            try shape(raw, type: "ScoreIR", legacy: true)
            guard var parts = raw["parts"] as? [[String: Any]] else { throw ScoreValidationError.invalid("Score parts required") }
            for pi in parts.indices {
                var staves = parts[pi]["staves"] as! [[String: Any]]
                for si in staves.indices {
                    var events = staves[si]["events"] as! [[String: Any]]
                    for ei in events.indices {
                        var notes = events[ei]["notes"] as! [[String: Any]]
                        for ni in notes.indices { notes[ni]["writtenPitch"] = NSNull(); notes[ni]["techniques"] = [] }
                        events[ei]["notes"] = notes; events[ei]["techniques"] = []
                    }
                    staves[si]["events"] = events
                }
                parts[pi]["staves"] = staves
            }
            raw["parts"] = parts; raw["formatVersion"] = 2
        }
        let canonical = try JSONSerialization.data(withJSONObject: raw, options: [.sortedKeys])
        try validate(canonical); return canonical
    }
    static func shape(_ value: Any, type: String, legacy: Bool = false) throws {
        guard let object = value as? [String: Any], let expected = fields[type] else {
            throw ScoreValidationError.invalid("Score JSON object required")
        }
        let effective = expected.filter { !(legacy && ((type == "ScoreNote" && ["writtenPitch", "techniques"].contains($0.key)) || (type == "ScoreEvent" && $0.key == "techniques"))) }
        try scoreRequire(Set(object.keys) == Set(effective.keys), "Missing/unknown \(type) JSON fields")
        for (key, child) in effective where !child.isEmpty {
            let raw = object[key]!
            if child.hasSuffix("[]") {
                guard let array = raw as? [Any] else { throw ScoreValidationError.invalid("Score JSON array required") }
                for item in array { try shape(item, type: String(child.dropLast(2)), legacy: legacy) }
            } else { try shape(raw, type: child, legacy: legacy) }
        }
    }
    static func addNulls(_ value: Any, type: String) throws -> Any {
        guard var object = value as? [String: Any], let expected = fields[type] else {
            throw ScoreValidationError.invalid("Invalid encoded score object")
        }
        for key in optional[type] ?? [] where object[key] == nil { object[key] = NSNull() }
        for (key, child) in expected where !child.isEmpty {
            if child.hasSuffix("[]"), let array = object[key] as? [Any] {
                object[key] = try array.map { try addNulls($0, type: String(child.dropLast(2))) }
            } else if let raw = object[key] { object[key] = try addNulls(raw, type: child) }
        }
        return object
    }
    private struct Scanner {
        let bytes: [UInt8]
        var index = 0
        var nodes = 0
        mutating func whitespace() { while index < bytes.count && [9, 10, 13, 32].contains(bytes[index]) { index += 1 } }
        mutating func string() throws -> String {
            let start = index
            try scoreRequire(index < bytes.count && bytes[index] == 34, "JSON string required")
            index += 1
            var escaped = false
            while index < bytes.count {
                let byte = bytes[index]; index += 1
                if escaped { escaped = false; continue }
                if byte == 92 { escaped = true; continue }
                if byte == 34 {
                    let text = try JSONDecoder().decode(String.self, from: Data(bytes[start..<index]))
                    try scoreRequire(text.count <= 4096, "Score JSON text limit exceeded")
                    return text
                }
            }
            throw ScoreValidationError.invalid("Unterminated JSON string")
        }
        mutating func value(depth: Int, integer: Bool = false) throws {
            whitespace(); nodes += 1
            try scoreRequire(depth <= 32 && nodes <= 2_000_000 && index < bytes.count, "Score JSON complexity exceeded")
            switch bytes[index] {
            case 123:
                index += 1; whitespace()
                var keys: Set<String> = []
                if index < bytes.count && bytes[index] == 125 { index += 1; return }
                while true {
                    whitespace(); let key = try string()
                    try scoreRequire(key.count <= 128 && keys.insert(key).inserted && keys.count <= 64, "Duplicate/excess JSON field")
                    whitespace()
                    try scoreRequire(index < bytes.count && bytes[index] == 58, "JSON colon required")
                    let integerField = ["numerator", "denominator", "repeatEnd", "endingNumbers", "pitch",
                                        "writtenPitch", "string", "fret", "voice", "tuning", "capo", "fifths", "formatVersion"].contains(key)
                    index += 1; try value(depth: depth + 1, integer: integerField); whitespace()
                    try scoreRequire(index < bytes.count, "Unterminated JSON object")
                    if bytes[index] == 125 { index += 1; break }
                    try scoreRequire(bytes[index] == 44, "JSON comma required"); index += 1
                }
            case 91:
                index += 1; whitespace()
                if index < bytes.count && bytes[index] == 93 { index += 1; return }
                var count = 0
                while true {
                    count += 1; try scoreRequire(count <= 1_000_000, "Score JSON array limit exceeded")
                    try value(depth: depth + 1, integer: integer); whitespace()
                    try scoreRequire(index < bytes.count, "Unterminated JSON array")
                    if bytes[index] == 93 { index += 1; break }
                    try scoreRequire(bytes[index] == 44, "JSON comma required"); index += 1
                }
            case 34: _ = try string()
            default:
                let start = index
                while index < bytes.count && ![9, 10, 13, 32, 44, 93, 125].contains(bytes[index]) { index += 1 }
                try scoreRequire(index > start && index - start <= 64, "Invalid JSON scalar")
                if integer {
                    let token = String(decoding: bytes[start..<index], as: UTF8.self)
                    try scoreRequire(token == "null" || token.range(of: "\\A-?(0|[1-9][0-9]*)\\z", options: .regularExpression) != nil,
                                     "Score integer must use an integer JSON token")
                }
            }
        }
    }
}
