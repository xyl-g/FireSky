import AppKit
import MapKit

enum RegionalPalette {
    static let steps = 51
    static func color(_ index: Int) -> NSColor {
        fill(Double(index) / Double(steps - 1) * 2.5).color
    }

    // Matches cloud-render.ts burnColor and BurnOutlookMap.svelte fillForScore.
    static func fill(_ quality: Double) -> RegionalFill {
        let t = min(1, max(0, quality / 2.5))
        let stops: [(Double, [Double])] = [
            (0, [253, 230, 220]), (0.2, [251, 176, 122]), (0.45, [245, 124, 102]),
            (0.7, [237, 91, 130]), (1, [201, 60, 145]),
        ]
        for index in 1..<stops.count where t <= stops[index].0 {
            let (leftAt, left) = stops[index - 1], (rightAt, right) = stops[index]
            let f = (t - leftAt) / (rightAt - leftAt)
            let rgb = zip(left, right).map { Int(($0 + ($1 - $0) * f).rounded()) }
            let alpha = t <= 0.001 ? 12 : Int((42 + 190 * sqrt(t)).rounded())
            return RegionalFill(red: rgb[0], green: rgb[1], blue: rgb[2], alpha: alpha)
        }
        return RegionalFill(red: 201, green: 60, blue: 145, alpha: 232)
    }
}

struct RegionalFill: Hashable {
    let red: Int
    let green: Int
    let blue: Int
    let alpha: Int
    var color: NSColor {
        NSColor(srgbRed: Double(red) / 255, green: Double(green) / 255,
                blue: Double(blue) / 255, alpha: Double(alpha) / 255)
    }
}

// Immutable MapKit geometry is built off the main thread, then installed as one snapshot.
struct RegionalMapLayers: @unchecked Sendable {
    enum Style { case value(RegionalFill), missing, outline, grid, smooth }
    let overlays: [MKOverlay]
    let styles: [ObjectIdentifier: Style]

    static func build(areas: [[Double]], cells: [RegionalCell], showGrid: Bool = false, smooth: Bool = false) throws -> RegionalMapLayers {
        var overlays: [MKOverlay] = []
        var styles: [ObjectIdentifier: Style] = [:]
        func append(_ overlay: MKOverlay, _ style: Style) {
            overlays.append(overlay)
            styles[ObjectIdentifier(overlay)] = style
        }
        var missing: [MKPolygon] = []
        if smooth, let smoothOverlay = try buildSmoothOverlay(cells: cells) {
            append(smoothOverlay, .smooth)
            for (index, cell) in cells.enumerated() where cell.quality == nil {
                if index % 256 == 0 { try Task.checkCancellation() }
                let c = cell.coordinate
                missing.append(polygon(west: c.lon - 0.125, south: c.lat - 0.125, east: c.lon + 0.125, north: c.lat + 0.125))
            }
        } else {
            var bins: [RegionalFill: [MKPolygon]] = [:]
            for (index, cell) in cells.enumerated() {
                if index % 256 == 0 { try Task.checkCancellation() }
                let c = cell.coordinate
                let shape = polygon(west: c.lon - 0.125, south: c.lat - 0.125, east: c.lon + 0.125, north: c.lat + 0.125)
                if let quality = cell.quality { bins[RegionalPalette.fill(quality), default: []].append(shape) }
                else { missing.append(shape) }
            }
            for (fill, polygons) in bins { append(MKMultiPolygon(polygons), .value(fill)) }
        }
        if !missing.isEmpty { append(MKMultiPolygon(missing), .missing) }
        var outlines: [MKPolygon] = []
        var lines: [MKPolyline] = []
        for area in areas {
            try Task.checkCancellation()
            guard let bounds = GridBounds(area) else { continue }
            let south = bounds.south - 0.125
            let north = south + Double(bounds.rows) * 0.25
            for (rangeWest, rangeEast) in bounds.longitudeRanges {
                let west = max(-180, rangeWest - 0.125)
                let east = min(180, rangeEast + 0.125)
                outlines.append(polygon(west: west, south: south, east: east, north: north))
                if showGrid {
                    // Include the two half-cell boundary lines around the
                    // segment, matching the ordinary-area grid contract.
                    let columns = Int(((rangeEast - rangeWest) * 4).rounded(.down)) + 1
                    for y in 0...bounds.rows {
                        let lat = south + Double(y) * 0.25
                        lines.append(line(lat, west, lat, east))
                    }
                    for x in 0...columns {
                        let lon = min(180, west + Double(x) * 0.25)
                        lines.append(line(south, lon, north, lon))
                    }
                }
            }
        }
        if !lines.isEmpty { append(MKMultiPolyline(lines), .grid) }
        // 覆盖边界常显（regional-10，方案 :78）：网格关闭且有数据时也保留外框描边。
        if !outlines.isEmpty { append(MKMultiPolygon(outlines), .outline) }
        return RegionalMapLayers(overlays: overlays, styles: styles)
    }

    /// Return a compact MapKit region for ordinary and wrapped areas. A
    /// naive min/max longitude fit turns 170E..170W into a 340-degree view;
    /// choosing the largest circular gap keeps the requested narrow arc.
    static func fitRegion(areas: [[Double]], paddingDegrees: Double = 0.25) -> MKCoordinateRegion? {
        let bounds = areas.compactMap(GridBounds.init)
        guard !bounds.isEmpty else { return nil }
        let south = bounds.map(\.south).min()! - paddingDegrees
        let north = bounds.map(\.north).max()! + paddingDegrees
        var segments = bounds.flatMap(\.longitudeRanges)
        segments.sort { $0.0 == $1.0 ? $0.1 < $1.1 : $0.0 < $1.0 }
        var merged: [(Double, Double)] = []
        for segment in segments {
            if let last = merged.last, segment.0 <= last.1 + 1e-9 {
                merged[merged.count - 1].1 = max(last.1, segment.1)
            } else {
                merged.append(segment)
            }
        }
        guard !merged.isEmpty else { return nil }
        let (centerLongitude, span): (Double, Double)
        if merged.count == 1 && merged[0].0 <= -180 + 1e-9 && merged[0].1 >= 180 - 1e-9 {
            centerLongitude = 0
            span = 360
        } else {
            var largestGap = -1.0
            var gapIndex = 0
            for index in merged.indices {
                let next = merged.index(after: index) == merged.endIndex
                    ? merged.startIndex : merged.index(after: index)
                let gap = next == merged.startIndex
                    ? merged[next].0 + 360 - merged[index].1
                    : merged[next].0 - merged[index].1
                if gap > largestGap {
                    largestGap = gap
                    gapIndex = index
                }
            }
            let startIndex = merged.index(after: gapIndex) == merged.endIndex
                ? merged.startIndex : merged.index(after: gapIndex)
            let start = merged[startIndex].0
            let rawEnd = merged[gapIndex].1
            let end = rawEnd < start ? rawEnd + 360 : rawEnd
            span = min(360, max(0.5, end - start + 2 * paddingDegrees))
            let midpoint = start + (end - start) / 2
            centerLongitude = midpoint > 180 ? midpoint - 360 : midpoint
        }
        return MKCoordinateRegion(
            center: CLLocationCoordinate2D(
                latitude: max(-85, min(85, (south + north) / 2)),
                longitude: centerLongitude
            ),
            span: MKCoordinateSpan(
                latitudeDelta: min(170, max(0.5, north - south)),
                longitudeDelta: span
            )
        )
    }

    /// Build a Web-Mercator-aligned bilinearly smoothed heatmap CGImage off the main thread.
    /// Missing cells stay strictly transparent (never fabricated as 0) and antimeridian wraps fall back to polygons.
    private static func buildSmoothOverlay(cells: [RegionalCell]) throws -> RegionalSmoothOverlay? {
        let validCells = cells.compactMap { cell -> (lat: Double, lon: Double, quality: Double)? in
            guard let quality = cell.quality, quality.isFinite else { return nil }
            return (cell.coordinate.lat, cell.coordinate.lon, quality)
        }
        guard !validCells.isEmpty else { return nil }
        var minLat = Double.infinity, maxLat = -Double.infinity
        var minLon = Double.infinity, maxLon = -Double.infinity
        for item in validCells {
            minLat = min(minLat, item.lat)
            maxLat = max(maxLat, item.lat)
            minLon = min(minLon, item.lon)
            maxLon = max(maxLon, item.lon)
        }
        let step = 0.25
        // Wrapped antimeridian grids fall back to discrete polygons.
        guard maxLon - minLon <= 350 else { return nil }
        let cols = Int(((maxLon - minLon) / step).rounded()) + 1
        let rows = Int(((maxLat - minLat) / step).rounded()) + 1
        guard cols >= 1, rows >= 1, cols * rows <= 400_000 else { return nil }

        var grid = [Double](repeating: .nan, count: rows * cols)
        for item in validCells {
            let col = Int(((item.lon - minLon) / step).rounded())
            let row = Int(((item.lat - minLat) / step).rounded())
            if col >= 0, col < cols, row >= 0, row < rows {
                grid[row * cols + col] = item.quality
            }
        }

        let west = max(-180.0, minLon - step / 2)
        let east = min(180.0, maxLon + step / 2)
        let south = max(-85.0, minLat - step / 2)
        let north = min(85.0, maxLat + step / 2)
        let topLeft = MKMapPoint(CLLocationCoordinate2D(latitude: north, longitude: west))
        let bottomRight = MKMapPoint(CLLocationCoordinate2D(latitude: south, longitude: east))
        let mapRect = MKMapRect(
            x: min(topLeft.x, bottomRight.x),
            y: min(topLeft.y, bottomRight.y),
            width: abs(bottomRight.x - topLeft.x),
            height: abs(bottomRight.y - topLeft.y)
        )
        guard mapRect.width > 0, mapRect.height > 0 else { return nil }

        let targetScale = 3
        let rawWidth = max(cols * targetScale, 32)
        let rawHeight = max(rows * targetScale, 32)
        let maxDim = 960
        let scaleDown = max(1.0, max(Double(rawWidth) / Double(maxDim), Double(rawHeight) / Double(maxDim)))
        let width = max(16, Int((Double(rawWidth) / scaleDown).rounded()))
        let height = max(16, Int((Double(rawHeight) / scaleDown).rounded()))

        var pixels = [UInt8](repeating: 0, count: width * height * 4)
        for py in 0..<height {
            if py % 32 == 0 { try Task.checkCancellation() }
            let fracY = (Double(py) + 0.5) / Double(height)
            let mercY = mapRect.minY + fracY * mapRect.height
            let lat = MKMapPoint(x: mapRect.midX, y: mercY).coordinate.latitude
            let rFloat = (lat - minLat) / step
            let rNearest = Int(rFloat.rounded())
            guard rNearest >= 0, rNearest < rows else { continue }
            let r0 = max(0, min(rows - 1, Int(floor(rFloat))))
            let r1 = max(0, min(rows - 1, r0 + 1))
            let ty = max(0, min(1, rFloat - Double(r0)))

            for px in 0..<width {
                let fracX = (Double(px) + 0.5) / Double(width)
                let lon = west + fracX * (east - west)
                let cFloat = (lon - minLon) / step
                let cNearest = Int(cFloat.rounded())
                guard cNearest >= 0, cNearest < cols else { continue }
                // Preserve missing-cell transparency strictly: never paint over a missing cell.
                guard !grid[rNearest * cols + cNearest].isNaN else { continue }

                let c0 = max(0, min(cols - 1, Int(floor(cFloat))))
                let c1 = max(0, min(cols - 1, c0 + 1))
                let tx = max(0, min(1, cFloat - Double(c0)))

                let samples: [(Double, Double)] = [
                    (grid[r0 * cols + c0], (1 - tx) * (1 - ty)),
                    (grid[r0 * cols + c1], tx * (1 - ty)),
                    (grid[r1 * cols + c0], (1 - tx) * ty),
                    (grid[r1 * cols + c1], tx * ty),
                ]
                var sum = 0.0
                var weight = 0.0
                for (value, w) in samples where !value.isNaN && w > 0 {
                    sum += value * w
                    weight += w
                }
                guard weight > 0 else { continue }
                let fill = RegionalPalette.fill(sum / weight)
                let idx = (py * width + px) * 4
                let a = Double(fill.alpha) / 255.0
                pixels[idx] = UInt8(min(255, max(0, Int((Double(fill.red) * a).rounded()))))
                pixels[idx + 1] = UInt8(min(255, max(0, Int((Double(fill.green) * a).rounded()))))
                pixels[idx + 2] = UInt8(min(255, max(0, Int((Double(fill.blue) * a).rounded()))))
                pixels[idx + 3] = UInt8(min(255, max(0, fill.alpha)))
            }
        }

        let colorSpace = CGColorSpace(name: CGColorSpace.sRGB) ?? CGColorSpaceCreateDeviceRGB()
        let bitmapInfo = CGBitmapInfo(rawValue: CGImageAlphaInfo.premultipliedLast.rawValue)
        guard let provider = CGDataProvider(data: Data(pixels) as CFData),
              let image = CGImage(
                width: width,
                height: height,
                bitsPerComponent: 8,
                bitsPerPixel: 32,
                bytesPerRow: width * 4,
                space: colorSpace,
                bitmapInfo: bitmapInfo,
                provider: provider,
                decode: nil,
                shouldInterpolate: true,
                intent: .defaultIntent
              ) else { return nil }
        return RegionalSmoothOverlay(image: image, boundingMapRect: mapRect)
    }

    static func polygon(west: Double, south: Double, east: Double, north: Double) -> MKPolygon {
        let coordinates = [coordinate(south, west), coordinate(south, east),
                           coordinate(north, east), coordinate(north, west)]
        return MKPolygon(coordinates: coordinates, count: coordinates.count)
    }
    private static func coordinate(_ lat: Double, _ lon: Double) -> CLLocationCoordinate2D {
        CLLocationCoordinate2D(latitude: min(85.05112878, max(-85.05112878, lat)), longitude: min(180, max(-180, lon)))
    }
    private static func line(_ a: Double, _ b: Double, _ c: Double, _ d: Double) -> MKPolyline {
        let points = [coordinate(a, b), coordinate(c, d)]
        return MKPolyline(coordinates: points, count: 2)
    }
}

final class RegionalSmoothOverlay: NSObject, MKOverlay {
    let image: CGImage
    let boundingMapRect: MKMapRect
    var coordinate: CLLocationCoordinate2D {
        MKMapPoint(x: boundingMapRect.midX, y: boundingMapRect.midY).coordinate
    }

    init(image: CGImage, boundingMapRect: MKMapRect) {
        self.image = image
        self.boundingMapRect = boundingMapRect
        super.init()
    }
}

final class RegionalSmoothRenderer: MKOverlayRenderer {
    override func draw(_ mapRect: MKMapRect, zoomScale: MKZoomScale, in context: CGContext) {
        guard let smooth = overlay as? RegionalSmoothOverlay else { return }
        let drawRect = rect(for: smooth.boundingMapRect)
        context.saveGState()
        context.interpolationQuality = .high
        // CGContext uses bottom-left origin in MapKit overlay rendering; flip Y so north stays at top.
        context.translateBy(x: 0, y: drawRect.minY + drawRect.maxY)
        context.scaleBy(x: 1, y: -1)
        context.draw(smooth.image, in: drawRect)
        context.restoreGState()
    }
}

final class RegionalGridRenderer: MKMultiPolylineRenderer {
    override func draw(_ mapRect: MKMapRect, zoomScale: MKZoomScale, in context: CGContext) {
        let cellWidth = MKMapRect.world.width / 360 * 0.25 * zoomScale
        guard cellWidth >= 6 else { return }
        super.draw(mapRect, zoomScale: zoomScale, in: context)
    }
}

final class RegionalFieldRenderer: MKMultiPolygonRenderer {
    var fillsCells = false

    override func draw(_ mapRect: MKMapRect, zoomScale: MKZoomScale, in context: CGContext) {
        context.saveGState()
        // Adjacent cells share an edge; antialiasing must not leave hairline seams.
        context.setShouldAntialias(false)
        if fillsCells, let fillColor {
            context.setFillColor(fillColor.cgColor)
            for cell in multiPolygon.polygons where cell.boundingMapRect.intersects(mapRect) {
                context.fill(rect(for: cell.boundingMapRect))
            }
        } else {
            super.draw(mapRect, zoomScale: zoomScale, in: context)
        }
        context.restoreGState()
    }
}

/// 采样格点标注（regional-6）：Web 采样圆点的原生对应，tooltip 为
/// 「档位 · 质量 · 云族 · 高/中/低云%」（BurnOutlookMap.svelte:391-396）。
/// 字段全部来自区域 points[] DTO；缺测格采样点用灰点，与地图缺测态一致。
final class RegionalSampleAnnotation: NSObject, MKAnnotation {
    let cell: RegionalCell
    let tooltip: String
    private let coordinateValue: CLLocationCoordinate2D

    var coordinate: CLLocationCoordinate2D { coordinateValue }

    init(cell: RegionalCell) {
        self.cell = cell
        self.coordinateValue = CLLocationCoordinate2D(latitude: cell.coordinate.lat, longitude: cell.coordinate.lon)
        let family: String
        switch cell.pressureFamily {
        case "high": family = "高云压力族"
        case "middle": family = "中云压力族"
        case "low": family = "低云压力族"
        default: family = "无"
        }
        let percent: (Double?) -> String = { value in
            guard let value else { return "—" }
            return "\(Int((value * 100).rounded()))"
        }
        let quality = cell.peakQuality.map { String(format: "%.3f", $0) } ?? "未计算"
        self.tooltip = "\(cell.peakLevel ?? "—") · \(quality)/2.5\n主导可烧压力族 \(family)"
            + "\n高/中/低云 \(percent(cell.highCloud))/\(percent(cell.midCloud))/\(percent(cell.lowCloud))%"
        super.init()
    }

    /// Web 采样圆点 fillOpacity 0.9；缺测（quality 缺失）用灰点。
    var markerColor: NSColor {
        guard let fill = cell.quality.map(RegionalPalette.fill) else { return NSColor(white: 0.5, alpha: 0.45) }
        return NSColor(srgbRed: Double(fill.red) / 255, green: Double(fill.green) / 255,
                       blue: Double(fill.blue) / 255, alpha: 0.9)
    }
}

final class RegionalSampleMarkerView: MKAnnotationView {
    override func draw(_ dirtyRect: NSRect) {
        guard let sample = annotation as? RegionalSampleAnnotation else { return }
        let path = NSBezierPath(ovalIn: bounds.insetBy(dx: 0.5, dy: 0.5))
        sample.markerColor.setFill()
        path.fill()
        path.lineWidth = 1
        sample.markerColor.setStroke()
        path.stroke()
    }
}
