import ActivityKit
import SwiftUI
import WidgetKit

// The layout of the one Live Activity every app shares, built only from its
// generic fields: symbol and status colour, headline, subtitle and detail
// lines, a countdown and a progress bar, each shown when the app set it. The
// countdown is SwiftUI's own timer text, so it advances with no further
// updates. A tap opens the app that started the activity.
//
// This file is in the WorkshopWidgets extension target, with
// modules/workshop-live-activity/ios/WorkshopActivityAttributes.swift added to
// the target's membership too.

@available(iOS 16.2, *)
struct WorkshopActivityWidget: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: WorkshopActivityAttributes.self) { context in
            LockScreenView(state: context.state)
                .padding(14)
                .activityBackgroundTint(Color.black.opacity(0.55))
                .activitySystemActionForegroundColor(Color.white)
                .widgetURL(context.attributes.link)
        } dynamicIsland: { context in
            let state = context.state
            return DynamicIsland {
                DynamicIslandExpandedRegion(.leading) {
                    Lines(state: state, detail: false)
                }
                DynamicIslandExpandedRegion(.trailing) {
                    if let to = state.countdownTo {
                        CountdownText(to: to)
                            .font(.title2.monospacedDigit())
                            .frame(width: 78)
                    } else if let progress = state.progress {
                        Text(progress, format: .percent.precision(.fractionLength(0)))
                            .font(.title2.monospacedDigit())
                    }
                }
                DynamicIslandExpandedRegion(.bottom) {
                    VStack(alignment: .leading, spacing: 6) {
                        if let detail = state.detail {
                            Text(detail).font(.caption).foregroundStyle(.secondary)
                        }
                        ProgressBar(state: state)
                    }
                }
            } compactLeading: {
                Symbol(state: state)
            } compactTrailing: {
                if let to = state.countdownTo {
                    CountdownText(to: to)
                        .monospacedDigit()
                        .frame(width: 46)
                } else if let progress = state.progress {
                    ProgressView(value: progress)
                        .progressViewStyle(.circular)
                        .tint(StatusTone.color(state.status))
                }
            } minimal: {
                Symbol(state: state)
            }
            .widgetURL(context.attributes.link)
        }
    }
}

@available(iOS 16.2, *)
private struct LockScreenView: View {
    let state: WorkshopActivityAttributes.ContentState

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .center, spacing: 12) {
                Symbol(state: state).font(.title2)
                Lines(state: state, detail: true)
                Spacer()
                if let to = state.countdownTo {
                    CountdownText(to: to)
                        .font(.title.monospacedDigit())
                        .multilineTextAlignment(.trailing)
                }
            }
            ProgressBar(state: state)
        }
    }
}

@available(iOS 16.2, *)
private struct Lines: View {
    let state: WorkshopActivityAttributes.ContentState
    let detail: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(state.headline).font(.headline).lineLimit(1)
            if let subtitle = state.subtitle {
                Text(subtitle).font(.caption).foregroundStyle(.secondary).lineLimit(1)
            }
            if detail, let line = state.detail {
                Text(line).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
            }
        }
    }
}

@available(iOS 16.2, *)
private struct Symbol: View {
    let state: WorkshopActivityAttributes.ContentState

    var body: some View {
        let name = UIImage(systemName: state.symbol) == nil ? "app.badge" : state.symbol
        Image(systemName: name).foregroundStyle(StatusTone.color(state.status))
    }
}

@available(iOS 16.2, *)
private struct ProgressBar: View {
    let state: WorkshopActivityAttributes.ContentState

    var body: some View {
        if let progress = state.progress {
            ProgressView(value: progress).tint(StatusTone.color(state.status))
        }
    }
}

private struct CountdownText: View {
    let to: Date
    var body: some View {
        Text(timerInterval: Date()...max(to, Date().addingTimeInterval(1)), countsDown: true)
    }
}

/// The kit's five status kinds as system colours, which the lock screen and
/// the Dynamic Island keep legible in either appearance.
enum StatusTone {
    static func color(_ status: String) -> Color {
        switch status {
        case "ok": return .green
        case "warn": return .yellow
        case "danger": return .red
        case "stopped": return .gray
        default: return .cyan
        }
    }
}
