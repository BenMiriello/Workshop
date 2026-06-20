import SwiftUI
import WidgetKit

// Entry point for the Widget Extension target. Deployment target is iOS 16.2
// (set on the target), so the Live Activity is always available here.
@main
struct WorkshopWidgetsBundle: WidgetBundle {
    var body: some Widget {
        CommuteLiveActivity()
    }
}
