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
    @State private var showBroadcast = false
    @State private var mainPage = "chords"
    @State private var metronomeEnabled = false
    @AppStorage("manualChordText") private var manualChordText = ""
    @AppStorage("alwaysOnTop") private var alwaysOnTop = false
    @AppStorage("displayNotation") private var displayNotation = "chords"
    @AppStorage("displayKey") private var displayKey = "track"
    @AppStorage("numberKeyChoice") private var numberKeyChoice = -1
    @AppStorage("originalKeyPitch") private var originalKeyPitch = -1
    @AppStorage("sectionKeyOverrides") private var sectionKeyOverrides = ""
    @AppStorage("detectKeyChangesV2") private var detectKeyChanges = true
    @State private var showSettings = false
    @State private var showKeySettings = false
    @State private var exportError: String?
    @State private var alignedChords: [ChordEvent] = []

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
        let source = !logic.chords.isEmpty ? logic.chords
            : !manualChords.isEmpty ? manualChords : snapshotChords
        let updated = ChordEvent.alignedForChart(source, beatsPerBar: beatsPerBar)
        if updated != alignedChords { alignedChords = updated }
    }
    private var chords: [ChordEvent] { alignedChords }
    private var beatsPerBar: Int {
        let beats = Int(logic.projectInfo.timeSignature?.split(separator: "/").first.map(String.init) ?? "4") ?? 4
        return max(1, min(12, beats))
    }
    private var activeChord: ChordEvent? { chords.last { $0.position <= logic.position } }
    private var barCount: Int { max(chords.last?.position.bar ?? 0, logic.position.bar, 16) }
    private var keySections: [KeySection] {
        let forced = numberKeyChoice < 0 ? nil : MusicalKey(root: numberKeyChoice / 2,
                                                            isMinor: numberKeyChoice % 2 == 1)
        let beats = Int(logic.projectInfo.timeSignature?.split(separator: "/").first.map(String.init) ?? "4") ?? 4
        let analyzed = ChordTheory.sections(for: chords, forcedKey: forced, detectChanges: detectKeyChanges,
                                           beatsPerBar: beats)
        return ChordTheory.sectionsWithManualChanges(sectionKeyOverrides, autoSections: analyzed)
    }
    private var trackFamilyRoot: Int { keySections.first?.key.majorFamilyRoot ?? 0 }
    private var displayShift: Int { shift(for: displayKey) }

    private func shift(for target: String) -> Int {
        if target == "track" { return 0 }
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
                    Text("节拍器").tag("metronome")
                }
                .pickerStyle(.segmented)
                .frame(maxWidth: 180)
                HStack(alignment: .bottom, spacing: 12) {
                    Text(activeChord.map(formattedSymbol) ?? "—")
                        .font(.system(size: 56, weight: .bold, design: .rounded))
                        .lineLimit(1)
                        .minimumScaleFactor(0.5)
                    VStack(spacing: 5) {
                        Text(logic.projectInfo.name)
                            .font(.system(size: 17, weight: .semibold))
                            .lineLimit(1)
                            .minimumScaleFactor(0.65)
                            .help(logic.projectInfo.name)
                        Text("\(logic.projectInfo.timeSignature ?? "—") · \(logic.projectInfo.bpmLabel)")
                            .font(.system(size: 14, weight: .medium))
                            .monospacedDigit()
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                            .minimumScaleFactor(0.65)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.bottom, 8)
                    Text("第 \(logic.position.bar) 小节 · 第 \(logic.position.beat) 拍")
                        .font(.callout.weight(.semibold))
                        .monospacedDigit()
                        .lineLimit(1)
                        .minimumScaleFactor(0.75)
                }
                Text(logic.status)
                    .font(.caption)
                    .foregroundStyle(logic.chords.isEmpty ? .orange : .secondary)
                    .lineLimit(1)
                if logic.chords.isEmpty && !snapshotChords.isEmpty {
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
                    .frame(maxWidth: 190)
                    Picker("显示调", selection: $displayKey) {
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
                NativeMetronome(info: logic.projectInfo, sample: logic.transport,
                                visible: mainPage == "metronome", enabled: $metronomeEnabled)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .opacity(mainPage == "metronome" ? 1 : 0)
                    .allowsHitTesting(mainPage == "metronome")
                    .accessibilityHidden(mainPage != "metronome")
            if mainPage == "chords" {
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
                    }
                    .onChange(of: logic.position.bar) { newBar in
                        withAnimation(.easeInOut(duration: 0.25)) {
                            proxy.scrollTo(newBar, anchor: .center)
                        }
                    }
                }
            }
            .background(Color(red: 0.97, green: 0.96, blue: 0.93))
            }
            }

            Divider()
            HStack(spacing: 12) {
                Button("刷新 Logic 和弦") { logic.refresh() }
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
                Button(showSettings ? "收起备用输入" : "备用输入") { showSettings.toggle() }
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
            DispatchQueue.main.async {
                for window in NSApp.windows { window.level = alwaysOnTop ? .floating : .normal }
            }
        }
        .onChange(of: alwaysOnTop) { isOnTop in
            for window in NSApp.windows { window.level = isOnTop ? .floating : .normal }
        }
        .onReceive(logic.$transport) { sample in publishBroadcast(sample: sample) }
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
        .onChange(of: logic.projectInfo) { _ in publishBroadcast() }
        .onChange(of: numberKeyChoice) { _ in publishBroadcast() }
        .onChange(of: sectionKeyOverrides) { _ in publishBroadcast() }
        .onChange(of: detectKeyChanges) { _ in publishBroadcast() }
        .onChange(of: manualChordText) { _ in
            refreshChartChords()
            publishBroadcast()
        }
        .onChange(of: broadcast.enabled) { enabled in logic.setBroadcasting(enabled || metronomeEnabled || mainPage == "metronome") }
        .onChange(of: mainPage) { page in logic.setBroadcasting(broadcast.enabled || metronomeEnabled || page == "metronome") }
        .onChange(of: metronomeEnabled) { enabled in logic.setBroadcasting(broadcast.enabled || enabled || mainPage == "metronome") }
        .alert("导出失败", isPresented: Binding(get: { exportError != nil },
                                            set: { if !$0 { exportError = nil } })) {
            Button("确定", role: .cancel) { exportError = nil }
        } message: {
            Text(exportError ?? "")
        }
    }

    private func publishBroadcast(sample: TransportSample? = nil) {
        guard broadcast.enabled else { return }
        broadcast.publish(chords: chords, sections: keySections, info: logic.projectInfo, sample: sample ?? logic.transport)
    }

    private func exportPDF(_ notation: ChartNotation, target: String) {
        let panel = NSSavePanel()
        panel.allowedContentTypes = [.pdf]
        let projectName = logic.projectInfo.name.trimmingCharacters(in: .whitespacesAndNewlines)
        let events = chords
        let meter = beatsPerBar
        let sections = keySections
        let semitones = shift(for: target)
        let targetPitch = (trackFamilyRoot + semitones + 12) % 12
        let firstKey = sections.first?.key ?? MusicalKey(root: 0, isMinor: false)
        var keyName = ChordTheory.noteName(firstKey.majorFamilyRoot + semitones, preferFlats: false) + "大调"
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
                                   preferFlats: [1, 3, 5, 8, 10].contains(targetPitch),
                                   beatsPerBar: meter, to: url)
            } catch {
                exportError = error.localizedDescription
            }
        }
    }

    private func barCell(_ bar: Int) -> some View {
        let items = chords.filter { $0.position.bar == bar }
        let isCurrent = logic.position.bar == bar
        let segments = barSegments(bar, items: items)
        let playheadOffset = beatOffset(logic.position)
        return VStack(alignment: .leading, spacing: 9) {
            HStack(spacing: 5) {
                Text("\(bar)")
                    .foregroundStyle(isCurrent ? Color.blue : Color.gray)
                if displayNotation == "numbers",
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
                                .frame(width: geometry.size.width * segment.length / Double(beatsPerBar), height: 56)
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
                            .offset(x: min(geometry.size.width - 2, geometry.size.width * playheadOffset / Double(beatsPerBar)))
                    }
                }
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, minHeight: 104, maxHeight: 104)
        .background(isCurrent ? Color(red: 0.87, green: 0.93, blue: 1.0) : Color.white)
        .overlay(Rectangle().stroke(isCurrent ? Color.blue : Color.black.opacity(0.25), lineWidth: isCurrent ? 2 : 0.8))
        .accessibilityLabel("第 \(bar) 小节，\(items.map(formattedSymbol).joined(separator: "，"))")
    }

    private func beatOffset(_ position: SongPosition) -> Double {
        return min(Double(beatsPerBar), max(0, position.quarterNoteOffset))
    }

    private func barSegments(_ bar: Int, items: [ChordEvent]) -> [BarSegment] {
        let start = SongPosition(bar: bar, beat: 1, division: 1, tick: 0)
        let carry = chords.last { $0.position < start }
        var onsets: [(offset: Double, event: ChordEvent)] = []
        for item in items {
            let offset = beatOffset(item.position)
            if onsets.last?.offset == offset {
                onsets[onsets.count - 1] = (offset, item)
            } else {
                onsets.append((offset, item))
            }
        }
        if onsets.isEmpty {
            return [BarSegment(id: -bar, start: 0, length: Double(beatsPerBar), symbol: carry == nil ? "·" : "%",
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
            let end = index + 1 < onsets.count ? onsets[index + 1].offset : Double(beatsPerBar)
            result.append(BarSegment(id: onset.event.id, start: onset.offset,
                                     length: max(0, end - onset.offset), symbol: formattedSymbol(onset.event),
                                     isCarry: false, isActive: onset.event.id == activeChord?.id))
        }
        return result
    }
}

@main
struct ChordCueApp: App {
    var body: some Scene {
        WindowGroup("ChordCue") { ChordCueView() }
    }
}
