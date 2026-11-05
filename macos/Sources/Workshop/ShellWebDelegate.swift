import AppKit
import UniformTypeIdentifiers
import WebKit

/// Navigation and UI for the shell's web view. Workshop's own origin stays in
/// the window; links to other sites open in the default browser. AppKit shows
/// nothing for file inputs, JavaScript dialogs or downloads unless the app
/// does, so this does.
@MainActor
final class ShellWebDelegate: NSObject, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
    private let home: URL

    init(home: URL) {
        self.home = home
    }

    private func isWorkshop(_ url: URL?) -> Bool {
        guard let url else { return false }
        if ["about", "data", "blob"].contains(url.scheme?.lowercased() ?? "") { return true }
        return url.host == home.host && url.port == home.port && url.scheme == home.scheme
    }

    // MARK: WKNavigationDelegate

    func webView(
        _ webView: WKWebView,
        decidePolicyFor navigationAction: WKNavigationAction,
        preferences: WKWebpagePreferences
    ) async -> (WKNavigationActionPolicy, WKWebpagePreferences) {
        if navigationAction.shouldPerformDownload { return (.download, preferences) }
        let url = navigationAction.request.url
        let topLevel = navigationAction.targetFrame?.isMainFrame ?? true
        if topLevel, !isWorkshop(url), let url {
            NSWorkspace.shared.open(url)
            return (.cancel, preferences)
        }
        return (.allow, preferences)
    }

    func webView(
        _ webView: WKWebView,
        decidePolicyFor navigationResponse: WKNavigationResponse
    ) async -> WKNavigationResponsePolicy {
        navigationResponse.canShowMIMEType ? .allow : .download
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
        download.delegate = self
    }

    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) {
        download.delegate = self
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        webView.reload()
    }

    // MARK: WKUIDelegate

    /// window.open and target=_blank: Workshop pages load in the window,
    /// anything else goes to the default browser.
    func webView(
        _ webView: WKWebView,
        createWebViewWith configuration: WKWebViewConfiguration,
        for navigationAction: WKNavigationAction,
        windowFeatures: WKWindowFeatures
    ) -> WKWebView? {
        guard let url = navigationAction.request.url else { return nil }
        if isWorkshop(url) {
            webView.load(navigationAction.request)
        } else {
            NSWorkspace.shared.open(url)
        }
        return nil
    }

    /// Workshop's own pages get the microphone and camera without WebKit asking
    /// again on every page load: the system's permission, asked once for the
    /// app, is the only question. Other sites are asked as WebKit would.
    func webView(
        _ webView: WKWebView,
        requestMediaCapturePermissionFor origin: WKSecurityOrigin,
        initiatedByFrame frame: WKFrameInfo,
        type: WKMediaCaptureType
    ) async -> WKPermissionDecision {
        let own = origin.protocol == home.scheme && origin.host == home.host && origin.port == (home.port ?? 0)
        return own ? .grant : .prompt
    }

    func webView(
        _ webView: WKWebView,
        runOpenPanelWith parameters: WKOpenPanelParameters,
        initiatedByFrame frame: WKFrameInfo
    ) async -> [URL]? {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.canChooseDirectories = parameters.allowsDirectories
        panel.canChooseFiles = true
        panel.showsHiddenFiles = true
        guard let window = webView.window else {
            return panel.runModal() == .OK ? panel.urls : nil
        }
        let response = await panel.beginSheetModal(for: window)
        return response == .OK ? panel.urls : nil
    }

    func webView(
        _ webView: WKWebView,
        runJavaScriptAlertPanelWithMessage message: String,
        initiatedByFrame frame: WKFrameInfo
    ) async {
        _ = await Dialogs.show(message, site: frame.request.url, buttons: ["OK"], in: webView.window)
    }

    func webView(
        _ webView: WKWebView,
        runJavaScriptConfirmPanelWithMessage message: String,
        initiatedByFrame frame: WKFrameInfo
    ) async -> Bool {
        await Dialogs.show(message, site: frame.request.url, buttons: ["OK", "Cancel"], in: webView.window) == 0
    }

    func webView(
        _ webView: WKWebView,
        runJavaScriptTextInputPanelWithPrompt prompt: String,
        defaultText: String?,
        initiatedByFrame frame: WKFrameInfo
    ) async -> String? {
        await Dialogs.prompt(prompt, defaultText: defaultText, site: frame.request.url, in: webView.window)
    }

    func webView(
        _ webView: WKWebView,
        decideMediaCapturePermissionsFor origin: WKSecurityOrigin,
        initiatedBy frame: WKFrameInfo,
        type: WKMediaCaptureType
    ) async -> WKPermissionDecision {
        origin.host == home.host ? .grant : .prompt
    }

    // MARK: WKDownloadDelegate

    /// Saves to Downloads under the suggested name, numbered when taken.
    func download(
        _ download: WKDownload,
        decideDestinationUsing response: URLResponse,
        suggestedFilename: String
    ) async -> URL? {
        let folder = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask)[0]
        let name = suggestedFilename.isEmpty ? "download" : suggestedFilename
        var target = folder.appendingPathComponent(name)
        let base = target.deletingPathExtension().lastPathComponent
        let ext = target.pathExtension
        var n = 2
        while FileManager.default.fileExists(atPath: target.path) {
            let numbered = ext.isEmpty ? "\(base) \(n)" : "\(base) \(n).\(ext)"
            target = folder.appendingPathComponent(numbered)
            n += 1
        }
        DownloadLog.note("saving \(response.url?.absoluteString ?? "?") to \(target.path)")
        return target
    }

    func downloadDidFinish(_ download: WKDownload) {
        DownloadLog.note("saved \(download.originalRequest?.url?.absoluteString ?? "?")")
        NSSound(named: "Glass")?.play()
    }

    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        let ns = error as NSError
        let under = (ns.userInfo[NSUnderlyingErrorKey] as? NSError).map { " under \($0.domain) \($0.code) \($0.localizedDescription)" } ?? ""
        DownloadLog.note("failed \(download.originalRequest?.url?.absoluteString ?? "?"): \(ns.domain) \(ns.code) \(ns.localizedDescription)\(under)")
        let alert = NSAlert(error: error)
        alert.messageText = "The download failed"
        alert.informativeText = "\(ns.localizedDescription) (\(ns.domain) \(ns.code))"
        alert.runModal()
    }
}

/// Each save to Downloads and why one failed, one line each, in a per-user
/// file under /tmp so a failure can be read from another account on the Mac.
enum DownloadLog {
    static let path = "/tmp/workshop-downloads-\(NSUserName()).log"

    static func note(_ line: String) {
        let stamped = "\(ISO8601DateFormatter().string(from: Date())) \(line)\n"
        guard let data = stamped.data(using: .utf8) else { return }
        if let handle = FileHandle(forWritingAtPath: path) {
            handle.seekToEndOfFile()
            handle.write(data)
            try? handle.close()
        } else {
            FileManager.default.createFile(atPath: path, contents: data,
                                           attributes: [.posixPermissions: 0o644])
        }
    }
}
