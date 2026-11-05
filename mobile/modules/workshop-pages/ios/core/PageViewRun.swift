import Foundation
import WebKit
#if canImport(UIKit)
import UIKit
#else
import AppKit
#endif

/// Scripts run in a page, messages from its scripts, and pictures of it.
extension PageView {
    /// Runs `script` as an async function body in the main frame, in the
    /// page's world or, when `isolated`, in `PageUserScripts.world`. The
    /// result is always a JSON string `{ok, value}` or `{ok, error}`. A page
    /// whose web process ended answers that it needs a reload: WebKit would
    /// otherwise report an "unsupported type" for every script sent to it.
    func run(_ script: String, isolated: Bool = false, done: @escaping (String) -> Void) {
        if crashed {
            done(Self.failure(Self.crashedMessage))
            return
        }
        let body = """
        try {
          const __workshopValue = await (async () => {
        \(script)
          })();
          return JSON.stringify({ ok: true, value: __workshopValue === undefined ? null : __workshopValue });
        } catch (e) {
          return JSON.stringify({ ok: false, error: String((e && e.message) || e) });
        }
        """
        let world: WKContentWorld = isolated ? PageUserScripts.world : .page
        webView.callAsyncJavaScript(body, arguments: [:], in: nil, in: world) { [weak self] result in
            switch result {
            case .success(let value):
                done((value as? String) ?? Self.failure("the script returned nothing"))
            case .failure(let error):
                if self?.crashed == true {
                    done(Self.failure(Self.crashedMessage))
                    return
                }
                let info = (error as NSError).userInfo
                let message = (info["WKJavaScriptExceptionMessage"] as? String) ?? error.localizedDescription
                done(Self.failure(message))
            }
        }
    }

    /// Whether a run's `world` option ("page", "isolated" or none) asks for
    /// the isolated world.
    static func isolated(_ world: String?) throws -> Bool {
        switch world {
        case nil, "page": return false
        case "isolated": return true
        default: throw PageError.fail("A script's world is page or isolated")
        }
    }

    /// A message a user script posted, handed on as a `pageMessage` event.
    /// A body that is not a JSON value, or is larger than
    /// `messageLimit` once encoded, is dropped and counted; the count rides
    /// on the page's later messages as `dropped`.
    func receive(_ message: WKScriptMessage) {
        let wrapped: [Any] = [message.body]
        guard JSONSerialization.isValidJSONObject(wrapped),
              let data = try? JSONSerialization.data(withJSONObject: wrapped),
              data.count <= Self.messageLimit + 2
        else {
            droppedMessages += 1
            return
        }
        var event: [String: Any?] = [
            "id": id,
            "world": message.world === WKContentWorld.page ? "page" : "isolated",
            "frameMain": message.frameInfo.isMainFrame,
            "frameUrl": message.frameInfo.request.url?.absoluteString ?? "",
            "body": message.body,
        ]
        if droppedMessages > 0 { event["dropped"] = droppedMessages }
        emit("pageMessage", event)
    }

    nonisolated static let messageLimit = 256 * 1024

    /// A JPEG of the page, base64; `nil` and a message when WebKit cannot draw it.
    func snapshot(done: @escaping (String?, String?) -> Void) {
        webView.takeSnapshot(with: WKSnapshotConfiguration()) { image, error in
            if let data = image.flatMap(Self.jpeg) {
                done(data.base64EncodedString(), nil)
            } else {
                done(nil, error?.localizedDescription ?? "the page could not be drawn")
            }
        }
    }

    #if canImport(UIKit)
    private static func jpeg(_ image: UIImage) -> Data? {
        image.jpegData(compressionQuality: 0.8)
    }
    #else
    private static func jpeg(_ image: NSImage) -> Data? {
        guard let tiff = image.tiffRepresentation, let bitmap = NSBitmapImageRep(data: tiff) else { return nil }
        return bitmap.representation(using: .jpeg, properties: [.compressionFactor: 0.8])
    }
    #endif

    nonisolated static func failure(_ message: String) -> String {
        let object: [String: Any] = ["ok": false, "error": message]
        guard let data = try? JSONSerialization.data(withJSONObject: object),
              let json = String(data: data, encoding: .utf8)
        else { return "{\"ok\":false,\"error\":\"script failed\"}" }
        return json
    }
}
