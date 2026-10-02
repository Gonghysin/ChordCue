import SwiftUI
import AppKit
import UniformTypeIdentifiers

struct BarSegment: Identifiable {
    let id: Int
    let start: Double
    let length: Double
    let symbol: String
    let isCarry: Bool
    let isActive: Bool
}

struct ChordCueView: View {
    private static let appIcon: NSImage? = {
        guard let url = Bundle.main.url(forResource: "ChordCue", withExtension: "icns") else { return nil }
        return NSImage(contentsOf: url)
    }()

    @StateObject private var logic = LogicReader()
    @StateObject private var broadcast = LANBroadcast()
    @StateObject private var scoreSession = MacScoreSession()
    @State private var playbackMode = "logic"
    @State private var loopStart = 1
    @State private var loopEnd = 2
    @State private var loopEnabled = false
    @State private var seekBar = 1
    @State private var tempoPercent = 100
    @State private var showBroadcast = false
    @State private var mainPage = "chords"
    @State private var metronomeEnabled = false
    @State private var keySettingsRevision = 0
    @AppStorage("manualChordText") private var manualChordText = ""
    @AppStorage("alwaysOnTop") private var alwaysOnTop = false
    @AppStorage("displayNotation") private var displayNotation = "chords"
    @AppStorage("scoreNotation") private var scoreNotation = "staff"
    @State private var scoreFollow = true
    @State private var lastChordRow: String?
    @AppStorage("displayKey") private var displayKey = "track"
    @AppStorage("numberKeyChoice") private var numberKeyChoice = -1
    @AppStorage("originalKeyPitch") private var originalKeyPitch = -1
    @AppStorage("sectionKeyOverrides") private var sectionKeyOverrides = ""
    @AppStorage("detectKeyChangesV2") private var detectKeyChanges = true
    @State private var showSettings = false
    @State private var showManualProject = false
    @State private var showKeySettings = false
    @State private var exportError: String?
    @State private var alignedChords: [ChordEvent] = []

    private var playbackSample: TransportSample { playbackMode == "standalone" ? scoreSession.sample : logic.transport }
    private var playbackPosition: SongPosition { playbackSample.position }
    private var displayedStatus: String {
        if scoreSession.project == nil { return logic.status }
        return scoreSession.status == "已打开手工工程，可按小节预设独立播放" ? "已打开手工工程，可独立播放" : scoreSession.status
    }
    private var projectInfo: ProjectInfo {
        guard let project = scoreSession.project else { return logic.projectInfo }
        if playbackMode == "standalone" { return scoreSession.info }
        var info = logic.projectInfo; info.name = project.name; return info
    }

    private var manualChords: [ChordEvent] {
        manualChordText.components(separatedBy: .newlines).enumerated().compactMap { index, raw in
            let parts = raw.trimmingCharacters(in: .whitespaces).split(maxSplits: 1, whereSeparator: \.isWhitespace)
            guard parts.count == 2 else { return nil }
            let position = parts[0].split(separator: ".")
            guard let bar = Int(position[0]), bar > 0,
                  let beat = position.count > 1 ? Int(position[1]) : 1,
                  beat > 0 else { return nil }
            return ChordEvent(id: index, position: SongPosition(bar: bar, beat: beat, division: 1, tick: 0),
                              symbol: String(parts[1]))
        }.sorted { $0.position < $1.position }
    }

    private static let bundledChords: [ChordEvent] = {
        guard let url = Bundle.main.url(forResource: "DemoChords", withExtension: "txt"),
              let text = try? String(contentsOf: url, encoding: .utf8) else { return [] }
        return text.components(separatedBy: .newlines).enumerated().compactMap { index, line in
            LogicReader.parseChord(line, id: index)
        }.sorted { $0.position < $1.position }
    }()

    private var snapshotChords: [ChordEvent] { Self.bundledChords }

    private func refreshChartChords() {
        // Imported/source-project positions are exact. Only the Logic display
        // adapter removes the small beat-boundary jitter reported by the host.
        guard scoreSession.project == nil else { return }
        let source = !logic.chords.isEmpty ? logic.chords
            : !manualChords.isEmpty ? manualChords : snapshotChords
        let updated = ChordEvent.alignedForChart(source, beatsPerBar: beatsPerBar)
        if updated != alignedChords { alignedChords = updated }
    }
    private var chords: [ChordEvent] { scoreSession.project != nil ? scoreSession.chords : alignedChords }
    private var beatsPerBar: Int {
        let beats = Int(logic.projectInfo.timeSignature?.split(separator: "/").first.map(String.init) ?? "4") ?? 4
        return max(1, min(12, beats))
    }
    private var activeChord: ChordEvent? {
        if scoreSession.score != nil {
            return chords.last { event in event.position.bar < playbackPosition.bar || (event.position.bar == playbackPosition.bar && sourceChordOffset(event) <= currentOffset) }
        }
        return chords.last { $0.position <= playbackPosition }
    }
    private var currentOffset: Double {
        if playbackMode == "standalone", let offset = scoreSession.route["sourceOffsetQuarter"] as? NSNumber { return offset.doubleValue }
        return beatOffset(playbackPosition)
    }
    private var displayedBeat: Int {
        guard playbackMode == "standalone", scoreSession.effectiveMeasures.indices.contains(playbackPosition.bar - 1) else { return playbackPosition.beat }
        let denominator = scoreSession.effectiveMeasures[playbackPosition.bar - 1].meter.denominator
        return Int(floor(currentOffset * Double(denominator) / 4)) + 1
    }
    private func sourceChordOffset(_ event: ChordEvent) -> Double {
        if let part = scoreSession.selectedPart, part.chords.indices.contains(event.id) { return part.chords[event.id].offset.value }
        return beatOffset(event.position)
    }
    private var barCount: Int { scoreSession.project?.bars ?? max(chords.last?.position.bar ?? 0, playbackPosition.bar, 16) }
    private var keySections: [KeySection] {
        if scoreSession.score != nil {
            if numberKeyChoice >= 0 { return [KeySection(firstBar: 1, key: MusicalKey(root: numberKeyChoice / 2, isMinor: numberKeyChoice % 2 == 1))] }
            return scoreSession.sourceKeySections
        }
        let forced = numberKeyChoice < 0 ? nil : MusicalKey(root: numberKeyChoice / 2,
                                                            isMinor: numberKeyChoice % 2 == 1)
        let beats = scoreSession.project?.meter ?? (Int(projectInfo.timeSignature?.split(separator: "/").first.map(String.init) ?? "4") ?? 4)
        let durations = scoreSession.project?.timingChanges.isEmpty == false ? scoreSession.effectiveMeasures.map(\.duration.value) : []
        let analyzed = ChordTheory.sections(for: chords, forcedKey: forced, detectChanges: detectKeyChanges,
                                           beatsPerBar: beats, measureDurations: durations)
        return ChordTheory.sectionsWithManualChanges(sectionKeyOverrides, autoSections: analyzed)
    }
    private var transpositionBase: MusicalKey? {
        if numberKeyChoice >= 0 { return MusicalKey(root: numberKeyChoice / 2, isMinor: numberKeyChoice % 2 == 1) }
        if scoreSession.score != nil { return scoreSession.firstSourceKey }
        return keySections.first?.key
    }
    private var trackFamilyRoot: Int { transpositionBase?.majorFamilyRoot ?? 0 }
    private var displayShift: Int { shift(for: displayKey) }

    private func shift(for target: String) -> Int {
        if target == "track" { return 0 }
        if scoreSession.score != nil, transpositionBase == nil { return 0 }
        if target == "original" {
            return originalKeyPitch < 0 ? 0 : originalKeyPitch - trackFamilyRoot
        }
        if target == "c" { return -trackFamilyRoot }
        if target.hasPrefix("key:"), let pitch = Int(target.dropFirst(4)), (0..<12).contains(pitch) {
            return pitch - trackFamilyRoot
        }
        return 0
    }

    private func formattedSymbol(_ event: ChordEvent) -> String {
        if displayNotation == "numbers" {
            if scoreSession.score != nil, numberKeyChoice < 0 {
                guard let key = scoreSession.chordKeys[event.id] else { return event.symbol + "（调性未知）" }
                return ChordTheory.number(event.symbol, in: key)
            }
            if scoreSession.score != nil, keySections.isEmpty { return event.symbol + "（调性未知）" }
            let key = keySections.last { $0.firstBar <= event.position.bar }?.key
                ?? MusicalKey(root: 0, isMinor: false)
            return ChordTheory.number(event.symbol, in: key)
        }
        let target = (trackFamilyRoot + displayShift + 12) % 12
        return ChordTheory.displayChord(ChordTheory.transpose(event.symbol, by: displayShift,
                                     preferFlats: [1, 3, 5, 8, 10].contains(target)))
    }


    var body: some View {
        VStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    if let icon = Self.appIcon {
                        Image(nsImage: icon)
                            .resizable()
                            .frame(width: 28, height: 28)
                            .accessibilityHidden(true)
                    }
                    Text("CHORD CUE")
                        .font(.system(size: 13, weight: .bold, design: .rounded))
                        .tracking(2)
                        .foregroundStyle(.secondary)
                    Spacer()
                    Button {
                        metronomeEnabled.toggle()
                    } label: {
                        Label(metronomeEnabled ? "关闭节拍器" : "开启节拍器",
                              systemImage: metronomeEnabled ? "metronome.fill" : "metronome")
                    }
                    Button {
                        showBroadcast.toggle()
                    } label: {
                        Label(broadcast.enabled ? "正在投放 · \(broadcast.viewers) 人" : "局域网投放",
                              systemImage: "antenna.radiowaves.left.and.right")
                    }
                    Button {
                        alwaysOnTop.toggle()
                    } label: {
                        Label(alwaysOnTop ? "取消置顶" : "窗口置顶",
                              systemImage: alwaysOnTop ? "pin.fill" : "pin")
                    }
                    .help(alwaysOnTop ? "让窗口恢复普通层级" : "让窗口保持在其他窗口上方")
                }
                Picker("页面", selection: $mainPage) {
                    Text("和弦谱").tag("chords")
                    Text("乐谱").tag("score").disabled(scoreSession.score == nil)
                    Text("节拍器").tag("metronome")
                }
                .pickerStyle(.segmented)
                .frame(maxWidth: 270)
                scoreToolbar
                HStack(alignment: .bottom, spacing: 12) {
                    Text(activeChord.map(formattedSymbol) ?? "—")
                        .font(.system(size: 56, weight: .bold, design: .rounded))
                        .lineLimit(1)
                        .minimumScaleFactor(0.5)
                    VStack(spacing: 5) {
                        Text(projectInfo.name)
                            .font(.system(size: 17, weight: .semibold))
                            .lineLimit(1)
                            .minimumScaleFactor(0.65)
                            .help(projectInfo.name)
                        Text("\(projectInfo.timeSignature ?? "—") · \(projectInfo.bpmLabel)")
                            .font(.system(size: 14, weight: .medium))
                            .monospacedDigit()
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                            .minimumScaleFactor(0.65)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.bottom, 8)
                    Text("第 \(playbackPosition.bar) 小节 · 第 \(displayedBeat) 拍")
                        .font(.callout.weight(.semibold))
                        .monospacedDigit()
                        .lineLimit(1)
                        .minimumScaleFactor(0.75)
                }
                Text(displayedStatus)
                    .font(.caption)
                    .foregroundStyle(logic.chords.isEmpty ? .orange : .secondary)
                    .lineLimit(1)
                if scoreSession.project == nil && logic.chords.isEmpty && !snapshotChords.isEmpty {
                    Text("显示演示和弦；授权后自动同步")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                if mainPage == "chords" {
                HStack(spacing: 16) {
                    Picker("谱面", selection: $displayNotation) {
                        Text("和弦").tag("chords")
                        Text("级数").tag("numbers")
                    }
                    .pickerStyle(.segmented)
                    .frame(maxWidth: 300)
                    Picker("显示调", selection: Binding(get: { displayKey }, set: { target in
                        if target != "track", scoreSession.score != nil, transpositionBase == nil {
                            displayKey = "track"; scoreSession.error = "源谱首调未知，不能移调；请先指定轨道定调"; return
                        }
                        displayKey = target
                    })) {
                        Text("轨道原样").tag("track")
                        Text("C 调").tag("c")
                        Text("移调前原调").tag("original")
                        ForEach(0..<12, id: \.self) { pitch in
                            Text(ChordTheory.chromatic[pitch] + " 调").tag("key:\(pitch)")
                        }
                    }
                    .frame(maxWidth: 205)
                    Spacer(minLength: 0)
                }
                }
                if showBroadcast {
                    VStack(alignment: .leading, spacing: 8) {
                        HStack {
                            Text(broadcast.status).font(.callout)
                            Spacer()
                            Button(broadcast.enabled ? "停止投放" : "开启投放") {
                                if broadcast.enabled {
                                    broadcast.stop()
                                    logic.setBroadcasting(metronomeEnabled || mainPage == "metronome")
                                } else {
                                    broadcast.start()
                                    logic.setBroadcasting(true)
                                    publishBroadcast()
                                }
                            }
                        }
                        ForEach(broadcast.links, id: \.self) { link in
                            HStack {
                                Text(link).font(.system(size: 11, design: .monospaced)).textSelection(.enabled)
                                    .lineLimit(2)
                                Spacer()
                                Button("复制链接") {
                                    NSPasteboard.general.clearContents()
                                    NSPasteboard.general.setString(link, forType: .string)
                                }
                                Button("预览") { if let url = URL(string: link) { NSWorkspace.shared.open(url) } }
                            }
                        }
                        Text("同一局域网内用浏览器打开。每个人独立选择移调或级数；播放位置跟随主机。链接持有者可查看谱面，停止投放后失效。")
                            .font(.caption).foregroundStyle(.secondary)
                        deviceAssignments
                    }
                    .padding(12)
                    .background(Color.blue.opacity(0.06))
                    .cornerRadius(10)
                }
            }
            .padding(.horizontal, 24)
            .padding(.vertical, 16)

            Divider()

            ZStack {
                NativeMetronome(info: projectInfo, sample: playbackSample,
                                visible: mainPage == "metronome", enabled: $metronomeEnabled,
                                route: nativeRoute)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .opacity(mainPage == "metronome" ? 1 : 0)
                    .allowsHitTesting(mainPage == "metronome")
                    .accessibilityHidden(mainPage != "metronome")
            if mainPage == "score", let score = scoreSession.score, let part = scoreSession.selectedPartId {
                VStack(spacing: 0) {
                    HStack {
                        Picker("乐谱", selection: $scoreNotation) {
                            Text("五线谱").tag("staff")
                            Text("TAB").tag("tab").disabled(scoreSession.selectedPart.map { !MacScoreSession.availableViews($0).contains("tab") } ?? true)
                        }.pickerStyle(.segmented).frame(maxWidth: 220)
                        Toggle("跟随播放", isOn: $scoreFollow)
                        Spacer()
                    }.padding(8)
                    if scoreNotation == "tab", displayShift != 0 {
                        Text("源 TAB 指法保留原调；五线谱和和弦可移调").font(.caption).padding(8)
                    }
                    MacScorePanel(controller: scoreSession.renderer, score: score, partId: part,
                                    kind: scoreNotation, shift: scoreNotation == "tab" ? 0 : displayShift, revision: scoreSession.chartRevision, follow: scoreFollow)
                }
            } else if mainPage == "chords" {
            GeometryReader { viewport in
                let columnCount = max(1, Int((viewport.size.width - 40) / 200))
                ScrollViewReader { proxy in
                    ScrollView {
                        LazyVGrid(columns: Array(repeating: GridItem(.flexible(minimum: 0), spacing: 0),
                                                 count: columnCount), spacing: 0) {
                            ForEach(1...barCount, id: \.self) { bar in
                                barCell(bar).id(bar)
                            }
                        }
                        .padding(20)
                        .padding(.bottom, viewport.size.height)
                    }
                    .onChange(of: "\(columnCount):\((playbackPosition.bar - 1) / columnCount):\(playbackSample.discontinuity):\(playbackSample.playing)") { identity in
                        let rowStart = ((playbackPosition.bar - 1) / columnCount) * columnCount + 1
                        if playbackSample.playing, identity != lastChordRow {
                            lastChordRow = identity
                            proxy.scrollTo(rowStart, anchor: .top)
                        } else if !playbackSample.playing { lastChordRow = nil }
                    }
                }
            }
            .background(Color(red: 0.97, green: 0.96, blue: 0.93))
            }
            }

            Divider()
            HStack(spacing: 12) {
                Button("刷新 Logic 和弦") { logic.refresh() }.disabled(scoreSession.project != nil)
                Button("辅助功能授权") { logic.requestPermission() }
                Menu("导出 PDF") {
                    Button("和弦谱 · 轨道原样") { exportPDF(.chords, target: "track") }
                    Button("和弦谱 · C 调") { exportPDF(.chords, target: "c") }
                    Button("和弦谱 · 移调前原调") { exportPDF(.chords, target: "original") }
                    Button("和弦谱 · 当前显示调") { exportPDF(.chords, target: displayKey) }
                    Divider()
                    Button("级数谱 · 当前显示调") { exportPDF(.numbers, target: displayKey) }
                }
                Spacer()
                Button(showKeySettings ? "收起调性设置" : "调性设置") { showKeySettings.toggle() }
                Button(showSettings ? "收起备用输入" : "备用输入") { showSettings.toggle() }.disabled(scoreSession.project != nil)
            }
            .padding(.horizontal, 22)
            .padding(.vertical, 11)

            if showKeySettings {
                VStack(alignment: .leading, spacing: 8) {
                    HStack(spacing: 18) {
                        Picker("轨道定调", selection: $numberKeyChoice) {
                            Text(detectKeyChanges ? "自动定调与转调" : "自动全曲定调").tag(-1)
                            ForEach(0..<24, id: \.self) { value in
                                let key = MusicalKey(root: value / 2, isMinor: value % 2 == 1)
                                Text(key.selectionLabel).tag(value)
                            }
                        }
                        Picker("移调前原调", selection: $originalKeyPitch) {
                            Text("未指定（轨道原样）").tag(-1)
                            ForEach(0..<12, id: \.self) { pitch in
                                Text(ChordTheory.chromatic[pitch] + " 调").tag(pitch)
                            }
                        }
                    }
                    Toggle("自动检测转调（综合前后文，抑制短暂借用和弦）", isOn: $detectKeyChanges)
                        .disabled(numberKeyChoice >= 0)
                        .font(.caption)
                    Text("手动转调点（可选）：每行“小节 调”，例如 33 E♭、49 Am；填写后以这些段落为准。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    TextEditor(text: $sectionKeyOverrides)
                        .font(.system(.body, design: .monospaced))
                        .frame(height: 65)
                }
                .padding(.horizontal, 22)
                .padding(.bottom, 12)
            }

            if showSettings {
                VStack(alignment: .leading, spacing: 8) {
                    Text("仅在无法读取 Logic 和弦轨时使用。每行输入“小节.拍 和弦”，例如 1.1 C。")
                        .font(.caption)
                    TextEditor(text: $manualChordText)
                        .font(.system(.body, design: .monospaced))
                        .frame(height: 110)
                }
                .padding(.horizontal, 22)
                .padding(.bottom, 15)
            }
        }
        .frame(minWidth: 420, minHeight: 300)
        .background(Color(nsColor: .windowBackgroundColor))
        .onAppear {
            refreshChartChords()
            if ["staff", "tab"].contains(displayNotation) {
                scoreNotation = displayNotation; displayNotation = "chords"
                if scoreSession.score != nil { mainPage = "score" }
            }
            DispatchQueue.main.async {
                for window in NSApp.windows { window.level = alwaysOnTop ? .floating : .normal }
            }
        }
        .onChange(of: alwaysOnTop) { isOnTop in
            for window in NSApp.windows { window.level = isOnTop ? .floating : .normal }
        }
        .onReceive(logic.$transport) { sample in
            if playbackMode == "logic" { publishBroadcast(sample: sample); scoreSession.renderer.setPosition(scoreLogicRoute(sample)) }
        }
        .onReceive(scoreSession.$sample) { sample in if playbackMode == "standalone" { publishBroadcast(sample: sample) } }
        .onReceive(scoreSession.$project) { project in DispatchQueue.main.async {
            if scoreNotation == "tab", scoreSession.selectedPart.map({ !MacScoreSession.availableViews($0).contains("tab") }) ?? true { scoreNotation = "staff" }
            if project?.score == nil, mainPage == "score" { mainPage = "chords" }
            if let loop = project?.loopRange { loopStart = loop.start; loopEnd = loop.endExclusive; loopEnabled = true } else { loopEnabled = false }
            if project?.score != nil, transpositionBase == nil { displayKey = "track" }
            publishBroadcast()
        } }
        .onReceive(logic.$chords) { _ in
            DispatchQueue.main.async {
                refreshChartChords()
                publishBroadcast()
            }
        }
        .onChange(of: logic.projectInfo.timeSignature) { _ in
            refreshChartChords()
            publishBroadcast()
        }
        .onChange(of: projectInfo) { _ in publishBroadcast() }
        .onChange(of: numberKeyChoice) { _ in if scoreSession.score != nil, transpositionBase == nil { displayKey = "track" }; publishBroadcast() }
        .onChange(of: sectionKeyOverrides) { _ in keySettingsRevision += 1; publishBroadcast() }
        .onChange(of: detectKeyChanges) { _ in keySettingsRevision += 1; publishBroadcast() }
        .onChange(of: manualChordText) { _ in refreshChartChords(); publishBroadcast() }
        .onChange(of: playbackMode) { mode in if mode == "logic" { scoreSession.pause() }; publishBroadcast() }
        .onChange(of: broadcast.enabled) { enabled in logic.setBroadcasting(enabled || metronomeEnabled || mainPage == "metronome") }
        .onChange(of: mainPage) { page in
            lastChordRow = nil
            logic.setBroadcasting(broadcast.enabled || metronomeEnabled || page == "metronome")
            scoreSession.renderer.setActive(page == "score")
        }
        .onChange(of: metronomeEnabled) { enabled in logic.setBroadcasting(broadcast.enabled || enabled || mainPage == "metronome") }
        .alert("导出失败", isPresented: Binding(get: { exportError != nil },
                                            set: { if !$0 { exportError = nil } })) {
            Button("确定", role: .cancel) { exportError = nil }
        } message: {
            Text(exportError ?? "")
        }
        .alert("谱面工程操作失败", isPresented: Binding(get: { scoreSession.error != nil }, set: { if !$0 { scoreSession.error = nil } })) {
            Button("确定", role: .cancel) { scoreSession.error = nil }
        } message: { Text(scoreSession.error ?? "") }
        .sheet(isPresented: Binding(get: { scoreSession.pendingScore != nil }, set: { if !$0 { scoreSession.cancelImport() } })) { scoreImportPreview }
        .sheet(isPresented: $showManualProject) {
            MacManualProjectEditor(minimumBars: max(1, manualChords.last?.position.bar ?? 1)) { name, bars in
                if scoreSession.createManualProject(name: name, bars: bars, chords: manualChords) {
                    playbackMode = "standalone"; mainPage = "chords"; tempoPercent = 100
                    seekBar = 1; loopStart = 1; loopEnd = 2; loopEnabled = false; return nil
                }
                let error = scoreSession.error ?? "手工工程无法创建"; scoreSession.error = nil; return error
            }
        }
    }

    private func publishBroadcast(sample: TransportSample? = nil) {
        guard broadcast.enabled else { return }
        broadcast.publish(chords: chords, sections: keySections, info: projectInfo, sample: sample ?? playbackSample,
                          score: scoreSession.score, selectedPartId: scoreSession.selectedPartId, bars: barCount,
                          route: playbackMode == "standalone" ? scoreSession.route : scoreLogicRoute(sample ?? playbackSample),
                          projectRevision: scoreSession.project == nil ? nil : scoreSession.chartRevision * 1000 + numberKeyChoice + 1 + (playbackMode == "standalone" ? 500 : 0),
                          playbackPlan: playbackMode == "standalone" ? scoreSession.routePlan : nil,
                          measures: scoreSession.manualMeasures, keySettingsRevision: keySettingsRevision)
    }

    private var nativeRoute: [String: Any] {
        guard playbackMode == "standalone" else { return [:] }
        var value = scoreSession.route; value["route"] = scoreSession.routePlan; return value
    }

    private func exportPDF(_ notation: ChartNotation, target: String) {
        if target != "track", scoreSession.score != nil, transpositionBase == nil {
            exportError = "源谱首调未知，不能移调；请先指定轨道定调"; return
        }
        let panel = NSSavePanel()
        panel.allowedContentTypes = [.pdf]
        let projectName = projectInfo.name.trimmingCharacters(in: .whitespacesAndNewlines)
        let events = chords
        let meter = scoreSession.project?.meter ?? beatsPerBar
        let alignLegacyChords = scoreSession.project == nil
        let sections = keySections
        let sourceScore = scoreSession.score
        let manualMeasures = sourceScore == nil ? scoreSession.effectiveMeasures : []
        let measureBPMs = scoreSession.measureBPMs
        let sourcePartId = scoreSession.selectedPartId
        let sourceKeys = numberKeyChoice < 0 ? scoreSession.chordKeys : Dictionary(uniqueKeysWithValues: events.map { ($0.id, MusicalKey(root: numberKeyChoice / 2, isMinor: numberKeyChoice % 2 == 1)) })
        let semitones = shift(for: target)
        let targetPitch = (trackFamilyRoot + semitones + 12) % 12
        let firstKey = transpositionBase ?? MusicalKey(root: 0, isMinor: false)
        var keyName = scoreSession.score != nil && transpositionBase == nil ? "调性未知" : ChordTheory.noteName(firstKey.majorFamilyRoot + semitones, preferFlats: false) + "大调"
        if firstKey.isMinor {
            keyName += "（" + ChordTheory.noteName(firstKey.root + semitones, preferFlats: false) + "小调）"
        }
        if sections.count > 1 { keyName += "_含转调" }
        let exportName = projectName.isEmpty || projectName == "未连接工程" ? "ChordCue" : projectName
        panel.nameFieldStringValue = "\(exportName)_\(notation.title)_\(keyName)"
            .replacingOccurrences(of: "/", with: "／")
            .replacingOccurrences(of: ":", with: "：") + ".pdf"
        panel.begin { response in
            guard response == .OK, let url = panel.url else { return }
            do {
                try ChartPDF.write(chords: events, sections: sections, notation: notation,
                                   semitones: semitones,
                                   preferFlats: [1, 3, 5, 8, 10].contains(targetPitch), beatsPerBar: meter, to: url,
                                   score: sourceScore, selectedPartId: sourcePartId, sourceKeys: sourceKeys,
                                   manualMeasures: manualMeasures, measureBPMs: measureBPMs, alignLegacyChords: alignLegacyChords)
            } catch {
                exportError = error.localizedDescription
            }
        }
    }

    private func barCell(_ bar: Int) -> some View {
        let items = chords.filter { $0.position.bar == bar }
        let isCurrent = playbackPosition.bar == bar
        let segments = barSegments(bar, items: items)
        let playheadOffset = currentOffset
        return VStack(alignment: .leading, spacing: 9) {
            HStack(spacing: 5) {
                Text(scoreSession.score.flatMap { $0.measures.indices.contains(bar - 1) ? $0.measures[bar - 1].number : nil } ?? "\(bar)")
                    .foregroundStyle(isCurrent ? Color.blue : Color.gray)
                if scoreSession.effectiveMeasures.indices.contains(bar - 1) {
                    let measure = scoreSession.effectiveMeasures[bar - 1]
                    Text("\(measure.meter.numerator)/\(measure.meter.denominator) · \(measure.duration.numerator)/\(measure.duration.denominator) ♩")
                        .font(.system(size: 9)).foregroundStyle(.secondary)
                    if let bpm = scoreSession.measureBPMs[measure.id] {
                        Text("♩=\(String(format: "%.4g", bpm))").font(.system(size: 9)).foregroundStyle(.secondary)
                    }
                }
                if displayNotation == "numbers", scoreSession.score == nil,
                   let section = keySections.first(where: { $0.firstBar == bar }) {
                    let shifted = MusicalKey(root: (section.key.root + displayShift + 24) % 12,
                                             isMinor: section.key.isMinor)
                    Text(shifted.label)
                        .foregroundStyle(Color.blue)
                }
            }
            .font(.system(size: 12, weight: .medium))
            GeometryReader { geometry in
                ZStack(alignment: .leading) {
                    HStack(spacing: 0) {
                        ForEach(segments) { segment in
                            Text(segment.symbol)
                                .font(.system(size: 22, weight: segment.isActive && isCurrent ? .bold : .medium, design: .rounded))
                                .foregroundStyle(segment.isActive && isCurrent ? Color.blue :
                                                    segment.isCarry ? Color.gray : Color.black)
                                .lineLimit(1)
                                .minimumScaleFactor(0.65)
                                .frame(width: geometry.size.width * segment.length / barLength(bar), height: 56)
                                .background(segment.isActive && isCurrent ? Color.blue.opacity(0.08) : Color.clear)
                                .overlay(alignment: .leading) {
                                    if segment.start > 0 {
                                        Rectangle()
                                            .fill(Color.black.opacity(0.28))
                                            .frame(width: 1, height: 40)
                                    }
                                }
                        }
                    }
                    if isCurrent {
                        Rectangle()
                            .fill(Color.blue)
                            .frame(width: 2, height: 58)
                            .offset(x: min(geometry.size.width - 2, geometry.size.width * playheadOffset / barLength(bar)))
                    }
                }
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, minHeight: 104, maxHeight: 104)
        .background(isCurrent ? Color(red: 0.87, green: 0.93, blue: 1.0) : Color.white)
        .overlay(Rectangle().stroke(isCurrent ? Color.blue : Color.black.opacity(0.25), lineWidth: isCurrent ? 2 : 0.8))
        .accessibilityLabel("第 \(bar) 小节，\(items.map(formattedSymbol).joined(separator: "，"))")
        .onTapGesture {
            if playbackMode == "standalone", scoreSession.effectiveMeasures.indices.contains(bar - 1) { scoreSession.seek(measureId: scoreSession.effectiveMeasures[bar - 1].id) }
        }
    }

    private func beatOffset(_ position: SongPosition) -> Double {
        return min(barLength(position.bar), max(0, position.quarterNoteOffset))
    }

    private func scoreLogicRoute(_ sample: TransportSample) -> [String: Any] {
        guard let score = scoreSession.score, score.measures.indices.contains(sample.position.bar - 1) else { return [:] }
        let measure = score.measures[sample.position.bar - 1]
        let quarter = min(measure.duration.value, max(0, Double(sample.position.beat - 1)
                           + Double(sample.position.division - 1) / 4 + Double(sample.position.tick) / 960))
        return ["sourceMeasureId": measure.id, "sourceOffsetQuarter": quarter, "playing": sample.playing,
                "meterNumerator": measure.meter.numerator, "meterDenominator": measure.meter.denominator]
    }

    private var scoreToolbar: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                Button(scoreSession.importing ? "正在导入…" : "导入 GP / MusicXML") {
                    let panel = NSOpenPanel(); panel.canChooseDirectories = false; panel.allowsMultipleSelection = false
                    panel.allowedFileTypes = ["gp", "gp3", "gp4", "gp5", "gpx", "musicxml", "xml", "mxl"]
                    panel.begin { response in if response == .OK, let url = panel.url { scoreSession.importFile(url) } }
                }.disabled(scoreSession.importing)
                if scoreSession.importing { Button("取消解析") { scoreSession.cancelImport() } }
                Button("打开工程") {
                    let panel = NSOpenPanel(); panel.canChooseDirectories = false; panel.allowsMultipleSelection = false; panel.allowedContentTypes = [.json]
                    panel.begin { response in if response == .OK, let url = panel.url, scoreSession.openProject(url) {
                        playbackMode = scoreSession.canPlay ? "standalone" : "logic"; tempoPercent = 100
                        seekBar = 1; loopStart = scoreSession.project?.loopRange?.start ?? 1
                        loopEnd = scoreSession.project?.loopRange?.endExclusive ?? 2; loopEnabled = scoreSession.project?.loopRange != nil
                    } }
                }
                Button("保存工程") {
                    let panel = NSSavePanel(); panel.allowedContentTypes = [.json]; panel.nameFieldStringValue = (scoreSession.project?.name ?? "ChordCue").replacingOccurrences(of: "/", with: "／") + ".chordcue.json"
                    panel.begin { response in if response == .OK, let url = panel.url { scoreSession.saveProject(url) } }
                }.disabled(scoreSession.project == nil)
                Spacer(minLength: 0)
                Picker("播放来源", selection: $playbackMode) {
                    Text("Logic 跟随").tag("logic"); Text("独立播放").tag("standalone")
                }.frame(maxWidth: 210).disabled(!scoreSession.canPlay)
            }
            HStack(spacing: 10) {
                Button("新建手工工程") { showManualProject = true }
                if scoreSession.score != nil { Text("独立播放按导入谱自动切换 BPM 与拍号").font(.caption).foregroundStyle(.secondary) }
            }
            if scoreSession.canPlay {
                HStack(spacing: 10) {
                    if let score = scoreSession.score {
                    Picker("主机声部", selection: Binding(get: { scoreSession.selectedPartId ?? score.parts.first?.id ?? "" }, set: {
                        scoreSession.selectPart($0)
                        if scoreNotation == "tab", scoreSession.selectedPart.map({ !MacScoreSession.availableViews($0).contains("tab") }) ?? true { scoreNotation = "staff" }
                    })) {
                        ForEach(score.parts, id: \.id) { part in Text(part.name).tag(part.id) }
                    }.frame(maxWidth: 260)
                    } else { Text("手工和弦工程").font(.caption) }
                    Button(playbackSample.playing ? "暂停" : "播放") { if playbackSample.playing { scoreSession.pause() } else { scoreSession.play() } }.disabled(playbackMode != "standalone")
                    Button("停止") { scoreSession.stop() }.disabled(playbackMode != "standalone")
                    Stepper("速度 \(tempoPercent)%", value: $tempoPercent, in: 25...400, step: 5).fixedSize().disabled(playbackMode != "standalone")
                    Stepper("定位 \(seekBar)", value: $seekBar, in: 1...max(1, barCount)).fixedSize()
                    Button("跳转") { if scoreSession.effectiveMeasures.indices.contains(seekBar - 1) { scoreSession.seek(measureId: scoreSession.effectiveMeasures[seekBar - 1].id) } }.disabled(playbackMode != "standalone")
                }
                HStack(spacing: 12) {
                    Stepper("循环起始 \(loopStart)", value: $loopStart, in: 1...max(1, barCount)).fixedSize()
                    Stepper("循环终止（不含）\(loopEnd)", value: $loopEnd, in: 2...max(2, barCount + 1)).fixedSize()
                    Toggle("循环", isOn: Binding(get: { loopEnabled }, set: { enabled in
                        guard !enabled || loopStart < loopEnd else { scoreSession.error = "循环终止必须在起始之后"; return }
                        loopEnabled = enabled
                        if enabled { scoreSession.setLoop(start: loopStart, endExclusive: loopEnd) } else { scoreSession.clearLoop() }
                    })).toggleStyle(.checkbox)
                    Button("更新循环") { scoreSession.setLoop(start: loopStart, endExclusive: loopEnd) }.disabled(!loopEnabled)
                }.font(.caption).disabled(playbackMode != "standalone")
                .onChange(of: tempoPercent) { value in scoreSession.setTempoScale(Double(value) / 100) }
                if let score = scoreSession.score, !score.warnings.isEmpty {
                    DisclosureGroup("源谱警告（\(score.warnings.count)）") {
                        ScrollView { VStack(alignment: .leading, spacing: 4) { ForEach(Array(score.warnings.enumerated()), id: \.offset) { _, warning in Text("\(warning.code)：\(warning.message)").font(.caption) } } }.frame(maxHeight: 90)
                    }
                }
                if let warnings = scoreSession.routePlan["warnings"] as? [[String: Any]], !warnings.isEmpty {
                    DisclosureGroup("播放路线警告（\(warnings.count)）") {
                        ForEach(Array(warnings.enumerated()), id: \.offset) { _, warning in Text("\(warning["code"] as? String ?? "route")：\(warning["message"] as? String ?? "")").font(.caption) }
                    }
                }
                if scoreSession.score != nil, scoreSession.chords.isEmpty, ["chords", "numbers"].contains(displayNotation) {
                    Text("该声部没有源和弦标记。请选择五线谱或 TAB 查看音符。").font(.caption).foregroundStyle(.secondary)
                }
                if playbackMode == "logic" {
                    Text("Logic 跟随按源谱顺序小节定位；独立播放执行谱源反复与跳转路线。").font(.caption).foregroundStyle(.secondary)
                }
            }
        }
    }

    private var scoreImportPreview: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let score = scoreSession.pendingScore {
                Text("导入预览 · \(score.title)").font(.headline)
                Text("\(score.measures.count) 个源小节 · \(score.parts.count) 个声部 · \(score.source.fileName ?? "源文件")").font(.caption).foregroundStyle(.secondary)
                HStack {
                    Picker("声部", selection: $scoreSession.pendingPartId) { ForEach(score.parts, id: \.id) { Text($0.name).tag($0.id) } }
                    Picker("预览", selection: $scoreSession.previewKind) {
                        Text("五线谱").tag("staff")
                        Text("TAB").tag("tab").disabled(!(score.parts.first { $0.id == scoreSession.pendingPartId }.map { MacScoreSession.availableViews($0).contains("tab") } ?? false))
                    }
                }
                MacScorePanel(controller: scoreSession.previewRenderer, score: score, partId: scoreSession.pendingPartId, kind: scoreSession.previewKind)
                if !score.warnings.isEmpty {
                    ScrollView { VStack(alignment: .leading, spacing: 4) { ForEach(Array(score.warnings.enumerated()), id: \.offset) { _, warning in Text("\(warning.code)：\(warning.message)").font(.caption) } } }.frame(maxHeight: 110)
                }
                HStack {
                    Button("取消", role: .cancel) { scoreSession.cancelImport() }
                    Spacer()
                    Button("导入选中声部") {
                        if scoreSession.acceptPreview() { playbackMode = "standalone"; scoreNotation = "staff"; mainPage = "score"; seekBar = 1; loopStart = 1; loopEnd = min(2, barCount + 1); loopEnabled = false }
                    }.keyboardShortcut(.defaultAction)
                }
            }
        }.padding(20).frame(minWidth: 740, minHeight: 560)
        .onChange(of: scoreSession.pendingPartId) { _ in scoreSession.previewKind = "staff" }
    }

    private var deviceAssignments: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(broadcast.devices) { device in
                MacDeviceAssignmentRow(device: device, parts: scoreSession.score?.parts ?? [], broadcast: broadcast)
            }
        }
    }

    private func barLength(_ bar: Int) -> Double {
        if scoreSession.effectiveMeasures.indices.contains(bar - 1) { return scoreSession.effectiveMeasures[bar - 1].duration.value }
        return Double(scoreSession.project?.meter ?? beatsPerBar)
    }

    private func barSegments(_ bar: Int, items: [ChordEvent]) -> [BarSegment] {
        let start = SongPosition(bar: bar, beat: 1, division: 1, tick: 0)
        let carry = chords.last { $0.position < start }
        var onsets: [(offset: Double, event: ChordEvent)] = []
        for item in items {
            let offset = min(barLength(bar), sourceChordOffset(item))
            if onsets.last?.offset == offset {
                onsets[onsets.count - 1] = (offset, item)
            } else {
                onsets.append((offset, item))
            }
        }
        if onsets.isEmpty {
            return [BarSegment(id: -bar, start: 0, length: barLength(bar), symbol: carry == nil ? "·" : "%",
                               isCarry: true, isActive: carry?.id == activeChord?.id)]
        }
        var result: [BarSegment] = []
        if let first = onsets.first, first.offset > 0 {
            result.append(BarSegment(id: -bar, start: 0, length: first.offset,
                                     symbol: carry == nil ? "·" : "—", isCarry: true,
                                     isActive: carry?.id == activeChord?.id))
        }
        for index in onsets.indices {
            let onset = onsets[index]
            let end = index + 1 < onsets.count ? onsets[index + 1].offset : barLength(bar)
            result.append(BarSegment(id: onset.event.id, start: onset.offset,
                                     length: max(0, end - onset.offset), symbol: formattedSymbol(onset.event),
                                     isCarry: false, isActive: onset.event.id == activeChord?.id))
        }
        return result
    }
}

private struct MacManualProjectEditor: View {
    @Environment(\.dismiss) private var dismiss
    @State private var name = "手工工程"
    @State private var bars: Int
    @State private var failure = ""
    let create: (String, Int) -> String?
    init(minimumBars: Int, create: @escaping (String, Int) -> String?) {
        _bars = State(initialValue: min(10_000, max(16, minimumBars))); self.create = create
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("新建手工和弦工程").font(.title2)
            Text("使用备用输入中的和弦创建工程。").font(.caption).foregroundStyle(.secondary)
            TextField("工程名称", text: $name)
            Stepper("\(bars) 小节", value: $bars, in: 1...10_000)
            Text("新建手工工程采用固定 4/4、♩=120。").font(.caption).foregroundStyle(.secondary)
            if !failure.isEmpty { Text(failure).foregroundStyle(.red).font(.caption) }
            HStack {
                Spacer(); Button("取消", role: .cancel) { dismiss() }
                Button("创建") { if let error = create(name, bars) { failure = error } else { dismiss() } }.keyboardShortcut(.defaultAction)
            }
        }.padding(20).frame(minWidth: 440)
    }
}

private struct MacDeviceAssignmentRow: View {
    let device: LANDeviceSummary
    let parts: [ScorePart]
    @ObservedObject var broadcast: LANBroadcast
    @State private var label: String
    init(device: LANDeviceSummary, parts: [ScorePart], broadcast: LANBroadcast) {
        self.device = device; self.parts = parts; self.broadcast = broadcast; _label = State(initialValue: device.label)
    }
    private var available: [String] {
        let source = parts.first(where: { $0.id == device.partId }).map(MacScoreSession.availableViews) ?? ["chords", "numbers", "metronome"]
        return source.filter { device.capabilities.contains($0) }
    }
    private func metric(_ value: Double?) -> String { value.map { String(format: "%.1f ms", $0) } ?? "—" }
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                TextField("设备名称", text: $label).frame(maxWidth: 170)
                Button("重命名") { broadcast.assign(clientId: device.id, partId: device.partId, view: device.view, label: label) }
                if !parts.isEmpty {
                    Picker("设备声部", selection: Binding(get: { device.partId ?? "" }, set: { id in
                        guard let part = parts.first(where: { $0.id == id }) else { return }
                        let views = MacScoreSession.availableViews(part).filter { device.capabilities.contains($0) }
                        broadcast.assign(clientId: device.id, partId: id, view: views.contains(device.view) ? device.view : views.first ?? "metronome")
                    })) { ForEach(parts, id: \.id) { Text($0.name).tag($0.id) } }.frame(maxWidth: 220)
                }
                Picker("视图", selection: Binding(get: { device.view }, set: { broadcast.assign(clientId: device.id, partId: device.partId, view: $0) })) {
                    ForEach(available, id: \.self) { view in Text(["chords": "和弦", "numbers": "级数", "staff": "五线谱", "tab": "TAB", "metronome": "节拍器"][view] ?? view).tag(view) }
                }.frame(maxWidth: 150)
            }
            Text("\(device.connected ? "在线" : "离线") · \(device.syncStatus) · RTT \(metric(device.rttMs)) · 时钟 \(device.clockStatus) · 测时抖动 \(metric(device.clockJitterMs)) · 校准距今 \(metric(device.clockProbeAgeMs)) · 音频输出延迟估计 \(device.audioOutputDelayMs.map { metric($0) } ?? "未提供估计") · ACK \(device.applied ? "已应用" : "等待") a\(device.assignmentRevision)/s\(device.scoreRevision)")
                .font(.system(size: 10, design: .monospaced)).foregroundStyle(device.connected && device.applied ? Color.secondary : Color.orange)
                .help("时间基准换算（主机−浏览器）：\(metric(device.clockOffsetMs))；这是时钟原点差，不是播放误差")
            Text(device.browser).font(.system(size: 10)).foregroundStyle(.secondary).lineLimit(1)
        }.padding(8).background(Color.gray.opacity(0.06)).cornerRadius(6)
    }
}

@main
struct ChordCueApp: App {
    var body: some Scene {
        WindowGroup("ChordCue") { ChordCueView() }
    }
}
