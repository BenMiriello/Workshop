import ActivityKit
import SwiftUI
import WidgetKit

// The commute Live Activity UI. The countdown uses SwiftUI's native timer text,
// so it advances on screen WITHOUT any further updates from the app — we only
// push a new ContentState when the underlying plan changes.
//
// NOTE: this file lives in the Widget Extension target. The shared type
// CommuteActivityAttributes comes from the module file of the same name — add
// that file to this target's membership in Xcode.

@available(iOS 16.2, *)
struct CommuteLiveActivity: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: CommuteActivityAttributes.self) { context in
            LockScreenView(context: context)
                .padding(14)
                .activityBackgroundTint(Color.black.opacity(0.55))
                .activitySystemActionForegroundColor(Color.white)
        } dynamicIsland: { context in
            DynamicIsland {
                DynamicIslandExpandedRegion(.leading) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(context.state.headline).font(.headline)
                        Text(context.state.subtitle).font(.caption2).foregroundStyle(.secondary)
                    }
                }
                DynamicIslandExpandedRegion(.trailing) {
                    CountdownText(to: context.state.countdownTo)
                        .font(.title2.monospacedDigit())
                        .frame(width: 78)
                }
                DynamicIslandExpandedRegion(.bottom) {
                    Text("Leave by \(context.state.leaveByLocal) · arrive \(context.state.targetArriveLocal)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            } compactLeading: {
                Image(systemName: "tram.fill")
            } compactTrailing: {
                CountdownText(to: context.state.countdownTo)
                    .monospacedDigit()
                    .frame(width: 46)
            } minimal: {
                Image(systemName: "tram.fill")
            }
        }
    }
}

@available(iOS 16.2, *)
private struct LockScreenView: View {
    let context: ActivityViewContext<CommuteActivityAttributes>

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            Image(systemName: "tram.fill")
                .font(.title2)
            VStack(alignment: .leading, spacing: 2) {
                Text(context.state.headline).font(.headline)
                if !context.state.subtitle.isEmpty {
                    Text(context.state.subtitle).font(.caption).foregroundStyle(.secondary)
                }
                Text("Leave by \(context.state.leaveByLocal)")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 2) {
                CountdownText(to: context.state.countdownTo)
                    .font(.title.monospacedDigit())
                Text("until leave").font(.caption2).foregroundStyle(.secondary)
            }
        }
    }
}

@available(iOS 16.2, *)
private struct CountdownText: View {
    let to: Date
    var body: some View {
        // Native live countdown; clamps at the target.
        Text(timerInterval: Date()...max(to, Date().addingTimeInterval(1)), countsDown: true)
    }
}
