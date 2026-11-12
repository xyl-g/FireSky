import Foundation
import CoreGraphics

enum MapZoom {
    static func rect(_ current: CGRect, anchor: CGPoint, delta: Double,
                     precise: Bool, worldSize: Double) -> CGRect? {
        guard current.width.isFinite, current.height.isFinite,
              current.width > 0, current.height > 0,
              current.minX.isFinite, current.minY.isFinite,
              anchor.x.isFinite, anchor.y.isFinite, delta.isFinite,
              worldSize.isFinite, worldSize >= 256, delta != 0 else { return nil }
        let movement = min(1.2, max(-1.2, delta * (precise ? 0.008 : 0.18)))
        let lower = 256 / min(current.width, current.height)
        let upper = worldSize / max(current.width, current.height)
        let scale = min(upper, max(lower, exp(-movement)))
        let width = current.width * scale, height = current.height * scale
        // Anchor the geographic point under the pointer, not the map's centre.
        let x = anchor.x - (anchor.x - current.minX) * scale
        let y = min(worldSize - height, max(0, anchor.y - (anchor.y - current.minY) * scale))
        return CGRect(x: x, y: y, width: width, height: height)
    }
}
