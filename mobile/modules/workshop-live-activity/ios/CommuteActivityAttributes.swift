import ActivityKit
import Foundation

// Shared between the app target (this module starts/updates the activity) and
// the WidgetKit extension target (which renders it). When adding the widget
// extension in Xcode, add THIS file to the widget target's membership too, so
// both compile the exact same type.
@available(iOS 16.2, *)
public struct CommuteActivityAttributes: ActivityAttributes {
    public struct ContentState: Codable, Hashable {
        public var headline: String
        public var subtitle: String
        public var leaveByLocal: String
        public var targetArriveLocal: String
        public var countdownTo: Date
        public var status: String

        public init(
            headline: String,
            subtitle: String,
            leaveByLocal: String,
            targetArriveLocal: String,
            countdownTo: Date,
            status: String
        ) {
            self.headline = headline
            self.subtitle = subtitle
            self.leaveByLocal = leaveByLocal
            self.targetArriveLocal = targetArriveLocal
            self.countdownTo = countdownTo
            self.status = status
        }
    }

    public var direction: String

    public init(direction: String) {
        self.direction = direction
    }
}
