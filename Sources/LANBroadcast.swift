import AppKit
import Network
import Darwin

// All socket state stays on queue. SwiftUI and musical analysis stay on the main thread.
final class LANBroadcast: ObservableObject {
    @Published private(set) var enabled = false
    @Published private(set) var links: [String] = []
    @Published private(set) var viewers = 0
    @Published private(set) var status = "投放未开启"

    private let queue = DispatchQueue(label: "local.codex.chordcue.lan", qos: .userInitiated)
    private var listener: NWListener?
    private var heartbeat: DispatchSourceTimer?
    private var peers: [UUID: BroadcastPeer] = [:]
    private var token = ""
    private var session = ""
    private var chart: Data?
    private var transport: Data?
    private var revision = 0
    private var lastChords: [ChordEvent] = []
    private var lastSections: [KeySection] = []
    private var lastName = ""
    private var lastMeter: String?

    deinit {
        heartbeat?.cancel()
        listener?.cancel()
        for peer in peers.values { peer.connection.cancel() }
    }

    func start() {
        guard !enabled else { return }
        enabled = true
        status = "正在开启局域网投放…"
        revision = 0
        lastChords = []
        lastSections = []
        lastName = ""
        lastMeter = nil
        let token = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        queue.async { [weak self] in
            guard let self else { return }
            self.token = token
            self.session = UUID().uuidString
            self.chart = nil
            self.transport = nil
            do {
                let listener = try NWListener(using: .tcp, on: .any)
                self.listener = listener
                listener.newConnectionHandler = { [weak self] connection in self?.accept(connection) }
                listener.stateUpdateHandler = { [weak self, weak listener] state in
                    guard let self, let listener, self.listener === listener else { return }
                    switch state {
                    case .ready:
                        let port = listener.port?.rawValue ?? 0
                        let addresses = Self.localAddresses()
                        DispatchQueue.main.async {
                            guard self.enabled else { return }
                            self.links = addresses.map { "http://\($0):\(port)/join/\(token)/" }
                            self.status = addresses == ["127.0.0.1"]
                                ? "未找到局域网地址，请连接 Wi-Fi 或有线网络" : "局域网投放已开启"
                        }
                    case .failed(let error):
                        DispatchQueue.main.async { self.stop(); self.status = "开启失败：\(error.localizedDescription)" }
                    default: break
                    }
                }
                listener.start(queue: self.queue)
                let timer = DispatchSource.makeTimerSource(queue: self.queue)
                timer.schedule(deadline: .now() + 1, repeating: 1)
                timer.setEventHandler { [weak self] in
                    guard let self else { return }
                    for peer in self.peers.values where peer.streaming {
                        peer.sendLatest(Data(": heartbeat\n\n".utf8), chart: false, queue: self.queue)
                    }
                }
                self.heartbeat = timer
                timer.resume()
            } catch {
                DispatchQueue.main.async { self.stop(); self.status = "开启失败：\(error.localizedDescription)" }
            }
        }
    }

    func stop() {
        enabled = false
        links = []
        viewers = 0
        status = "投放未开启"
        queue.async { [weak self] in
            guard let self else { return }
            self.listener?.cancel()
            self.listener = nil
            self.heartbeat?.cancel()
            self.heartbeat = nil
            for peer in self.peers.values { peer.connection.cancel() }
            self.peers.removeAll()
            self.chart = nil
            self.transport = nil
        }
    }

    func publish(chords: [ChordEvent], sections: [KeySection], info: ProjectInfo, sample: TransportSample) {
        guard enabled else { return }
        var newChart: Data?
        if chords != lastChords || sections != lastSections || info.name != lastName || info.timeSignature != lastMeter {
            revision += 1
            lastChords = chords
            lastSections = sections
            lastName = info.name
            lastMeter = info.timeSignature
            let events: [[String: Any]] = chords.map { event in
                let key = sections.last { $0.firstBar <= event.position.bar }?.key ?? MusicalKey(root: 0, isMinor: false)
                return ["id": event.id, "bar": event.position.bar, "beat": event.position.beat,
                        "division": event.position.division, "tick": event.position.tick,
                        "symbol": event.symbol, "number": ChordTheory.number(event.symbol, in: key)]
            }
            let keys: [[String: Any]] = sections.map {
                ["bar": $0.firstBar, "root": $0.key.root, "minor": $0.key.isMinor,
                 "family": $0.key.majorFamilyRoot]
            }
            newChart = Self.event("chart", ["revision": revision, "name": info.name,
                                            "meter": info.timeSignature ?? "—", "events": events, "sections": keys])
        }
        let state = Self.transportPayload(sample: sample, info: info, revision: revision)
        let data = Self.event("transport", state)
        queue.async { [weak self] in
            guard let self, self.listener != nil else { return }
            if let newChart { self.chart = newChart }
            self.transport = data
            for peer in self.peers.values where peer.streaming {
                if let newChart { peer.sendLatest(newChart, chart: true, queue: self.queue) }
                peer.sendLatest(data, chart: false, queue: self.queue)
            }
        }
    }

    static func transportPayload(sample: TransportSample, info: ProjectInfo, revision: Int) -> [String: Any] {
        ["revision": revision, "sampleTime": sample.time,
                                  "readMs": sample.readMilliseconds, "valid": sample.valid,
                                  "precise": sample.precise, "rate": sample.beatsPerSecond,
                                  "bar": sample.position.bar, "beat": sample.position.beat,
                                  "division": sample.position.division, "tick": sample.position.tick,
                                  "bpm": info.bpm ?? 0, "meter": info.timeSignature ?? "—",
                                  "playing": sample.playing, "discontinuity": sample.discontinuity]
    }

    private static func event(_ name: String, _ value: [String: Any]) -> Data {
        guard let json = try? JSONSerialization.data(withJSONObject: value) else { return Data() }
        var result = Data("event: \(name)\ndata: ".utf8)
        result.append(json)
        result.append(Data("\n\n".utf8))
        return result
    }

    private func accept(_ connection: NWConnection) {
        guard peers.count < 40, Self.isLocal(connection.endpoint) else { connection.cancel(); return }
        let peer = BroadcastPeer(connection)
        peers[peer.id] = peer
        connection.stateUpdateHandler = { [weak self, weak peer] state in
            switch state {
            case .failed, .cancelled:
                guard let self, let peer else { return }
                self.peers.removeValue(forKey: peer.id)
                self.publishViewers()
            default: break
            }
        }
        connection.start(queue: queue)
        queue.asyncAfter(deadline: .now() + 5) { [weak peer] in
            if let peer, !peer.streaming { peer.connection.cancel() }
        }
        receive(peer)
    }

    private func receive(_ peer: BroadcastPeer) {
        peer.connection.receive(minimumIncompleteLength: 1, maximumLength: 8192) { [weak self, weak peer] data, _, complete, error in
            guard let self, let peer else { return }
            if let data { peer.request.append(data) }
            guard peer.request.count <= 8192 else { peer.connection.cancel(); return }
            if let text = String(data: peer.request, encoding: .utf8), text.contains("\r\n\r\n") {
                self.respond(peer, request: text)
            } else if complete || error != nil { peer.connection.cancel() }
            else { self.receive(peer) }
        }
    }

    private func respond(_ peer: BroadcastPeer, request: String) {
        let received = ProcessInfo.processInfo.systemUptime * 1000
        let first = request.components(separatedBy: "\r\n").first?.split(separator: " ") ?? []
        guard first.count == 3, first[0] == "GET" else { reply(peer, status: "405 Method Not Allowed", body: Data()); return }
        let path = String(first[1]).components(separatedBy: "?")[0]
        let base = "/join/\(token)/"
        guard path.hasPrefix(base) else { reply(peer, status: "404 Not Found", body: Data()); return }
        switch String(path.dropFirst(base.count)) {
        case "":
            guard let url = Bundle.main.url(forResource: "Broadcast", withExtension: "html"),
                  let html = try? Data(contentsOf: url) else {
                reply(peer, status: "503 Service Unavailable", body: Data("网页资源缺失，请更新应用。".utf8)); return
            }
            reply(peer, type: "text/html; charset=utf-8", body: html)
        case "clock":
            let payload: [String: Any] = ["received": received,
                                         "sent": ProcessInfo.processInfo.systemUptime * 1000, "session": session]
            reply(peer, type: "application/json", body: (try? JSONSerialization.data(withJSONObject: payload)) ?? Data())
        case "Metronome.js":
            guard let url = Bundle.main.url(forResource: "Metronome", withExtension: "js"),
                  let script = try? Data(contentsOf: url) else {
                reply(peer, status: "503 Service Unavailable", body: Data()); return
            }
            reply(peer, type: "text/javascript; charset=utf-8", body: script)
        case "events":
            guard peers.values.filter({ $0.streaming }).count < 24 else {
                reply(peer, status: "503 Service Unavailable", body: Data()); return
            }
            peer.streaming = true
            peer.sending = true
            let headers = "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-store\r\nConnection: keep-alive\r\nX-Content-Type-Options: nosniff\r\n\r\nretry: 1000\n\n"
            peer.connection.send(content: Data(headers.utf8), completion: .contentProcessed { [weak self, weak peer] error in
                guard let self, let peer else { return }
                if error != nil { peer.connection.cancel(); return }
                peer.sending = false
                peer.flush(queue: self.queue)
            })
            if let chart { peer.sendLatest(chart, chart: true, queue: queue) }
            if let transport { peer.sendLatest(transport, chart: false, queue: queue) }
            publishViewers()
            peer.connection.receive(minimumIncompleteLength: 1, maximumLength: 1) { [weak peer] _, _, _, _ in peer?.connection.cancel() }
        default: reply(peer, status: "404 Not Found", body: Data())
        }
    }

    private func reply(_ peer: BroadcastPeer, status: String = "200 OK", type: String = "text/plain; charset=utf-8", body: Data) {
        let header = "HTTP/1.1 \(status)\r\nContent-Type: \(type)\r\nContent-Length: \(body.count)\r\nCache-Control: no-store\r\nConnection: close\r\nX-Content-Type-Options: nosniff\r\nReferrer-Policy: no-referrer\r\nContent-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'\r\n\r\n"
        var response = Data(header.utf8)
        response.append(body)
        peer.connection.send(content: response, completion: .contentProcessed { _ in peer.connection.cancel() })
    }

    private func publishViewers() {
        let count = peers.values.filter { $0.streaming }.count
        DispatchQueue.main.async { [weak self] in self?.viewers = count }
    }

    private static func isLocal(_ endpoint: NWEndpoint) -> Bool {
        guard case .hostPort(let host, _) = endpoint else { return false }
        let ip = String(describing: host).lowercased().split(separator: "%").first.map(String.init) ?? ""
        if ip == "::1" || ip.hasPrefix("fe80:") || ip.hasPrefix("fc") || ip.hasPrefix("fd") { return true }
        let v4 = ip.replacingOccurrences(of: "::ffff:", with: "").split(separator: ".").compactMap { Int($0) }
        guard v4.count == 4 else { return false }
        return v4[0] == 127 || v4[0] == 10 || (v4[0] == 192 && v4[1] == 168)
            || (v4[0] == 172 && (16...31).contains(v4[1])) || (v4[0] == 169 && v4[1] == 254)
    }

    private static func localAddresses() -> [String] {
        var head: UnsafeMutablePointer<ifaddrs>?
        guard getifaddrs(&head) == 0 else { return ["127.0.0.1"] }
        defer { freeifaddrs(head) }
        var result: [String] = []
        var cursor = head
        while let item = cursor {
            defer { cursor = item.pointee.ifa_next }
            guard let address = item.pointee.ifa_addr, address.pointee.sa_family == UInt8(AF_INET),
                  item.pointee.ifa_flags & UInt32(IFF_UP) != 0,
                  item.pointee.ifa_flags & UInt32(IFF_LOOPBACK) == 0 else { continue }
            let name = String(cString: item.pointee.ifa_name)
            guard name.hasPrefix("en") || name.hasPrefix("bridge") else { continue }
            var buffer = [CChar](repeating: 0, count: Int(NI_MAXHOST))
            if getnameinfo(address, socklen_t(address.pointee.sa_len), &buffer, socklen_t(buffer.count), nil, 0, NI_NUMERICHOST) == 0 {
                let ip = String(cString: buffer)
                if !result.contains(ip) { result.append(ip) }
            }
        }
        return result.isEmpty ? ["127.0.0.1"] : result
    }
}

private final class BroadcastPeer {
    let id = UUID()
    let connection: NWConnection
    var request = Data()
    var streaming = false
    var sending = false
    private var pendingChart: Data?
    private var pendingState: Data?

    init(_ connection: NWConnection) { self.connection = connection }

    func sendLatest(_ data: Data, chart: Bool, queue: DispatchQueue) {
        if chart { pendingChart = data } else { pendingState = data }
        flush(queue: queue)
    }

    func flush(queue: DispatchQueue) {
        guard !sending else { return }
        let data: Data
        if let chart = pendingChart { data = chart; pendingChart = nil }
        else if let state = pendingState { data = state; pendingState = nil }
        else { return }
        sending = true
        connection.send(content: data, completion: .contentProcessed { [weak self] error in
            guard let self else { return }
            if error != nil { self.connection.cancel(); return }
            self.sending = false
            self.flush(queue: queue)
        })
    }
}
