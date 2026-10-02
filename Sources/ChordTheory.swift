import Foundation

struct MusicalKey: Equatable {
    let root: Int
    let isMinor: Bool

    var majorFamilyRoot: Int { (root + (isMinor ? 3 : 0)) % 12 }

    var label: String {
        let main = "1=\(ChordTheory.noteName(majorFamilyRoot, preferFlats: false))"
        return isMinor ? main + " · 6=\(ChordTheory.noteName(root, preferFlats: false))（小调）" : main
    }

    var selectionLabel: String {
        ChordTheory.noteName(root, preferFlats: false) + (isMinor ? " 小调" : " 大调")
    }
}

struct KeySection: Equatable {
    let firstBar: Int
    let key: MusicalKey
}

enum ChordTheory {
    private static var cachedChords: [ChordEvent] = []
    private static var cachedForcedKey: MusicalKey?
    private static var cachedSections: [KeySection] = []
    private static var cachedDetectChanges = false
    private static var cachedBeatsPerBar = 4
    private static var cachedMeasureDurations: [Double] = []

    // Krumhansl–Kessler profiles, also used by music21's KrumhanslSchmuckler.
    // Reference and upstream BSD notice: THIRD_PARTY_NOTICES.md.
    private static let majorProfile = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
    private static let minorProfile = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]

    private struct Evidence {
        let start: Double
        let end: Double
        let root: Int
        let intervals: [Int]
        let minor: Bool
        let dominant: Bool
        let hasThird: Bool
    }

    static let chromatic = ["C", "C♯", "D", "E♭", "E", "F", "F♯", "G", "A♭", "A", "B♭", "B"]
    private static let sharpNames = ["C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B"]
    private static let flatNames = ["C", "D♭", "D", "E♭", "E", "F", "G♭", "G", "A♭", "A", "B♭", "B"]
    private static let baseNotes: [Character: Int] = ["C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11]
    private static let degrees = ["1", "♭2", "2", "♭3", "3", "4", "♯4", "5", "♭6", "6", "♭7", "7"]

    static func noteName(_ pitch: Int, preferFlats: Bool) -> String {
        (preferFlats ? flatNames : sharpNames)[(pitch % 12 + 12) % 12]
    }

    static func transpose(_ symbol: String, by semitones: Int, preferFlats: Bool = false) -> String {
        guard symbol != "N.C.", semitones % 12 != 0 else { return symbol }
        let parts = symbol.split(separator: "/", maxSplits: 1, omittingEmptySubsequences: false).map(String.init)
        guard let main = parsedRoot(parts[0]) else { return symbol }
        var result = noteName(main.pitch + semitones, preferFlats: preferFlats) + main.remainder
        if parts.count == 2 {
            if let bass = parsedRoot(parts[1]) {
                result += "/" + noteName(bass.pitch + semitones, preferFlats: preferFlats) + bass.remainder
            } else {
                result += "/" + parts[1]
            }
        }
        return result
    }

    static func key(named text: String) -> MusicalKey? {
        guard let parsed = parsedRoot(text) else { return nil }
        let quality = parsed.remainder.trimmingCharacters(in: .whitespaces).lowercased()
        guard quality.isEmpty || quality == "m" || quality == "minor" || quality == "小调" else {
            return nil
        }
        return MusicalKey(root: parsed.pitch, isMinor: !quality.isEmpty)
    }

    static func displayChord(_ symbol: String) -> String {
        let parts = symbol.split(separator: "/", maxSplits: 1, omittingEmptySubsequences: false).map(String.init)
        guard let main = parsedRoot(parts[0]), main.remainder.contains("7") else { return symbol }
        let root = String(parts[0].dropLast(main.remainder.count))
        return root + main.remainder.replacingOccurrences(of: "7", with: "₇")
            + (parts.count == 2 ? "/" + parts[1] : "")
    }

    static func sectionsWithManualChanges(_ text: String, autoSections: [KeySection]) -> [KeySection] {
        let lines = text.components(separatedBy: .newlines)
        let manual: [KeySection] = lines.compactMap { line in
            let parts = line.trimmingCharacters(in: .whitespaces)
                .split(maxSplits: 1, whereSeparator: \.isWhitespace)
            guard parts.count == 2, let bar = Int(parts[0]), bar > 0,
                  let key = key(named: String(parts[1])) else { return nil }
            return KeySection(firstBar: bar, key: key)
        }.sorted { $0.firstBar < $1.firstBar }
        guard !manual.isEmpty else { return autoSections }
        let baseline = autoSections.first?.key ?? MusicalKey(root: 0, isMinor: false)
        var result = [KeySection(firstBar: 1, key: baseline)]
        for section in manual {
            if section.firstBar == result.last?.firstBar {
                result[result.count - 1] = section
            } else {
                result.append(section)
            }
        }
        return result
    }

    static func number(_ symbol: String, in key: MusicalKey) -> String {
        guard symbol != "N.C." else { return symbol }
        let parts = symbol.split(separator: "/", maxSplits: 1, omittingEmptySubsequences: false).map(String.init)
        guard let main = parsedRoot(parts[0]) else { return symbol }
        let interval = (main.pitch - key.majorFamilyRoot + 12) % 12
        let superscripts: [Character: Character] = ["0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴",
                                                   "5": "⁵", "6": "⁶", "7": "₇", "8": "⁸", "9": "⁹"]
        let quality = String(main.remainder.map { superscripts[$0] ?? $0 })
            .replacingOccurrences(of: "b⁵", with: "b5")
        var result = degrees[interval] + quality
        if parts.count == 2 {
            if let bass = parsedRoot(parts[1]) {
                result += "/" + degrees[(bass.pitch - key.majorFamilyRoot + 12) % 12] + bass.remainder
            } else {
                result += "/" + parts[1]
            }
        }
        return result
    }

    static func sections(for chords: [ChordEvent], forcedKey: MusicalKey?, detectChanges: Bool = true,
                         beatsPerBar: Int = 4, measureDurations: [Double] = []) -> [KeySection] {
        let meter = max(1, min(32, beatsPerBar))
        if !cachedSections.isEmpty && cachedChords == chords && cachedForcedKey == forcedKey
            && cachedDetectChanges == detectChanges && cachedBeatsPerBar == meter
            && cachedMeasureDurations == measureDurations {
            return cachedSections
        }
        let sections = analyzedSections(for: chords, forcedKey: forcedKey, detectChanges: detectChanges,
                                        beatsPerBar: meter, measureDurations: measureDurations)
        cachedChords = chords
        cachedForcedKey = forcedKey
        cachedSections = sections
        cachedDetectChanges = detectChanges
        cachedBeatsPerBar = meter
        cachedMeasureDurations = measureDurations
        return sections
    }

    private static func analyzedSections(for chords: [ChordEvent], forcedKey: MusicalKey?, detectChanges: Bool,
                                         beatsPerBar: Int, measureDurations: [Double]) -> [KeySection] {
        if let forcedKey { return [KeySection(firstBar: 1, key: forcedKey)] }
        guard !chords.isEmpty else { return [KeySection(firstBar: 1, key: MusicalKey(root: 0, isMinor: false))] }
        let sorted = chords.sorted { $0.position < $1.position }
        let lastBar = max(1, sorted.last?.position.bar ?? 1)
        let meter = Double(beatsPerBar)
        var boundaries = (0...lastBar).map { Double($0) * meter }
        if measureDurations.count >= lastBar && measureDurations.prefix(lastBar).allSatisfy({ $0.isFinite && $0 > 0 }) {
            boundaries = [0]
            for duration in measureDurations.prefix(lastBar) { boundaries.append(boundaries.last! + duration) }
        }
        let evidence: [Evidence] = sorted.enumerated().compactMap { index, event in
            guard let chord = parsedRoot(event.symbol.components(separatedBy: "/")[0]) else { return nil }
            let quality = chord.remainder.lowercased().replacingOccurrences(of: " ", with: "")
            let minor = quality.hasPrefix("m") && !quality.hasPrefix("maj")
            let diminished = quality.contains("dim") || quality.contains("°") || quality.contains("ø")
            let suspended = quality.contains("sus") || quality == "5"
            let augmented = quality.contains("aug") || quality.hasPrefix("+")
            var tones = [0, minor || diminished ? 3 : 4, diminished ? 6 : augmented ? 8 : 7]
            if suspended { tones = quality.contains("sus2") ? [0, 2, 7] : quality == "5" ? [0, 7] : [0, 5, 7] }
            if quality.contains("b5") { tones = tones.map { $0 == 7 ? 6 : $0 } }
            if quality.contains("#5") { tones = tones.map { $0 == 7 ? 8 : $0 } }
            let extended = ["7", "9", "11", "13"].contains { quality.contains($0) } && !quality.contains("add")
            if extended { tones.append(quality.contains("maj") ? 11 : diminished && !quality.contains("ø") && !minor ? 9 : 10) }
            if quality.contains("6") || quality.contains("13") { tones.append(9) }
            if quality.contains("9") { tones.append(quality.contains("b9") ? 1 : quality.contains("#9") ? 3 : 2) }
            if quality.contains("11") { tones.append(quality.contains("#11") ? 6 : 5) }
            func offset(_ position: SongPosition) -> Double {
                boundaries[position.bar - 1] + Double(position.beat - 1)
                    + Double(position.division - 1) / 4 + Double(position.tick) / 960
            }
            let start = offset(event.position)
            let end = index + 1 < sorted.count ? offset(sorted[index + 1].position) : boundaries[lastBar]
            guard end > start else { return nil }
            return Evidence(start: start, end: end, root: chord.pitch, intervals: Array(Set(tones)).sorted(),
                            minor: minor, dominant: extended && !minor && !diminished && !suspended
                                && !quality.contains("maj"), hasThird: !suspended && !diminished)
        }
        guard !evidence.isEmpty else { return [KeySection(firstBar: 1, key: MusicalKey(root: 0, isMinor: false))] }
        let candidates = (0..<12).flatMap { root in
            [MusicalKey(root: root, isMinor: false), MusicalKey(root: root, isMinor: true)]
        }
        let globalScores = candidates.map { score($0, evidence, from: 0, to: boundaries[lastBar]) }
        let globalIndex = globalScores.indices.max { globalScores[$0] < globalScores[$1] } ?? 0
        let globalKey = candidates[globalIndex]
        guard detectChanges, lastBar >= 8 else { return [KeySection(firstBar: 1, key: globalKey)] }

        // Offline state decoding can use both sides of a boundary. Relative major/minor
        // share one numbered-key family, so uncertainty between them cannot create a modulation.
        let families = (0..<12).map { family in candidates.indices.filter { candidates[$0].majorFamilyRoot == family } }
        var emissions = Array(repeating: Array(repeating: 0.0, count: 12), count: lastBar)
        for bar in 0..<lastBar {
            let local = candidates.map { score($0, evidence, from: boundaries[bar], to: boundaries[bar + 1]) }
            for family in 0..<12 {
                emissions[bar][family] = families[family].map { local[$0] }.max() ?? 0
                // A weak whole-song prior breaks ambiguous common-chord ties without blocking new evidence.
                emissions[bar][family] += 0.15 * (families[family].map { globalScores[$0] }.max() ?? 0)
            }
        }
        var previous = emissions[0]
        var back = Array(repeating: Array(repeating: 0, count: 12), count: lastBar)
        for bar in 1..<lastBar {
            var next = Array(repeating: 0.0, count: 12)
            for family in 0..<12 {
                let best = (0..<12).max { lhs, rhs in
                    previous[lhs] - (lhs == family ? 0 : 10) < previous[rhs] - (rhs == family ? 0 : 10)
                } ?? family
                next[family] = previous[best] - (best == family ? 0 : 10) + emissions[bar][family]
                back[bar][family] = best
            }
            previous = next
        }
        var path = Array(repeating: globalKey.majorFamilyRoot, count: lastBar)
        path[lastBar - 1] = previous.indices.max { previous[$0] < previous[$1] } ?? globalKey.majorFamilyRoot
        for bar in stride(from: lastBar - 1, through: 1, by: -1) { path[bar - 1] = back[bar][path[bar]] }

        // Absorb brief tonicizations into their better-supported neighbor. Require
        // four bars and multiple chord roots before creating a numbered-key section.
        var start = 0
        while start < lastBar {
            var end = start + 1
            while end < lastBar && path[end] == path[start] { end += 1 }
            let roots = Set(evidence.filter { $0.start < boundaries[end] && $0.end > boundaries[start] }.map(\.root))
            if end - start < 4 || roots.count < 3 {
                let neighbors = Set([start > 0 ? path[start - 1] : globalKey.majorFamilyRoot,
                                     end < lastBar ? path[end] : globalKey.majorFamilyRoot])
                let replacement = neighbors.max { lhs, rhs in
                    emissions[start..<end].reduce(0) { $0 + $1[lhs] }
                        < emissions[start..<end].reduce(0) { $0 + $1[rhs] }
                } ?? globalKey.majorFamilyRoot
                for bar in start..<end { path[bar] = replacement }
            }
            start = end
        }
        var result: [KeySection] = []
        start = 0
        while start < lastBar {
            var end = start + 1
            while end < lastBar && path[end] == path[start] { end += 1 }
            let indices = families[path[start]]
            let index = indices.max { lhs, rhs in
                score(candidates[lhs], evidence, from: boundaries[start], to: boundaries[end])
                    < score(candidates[rhs], evidence, from: boundaries[start], to: boundaries[end])
            } ?? globalIndex
            result.append(KeySection(firstBar: start + 1, key: candidates[index]))
            start = end
        }
        return result
    }

    private static func score(_ key: MusicalKey, _ evidence: [Evidence], from start: Double, to end: Double) -> Double {
        let scale = key.isMinor ? [0, 2, 3, 5, 7, 8, 10] : [0, 2, 4, 5, 7, 9, 11]
        var histogram = Array(repeating: 0.0, count: 12)
        var compatibility = 0.0
        var duration = 0.0
        for (index, chord) in evidence.enumerated() {
            let length = max(0, min(end, chord.end) - max(start, chord.start))
            guard length > 0 else { continue }
            let root = (chord.root - key.root + 12) % 12
            let harmonicDominant = key.isMinor && root == 7 && !chord.minor && chord.hasThird
            let fitting = chord.intervals.filter {
                let tone = (root + $0) % 12
                return scale.contains(tone) || (harmonicDominant && tone == 11)
            }.count
            var value = 4 * Double(fitting) / Double(chord.intervals.count) - 2
            if scale.contains(root) { value += 0.5 }
            if root == 0 && chord.hasThird && chord.minor == key.isMinor { value += 0.8 }
            if harmonicDominant { value += 0.8 }
            if index > 0, chord.start >= start, root == 0, chord.hasThird, chord.minor == key.isMinor {
                let before = evidence[index - 1]
                if before.dominant && (before.root - chord.root + 12) % 12 == 7 {
                    value += 0.8
                }
            }
            compatibility += length * value
            duration += length
            for tone in chord.intervals { histogram[(chord.root + tone) % 12] += length }
        }
        guard duration > 0 else { return 0 }
        let profile = key.isMinor ? minorProfile : majorProfile
        let hMean = histogram.reduce(0, +) / 12
        let pMean = profile.reduce(0, +) / 12
        var numerator = 0.0, hVariance = 0.0, pVariance = 0.0
        for pitch in 0..<12 {
            let h = histogram[pitch] - hMean
            let p = profile[(pitch - key.root + 12) % 12] - pMean
            numerator += h * p
            hVariance += h * h
            pVariance += p * p
        }
        let correlation = hVariance > 0 ? numerator / sqrt(hVariance * pVariance) : 0
        return compatibility / duration + 2 * correlation
    }

    private static func parsedRoot(_ text: String) -> (pitch: Int, remainder: String)? {
        let trimmed = text.trimmingCharacters(in: .whitespaces)
        guard let first = trimmed.first,
              let base = baseNotes[Character(String(first).uppercased())] else { return nil }
        var remainder = String(trimmed.dropFirst())
        var accidental = 0
        if let next = remainder.first, next == "#" || next == "♯" || next == "b" || next == "♭" {
            accidental = next == "#" || next == "♯" ? 1 : -1
            remainder.removeFirst()
        } else if remainder.uppercased().hasPrefix(" SHARP") {
            accidental = 1
            remainder = String(remainder.dropFirst(6))
        } else if remainder.uppercased().hasPrefix(" FLAT") {
            accidental = -1
            remainder = String(remainder.dropFirst(5))
        }
        return ((base + accidental + 12) % 12, remainder)
    }
}
