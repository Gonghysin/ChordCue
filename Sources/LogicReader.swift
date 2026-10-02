import AppKit
import ApplicationServices

struct SongPosition: Comparable {
    let bar: Int
    let beat: Int
    let division: Int
    let tick: Int

    static let start = SongPosition(bar: 1, beat: 1, division: 1, tick: 0)

    var quarterNoteOffset: Double {
        Double(beat - 1) + Double(division - 1) / 4 + Double(tick) / 960
    }

    func alignedForChart(beatsPerBar: Int) -> SongPosition {
        let meter = max(1, beatsPerBar)
        let offsetTicks = quarterNoteOffset * 960
        let nearestBeat = Int((offsetTicks / 960).rounded())
        // Logic's analyzed chords may sit a few ticks either side of a beat.
        // Keep actual subdivisions; only remove this small boundary jitter.
        guard offsetTicks >= 0, abs(offsetTicks - Double(nearestBeat * 960)) <= 30 else { return self }
        return SongPosition(bar: bar + nearestBeat / meter, beat: nearestBeat % meter + 1,
                            division: 1, tick: 0)
    }

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

    static func alignedForChart(_ events: [ChordEvent], beatsPerBar: Int) -> [ChordEvent] {
        events.sorted { $0.position < $1.position }.map {
            ChordEvent(id: $0.id, position: $0.position.alignedForChart(beatsPerBar: beatsPerBar),
                       symbol: $0.symbol)
        }
    }
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
    @Published private(set) var position = SongPosition.start
    @Published private(set) var status = "正在连接 Logic…"
    @Published private(set) var projectInfo = ProjectInfo()
    @Published private(set) var transport = TransportSample()

    private var playheadFields: [String: AXUIElement] = [:]
    private var playheadThumb: AXUIElement?
    private var logicApplication: AXUIElement?
    private var projectWindow: AXUIElement?
    private var projectDocument: String?
    private var tempoField: AXUIElement?
    private var signatureField: AXUIElement?
    private var playButton: AXUIElement?
    private var cachedChordContainer: AXUIElement?
    private var previousSample: TransportSample?
    private var transportEpoch = 0
    private var lastPositionAdvance = 0.0
    private var broadcasting = false
    private var lastMetadataRead = Date.distantPast
    private var lastProjectInfo = ProjectInfo()
    private let readerQueue = DispatchQueue(label: "local.codex.chordcue.logic-reader", qos: .userInitiated)
    private var timer: DispatchSourceTimer?
    private var lastReload = Date.distantPast
    private static let chordPattern = try! NSRegularExpression(
        pattern: #"^(.*?) (\d+) bars(?: (\d+) beats)?(?: (\d+) divisions)?(?: (\d+) ticks)?\s*$"#
    )
    private static let positionPattern = try! NSRegularExpression(
        pattern: #"^(-?\d+) bars?(?: (\d+) beats?)?(?: (\d+) divisions?)?(?: (\d+) ticks?)?\s*$"#
    )

    init() {
        refresh()
        let timer = DispatchSource.makeTimerSource(queue: readerQueue)
        timer.schedule(deadline: .now() + 0.15, repeating: 0.15)
        timer.setEventHandler { [weak self] in
            self?.poll()
        }
        self.timer = timer
        timer.resume()
    }

    deinit { timer?.cancel() }

    func setBroadcasting(_ enabled: Bool) {
        readerQueue.async { [weak self] in
            guard let self else { return }
            self.broadcasting = enabled
            self.timer?.schedule(deadline: .now(), repeating: enabled ? 0.05 : 0.15)
        }
    }

    private func invalidateTransport() {
        previousSample = nil
        lastPositionAdvance = 0
        transportEpoch += 1
        var sample = TransportSample()
        sample.discontinuity = transportEpoch
        DispatchQueue.main.async { [weak self] in self?.transport = sample }
    }

    private func updateStatus(_ message: String) {
        DispatchQueue.main.async { [weak self] in self?.status = message }
    }

    func requestPermission() {
        let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
        _ = AXIsProcessTrustedWithOptions(options)
        refresh()
    }

    func refresh() {
        readerQueue.async { [weak self] in self?.reloadOnReaderQueue() }
    }

    private func reloadOnReaderQueue() {
        lastReload = Date()
        guard AXIsProcessTrusted() else {
            invalidateTransport()
            playheadFields = [:]
            playheadThumb = nil
            cachedChordContainer = nil
            updateStatus("需要在系统设置中允许 ChordCue 使用辅助功能")
            return
        }
        guard let logic = NSWorkspace.shared.runningApplications.first(where: { $0.bundleIdentifier == "com.apple.logic10" }) else {
            invalidateTransport()
            updateStatus("未找到运行中的 Logic Pro")
            playheadFields = [:]
            playheadThumb = nil
            logicApplication = nil
            projectWindow = nil
            projectDocument = nil
            tempoField = nil
            signatureField = nil
            cachedChordContainer = nil
            publishProjectInfo(ProjectInfo())
            return
        }
        let application = AXUIElementCreateApplication(logic.processIdentifier)
        AXUIElementSetMessagingTimeout(application, 0.25)
        logicApplication = application
        let windows = attribute(application, kAXWindowsAttribute as CFString) as? [AXUIElement] ?? []
        let mainWindow = attribute(application, kAXMainWindowAttribute as CFString).map { $0 as! AXUIElement }
        let selectedWindow = mainWindow.flatMap { window in
            attribute(window, kAXDocumentAttribute as CFString) != nil ? window : nil
        } ?? windows.first { attribute($0, kAXDocumentAttribute as CFString) != nil }
        let document = selectedWindow.flatMap { attribute($0, kAXDocumentAttribute as CFString) as? String }
        let unchangedWindow = selectedWindow != nil && projectWindow != nil
            && CFEqual(selectedWindow, projectWindow) && document == projectDocument
        if unchangedWindow, let container = cachedChordContainer,
           !playheadFields.isEmpty || playheadThumb != nil,
           let children = attribute(container, kAXChildrenAttribute as CFString) as? [AXUIElement] {
            readProjectInfo()
            publishChords(children, deadline: Date().addingTimeInterval(2))
            return
        }
        invalidateTransport()
        if !unchangedWindow {
            DispatchQueue.main.async { [weak self] in self?.chords = [] }
        }
        cachedChordContainer = nil
        projectWindow = selectedWindow
        projectDocument = document
        var chordContainer: AXUIElement?
        var fields: [String: AXUIElement] = [:]
        var thumb: AXUIElement?
        var tempo: AXUIElement?
        var signature: AXUIElement?
        var play: AXUIElement?
        let deadline = Date().addingTimeInterval(4)
        var visited = 0

        func visit(_ element: AXUIElement, depth: Int) {
            if depth > 12 || visited >= 12000 || Date() > deadline { return }
            if chordContainer != nil && thumb != nil && fields["bar"] != nil && tempo != nil && signature != nil { return }
            visited += 1
            let description = attribute(element, kAXDescriptionAttribute as CFString) as? String ?? ""
            if description == "Tempo", tempo == nil { tempo = element }
            if description == "Time Signature", signature == nil { signature = element }
            if description == "Play", play == nil { play = element }
            if play == nil, attribute(element, kAXTitleAttribute as CFString) as? String == "Play" {
                play = element
            }
            if description == "chord container" {
                chordContainer = element
                return
            }
            if description == "Playhead thumb", thumb == nil { thumb = element }
            let children = attribute(element, kAXChildrenAttribute as CFString) as? [AXUIElement] ?? []
            if description == "Playhead Position" {
                var found: [String: AXUIElement] = [:]
                for child in children {
                    if let name = attribute(child, kAXDescriptionAttribute as CFString) as? String,
                       ["bar", "beat", "division", "tick"].contains(name) {
                        found[name] = child
                    }
                }
                if found["bar"] != nil && found["beat"] != nil && found.count > fields.count {
                    fields = found
                }
            }
            for child in children { visit(child, depth: depth + 1) }
        }

        if let selectedWindow { visit(selectedWindow, depth: 0) }
        playheadFields = fields
        playheadThumb = thumb
        tempoField = tempo
        signatureField = signature
        playButton = play
        readProjectInfo()
        guard let chordContainer else {
            updateStatus(Date() > deadline ? "Logic 读取超时，正在重试…" : "未读到和弦轨，请在 Logic 显示全局和弦轨")
            return
        }
        cachedChordContainer = chordContainer
        let children = attribute(chordContainer, kAXChildrenAttribute as CFString) as? [AXUIElement] ?? []
        publishChords(children, deadline: deadline)
    }

    private func publishChords(_ children: [AXUIElement], deadline: Date) {
        let events = children.enumerated().compactMap { index, child -> ChordEvent? in
            if Date() > deadline { return nil }
            guard let description = attribute(child, kAXDescriptionAttribute as CFString) as? String else { return nil }
            return Self.parseChord(description, id: index)
        }.sorted { $0.position < $1.position }
        guard Date() <= deadline else {
            updateStatus("Logic 读取超时，正在重试…")
            return
        }
        DispatchQueue.main.async { [weak self] in self?.chords = events }
        updateStatus(readPosition()
            ? "Logic 已连接 · \(events.count) 个和弦"
            : "读到 \(events.count) 个和弦，请在 Logic 顶部显示小节与拍")
    }

    private func poll() {
        if Date().timeIntervalSince(lastMetadataRead) >= 0.25 {
            if let application = logicApplication,
               let mainWindow = attribute(application, kAXMainWindowAttribute as CFString),
               attribute(mainWindow as! AXUIElement, kAXDocumentAttribute as CFString) != nil,
               projectWindow == nil || !CFEqual(mainWindow, projectWindow)
                || (attribute(mainWindow as! AXUIElement, kAXDocumentAttribute as CFString) as? String) != projectDocument {
                reloadOnReaderQueue()
                return
            }
            readProjectInfo()
        }
        let interval = playheadFields.isEmpty && playheadThumb == nil ? 2.0 : 5.0
        if Date().timeIntervalSince(lastReload) > interval {
            reloadOnReaderQueue()
        } else if !playheadFields.isEmpty || playheadThumb != nil {
            if !readPosition() {
                invalidateTransport()
                playheadFields = [:]
                playheadThumb = nil
                updateStatus("播放头连接中断，正在重新连接…")
            }
        }
    }

    private func publishProjectInfo(_ info: ProjectInfo) {
        guard info != lastProjectInfo else { return }
        lastProjectInfo = info
        DispatchQueue.main.async { [weak self] in self?.projectInfo = info }
    }

    private func readProjectInfo() {
        lastMetadataRead = Date()
        guard let window = projectWindow else { return }
        var info = ProjectInfo()
        if let document = attribute(window, kAXDocumentAttribute as CFString) as? String,
           let url = URL(string: document) {
            info.name = url.deletingPathExtension().lastPathComponent
        } else if let title = attribute(window, kAXTitleAttribute as CFString) as? String {
            info.name = title
        }
        if let tempoField {
            let value = attribute(tempoField, kAXValueAttribute as CFString)
            info.bpm = (value as? NSNumber)?.doubleValue ?? (value as? String).flatMap(Double.init)
        }
        if let signatureField {
            info.timeSignature = attribute(signatureField, kAXValueAttribute as CFString) as? String
        }
        publishProjectInfo(info)
    }

    @discardableResult
    private func readPosition() -> Bool {
        let started = ProcessInfo.processInfo.systemUptime * 1000
        func publish(_ parsed: SongPosition, precise: Bool) {
            let playing = playButton.flatMap { attribute($0, kAXValueAttribute as CFString) as? NSNumber }?.boolValue
            let ended = ProcessInfo.processInfo.systemUptime * 1000
            var sample = TransportSample(position: parsed, time: (started + ended) / 2,
                                         readMilliseconds: ended - started, valid: true, precise: precise)
            if precise, playing != false, let previous = previousSample, previous.valid, previous.precise {
                let elapsed = (sample.time - previous.time) / 1000
                let numerator = Int(lastProjectInfo.timeSignature?.split(separator: "/").first ?? "4") ?? 4
                let denominator = Int(lastProjectInfo.timeSignature?.split(separator: "/").last ?? "4") ?? 4
                let meter = Double(max(1, numerator))
                func beat(_ p: SongPosition) -> Double {
                    Double(p.bar - 1) * meter + Double(p.beat - 1)
                        + Double(p.division - 1) / 4 + Double(p.tick) / 960
                }
                let delta = beat(parsed) - beat(previous.position)
                let rate = delta / max(0.001, elapsed)
                // Predict only measured forward motion; stopping, jumps and stale reads snap to Logic.
                let tempoRate = (lastProjectInfo.bpm ?? 120) / 60
                if delta > 0 { lastPositionAdvance = sample.time }
                let expected = tempoRate * elapsed
                let continuous = delta >= 0 && abs(delta - expected) < max(0.25, tempoRate * 0.15)
                if denominator == 4, elapsed > 0.015, elapsed < 0.3,
                   continuous, sample.readMilliseconds < 80 {
                    if let bpm = lastProjectInfo.bpm, bpm.isFinite, bpm > 0,
                       playing == true || (delta > 0 && rate > 0.1 && rate < 12)
                        || (previous.playing && sample.time - lastPositionAdvance < 150) {
                        // Logic supplies the tempo. AX repaint intervals are not an audio clock.
                        sample.beatsPerSecond = bpm / 60
                    } else if delta > 0, rate > 0.1, rate < 12 {
                        sample.beatsPerSecond = rate
                    }
                }
            }
            sample.playing = playing ?? (sample.beatsPerSecond > 0)
            if let previous = previousSample {
                let elapsed = (sample.time - previous.time) / 1000
                if previous.playing != sample.playing || elapsed > 0.3
                    || (sample.playing && parsed != previous.position && sample.beatsPerSecond == 0) {
                    transportEpoch += 1
                }
            }
            sample.discontinuity = transportEpoch
            previousSample = sample
            DispatchQueue.main.async { [weak self] in
                self?.position = parsed
                self?.transport = sample
            }
        }
        if let thumb = playheadThumb,
           let text = attribute(thumb, kAXValueDescriptionAttribute as CFString) as? String,
           let parsed = Self.parsePosition(text) {
            publish(parsed, precise: true)
            return true
        }
        func number(_ key: String) -> Int? {
            guard let field = playheadFields[key],
                  let value = attribute(field, kAXValueAttribute as CFString) as? NSNumber else { return nil }
            return value.intValue
        }
        guard let bar = number("bar"), let beat = number("beat") else { return false }
        let parsed = SongPosition(bar: bar, beat: beat,
                                  division: number("division") ?? 1, tick: number("tick") ?? 0)
        publish(parsed, precise: playheadFields["division"] != nil && playheadFields["tick"] != nil)
        return true
    }

    static func parsePosition(_ text: String) -> SongPosition? {
        let range = NSRange(text.startIndex..<text.endIndex, in: text)
        guard let match = positionPattern.firstMatch(in: text, range: range),
              let barRange = Range(match.range(at: 1), in: text),
              let bar = Int(text[barRange]) else { return nil }
        func component(_ index: Int, default fallback: Int) -> Int {
            guard let range = Range(match.range(at: index), in: text) else { return fallback }
            return Int(text[range]) ?? fallback
        }
        return SongPosition(bar: bar, beat: component(2, default: 1),
                            division: component(3, default: 1), tick: component(4, default: 0))
    }

    private func attribute(_ element: AXUIElement, _ key: CFString) -> AnyObject? {
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, key, &value) == .success else { return nil }
        return value
    }

    static func parseChord(_ text: String, id: Int) -> ChordEvent? {
        let range = NSRange(text.startIndex..<text.endIndex, in: text)
        guard let match = chordPattern.firstMatch(in: text, range: range),
              let nameRange = Range(match.range(at: 1), in: text),
              let barRange = Range(match.range(at: 2), in: text),
              let bar = Int(text[barRange]) else { return nil }
        func component(_ index: Int, default fallback: Int) -> Int {
            guard let range = Range(match.range(at: index), in: text) else { return fallback }
            return Int(text[range]) ?? fallback
        }
        return ChordEvent(id: id,
                          position: SongPosition(bar: bar, beat: component(3, default: 1),
                                                 division: component(4, default: 1), tick: component(5, default: 0)),
                          symbol: symbol(for: String(text[nameRange])))
    }

    private static func symbol(for name: String) -> String {
        if name == "No Chord" { return "N.C." }
        let split = name.split(separator: "/", maxSplits: 1).map(String.init)
        let main = split[0]
        let words = main.split(separator: " ").map(String.init)
        guard let first = words.first, first.count == 1,
              let letter = first.first, ("a"..."g").contains(String(letter)) else { return name }
        var index = 1
        var root = first.uppercased()
        if index < words.count && (words[index] == "flat" || words[index] == "sharp") {
            root += words[index] == "flat" ? "♭" : "♯"
            index += 1
        }
        let quality = words.dropFirst(index).joined(separator: " ")
        let suffix: String
        switch quality {
        case "", "major": suffix = ""
        case "major 7": suffix = "7"
        case "major major 7": suffix = "maj7"
        case "major 6": suffix = "6"
        case "minor": suffix = "m"
        case "minor 7": suffix = "m7"
        case "minor 7 flat 5", "half diminished", "half diminished 7": suffix = "m7(b5)"
        case "minor 6": suffix = "m6"
        case "minor major 7": suffix = "m(maj7)"
        case "major 9": suffix = "9"
        case "major major 9": suffix = "maj9"
        case "minor 9": suffix = "m9"
        case "diminished": suffix = "dim"
        case "diminished 7": suffix = "dim7"
        case "augmented": suffix = "aug"
        case "suspended 2": suffix = "sus2"
        case "suspended 4": suffix = "sus4"
        case "augmented 7": suffix = "aug7"
        case "5": suffix = "5"
        default: suffix = " \(quality)"
        }
        var bass = ""
        if split.count == 2 {
            bass = "/" + split[1].trimmingCharacters(in: .whitespaces).uppercased()
                .replacingOccurrences(of: " SHARP", with: "♯")
                .replacingOccurrences(of: " FLAT", with: "♭")
        }
        return root + suffix + bass
    }
}
