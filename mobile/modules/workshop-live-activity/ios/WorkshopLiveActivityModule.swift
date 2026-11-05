import ActivityKit
import ExpoModulesCore
import Foundation

// Local-update Live Activity control, in the APP target. The state arrives
// checked by native/liveActivity.ts, which also decides who may update or end
// an activity from `list()`. No push tokens here: push-driven updates are the
// deferred paid feature.

public class WorkshopLiveActivityModule: Module {
    public func definition() -> ModuleDefinition {
        Name("WorkshopLiveActivity")

        Function("isSupported") { () -> Bool in
            if #available(iOS 16.2, *) {
                return ActivityAuthorizationInfo().areActivitiesEnabled
            }
            return false
        }

        Function("list") { () -> [[String: String]] in
            guard #available(iOS 16.2, *) else { return [] }
            return Activity<WorkshopActivityAttributes>.activities.map {
                ["id": $0.id, "app": $0.attributes.app]
            }
        }

        AsyncFunction("start") { (app: String, state: [String: Any]) throws -> String in
            guard #available(iOS 16.2, *) else {
                throw Exception(name: "Unsupported", description: "Live Activities require iOS 16.2+")
            }
            guard ActivityAuthorizationInfo().areActivitiesEnabled else {
                throw Exception(name: "Disabled", description: "Live Activities are disabled in Settings")
            }
            let attributes = WorkshopActivityAttributes(app: app, link: Self.link(to: app))
            let content = ActivityContent(state: Self.contentState(from: state), staleDate: nil)
            let activity = try Activity.request(attributes: attributes, content: content, pushType: nil)
            return activity.id
        }

        AsyncFunction("update") { (id: String, state: [String: Any]) in
            guard #available(iOS 16.2, *) else { return }
            let content = ActivityContent(state: Self.contentState(from: state), staleDate: nil)
            for activity in Activity<WorkshopActivityAttributes>.activities where activity.id == id {
                await activity.update(content)
            }
        }

        AsyncFunction("end") { (id: String) in
            guard #available(iOS 16.2, *) else { return }
            for activity in Activity<WorkshopActivityAttributes>.activities where activity.id == id {
                await activity.end(nil, dismissalPolicy: .immediate)
            }
        }
    }

    /// `<scheme>://?open=<app>`, which the shell's index screen turns into the
    /// app's page in Workshop.
    private static func link(to app: String) -> URL? {
        let types = Bundle.main.object(forInfoDictionaryKey: "CFBundleURLTypes") as? [[String: Any]]
        guard let scheme = (types?.first?["CFBundleURLSchemes"] as? [String])?.first else { return nil }
        var parts = URLComponents()
        parts.scheme = scheme
        parts.host = ""
        parts.queryItems = [URLQueryItem(name: "open", value: app)]
        return parts.url
    }

    private static func date(_ value: Any?) -> Date? {
        guard let text = value as? String else { return nil }
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = iso.date(from: text) { return date }
        iso.formatOptions = [.withInternetDateTime]
        return iso.date(from: text)
    }

    @available(iOS 16.2, *)
    private static func contentState(from state: [String: Any]) -> WorkshopActivityAttributes.ContentState {
        WorkshopActivityAttributes.ContentState(
            headline: (state["headline"] as? String) ?? "",
            subtitle: state["subtitle"] as? String,
            detail: state["detail"] as? String,
            countdownTo: date(state["countdownTo"]),
            progress: (state["progress"] as? NSNumber)?.doubleValue,
            status: (state["status"] as? String) ?? "running",
            symbol: (state["symbol"] as? String) ?? "app.badge"
        )
    }
}
