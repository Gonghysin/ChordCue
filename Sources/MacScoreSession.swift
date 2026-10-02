import SwiftUI
import WebKit
import CryptoKit

// Uses only packaged scripts/fonts. A failed import/route leaves the active project intact.
final class MacScoreSession: ObservableObject {
    @Published private(set) var project: MacScoreProject?
    @Published var pendingScore: ScoreIR?
    @Published var pendingPartId = ""
    @Published var previewKind = "staff"
    @Published private(set) var importing = false
    @Published private(set) var status = "导入 Guitar Pro / MusicXML 后可独立播放"
    @Published var error: String?
    @Published private(set) var sample = TransportSample()
    @Published private(set) var info = ProjectInfo()
    @Published private(set) var route: [String: Any] = [:]
    private(set) var chartRevision = 0
    private(set) var routePlan: [String: Any] = [:]
    private var routeId = ""
    private var chordCache: [ChordEvent] = []
    private var keyCache: [KeySection] = []
    private(set) var chordKeys: [Int: MusicalKey] = [:]
    private(set) var firstSourceKey: MusicalKey?
    let renderer = MacScoreWebController()
    let previewRenderer = MacScoreWebController()
    private let importer = MacScoreWebController()
    private var engine: StandaloneTransport?
    private var playbackSource: ScoreIR?
    private(set) var manualMeasures: [[String: Any]] = []
    private(set) var measureBPMs: [String: Double] = [:]
    private var timer: Timer?
    private var importEpoch = UUID()
    init() {
        timer = Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { [weak self] _ in self?.updateSnapshot() }
        renderer.onSeek = { [weak self] id, offset in self?.seek(measureId: id, offset: offset) }
    }
    deinit { timer?.invalidate() }
    var score: ScoreIR? { project?.score }
    var selectedPartId: String? { project?.selectedPartId ?? score?.parts.first?.id }
    var selectedPart: ScorePart? { score?.parts.first { $0.id == selectedPartId } }
    var effectiveMeasures: [ScoreMeasure] { playbackSource?.measures ?? [] }
    var canPlay: Bool { engine != nil }
    var timingRows: [TimingChange] { project?.timingRows ?? [] }
    var chords: [ChordEvent] { chordCache }
    var sourceKeySections: [KeySection] { keyCache }
    private func refreshDisplayCaches() {
        chordCache = buildChords; keyCache = buildKeySections; chordKeys = [:]; firstSourceKey = nil
        guard let score, let part = selectedPart else { return }
        let indices = Dictionary(uniqueKeysWithValues: score.measures.enumerated().map { ($0.element.id, $0.offset + 1) })
        let changes = score.keyChanges.sorted { lhs, rhs in
            let left = indices[lhs.measureId] ?? 0, right = indices[rhs.measureId] ?? 0
            return left == right ? lhs.offset.value < rhs.offset.value : left < right
        }
        if let first = changes.first, first.mode != "unknown" {
            let family = (7 * first.fifths % 12 + 12) % 12
            firstSourceKey = MusicalKey(root: (family + (first.mode == "minor" ? 9 : 0)) % 12, isMinor: first.mode == "minor")
        }
        var cursor = 0
        var key: MusicalKey? = nil
        for chord in chordCache {
            let offset = part.chords[chord.id].offset.value
            while cursor < changes.count {
                let change = changes[cursor], bar = indices[change.measureId] ?? 0
                if bar > chord.position.bar || bar == chord.position.bar && change.offset.value > offset { break }
                let family = (7 * change.fifths % 12 + 12) % 12
                key = change.mode == "unknown" ? nil : MusicalKey(root: (family + (change.mode == "minor" ? 9 : 0)) % 12, isMinor: change.mode == "minor")
                cursor += 1
            }
            if let key { chordKeys[chord.id] = key }
        }
    }
    private var buildChords: [ChordEvent] {
        guard let score else { return project?.legacyChords ?? [] }
        let indices = Dictionary(uniqueKeysWithValues: score.measures.enumerated().map { ($0.element.id, $0.offset + 1) })
        let annotations = selectedPart?.chords ?? []
        return annotations.enumerated().compactMap { index, chord -> ChordEvent? in
            guard let bar = indices[chord.measureId] else { return nil }
            let tick = Int((chord.offset.value * 960).rounded())
            return ChordEvent(id: index, position: SongPosition(bar: bar, beat: tick / 960 + 1,
                                                              division: tick % 960 / 240 + 1, tick: tick % 240), symbol: chord.text)
        }.sorted { lhs, rhs in
            if lhs.position.bar != rhs.position.bar { return lhs.position.bar < rhs.position.bar }
            return annotations[lhs.id].offset.value < annotations[rhs.id].offset.value
        }
    }
    private var buildKeySections: [KeySection] {
        guard let score else { return [] }
        let indices = Dictionary(uniqueKeysWithValues: score.measures.enumerated().map { ($0.element.id, $0.offset + 1) })
        return score.keyChanges.compactMap { key -> KeySection? in
            guard key.mode != "unknown", let bar = indices[key.measureId] else { return nil }
            let major = (7 * key.fifths % 12 + 12) % 12
            return KeySection(firstBar: bar, key: MusicalKey(root: (major + (key.mode == "minor" ? 9 : 0)) % 12, isMinor: key.mode == "minor"))
        }
    }
    static func availableViews(_ part: ScorePart) -> [String] {
        var result = ["staff", "metronome"]
        if !part.chords.isEmpty { result.insert(contentsOf: ["chords", "numbers"], at: 0) }
        if part.staves.contains(where: { !$0.tuning.isEmpty && $0.events.contains(where: { $0.notes.contains(where: { $0.string != nil }) }) }) { result.append("tab") }
        return result
    }
    func importFile(_ url: URL) {
        cancelImport(); let epoch = UUID(); importEpoch = epoch; importing = true; status = "正在离线解析 \(url.lastPathComponent)…"
        do {
            let handle = try FileHandle(forReadingFrom: url); defer { try? handle.close() }
            let bytes = try handle.read(upToCount: MacScoreProject.maximumBytes + 1) ?? Data()
            guard !bytes.isEmpty, bytes.count <= MacScoreProject.maximumBytes else { throw ScoreValidationError.invalid("谱面文件为空或超过 32 MiB 限制") }
            let hash = SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined()
            importer.importBytes(bytes, name: url.lastPathComponent, hash: hash) { [weak self] result in
                guard let self, self.importEpoch == epoch else { return }
                self.importing = false
                switch result {
                case .success(let score): self.pendingScore = score; self.pendingPartId = score.parts.first?.id ?? ""; self.previewKind = "staff"; self.status = "预览并选择声部后导入"
                case .failure(let error): self.error = String(describing: error); self.status = "导入失败，原项目保持不变"
                }
            }
        } catch { importing = false; self.error = String(describing: error); status = "导入失败，原项目保持不变" }
    }
    func cancelImport() {
        importEpoch = UUID(); importing = false; importer.cancelImport(); pendingScore = nil
    }
    @discardableResult func acceptPreview() -> Bool {
        guard let score = pendingScore else { return false }
        do {
            let project = try MacScoreProject(score: score, selectedPartId: pendingPartId)
            try install(project); pendingScore = nil; return true
        } catch { self.error = String(describing: error); return false }
    }
    @discardableResult func openProject(_ url: URL) -> Bool {
        do { let project = try MacScoreProject.load(url); try install(project); return true }
        catch { self.error = String(describing: error); return false }
    }
    func saveProject(_ url: URL) {
        guard let project else { return }
        do { try project.save(url); status = "已保存完整工程：\(url.lastPathComponent)" }
        catch { self.error = String(describing: error) }
    }
    @discardableResult func createManualProject(name: String, bars: Int, chords: [ChordEvent]) -> Bool {
        do { try install(MacScoreProject(name: name, bars: bars, chords: chords)); return true }
        catch { self.error = String(describing: error); return false }
    }
    @discardableResult func applyTiming(_ changes: [TimingChange]) -> Bool {
        guard let project else { return false }
        do {
            let candidate = try project.applyingTiming(changes)
            try install(candidate, preservePosition: true)
            error = nil; status = "已应用按小节 BPM/拍号预设，播放已暂停"; return true
        } catch { self.error = String(describing: error); return false }
    }
    private func install(_ candidate: MacScoreProject, preservePosition: Bool = false) throws {
        _ = try candidate.encodedValidated()
        // Legacy v1 text/size limits predate ScoreIR. Keep those projects readable
        // when only their optional playback adapter cannot satisfy the newer limits.
        var source: ScoreIR? = nil, unavailableReason: String? = nil
        if candidate.score == nil && candidate.timingChanges.isEmpty {
            if candidate.bars > 10_000 { unavailableReason = "独立播放最多支持 10000 小节" }
            else {
                do { source = try candidate.playbackScore() }
                catch { unavailableReason = "此旧工程无法生成独立播放时间线：\(error)" }
            }
        } else { source = try candidate.playbackScore() }
        let next = try source.map { try StandaloneTransport(score: $0, selectedPartId: candidate.selectedPartId) }
        if let loop = candidate.loopRange { try next?.setLoop(startSourceIndex: loop.start, endSourceIndexExclusive: loop.endExclusive) }
        if preservePosition, let old = engine, let next, let source {
            try next.setTempoScale(old.plan.tempoScale)
            let wire = old.snapshot(includeRoute: false), bar = (wire["bar"] as? Int) ?? 1
            if bar == old.score.measures.count + 1 { try next.seekQuarter(next.plan.endQuarter) }
            else if source.measures.indices.contains(bar - 1) {
                let offset = (wire["sourceOffsetQuarter"] as? Double) ?? 0
                let valid = min(offset, max(0, source.measures[bar - 1].duration.value.nextDown))
                let identifier = wire["occurrenceId"] as? String
                let occurrences = old.plan.occurrences.filter { $0.sourceIndex == bar }
                let occurrence = occurrences.firstIndex(where: { $0.id == identifier }).map { $0 + 1 } ?? 1
                try next.seek(sourceIndex: bar, offsetQuarter: valid, occurrence: occurrence)
            }
        }
        var nextBPMs: [String: Double] = [:]
        if let source {
            let rows = try TimingChange.expanded(MacTiming.rows(for: source), bars: source.measures.count)
            nextBPMs = Dictionary(uniqueKeysWithValues: source.measures.enumerated().map { ($0.element.id, rows[$0.offset].bpm) })
        }
        // Every fallible step finishes before retiring the current engine.
        engine?.stop(); engine = next; playbackSource = source; project = candidate
        manualMeasures = candidate.score == nil ? MacTiming.metadata(source?.measures ?? []) : []
        measureBPMs = nextBPMs
        if next == nil { sample = TransportSample(); route = [:]; routePlan = [:]; routeId = "" }
        refreshDisplayCaches(); chartRevision += 1
        info = ProjectInfo(name: candidate.name, timeSignature: "\(candidate.meter)/4", bpm: candidate.bpm)
        updateSnapshot(); status = next == nil ? "已打开手工工程；\(unavailableReason ?? "独立播放不可用")" : candidate.score == nil ? "已打开手工工程，可按小节预设独立播放" : "已导入完整谱源，可独立播放"
    }
    func selectPart(_ id: String) {
        guard var updated = project, updated.score?.parts.contains(where: { $0.id == id }) == true else { return }
        do { try engine?.selectPart(id); updated.selectedPartId = id; _ = try updated.encodedValidated(); project = updated; refreshDisplayCaches(); chartRevision += 1; updateSnapshot() }
        catch { self.error = String(describing: error) }
    }
    func play() { engine?.play(); updateSnapshot() }
    func pause() { engine?.pause(); updateSnapshot() }
    func stop() { engine?.stop(); updateSnapshot() }
    func seek(measureId: String, offset: Double = 0) {
        guard let index = playbackSource?.measures.firstIndex(where: { $0.id == measureId }) else { return }
        do { try engine?.seek(sourceIndex: index + 1, offsetQuarter: offset); updateSnapshot() } catch { self.error = String(describing: error) }
    }
    func setLoop(start: Int, endExclusive: Int) {
        do {
            var updated = project; try updated?.setLoop(start: start, endExclusive: endExclusive)
            try engine?.setLoop(startSourceIndex: start, endSourceIndexExclusive: endExclusive)
            project = updated; updateSnapshot()
        } catch { self.error = String(describing: error) }
    }
    func clearLoop() {
        do { var updated = project; try updated?.setLoop(start: nil); engine?.clearLoop(); project = updated; updateSnapshot() }
        catch { self.error = String(describing: error) }
    }
    func setTempoScale(_ scale: Double) {
        do { try engine?.setTempoScale(scale); updateSnapshot() } catch { self.error = String(describing: error) }
    }
    private func updateSnapshot() {
        guard let engine else { return }
        let wire = engine.snapshot(includeRoute: false)
        let identity = wire["routeId"] as? String ?? ""
        if identity != routeId { routeId = identity; routePlan = engine.plan.toDictionary(); chartRevision += 1 }
        func number(_ name: String, _ fallback: Double = 0) -> Double { (wire[name] as? NSNumber)?.doubleValue ?? fallback }
        var value = TransportSample()
        value.position = SongPosition(bar: Int(number("bar", 1)), beat: Int(number("beat", 1)), division: Int(number("division", 1)), tick: Int(number("tick")))
        value.time = number("sampleTime", ProcessInfo.processInfo.systemUptime * 1000); value.readMilliseconds = number("readMs")
        value.beatsPerSecond = number("rate"); value.valid = wire["valid"] as? Bool ?? true
        value.precise = wire["precise"] as? Bool ?? true; value.playing = wire["playing"] as? Bool ?? false
        value.discontinuity = Int(number("discontinuity")); route = wire
        var metadata = info; metadata.bpm = number("bpm", metadata.bpm ?? 120); metadata.timeSignature = wire["meter"] as? String ?? metadata.timeSignature
        if metadata != info { info = metadata }
        sample = value
        let keys = Set(["sourceMeasureId", "sourceOffsetQuarter", "playing", "preparing", "valid", "occurrenceId", "discontinuity", "loopIteration"])
        renderer.setPosition(wire.filter { keys.contains($0.key) })
    }
}

final class MacScoreWebController: NSObject, ObservableObject, WKNavigationDelegate, WKScriptMessageHandler {
    let webView: WKWebView
    @Published private(set) var ready = false
    @Published private(set) var error: String?
    var onSeek: ((String, Double) -> Void)?
    private var latest: (ScoreIR, String, String, Int, Int)?
    private var shownIdentity: String?
    private var rendered = false
    private var pendingImport: (() -> Void)?
    private var timeout: DispatchWorkItem?
    private var importEpoch = UUID()
    private var lastPosition: [String: Any]?
    private var following = true
    private var active = true
    override init() {
        let config = WKWebViewConfiguration()
        config.preferences.javaScriptCanOpenWindowsAutomatically = false
        config.userContentController.addUserScript(WKUserScript(source: "window.ChordCueScoreBridge={seek:(id,quarter)=>window.webkit.messageHandlers.scoreSeek.postMessage({id,quarter})};", injectionTime: .atDocumentEnd, forMainFrameOnly: true))
        webView = WKWebView(frame: .zero, configuration: config)
        super.init()
        config.userContentController.add(WeakScoreMessageHandler(self), name: "scoreSeek")
        webView.navigationDelegate = self
        if let root = Bundle.main.resourceURL {
            webView.loadFileURL(root.appendingPathComponent("score/ScoreView.html"), allowingReadAccessTo: root)
        } else { error = "应用缺少本地谱面资源" }
    }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        webView.evaluateJavaScript("window.ScoreViewReady === true") { [weak self] result, failure in
            guard let self else { return }
            self.ready = result as? Bool == true && failure == nil
            if !self.ready { self.rendered = false; self.webView.isHidden = true; self.error = "本地谱面解析器未成功加载"; return }
            let pending = self.pendingImport; self.pendingImport = nil; pending?()
            if let latest = self.latest { self.show(latest.0, partId: latest.1, kind: latest.2, shift: latest.3, revision: latest.4) }
        }
    }
    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) { rendered = false; webView.isHidden = true; self.error = error.localizedDescription }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) { rendered = false; webView.isHidden = true; self.error = error.localizedDescription }
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url, let root = Bundle.main.resourceURL,
              url.isFileURL, url.standardizedFileURL.path == root.appendingPathComponent("score/ScoreView.html").standardizedFileURL.path else { decisionHandler(.cancel); return }
        decisionHandler(.allow)
    }
    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard rendered, error == nil, message.frameInfo.isMainFrame, let body = message.body as? [String: Any],
              let id = body["id"] as? String, let offset = body["quarter"] as? Double, offset.isFinite else { return }; onSeek?(id, offset)
    }
    func importBytes(_ bytes: Data, name: String, hash: String, completion: @escaping (Result<ScoreIR, Error>) -> Void) {
        cancelImport(); let epoch = UUID(); importEpoch = epoch
        let work = DispatchWorkItem { [weak self] in
            guard let self, self.importEpoch == epoch else { return }
            self.cancelImport(); completion(.failure(ScoreValidationError.invalid("解析超时，原项目保持不变；请使用较小或简化的导出文件")))
        }
        timeout = work; DispatchQueue.main.asyncAfter(deadline: .now() + 30, execute: work)
        let start = { [weak self] in
            guard let self, self.importEpoch == epoch else { return }
            self.webView.callAsyncJavaScript("try { return JSON.stringify({ok:true,score:window.importScoreBytes(bytes,name,hash)}); } catch(e) { return JSON.stringify({ok:false,error:String(e.message)}); }",
                                            arguments: ["bytes": bytes.base64EncodedString(), "name": name, "hash": hash], in: nil, in: .page) { [weak self] result in
                guard let self, self.importEpoch == epoch else { return }; self.timeout?.cancel(); self.timeout = nil
                do {
                    let value = try result.get()
                    guard let text = value as? String, let data = text.data(using: .utf8), let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { throw ScoreValidationError.invalid("谱源解析失败") }
                    guard object["ok"] as? Bool == true, let raw = object["score"] else { throw ScoreValidationError.invalid(object["error"] as? String ?? "谱源解析失败") }
                    completion(.success(try ScoreIR.decodeValidated(JSONSerialization.data(withJSONObject: raw))))
                } catch { completion(.failure(error)) }
            }
        }
        if ready { start() } else { pendingImport = start }
    }
    func cancelImport() {
        let wasRunning = timeout != nil
        importEpoch = UUID(); pendingImport = nil; timeout?.cancel(); timeout = nil
        if wasRunning { ready = false; shownIdentity = nil; webView.stopLoading(); webView.reload() }
    }
    func show(_ score: ScoreIR, partId: String, kind: String, shift: Int, revision: Int) {
        latest = (score, partId, kind, shift, revision); guard ready else { return }
        let identity = "\(revision)|\(score.id)|\(score.source.sha256 ?? "")|\(partId)|\(kind)|\(shift)"
        guard identity != shownIdentity else { return }
        rendered = false; webView.isHidden = true
        do {
            let data = try score.encodedValidated(); shownIdentity = identity; error = nil
            let json = String(decoding: data, as: UTF8.self)
            webView.callAsyncJavaScript("const ok = await window.showScore(JSON.parse(score),part,kind,shift); if(!ok) throw Error(document.getElementById('error').textContent || '谱面无法显示'); return true;",
                                        arguments: ["score": json, "part": partId, "kind": kind, "shift": shift], in: nil, in: .page) { [weak self] result in
                guard let self, self.shownIdentity == identity else { return }
                switch result {
                case .success:
                    self.rendered = true; self.webView.isHidden = false
                    self.setFollow(self.following); self.setActive(self.active)
                    if let position = self.lastPosition { self.setPosition(position) }
                case .failure(let failure): self.error = failure.localizedDescription
                }
            }
        } catch { self.error = String(describing: error) }
    }
    func setPosition(_ sample: [String: Any]) {
        lastPosition = sample
        guard ready, rendered, error == nil, let data = try? JSONSerialization.data(withJSONObject: sample), let json = String(data: data, encoding: .utf8) else { return }
        webView.callAsyncJavaScript("window.setScorePosition(JSON.parse(sample));", arguments: ["sample": json], in: nil, in: .page) { _ in }
    }
    func setFollow(_ enabled: Bool) {
        following = enabled
        guard ready else { return }
        webView.callAsyncJavaScript("window.setScoreFollow(enabled);", arguments: ["enabled": enabled], in: nil, in: .page) { _ in }
    }
    func setActive(_ enabled: Bool) {
        active = enabled
        guard ready else { return }
        webView.callAsyncJavaScript("window.setScoreActive(enabled);", arguments: ["enabled": enabled], in: nil, in: .page) { _ in }
    }
}

private final class WeakScoreMessageHandler: NSObject, WKScriptMessageHandler {
    weak var target: MacScoreWebController?
    init(_ target: MacScoreWebController) { self.target = target }
    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) { target?.userContentController(userContentController, didReceive: message) }
}

struct MacScoreWebView: NSViewRepresentable {
    @ObservedObject var controller: MacScoreWebController
    let score: ScoreIR, partId: String, kind: String
    var shift = 0
    var revision = 0
    var follow = true
    func makeNSView(context: Context) -> WKWebView { controller.webView }
    func updateNSView(_ view: WKWebView, context: Context) { controller.setFollow(follow); controller.setActive(true); controller.show(score, partId: partId, kind: kind, shift: shift, revision: revision) }
}

struct MacScorePanel: View {
    @ObservedObject var controller: MacScoreWebController
    let score: ScoreIR, partId: String, kind: String
    var shift = 0
    var revision = 0
    var follow = true
    var body: some View {
        VStack(spacing: 0) {
            MacScoreWebView(controller: controller, score: score, partId: partId, kind: kind, shift: shift, revision: revision, follow: follow)
            if let error = controller.error { Text(error).font(.caption).foregroundStyle(.red).padding(8) }
        }
    }
}
