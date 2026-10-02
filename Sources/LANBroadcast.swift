import AppKit
import Network
import Darwin

// All socket state stays on queue. SwiftUI and musical analysis stay on the main thread.
final class LANBroadcast: ObservableObject {
    @Published private(set) var enabled = false
    @Published private(set) var links: [String] = []
    @Published private(set) var viewers = 0
    @Published private(set) var devices: [LANDeviceSummary] = []
    @Published private(set) var status = "投放未开启"

    private let queue = DispatchQueue(label: "local.codex.chordcue.lan", qos: .userInitiated)
    private var listener: NWListener?
    private var heartbeat: DispatchSourceTimer?
    private var peers: [UUID: BroadcastPeer] = [:]
    private var token = ""
    private var session = ""
    private var chart: BroadcastChartFrame?
    private var transport: Data?
    private var revision = 0
    private var lastChartJSON: Data?
    private var lastProjectRevision: Int?
    private var lastKeySettingsRevision: Int?
    private var registry = LANDeviceRegistry()
    private var generation = UUID()

    deinit {
        heartbeat?.cancel()
        listener?.cancel()
        for peer in peers.values { peer.cancel() }
    }

    func start() {
        guard !enabled else { return }
        enabled = true
        status = "正在开启局域网投放…"
        revision = 0
        lastChartJSON = nil
        lastProjectRevision = nil
        lastKeySettingsRevision = nil
        let run = UUID()
        generation = run
        let token = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        queue.async { [weak self] in
            guard let self else { return }
            self.token = token
            self.session = UUID().uuidString
            self.registry = LANDeviceRegistry()
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
                            guard self.enabled, self.generation == run else { return }
                            self.links = addresses.map { address in
                                let authority = address.contains(":") ? "[" + address.replacingOccurrences(of: "%", with: "%25") + "]" : address
                                return "http://\(authority):\(port)/join/\(token)/"
                            }
                            self.status = addresses == ["127.0.0.1"]
                                ? "未找到局域网地址，请连接 Wi-Fi 或有线网络" : "局域网投放已开启"
                        }
                    case .failed(let error):
                        DispatchQueue.main.async { guard self.generation == run else { return }; self.stop(); self.status = "开启失败：\(error.localizedDescription)" }
                    default: break
                    }
                }
                listener.start(queue: self.queue)
                let timer = DispatchSource.makeTimerSource(queue: self.queue)
                timer.schedule(deadline: .now() + 1, repeating: 1)
                timer.setEventHandler { [weak self] in
                    guard let self else { return }
                    for peer in self.peers.values where peer.streaming {
                        peer.heartbeat(queue: self.queue)
                    }
                    self.publishViewers()
                }
                self.heartbeat = timer
                timer.resume()
            } catch {
                DispatchQueue.main.async { guard self.generation == run else { return }; self.stop(); self.status = "开启失败：\(error.localizedDescription)" }
            }
        }
    }

    func stop() {
        lastChartJSON = nil
        enabled = false
        generation = UUID()
        links = []
        viewers = 0
        devices = []
        status = "投放未开启"
        queue.async { [weak self] in
            guard let self else { return }
            self.listener?.cancel()
            self.listener = nil
            self.heartbeat?.cancel()
            self.heartbeat = nil
            for peer in self.peers.values { peer.cancel() }
            self.peers.removeAll()
            self.chart = nil
            self.transport = nil
            self.registry = LANDeviceRegistry()
        }
    }

    func assign(clientId: String, partId: String?, view: String, label: String? = nil) {
        queue.async { [weak self] in
            guard let self else { return }
            do {
                try self.registry.assign(clientId, partId: partId, view: view, label: label)
                for peer in self.peers.values where peer.streaming { self.sendAssignment(peer) }
                self.publishViewers()
            } catch { DispatchQueue.main.async { self.status = "分配失败：设备或谱面不支持该视图" } }
        }
    }

    func publish(chords: [ChordEvent], sections: [KeySection], info: ProjectInfo, sample: TransportSample,
                 score: ScoreIR? = nil, selectedPartId: String? = nil, bars: Int? = nil, route: [String: Any] = [:],
                 projectRevision: Int? = nil, playbackPlan: [String: Any]? = nil, measures: [[String: Any]] = [], keySettingsRevision: Int = 0) {
        guard enabled else { return }
        var newChart: Data?
        if projectRevision == nil || projectRevision != lastProjectRevision || keySettingsRevision != lastKeySettingsRevision { do {
            let events: [[String: Any]] = (score == nil ? chords : []).map { event in
                let key = sections.last { $0.firstBar <= event.position.bar }?.key ?? MusicalKey(root: 0, isMinor: false)
                return ["id": event.id, "bar": event.position.bar, "beat": event.position.beat,
                        "division": event.position.division, "tick": event.position.tick,
                        "symbol": event.symbol, "number": ChordTheory.number(event.symbol, in: key)]
            }
            let keys: [[String: Any]] = (score == nil ? sections : []).map {
                ["bar": $0.firstBar, "root": $0.key.root, "minor": $0.key.isMinor,
                 "family": $0.key.majorFamilyRoot]
            }
            var payload: [String: Any] = ["name": info.name, "bars": bars ?? max(chords.last?.position.bar ?? 1, sample.position.bar),
                                          "meter": info.timeSignature ?? "—", "events": events, "sections": keys]
            if score == nil && !measures.isEmpty { payload["measures"] = measures }
            var embeddedBytes = 0
            if let score {
                let sourceData = try score.encodedValidated()
                embeddedBytes += sourceData.count
                payload["score"] = try JSONSerialization.jsonObject(with: sourceData)
                payload["protocolVersion"] = 2
                payload["selectedPartId"] = selectedPartId as Any? ?? NSNull()
            }
            if let playbackPlan {
                let routeData = try JSONSerialization.data(withJSONObject: playbackPlan, options: [.sortedKeys])
                guard routeData.count <= 32 * 1024 * 1024 else { throw LANDeviceRegistry.Failure.invalid }
                embeddedBytes += routeData.count
                payload["route"] = playbackPlan
            }
            let identity = try JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])
            if identity != lastChartJSON {
                payload["revision"] = revision + 1
                let data = try JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])
                guard data.count <= 65 * 1024 * 1024,
                      score == nil || data.count - embeddedBytes <= 1024 * 1024 else {
                    throw NSError(domain: "ChordCue.LAN", code: 1,
                                  userInfo: [NSLocalizedDescriptionKey: "乐谱投放数据过大：\(data.count) 字节（上限 65 MiB）"])
                }
                newChart = Self.event("chart", payload)
                revision += 1
                lastChartJSON = identity
            }
            lastProjectRevision = projectRevision
            lastKeySettingsRevision = keySettingsRevision
        } catch { status = "谱面无法投放：\(error)"; return } }
        var state = Self.transportPayload(sample: sample, info: info, revision: revision)
        for (key, value) in route where key != "revision" { state[key] = value }
        let data = Self.event("transport", state)
        guard data.count <= 64 * 1024 + 26 else { status = "播放位置数据超过 64 KiB，投放暂停更新"; return }
        let currentRevision = revision
        queue.async { [weak self] in
            guard let self, self.listener != nil else { return }
            let frame = newChart.map { BroadcastChartFrame($0) }
            if let frame { self.chart = frame; self.registry.setParts(score, revision: currentRevision) }
            self.transport = data
            for peer in self.peers.values where peer.streaming {
                if let frame { peer.sendChart(frame, queue: self.queue) }
                self.sendAssignment(peer)
                peer.sendLatest(data, queue: self.queue)
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
        guard let json = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]) else { return Data() }
        var result = Data("event: \(name)\ndata: ".utf8)
        result.append(json)
        result.append(Data("\n\n".utf8))
        return result
    }

    private func accept(_ connection: NWConnection) {
        guard peers.count < 40, Self.isLocal(connection.endpoint) else { connection.cancel(); return }
        let peer = BroadcastPeer(connection) { [weak self] peer, frame in
            self?.reserveChart(peer, frame: frame) ?? false
        }
        peers[peer.id] = peer
        connection.stateUpdateHandler = { [weak self, weak peer] state in
            switch state {
            case .failed, .cancelled:
                guard let self, let peer else { return }
                peer.cancel()
                self.peers.removeValue(forKey: peer.id)
                if let clientId = peer.clientId { self.registry.disconnect(clientId, connection: peer.id) }
                self.publishViewers()
            default: break
            }
        }
        connection.start(queue: queue)
        queue.asyncAfter(deadline: .now() + 5) { [weak peer] in
            if let peer, !peer.streaming { peer.cancel() }
        }
        receive(peer)
    }

    private func receive(_ peer: BroadcastPeer) {
        peer.connection.receive(minimumIncompleteLength: 1, maximumLength: 8192) { [weak self, weak peer] data, _, complete, error in
            guard let self, let peer, !peer.closed else { return }
            if let data { peer.request.append(data) }
            guard peer.request.count <= 16384 else { peer.cancel(); return }
            do {
                if let request = try LANRequest.parse(peer.request) { self.respond(peer, request: request); return }
            } catch LANDeviceRegistry.Failure.tooLarge { self.reply(peer, status: "413 Content Too Large", body: Data()); return }
            catch { self.reply(peer, status: "400 Bad Request", body: Data()); return }
            if complete || error != nil { peer.cancel() } else { self.receive(peer) }
        }
    }

    private func respond(_ peer: BroadcastPeer, request: LANRequest) {
        peer.request.removeAll(keepingCapacity: false)
        let path = request.path
        let base = "/join/\(token)/"
        guard path.hasPrefix(base) else { reply(peer, status: "404 Not Found", body: Data()); return }
        let route = String(path.dropFirst(base.count))
        if request.method == "POST" {
            guard ["register", "telemetry"].contains(route) else { reply(peer, status: "405 Method Not Allowed", body: Data()); return }
            let authority = request.headers["host"] ?? ""
            guard let host = URLComponents(string: "http://" + authority), host.port == Int(listener?.port?.rawValue ?? 0),
                  let name = host.host, name == "localhost" || Self.isLocalAddress(name),
                  request.headers["origin"] == "http://" + authority,
                  request.headers["content-type"]?.components(separatedBy: ";").first == "application/json" else {
                reply(peer, status: "403 Forbidden", body: Data()); return
            }
            do {
                let payload = try LANRequest.jsonObject(request.body)
                var result: [String: Any]
                if route == "register" { result = try registry.register(payload, browser: request.headers["user-agent"] ?? "浏览器"); result["sessionId"] = session }
                else { result = try registry.telemetry(payload) }
                reply(peer, type: "application/json", body: try JSONSerialization.data(withJSONObject: result))
                publishViewers()
            } catch LANDeviceRegistry.Failure.forbidden { reply(peer, status: "403 Forbidden", body: Data()) }
            catch { reply(peer, status: "400 Bad Request", body: Data()) }
            return
        }
        guard request.method == "GET" else { reply(peer, status: "405 Method Not Allowed", body: Data()); return }
        switch String(path.dropFirst(base.count)) {
        case "":
            guard let url = Bundle.main.url(forResource: "Broadcast", withExtension: "html"),
                  let html = try? Data(contentsOf: url) else {
                reply(peer, status: "503 Service Unavailable", body: Data("网页资源缺失，请更新应用。".utf8)); return
            }
            reply(peer, type: "text/html; charset=utf-8", body: html)
        case "clock":
            let payload: [String: Any] = ["received": request.received,
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
            if let clientId = request.query["clientId"] {
                do { try registry.attach(clientId, key: request.query["resumeKey"] ?? "", connection: peer.id); peer.clientId = clientId }
                catch { reply(peer, status: "403 Forbidden", body: Data()); return }
            }
            peer.streaming = true
            peer.sending = true
            let headers = "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-store\r\nConnection: keep-alive\r\nX-Content-Type-Options: nosniff\r\n\r\nretry: 1000\n\n"
            peer.connection.send(content: Data(headers.utf8), completion: .contentProcessed { [weak self, weak peer] error in
                guard let self, let peer, !peer.closed else { return }
                if error != nil { peer.cancel(); return }
                peer.sending = false
                peer.flush(queue: self.queue)
            })
            if let chart { peer.sendChart(chart, queue: queue) }
            sendAssignment(peer)
            if let transport { peer.sendLatest(transport, queue: queue) }
            publishViewers()
            peer.connection.receive(minimumIncompleteLength: 1, maximumLength: 1) { [weak peer] _, _, _, _ in peer?.cancel() }
        default:
            let assets = ["score/DeviceClient.js", "score/PlaybackPlan.js", "score/ScoreIO.js", "score/ScoreView.js", "score/ScoreView.css",
                          "vendor/fflate/umd/index.js", "vendor/alphatab/dist/alphaTab.js", "vendor/alphatab/dist/alphaTab.min.js",
                          "vendor/alphatab/dist/font/Bravura.woff2", "vendor/alphatab/dist/font/Bravura.woff", "vendor/alphatab/dist/font/Bravura.otf"]
            guard assets.contains(route), let root = Bundle.main.resourceURL,
                  let data = try? Data(contentsOf: root.appendingPathComponent(route)) else { reply(peer, status: "404 Not Found", body: Data()); return }
            let ext = (route as NSString).pathExtension
            let types = ["js": "text/javascript; charset=utf-8", "css": "text/css; charset=utf-8", "woff2": "font/woff2", "woff": "font/woff", "otf": "font/otf"]
            reply(peer, type: types[ext] ?? "application/octet-stream", body: data)
        }
    }

    private func reply(_ peer: BroadcastPeer, status: String = "200 OK", type: String = "text/plain; charset=utf-8", body: Data) {
        let header = "HTTP/1.1 \(status)\r\nContent-Type: \(type)\r\nContent-Length: \(body.count)\r\nCache-Control: no-store\r\nConnection: close\r\nX-Content-Type-Options: nosniff\r\nReferrer-Policy: no-referrer\r\nContent-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; font-src 'self' data:; frame-ancestors 'none'\r\n\r\n"
        var response = Data(header.utf8)
        response.append(body)
        peer.connection.send(content: response, completion: .contentProcessed { _ in peer.cancel() })
    }

    private func publishViewers() {
        let count = peers.values.filter { $0.streaming }.count
        let summaries = registry.summaries()
        DispatchQueue.main.async { [weak self] in guard let self, self.enabled else { return }; self.viewers = count; self.devices = summaries }
    }

    private func reserveChart(_ peer: BroadcastPeer, frame: BroadcastChartFrame) -> Bool {
        // Count a shared immutable chart once, even when many peers send it.
        // Completed charts keep no body here; stale frames have a finite budget.
        func usage() -> Int {
            var sizes = [frame.identity: frame.data.count]
            for item in peers.values {
                if let active = item.activeChart { sizes[active.identity] = active.data.count }
            }
            return sizes.values.reduce(0, +)
        }
        for victim in peers.values.sorted(by: { $0.chartStarted < $1.chartStarted }) {
            if usage() <= 256 * 1024 * 1024 { return true }
            if victim !== peer, victim.activeChart != nil { victim.cancel() }
        }
        return usage() <= 256 * 1024 * 1024
    }

    private func sendAssignment(_ peer: BroadcastPeer) {
        guard let id = peer.clientId, var value = registry.assignment(id) else { return }
        value["sessionId"] = session
        let data = Self.event("assignment", value)
        guard data != peer.lastAssignment else { return }
        peer.lastAssignment = data
        peer.sendAssignment(data, queue: queue)
    }

    private static func isLocal(_ endpoint: NWEndpoint) -> Bool {
        guard case .hostPort(let host, _) = endpoint else { return false }
        return isLocalAddress(String(describing: host))
    }

    private static func isLocalAddress(_ address: String) -> Bool {
        let ip = address.trimmingCharacters(in: CharacterSet(charactersIn: "[]")).lowercased().split(separator: "%").first.map(String.init) ?? ""
        var v6 = in6_addr()
        if inet_pton(AF_INET6, ip, &v6) == 1 {
            let bytes = withUnsafeBytes(of: v6) { Array($0) }
            if bytes.prefix(15).allSatisfy({ $0 == 0 }) && bytes[15] == 1 { return true }
            if bytes[0] == 0xfe && bytes[1] & 0xc0 == 0x80 || bytes[0] & 0xfe == 0xfc { return true }
            guard bytes.prefix(10).allSatisfy({ $0 == 0 }), bytes[10] == 0xff, bytes[11] == 0xff else { return false }
            return localIPv4(bytes.suffix(4).map(Int.init))
        } else { var v4 = in_addr(); guard inet_pton(AF_INET, ip, &v4) == 1 else { return false } }
        let v4 = ip.replacingOccurrences(of: "::ffff:", with: "").split(separator: ".").compactMap { Int($0) }
        guard v4.count == 4 else { return false }
        return localIPv4(v4)
    }

    private static func localIPv4(_ v4: [Int]) -> Bool {
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
            guard let address = item.pointee.ifa_addr, [UInt8(AF_INET), UInt8(AF_INET6)].contains(address.pointee.sa_family),
                  item.pointee.ifa_flags & UInt32(IFF_UP) != 0,
                  item.pointee.ifa_flags & UInt32(IFF_LOOPBACK) == 0 else { continue }
            var buffer = [CChar](repeating: 0, count: Int(NI_MAXHOST))
            if getnameinfo(address, socklen_t(address.pointee.sa_len), &buffer, socklen_t(buffer.count), nil, 0, NI_NUMERICHOST) == 0 {
                let ip = String(cString: buffer)
                if isLocalAddress(ip), !result.contains(ip) { result.append(ip) }
            }
        }
        return result.isEmpty ? ["127.0.0.1"] : result
    }
}

private final class BroadcastChartFrame {
    let identity = UUID()
    let data: Data
    init(_ data: Data) { self.data = data }
}

private final class BroadcastPeer {
    let id = UUID()
    let connection: NWConnection
    var request = Data()
    var streaming = false
    var sending = false
    var clientId: String?
    var lastAssignment: Data?
    private(set) var closed = false
    private(set) var activeChart: BroadcastChartFrame?
    private(set) var chartStarted = 0.0
    private var pendingChart: BroadcastChartFrame?
    private var pendingAssignment: Data?
    private var pendingState: Data?
    private var sendSerial = 0
    private var activeData: Data?
    private var activeOffset = 0
    private let reserveChart: (BroadcastPeer, BroadcastChartFrame) -> Bool

    init(_ connection: NWConnection, reserveChart: @escaping (BroadcastPeer, BroadcastChartFrame) -> Bool) {
        self.connection = connection; self.reserveChart = reserveChart
    }

    func sendChart(_ frame: BroadcastChartFrame, queue: DispatchQueue) {
        guard !closed else { return }
        pendingChart = frame
        flush(queue: queue)
    }

    func sendLatest(_ data: Data, queue: DispatchQueue) {
        guard !closed else { return }
        pendingState = data
        flush(queue: queue)
    }

    func sendAssignment(_ data: Data, queue: DispatchQueue) {
        guard !closed else { return }
        pendingAssignment = data; flush(queue: queue)
    }

    func heartbeat(queue: DispatchQueue) {
        if !sending, activeData == nil, pendingChart == nil, pendingAssignment == nil, pendingState == nil { sendLatest(Data(": heartbeat\n\n".utf8), queue: queue) }
    }

    func flush(queue: DispatchQueue) {
        guard !closed, !sending else { return }
        if activeData == nil {
            if let chart = pendingChart {
                guard reserveChart(self, chart) else { cancel(); return }
                activeChart = chart; chartStarted = ProcessInfo.processInfo.systemUptime
                activeData = chart.data; pendingChart = nil
            }
            else if let assignment = pendingAssignment { activeData = assignment; pendingAssignment = nil }
            else if let state = pendingState { activeData = state; pendingState = nil }
            else { return }
            activeOffset = 0
        }
        guard let activeData else { return }
        let end = min(activeData.count, activeOffset + 64 * 1024)
        let data = activeData.subdata(in: activeOffset..<end)
        activeOffset = end
        sending = true
        sendSerial += 1
        let serial = sendSerial
        queue.asyncAfter(deadline: .now() + 5) { [weak self] in
            if let self, self.sending, self.sendSerial == serial { self.cancel() }
        }
        connection.send(content: data, completion: .contentProcessed { [weak self] error in
            guard let self, !self.closed, self.sendSerial == serial else { return }
            if error != nil { self.cancel(); return }
            self.sending = false
            if self.activeOffset == self.activeData?.count {
                self.activeData = nil; self.activeChart = nil; self.activeOffset = 0
            }
            self.flush(queue: queue)
        })
    }

    func cancel() {
        guard !closed else { return }
        closed = true; streaming = false; sending = false; sendSerial += 1
        activeData = nil; activeChart = nil; activeOffset = 0
        pendingChart = nil; pendingAssignment = nil; pendingState = nil
        lastAssignment = nil; request.removeAll(keepingCapacity: false)
        connection.cancel()
    }
}
