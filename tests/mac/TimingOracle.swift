import Foundation

struct SongPosition: Comparable {
    let bar: Int, beat: Int, division: Int, tick: Int
    static func < (a: SongPosition, b: SongPosition) -> Bool { [a.bar, a.beat, a.division, a.tick].lexicographicallyPrecedes([b.bar, b.beat, b.division, b.tick]) }
}
struct ChordEvent: Equatable { let id: Int; let position: SongPosition; let symbol: String }

@main struct TimingOracle {
    static func require(_ condition: @autoclosure () throws -> Bool, _ text: String) throws {
        if try !condition() { throw ScoreValidationError.invalid("Timing fixture failed: " + text) }
    }
    static func rejected(_ action: () throws -> Void) throws {
        do { try action() } catch { return }; throw ScoreValidationError.invalid("Expected rejection")
    }
    static func rejectedInBar(_ bar: Int, _ action: () throws -> Void) throws {
        do { try action() }
        catch {
            try require(String(describing: error).contains("第 \(bar) 小节"), "rejection identifies source bar")
            return
        }
        throw ScoreValidationError.invalid("Expected contextual rejection")
    }
    static func fraction(_ n: Int, _ d: Int = 1) throws -> QuarterFraction { try QuarterFraction(numerator: n, denominator: d) }
    static func source(duration: Int = 4, noteOffset: Int = 0, chordOffset: Int? = nil) throws -> ScoreIR {
        let zero = try fraction(0), end = try fraction(duration)
        let meter = ScoreMeter(numerator: 4, denominator: 4)
        let marker = ScoreMarker(id: "marker.end", kind: "fine", label: "Fine", offset: end)
        let first = ScoreMeasure(id: "m1", number: "A", duration: end, meter: meter, repeatStart: false, repeatEnd: nil,
                                 endingNumbers: [], markers: [marker], navigation: [ScoreNavigation(kind: "fine", targetMarkerId: nil, offset: end)])
        let second = ScoreMeasure(id: "m2", number: "A", duration: try fraction(4), meter: meter, repeatStart: false,
                                  repeatEnd: nil, endingNumbers: [], markers: [], navigation: [])
        let technique = ScoreTechnique(kind: "palmMute", targetNoteId: nil, value: nil, direction: nil, curve: [])
        let note = ScoreNote(id: "n1", pitch: 60, string: nil, fret: nil, tieStart: false, tieStop: false, unpitched: false,
                             accidental: "natural", writtenPitch: 60, techniques: [technique])
        let event = ScoreEvent(id: "e1", measureId: "m1", offset: try fraction(noteOffset), duration: try fraction(1), voice: 1,
                               isRest: false, grace: false, notes: [note], techniques: [])
        let staff = ScoreStaff(id: "s1", name: "Staff", kind: "standard", clef: "G2", tuning: [], capo: 0, events: [event])
        let chords = try chordOffset.map { [ScoreChord(id: "c1", measureId: "m1", offset: try fraction($0), text: "C", staffId: "s1")] } ?? []
        let part = ScorePart(id: "p1", name: "Part", instrument: "Piano", staves: [staff], chords: chords)
        var tempos = [ScoreTempoChange(measureId: "m1", offset: zero, bpm: 120), ScoreTempoChange(measureId: "m1", offset: end, bpm: 80)]
        if duration > 2 { tempos.append(ScoreTempoChange(measureId: "m1", offset: try fraction(2), bpm: 90)) }
        let key = ScoreKeyChange(measureId: "m1", offset: end, fifths: 3, mode: "major")
        let warning = ScoreWarning(code: "fixture.warning", message: "Source warning retained", severity: "warning", measureId: "m1", partId: "p1")
        let score = ScoreIR(id: "source", title: "Fixture", source: ScoreSource(format: "musicxml", fileName: "fixture.xml", sha256: nil),
                            measures: [first, second], parts: [part], tempoChanges: tempos, keyChanges: [key], warnings: [warning], formatVersion: 2)
        try score.validate(); return score
    }
    static func main() throws {
        let rows = [TimingChange(bar: 1, bpm: 120, numerator: 4, denominator: 4), TimingChange(bar: 2, bpm: 90, numerator: 6, denominator: 8),
                    TimingChange(bar: 3, bpm: 150, numerator: 7, denominator: 8)]
        let chord = ChordEvent(id: 7, position: SongPosition(bar: 3, beat: 3, division: 3, tick: 0), symbol: "G")
        let legacy = try MacScoreProject(name: "Timing", bars: 3, chords: [chord])
        let legacyJSON = try JSONSerialization.jsonObject(with: legacy.encodedValidated()) as! [String: Any]
        try require(legacyJSON["schemaVersion"] as? Int == 1 && legacyJSON["timingChanges"] == nil, "legacy remains v1")
        let project = try legacy.applyingTiming(rows), playback = try project.playbackScore(), plan = try ScorePlayPlan(score: playback)
        try require(project.score == nil && project.selectedPartId == nil, "manual adapter is not an imported score")
        try require(playback.parts[0].staves[0].events.isEmpty, "manual adapter invents no notes/rests")
        try require(playback.measures.map(\.duration.value) == [4, 3, 3.5], "fractional 6/8 and 7/8 capacity")
        try require(playback.parts[0].chords[0].offset.value == 2.5, "legacy ticks remain quarters")
        try require(abs(plan.durationSeconds - 5.4) < 1e-12 && plan.endQuarter == 10.5, "mixed timing integration")
        let encoded = try project.encodedValidated(), decoded = try MacScoreProject.decodeValidated(encoded)
        let wire = try JSONSerialization.jsonObject(with: encoded) as! [String: Any]
        try require(wire["schemaVersion"] as? Int == 3 && wire["score"] == nil && decoded.timingChanges == rows, "v3 round trip")
        let metadata = MacTiming.metadata(playback.measures)
        try require(metadata[2]["id"] as? String == "manual.m3" && metadata[2]["sourceNumber"] as? String == "3", "route and metadata identity")
        let duration = metadata[2]["duration"] as! [String: Int]
        try require(duration == ["numerator": 7, "denominator": 2], "wire duration exact fraction")
        for bad in [[TimingChange(bar: 2, bpm: 120, numerator: 4, denominator: 4)],
                    [rows[0], rows[0]], [rows[0], rows[2], rows[1]],
                    [TimingChange(bar: 1, bpm: 0, numerator: 4, denominator: 4)],
                    [TimingChange(bar: 1, bpm: 120, numerator: 4, denominator: 3)]] {
            try rejected { _ = try legacy.applyingTiming(bad) }
        }
        let rounded = try MacScoreProject(name: "Rounding", bars: 3).applyingTiming([TimingChange(bar: 1, bpm: 1000, numerator: 5, denominator: 8)])
        try require(rounded.bpm == 300 && rounded.meter == 2, "Python-compatible fallback rounding")
        var invalid = wire; invalid["bars"] = 10001
        try rejected { _ = try MacScoreProject.decodeValidated(JSONSerialization.data(withJSONObject: invalid)) }
        invalid = wire; invalid["schemaVersion"] = 2
        try rejected { _ = try MacScoreProject.decodeValidated(JSONSerialization.data(withJSONObject: invalid)) }
        invalid = wire; invalid["timingChanges"] = [["bar": 1, "bpm": true, "numerator": 4, "denominator": 4]]
        try rejected { _ = try MacScoreProject.decodeValidated(JSONSerialization.data(withJSONObject: invalid)) }
        let longLegacy = try MacScoreProject(name: "Old", bars: 10001)
        try require(try MacScoreProject.decodeValidated(longLegacy.encodedValidated()).bars == 10001, "large v1 remains readable")
        try rejected { _ = try longLegacy.applyingTiming([rows[0]]) }
        let longText = try MacScoreProject(name: String(repeating: "x", count: 1025), bars: 16)
        try require(try MacScoreProject.decodeValidated(longText.encodedValidated()).name == longText.name, "legacy text remains readable")
        try rejected { _ = try longText.playbackScore() }
        let keyFixture = [(1, 1, "C"), (1, 4, "F#m"), (2, 1, "F"), (2, 3, "F#")].enumerated().map {
            ChordEvent(id: $0.offset, position: SongPosition(bar: $0.element.0, beat: $0.element.1, division: 1, tick: 0), symbol: $0.element.2)
        }
        let variableKey = ChordTheory.sections(for: keyFixture, forcedKey: nil, beatsPerBar: 4, measureDurations: [4, 3])
        try require(variableKey.first?.key == MusicalKey(root: 0, isMinor: false), "variable capacities use source quarter boundaries")
        try require(ChordTheory.sections(for: keyFixture, forcedKey: nil, beatsPerBar: 6, measureDurations: [4, 3]) == variableKey, "playhead meter does not change inferred key")
        try require(ChordTheory.sections(for: keyFixture, forcedKey: nil, beatsPerBar: 6).first?.key == MusicalKey(root: 10, isMinor: true), "legacy fixed meter and duration cache remain distinct")

        var now = 0.0
        let engine = try StandaloneTransport(score: playback, clock: { now })
        try engine.setLoop(startSourceIndex: 1, endSourceIndexExclusive: 4); engine.play()
        now = 2.4
        try require(engine.snapshot()["meter"] as? String == "6/8" && engine.snapshot()["bpm"] as? Double == 90, "automatic bar timing switch")
        for cycle in [1, 3, 100, 1000] {
            for delta in [-1e-9, 0.0, 1e-9] {
                now = 0.4 + Double(cycle) * plan.durationSeconds + delta
                let sample = engine.snapshot(), loop = (sample["playback"] as! [String: Any])["loop"] as! [String: Any]
                try require(loop["iteration"] as? Int == (delta < 0 ? cycle - 1 : cycle), "loop iteration \(cycle)/\(delta)")
                let quarter = sample["playQuarter"] as! Double
                try require(delta < 0 ? quarter > 10.4999999 : quarter < 1e-7, "loop phase \(cycle)/\(delta)")
            }
        }
        let original = try source()
        try require(try MacTiming.applying(MacTiming.rows(for: original), to: original) == original, "unchanged source table preserves maps")
        let edited = try MacTiming.applying([TimingChange(bar: 1, bpm: 150, numerator: 6, denominator: 4)], to: original)
        try require(edited.parts == original.parts && edited.source == original.source && edited.warnings == original.warnings, "notes/techniques/source/warnings retained")
        try require(edited.measures[0].duration.value == 6 && edited.measures[1].duration.value == 6, "normal bar length follows new meter")
        try require(edited.tempoChanges.contains { $0.measureId == "m1" && $0.offset.value == 2 && $0.bpm == 90 }, "intra-bar tempo retained")
        try require(edited.tempoChanges.contains { $0.measureId == "m1" && $0.offset.value == 6 && $0.bpm == 80 }, "end tempo moves")
        try require(edited.tempoChanges.contains { $0.measureId == "m2" && $0.offset.value == 0 && $0.bpm == 150 }, "next bar returns to preset")
        try require(edited.keyChanges[0].offset.value == 6 && edited.measures[0].markers[0].offset.value == 6
                    && edited.measures[0].navigation[0].offset.value == 6, "end key/marker/navigation move")
        let sourceProject = try MacScoreProject(score: edited)
        let sourceWire = try JSONSerialization.jsonObject(with: sourceProject.encodedValidated()) as! [String: Any]
        try require(sourceWire["schemaVersion"] as? Int == 2 && sourceWire["timingChanges"] == nil, "edited imports remain v2")
        try require(try MacScoreProject.decodeValidated(sourceProject.encodedValidated()).score == edited, "edited source round trip")
        try rejectedInBar(1) { _ = try MacTiming.applying([TimingChange(bar: 1, bpm: 150, numerator: 2, denominator: 4)], to: original) }
        let late = try source(noteOffset: 3)
        try rejectedInBar(1) { _ = try MacTiming.applying([TimingChange(bar: 1, bpm: 150, numerator: 3, denominator: 4)], to: late) }
        let lateChord = try source(chordOffset: 3)
        try rejectedInBar(1) { _ = try MacTiming.applying([TimingChange(bar: 1, bpm: 150, numerator: 3, denominator: 4)], to: lateChord) }
        let pickup = try source(duration: 1)
        let pickupEdit = try MacTiming.applying([TimingChange(bar: 1, bpm: 150, numerator: 3, denominator: 8)], to: pickup)
        try require(pickupEdit.measures[0].duration.value == 1, "pickup duration retained")
        try rejected { _ = try MacTiming.applying([TimingChange(bar: 1, bpm: 150, numerator: 1, denominator: 8)], to: pickup) }
        let irregular = try source(duration: 5)
        let irregularEdit = try MacTiming.applying([TimingChange(bar: 1, bpm: 150, numerator: 4, denominator: 4)], to: irregular)
        try require(irregularEdit.measures[0].duration.value == 5, "unchanged irregular meter allows BPM editing")
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("chordcue-timing-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: folder) }
        let destination = folder.appendingPathComponent("project.json")
        try project.save(destination); try require(try MacScoreProject.load(destination).timingChanges == rows, "atomic v3 save")
        let future = Data(#"{"schemaVersion":99}"#.utf8); try future.write(to: destination)
        try rejected { try project.save(destination) }
        try require(try Data(contentsOf: destination) == future, "future destination preserved")
        print("Swift timing fixtures passed")
    }
}
