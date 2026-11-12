import Foundation

/// Builds and validates immutable profile queries against one published result.
/// The descriptor is copied from the Rust result; no new forecast or download
/// is started when a profile is selected.
struct ProfileQueryRequest: Sendable {
    let resultID: String
    let sourceBytes: Int64
    let sourceSHA256: String
    let model: String
    let lat: Double
    let lon: Double
    let validTimeUTC: String
    let cycleUTC: String

    var params: [String: Any] {
        [
            "result_id": resultID,
            "source_identity": ["bytes": sourceBytes, "sha256": sourceSHA256],
            "model_source": model,
            "lat": lat,
            "lon": lon,
            "valid_time_utc": validTimeUTC,
        ]
    }
}

enum ProfileQueryBuilder {
    static func pointRequest(report: [String: Any], model: String,
                             lat: Double, lon: Double, localTime: String) -> ProfileQueryRequest? {
        let observer = (report["cross_section"] as? [String: Any])?["observer"] as? [String: Any]
        if let observer {
            guard let frozenLat = number(observer["lat"]),
                  let frozenLon = number(observer["lon"]) else { return nil }
            return request(report: report, model: model, lat: frozenLat, lon: frozenLon,
                           localTime: localTime)
        }
        return request(report: report, model: model, lat: lat, lon: lon, localTime: localTime)
    }

    static func request(report: [String: Any], model: String,
                        lat: Double, lon: Double, localTime: String) -> ProfileQueryRequest? {
        guard ["gfs", "ec", "satellite"].contains(model),
              lat.isFinite, (-90...90).contains(lat),
              lon.isFinite, (-180...180).contains(lon),
              let source = report["profile_source"] as? [String: Any],
              source["contract"] as? String == "rust-desktop-profile-source-v1",
              source["available"] as? Bool == true,
              source["query_method"] as? String == "profile_query",
              source["model_source"] as? String == model,
              source["profile_kind"] as? String == (model == "satellite"
                  ? "satellite-step-solar-path-diagnostic" : "model-pressure-cross-section"),
              let cycle = source["cycle_utc"] as? String,
              let cycleDate = utcDate(cycle),
              let resultID = source["result_id"] as? String,
              isHex64(resultID),
              let identity = source["source_identity"] as? [String: Any],
              let bytes = integer(identity["bytes"]), bytes >= 0,
              let sha = identity["sha256"] as? String, isHex64(sha),
              let validDate = utcDate(localTime) else {
            return nil
        }
        return ProfileQueryRequest(
            resultID: resultID,
            sourceBytes: bytes,
            sourceSHA256: sha,
            model: model,
            lat: lat,
            lon: lon,
            validTimeUTC: formatUTC(validDate, source: localTime),
            cycleUTC: formatUTC(cycleDate, source: cycle)
        )
    }

    static func responseMatches(_ response: [String: Any],
                                request: ProfileQueryRequest) -> Bool {
        guard response["contract"] as? String == "rust-desktop-profile-query-v1",
              response["result_id"] as? String == request.resultID,
              response["model_source"] as? String == request.model,
              let lat = number(response["lat"]),
              let lon = number(response["lon"]),
              abs(lat - request.lat) <= 0.000001,
              abs(lon - request.lon) <= 0.000001,
              let identity = response["source_identity"] as? [String: Any],
              integer(identity["bytes"]) == request.sourceBytes,
              identity["sha256"] as? String == request.sourceSHA256,
              let responseTime = response["valid_time_utc"] as? String,
              let expectedTime = utcDate(request.validTimeUTC),
              let actualTime = utcDate(responseTime),
              abs(actualTime.timeIntervalSince(expectedTime)) <= 0.000001 else {
            return false
        }
        return true
    }

    static func sameUTC(_ lhs: String, _ rhs: String) -> Bool {
        guard let left = utcDate(lhs), let right = utcDate(rhs) else { return false }
        return abs(left.timeIntervalSince(right)) <= 0.000001
    }

    private static func number(_ value: Any?) -> Double? {
        if let value = value as? NSNumber, value.doubleValue.isFinite { return value.doubleValue }
        if let value = value as? Double, value.isFinite { return value }
        if let value = value as? Int { return Double(value) }
        return nil
    }

    private static func integer(_ value: Any?) -> Int64? {
        if let value = value as? NSNumber {
            let integer = value.int64Value
            return value.doubleValue == Double(integer) ? integer : nil
        }
        if let value = value as? Int { return Int64(value) }
        if let value = value as? Int64 { return value }
        return nil
    }

    private static func isHex64(_ value: String) -> Bool {
        value.count == 64 && value.unicodeScalars.allSatisfy {
            ($0.value >= 48 && $0.value <= 57) || ($0.value >= 97 && $0.value <= 102)
        }
    }

    private static func utcDate(_ value: String) -> Date? {
        let internet = ISO8601DateFormatter()
        internet.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = internet.date(from: value) { return date }
        internet.formatOptions = [.withInternetDateTime]
        return internet.date(from: value)
    }

    private static func formatUTC(_ value: Date, source: String) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.formatOptions = [.withInternetDateTime]
        let seconds = formatter.string(from: Date(timeIntervalSince1970: floor(value.timeIntervalSince1970)))
        // Foundation's fractional formatter emits milliseconds; keep the exact
        // source fraction so Rust can validate its microsecond event grid.
        let parts = source.split(separator: ".", maxSplits: 1)
        let fraction = parts.count == 2 ? String(parts[1].prefix(while: { $0.isNumber })) : "000"
        return String(seconds.dropLast()) + "." + fraction + "Z"
    }

}
