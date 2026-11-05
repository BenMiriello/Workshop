import ActivityKit
import Foundation

// The one Live Activity every app shares (contract:
// shared/native-live-activity.ts). Compiled into the app target, which starts
// and updates activities, and into the WorkshopWidgets extension, which lays
// them out, so both see the exact same type.
@available(iOS 16.2, *)
public struct WorkshopActivityAttributes: ActivityAttributes {
    public struct ContentState: Codable, Hashable {
        public var headline: String
        public var subtitle: String?
        public var detail: String?
        public var countdownTo: Date?
        public var progress: Double?
        /// One of the kit's five status kinds.
        public var status: String
        /// An SF Symbol name.
        public var symbol: String

        public init(
            headline: String,
            subtitle: String?,
            detail: String?,
            countdownTo: Date?,
            progress: Double?,
            status: String,
            symbol: String
        ) {
            self.headline = headline
            self.subtitle = subtitle
            self.detail = detail
            self.countdownTo = countdownTo
            self.progress = progress
            self.status = status
            self.symbol = symbol
        }
    }

    /// The slug of the app that started the activity: its owner, and the app
    /// a tap opens.
    public var app: String
    /// The shell's own deep link to that app, read from the app target's URL
    /// scheme (the widget extension has none of its own).
    public var link: URL?

    public init(app: String, link: URL?) {
        self.app = app
        self.link = link
    }
}
