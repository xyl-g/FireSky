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
}

struct ObservationMonth {
    let calendar: Calendar
    let start: Date

    init(containing date: Date, timezone: String) {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: timezone) ?? .current
        calendar.firstWeekday = 2
        self.calendar = calendar
        self.start = calendar.dateInterval(of: .month, for: date)!.start
    }

    var title: String {
        "\(calendar.component(.year, from: start))年\(calendar.component(.month, from: start))月"
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
