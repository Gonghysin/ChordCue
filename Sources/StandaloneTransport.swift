import Foundation

private func planRequire(_ condition: Bool, _ message: String) throws {
    if !condition { throw ScoreValidationError.invalid(message) }
}

// Bounded rational route arithmetic. It is exact for the shared supported grid;
// reject an excessive common denominator before falling back to floating time.
private struct RouteFraction {
    let numerator: Int64
    let denominator: Int64
    init(_ numerator: Int64, _ denominator: Int64 = 1) throws {
        try planRequire(numerator >= 0 && denominator > 0, "Invalid route fraction")
        var a = numerator, b = denominator
        while b != 0 { let remainder = a % b; a = b; b = remainder }
        let n = numerator / a, d = denominator / a
        try planRequire(d <= 1_000_000 && n <= 1_000_000 * d, "Route rational complexity exceeded")
        self.numerator = n; self.denominator = d
    }
    init(_ fraction: QuarterFraction) throws { try self.init(Int64(fraction.numerator), Int64(fraction.denominator)) }
    func adding(_ other: RouteFraction) throws -> RouteFraction {
        // Each operand is bounded to 1e12/1e6; each product fits Int64.
        return try RouteFraction(numerator * other.denominator + other.numerator * denominator,
                                 denominator * other.denominator)
    }
    var value: Double { Double(numerator) / Double(denominator) }
}

struct ScorePlayOccurrence {
    let id: String
    let sourceMeasureId: String
    let sourceIndex: Int
    let sourceNumber: String
    let startQuarter: Double
    let endQuarter: Double
    let meter: ScoreMeter
}

struct ScoreTempoSegment {
    let startQuarter: Double
    let endQuarter: Double
    let startSeconds: Double
    let endSeconds: Double
    let bpm: Double
    let occurrenceId: String
    let sourceMeasureId: String
    let sourceOffsetQuarter: Double
}

final class ScorePlayPlan {
    let score: ScoreIR
    let tempoScale: Double
    let occurrences: [ScorePlayOccurrence]
    let segments: [ScoreTempoSegment]
    let endQuarter: Double
    let durationSeconds: Double
    let warnings: [ScoreWarning]
    let routeId: String
    private let wire: [String: Any]
    private let quarterStarts: [Double]
    private let secondStarts: [Double]
    private let occurrenceStarts: [Double]

    init(score: ScoreIR, tempoScale: Double = 1) throws {
        try score.validate()
        try planRequire(tempoScale.isFinite && (0.02...50).contains(tempoScale), "Invalid tempo scale")
        var warnings = score.warnings
        var warningKeys = Set(warnings.map { "\($0.code):\($0.measureId ?? "")" })
        func warn(_ code: String, _ message: String, _ index: Int) throws {
            let measureId = score.measures[index].id, key = "\(code):\(measureId)"
            if warningKeys.insert(key).inserted {
                try planRequire(warnings.count < 10_000, "Route warning complexity exceeded")
                warnings.append(ScoreWarning(code: code, message: message, severity: "warning", measureId: measureId, partId: nil))
            }
        }
        var repeats: [Int: (Int, Int, Int)] = [:], stack: [Int] = []
        for (index, measure) in score.measures.enumerated() {
            if measure.repeatStart {
                stack.append(index)
                try planRequire(stack.count <= 8, "Repeat nesting exceeds eight levels")
            }
            if let passes = measure.repeatEnd {
                let start = stack.popLast() ?? 0
                if repeats[start] != nil {
                    try warn("repeat-ambiguous", "Multiple repeat ends share a start; later repeat omitted", index)
                    continue
                }
                var extended = index
                while extended + 1 < score.measures.count && !score.measures[extended + 1].endingNumbers.isEmpty { extended += 1 }
                repeats[start] = (index, extended, passes)
            }
        }
        for start in stack { try warn("repeat-unclosed", "Repeat start has no matching end; playing it once", start) }
        var finalPass: [Int: Int] = [:], expanded: [Int] = []
        func emitRange(_ start: Int, _ end: Int, _ pass: Int? = nil, _ ignoreStart: Int? = nil, _ depth: Int = 0) throws {
            try planRequire(depth <= 8, "Repeat nesting exceeds eight levels")
            var index = start
            while index <= end {
                let measure = score.measures[index]
                if let pass = pass, !measure.endingNumbers.isEmpty && !measure.endingNumbers.contains(pass) { index += 1; continue }
                if let (_, extended, passes) = repeats[index], index != ignoreStart {
                    if extended > end {
                        try warn("repeat-overlap", "Overlapping repeat/ending span is played once", index)
                    } else {
                        for item in index...extended { finalPass[item] = passes }
                        for traversal in 1...passes { try emitRange(index, extended, traversal, index, depth + 1) }
                        index = extended + 1; continue
                    }
                }
                if !measure.endingNumbers.isEmpty && pass == nil {
                    try warn("ending-unscoped", "Ending has no enclosing repeat; playing source once", index)
                }
                expanded.append(index)
                try planRequire(expanded.count <= 100_000, "Playback occurrence limit exceeded")
                index += 1
            }
        }
        try emitRange(0, score.measures.count - 1)
        var markers: [String: (Int, Double)] = [:]
        for (index, measure) in score.measures.enumerated() {
            for marker in measure.markers { markers[marker.id] = (index, marker.offset.value) }
        }
        var route: [Int] = [], queue = expanded, pointer = 0, jumped = false, executed: Set<String> = []
        while pointer < queue.count {
            let index = queue[pointer], measure = score.measures[index]
            route.append(index)
            try planRequire(route.count <= 100_000, "Navigation occurrence limit exceeded")
            pointer += 1
            if jumped && measure.markers.contains(where: { $0.kind == "fine" }) { break }
            for jump in measure.navigation {
                if jump.offset != measure.duration {
                    try warn("navigation-mid-measure", "Mid-measure navigation is not executed; verify route preview", index)
                    continue
                }
                if jump.kind == "fine" { if jumped { pointer = queue.count }; continue }
                if jump.kind == "toCoda" && !jumped { continue }
                let key = "\(index):\(jump.kind)"
                if executed.contains(key) { continue }
                var target: Int? = jump.kind == "dc" ? 0 : nil
                if let markerId = jump.targetMarkerId, let (markerIndex, offset) = markers[markerId] {
                    if offset != 0 { try warn("navigation-mid-target", "Mid-measure navigation target is not executed", index); continue }
                    target = markerIndex
                }
                guard let target = target else {
                    try warn("navigation-unresolved", "Navigation target is unresolved; playing source continuation", index)
                    continue
                }
                executed.insert(key); jumped = true
                queue = (target..<score.measures.count).filter {
                    let endings = score.measures[$0].endingNumbers
                    return endings.isEmpty || endings.contains(finalPass[$0] ?? endings.max()!)
                }
                pointer = 0; break
            }
        }
        try planRequire(!route.isEmpty, "Empty playback route")
        var changes: [String: [ScoreTempoChange]] = [:]
        for change in score.tempoChanges { changes[change.measureId, default: []].append(change) }
        var local: [Int: [(QuarterFraction, QuarterFraction, Double)]] = [:]
        var bpm = 120.0
        let zero = try QuarterFraction(numerator: 0)
        for (index, measure) in score.measures.enumerated() {
            var pieces: [(QuarterFraction, QuarterFraction, Double)] = [], offset = zero
            let points = (changes[measure.id] ?? []).sorted { $0.offset.isBefore($1.offset) }
            for point in points {
                if offset.isBefore(point.offset) { pieces.append((offset, point.offset, bpm * tempoScale)) }
                bpm = point.bpm; offset = point.offset
            }
            if offset.isBefore(measure.duration) { pieces.append((offset, measure.duration, bpm * tempoScale)) }
            local[index] = pieces
        }
        var occurrences: [ScorePlayOccurrence] = [], segments: [ScoreTempoSegment] = []
        var quarter = try RouteFraction(0), seconds = 0.0, visits: [String: Int] = [:]
        for index in route {
            let measure = score.measures[index]
            visits[measure.id, default: 0] += 1
            let identifier = "\(measure.id)@\(visits[measure.id]!)"
            let end = try quarter.adding(RouteFraction(measure.duration))
            occurrences.append(ScorePlayOccurrence(id: identifier, sourceMeasureId: measure.id, sourceIndex: index + 1,
                sourceNumber: measure.number, startQuarter: quarter.value, endQuarter: end.value, meter: measure.meter))
            for (start, finish, tempo) in local[index]! {
                let segmentStart = try quarter.adding(RouteFraction(start)), segmentEnd = try quarter.adding(RouteFraction(finish))
                let duration = (finish.value - start.value) * 60 / tempo
                segments.append(ScoreTempoSegment(startQuarter: segmentStart.value, endQuarter: segmentEnd.value,
                    startSeconds: seconds, endSeconds: seconds + duration, bpm: tempo, occurrenceId: identifier,
                    sourceMeasureId: measure.id, sourceOffsetQuarter: start.value))
                try planRequire(segments.count <= 200_000, "Tempo segment limit exceeded")
                seconds += duration
            }
            quarter = end
        }
        let occurrenceWire: [[String: Any]] = occurrences.map { ["id": $0.id, "sourceMeasureId": $0.sourceMeasureId,
            "sourceIndex": $0.sourceIndex, "sourceNumber": $0.sourceNumber, "startQuarter": $0.startQuarter,
            "endQuarter": $0.endQuarter, "meter": ["numerator": $0.meter.numerator, "denominator": $0.meter.denominator]] }
        let segmentWire: [[String: Any]] = segments.map { ["startQuarter": $0.startQuarter, "endQuarter": $0.endQuarter,
            "startSeconds": $0.startSeconds, "endSeconds": $0.endSeconds, "bpm": $0.bpm, "occurrenceId": $0.occurrenceId,
            "sourceMeasureId": $0.sourceMeasureId, "sourceOffsetQuarter": $0.sourceOffsetQuarter] }
        let warningWire: [[String: Any]] = warnings.map { ["code": $0.code, "message": $0.message, "severity": $0.severity,
            "measureId": $0.measureId as Any? ?? NSNull(), "partId": $0.partId as Any? ?? NSNull()] }
        let wire: [String: Any] = ["version": 1, "endQuarter": quarter.value, "durationSeconds": seconds,
                                  "occurrences": occurrenceWire, "segments": segmentWire, "warnings": warningWire]
        let encoded = try JSONSerialization.data(withJSONObject: wire, options: [.sortedKeys])
        try planRequire(encoded.count <= 32 * 1024 * 1024, "Route JSON exceeds 32 MiB")
        var hash: UInt64 = 14_695_981_039_346_656_037
        for byte in encoded { hash = (hash ^ UInt64(byte)) &* 1_099_511_628_211 }
        self.score = score; self.tempoScale = tempoScale; self.occurrences = occurrences; self.segments = segments
        self.endQuarter = quarter.value; self.durationSeconds = seconds; self.warnings = warnings; self.wire = wire
        self.quarterStarts = segments.map(\.startQuarter); self.secondStarts = segments.map(\.startSeconds)
        self.occurrenceStarts = occurrences.map(\.startQuarter)
        self.routeId = String(format: "%016llx", hash)
    }

    func toDictionary() -> [String: Any] { wire }
    private func find(_ value: Double, _ array: [Double]) -> Int {
        var low = 0, high = array.count
        while low < high { let middle = (low + high) / 2; if array[middle] <= value { low = middle + 1 } else { high = middle } }
        return max(0, low - 1)
    }
    func timeAt(_ quarter: Double) -> Double {
        if quarter >= endQuarter { return durationSeconds }
        let segment = segments[find(quarter, quarterStarts)]
        return segment.startSeconds + (quarter - segment.startQuarter) * 60 / segment.bpm
    }
    func quarterAt(_ seconds: Double) -> Double {
        if seconds >= durationSeconds { return endQuarter }
        let segment = segments[find(seconds, secondStarts)]
        return segment.startQuarter + (seconds - segment.startSeconds) * segment.bpm / 60
    }
    func occurrenceAt(_ quarter: Double) -> ScorePlayOccurrence { occurrences[find(quarter, occurrenceStarts)] }
    func bpmAt(_ quarter: Double) -> Double { segments[find(quarter, quarterStarts)].bpm }
    func sourcePosition(_ index: Int, offset: Double = 0, occurrence: Int = 1) throws -> Double {
        let choices = occurrences.filter { $0.sourceIndex == index }
        try planRequire(occurrence >= 1 && occurrence <= choices.count && offset.isFinite, "Unknown source occurrence")
        let item = choices[occurrence - 1]
        try planRequire(offset >= 0 && offset < item.endQuarter - item.startQuarter, "Source offset outside measure")
        return item.startQuarter + offset
    }
    func sourceLoopBounds(_ start: Int, _ endExclusive: Int) throws -> (Double, Double) {
        guard let first = occurrences.firstIndex(where: { start <= $0.sourceIndex && $0.sourceIndex < endExclusive }) else {
            throw ScoreValidationError.invalid("Loop has no performed source measures")
        }
        var end = first
        while end + 1 < occurrences.count && start <= occurrences[end + 1].sourceIndex && occurrences[end + 1].sourceIndex < endExclusive { end += 1 }
        return (occurrences[first].startQuarter, occurrences[end].endQuarter)
    }
}

final class StandaloneTransport {
    let score: ScoreIR
    private(set) var selectedPartId: String
    private(set) var plan: ScorePlayPlan
    private let clock: () -> Double
    private var quarter = 0.0, playing = false, anchor: Double?
    private var discontinuity = 0, closed = false
    private var loop: (Double, Double)?
    private var sourceLoop: (Int, Int)?

    init(score: ScoreIR, selectedPartId: String? = nil,
         clock: @escaping () -> Double = { ProcessInfo.processInfo.systemUptime }) throws {
        self.plan = try ScorePlayPlan(score: score)
        let selection = selectedPartId ?? score.parts[0].id
        try planRequire(score.parts.contains { $0.id == selection }, "Unknown selected part")
        self.score = score; self.selectedPartId = selection; self.clock = clock
    }
    private func stateAt(_ now: Double) -> (Double, Bool, Int) {
        guard playing, let anchor = anchor else { return (quarter, false, 0) }
        let seconds = plan.timeAt(quarter) + max(0, now - anchor)
        if let (start, end) = loop {
            let first = plan.timeAt(start), length = plan.timeAt(end) - first
            let elapsed = seconds - first, ratio = elapsed / length, nearest = ratio.rounded()
            let tolerance = min(0.5e-9 / length, 2 * Double.ulpOfOne * max(1, abs(ratio)))
            let exactBoundary = abs(ratio - nearest) <= tolerance
            let iteration = Int(exactBoundary ? nearest : floor(ratio))
            let phase = exactBoundary ? 0 : elapsed - Double(iteration) * length
            let wrapped = first + phase
            return (plan.quarterAt(wrapped), true, iteration)
        }
        if seconds >= plan.durationSeconds { return (plan.endQuarter, false, 0) }
        return (plan.quarterAt(seconds), true, 0)
    }
    private func constrained(_ value: Double) -> Double {
        if let (start, end) = loop, !(start <= value && value < end) { return start }; return value
    }
    private func transition(_ value: Double, _ running: Bool, _ now: Double, prepare: Bool = false) {
        let previous = anchor
        quarter = value; playing = running
        anchor = !running ? nil : prepare ? now + 0.4 : (previous != nil && previous! > now ? previous : now)
        discontinuity += 1
    }
    func play() {
        if closed { return }
        let now = clock(), (current, running, _) = stateAt(now)
        if running { return }
        transition(constrained(current >= plan.endQuarter ? 0 : current), true, now, prepare: true)
    }
    func pause() { if !closed && playing { let now = clock(); transition(stateAt(now).0, false, now) } }
    func stop() { if !closed { transition(0, false, clock()) } }
    func close() { if !closed { pause(); closed = true } }
    func seek(sourceIndex: Int, offsetQuarter: Double = 0, occurrence: Int = 1) throws {
        if sourceIndex == score.measures.count + 1 && offsetQuarter == 0 { try seekQuarter(plan.endQuarter); return }
        try seekQuarter(plan.sourcePosition(sourceIndex, offset: offsetQuarter, occurrence: occurrence))
    }
    func seekQuarter(_ value: Double) throws {
        try planRequire(!closed && value.isFinite && 0 <= value && value <= plan.endQuarter, "Invalid route seek")
        let now = clock(), (_, running, _) = stateAt(now)
        transition(constrained(value), running, now)
    }
    func selectPart(_ id: String) throws {
        try planRequire(!closed && score.parts.contains { $0.id == id }, "Unknown selected part")
        selectedPartId = id
    }
    func setTempoScale(_ scale: Double) throws {
        try planRequire(!closed, "Transport closed")
        if scale == plan.tempoScale { return }
        let candidate = try ScorePlayPlan(score: score, tempoScale: scale)
        let now = clock(), (current, running, _) = stateAt(now)
        plan = candidate
        transition(current, running, now)
    }
    func setLoop(startSourceIndex: Int, endSourceIndexExclusive: Int) throws {
        try planRequire(!closed && 1 <= startSourceIndex && startSourceIndex < endSourceIndexExclusive
            && endSourceIndexExclusive <= score.measures.count + 1, "Invalid source loop")
        let bounds = try plan.sourceLoopBounds(startSourceIndex, endSourceIndexExclusive)
        let now = clock(), (current, running, _) = stateAt(now)
        loop = bounds; sourceLoop = (startSourceIndex, endSourceIndexExclusive)
        transition(running ? constrained(current) : current, running, now)
    }
    func clearLoop() {
        if closed || loop == nil { return }
        let now = clock(), (current, running, _) = stateAt(now)
        loop = nil; sourceLoop = nil; transition(current, running, now)
    }
    func snapshot(revision: Int = 1, includeRoute: Bool = true) -> [String: Any] {
        let now = clock(), (current, running, iteration) = stateAt(now)
        let occurrence = plan.occurrenceAt(current), offset = max(0, current - occurrence.startQuarter)
        let ticks = Int(floor(offset * 960)), withinBeat = ticks % 960, atEnd = current >= plan.endQuarter
        let bpm = plan.bpmAt(current)
        var playback: [String: Any] = ["endBeat": plan.endQuarter, "loop": NSNull()]
        if running, let anchor = anchor { playback["startTime"] = anchor * 1000 }
        if let (start, end) = loop { playback["loop"] = ["startBeat": start, "endBeat": end, "iteration": iteration] }
        var result: [String: Any] = ["revision": revision, "sampleTime": now * 1000, "readMs": 0.0,
            "valid": !closed, "precise": !closed, "rate": bpm / 60, "bpm": bpm,
            "bar": atEnd ? score.measures.count + 1 : occurrence.sourceIndex,
            "beat": atEnd ? 1 : ticks / 960 + 1, "division": atEnd ? 1 : withinBeat / 240 + 1,
            "tick": atEnd ? 0 : withinBeat % 240, "meter": "\(occurrence.meter.numerator)/\(occurrence.meter.denominator)",
            "meterNumerator": occurrence.meter.numerator, "meterDenominator": occurrence.meter.denominator,
            "playing": running, "preparing": running && anchor != nil && now < anchor!,
            "discontinuity": discontinuity, "playback": playback, "playQuarter": current, "positionQuarter": current,
            "sourceMeasureId": occurrence.sourceMeasureId, "sourceOffsetQuarter": offset, "occurrenceId": occurrence.id,
            "selectedPartId": selectedPartId, "routeId": plan.routeId]
        if includeRoute { result["route"] = plan.toDictionary() }
        return result
    }
}
