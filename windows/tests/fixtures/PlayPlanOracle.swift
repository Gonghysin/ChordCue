import Foundation

@main
struct PlayPlanOracle {
    static func main() {
        do {
            let source = FileHandle.standardInput.readDataToEndOfFile()
            let score = try ScoreIR.decodeValidated(source)
            let plan = try ScorePlayPlan(score: score)
            FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: plan.toDictionary(), options: [.sortedKeys]))
        } catch {
            FileHandle.standardError.write(Data("\(error)\n".utf8)); exit(1)
        }
    }
}
