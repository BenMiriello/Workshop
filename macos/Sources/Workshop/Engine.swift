import Foundation
import WebKit

/// The shell's web engine as the bridge reports it (`WorkshopNative.engine`,
/// shared/native-bridge.ts): the installed Safari's version and WebKit's own
/// build, each left out when it cannot be read.
enum Engine {
    static let global = "__WORKSHOP_NATIVE_ENGINE__"

    static var facts: [String: String] {
        var facts: [String: String] = [:]
        let safari = URL(fileURLWithPath: "/Applications/Safari.app/Contents/Info.plist")
        if let version = NSDictionary(contentsOf: safari)?["CFBundleShortVersionString"] as? String {
            facts["safariVersion"] = version
        }
        if let build = Bundle(for: WKWebView.self).object(forInfoDictionaryKey: "CFBundleVersion") as? String {
            facts["webkitVersion"] = build
        }
        return facts
    }

    /// Sets the global the bridge client reads `engine` from as it installs.
    static var script: String {
        guard let data = try? JSONSerialization.data(withJSONObject: facts),
              let json = String(data: data, encoding: .utf8)
        else { return "" }
        return "window.\(global)=\(json);"
    }
}
