import Foundation

enum ObservationDate {
    static func label(_ date: Date, timezone: String) -> String {
        format(date, timezone: timezone, pattern: "yyyy-MM-dd")
    }

    static func date(_ value: String, timezone: String) -> Date? {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(identifier: timezone) ?? .current
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter.date(from: value)
    }

    static func timeLabel(_ date: Date, timezone: String) -> String {
        format(date, timezone: timezone, pattern: "HH:mm")
    }

    private static func format(_ date: Date, timezone: String, pattern: String) -> String {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(identifier: timezone) ?? .current
        formatter.dateFormat = pattern
        return formatter.string(from: date)
    }

    /// Tolerates both plain and fractional-second ISO8601 stamps: engine task
    /// events carry `.300Z` while some receipts use whole seconds.
    static func parseISO(_ value: String) -> Date? {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: value) { return date }
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.date(from: value)
    }
}

/// UX #4：历史/同步时间戳统一本地化展示（本机时区，"MM-dd HH:mm"）。
/// 解析失败时原样返回，不吞掉信息；原始 UTC 串由调用方放进 help 提示。
func historyTimeText(_ iso: String, timeZone: TimeZone = .current) -> String {
    guard let date = ObservationDate.parseISO(iso) else { return iso }
    let formatter = DateFormatter()
    formatter.calendar = Calendar(identifier: .gregorian)
    formatter.locale = Locale(identifier: "zh_CN")
    formatter.timeZone = timeZone
    formatter.dateFormat = "MM-dd HH:mm"
    return formatter.string(from: date)
}

struct ObservationMonth {
    let calendar: Calendar
    let start: Date

    init(containing date: Date, timezone: String) {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: timezone) ?? .current
        calendar.firstWeekday = 2
        calendar.locale = Locale(identifier: L10n.effective == .english ? "en_US" : "zh_CN")
        self.calendar = calendar
        self.start = calendar.dateInterval(of: .month, for: date)!.start
    }

    var title: String {
        let formatter = DateFormatter()
        formatter.calendar = calendar
        formatter.locale = calendar.locale
        formatter.timeZone = calendar.timeZone
        formatter.dateFormat = L10n.effective == .english ? "LLLL yyyy" : "yyyy年M月"
        return formatter.string(from: start)
    }

    var weekdaySymbols: [String] {
        let formatter = DateFormatter()
        formatter.calendar = calendar
        formatter.locale = calendar.locale
        let symbols = formatter.shortStandaloneWeekdaySymbols ?? formatter.shortWeekdaySymbols ?? []
        let offset = max(0, calendar.firstWeekday - 1)
        return Array(symbols.dropFirst(offset)) + Array(symbols.prefix(offset))
    }

    var days: [Date] {
        let offset = (calendar.component(.weekday, from: start) - calendar.firstWeekday + 7) % 7
        let first = calendar.date(byAdding: .day, value: -offset, to: start)!
        return (0..<42).compactMap {
            let day = calendar.date(byAdding: .day, value: $0, to: first)!
            return calendar.date(bySettingHour: 12, minute: 0, second: 0, of: day)
        }
    }

    func moved(_ months: Int) -> Date {
        calendar.date(byAdding: .month, value: months, to: start)!
    }
}
