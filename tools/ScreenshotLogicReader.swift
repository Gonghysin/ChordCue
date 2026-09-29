// Screenshot-only data source. Build in place of Sources/LogicReader.swift.
// No Accessibility access, host connection, network requests, or song data.
import AppKit
import ApplicationServices

struct SongPosition: Comparable {
    let bar: Int
    let beat: Int
    let division: Int
    let tick: Int

    static let start = SongPosition(bar: 1, beat: 1, division: 1, tick: 0)

    static func < (lhs: SongPosition, rhs: SongPosition) -> Bool {
        [lhs.bar, lhs.beat, lhs.division, lhs.tick].lexicographicallyPrecedes(
            [rhs.bar, rhs.beat, rhs.division, rhs.tick]
        )
    }
}

struct ChordEvent: Identifiable, Equatable {
    let id: Int
    let position: SongPosition
    let symbol: String
}

struct ProjectInfo: Equatable {
    var name = "未连接工程"
    var timeSignature: String?
    var bpm: Double?

    var bpmLabel: String {
        guard let bpm, bpm.isFinite, bpm > 0 else { return "— BPM" }
        let rounded = (bpm * 100).rounded() / 100
        let value = rounded == rounded.rounded()
            ? String(format: "%.0f", rounded) : String(format: "%.2f", rounded)
        return "\(value) BPM"
    }
}

struct TransportSample: Equatable {
    var position = SongPosition.start
    var time = ProcessInfo.processInfo.systemUptime * 1000
    var readMilliseconds = 0.0
    var beatsPerSecond = 0.0
    var valid = false
    var precise = false
    var playing = false
    var discontinuity = 0
}


final class LogicReader: ObservableObject {
    @Published private(set) var chords: [ChordEvent] = []
    @Published private(set) var position = SongPosition(bar: 5, beat: 3, division: 1, tick: 0)
    @Published private(set) var status = "原创演示数据 · 不连接宿主"
    @Published private(set) var projectInfo = ProjectInfo(name: "ChordCue 演示 · C 大调", timeSignature: "4/4", bpm: 120)
    @Published private(set) var transport = TransportSample()
    private var timer: Timer?
    private var sizedWindow = false

    init() {
        let bars: [[String]] = [
            ["Cmaj7"], ["Am7"], ["Fmaj7"], ["G7"],
            ["C", "G/B"], ["Am7", "D7"], ["Dm7", "G7"], ["Cmaj7"],
            ["Fmaj7"], ["Em7", "Am7"], ["Dm7"], ["G7"],
            ["Cmaj7", "Am7"], ["Dm7", "G7"], ["C", "F"], ["Cmaj7"]
        ]
        for (bar, symbols) in bars.enumerated() {
            for (part, symbol) in symbols.enumerated() {
                chords.append(ChordEvent(id: chords.count,
                    position: SongPosition(bar: bar + 1, beat: part * 2 + 1, division: 1, tick: 0),
                    symbol: symbol))
            }
        }
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { [weak self] _ in self?.refresh() }
    }

    deinit { timer?.invalidate() }
    func refresh() {
        if !sizedWindow, let window = NSApplication.shared.windows.first {
            sizedWindow = true
            DispatchQueue.main.asyncAfter(deadline: .now() + 1) {
                window.setContentSize(NSSize(width: 900, height: 750))
                window.center()
            }
        }
        var value = TransportSample()
        value.position = position
        value.valid = true
        value.precise = true
        transport = value
    }
    func requestPermission() {}
    func setBroadcasting(_ enabled: Bool) {}
    static func parseChord(_ text: String, id: Int) -> ChordEvent? { nil }
}
