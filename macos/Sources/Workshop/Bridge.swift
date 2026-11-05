import Foundation
import WebKit

/// The `window.WorkshopNative` bridge for the shell's web view. The client
/// is the phone's (mobile/native/inject.ts, bundled as bridge.js by the build),
/// which posts through `window.ReactNativeWebView`; a shim maps that onto this
/// message handler. Answers and events go back through the client's receive
/// function, as on the phone. The wire model is shared/native-bridge.ts.
@MainActor
final class Bridge: NSObject, WKScriptMessageHandler {
    /// Takes the request's arguments and the app it says it comes from
    /// (`app`, set by `WorkshopNative.forApp`).
    typealias Handler = @MainActor ([Any], String?) async throws -> Any?

    weak var webView: WKWebView?
    /// Runs each time the client installs into a new top document (first
    /// load, reload), so native state of the previous document can go.
    var onDocument: (() -> Void)?
    /// Takes the web UI's background color each time it changes
    /// (mobile/native/appearance.ts).
    var onAppearance: ((CGColor) -> Void)?
    private var handlers: [String: Handler] = [:]

    static let channel = "workshopNative"
    static let receive = "__WORKSHOP_NATIVE_RECV__"

    init(controller: WKUserContentController) {
        super.init()
        controller.add(self, name: Self.channel)
        if let client = Self.client() {
            let shim = "window.ReactNativeWebView={postMessage:function(m){" +
                "window.webkit.messageHandlers.\(Self.channel).postMessage(m);}};"
            controller.addUserScript(WKUserScript(
                source: shim + Engine.script + client, injectionTime: .atDocumentStart, forMainFrameOnly: true
            ))
        }
    }

    private static func client() -> String? {
        guard let url = Bundle.main.url(forResource: "bridge", withExtension: "js") else { return nil }
        return try? String(contentsOf: url, encoding: .utf8)
    }

    /// Registers `cap.method`; the name matches the bridge manifest
    /// ("pages", "contentRules.set").
    func handle(_ cap: String, _ method: String, _ handler: @escaping Handler) {
        handlers["\(cap).\(method)"] = handler
    }

    func emit(_ topic: String, _ payload: Any?) {
        send(["t": "evt", "topic": topic, "payload": payload ?? NSNull()])
    }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.frameInfo.isMainFrame,
              let text = message.body as? String,
              let data = text.data(using: .utf8),
              let request = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return }
        if request["t"] as? String == "hello" {
            onDocument?()
            return
        }
        if request["t"] as? String == "ws-appearance" {
            if let bg = request["bg"] as? String, let color = Self.color(bg) { onAppearance?(color) }
            return
        }
        guard request["t"] as? String == "req",
              let id = request["id"] as? Int,
              let cap = request["cap"] as? String,
              let method = request["method"] as? String
        else { return }
        let args = request["args"] as? [Any] ?? []
        let app = request["app"] as? String
        guard let handler = handlers["\(cap).\(method)"] else {
            send(["t": "res", "id": id, "ok": false, "error": "unknown method \(cap).\(method)"])
            return
        }
        Task { @MainActor in
            do {
                let result = try await handler(args, app)
                self.send(["t": "res", "id": id, "ok": true, "result": result ?? NSNull()])
            } catch {
                self.send(["t": "res", "id": id, "ok": false, "error": Self.message(error)])
            }
        }
    }

    /// A computed CSS color, "rgb(16, 13, 17)" or "rgba(...)", as a CGColor.
    static func color(_ css: String) -> CGColor? {
        guard let open = css.firstIndex(of: "("), let close = css.lastIndex(of: ")") else { return nil }
        let parts = css[css.index(after: open)..<close]
            .split(whereSeparator: { ", /".contains($0) })
            .compactMap { Double($0) }
        guard parts.count >= 3 else { return nil }
        return CGColor(srgbRed: parts[0] / 255, green: parts[1] / 255, blue: parts[2] / 255,
                       alpha: parts.count > 3 ? parts[3] : 1)
    }

    private static func message(_ error: Error) -> String {
        if let error = error as? LocalizedError, let text = error.errorDescription { return text }
        return error.localizedDescription
    }

    private func send(_ object: [String: Any]) {
        guard let webView,
              JSONSerialization.isValidJSONObject(object),
              let data = try? JSONSerialization.data(withJSONObject: object),
              let json = String(data: data, encoding: .utf8)
        else { return }
        webView.evaluateJavaScript("window.\(Self.receive)&&window.\(Self.receive)(\(json))")
    }
}
