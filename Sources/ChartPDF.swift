import AppKit
import CoreGraphics

enum ChartNotation {
    case chords
    case numbers

    var title: String { self == .chords ? "和弦谱" : "级数谱" }
}

enum ChartPDF {
    enum ExportError: LocalizedError {
        case cannotCreatePDF

        var errorDescription: String? { "无法创建 PDF 文件，请选择其他保存位置。" }
    }

    static func write(chords: [ChordEvent], sections: [KeySection], notation: ChartNotation,
                      semitones: Int, preferFlats: Bool, beatsPerBar: Int = 4, to url: URL) throws {
        let meter = CGFloat(max(1, beatsPerBar))
        let chords = ChordEvent.alignedForChart(chords, beatsPerBar: beatsPerBar)
        var page = CGRect(x: 0, y: 0, width: 595, height: 842)
        guard let consumer = CGDataConsumer(url: url as CFURL),
              let context = CGContext(consumer: consumer, mediaBox: &page, nil) else {
            throw ExportError.cannotCreatePDF
        }
        let lastBar = max(1, chords.map(\.position.bar).max() ?? 1)
        let rowsPerPage = 7
        let barsPerRow = 4
        let barsPerPage = rowsPerPage * barsPerRow
        let pageCount = (lastBar + barsPerPage - 1) / barsPerPage

        for pageIndex in 0..<pageCount {
            context.beginPDFPage(nil)
            NSGraphicsContext.saveGraphicsState()
            NSGraphicsContext.current = NSGraphicsContext(cgContext: context, flipped: false)
            context.setFillColor(NSColor.white.cgColor)
            context.fill(page)

            draw(url.deletingPathExtension().lastPathComponent,
                 in: CGRect(x: 42, y: 790, width: 450, height: 28),
                 size: 21, weight: .bold, color: .black)
            draw("\(pageIndex + 1) / \(pageCount)", in: CGRect(x: 492, y: 795, width: 60, height: 18),
                 size: 10, color: .gray, alignment: .right)
            let pageBar = pageIndex * barsPerPage + 1
            let pageKey = sections.last(where: { $0.firstBar <= pageBar })?.key
                ?? MusicalKey(root: 0, isMinor: false)
            let shiftedPageKey = MusicalKey(root: (pageKey.root + semitones + 24) % 12,
                                            isMinor: pageKey.isMinor)
            let subtitle = notation == .numbers
                ? "\(shiftedPageKey.label) · 数字为和弦根音，七和弦的 7 标在右下角"
                : "和弦移调 \(semitones >= 0 ? "+" : "")\(semitones) 半音"
            draw(subtitle, in: CGRect(x: 42, y: 761, width: 500, height: 20),
                 size: 10, color: .darkGray)

            let left: CGFloat = 42
            let cellWidth: CGFloat = 127.75
            let cellHeight: CGFloat = 95
            for localIndex in 0..<barsPerPage {
                let bar = pageIndex * barsPerPage + localIndex + 1
                if bar > lastBar { break }
                let column = localIndex % barsPerRow
                let row = localIndex / barsPerRow
                let rect = CGRect(x: left + CGFloat(column) * cellWidth,
                                  y: 650 - CGFloat(row) * cellHeight,
                                  width: cellWidth, height: cellHeight)
                context.setStrokeColor(NSColor(white: 0.72, alpha: 1).cgColor)
                context.setLineWidth(0.6)
                context.stroke(rect)
                draw("\(bar)", in: CGRect(x: rect.minX + 7, y: rect.maxY - 20,
                                           width: 28, height: 14), size: 9, color: .gray)

                if let section = sections.last(where: { $0.firstBar <= bar }), section.firstBar == bar {
                    let shifted = MusicalKey(root: (section.key.root + semitones + 24) % 12,
                                             isMinor: section.key.isMinor)
                    draw(shifted.label, in: CGRect(x: rect.minX + 32, y: rect.maxY - 22,
                                                   width: rect.width - 39, height: 17),
                         size: 10, weight: .semibold, color: NSColor.systemBlue, alignment: .right)
                }

                let events = chords.filter { $0.position.bar == bar }
                let previous = chords.last { $0.position.bar < bar }
                if events.isEmpty {
                    draw(previous == nil ? "·" : "%",
                         in: CGRect(x: rect.minX + 8, y: rect.minY + 30,
                                    width: rect.width - 16, height: 28),
                         size: 18, color: .darkGray, alignment: .center)
                    continue
                }
                var onsets: [(offset: CGFloat, event: ChordEvent)] = []
                for event in events {
                    let offset = min(meter, max(0, CGFloat(event.position.quarterNoteOffset)))
                    if onsets.last?.offset == offset {
                        onsets[onsets.count - 1] = (offset, event)
                    } else {
                        onsets.append((offset, event))
                    }
                }
                if let first = onsets.first, first.offset > 0 {
                    let width = (rect.width - 12) * first.offset / meter
                    draw(previous == nil ? "·" : "—",
                         in: CGRect(x: rect.minX + 6, y: rect.minY + 31, width: width, height: 28),
                         size: 16, color: .gray, alignment: .center)
                }
                for index in onsets.indices {
                    let onset = onsets[index]
                    let end = index + 1 < onsets.count ? onsets[index + 1].offset : meter
                    let contentWidth = rect.width - 12
                    let x = rect.minX + 6 + contentWidth * onset.offset / meter
                    let width = contentWidth * (end - onset.offset) / meter
                    let key = sections.last(where: { $0.firstBar <= bar })?.key
                        ?? MusicalKey(root: 0, isMinor: false)
                    let symbol = notation == .numbers
                        ? ChordTheory.number(onset.event.symbol, in: key)
                        : ChordTheory.displayChord(ChordTheory.transpose(onset.event.symbol, by: semitones,
                                                preferFlats: preferFlats))
                    if onset.offset > 0 {
                        context.setStrokeColor(NSColor(white: 0.75, alpha: 1).cgColor)
                        context.move(to: CGPoint(x: x, y: rect.minY + 25))
                        context.addLine(to: CGPoint(x: x, y: rect.minY + 62))
                        context.strokePath()
                    }
                    draw(symbol, in: CGRect(x: x + 2, y: rect.minY + 31,
                                            width: max(10, width - 4), height: 28),
                         size: width < 36 ? 9 : width < 57 ? 11 : 15,
                         weight: .semibold, color: .black, alignment: .center)
                }
            }
            NSGraphicsContext.restoreGraphicsState()
            context.endPDFPage()
        }
        context.closePDF()
    }

    private static func draw(_ text: String, in rect: CGRect, size: CGFloat,
                             weight: NSFont.Weight = .regular, color: NSColor,
                             alignment: NSTextAlignment = .left) {
        let paragraph = NSMutableParagraphStyle()
        paragraph.alignment = alignment
        paragraph.lineBreakMode = .byTruncatingTail
        var fittedSize = size
        while fittedSize > 6 {
            let width = (text as NSString).size(withAttributes: [
                .font: NSFont.systemFont(ofSize: fittedSize, weight: weight)
            ]).width
            if width <= rect.width { break }
            fittedSize -= 0.5
        }
        NSAttributedString(string: text, attributes: [
            .font: NSFont.systemFont(ofSize: fittedSize, weight: weight),
            .foregroundColor: color,
            .paragraphStyle: paragraph
        ]).draw(in: rect)
    }
}
