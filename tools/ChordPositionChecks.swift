import Foundation

@main
struct ChordPositionChecks {
    static func main() {
        let cases: [(SongPosition, Int, SongPosition)] = [
            // Two ticks before the next bar, as reported by Logic.
            (.init(bar: 4, beat: 4, division: 4, tick: 238), 4, .init(bar: 5, beat: 1, division: 1, tick: 0)),
            (.init(bar: 9, beat: 4, division: 4, tick: 238), 4, .init(bar: 10, beat: 1, division: 1, tick: 0)),
            (.init(bar: 12, beat: 2, division: 4, tick: 229), 4, .init(bar: 12, beat: 3, division: 1, tick: 0)),
            (.init(bar: 8, beat: 1, division: 1, tick: 13), 4, .init(bar: 8, beat: 1, division: 1, tick: 0)),
            // Exact tolerance boundary; farther anticipations remain untouched.
            (.init(bar: 4, beat: 4, division: 4, tick: 210), 4, .init(bar: 5, beat: 1, division: 1, tick: 0)),
            (.init(bar: 4, beat: 4, division: 4, tick: 209), 4, .init(bar: 4, beat: 4, division: 4, tick: 209)),
            (.init(bar: 4, beat: 4, division: 4, tick: 180), 4, .init(bar: 4, beat: 4, division: 4, tick: 180)),
            // Eighth-note changes must not be rounded to quarter notes.
            (.init(bar: 4, beat: 2, division: 3, tick: 0), 4, .init(bar: 4, beat: 2, division: 3, tick: 0)),
            (.init(bar: 4, beat: 4, division: 3, tick: 0), 4, .init(bar: 4, beat: 4, division: 3, tick: 0)),
            (.init(bar: 4, beat: 3, division: 4, tick: 238), 3, .init(bar: 5, beat: 1, division: 1, tick: 0)),
            (.init(bar: 4, beat: 5, division: 4, tick: 238), 5, .init(bar: 5, beat: 1, division: 1, tick: 0))
        ]
        for (position, meter, expected) in cases {
            let actual = position.alignedForChart(beatsPerBar: meter)
            precondition(actual == expected, "\(position) -> \(actual), expected \(expected)")
            precondition(actual.alignedForChart(beatsPerBar: meter) == actual, "Alignment must be idempotent")
        }
        let raw = LogicReader.parseChord("d major 4 bars 4 beats 4 divisions 238 ticks", id: 7)!
        let aligned = ChordEvent.alignedForChart([raw], beatsPerBar: 4)[0]
        precondition(raw.position.bar == 4 && raw.position.tick == 238, "Keep the original host data")
        precondition(aligned.position.bar == 5 && aligned.position.beat == 1)
        precondition(aligned.id == raw.id && aligned.symbol == raw.symbol)
        precondition(SongPosition(bar: 1, beat: 2, division: 3, tick: 0).quarterNoteOffset == 1.5)
        print("Passed \(cases.count) boundary/subdivision cases, idempotence, parsing and raw-data preservation.")
    }
}
