import Foundation

// Shared project wire shape. All BPM values are quarter notes per minute.
struct TimingChange: Codable, Equatable {
    var bar: Int
    var bpm: Double
    var numerator: Int
    var denominator: Int

    var meter: ScoreMeter { ScoreMeter(numerator: numerator, denominator: denominator) }
    var capacity: QuarterFraction { get throws { try QuarterFraction(numerator: numerator * 4, denominator: denominator) } }

    static func validate(_ changes: [TimingChange], bars: Int) throws {
        guard (1...10_000).contains(bars), !changes.isEmpty, changes.count <= min(bars, 10_000), changes.first?.bar == 1 else {
            throw ScoreValidationError.invalid("时间线必须从第 1 小节开始")
        }
        var previous = 0
        for item in changes {
            guard item.bar > previous, item.bar <= bars, item.bpm.isFinite, (1...1000).contains(item.bpm) else {
                throw ScoreValidationError.invalid("时间线小节必须唯一递增，BPM 必须为 1…1000")
            }
            try item.meter.validate(); previous = item.bar
        }
    }

    static func expanded(_ changes: [TimingChange], bars: Int) throws -> [TimingChange] {
        try validate(changes, bars: bars)
        var cursor = 0
        return (1...bars).map { bar in
            while cursor + 1 < changes.count && changes[cursor + 1].bar <= bar { cursor += 1 }
            var value = changes[cursor]; value.bar = bar; return value
        }
    }
}

enum MacTiming {
    static func rows(for score: ScoreIR) -> [TimingChange] {
        let byMeasure = Dictionary(grouping: score.tempoChanges, by: \.measureId)
        var carried = 120.0, result: [TimingChange] = []
        for (index, measure) in score.measures.enumerated() {
            let points = (byMeasure[measure.id] ?? []).sorted { $0.offset.isBefore($1.offset) }
            if let start = points.first(where: { $0.offset.numerator == 0 }) { carried = start.bpm }
            let row = TimingChange(bar: index + 1, bpm: carried, numerator: measure.meter.numerator, denominator: measure.meter.denominator)
            if let previous = result.last {
                if previous.bpm != row.bpm || previous.numerator != row.numerator || previous.denominator != row.denominator { result.append(row) }
            } else { result.append(row) }
            if let last = points.last { carried = last.bpm }
        }
        return result
    }

    static func applying(_ changes: [TimingChange], to score: ScoreIR) throws -> ScoreIR {
        try score.validate(); try TimingChange.validate(changes, bars: score.measures.count)
        // Opening and applying an unchanged table preserves all original maps.
        if changes == rows(for: score) { return score }
        let values = try TimingChange.expanded(changes, bars: score.measures.count)
        var endChanges: [String: (old: QuarterFraction, new: QuarterFraction)] = [:]
        let measures = try score.measures.enumerated().map { index, old -> ScoreMeasure in
            let row = values[index], oldCapacity = try QuarterFraction(numerator: old.meter.numerator * 4, denominator: old.meter.denominator)
            let capacity = try row.capacity
            let duration = old.duration == oldCapacity ? capacity : old.duration
            if old.meter != row.meter && !duration.isBefore(capacity, inclusive: true) {
                throw ScoreValidationError.invalid("第 \(index + 1) 小节的弱起/非标准时长超出新拍号")
            }
            endChanges[old.id] = (old.duration, duration)
            let markers = old.markers.map { ScoreMarker(id: $0.id, kind: $0.kind, label: $0.label, offset: $0.offset == old.duration ? duration : $0.offset) }
            let navigation = old.navigation.map { ScoreNavigation(kind: $0.kind, targetMarkerId: $0.targetMarkerId, offset: $0.offset == old.duration ? duration : $0.offset) }
            return ScoreMeasure(id: old.id, number: old.number, duration: duration, meter: row.meter, repeatStart: old.repeatStart,
                                repeatEnd: old.repeatEnd, endingNumbers: old.endingNumbers, markers: markers, navigation: navigation)
        }
        func moved(_ id: String, _ offset: QuarterFraction) -> QuarterFraction {
            guard let endpoints = endChanges[id], offset == endpoints.old else { return offset }; return endpoints.new
        }
        let zero = try QuarterFraction(numerator: 0)
        var tempos = score.tempoChanges.filter { $0.offset.numerator != 0 }.map {
            ScoreTempoChange(measureId: $0.measureId, offset: moved($0.measureId, $0.offset), bpm: $0.bpm)
        }
        // A mid-bar source tempo is kept; the next bar returns to its preset.
        tempos += measures.enumerated().map { ScoreTempoChange(measureId: $0.element.id, offset: zero, bpm: values[$0.offset].bpm) }
        let order = Dictionary(uniqueKeysWithValues: measures.enumerated().map { ($0.element.id, $0.offset) })
        tempos.sort { order[$0.measureId]! == order[$1.measureId]! ? $0.offset.isBefore($1.offset) : order[$0.measureId]! < order[$1.measureId]! }
        let keys = score.keyChanges.map { ScoreKeyChange(measureId: $0.measureId, offset: moved($0.measureId, $0.offset), fifths: $0.fifths, mode: $0.mode) }
        func barLabel(_ id: String) -> String {
            let index = order[id]!, measure = measures[index]
            return "第 \(index + 1) 小节（源编号 \(measure.number)）"
        }
        func checkedPosition(_ id: String, _ offset: QuarterFraction, atEnd: Bool, kind: String) throws -> QuarterFraction {
            let duration = measures[order[id]!].duration
            guard offset.isBefore(duration, inclusive: atEnd) else {
                throw ScoreValidationError.invalid("\(barLabel(id))的\(kind)位置超出新小节时长；请先调整该小节内容")
            }
            return duration
        }
        for part in score.parts {
            for staff in part.staves {
                for event in staff.events {
                    let kind = event.isRest ? "休止符" : event.grace ? "装饰音" : "音符"
                    let duration = try checkedPosition(event.measureId, event.offset, atEnd: event.grace, kind: kind)
                    guard QuarterFraction.sumFits(event.offset, event.duration, duration) else {
                        throw ScoreValidationError.invalid("\(barLabel(event.measureId))的\(kind)时值超出新小节结尾；请先调整该小节内容")
                    }
                }
            }
            for chord in part.chords { _ = try checkedPosition(chord.measureId, chord.offset, atEnd: false, kind: "和弦") }
        }
        for measure in measures {
            for marker in measure.markers { _ = try checkedPosition(measure.id, marker.offset, atEnd: true, kind: "标记") }
            for navigation in measure.navigation { _ = try checkedPosition(measure.id, navigation.offset, atEnd: true, kind: "导航") }
        }
        var tempoOnsets: Set<String> = [], keyOnsets: Set<String> = []
        for tempo in tempos {
            _ = try checkedPosition(tempo.measureId, tempo.offset, atEnd: true, kind: "BPM 变化")
            guard tempoOnsets.insert("\(tempo.measureId):\(tempo.offset.numerator)/\(tempo.offset.denominator)").inserted else {
                throw ScoreValidationError.invalid("\(barLabel(tempo.measureId))的原结尾 BPM 与保留的中途变化重叠；请先调整该小节内容")
            }
        }
        for key in keys {
            _ = try checkedPosition(key.measureId, key.offset, atEnd: true, kind: "调号变化")
            guard keyOnsets.insert("\(key.measureId):\(key.offset.numerator)/\(key.offset.denominator)").inserted else {
                throw ScoreValidationError.invalid("\(barLabel(key.measureId))的原结尾调号与保留的中途变化重叠；请先调整该小节内容")
            }
        }
        let candidate = ScoreIR(id: score.id, title: score.title, source: score.source, measures: measures, parts: score.parts,
                                tempoChanges: tempos, keyChanges: keys, warnings: score.warnings, formatVersion: score.formatVersion)
        // Notes/chords and all non-end offsets are unchanged; validation rejects
        // any event or metadata that no longer fits instead of truncating it.
        try candidate.validate(); return candidate
    }

    static func manualScore(name: String, bars: Int, changes: [TimingChange], events: [[String: Any]]) throws -> ScoreIR {
        guard bars <= 10_000 else { throw ScoreValidationError.invalid("独立时间线最多支持 10000 小节") }
        let rows = try TimingChange.expanded(changes, bars: bars), zero = try QuarterFraction(numerator: 0)
        let measures = try rows.map { row in
            ScoreMeasure(id: "manual.m\(row.bar)", number: "\(row.bar)", duration: try row.capacity, meter: row.meter,
                         repeatStart: false, repeatEnd: nil, endingNumbers: [], markers: [], navigation: [])
        }
        let tempos = changes.map { ScoreTempoChange(measureId: "manual.m\($0.bar)", offset: zero, bpm: $0.bpm) }
        let chords = try events.map { event -> ScoreChord in
            guard let id = event["id"] as? Int, let bar = event["bar"] as? Int, let tick = event["tick"] as? Int,
                  let text = event["symbol"] as? String else { throw ScoreValidationError.invalid("手工和弦无效") }
            return ScoreChord(id: "manual.chord\(id)", measureId: "manual.m\(bar)", offset: try QuarterFraction(numerator: tick, denominator: 960), text: text, staffId: nil)
        }
        let staff = ScoreStaff(id: "manual.staff", name: "手工和弦", kind: "standard", clef: "G2", tuning: [], capo: 0, events: [])
        let part = ScorePart(id: "manual.part", name: "手工和弦", instrument: nil, staves: [staff], chords: chords)
        let score = ScoreIR(id: "manual.score", title: name, source: ScoreSource(format: "manual", fileName: nil, sha256: nil),
                            measures: measures, parts: [part], tempoChanges: tempos, keyChanges: [], warnings: [], formatVersion: 2)
        try score.validate(); return score
    }

    static func metadata(_ measures: [ScoreMeasure]) -> [[String: Any]] {
        measures.map { ["id": $0.id, "number": $0.number, "sourceNumber": $0.number,
                       "duration": ["numerator": $0.duration.numerator, "denominator": $0.duration.denominator],
                       "meter": ["numerator": $0.meter.numerator, "denominator": $0.meter.denominator]] }
    }
}
