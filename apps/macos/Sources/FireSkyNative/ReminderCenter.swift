import AppKit
import Foundation
import UserNotifications

/// 基于已发布结果快照的本地提醒。只负责系统通知授权与调度，
/// 不重新计算任何观测指数、最佳时刻或推荐窗口。
@MainActor
final class ReminderCenter: ObservableObject {
    enum Permission: String {
        case unknown
        case notDetermined
        case authorized
        case denied
    }

    struct Scheduled: Codable {
        let identifier: String
        let key: String
        let title: String
        let body: String
        let fireDate: Date
        let minutesBefore: Int
        let timeZoneIdentifier: String
    }

    @Published private(set) var permission: Permission = .unknown
    @Published private(set) var scheduled: Scheduled?

    private let center: UNUserNotificationCenter?
    private let defaults = UserDefaults.standard
    private let storageKey = "firesky.reminder.current"

    init() {
        // UNUserNotificationCenter 要求真实 .app bundle；裸测试可执行文件只保留状态显示，
        // 不伪造系统授权或通知注册。
        center = Bundle.main.bundleURL.pathExtension == "app" ? .current() : nil
        let env = ProcessInfo.processInfo.environment
        if let override = env["FIRESKY_TEST_REMINDER_PERMISSION"].flatMap(Permission.init(rawValue:)) {
            permission = override
        } else if center != nil {
            refreshPermission()
        } else {
            permission = .denied
        }
        if env["FIRESKY_TEST_REMINDER_SCHEDULED"] == "1" {
            scheduled = Scheduled(identifier: "firesky.test.scheduled",
                                  key: "ui-fixture",
                                  title: "FireSky 观测提醒（界面测试）",
                                  body: "界面测试提醒，不代表真实通知。",
                                  fireDate: Date().addingTimeInterval(30 * 60),
                                  minutesBefore: 30,
                                  timeZoneIdentifier: "Asia/Shanghai")
        } else {
            loadScheduled()
        }
    }

    func refreshPermission() {
        guard let center else { return }
        center.getNotificationSettings { [weak self] settings in
            let value: Permission
            switch settings.authorizationStatus {
            case .authorized, .provisional, .ephemeral:
                value = .authorized
            case .denied:
                value = .denied
            case .notDetermined:
                value = .notDetermined
            @unknown default:
                value = .denied
            }
            DispatchQueue.main.async { self?.permission = value }
        }
    }

    func requestAuthorization(completion: @escaping (Bool) -> Void) {
        guard let center else {
            completion(false)
            return
        }
        center.requestAuthorization(options: [.alert, .sound]) { [weak self] granted, error in
            DispatchQueue.main.async {
                self?.permission = granted && error == nil ? .authorized : .denied
                completion(granted && error == nil)
            }
        }
    }

    /// 调度一条一次性提醒；调用方负责提供已经来自 Rust 报告的最佳时刻。
    func schedule(key: String, title: String, body: String, fireDate: Date,
                  minutesBefore: Int, timeZone: TimeZone,
                  completion: @escaping (Bool, String?) -> Void) {
        guard let center, permission == .authorized else {
            completion(false, center == nil ? "当前运行环境不支持系统通知。" : "尚未获得通知权限。")
            return
        }
        let triggerDate = fireDate.addingTimeInterval(-Double(minutesBefore * 60))
        let interval = triggerDate.timeIntervalSinceNow
        guard interval >= 60 else {
            completion(false, "该结果的最佳时刻已过或不足一小时，无法设置提醒。")
            return
        }

        let identifier = "firesky.reminder.\(UUID().uuidString)"
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        content.sound = .default
        let request = UNNotificationRequest(
            identifier: identifier,
            content: content,
            trigger: UNTimeIntervalNotificationTrigger(timeInterval: interval, repeats: false))

        // 同一结果重复设置时先移除旧请求，避免通知中心出现两条。
        if let existing = scheduled?.identifier {
            center.removePendingNotificationRequests(withIdentifiers: [existing])
            center.removeDeliveredNotifications(withIdentifiers: [existing])
        }

        center.add(request) { [weak self] error in
            DispatchQueue.main.async {
                guard let self, error == nil else {
                    completion(false, error?.localizedDescription ?? "通知注册失败。")
                    return
                }
                let item = Scheduled(identifier: identifier, key: key, title: title, body: body,
                                     fireDate: triggerDate, minutesBefore: minutesBefore,
                                     timeZoneIdentifier: timeZone.identifier)
                self.scheduled = item
                self.persist(item)
                completion(true, nil)
            }
        }
    }

    /// 仅供快照夹具构建「已设置提醒」状态；生产路径无对应环境变量时为空操作。
    func adoptTestScheduled(key: String) {
        guard ProcessInfo.processInfo.environment["FIRESKY_TEST_REMINDER_SCHEDULED"] == "1" else { return }
        scheduled = Scheduled(identifier: "firesky.test.scheduled",
                              key: key,
                              title: "FireSky 观测提醒（界面测试）",
                              body: "界面测试提醒，不代表真实通知。",
                              fireDate: Date().addingTimeInterval(30 * 60),
                              minutesBefore: 30,
                              timeZoneIdentifier: "Asia/Shanghai")
    }

    func cancel() {
        guard let scheduled else { return }
        center?.removePendingNotificationRequests(withIdentifiers: [scheduled.identifier])
        center?.removeDeliveredNotifications(withIdentifiers: [scheduled.identifier])
        self.scheduled = nil
        defaults.removeObject(forKey: storageKey)
    }

    func openSystemSettings() {
        guard let url = URL(string: "x-apple.systempreferences:com.apple.Notifications-Settings.extension") else { return }
        NSWorkspace.shared.open(url)
    }

    /// 即时投递高分火烧云预警横幅通知（由后台同步完成后的 engine alert_rules 触发）。
    func deliverImmediate(id: String, title: String, body: String) {
        guard let center, permission == .authorized else { return }
        let safeID = id.replacingOccurrences(of: "|", with: "_")
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        content.sound = .default
        let request = UNNotificationRequest(
            identifier: "firesky.alert.\(safeID)",
            content: content,
            trigger: nil
        )
        center.add(request) { _ in }
    }

    /// 后台同步助手完成/失败通知：由主应用以自身 bundle 身份投递系统横幅，点击可打开 FireSky。
    func deliverSyncNotification(id: String, title: String, body: String) {
        guard let center else { return }
        let safeID = String(id.map { (":| \t\n".contains($0)) ? "_" : $0 })
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        content.sound = .default
        content.userInfo = ["kind": "sync"]
        let request = UNNotificationRequest(
            identifier: "firesky.sync.\(safeID)",
            content: content,
            trigger: nil
        )
        if permission == .authorized {
            center.add(request) { _ in }
        } else if permission == .notDetermined {
            requestAuthorization { granted in
                guard granted else { return }
                center.add(request) { _ in }
            }
        }
    }

    private func persist(_ item: Scheduled) {
        guard let data = try? JSONEncoder().encode(item) else { return }
        defaults.set(data, forKey: storageKey)
    }

    private func loadScheduled() {
        guard let center else { return }
        let key = storageKey
        guard let data = defaults.data(forKey: key),
              let item = try? JSONDecoder().decode(Scheduled.self, from: data) else { return }
        center.getPendingNotificationRequests { [weak self] requests in
            let stillPending = requests.contains { $0.identifier == item.identifier }
            DispatchQueue.main.async {
                self?.defaults.removeObject(forKey: key)
                self?.scheduled = stillPending ? item : nil
                if !stillPending { return }
                self?.persist(item)
            }
        }
    }
}
