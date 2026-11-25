import Foundation
import SwiftUI
import UIKit

struct GridCoordinate: Hashable, Sendable {
    let lat: Double
    let lon: Double

    init?(lat: Double, lon: Double) {
        guard lat.isFinite, lon.isFinite, (-90...90).contains(lat), (-180...180).contains(lon) else { return nil }
        self.lat = (lat * 1_000_000).rounded() / 1_000_000
        self.lon = (lon * 1_000_000).rounded() / 1_000_000
    }

    var label: String { String(format: "%.2f°, %.2f°", lat, lon) }
}

enum RegionalPalette {
    static let steps = 51

    static func color(_ index: Int) -> Color {
        fill(Double(index) / Double(steps - 1) * 2.5).swiftUIColor
    }

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
            let alpha = t <= 0.001 ? 24 : Int((60 + 185 * sqrt(t)).rounded())
            return RegionalFill(red: rgb[0], green: rgb[1], blue: rgb[2], alpha: alpha)
        }
        return RegionalFill(red: 201, green: 60, blue: 145, alpha: 245)
    }
}

struct RegionalFill: Hashable {
    let red: Int
    let green: Int
    let blue: Int
    let alpha: Int

    var uiColor: UIColor {
        UIColor(red: CGFloat(red) / 255, green: CGFloat(green) / 255,
                blue: CGFloat(blue) / 255, alpha: CGFloat(alpha) / 255)
    }

    var swiftUIColor: Color {
        Color(red: Double(red) / 255, green: Double(green) / 255,
              blue: Double(blue) / 255, opacity: Double(alpha) / 255)
    }
}

struct RegionalCell: Identifiable, Sendable {
    let coordinate: GridCoordinate
    let name: String
    let quality: Double?
    let level: String
    let bestTime: String?
    let error: String?
    let peakQuality: Double?
    let peakLevel: String?
    let eventQuality: Double?
    let confidenceScore: Double?
    let pressureFamily: String?
    let cloudCoverageEffective: Double?
    let durationMinutes: Double?
    let qualityAod: Double?
    let incomingAdjustment: Double?
    let observerAdjustment: Double?
    let highCloud: Double?
    let midCloud: Double?
    let lowCloud: Double?
    let timezone: String?

    var id: GridCoordinate { coordinate }
    var label: String { name.isEmpty ? coordinate.label : name }

    init(coordinate: GridCoordinate, name: String, quality: Double?, level: String,
         bestTime: String?, error: String?,
         peakQuality: Double? = nil, peakLevel: String? = nil, eventQuality: Double? = nil,
         confidenceScore: Double? = nil, pressureFamily: String? = nil,
         cloudCoverageEffective: Double? = nil, durationMinutes: Double? = nil,
         qualityAod: Double? = nil, incomingAdjustment: Double? = nil,
         observerAdjustment: Double? = nil,
         highCloud: Double? = nil, midCloud: Double? = nil, lowCloud: Double? = nil,
         timezone: String? = nil) {
        self.coordinate = coordinate
        self.name = name
        self.quality = quality
        self.level = level
        self.bestTime = bestTime
        self.error = error
        self.peakQuality = peakQuality
        self.peakLevel = peakLevel
        self.eventQuality = eventQuality
        self.confidenceScore = confidenceScore
        self.pressureFamily = pressureFamily
        self.cloudCoverageEffective = cloudCoverageEffective
        self.durationMinutes = durationMinutes
        self.qualityAod = qualityAod
        self.incomingAdjustment = incomingAdjustment
        self.observerAdjustment = observerAdjustment
        self.highCloud = highCloud
        self.midCloud = midCloud
        self.lowCloud = lowCloud
        self.timezone = timezone
    }

    init(coordinate: GridCoordinate, row: [String: Any], quality: Double?, level: String,
         bestTime: String?, error: String?) {
        let components = row["quality_components"] as? [String: Any] ?? [:]
        self.init(
            coordinate: coordinate, name: row["name"] as? String ?? "",
            quality: quality, level: level, bestTime: bestTime, error: error,
            peakQuality: Self.number(row["peak_quality_index"]),
            peakLevel: row["peak_quality_level"] as? String,
            eventQuality: Self.number(row["quality_index"]),
            confidenceScore: Self.number(row["confidence_score"]),
            pressureFamily: row["dominant_burnable_pressure_family"] as? String,
            cloudCoverageEffective: Self.number(components["burnable_cloud_coverage_effective"]),
            durationMinutes: Self.number(components["duration_minutes"] ?? row["duration_minutes"]),
            qualityAod: Self.number(components["quality_aod550"]),
            incomingAdjustment: Self.number(components["incoming_aod_adjustment"]),
            observerAdjustment: Self.number(components["observer_aod_adjustment"]),
            highCloud: Self.number(row["high_cloud"]),
            midCloud: Self.number(row["mid_cloud"]),
            lowCloud: Self.number(row["low_cloud"]),
            timezone: row["timezone_name"] as? String
        )
    }

    private static func number(_ value: Any?) -> Double? {
        guard let number = value as? NSNumber, number.doubleValue.isFinite else { return nil }
        return number.doubleValue
    }
}

struct RegionalModel: Sendable {
    let available: Bool
    let error: String?
    let cells: [RegionalCell]
    let ranked: [RegionalCell]
    let cityRanking: [RegionalCell]
    let cityRankingStatus: String
    let cityRankingReason: String?
    let byCoordinate: [GridCoordinate: RegionalCell]
    private let hitBuckets: [GridCoordinate: [GridCoordinate]]
    let missingCount: Int
    let highCount: Int
    let cycle: String?
    let precision: String?
    let pointSpacingDeg: Double
    let regionCount: Int
    let pointCount: Int
    let successCount: Int
    let errorCount: Int
    let prefetchSeconds: Double?
    let nativeTimingsMS: [String: Double]
    let cacheStatus: String?
    let cacheGeneratedAt: String?

    var validCount: Int { ranked.count }
    var peak: RegionalCell? { ranked.first }
    var status: String { error != nil ? "未完成" : available ? "预测完成" : "等待预测" }

    init(report: [String: Any]?, error: String? = nil) {
        self.error = error
        available = report != nil && error == nil
        cycle = available ? report?["cycle_time_utc"] as? String : nil
        if available, let precisionObject = report?["precision"] as? [String: Any] {
            let pointSpacing = (precisionObject["point_spacing_deg"] as? NSNumber)?.doubleValue
            let modelSpacing = (precisionObject["model_grid_spacing_deg"] as? NSNumber)?.doubleValue
            self.pointSpacingDeg = pointSpacing ?? 0.25
            if let pointSpacing, let modelSpacing {
                precision = String(format: "点 %.2f° · 模型 %.2f°", pointSpacing, modelSpacing)
            } else if let pointSpacing {
                precision = String(format: "点 %.2f°", pointSpacing)
            } else {
                precision = nil
            }
        } else {
            self.pointSpacingDeg = 0.25
            precision = nil
        }
        regionCount = available ? (report?["regions"] as? [Any])?.count ?? 0 : 0
        pointCount = available ? (report?["point_count"] as? NSNumber)?.intValue ?? 0 : 0
        successCount = available ? (report?["success_count"] as? NSNumber)?.intValue ?? 0 : 0
        errorCount = available ? (report?["error_count"] as? NSNumber)?.intValue ?? 0 : 0
        prefetchSeconds = available ? ((report?["timing"] as? [String: Any])?["prefetch_seconds"] as? NSNumber)?.doubleValue : nil
        let timings = available ? (report?["timing"] as? [String: Any])?["native_timings_ms"] as? [String: Any] : nil
        nativeTimingsMS = (timings ?? [:]).compactMapValues(Self.number)
        let cache = available ? (report?["cache"] as? [String: Any]) : nil
        cacheStatus = cache?["status"] as? String
        cacheGeneratedAt = cache?["generated_at_utc"] as? String
        let cityReport = report?["city_ranking"] as? [String: Any]
        cityRankingStatus = cityReport?["status"] as? String ?? "unavailable"
        cityRankingReason = cityReport?["reason"] as? String

        var index: [GridCoordinate: RegionalCell] = [:]
        var cities: [RegionalCell] = []
        if available {
            let rows = (report?["points"] as? [[String: Any]])
                ?? (report?["cells"] as? [[String: Any]])
                ?? []
            for row in rows {
                guard let coordinate = Self.coordinate(row) else { continue }
                let value = Self.number(row["peak_quality_index"] ?? row["quality_index"])
                let quality = value.flatMap { (0...2.5).contains($0) ? $0 : nil }
                index[coordinate] = RegionalCell(
                    coordinate: coordinate, row: row, quality: quality,
                    level: row["peak_quality_level"] as? String ?? row["quality_level"] as? String ?? "—",
                    bestTime: row["best_time_local"] as? String, error: quality == nil ? "缺测" : nil
                )
            }
            for row in report?["errors"] as? [[String: Any]] ?? [] {
                guard let coordinate = Self.coordinate(row) else { continue }
                index[coordinate] = RegionalCell(
                    coordinate: coordinate, name: row["name"] as? String ?? "",
                    quality: nil, level: "缺测", bestTime: nil, error: row["error"] as? String ?? "该网格未完成"
                )
            }
            for row in cityReport?["items"] as? [[String: Any]] ?? [] {
                guard let coordinate = Self.coordinate(row) else { continue }
                let value = Self.number(row["peak_quality_index"] ?? row["quality_index"])
                let quality = value.flatMap { (0...2.5).contains($0) ? $0 : nil }
                cities.append(RegionalCell(
                    coordinate: coordinate, row: row, quality: quality,
                    level: row["peak_quality_level"] as? String ?? row["quality_level"] as? String ?? "—",
                    bestTime: row["best_time_local"] as? String,
                    error: quality == nil ? (row["error"] as? String ?? "缺测") : nil
                ))
            }
        }
        byCoordinate = index
        cityRanking = cities.enumerated().sorted { left, right in
            if left.element.quality == right.element.quality {
                return left.offset < right.offset
            }
            return (left.element.quality ?? -1) > (right.element.quality ?? -1)
        }.map(\.element)

        var buckets: [GridCoordinate: [GridCoordinate]] = [:]
        for coordinate in index.keys {
            if let key = GridCoordinate(lat: (coordinate.lat * 4).rounded() / 4,
                                        lon: (coordinate.lon * 4).rounded() / 4) {
                buckets[key, default: []].append(coordinate)
            }
            let boundaryAlias: Double?
            if coordinate.lon > 179.5 {
                boundaryAlias = -180
            } else if coordinate.lon < -179.5 {
                boundaryAlias = 180
            } else {
                boundaryAlias = nil
            }
            if let boundaryAlias,
               let key = GridCoordinate(lat: (coordinate.lat * 4).rounded() / 4,
                                        lon: boundaryAlias) {
                buckets[key, default: []].append(coordinate)
            }
        }
        hitBuckets = buckets
        cells = index.values.sorted {
            $0.coordinate.lat == $1.coordinate.lat ? $0.coordinate.lon < $1.coordinate.lon : $0.coordinate.lat > $1.coordinate.lat
        }
        ranked = cells.filter { $0.quality != nil }.sorted {
            if $0.quality == $1.quality {
                return $0.coordinate.lat == $1.coordinate.lat ? $0.coordinate.lon < $1.coordinate.lon : $0.coordinate.lat > $1.coordinate.lat
            }
            return $0.quality! > $1.quality!
        }
        highCount = ranked.filter { $0.quality! >= 1.5 }.count
        let reportedCount = available ? (report?["point_count"] as? Int ?? cells.count) : 0
        let errors = available ? (report?["error_count"] as? Int ?? 0) : 0
        missingCount = max(cells.count - ranked.count, reportedCount - ranked.count, errors)
    }

    func cell(at coordinate: GridCoordinate) -> RegionalCell? { byCoordinate[coordinate] }

    func hit(lat: Double, lon: Double, maxDistanceDeg: Double? = nil) -> GridCoordinate? {
        guard lat.isFinite, lon.isFinite else { return nil }
        let tolerance = maxDistanceDeg ?? max(0.125, pointSpacingDeg * 0.75)
        if tolerance <= 0.35 {
            let y = (lat * 4).rounded() / 4, x = (lon * 4).rounded() / 4
            var nearest: GridCoordinate?
            var distance = Double.infinity
            for dy in -1...1 {
                for dx in -1...1 {
                    guard let key = GridCoordinate(lat: y + Double(dy) * 0.25, lon: x + Double(dx) * 0.25) else { continue }
                    for candidate in hitBuckets[key] ?? [] {
                        let a = abs(candidate.lat - lat)
                        let directLongitudeDistance = abs(candidate.lon - lon)
                        let b = min(directLongitudeDistance, 360 - directLongitudeDistance)
                        if a <= tolerance, b <= tolerance, a * a + b * b < distance {
                            distance = a * a + b * b
                            nearest = candidate
                        }
                    }
                }
            }
            if let nearest { return nearest }
        }
        var nearest: GridCoordinate?
        var bestDistance = Double.infinity
        for cell in cells {
            let a = abs(cell.coordinate.lat - lat)
            let directLon = abs(cell.coordinate.lon - lon)
            let b = min(directLon, 360 - directLon)
            if a <= tolerance, b <= tolerance, a * a + b * b < bestDistance {
                bestDistance = a * a + b * b
                nearest = cell.coordinate
            }
        }
        return nearest
    }

    private static func coordinate(_ row: [String: Any]) -> GridCoordinate? {
        guard let lat = number(row["lat"]), let lon = number(row["lon"]) else { return nil }
        return GridCoordinate(lat: lat, lon: lon)
    }

    private static func number(_ value: Any?) -> Double? {
        guard let number = value as? NSNumber, number.doubleValue.isFinite else { return nil }
        return number.doubleValue
    }
}

struct RegionalForecast: Sendable {
    let models: [String: RegionalModel]

    init(result: [String: Any]?) {
        let reports = result?["model_results"] as? [String: Any] ?? [:]
        let errors = result?["model_errors"] as? [String: Any] ?? [:]
        models = Dictionary(uniqueKeysWithValues: ["gfs", "ec"].map { model in
            (model, RegionalModel(report: reports[model] as? [String: Any], error: errors[model] as? String))
        })
    }

    func model(_ name: String) -> RegionalModel { models[name] ?? RegionalModel(report: nil) }
}

struct GridBounds: Sendable {
    let west: Double
    let south: Double
    let east: Double
    let north: Double

    var longitudeSpan: Double { east >= west ? east - west : east + 360 - west }
    var columns: Int { Int((longitudeSpan * 4).rounded(.down)) + 1 }
    var rows: Int { Int(((north - south) * 4).rounded(.down)) + 1 }

    init?(_ area: [Double]) {
        guard area.count == 4, area.allSatisfy(\.isFinite),
              area[0] >= -180, area[2] <= 180, area[1] >= -90, area[3] <= 90,
              area[2] != area[0], area[3] > area[1] else { return nil }
        let longitudeSpan = area[2] >= area[0] ? area[2] - area[0] : area[2] + 360 - area[0]
        guard longitudeSpan > 0 else { return nil }
        west = area[0]; south = area[1]; east = area[2]; north = area[3]
    }
}
