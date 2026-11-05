import AppKit

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private var shell: ShellWindow?
    private var updates: UpdateWatch?

    func applicationDidFinishLaunching(_ notification: Notification) {
        followSystemTextSettings()
        NSApp.mainMenu = MainMenu.build()
        guard let url = Settings.workshopURL else {
            let alert = NSAlert()
            alert.messageText = "This build has no Workshop address"
            alert.informativeText = "Rebuild it with scripts/macos-build.sh."
            alert.runModal()
            NSApp.terminate(nil)
            return
        }
        let shell = ShellWindow(url: url)
        self.shell = shell
        shell.showWindow(nil)
        updates = UpdateWatch { [weak shell] in shell?.offerRelaunch() }
        NSApp.activate(ignoringOtherApps: true)
    }

    /// Spelling correction and text replacement follow the Mac's Keyboard
    /// settings. WebKit reads these two keys from the app's own defaults
    /// ahead of the system's, so a value left there would override them.
    private func followSystemTextSettings() {
        let defaults = UserDefaults.standard
        defaults.removeObject(forKey: "WebAutomaticSpellingCorrectionEnabled")
        defaults.removeObject(forKey: "WebAutomaticTextReplacementEnabled")
    }

    /// Closing the window hides it; the Dock icon brings it back as it was.
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !flag { shell?.showWindow(nil) }
        return true
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }

    @objc func reloadWorkshop(_ sender: Any?) {
        shell?.reloadWorkshop()
    }

    @objc func findInPage(_ sender: Any?) {
        shell?.findInPage()
    }

    @objc func toggleBeside(_ sender: Any?) {
        shell?.toggleBeside()
    }

    @objc func relaunch(_ sender: Any?) {
        UpdateWatch.relaunch()
    }

    @objc func showWorkshop(_ sender: Any?) {
        shell?.showWindow(nil)
    }
}

/// Values the build writes into Info.plist.
enum Settings {
    static var workshopURL: URL? {
        guard let text = Bundle.main.object(forInfoDictionaryKey: "WorkshopURL") as? String,
              !text.isEmpty
        else { return nil }
        return URL(string: text)
    }

    static var build: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? ""
    }
}
