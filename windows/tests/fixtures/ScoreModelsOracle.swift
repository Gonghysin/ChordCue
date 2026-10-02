import Foundation

@main
struct ScoreModelsOracle {
    static func main() {
        do {
            let source = FileHandle.standardInput.readDataToEndOfFile()
            let score = try ScoreIR.decodeValidated(source)
            let canonical = try score.encodedValidated()
            FileHandle.standardOutput.write(canonical)
        } catch {
            FileHandle.standardError.write(Data("\(error)\n".utf8))
            exit(1)
        }
    }
}
