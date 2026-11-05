import Foundation

/// The zoom factors a page view accepts.
enum PageZoom {
    static let range: ClosedRange<Double> = 0.3...3.0

    /// `factor` brought into `range`; a number that is not finite is refused.
    static func clamped(_ factor: Double) throws -> Double {
        guard factor.isFinite else { throw PageError.fail("Zoom must be a number") }
        return min(max(factor, range.lowerBound), range.upperBound)
    }
}
