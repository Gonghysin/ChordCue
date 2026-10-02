import SwiftUI
import WebKit

struct NativeMetronome: NSViewRepresentable {
    let info: ProjectInfo
    let sample: TransportSample
    let visible: Bool
    @Binding var enabled: Bool

    func makeCoordinator() -> Coordinator { Coordinator(enabled: $enabled) }

    func makeNSView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        // JavaScript explicitly enables sound; avoid a second WebKit gesture gate on resume.
        configuration.mediaTypesRequiringUserActionForPlayback = []
        configuration.userContentController.addUserScript(WKUserScript(
            source: "window.ChordCueNative = true;", injectionTime: .atDocumentStart, forMainFrameOnly: true))
        configuration.userContentController.add(context.coordinator, name: "clock")
        configuration.userContentController.add(context.coordinator, name: "audioState")
        let view = WKWebView(frame: .zero, configuration: configuration)
        context.coordinator.webView = view
        context.coordinator.observeSession()
        view.navigationDelegate = context.coordinator
        if let url = Bundle.main.url(forResource: "Broadcast", withExtension: "html") {
            view.loadFileURL(url, allowingReadAccessTo: url.deletingLastPathComponent())
        }
        return view
    }

    func updateNSView(_ view: WKWebView, context: Context) {
        context.coordinator.enabled = $enabled
        if context.coordinator.requestedEnabled != enabled {
            context.coordinator.requestedEnabled = enabled
            context.coordinator.pendingAudio = enabled
        }
        if !visible && !enabled && context.coordinator.pendingAudio == nil {
            context.coordinator.pending = nil
            return
        }
        context.coordinator.pending = ["name": info.name,
                                       "transport": LANBroadcast.transportPayload(sample: sample, info: info, revision: 1)]
        context.coordinator.flush()
    }

    static func dismantleNSView(_ view: WKWebView, coordinator: Coordinator) {
        coordinator.ready = false
        coordinator.pending = nil
        coordinator.removeObservers()
        view.configuration.userContentController.removeScriptMessageHandler(forName: "clock")
        view.configuration.userContentController.removeScriptMessageHandler(forName: "audioState")
        view.evaluateJavaScript("window.ChordCueMetronome?.shutdown();")
        view.navigationDelegate = nil
    }

    final class Coordinator: NSObject, WKNavigationDelegate, WKScriptMessageHandler {
        weak var webView: WKWebView?
        var ready = false
        var pending: [String: Any]?
        var enabled: Binding<Bool>
        var requestedEnabled = false
        var pendingAudio: Bool?
        private var sending = false
        private let session = UUID().uuidString
        private var observers: [NSObjectProtocol] = []

        init(enabled: Binding<Bool>) { self.enabled = enabled }

        func observeSession() {
            for name in [NSWorkspace.sessionDidResignActiveNotification, NSWorkspace.willSleepNotification] {
                observers.append(NSWorkspace.shared.notificationCenter.addObserver(forName: name, object: nil,
                    queue: .main) { [weak self] _ in
                        self?.webView?.evaluateJavaScript("window.ChordCueMetronome?.disable();")
                    })
            }
        }

        func removeObservers() {
            for observer in observers { NSWorkspace.shared.notificationCenter.removeObserver(observer) }
            observers = []
        }

        deinit { removeObservers() }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            ready = true
            flush()
        }

        func flush() {
            guard ready, !sending, var value = pending, let view = webView else { return }
            if let audio = pendingAudio { value["audioEnabled"] = audio }
            guard
                  let json = try? JSONSerialization.data(withJSONObject: value),
                  let text = String(data: json, encoding: .utf8) else { return }
            pending = nil
            pendingAudio = nil
            sending = true
            view.evaluateJavaScript("window.acceptNativeState(\(text));") { [weak self] _, _ in
                self?.sending = false
                self?.flush()
            }
        }

        func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
            if message.name == "audioState", message.frameInfo.isMainFrame, let value = message.body as? Bool {
                requestedEnabled = value
                pendingAudio = nil
                enabled.wrappedValue = value
                return
            }
            let received = ProcessInfo.processInfo.systemUptime * 1000
            guard message.name == "clock", message.frameInfo.isMainFrame,
                  let value = message.body as? [String: Any],
                  let started = value["t0"] as? Double, started.isFinite else { return }
            let clock: [String: Any] = ["received": received,
                                       "sent": ProcessInfo.processInfo.systemUptime * 1000, "session": session]
            guard let json = try? JSONSerialization.data(withJSONObject: clock),
                  let text = String(data: json, encoding: .utf8) else { return }
            webView?.evaluateJavaScript("window.acceptNativeClock(\(text), \(started));")
        }

        func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                     decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            decisionHandler(navigationAction.request.url?.isFileURL == true ? .allow : .cancel)
        }
    }
}
