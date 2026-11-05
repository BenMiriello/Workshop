import AppKit

/// Notices when a newer build is waiting and calls `onUpdate` once, so the
/// window can offer a relaunch. While this app runs, a build waits beside it
/// as `.next.app` and goes in place once the app quits (scripts/lib/
/// macos-build-mac.sh): the running bundle is never touched, since an ad hoc
/// app whose files change under it loses the microphone.
@MainActor
final class UpdateWatch {
    private let launched = Settings.build
    private var timer: Timer?

    init(onUpdate: @escaping @MainActor () -> Void) {
        timer = Timer.scheduledTimer(withTimeInterval: 20, repeats: true) { [weak self] timer in
            MainActor.assumeIsolated {
                guard let self, let onDisk = Self.buildOnDisk(), onDisk != self.launched else { return }
                timer.invalidate()
                onUpdate()
            }
        }
    }

    private static var waiting: URL {
        Bundle.main.bundleURL.deletingLastPathComponent().appendingPathComponent(".next.app")
    }

    private static func buildOnDisk() -> String? {
        for app in [waiting, Bundle.main.bundleURL] {
            let plist = app.appendingPathComponent("Contents/Info.plist")
            if let build = NSDictionary(contentsOf: plist)?["CFBundleVersion"] as? String { return build }
        }
        return nil
    }

    /// Opens a fresh copy of the app from the same path once this one has quit
    /// and a waiting build has gone in (given up on after 15 seconds).
    static func relaunch() {
        let path = Bundle.main.bundleURL.path
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/bin/sh")
        task.arguments = ["-c", "while /bin/kill -0 \(ProcessInfo.processInfo.processIdentifier) 2>/dev/null; do sleep 0.2; done; "
            + "n=0; while [ -d \"$1\" ] && [ $n -lt 75 ]; do sleep 0.2; n=$((n+1)); done; /usr/bin/open \"$0\"",
            path, waiting.path]
        try? task.run()
        NSApp.terminate(nil)
    }
}
