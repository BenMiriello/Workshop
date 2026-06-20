import ActivityKit
import ExpoModulesCore
import Foundation

// Local-update Live Activity control. Runs in the APP target: starts/updates/
// ends the commute activity via ActivityKit. No push tokens here — push-driven
// updates are the deferred paid feature.

public class WorkshopLiveActivityModule: Module {
    public func definition() -> ModuleDefinition {
        Name("WorkshopLiveActivity")

        Function("isSupported") { () -> Bool in
            if #available(iOS 16.2, *) {
                return ActivityAuthorizationInfo().areActivitiesEnabled
            }
            return false
        }

        AsyncFunction("start") { (state: [String: Any]) throws -> String in
            guard #available(iOS 16.2, *) else {
                throw Exception(name: "Unsupported", description: "Live Activities require iOS 16.2+")
            }
            guard ActivityAuthorizationInfo().areActivitiesEnabled else {
                throw Exception(name: "Disabled", description: "Live Activities are disabled in Settings")
            }
            let attributes = CommuteActivityAttributes(
                direction: (state["direction"] as? String) ?? "to-work"
            )
            let content = ActivityContent(
                state: Self.contentState(from: state),
                staleDate: nil
            )
            let activity = try Activity.request(
                attributes: attributes,
                content: content,
                pushType: nil
            )
            return activity.id
        }

        AsyncFunction("update") { (id: String, state: [String: Any]) in
            guard #available(iOS 16.2, *) else { return }
            let content = ActivityContent(state: Self.contentState(from: state), staleDate: nil)
            for activity in Activity<CommuteActivityAttributes>.activities where activity.id == id {
                await activity.update(content)
            }
        }

        AsyncFunction("end") { (id: String) in
            guard #available(iOS 16.2, *) else { return }
            for activity in Activity<CommuteActivityAttributes>.activities where activity.id == id {
                await activity.end(nil, dismissalPolicy: .immediate)
            }
        }
    }

    @available(iOS 16.2, *)
    private static func contentState(from state: [String: Any]) -> CommuteActivityAttributes.ContentState {
        let iso = ISO8601DateFormatter()
        let countdown = (state["countdownTo"] as? String).flatMap { iso.date(from: $0) } ?? Date()
        return CommuteActivityAttributes.ContentState(
            headline: (state["headline"] as? String) ?? "",
            subtitle: (state["subtitle"] as? String) ?? "",
            leaveByLocal: (state["leaveByLocal"] as? String) ?? "",
            targetArriveLocal: (state["targetArriveLocal"] as? String) ?? "",
            countdownTo: countdown,
            status: (state["status"] as? String) ?? "info"
        )
    }
}
