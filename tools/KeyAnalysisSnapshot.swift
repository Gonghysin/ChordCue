import Foundation

@main
struct KeyAnalysisSnapshot {
    static func main() throws {
        guard CommandLine.arguments.count >= 2 else {
            throw NSError(domain: "ChordCue", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: "需要 Logic 和弦快照路径。"])
        }
        let text = try String(contentsOfFile: CommandLine.arguments[1], encoding: .utf8)
        let chords = text.components(separatedBy: .newlines).enumerated().compactMap { index, line in
            LogicReader.parseChord(line.replacingOccurrences(of: "EXPORT|", with: ""), id: index)
        }.sorted { $0.position < $1.position }
        guard !chords.isEmpty else {
            throw NSError(domain: "ChordCue", code: 2,
                          userInfo: [NSLocalizedDescriptionKey: "快照没有可解析的和弦。"])
        }
        let meter = CommandLine.arguments.count > 2 ? Int(CommandLine.arguments[2]) ?? 4 : 4
        func sectionJSON(_ sections: [KeySection]) -> [[String: Any]] {
            sections.map { ["bar": $0.firstBar, "key": $0.key.selectionLabel,
                            "family": $0.key.majorFamilyRoot] }
        }
        let automatic = ChordTheory.sections(for: chords, forcedKey: nil, detectChanges: true, beatsPerBar: meter)
        let result: [String: Any] = [
            "global": sectionJSON(ChordTheory.sections(for: chords, forcedKey: nil, detectChanges: false, beatsPerBar: meter)),
            "sections": sectionJSON(automatic),
            "slashDegrees": chords.filter { $0.symbol.contains("/") }.map { event -> [String: Any] in
                let key = automatic.last { $0.firstBar <= event.position.bar }?.key
                    ?? MusicalKey(root: 0, isMinor: false)
                return ["bar": event.position.bar, "symbol": event.symbol, "key": key.selectionLabel,
                        "degree": ChordTheory.number(event.symbol, in: key)]
            },
            "chords": chords.map { ["symbol": $0.symbol, "bar": $0.position.bar, "beat": $0.position.beat,
                                     "division": $0.position.division, "tick": $0.position.tick] }
        ]
        let data = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])
        print(String(decoding: data, as: UTF8.self))
    }
}
