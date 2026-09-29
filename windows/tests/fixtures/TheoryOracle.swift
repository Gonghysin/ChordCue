// Compile with the actual macOS source, not a copied theory implementation:
// swiftc -parse-as-library Sources/LogicReader.swift Sources/ChordTheory.swift \
//   windows/tests/fixtures/TheoryOracle.swift -o /tmp/chordcue-theory-oracle
import Foundation

@main
struct TheoryOracle {
    static func main() throws {
        guard CommandLine.arguments.count == 2 else {
            throw NSError(domain: "TheoryOracle", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: "Expected theory_cases.json path"])
        }
        let data = try Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1]))
        let fixture = try JSONSerialization.jsonObject(with: data) as! [String: Any]
        func keyFromJSON(_ value: Any?) -> MusicalKey? {
            guard let item = value as? [String: Any] else { return nil }
            return MusicalKey(root: item["root"] as! Int, isMinor: item["minor"] as! Bool)
        }
        func keyJSON(_ key: MusicalKey) -> [String: Any] {
            ["root": key.root, "minor": key.isMinor, "family": key.majorFamilyRoot,
             "label": key.label, "selectionLabel": key.selectionLabel]
        }
        func sectionsJSON(_ sections: [KeySection]) -> [[String: Any]] {
            sections.map { ["bar": $0.firstBar, "key": keyJSON($0.key)] }
        }
        var result: [String: Any] = [:]
        result["noteNames"] = (fixture["noteNames"] as! [[String: Any]]).map {
            ChordTheory.noteName($0["pitch"] as! Int, preferFlats: $0["preferFlats"] as! Bool)
        }
        result["transposes"] = (fixture["transposes"] as! [[String: Any]]).map {
            ChordTheory.transpose($0["symbol"] as! String, by: $0["semitones"] as! Int,
                                  preferFlats: $0["preferFlats"] as! Bool)
        }
        result["displays"] = (fixture["displays"] as! [String]).map { ChordTheory.displayChord($0) }
        result["numbers"] = (fixture["numbers"] as! [[String: Any]]).map {
            ChordTheory.number($0["symbol"] as! String, in: keyFromJSON($0["key"])!)
        }
        result["keys"] = (fixture["keys"] as! [String]).map { text -> Any in
            guard let key = ChordTheory.key(named: text) else { return NSNull() }
            return keyJSON(key)
        }
        result["snapshots"] = (fixture["snapshots"] as! [[String: Any]]).map { item -> [String: Any] in
            let text = item["text"] as! String
            let chords = text.components(separatedBy: .newlines).enumerated().compactMap { index, line in
                LogicReader.parseChord(line.replacingOccurrences(of: "EXPORT|", with: ""), id: index)
            }.sorted { $0.position < $1.position }
            let meter = item["meter"] as! Int
            let forced = keyFromJSON(item["forcedKey"])
            let sections = ChordTheory.sections(for: chords, forcedKey: forced, detectChanges: true, beatsPerBar: meter)
            let global = ChordTheory.sections(for: chords, forcedKey: forced, detectChanges: false, beatsPerBar: meter)
            let manual = item["manualSections"] as! [[String: Any]]
            let manualText = manual.map { change -> String in
                let key = keyFromJSON(change["key"])!
                return "\(change["bar"] as! Int) " + ChordTheory.noteName(key.root, preferFlats: false)
                    + (key.isMinor ? "m" : "")
            }.joined(separator: "\n")
            return [
                "name": item["name"]!,
                "chords": chords.map { event -> [String: Any] in
                    ["id": event.id, "bar": event.position.bar, "beat": event.position.beat,
                     "division": event.position.division, "tick": event.position.tick, "symbol": event.symbol]
                },
                "global": sectionsJSON(global), "sections": sectionsJSON(sections),
                "manual": sectionsJSON(ChordTheory.sectionsWithManualChanges(manualText, autoSections: sections)),
                "degrees": chords.map { event in
                    let key = sections.last { $0.firstBar <= event.position.bar }!.key
                    return ChordTheory.number(event.symbol, in: key)
                },
            ]
        }
        let output = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])
        print(String(decoding: output, as: UTF8.self))
    }
}
