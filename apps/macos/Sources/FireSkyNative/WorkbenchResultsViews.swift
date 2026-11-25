import AppKit
import Charts
import SwiftUI

/// 首次成功路径：不替代预检窗口，只把用户下一步要做的四件事放到可见位置。
private struct WorkFirstRunGuide: View {
    @EnvironmentObject private var store: Store
    @Environment(\.colorScheme) private var scheme

    private struct Step: View {
        @Environment(\.colorScheme) private var scheme
        let number: String
        let symbol: String
        let title: String
        let detail: String
        let action: String
        var disabled = false
        let run: () -> Void

        var body: some View {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 7) {
                    Image(systemName: symbol)
                        .fireSkyFont(size: 13, weight: .semibold, relativeTo: .caption)
                        .foregroundStyle(warm)
                    Text(number).fireSkyFont(size: 10, weight: .bold, relativeTo: .caption).monospacedDigit()
                        .foregroundStyle(.secondary)
                    Spacer(minLength: 0)
                }
                VStack(alignment: .leading, spacing: 3) {
                    Text(title).fireSkyFont(size: 12, weight: .semibold)
                    Text(detail).fireSkyFont(size: 10, relativeTo: .caption)
                        .foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 2)
                Button(action) { run() }
                    .buttonStyle(.borderless)
                    .fireSkyFont(size: 11, weight: .medium, relativeTo: .caption)
                    .disabled(disabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(10)
            .frame(minHeight: 116, alignment: .leading)
            .background(Color.primary.opacity(scheme == .dark ? 0.045 : 0.035),
                        in: RoundedRectangle(cornerRadius: 8))
            .overlay {
                RoundedRectangle(cornerRadius: 8)
                    .strokeBorder(Color.secondary.opacity(0.16), lineWidth: 1)
            }
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Image(systemName: "sparkles")
                    .fireSkyFont(size: 13, weight: .semibold, relativeTo: .caption).foregroundStyle(warm)
                Text(L10n.t("首次预测 · 四步完成")).fireSkyFont(size: 12, weight: .semibold)
                Spacer(minLength: 8)
                Text(L10n.t("每一步都可稍后调整"))
                    .fireSkyFont(size: 10, relativeTo: .caption).foregroundStyle(.secondary)
            }
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 158), spacing: 10)], spacing: 10) {
                Step(number: "01", symbol: "map", title: L10n.t("选好地点"),
                     detail: L10n.t("搜索、地图选点或选择收藏点"), action: L10n.t("打开地图")) {
                    store.showMapPicker = true
                }
                Step(number: "02", symbol: "location", title: L10n.t("确认位置"),
                     detail: L10n.t(store.metadataReady ? "海拔与时区已确认" : "获取并确认海拔与时区"),
                     action: L10n.t(store.metadataReady ? "调整坐标" : "编辑坐标")) {
                    store.editingCoordinates.toggle()
                }
                Step(number: "03", symbol: "key.horizontal", title: L10n.t("数据源"),
                     detail: L10n.t(store.camsConfigured ? "CAMS 已配置" : "CAMS 可选，仍可使用本地数据"),
                     action: L10n.t(store.camsConfigured ? "查看配置" : "配置 CAMS")) {
                    store.openCAMSSetup()
                }
                Step(number: "04", symbol: "arrow.down.circle", title: L10n.t("预检与下载"),
                     detail: L10n.t("逐项确认缺失数据，过程中可取消"), action: L10n.t("数据预检"),
                     disabled: store.busy || !store.online) {
                    store.beginCheck()
                }
            }
            Text(L10n.t("提示：首次下载量取决于本机缺失的数据集；预检会列出本次所需文件与状态，下载后可随时重新预检。"))
                .fireSkyFont(size: 10, relativeTo: .caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityLabel(L10n.t("首次下载量取决于本机缺失的数据集，预检会列出本次所需文件与状态。"))
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .fireSkySurface(role: .content, emphasized: true)
    }
}

/// 行动摘要（交接文档 P0-2）：只读取 Rust 报告字段，不在 SwiftUI 重算科学结果。
/// 双模式并列展示，不平均、不互相填补；缺测与单模式失败都有独立文案。
private struct WorkActionSummaryView: View {
    @EnvironmentObject private var store: Store
    @EnvironmentObject private var reminders: ReminderCenter
    let comparison: ForecastComparison

    private struct ModelSnapshot {
        let model: String
        let level: String
        let best: String
        let window: String
        let limit: String?
    }

    private var snapshots: [ModelSnapshot] {
        ForecastComparison.models.compactMap { model in
            guard let report = comparison.report(model) else { return nil }
            let level = report["peak_quality_level"] as? String
                ?? report["quality_level"] as? String
                ?? L10n.tf("指数 %@ / 2.5", format(comparison.peak(model), digits: 2))
            let rawLimit = (report["quality_components"] as? JSONObject)?["quality_limiting_factor"] as? String
            return ModelSnapshot(model: model,
                                 level: level,
                                 best: timeLabel(report["best_time_local"] as? String),
                                 window: windowLabel(report),
                                 limit: rawLimit?.isEmpty == true ? nil : rawLimit)
        }
    }

    private var failedModels: [String] {
        ForecastComparison.models.filter { comparison.error($0) != nil }
    }

    /// 提醒只读取已发布报告的原始最佳时刻；多模型存在时明确标注依据的模式。
    private var reminderBest: (model: String, time: String)? {
        for model in ForecastComparison.models {
            guard let report = comparison.report(model),
                  let time = report["best_time_local"] as? String, !time.isEmpty else { continue }
            return (model, time)
        }
        return nil
    }

    private var levelsAgree: Bool {
        snapshots.count == 2 && Set(snapshots.map(\.level)).count == 1
    }

    private var headline: String {
        if snapshots.isEmpty {
            return failedModels.isEmpty
                ? L10n.t("完成一次预测后，这里会给出是否值得外出、最佳时刻和主要限制。")
                : L10n.t("两个模式都未完成，暂不能给出观测建议。")
        }
        if snapshots.count == 1 {
            return L10n.tf("仅 %@ 可用：%@",
                           snapshots[0].model.uppercased(),
                           L10n.qualityLevel(snapshots[0].level))
        }
        return levelsAgree
            ? L10n.tf("两个模式结论一致：%@", L10n.qualityLevel(snapshots[0].level))
            : L10n.tf("两个模式结论不同：GFS %@ · EC %@",
                      L10n.qualityLevel(snapshots[0].level),
                      L10n.qualityLevel(snapshots[1].level))
    }

    private var timingText: String {
        guard let first = snapshots.first else { return "" }
        if levelsAgree, snapshots.allSatisfy({ $0.best == first.best && $0.window == first.window }) {
            return L10n.tf("最佳 %@ · 推荐窗口 %@", first.best, first.window)
        }
        return snapshots.map {
            L10n.tf("%@ 最佳 %@ · 窗口 %@", $0.model.uppercased(), $0.best, $0.window)
        }
            .joined(separator: "   ·   ")
    }

    private var notes: [String] {
        var result: [String] = []
        for model in failedModels {
            result.append(L10n.tf("%@ 未完成：%@",
                                  model.uppercased(),
                                  comparison.error(model) ?? L10n.t("未知原因")))
        }
        for snapshot in snapshots {
            if let limit = snapshot.limit {
                result.append(L10n.tf("%@ 主要限制：%@",
                                      snapshot.model.uppercased(),
                                      L10n.qualityLimit(limit)))
            }
        }
        if store.resultParametersStale {
            result.append(L10n.t("当前结果对应旧参数，可点「按新参数重算」更新。"))
        }
        return result
    }

    private var exportFilename: String { SummaryExport.suggestedFilename(store) }

    private var exportText: String { SummaryExport.summaryText(store: store, comparison: comparison) }

    private var statusSymbol: String {
        if snapshots.isEmpty { return failedModels.isEmpty ? "sparkles" : "exclamationmark.triangle" }
        return levelsAgree && failedModels.isEmpty ? "checkmark.circle.fill" : "exclamationmark.triangle"
    }

    var body: some View {
        Group {
            if snapshots.isEmpty && failedModels.isEmpty && comparison.result == nil {
                WorkFirstRunGuide()
            } else {
                VStack(alignment: .leading, spacing: 8) {
                    HStack(spacing: 8) {
                        Image(systemName: statusSymbol)
                            .fireSkyFont(size: 13, weight: .semibold, relativeTo: .caption)
                            .foregroundStyle(warm)
                        Text(L10n.t("行动摘要"))
                            .fireSkyFont(size: 11, weight: .semibold, relativeTo: .caption)
                            .foregroundStyle(.secondary)
                        Spacer(minLength: 8)
                        if !snapshots.isEmpty {
                            Menu {
                                Button(L10n.text("保存为文本文件", "Save as text")) {
                                    SummaryExport.save(exportText, suggestedFilename: exportFilename)
                                }
                                Button(L10n.text("复制摘要", "Copy summary")) {
                                    SummaryExport.copy(exportText)
                                }
                            } label: {
                                Label(L10n.text("导出摘要", "Export summary"), systemImage: "square.and.arrow.up")
                            }
                            .buttonStyle(.borderless)
                            .font(.system(size: 11))
                            .help(L10n.text("导出地点、日期、模型状态、来源时效与缺测说明；不重算科学结论。",
                                            "Export location, date, model status, source timeliness and missing-data notes without recomputing results."))
                            Button {
                                store.starLocation()
                            } label: {
                                Label(L10n.t("收藏该地点"), systemImage: "star")
                            }
                            .buttonStyle(.borderless)
                            .fireSkyFont(size: 11, relativeTo: .caption)
                            .disabled(!store.metadataReady)
                            .help(L10n.t(store.metadataReady
                                         ? "把当前观测点加入收藏"
                                         : "请先确认海拔与时区，再收藏该地点"))
                        }
                    }
                    Text(headline)
                        .fireSkyFont(size: 15, weight: .semibold, relativeTo: .title3)
                        .foregroundStyle(.primary)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityLabel(L10n.tf("结论：%@", headline))
                    if !timingText.isEmpty {
                        Text(timingText)
                            .fireSkyFont(size: 12).monospacedDigit()
                            .foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                            .accessibilityLabel(L10n.tf("时间与窗口：%@", timingText))
                    }
                    if let reminderBest {
                        WorkReminderControl(bestTime: reminderBest.time,
                                            model: reminderBest.model,
                                            detail: timingText)
                    }
                    ForEach(notes, id: \.self) { note in
                        Text(note)
                            .fireSkyFont(size: 11, relativeTo: .caption)
                            .foregroundStyle(.orange)
                            .fixedSize(horizontal: false, vertical: true)
                            .accessibilityLabel(note)
                    }
                }
                .padding(14)
                .frame(maxWidth: .infinity, alignment: .leading)
                .fireSkySurface(role: .content, emphasized: !snapshots.isEmpty)
            }
        }
    }
}

private struct WorkReminderControl: View {
    @EnvironmentObject private var store: Store
    @EnvironmentObject private var reminders: ReminderCenter
    let bestTime: String?
    let model: String
    let detail: String

    private final class ActionState: ObservableObject {
        @Published var busy = false
        @Published var message: String?
    }
    @StateObject private var action = ActionState()

    private let minutesBefore = 30

    private var timeZone: TimeZone { TimeZone(identifier: store.timezone) ?? .current }
    private var reminderKey: String {
        [store.name, String(store.lat), String(store.lon),
         store.targetDateLabel, store.phenomenon, model, bestTime ?? ""].joined(separator: "|")
    }
    private var isCurrentScheduled: Bool { reminders.scheduled?.key == reminderKey }

    var body: some View {
        Group {
            if let fireDate = parsedBestTime {
                if fireDate.addingTimeInterval(-Double(minutesBefore * 60)) <= Date() {
                    Text(L10n.text("该结果的最佳时刻已过，无法设置提醒。", "This result's best time has passed; no reminder can be set."))
                        .font(.system(size: 10)).foregroundStyle(.secondary)
                } else {
                    reminderContent(fireDate: fireDate)
                }
            } else {
                EmptyView()
            }
        }
    }

    @ViewBuilder
    private func reminderContent(fireDate: Date) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            switch reminders.permission {
            case .authorized:
                if isCurrentScheduled, let scheduled = reminders.scheduled {
                    HStack(spacing: 8) {
                        Image(systemName: "bell.badge.fill").foregroundStyle(warm)
                        Text(String(format: L10n.text("已设置提醒 · %1$@ 前 %2$d 分钟",
                                                       "Reminder set · %1$@ · %2$d min before"),
                                    scheduledTime(scheduled.fireDate), minutesBefore))
                            .font(.system(size: 10))
                        Button(L10n.text("取消提醒", "Cancel reminder")) { reminders.cancel() }
                            .buttonStyle(.borderless).font(.system(size: 10))
                            .accessibilityLabel(L10n.text("取消提醒", "Cancel reminder"))
                    }
                } else {
                    Button {
                        schedule(fireDate: fireDate)
                    } label: {
                        Label(reminders.scheduled == nil
                              ? L10n.text("设置最佳时刻提醒", "Remind me before the best time")
                              : L10n.text("替换为当前结果提醒", "Replace with this result reminder"),
                              systemImage: "bell")
                            .font(.system(size: 10))
                    }
                    .buttonStyle(.borderless)
                    .disabled(action.busy)
                }
            case .denied:
                Text(L10n.text("通知权限已关闭。可在系统设置中开启后再设置提醒。",
                               "Notifications are off. Enable them in System Settings to use reminders."))
                    .font(.system(size: 10)).foregroundStyle(.secondary)
                Button(L10n.text("打开系统通知设置", "Open notification settings")) {
                    reminders.openSystemSettings()
                }
                .buttonStyle(.borderless).font(.system(size: 10))
            case .unknown, .notDetermined:
                Button {
                    requestAndSchedule(fireDate: fireDate)
                } label: {
                    Label(L10n.text("开启最佳时刻提醒", "Enable best-time reminder"), systemImage: "bell.badge")
                        .font(.system(size: 10))
                }
                .buttonStyle(.borderless)
                .disabled(action.busy)
            }
            Text(String(format: L10n.text("依据 %1$@ 最佳时刻，提前 %2$d 分钟通知；时区 %3$@。可在此取消，也可在系统通知设置中关闭。",
                                          "Based on %1$@ best time, notify %2$d min earlier; time zone %3$@. Cancel here or in Notification settings."),
                         model.uppercased(), minutesBefore, store.timezone))
                .font(.system(size: 9)).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            if let message = action.message {
                Text(message).font(.system(size: 9)).foregroundStyle(.orange)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.top, 2)
    }

    private var parsedBestTime: Date? {
        guard let bestTime else { return nil }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: bestTime) { return date }
        formatter.formatOptions = .withInternetDateTime
        return formatter.date(from: bestTime)
    }

    private func scheduledTime(_ date: Date) -> String {
        let formatter = DateFormatter()
        formatter.timeZone = timeZone
        formatter.dateStyle = .short
        formatter.timeStyle = .short
        return formatter.string(from: date)
    }

    private func requestAndSchedule(fireDate: Date) {
        action.busy = true
        action.message = nil
        reminders.requestAuthorization { [weak action] granted in
            guard granted else {
                action?.busy = false
                action?.message = L10n.text("未获得通知权限；可在系统设置中开启。",
                                            "Notification permission was not granted; enable it in System Settings.")
                return
            }
            schedule(fireDate: fireDate)
        }
    }

    private func schedule(fireDate: Date) {
        action.busy = true
        action.message = nil
        let body = "\(store.name) · \(store.resultPhenomenonLabel)\(detail.isEmpty ? "" : "\n\(detail)")"
        reminders.schedule(
            key: reminderKey,
            title: L10n.text("FireSky 观测提醒", "FireSky observation reminder"),
            body: body,
            fireDate: fireDate,
            minutesBefore: minutesBefore,
            timeZone: timeZone
        ) { [weak action] success, message in
            action?.busy = false
            action?.message = success ? nil : message
        }
    }
}

struct WorkHeroView: View {
    @EnvironmentObject private var store: Store
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    private var comparison: ForecastComparison { store.pointComparison }
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if store.result != nil && store.resultTab != "概览" {
                analysisContext
            } else {
                WorkActionSummaryView(comparison: comparison)
                if store.result != nil {
                    VStack(alignment: .leading, spacing: 12) {
                        HStack {
                            Text(L10n.t("模式结果")).fireSkyFont(size: 12, weight: .semibold)
                            Spacer()
                        }
                        // 标题跟随结果原始参数，而不是当前表单。
                        Text("\(store.resultTitle) \(L10n.t(store.resultPhenomenonLabel))")
                            .fireSkyFont(size: 11, relativeTo: .caption)
                            .foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                        WorkStaleResultBanner()
                        HStack(alignment: .top, spacing: 18) {
                            summary("gfs").frame(maxWidth: .infinity, alignment: .leading)
                            Divider().opacity(0.5)
                            summary("ec").frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                    .padding(16)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .workSurface()
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var analysisContext: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("\(store.resultTitle) \(L10n.t(store.resultPhenomenonLabel))")
                .fireSkyFont(size: 11, relativeTo: .caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            WorkStaleResultBanner()
            LazyVGrid(columns: Array(repeating: GridItem(.flexible(), alignment: .leading),
                                    count: dynamicTypeSize.isAccessibilitySize ? 1 : 2),
                      alignment: .leading, spacing: 8) {
                ForEach(ForecastComparison.models, id: \.self) { model in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack(spacing: 8) {
                            Text(model.uppercased()).foregroundStyle(modelColor(model))
                            Text(format(comparison.peak(model), digits: 2) + " / 2.5")
                                .monospacedDigit()
                            Spacer(minLength: 0)
                            Text(timeLabel(comparison.report(model)?["best_time_local"] as? String))
                                .monospacedDigit()
                                .help(L10n.t("最佳时刻"))
                                .accessibilityLabel(L10n.t("最佳时刻") + " " +
                                    timeLabel(comparison.report(model)?["best_time_local"] as? String))
                        }
                        .fireSkyFont(size: 12, weight: .semibold)
                        Text(L10n.status(comparison.status(model)))
                            .fireSkyFont(size: 11, relativeTo: .caption)
                            .foregroundStyle(comparison.error(model) == nil ? Color.secondary : Color.red)
                            .fixedSize(horizontal: false, vertical: true)
                        if let error = comparison.error(model) {
                            Text(error).fireSkyFont(size: 11, relativeTo: .caption)
                                .foregroundStyle(.red).textSelection(.enabled)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    .help(modelSourceNote(model))
                    .accessibilityElement(children: .combine)
                }
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .workSurface()
    }

    private func summary(_ model: String) -> some View {
        let report = comparison.report(model)
        let quality = comparison.peak(model)
        return VStack(alignment: .leading, spacing: 9) {
            HStack {
                Circle().fill(modelColor(model)).frame(width: 6, height: 6)
                Text(model.uppercased()).fireSkyFont(size: 12, weight: .semibold)
                Spacer(minLength: 4)
                Text(L10n.status(comparison.status(model)))
                    .fireSkyFont(size: 11, relativeTo: .caption)
                    .foregroundStyle(comparison.error(model) == nil ? Color.secondary : Color.red)
                    .fixedSize(horizontal: false, vertical: true)
            }
            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 4) {
                    HStack(alignment: .firstTextBaseline, spacing: 5) {
                        Text(format(quality, digits: 2)).fireSkyFont(size: 32, weight: .semibold, relativeTo: .largeTitle)
                            .monospacedDigit().foregroundStyle(quality == nil ? Color.secondary : modelColor(model))
                            .lineLimit(1).minimumScaleFactor(0.8)
                        Text("/ 2.5").fireSkyFont(size: 10, relativeTo: .caption).foregroundStyle(.secondary)
                    }
                    Text(L10n.qualityLevel(report?["peak_quality_level"] as? String
                        ?? report?["quality_level"] as? String ?? L10n.t("峰值质量指数")))
                        .fireSkyFont(size: 11, relativeTo: .caption)
                        .foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
                VStack(alignment: .leading, spacing: 5) {
                    Text(L10n.t("最佳时刻")).fireSkyFont(size: 11, relativeTo: .caption)
                        .foregroundStyle(.secondary)
                    Text(timeLabel(report?["best_time_local"] as? String))
                        .fireSkyFont(size: 22, weight: .semibold, relativeTo: .title2).monospacedDigit()
                }
            }
            SkyTimeline(report: report, timezone: store.timezone)
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .top, spacing: 5) {
                    if let cycle = cycleLabel(model, report) {
                        Text(L10n.tf("起报 %@", cycle))
                            .lineLimit(2).fixedSize(horizontal: false, vertical: true).workChip()
                    }
                    pressureFamilyChip(model, report)
                    cloudGenusChip(report)
                }
                VStack(alignment: .leading, spacing: 5) {
                    if let cycle = cycleLabel(model, report) {
                        Text(L10n.tf("起报 %@", cycle)).workChip()
                    }
                    HStack(spacing: 5) {
                        pressureFamilyChip(model, report)
                        cloudGenusChip(report)
                    }
                }
            }
            .fireSkyFont(size: 11, relativeTo: .caption).foregroundStyle(.secondary)
            Text(modelSourceNote(model))
                .fireSkyFont(size: 11, relativeTo: .caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            if let message = comparison.error(model) {
                Text(message).fireSkyFont(size: 11, relativeTo: .caption)
                    .foregroundStyle(.red).fixedSize(horizontal: false, vertical: true)
                    .help(message).textSelection(.enabled)
            }
        }
        .frame(maxWidth: .infinity, minHeight: 124, alignment: .topLeading)
        .fixedSize(horizontal: false, vertical: true)
            .accessibilityElement(children: .contain).accessibilityLabel(model.uppercased())
    }

    private func pressureFamilyChip(_ model: String, _ report: JSONObject?) -> some View {
        Text(L10n.t(report == nil ? "未预测" : familyLabel(report?["dominant_burnable_pressure_family"])))
            .lineLimit(2).fixedSize(horizontal: false, vertical: true).workChip()
            .help(L10n.t(report == nil
                         ? "尚未运行预测；完成一次预测后显示主导可烧压力族。"
                         : "主导可烧压力族（来自本次预测结果）"))
    }

    @ViewBuilder
    private func cloudGenusChip(_ report: JSONObject?) -> some View {
        if let text = cloudGenusBrief(report?["cloud_genus_proxy"] as? JSONObject) {
            Text(text)
                .lineLimit(1).fixedSize(horizontal: false, vertical: true).workChip()
                .help(L10n.text("依据压力层、相态与云层几何的模型云属代理，不代表卫星或人工云属观测。",
                                "Model-based cloud genus proxy from pressure, phase, and layer geometry; not a satellite or human observation."))
        }
    }
}

private func cloudGenusBrief(_ proxy: JSONObject?) -> String? {
    guard let proxy else { return nil }
    let label = proxy["label"] as? String ?? "unknown"
    let pct: String = {
        guard let conf = proxy["confidence"] as? NSNumber, conf.doubleValue.isFinite else { return "" }
        return " \(Int((conf.doubleValue * 100).rounded()))%"
    }()
    if label != "unknown", !label.isEmpty {
        return "\(genusLabel(label))（代理\(pct)）"
    }
    if let compatible = proxy["compatible_genera"] as? [String], let first = compatible.first, !first.isEmpty {
        return "\(genusLabel(first))等（代理）"
    }
    return nil
}

private let metricSpec: [(String, String, String)] = [
    ("burnable", "有效可烧云量", "前向可烧云占视野比例"),
    ("material", "云质厚度", "云体材质负载"),
    ("brightness", "亮度色彩", "霞光亮度与色彩评分"),
    ("cloud_layer", "主要云层", "云底–云顶 AGL"),
    ("aod", "AOD 550", "气溶胶光学厚度 · 通透度"),
    ("incoming", "入射受光", "太阳入射照亮云体的因子"),
    ("contrast", "返程对比度", "观测者视线大气对比因子"),
    ("duration", "持续时长", "核心观赏窗口 · 分钟"),
    ("confidence", "输入支持度", "输入覆盖与几何支持 · 非命中概率"),
    ("convective", "对流风险", "模式对流风险指标 · 非事件概率"),
]

struct WorkResultsView: View {
    @EnvironmentObject private var store: Store
    @Environment(\.colorScheme) private var scheme
    private var comparison: ForecastComparison { store.pointComparison }
    private var report: JSONObject? { comparison.report(store.selectedModel) }
    private let groups = [("云况与色彩", 0..<4), ("大气光学", 4..<7), ("时效与可靠性", 7..<10)]
    var body: some View {
        if comparison.result == nil {
            firstRunEmptyCanvas
        } else {
            VStack(spacing: 12) {
                HStack(alignment: .center, spacing: 12) {
                    WorkAnalysisTabs()
                    Spacer(minLength: 8)
                    if store.resultTab != "概览" {
                        HStack(spacing: 8) {
                            Text(L10n.status(comparison.status(store.selectedModel)))
                                .fireSkyFont(size: 10, relativeTo: .caption).foregroundStyle(.secondary)
                            WorkSegments(options: ForecastComparison.models, titles: ["GFS", "EC"], selection: $store.selectedModel)
                                .frame(width: 132)
                        }
                    }
                }
                ScrollView {
                    VStack(spacing: 12) {
                        if store.resultTab == "概览" {
                            ForEach(groups, id: \.0) { title, range in
                                VStack(alignment: .leading, spacing: 7) {
                                    Text(L10n.t(title))
                                        .fireSkyFont(size: 11, weight: .medium, relativeTo: .caption)
                                        .foregroundStyle(.secondary)
                                    // 自适应列数：宽屏多列，窄窗口自动降列，不靠继续缩小字号。
                                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 165), spacing: 10)],
                                              alignment: .leading, spacing: 10) {
                                        ForEach(Array(metricSpec[range]), id: \.0) { spec in
                                            VStack(alignment: .leading, spacing: 7) {
                                                Text(L10n.t(spec.1)).fireSkyFont(size: 12)
                                                    .foregroundStyle(.primary.opacity(0.8))
                                                    .fixedSize(horizontal: false, vertical: true)
                                                if spec.0 == "aod" {
                                                    WorkAODValue(summary: comparison.aod)
                                                } else if spec.0 == "cloud_layer" {
                                                    VStack(alignment: .leading, spacing: 4) {
                                                        ForEach(ForecastComparison.models, id: \.self) { model in
                                                            let modelReport = comparison.report(model)
                                                            let genus = cloudGenusBrief(modelReport?["cloud_genus_proxy"] as? JSONObject)
                                                            VStack(alignment: .leading, spacing: 1) {
                                                                HStack(spacing: 5) {
                                                                    Text(model.uppercased())
                                                                        .fireSkyFont(size: 10, weight: .medium, relativeTo: .caption)
                                                                        .foregroundStyle(modelColor(model)).frame(width: 25, alignment: .leading)
                                                                    Text(metricValue(spec.0, modelReport))
                                                                        .fireSkyFont(size: 14, weight: .semibold).monospacedDigit()
                                                                        .lineLimit(1).minimumScaleFactor(0.65)
                                                                }
                                                                if let genus {
                                                                    Text(genus)
                                                                        .fireSkyFont(size: 10, relativeTo: .caption)
                                                                        .foregroundStyle(.secondary)
                                                                        .padding(.leading, 30)
                                                                        .lineLimit(1)
                                                                }
                                                            }
                                                        }
                                                    }.frame(maxWidth: .infinity, alignment: .leading)
                                                } else {
                                                    HStack(spacing: 10) {
                                                        ForEach(ForecastComparison.models, id: \.self) { model in
                                                            VStack(alignment: .leading, spacing: 3) {
                                                                Text(model.uppercased())
                                                                    .fireSkyFont(size: 10, weight: .medium, relativeTo: .caption)
                                                                    .foregroundStyle(modelColor(model))
                                                                Text(metricValue(spec.0, comparison.report(model)))
                                                                    .fireSkyFont(size: 19, weight: .semibold, relativeTo: .title3).monospacedDigit()
                                                                    .foregroundStyle(comparison.report(model) == nil ? Color.secondary : Color.primary)
                                                                    .lineLimit(1).minimumScaleFactor(0.8)
                                                            }.frame(maxWidth: .infinity, alignment: .leading)
                                                        }
                                                    }
                                                }
                                                Text(L10n.t(spec.0 == "aod" ? comparison.aod.caption : spec.2))
                                                    .fireSkyFont(size: 10, relativeTo: .caption)
                                                    .foregroundStyle(.secondary)
                                                    .fixedSize(horizontal: false, vertical: true)
                                            }.padding(12).frame(maxWidth: .infinity, alignment: .leading)
                                                .frame(minHeight: 116, alignment: .topLeading).workSurface()
                                        }
                                    }
                                }
                            }
                            WorkQualityBreakdownView(comparison: comparison)
                            WorkComparisonCurveView(comparison: comparison, timezone: store.timezone)
                        } else if let message = comparison.error(store.selectedModel) {
                            Text(L10n.tf("%@ 未完成：%@", store.selectedModel.uppercased(), message))
                                .fireSkyFont(size: 12).foregroundStyle(.red)
                                .fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
                                .padding(18).frame(maxWidth: .infinity, alignment: .leading).workSurface()
                        } else if report == nil, comparison.result != nil {
                            Text(L10n.tf("%@ 暂无结果", store.selectedModel.uppercased()))
                                .fireSkyFont(size: 12)
                                .foregroundStyle(.secondary).padding(30)
                        } else if store.resultTab == "逐时台账" {
                            WorkLedgerView(report: report,
                                           bestTime: report?["best_time_local"] as? String,
                                           selectedTime: $store.pointSelectedTime)
                        } else {
                            WorkDiagnosticView(report: report, profile: store.resultTab == "大气剖面")
                        }
                    }.padding(.trailing, 2)
                }.defaultScrollAnchor(.top).scrollIndicators(.hidden)
            }
        }
    }

    /// 首次无结果空态：隐藏十余张空「—」占位卡，聚焦行动路径与将展示的专业能力。
    private var firstRunEmptyCanvas: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 14) {
                FireSkyMarkScene(size: 52)
                VStack(alignment: .leading, spacing: 5) {
                    Text(L10n.text("准备就绪，开始首次朝晚霞预测", "Ready for your first twilight forecast"))
                        .fireSkyFont(size: 15, weight: .semibold, relativeTo: .title3)
                    Text(L10n.text("预测完成后，这里会依次展示双模式行动结论、GFS 与 EC 独立指数、大气垂直剖面和逐时台账。",
                                   "Once complete, you will see the action summary, independent GFS/EC scores, atmospheric cross-section, and hourly ledger here."))
                        .fireSkyFont(size: 12).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 8)
            }
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 190), spacing: 10)], spacing: 10) {
                emptyPreviewItem(
                    symbol: "chart.bar.xaxis",
                    title: L10n.text("双模式独立对比", "Independent GFS & EC"),
                    detail: L10n.text("GFS 0.25° 与 EC 公开数据各自评分（0–2.5），不平均、不互相填补。",
                                      "GFS 0.25° and EC Open Data scored independently on 0–2.5 without blending.")
                )
                emptyPreviewItem(
                    symbol: "cloud.sun",
                    title: L10n.text("太阳方向大气剖面", "Solar Atmospheric Profile"),
                    detail: L10n.text("沿太阳光路查看高中低云层、地形遮挡与理论受光高度。",
                                      "Inspect cloud layers, terrain masking, and illuminated altitudes along the solar path.")
                )
                emptyPreviewItem(
                    symbol: "clock.badge.checkmark",
                    title: L10n.text("最佳时刻与窗口", "Best Time & Window"),
                    detail: L10n.text("给出推荐到达时段、主要限制因素，并支持设置提醒或导出摘要。",
                                      "Recommended viewing window, limiting factors, reminders, and text summary export.")
                )
            }
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .workSurface()
    }

    private func emptyPreviewItem(symbol: String, title: String, detail: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 7) {
                Image(systemName: symbol)
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(warm)
                Text(title).fireSkyFont(size: 12, weight: .semibold)
            }
            Text(detail)
                .fireSkyFont(size: 11, relativeTo: .caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(12)
        .frame(maxWidth: .infinity, minHeight: 84, alignment: .topLeading)
        .background(Color.primary.opacity(scheme == .dark ? 0.04 : 0.03),
                    in: RoundedRectangle(cornerRadius: 10))
        .overlay {
            RoundedRectangle(cornerRadius: 10)
                .strokeBorder(Color.secondary.opacity(0.14), lineWidth: 1)
        }
    }
}

/// Read-only score composition panel (plan item point-3). Every value is read from the
/// backend report (quality_components / report fields); nothing is recomputed in SwiftUI.
private struct WorkQualityBreakdownView: View {
    let comparison: ForecastComparison

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                Text(L10n.t("评分组成")).fireSkyFont(size: 13, weight: .semibold)
                Text(L10n.t("只读展示后端计算值 · 不在客户端重算"))
                    .fireSkyFont(size: 9, relativeTo: .caption).foregroundStyle(.secondary)
                Spacer()
            }
            ForEach(ForecastComparison.models, id: \.self) { model in
                if let report = comparison.report(model) {
                    breakdown(model: model, report: report)
                } else if let message = comparison.error(model) {
                    Text("\(model.uppercased()) 未完成：\(message)")
                        .font(.system(size: 10)).foregroundStyle(.red).lineLimit(2)
                } else {
                    Text(L10n.tf("%@ 暂无结果", model.uppercased()))
                        .fireSkyFont(size: 10, relativeTo: .caption).foregroundStyle(.secondary)
                }
            }
        }.padding(16).frame(maxWidth: .infinity, alignment: .leading).workSurface()
    }

    private func breakdown(model: String, report: JSONObject) -> some View {
        let components = report["quality_components"] as? JSONObject ?? [:]
        let modelSource = report["model_source"] as? String ?? model
        return VStack(alignment: .leading, spacing: 9) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Circle().fill(modelColor(model)).frame(width: 6, height: 6)
                Text(model.uppercased()).font(.system(size: 11, weight: .semibold))
                Text("\(format(report["quality_index"], digits: 3)) / 2.5")
                    .font(.system(size: 12, weight: .semibold)).monospacedDigit()
                    .foregroundStyle(modelColor(model))
                Text(report["quality_level"] as? String ?? "")
                    .font(.system(size: 9)).foregroundStyle(.secondary).lineLimit(1)
                Spacer()
                if let limiting = components["quality_limiting_factor"] as? String, !limiting.isEmpty {
                    Text("当前限制因素 \(limiting)")
                        .font(.system(size: 9)).foregroundStyle(.secondary).lineLimit(1).help(limiting)
                }
            }
            HStack(alignment: .top, spacing: 14) {
                VStack(spacing: 0) {
                    adjustmentRow("云量基础分",
                                  "有效可烧 \(ratioText(components["burnable_cloud_coverage_effective"])) · 可烧载荷 \(format(components["forward_burnable_cloud_load_effective"] ?? components["forward_burnable_cloud_load_peak"], digits: 2)) · 前方总云场 \(ratioText(components["forward_view_cloud_field_amount_peak"]))",
                                  signedDelta(components["coverage_base_score"] ?? components["coverage_score"]))
                    Divider().opacity(0.5)
                    adjustmentRow("可烧云比例",
                                  "可烧/总云量 \(ratioText(components["forward_burnable_cloud_ratio_peak"])) · 前方可烧云量 \(ratioText(components["forward_burnable_cloud_field_amount_peak"]))",
                                  nil)
                    Divider().opacity(0.5)
                    adjustmentRow("几何遮挡（已计入可烧量）",
                                  "最佳云入射遮挡 \(ratioText(components["solar_incoming_block_risk_at_best_cloud"] ?? components["solar_incoming_block_risk_mean"])) · 不重复扣分",
                                  nil)
                    Divider().opacity(0.5)
                    adjustmentRow("持续时间修正",
                                  "\(format(report["duration_minutes"] ?? components["duration_minutes"], digits: 0)) 分钟 · ×\(format(components["duration_factor"], digits: 3))",
                                  signedDelta(components["duration_adjustment"]))
                    Divider().opacity(0.5)
                    adjustmentRow("入射 AOD 修正",
                                  "本地 AOD \(format(components["quality_aod550"], digits: 3)) · 入射因子 ×\(format(components["incoming_aod_illumination_factor"], digits: 3))",
                                  signedDelta(components["incoming_aod_adjustment"]))
                    Divider().opacity(0.5)
                    adjustmentRow("观测 AOD 修正",
                                  "观测对比因子 ×\(format(components["observer_aod_contrast_factor"], digits: 3))",
                                  signedDelta(components["observer_aod_adjustment"]))
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                VStack(alignment: .leading, spacing: 6) {
                    capRow("修正后原始分", format(components["raw_quality_index"], digits: 3))
                    capRow("云量基础分", format(components["coverage_base_score"] ?? components["coverage_score"], digits: 3))
                    capRow("分析范围", valueSuffix(components["forward_field_radius_km"] ?? report["analysis_radius_km"], " km", digits: 0))
                    capRow("太阳方向扇区", valueSuffix(components["forward_sector_half_angle_deg"], "°", digits: 0, sign: "±"))
                    capRow("总云场来源", modelSource == "ec" ? "EC TCC + PL-RH 云柱代理" : "GFS 全量云场")
                    capRow("可烧量来源", text(components["quality_coverage_source"]) ?? "前方全量云场")
                    capRow("持续时间修正系数", "×\(format(components["duration_factor"], digits: 3))")
                    capRow("入射 / 观测回程因子",
                           "×\(format(components["incoming_aod_illumination_factor"], digits: 3)) / ×\(format(components["observer_aod_contrast_factor"], digits: 3))")
                    capRow("观测回程光学量",
                           "τ \(format(components["observer_return_optical_depth_mean"], digits: 3)) · T \(format(components["observer_return_transmission_mean"], digits: 3))")
                }.frame(width: 208, alignment: .leading)
            }
            VStack(alignment: .leading, spacing: 3) {
                Text("几何遮挡已包含在可烧云量中，不再重复扣分；持续时间和 AOD 各修正一次。低 AOD 小幅加分，高 AOD 有符号扣分。")
                Text("可烧量只包含既受太阳入射照亮、又能被地面观测者看到的云底面积；同一视线按距离由近到远合成，先遇到的低云会遮住其后的中高云。")
                Text("摄影焦段、Top-K、代表云和云边界不进入主评分。")
            }.font(.system(size: 9)).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.top, 4)
    }

    private func adjustmentRow(_ title: String, _ measured: String, _ delta: String?) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Text(title).font(.system(size: 10)).foregroundStyle(.secondary)
                .frame(width: 150, alignment: .leading)
            Text(measured).font(.system(size: 10)).monospacedDigit()
                .frame(maxWidth: .infinity, alignment: .leading).lineLimit(2)
            Text(delta ?? "—")
                .font(.system(size: 11, weight: .medium)).monospacedDigit()
                .foregroundStyle(deltaColor(delta))
                .frame(width: 56, alignment: .trailing)
                .accessibilityLabel(delta == nil ? "\(title)，无独立修正量" : "\(title)，修正量 \(delta!)")
        }.padding(.vertical, 6)
    }

    private func capRow(_ title: String, _ value: String) -> some View {
        HStack(alignment: .firstTextBaseline) {
            Text(title).font(.system(size: 9)).foregroundStyle(.secondary)
            Spacer(minLength: 6)
            Text(value).font(.system(size: 9, weight: .medium)).monospacedDigit()
                .multilineTextAlignment(.trailing).lineLimit(2)
        }
    }

    private func signedDelta(_ value: Any?) -> String? {
        guard let number = value as? NSNumber, number.doubleValue.isFinite else { return nil }
        if abs(number.doubleValue) < 0.0005 { return "0.000" }
        return String(format: "%+.3f", number.doubleValue)
    }

    /// 加分为绿、扣分为红、零修正保持中性（对齐 Web 的 delta 着色）。
    private func deltaColor(_ delta: String?) -> Color {
        guard let delta, delta != "0.000" else { return Color.secondary }
        return delta.hasPrefix("-") ? Color.red : Color.green
    }

    private func valueSuffix(_ value: Any?, _ suffix: String, digits: Int, sign: String = "") -> String {
        guard let number = value as? NSNumber, number.doubleValue.isFinite else { return "—" }
        return "\(sign)\(String(format: "%.\(digits)f", number.doubleValue))\(suffix)"
    }

    private func text(_ value: Any?) -> String? {
        guard let value = value as? String, !value.isEmpty else { return nil }
        return value
    }
}
/// 图表悬停读数的瞬态容器。本机工具链仅含 CommandLineTools（无 Xcode），
/// 没有 SwiftUIMacros 插件，SwiftUI 宏属性（@State 等）无法参与编译；
/// @StateObject/@Published 为普通属性包装器，可正常展开。
private final class ChartHoverState: ObservableObject {
    @Published var date: Date?
}

struct WorkComparisonCurveView: View {
    @EnvironmentObject private var store: Store
    let comparison: ForecastComparison
    let timezone: String
    /// 悬停时刻（原生图形规范：图表需可读 tooltip）；仅在指针悬停期间生效。
    @StateObject private var hover = ChartHoverState()

    var body: some View {
        let points = comparison.curve(timezone: timezone)
        let ratios = comparison.ratioCurve(timezone: timezone)
        let aod = comparison.aodCurve(timezone: timezone)
        let peak = points.max { $0.quality < $1.quality }
        let selectedDate = Self.time(points, matching: store.pointSelectedTime)
        return VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                Text("霞光质量与大气趋势").font(.system(size: 13, weight: .semibold))
                Text("质量轴固定 0–2.5 · 比例轴 0–100% · 缺测断线不补零")
                    .font(.system(size: 9)).foregroundStyle(.secondary)
                Spacer()
                HStack(spacing: 10) {
                    lineSwatch("GFS", warm, dashed: false)
                    lineSwatch("EC", coolAccent, dashed: true)
                }.font(.system(size: 10)).foregroundStyle(.secondary)
            }
            summaryChips(points: points, peak: peak)
            qualityChart(points: points, selectedDate: selectedDate)
            ratioChart(ratios)
            aodChart(aod)
            timelineStrip(peakLocal: peak?.local)
        }.padding(16).workSurface()
    }

    private func summaryChips(points: [ForecastCurvePoint], peak: ForecastCurvePoint?) -> some View {
        let aodValues = comparison.aodCurve(timezone: timezone).map(\.quality)
        let steps = comparison.report(store.selectedModel)?["steps"] as? [JSONObject] ?? []
        let validCount = steps.filter { $0["valid"] as? Bool != false }.count
        return HStack(spacing: 18) {
            summaryChip("峰值质量", peak.map { String(format: "%.3f / 2.5", $0.quality) } ?? "—")
            summaryChip("峰值时刻", peak.map { ObservationDate.timeLabel($0.time, timezone: timezone) } ?? "—")
            summaryChip("有效时次（\(store.selectedModel.uppercased())）",
                        steps.isEmpty ? "—" : "\(validCount) 有效 · 共 \(steps.count) 时次")
            summaryChip("AOD 550 范围",
                        aodValues.isEmpty ? "—" : String(format: "%.3f–%.3f", aodValues.min() ?? 0, aodValues.max() ?? 0))
        }
    }

    private func summaryChip(_ title: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.system(size: 8)).foregroundStyle(.secondary)
            Text(value).font(.system(size: 11, weight: .medium)).monospacedDigit().lineLimit(1)
        }.help(title)
    }

    private func lineSwatch(_ title: String, _ color: Color, dashed: Bool) -> some View {
        HStack(spacing: 5) {
            Rectangle()
                .fill(dashed ? Color.clear : color)
                .frame(width: 14, height: 2)
                .overlay {
                    Rectangle().stroke(color, style: StrokeStyle(lineWidth: 1.6, dash: dashed ? [4, 3] : []))
                        .opacity(dashed ? 1 : 0)
                }
            Text(title)
        }
    }

    /// 距悬停时刻最近的有效曲线点（循环实现避免过重的类型推断表达式）。
    private static func hoveredPoint(_ points: [ForecastCurvePoint], near date: Date) -> ForecastCurvePoint? {
        var best: ForecastCurvePoint?
        var bestDelta = Double.infinity
        for point in points {
            let delta = abs(point.time.timeIntervalSince(date))
            if delta < bestDelta {
                bestDelta = delta
                best = point
            }
        }
        return best
    }

    @ChartContentBuilder
    private func seriesMarks(_ points: [ForecastCurvePoint]) -> some ChartContent {
        ForEach(points) { point in
            LineMark(x: .value("时间", point.time), y: .value("质量指数", point.quality),
                     series: .value("曲线", point.series))
                .foregroundStyle(modelColor(point.model))
                .lineStyle(StrokeStyle(lineWidth: 2, dash: point.model == "ec" ? [4, 3] : []))
            PointMark(x: .value("时间", point.time), y: .value("质量指数", point.quality))
                .foregroundStyle(modelColor(point.model)).symbolSize(8)
        }
    }

    @ChartContentBuilder
    private func bestMarks(_ points: [ForecastCurvePoint]) -> some ChartContent {
        ForEach(ForecastComparison.models, id: \.self) { model in
            if let best = bestPoint(model, in: points) {
                PointMark(x: .value("时间", best.time), y: .value("质量指数", best.quality))
                    .symbol(.diamond).symbolSize(46)
                    .foregroundStyle(modelColor(model))
                    .accessibilityLabel("\(model.uppercased()) 最佳时刻 \(ObservationDate.timeLabel(best.time, timezone: timezone))，质量 \(String(format: "%.3f", best.quality))")
            }
        }
    }

    @ChartContentBuilder
    private func hoverMarks(_ points: [ForecastCurvePoint]) -> some ChartContent {
        // 悬停读数（原生图形规范 tooltip）：取最近有效点，数值直接来自曲线数据。
        if let hoverDate = hover.date,
           let hovered = Self.hoveredPoint(points, near: hoverDate) {
            RuleMark(x: .value("悬停时刻", hovered.time))
                .foregroundStyle(Color.secondary.opacity(0.35))
                .lineStyle(StrokeStyle(lineWidth: 1))
            PointMark(x: .value("时间", hovered.time), y: .value("质量指数", hovered.quality))
                .symbol(.circle).symbolSize(30)
                .foregroundStyle(modelColor(hovered.model))
                .annotation(position: .top, spacing: 5) {
                    VStack(alignment: .leading, spacing: 1) {
                        Text("\(hovered.model.uppercased()) \(ObservationDate.timeLabel(hovered.time, timezone: timezone))")
                            .font(.system(size: 9, weight: .medium))
                        Text("质量 \(String(format: "%.3f", hovered.quality))")
                            .font(.system(size: 9)).monospacedDigit()
                    }.padding(4).fireSkySurface(role: .overlay, cornerRadius: 4)
                        .accessibilityHidden(true)
                }
        }
    }

    private func qualityChart(points: [ForecastCurvePoint], selectedDate: Date?) -> some View {
        Chart {
            seriesMarks(points)
            bestMarks(points)
            if let selectedDate {
                RuleMark(x: .value("选中时刻", selectedDate))
                    .foregroundStyle(Color.secondary.opacity(0.55))
                    .lineStyle(StrokeStyle(lineWidth: 1, dash: [3, 3]))
                    .accessibilityLabel("选中时刻")
            }
            hoverMarks(points)
        }
        .chartYScale(domain: 0...2.5)
        .chartYAxis {
            AxisMarks(position: .leading, values: [0, 1.25, 2.5]) { value in
                AxisGridLine(stroke: StrokeStyle(lineWidth: 0.5, dash: [3, 3]))
                AxisValueLabel {
                    if let number = value.as(Double.self) {
                        Text(number.formatted(.number.precision(.fractionLength(0...2))))
                    }
                }
            }
        }
        .chartXAxis {
            AxisMarks(values: .automatic(desiredCount: 5)) { value in
                AxisGridLine()
                AxisValueLabel {
                    if let date = value.as(Date.self) { Text(ObservationDate.timeLabel(date, timezone: timezone)) }
                }
            }
        }
        .chartYAxisLabel("质量指数 · 0–2.5")
        .frame(height: 164)
        .overlay {
            if points.isEmpty {
                Text(comparison.result == nil ? "等待预测" : "暂无质量曲线")
                    .font(.system(size: 10)).foregroundStyle(.secondary)
            }
        }
        .accessibilityLabel("逐时质量趋势，GFS 实线，EC 虚线，菱形为各模式最佳时刻，竖虚线为选中时刻")
        .chartOverlay { proxy in
            Rectangle().fill(Color.clear).contentShape(Rectangle())
                .onContinuousHover { phase in
                    switch phase {
                    case .active(let location):
                        hover.date = proxy.value(atX: location.x, as: Date.self)
                    case .ended:
                        hover.date = nil
                    @unknown default:
                        hover.date = nil
                    }
                }
        }
    }

    /// 0–100% aligned sub-plot for the three ratio series (plan: 右轴或对齐小图).
    private func ratioChart(_ ratios: [ForecastRatioPoint]) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 12) {
                ForEach(["coverage", "ratio", "transmission"], id: \.self) { kind in
                    HStack(spacing: 5) {
                        Rectangle().fill(ratioSeriesColor(kind)).frame(width: 12, height: 2.5)
                        Text(ratioSeriesLabel(kind))
                    }.font(.system(size: 9)).foregroundStyle(.secondary)
                }
                Spacer()
                Text("EC 为虚线").font(.system(size: 9)).foregroundStyle(.tertiary)
            }
            Chart {
                ForEach(ratios) { point in
                    LineMark(x: .value("时间", point.time), y: .value("条件占比", point.value),
                             series: .value("曲线", point.series))
                        .foregroundStyle(ratioSeriesColor(point.kind))
                        .lineStyle(StrokeStyle(lineWidth: 1.8, dash: point.model == "ec" ? [4, 3] : []))
                }
            }
            .accessibilityLabel("覆盖率、照明与透过率趋势，共 \(ratios.count) 个采样点；EC 为虚线。")
            .chartYScale(domain: 0...1)
            .chartYAxis {
                AxisMarks(position: .trailing, values: [0, 0.5, 1]) { value in
                    AxisGridLine(stroke: StrokeStyle(lineWidth: 0.5, dash: [3, 3]))
                    AxisValueLabel {
                        if let number = value.as(Double.self) {
                            Text("\(Int((number * 100).rounded()))%")
                        }
                    }
                }
            }
            .chartXAxis {
                AxisMarks(values: .automatic(desiredCount: 5)) { value in
                    AxisGridLine()
                    AxisValueLabel {
                        if let date = value.as(Date.self) { Text(ObservationDate.timeLabel(date, timezone: timezone)) }
                    }
                }
            }
            .chartYAxisLabel("条件占比 · 0–100%")
            .frame(height: 128)
            .overlay {
                if ratios.isEmpty {
                    Text("暂无 0–100% 比例序列")
                        .font(.system(size: 10)).foregroundStyle(.secondary)
                }
            }
            .accessibilityLabel("可烧云占比、可烧比例与回程透过率趋势，占比轴固定 0 到 100%")
        }
    }

    private func aodChart(_ aod: [ForecastCurvePoint]) -> some View {
        let values = aod.map(\.quality)
        let times = aod.map(\.time)
        let domain = Self.aodDomain(aod)
        return VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline) {
                Text("AOD 550 趋势").font(.system(size: 11, weight: .semibold))
                Text(values.isEmpty ? "暂无可用 AOD550 时序"
                     : String(format: "范围 %.3f–%.3f · 来源 %@", values.min() ?? 0, values.max() ?? 0, comparison.aod.source))
                    .font(.system(size: 9)).foregroundStyle(.secondary)
                Spacer()
                if let first = times.min(), let last = times.max() {
                    Text("时次 \(ObservationDate.timeLabel(first, timezone: timezone))–\(ObservationDate.timeLabel(last, timezone: timezone))")
                        .font(.system(size: 9)).foregroundStyle(.secondary).monospacedDigit()
                }
            }
            Chart {
                ForEach(aod) { point in
                    LineMark(x: .value("时间", point.time), y: .value("AOD 550", point.quality),
                             series: .value("曲线", point.series))
                        .foregroundStyle(modelColor(point.model))
                        .lineStyle(StrokeStyle(lineWidth: 1.8, dash: point.model == "ec" ? [4, 3] : []))
                    PointMark(x: .value("时间", point.time), y: .value("AOD 550", point.quality))
                        .foregroundStyle(modelColor(point.model)).symbolSize(6)
                }
            }
            .chartYScale(domain: domain)
            .chartXAxis {
                AxisMarks(values: .automatic(desiredCount: 4)) { value in
                    AxisGridLine()
                    AxisValueLabel {
                        if let date = value.as(Date.self) { Text(ObservationDate.timeLabel(date, timezone: timezone)) }
                    }
                }
            }
            .chartYAxisLabel("AOD 550")
            .frame(height: 118)
            .overlay {
                if aod.isEmpty {
                    Text(comparison.result == nil ? "等待预测" : "暂无可用 AOD550 时序")
                        .font(.system(size: 10)).foregroundStyle(.secondary)
                }
            }
            .accessibilityLabel("AOD 550 逐时趋势，自适应取值范围，缺测时段断线")
        }
    }

    /// Event timeline strip; selecting a bar updates the shared local selection only
    /// (never the map). Keyboard focusable buttons with VoiceOver labels.
    private func timelineStrip(peakLocal: String?) -> some View {
        let steps = comparison.report(store.selectedModel)?["steps"] as? [JSONObject] ?? []
        let validSteps = steps.filter { $0["valid"] as? Bool != false }
        return VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline) {
                Text("事件时间轴（\(store.selectedModel.uppercased())）")
                    .font(.system(size: 11, weight: .semibold))
                Text("点击选择时刻；选中态不驱动地图")
                    .font(.system(size: 9)).foregroundStyle(.secondary)
                Spacer()
                Text("深色为峰值 · 高亮为当前")
                    .font(.system(size: 9)).foregroundStyle(.tertiary)
            }
            if steps.isEmpty {
                Text(comparison.result == nil ? "等待预测" : "暂无时次")
                    .font(.system(size: 10)).foregroundStyle(.secondary)
            } else {
                HStack(alignment: .bottom, spacing: 3) {
                    ForEach(Array(steps.enumerated()), id: \.offset) { _, step in
                        let valid = step["valid"] as? Bool != false
                        let local = step["valid_time_local"] as? String ?? ""
                        if valid {
                            let selected = store.pointSelectedTime == local
                            let isPeak = !local.isEmpty && local == peakLocal
                            Button {
                                store.pointSelectedTime = local
                            } label: {
                                RoundedRectangle(cornerRadius: 2)
                                    .fill(selected ? warm : isPeak ? warm.opacity(0.62) : Color.secondary.opacity(0.32))
                                    .frame(height: selected ? 18 : 10)
                            }.buttonStyle(.plain)
                                .accessibilityLabel("查看 \(timeLabel(local))，质量 \(format(step["instant_quality_index"], digits: 3))")
                                .accessibilityAddTraits(selected ? .isSelected : [])
                                .help("\(timeLabel(local))\(isPeak ? " · 峰值" : "")")
                        } else {
                            RoundedRectangle(cornerRadius: 2)
                                .stroke(Color.secondary.opacity(0.4), style: StrokeStyle(lineWidth: 1, dash: [2, 2]))
                                .frame(height: 10)
                                .accessibilityHidden(true)
                                .help("超出有效太阳窗口")
                        }
                    }
                }
                if let first = validSteps.first?["valid_time_local"] as? String,
                   let last = validSteps.last?["valid_time_local"] as? String {
                    HStack {
                        Text(timeLabel(first)).monospacedDigit()
                        Spacer()
                        Text(timeLabel(last)).monospacedDigit()
                    }.font(.system(size: 9)).foregroundStyle(.secondary)
                }
            }
        }
    }

    private func bestPoint(_ model: String, in points: [ForecastCurvePoint]) -> ForecastCurvePoint? {
        guard let best = comparison.report(model)?["best_time_local"] as? String, !best.isEmpty else { return nil }
        return points.first { $0.model == model && $0.local == best }
    }

    private static func time(_ points: [ForecastCurvePoint], matching local: String?) -> Date? {
        guard let local, !local.isEmpty else { return nil }
        return points.first { $0.local == local }?.time
    }

    private static func aodDomain(_ points: [ForecastCurvePoint]) -> ClosedRange<Double> {
        let values = points.map(\.quality)
        guard let low = values.min(), let high = values.max() else { return 0...1 }
        let padding = Swift.max(0.05, (high - low) * 0.25)
        let minLow = Swift.max(0, low - padding)
        return minLow...Swift.max(minLow + 0.1, high + padding)
    }
}

private final class LedgerFilterState: ObservableObject {
    @Published var showInvalid = false
}

struct WorkLedgerView: View {
    @EnvironmentObject private var store: Store
    @StateObject private var filter = LedgerFilterState()
    let report: JSONObject?
    let bestTime: String?
    @Binding var selectedTime: String?
    private var allSteps: [JSONObject] { report?["steps"] as? [JSONObject] ?? [] }
    private var visibleSteps: [JSONObject] {
        filter.showInvalid ? allSteps : allSteps.filter { $0["valid"] as? Bool != false }
    }
    private var hiddenInvalidCount: Int {
        allSteps.filter { $0["valid"] as? Bool == false }.count
    }
    var body: some View {
        VStack(spacing: 0) {
            ScrollView(.horizontal, showsIndicators: false) {
                VStack(spacing: 0) {
                    header
                    Divider()
                    ForEach(Array(visibleSteps.enumerated()), id: \.offset) { _, item in
                        row(item)
                        Divider().opacity(0.5)
                    }
                    if report == nil {
                        Text(L10n.t("等待预测")).foregroundStyle(.secondary).padding(40)
                    } else if visibleSteps.isEmpty {
                        Text(L10n.text("当前时段内没有有效判断时次。", "No valid evaluated time steps in this window."))
                            .font(.callout).foregroundStyle(.secondary).padding(28)
                    }
                }
                .frame(minWidth: 1284, alignment: .leading)
            }
            Divider().opacity(0.45)
            HStack(spacing: 10) {
                Text("云底–云顶为海拔 ASL 口径，「云层 AGL」为离地口径；— 表示缺测，默认仅展示有效判断时次；点击行仅切换本地选中时刻，不驱动地图。")
                    .font(.system(size: 9)).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 8)
                if hiddenInvalidCount > 0 {
                    Button {
                        filter.showInvalid.toggle()
                    } label: {
                        Label(
                            filter.showInvalid
                                ? L10n.text("隐藏 \(hiddenInvalidCount) 个未判断时次", "Hide \(hiddenInvalidCount) unevaluated")
                                : L10n.text("显示 \(hiddenInvalidCount) 个未判断时次", "Show \(hiddenInvalidCount) unevaluated"),
                            systemImage: filter.showInvalid ? "eye.slash" : "eye"
                        )
                        .font(.system(size: 10, weight: .medium))
                    }
                    .buttonStyle(.borderless)
                }
            }
            .padding(.horizontal, 14).padding(.vertical, 8)
        }
        .workSurface()
    }

    private var header: some View {
        HStack(spacing: 0) {
            Text(L10n.t("时间")).frame(width: 88, alignment: .leading)
            Text(L10n.t("质量")).frame(width: 68, alignment: .leading)
            Text(L10n.t("档位")).frame(width: 92, alignment: .leading)
            Text(L10n.t("可烧")).frame(width: 52, alignment: .leading)
            Text(L10n.t("可烧云量")).frame(width: 84, alignment: .leading)
            Text(L10n.t("主导压力族")).frame(width: 96, alignment: .leading)
            Text(L10n.t("云底–云顶 ASL")).frame(width: 120, alignment: .leading)
            Text(L10n.t("云层 AGL")).frame(width: 112, alignment: .leading)
            Text(L10n.t("亮度颜色")).frame(width: 68, alignment: .leading)
            Text(L10n.t("可烧比例")).frame(width: 68, alignment: .leading)
            Text(L10n.t("回程透过率")).frame(width: 76, alignment: .leading)
            Text(L10n.t("入射阻挡")).frame(width: 72, alignment: .leading)
            Text("AOD 550").frame(width: 72, alignment: .leading)
            Text(L10n.t("太阳仰角")).frame(width: 78, alignment: .leading)
            Text(L10n.t("场景")).frame(width: 138, alignment: .leading)
        }
        .font(.system(size: 10, weight: .semibold))
        .foregroundStyle(.secondary)
        .padding(.horizontal, 14)
        .padding(.vertical, 14)
    }

    private func row(_ item: JSONObject) -> some View {
        let valid = item["valid"] as? Bool != false
        let burnable = item["physically_burnable"] as? Bool
        let base = item["dominant_burnable_cloud_base_agl_mean_m"]
        let top = item["dominant_burnable_cloud_top_agl_mean_m"]
        let baseASL = item["dominant_burnable_cloud_base_asl_mean_m"]
        let topASL = item["dominant_burnable_cloud_top_asl_mean_m"]
        let local = item["valid_time_local"] as? String ?? ""
        let selected = !local.isEmpty && selectedTime == local
        let isBest = !local.isEmpty && local == bestTime
        return Button {
            guard valid, !local.isEmpty else { return }
            selectedTime = local
        } label: {
            HStack(spacing: 0) {
                HStack(spacing: 4) {
                    Text(timeLabel(local))
                    if isBest {
                        Image(systemName: "star.fill")
                            .font(.system(size: 9))
                            .foregroundStyle(warm)
                    }
                }
                .frame(width: 88, alignment: .leading)
                Text(valid ? format(item["instant_quality_index"], digits: 3) : "—")
                    .frame(width: 68, alignment: .leading)
                Text(valid ? item["instant_quality_level"] as? String ?? "—" : "缺测")
                    .frame(width: 92, alignment: .leading)
                Image(systemName: !valid || burnable == nil ? "minus"
                      : burnable == true ? "checkmark" : "xmark")
                    .foregroundStyle(!valid || burnable == nil ? Color.secondary : burnable == true ? coolAccent : Color.orange)
                    .frame(width: 52, alignment: .leading)
                    .accessibilityLabel(!valid ? "缺测" : burnable == true ? "可烧" : burnable == false ? "不可烧" : "未提供")
                Text(valid ? percent(item["burnable_cloud_coverage"]) : "—")
                    .frame(width: 84, alignment: .leading)
                Text(valid ? familyLabel(item["dominant_burnable_pressure_family"]) : "—")
                    .frame(width: 96, alignment: .leading)
                    .foregroundStyle(item["dominant_burnable_pressure_family"] == nil ? Color.secondary : Color.primary)
                Text(valid ? aslRangeLabel(base: baseASL, top: topASL) : "—")
                    .frame(width: 120, alignment: .leading)
                Text(valid ? cloudLayerLabel(base: base, top: top) : "—")
                    .frame(width: 112, alignment: .leading)
                Text(valid ? ratioText(item["brightness_color"]) : "—")
                    .frame(width: 68, alignment: .leading)
                Text(valid ? ratioText(item["forward_burnable_cloud_ratio"]) : "—")
                    .frame(width: 68, alignment: .leading)
                Text(valid ? ratioText(item["observer_return_transmission"]) : "—")
                    .frame(width: 76, alignment: .leading)
                Text(valid ? ratioText(item["solar_incoming_block_risk"]) : "—")
                    .frame(width: 72, alignment: .leading)
                Text(valid ? format(item["quality_aod550"], digits: 2) : "—")
                    .frame(width: 72, alignment: .leading)
                Text(valid ? format(item["solar_elevation_deg"], digits: 1) : "—")
                    .frame(width: 78, alignment: .leading)
                Text(valid ? sceneLabel(item["selected_regime"]) : "—")
                    .frame(width: 138, alignment: .leading)
            }
            .font(.system(size: 11))
            .monospacedDigit()
            .padding(.vertical, 12)
            .padding(.horizontal, 14)
            .contentShape(Rectangle())
            .background(selected ? warm.opacity(0.10) : Color.clear, in: RoundedRectangle(cornerRadius: 6))
        }
        .buttonStyle(.plain)
        .disabled(!valid || local.isEmpty)
        .accessibilityLabel(valid ? "时次 \(timeLabel(local))，质量 \(format(item["instant_quality_index"], digits: 3))\(isBest ? "，最佳时刻" : "")" : "缺测时次")
        .accessibilityAddTraits(selected ? .isSelected : [])
    }

    /// ASL datum column, matching the Web「加权云底–云顶」column (km ASL).
    private func aslRangeLabel(base: Any?, top: Any?) -> String {
        guard let base = base as? NSNumber, base.doubleValue.isFinite,
              let top = top as? NSNumber, top.doubleValue.isFinite else { return "—" }
        return String(format: "%.1f–%.1f km", base.doubleValue / 1000, top.doubleValue / 1000)
    }

    private func cloudLayerLabel(base: Any?, top: Any?) -> String {
        guard let base = base as? NSNumber, let top = top as? NSNumber else { return "—" }
        return "\(Int(base.doubleValue.rounded()))–\(Int(top.doubleValue.rounded())) m"
    }
}

struct WorkDiagnosticView: View {
    let report: JSONObject?
    let profile: Bool
    var waiting = "等待预测"
    @EnvironmentObject private var store: Store
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(profile ? "太阳方向大气剖面" : "诊断与时间窗").font(.system(size: 13, weight: .semibold))
            if let report {
                if !profile {
                    Text(summaryText(report))
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if profile {
                    WorkCrossSectionView(
                        section: report["cross_section"] as? JSONObject,
                        genusProxy: report["cloud_genus_proxy"] as? JSONObject
                    )
                    if let source = report["profile_source"] as? JSONObject,
                       source["available"] as? Bool == true {
                        let model = source["model_source"] as? String ?? "gfs"
                        Button("读取冻结剖面", systemImage: "waveform.path.ecg") {
                            store.queryPointProfile()
                        }
                        .disabled(store.busy)
                        .help("从已发布结果的冻结源读取太阳方向剖面")
                        FrozenProfileView(model: model) { store.queryPointProfile() }
                    }
                }
                WorkDiagnosticGroup(title: "运行与时间") {
                    diagnosticRow("预测周期", report["cycle_time_utc"])
                    diagnosticRow("本地锚点", report["anchor_local"])
                    diagnosticRow("时区", report["timezone_name"])
                    diagnosticRow("模式", report["forecast_mode"])
                    diagnosticRow("计算后端", report["compute_backend"])
                    diagnosticRow("分析半径", report["analysis_radius_km"], suffix: " km")
                    diagnosticRow("选中场景", sceneLabel(report["selected_regime"]))
                    diagnosticRow("持续时长", report["duration_minutes"], digits: 0, suffix: " 分钟")
                    diagnosticRow("跨模式分歧", "当前后端未计算，GFS/EC 请并列比较")
                }
                if let cache = report["cache"] as? JSONObject {
                    WorkDiagnosticGroup(title: "缓存状态") {
                        diagnosticRow("状态", cache["status"])
                        diagnosticRow("生成时间（UTC）", cache["generated_at_utc"])
                    }
                }
                WorkDiagnosticGroup(title: "云体几何与可解释性") {
                    diagnosticRow("几何代理", report["cloud_geometry_proxy_source"])
                    diagnosticRow("几何支持样本", report["cloud_geometry_support_count"])
                    diagnosticRow("几何不确定度", report["cloud_geometry_uncertainty_m"], digits: 0, suffix: " m")
                    diagnosticRow("几何支持度", report["cloud_geometry_confidence"], percentValue: true)
                    diagnosticRow("置信度语义", report["confidence_kind"] ?? report["confidence_score_semantics"])
                    diagnosticRow("质量指数语义", report["quality_index_semantics"])
                    diagnosticRow("主压力族", familyLabel(report["dominant_burnable_pressure_family"]))
                }
                WorkGenusPhaseDiagnosis(report: report)
                if let scores = report["scene_scores"] as? JSONObject, !scores.isEmpty {
                    WorkDiagnosticGroup(title: "场景分数") {
                        ForEach(scores.keys.sorted(), id: \.self) { key in
                            diagnosticRow(key, scores[key], percentValue: true)
                        }
                    }
                }
                if report["nowcast"] as? JSONObject == nil {
                    WorkSharedDiagnosis(report: report)
                }
                if let aerosol = report["aerosol"] as? JSONObject {
                    WorkDiagnosticGroup(title: "气溶胶来源") {
                        diagnosticRow("AOD 550", aerosol["aod550"], digits: 3)
                        diagnosticRow("来源", aerosol["source"])
                        diagnosticRow("数据质量", aerosol["quality"])
                    }
                }
                if let source = report["profile_source"] as? JSONObject {
                    WorkDiagnosticGroup(title: "剖面能力") {
                        let available = source["available"] as? Bool == true
                        diagnosticRow("冻结剖面", available ? "可查询" : "不可用")
                        if !available { diagnosticRow("原因", source["reason"]) }
                        if let expires = source["expires_at_utc"] as? String {
                            diagnosticRow("冻结源有效期", expires)
                        }
                    }
                }
                if let provenance = report["data_provenance"] as? JSONObject {
                    WorkDiagnosticGroup(title: "数据来源") {
                        ForEach(provenance.keys.sorted(), id: \.self) { key in
                            diagnosticRow(key, provenance[key])
                        }
                    }
                }
                HStack(spacing: 10) {
                    if let path = report["html_report_path"] as? String, !path.isEmpty {
                        Button("完整报告", systemImage: "doc.text") {
                            let url = URL(fileURLWithPath: path, relativeTo: URL(fileURLWithPath: FileManager.default.currentDirectoryPath))
                            NSWorkspace.shared.open(url)
                        }
                    }
                    if let map = report["map_capability"] as? JSONObject,
                       map["available"] as? Bool == false,
                       let reason = map["reason"] as? String {
                        Label(friendlyEngineMessage(reason), systemImage: "map")
                            .font(.system(size: 10)).foregroundStyle(.secondary)
                            .lineLimit(2).help(friendlyEngineMessage(reason))
                    }
                }
            } else {
                Text(waiting).foregroundStyle(.secondary).frame(maxWidth: .infinity, minHeight: 160)
            }
        }.padding(18).frame(maxWidth: .infinity, alignment: .leading).workSurface()
    }
}

/// Cross-section grid parsed from the already-returned cross_section.fields payload
/// (engine/src/cross_section.rs). Nulls stay nil so missing cells are never drawn as clear sky.
private struct CrossSectionData {
    let distances: [Double]
    let levels: [Int]
    let heights: [[Double?]]
    let temps: [[Double?]]
    let rh: [[Double?]]
    let amounts: [[Double?]]
    let presence: [[Double?]]
    let fractions: [[Double?]]
    let familyCodes: [[Int]]
    let statuses: [[Int]]
    let layerBases: [[Double?]]
    let layerTops: [[Double?]]
    let terrain: [Double]
    let requiredASL: [Double]
    let targets: [JSONObject]

    init?(section: JSONObject) {
        let distances = (section["distances_km"] as? [Any])?.compactMap(number) ?? []
        guard !distances.isEmpty else { return nil }
        let fields = section["fields"] as? JSONObject ?? [:]
        let grid: (String) -> [[Double?]] = { key in
            ((fields[key] as? [[Any]]) ?? []).map { (row: [Any]) in
                row.map { ($0 as? NSNumber)?.doubleValue }
            }
        }
        let ints: (String) -> [[Int]] = { key in
            ((fields[key] as? [[Any]]) ?? []).map { (row: [Any]) in
                row.map { ($0 as? NSNumber)?.intValue ?? 0 }
            }
        }
        self.distances = distances
        self.levels = (section["pressure_levels_hpa"] as? [Any])?.compactMap { ($0 as? NSNumber)?.intValue } ?? []
        self.heights = grid("geopotential_height_asl_m")
        self.temps = grid("temperature_c")
        self.rh = grid("rh_percent")
        self.amounts = grid("cloud_amount")
        self.presence = grid("cloud_presence")
        self.fractions = grid("cloud_fraction_percent")
        self.familyCodes = ints("pressure_family_code")
        self.statuses = ints("cloud_layer_status")
        self.layerBases = grid("cloud_layer_base_asl_m")
        self.layerTops = grid("cloud_layer_top_asl_m")
        self.terrain = (section["terrain_elevation_asl_m"] as? [Any])?.compactMap(number) ?? []
        self.requiredASL = (section["required_illumination_base_asl_m"] as? [Any])?.compactMap(number) ?? []
        self.targets = section["theoretical_targets"] as? [JSONObject] ?? []
    }
}

private struct WorkCrossSectionView: View {
    let section: JSONObject?
    let genusProxy: JSONObject?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let section {
                CrossSectionFigureView(section: section, genusProxy: genusProxy)
            } else {
                Text("此结果没有可查询的冻结模式剖面。")
                    .font(.system(size: 10)).foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("太阳方向大气剖面")
    }
}

/// 剖面缩放状态容器；本机 CommandLineTools 无 SwiftUIMacros，使用 ObservableObject 代替 @State。
private final class CrossSectionZoomState: ObservableObject {
    @Published var scale = 1.0
}

/// 距离×高度剖面图层组件（regional-7，区域/单点共用，方案 :123-124）。
/// 图层：温度层线、RH/云量色块（压力族配色）、地形填充、云层状态描边、
/// 理论照明基准与理论目标云底/云顶；缺测层不绘制、不解释成晴空。
struct CrossSectionFigureView: View {
    let section: JSONObject
    let genusProxy: JSONObject?
    @Environment(\.colorScheme) private var scheme
    @StateObject private var zoom = CrossSectionZoomState()
    private var zoomScale: Double {
        get { zoom.scale }
        nonmutating set { zoom.scale = newValue }
    }

    init(section: JSONObject, genusProxy: JSONObject? = nil) {
        self.section = section
        self.genusProxy = genusProxy
    }

    private let plotAspectRatio: CGFloat = 2.45

    private var data: CrossSectionData? { CrossSectionData(section: section) }

    private var canvasAccessibilityLabel: String {
        guard let data else { return "大气剖面图不可用" }
        let low = Int(data.terrain.min() ?? 0)
        let high = Int(data.terrain.max() ?? 0)
        return "大气剖面图，\(data.distances.count) 个距离采样点，\(data.levels.count) 个气压层，地形高度 \(low) 至 \(high) 米，\(data.targets.count) 个目标云层标记"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let data {
                plotControls
                GeometryReader { geometry in
                    let plotWidth = geometry.size.width * CGFloat(zoomScale)
                    ScrollView(.horizontal) {
                        Canvas { context, size in
                            draw(context: context, size: size, data: data, scale: zoomScale)
                        }
                        .frame(width: plotWidth, height: geometry.size.height)
                    }
                    .frame(height: geometry.size.height)
                    .scrollIndicators(zoomScale > 1 ? .automatic : .hidden)
                }
                .aspectRatio(plotAspectRatio / CGFloat(zoomScale), contentMode: .fit)
                .accessibilityLabel(canvasAccessibilityLabel)
                .accessibilityHint("使用放大和缩小按钮查看细节，或选择适配窗口恢复完整剖面。")
                .accessibilityAdjustableAction { direction in
                    switch direction {
                    case .increment: zoomScale = min(2.5, zoomScale + 0.25)
                    case .decrement: zoomScale = max(1, zoomScale - 0.25)
                    @unknown default: break
                    }
                }
                .accessibilityValue("\(Int((zoomScale * 100).rounded()))%")
                legend
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 112), alignment: .leading)],
                          alignment: .leading, spacing: 8) {
                    diagnosticChip("太阳高度", section["solar_elevation_deg"], suffix: "°")
                    diagnosticChip("太阳方位", section["solar_azimuth_deg"], suffix: "°")
                    diagnosticChip("剖面范围", section["analysis_radius_km"], suffix: " km")
                    diagnosticChip("相对时刻", offsetLabel(section["offset_minutes"]))
                    diagnosticChip("有效时次", timeLabel(section["valid_time_local"] as? String))
                    diagnosticChip("采样", section["spatial_sampling"])
                }
                genusSummary
                targetStrip(data.targets)
            } else {
                Text("后端未返回可绘制的剖面字段；可在诊断中查看理论目标与照明基准。")
                    .font(.system(size: 10)).foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, minHeight: 120, alignment: .center)
            }
        }
    }

    private var plotControls: some View {
        HStack(spacing: 7) {
            Text("沿光路高度 · 海拔口径")
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(.secondary)
            Spacer(minLength: 8)
            Text("\(Int((zoomScale * 100).rounded()))%")
                .font(.system(size: 11, weight: .medium).monospacedDigit())
                .foregroundStyle(.secondary)
                .frame(width: 38, alignment: .trailing)
                .accessibilityLabel("剖面缩放")
                .accessibilityValue("\(Int((zoomScale * 100).rounded()))%")
            Button {
                zoomScale = max(1, zoomScale - 0.25)
            } label: {
                Image(systemName: "minus.magnifyingglass").frame(width: 24, height: 24)
            }
            .buttonStyle(.borderless)
            .help("缩小剖面")
            .accessibilityLabel("缩小剖面")
            .disabled(zoomScale <= 1)
            Button {
                zoomScale = min(2.5, zoomScale + 0.25)
            } label: {
                Image(systemName: "plus.magnifyingglass").frame(width: 24, height: 24)
            }
            .buttonStyle(.borderless)
            .help("放大剖面")
            .accessibilityLabel("放大剖面")
            .disabled(zoomScale >= 2.5)
            Button {
                zoomScale = 1
            } label: {
                Image(systemName: "arrow.counterclockwise").frame(width: 24, height: 24)
            }
            .buttonStyle(.borderless)
            .help("适配窗口")
            .accessibilityLabel("适配窗口")
            .disabled(zoomScale == 1)
        }
    }

    @ViewBuilder
    private var genusSummary: some View {
        if let genusProxy {
            let label = genusProxy["label"] as? String ?? "unknown"
            let compatible = (genusProxy["compatible_genera"] as? [String] ?? [])
                .map(genusLabel).joined(separator: "、")
            let candidates = genusCandidates(genusProxy["candidate_scores"])
            HStack(alignment: .top, spacing: 8) {
                Image(systemName: "cloud").foregroundStyle(coolAccent)
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 6) {
                        Text("云属候选（代理）").font(.system(size: 10, weight: .semibold))
                        if label != "unknown" {
                            Text(genusLabel(label)).font(.system(size: 10, weight: .semibold))
                        } else {
                            Text(compatible.isEmpty ? "未定" : "兼容：\(compatible)")
                                .font(.system(size: 10, weight: .medium))
                        }
                        Spacer(minLength: 4)
                        if let confidence = genusProxy["confidence"] as? NSNumber,
                           confidence.doubleValue.isFinite {
                            Text("\(Int((confidence.doubleValue * 100).rounded()))%")
                                .font(.system(size: 9, weight: .medium).monospacedDigit())
                                .foregroundStyle(.secondary)
                        }
                    }
                    if let candidates {
                        Text("候选：\(candidates)")
                            .font(.system(size: 9))
                            .foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    Text("依据压力层、相态与云层几何的模型代理，不代表卫星或人工云属观测。")
                        .font(.system(size: 9))
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .padding(.horizontal, 9)
            .padding(.vertical, 7)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.secondary.opacity(0.055), in: RoundedRectangle(cornerRadius: 6))
            .accessibilityElement(children: .combine)
        }
    }

    private func genusCandidates(_ scores: Any?) -> String? {
        guard let scores = scores as? JSONObject else { return nil }
        let ranked = scores.sorted {
            (($0.value as? NSNumber)?.doubleValue ?? -1) > (($1.value as? NSNumber)?.doubleValue ?? -1)
        }
        let labels = ranked.prefix(3).compactMap { key, value -> String? in
            guard let score = value as? NSNumber, score.doubleValue.isFinite else { return nil }
            return "\(genusLabel(key)) \(Int((score.doubleValue * 100).rounded()))%"
        }
        return labels.isEmpty ? nil : labels.joined(separator: " · ")
    }

    // MARK: drawing

    private func draw(context: GraphicsContext, size: CGSize, data: CrossSectionData, scale: Double) {
        let top = 10.0, bottom = size.height - 24
        guard bottom > top + 20 else { return }
        let maxHeight = 18_000.0
        let labelSize = CGFloat(min(14, max(10, 10 * scale)))
        let strokeScale = CGFloat(scale)
        let targetAltitudeStep = maxHeight * 34 / Double(bottom - top)
        let altitudeStep = [1000.0, 1500, 2000, 3000, 5000, 7500, 10_000]
            .first(where: { $0 >= targetAltitudeStep }) ?? maxHeight
        let altitudes = Array(stride(from: 0.0, through: maxHeight, by: altitudeStep))
        func altitudeLabel(_ altitude: Double) -> Text {
            let label = altitude.truncatingRemainder(dividingBy: 1000) == 0
                ? "\(Int(altitude / 1000))km" : String(format: "%.1fkm", altitude / 1000)
            return Text(label).font(.system(size: labelSize)).foregroundStyle(.secondary)
        }
        // Fractional altitude ticks widen at higher zoom levels.
        let labelWidth = altitudes.map {
            context.resolve(altitudeLabel($0)).measure(in: size).width
        }.max() ?? 0
        let left = max(40, ceil(labelWidth) + 8), right = size.width - 12
        guard right > left + 20 else { return }
        let maxDistance = max(1, data.distances.last ?? 1)
        let x = { (distance: Double) in left + distance / maxDistance * (right - left) }
        let y = { (height: Double) in top + (1 - min(max(height, 0), maxHeight) / maxHeight) * (bottom - top) }

        // Atmospheric sky gradient backdrop inside the plot frame
        let plotRect = CGRect(x: left, y: top, width: right - left, height: bottom - top)
        let plotPath = Path(roundedRect: plotRect, cornerRadius: 8 * strokeScale)
        let skyGradient = Gradient(colors: scheme == .dark
            ? [Color(red: 0.09, green: 0.13, blue: 0.22).opacity(0.75),
               Color(red: 0.14, green: 0.19, blue: 0.28).opacity(0.45),
               Color(red: 0.28, green: 0.20, blue: 0.19).opacity(0.32)]
            : [Color(red: 0.85, green: 0.91, blue: 0.97).opacity(0.72),
               Color(red: 0.94, green: 0.95, blue: 0.98).opacity(0.52),
               Color(red: 0.99, green: 0.93, blue: 0.87).opacity(0.55)])
        context.fill(
            plotPath,
            with: .linearGradient(skyGradient,
                                  startPoint: CGPoint(x: left, y: top),
                                  endPoint: CGPoint(x: left, y: bottom))
        )
        context.stroke(plotPath, with: .color(Color.secondary.opacity(scheme == .dark ? 0.28 : 0.22)),
                       lineWidth: 0.9 * strokeScale)

        // Subtle golden-amber solar illumination corridor above requiredASL
        if data.requiredASL.count == data.distances.count {
            var sunlit = Path()
            sunlit.move(to: CGPoint(x: left, y: top))
            sunlit.addLine(to: CGPoint(x: right, y: top))
            for index in data.distances.indices.reversed() {
                sunlit.addLine(to: CGPoint(x: x(data.distances[index]), y: y(data.requiredASL[index])))
            }
            sunlit.closeSubpath()
            let sunGrad = Gradient(colors: [
                Color(red: 0.98, green: 0.69, blue: 0.30).opacity(scheme == .dark ? 0.03 : 0.04),
                Color(red: 0.96, green: 0.57, blue: 0.22).opacity(scheme == .dark ? 0.14 : 0.13),
            ])
            context.fill(
                sunlit,
                with: .linearGradient(sunGrad,
                                      startPoint: CGPoint(x: left, y: top),
                                      endPoint: CGPoint(x: left, y: bottom))
            )
        }

        for altitude in altitudes {
            let lineY = y(altitude)
            var grid = Path()
            grid.move(to: CGPoint(x: left, y: lineY))
            grid.addLine(to: CGPoint(x: right, y: lineY))
            context.stroke(grid, with: .color(Color.secondary.opacity(0.18)), lineWidth: 0.6 * strokeScale)
            context.draw(altitudeLabel(altitude),
                         at: CGPoint(x: left - 5, y: lineY), anchor: .trailing)
        }
        // distance grid every 4th sample
        for (index, distance) in data.distances.enumerated() where index % 4 == 0 {
            let lineX = x(distance)
            var grid = Path()
            grid.move(to: CGPoint(x: lineX, y: top))
            grid.addLine(to: CGPoint(x: lineX, y: bottom))
            context.stroke(grid, with: .color(Color.secondary.opacity(0.14)),
                           style: StrokeStyle(lineWidth: 0.6 * strokeScale, dash: [3 * strokeScale, 4 * strokeScale]))
            context.draw(Text("\(Int(distance))km").font(.system(size: labelSize)).foregroundStyle(.secondary),
                         at: CGPoint(x: lineX, y: bottom + 10),
                         anchor: distance == maxDistance ? .trailing : .center)
        }

        // terrain fill (vertical earth-slate gradient) + crisp ridge line + observer badge
        if data.terrain.count == data.distances.count {
            var fill = Path()
            fill.move(to: CGPoint(x: x(data.distances[0]), y: y(data.terrain[0])))
            for index in data.distances.indices.dropFirst() {
                fill.addLine(to: CGPoint(x: x(data.distances[index]), y: y(data.terrain[index])))
            }
            fill.addLine(to: CGPoint(x: right, y: bottom))
            fill.addLine(to: CGPoint(x: left, y: bottom))
            fill.closeSubpath()
            let terrainMinY = data.terrain.map { y($0) }.min() ?? bottom
            let terrainGrad = Gradient(colors: scheme == .dark
                ? [Color(red: 0.36, green: 0.42, blue: 0.44).opacity(0.62),
                   Color(red: 0.20, green: 0.24, blue: 0.28).opacity(0.85)]
                : [Color(red: 0.46, green: 0.51, blue: 0.49).opacity(0.38),
                   Color(red: 0.32, green: 0.37, blue: 0.36).opacity(0.58)])
            context.fill(
                fill,
                with: .linearGradient(terrainGrad,
                                      startPoint: CGPoint(x: left, y: terrainMinY),
                                      endPoint: CGPoint(x: left, y: bottom))
            )
            var line = Path()
            line.move(to: CGPoint(x: x(data.distances[0]), y: y(data.terrain[0])))
            for index in data.distances.indices.dropFirst() {
                line.addLine(to: CGPoint(x: x(data.distances[index]), y: y(data.terrain[index])))
            }
            context.stroke(line, with: .color(Color(red: 0.41, green: 0.46, blue: 0.45)),
                           lineWidth: 1.8 * strokeScale)
            let observer = CGPoint(x: x(data.distances[0]), y: y(data.terrain[0]))
            let haloSize = 15 * strokeScale
            let halo = CGRect(x: observer.x - haloSize / 2, y: observer.y - haloSize / 2,
                              width: haloSize, height: haloSize)
            context.fill(Path(ellipseIn: halo), with: .color(Color(red: 0.95, green: 0.49, blue: 0.26).opacity(0.24)))
            let dotSize = 9 * strokeScale
            let dot = CGRect(x: observer.x - dotSize / 2, y: observer.y - dotSize / 2,
                             width: dotSize, height: dotSize)
            context.fill(Path(ellipseIn: dot), with: .color(Color(red: 0.95, green: 0.49, blue: 0.26)))
            context.stroke(Path(ellipseIn: dot), with: .color(.white.opacity(0.9)), lineWidth: 1.4 * strokeScale)
            context.draw(Text("观测点").font(.system(size: labelSize, weight: .semibold)).foregroundStyle(.primary),
                         at: CGPoint(x: observer.x + dotSize * 1.1, y: observer.y - 9 * strokeScale), anchor: .leading)
        }

        // Use the model's cloud base/top envelope for each pressure family with rounded strata shading.
        let sampleCount = Swift.max(1, data.distances.count - 1)
        let columnSpan = (right - left) / Double(sampleCount)
        let cellWidth = Swift.max(6.0, columnSpan * 0.92)
        func scalar(_ grid: [[Double?]], _ sample: Int, _ level: Int) -> Double? {
            guard grid.indices.contains(sample), grid[sample].indices.contains(level) else { return nil }
            return grid[sample][level]
        }
        func integer(_ grid: [[Int]], _ sample: Int, _ level: Int) -> Int? {
            guard grid.indices.contains(sample), grid[sample].indices.contains(level) else { return nil }
            return grid[sample][level]
        }
        for i in data.distances.indices {
            for family in 1...3 {
                let cloudyLevels = data.levels.indices.filter { level in
                    guard integer(data.familyCodes, i, level) == family else { return false }
                    let amount = scalar(data.amounts, i, level)
                        ?? max(scalar(data.presence, i, level) ?? 0,
                               (scalar(data.fractions, i, level) ?? 0) / 100)
                    return amount >= 0.06
                }
                guard !cloudyLevels.isEmpty else { continue }

                let bases = cloudyLevels.compactMap { scalar(data.layerBases, i, $0) }
                let tops = cloudyLevels.compactMap { scalar(data.layerTops, i, $0) }
                let heights = cloudyLevels.compactMap { scalar(data.heights, i, $0) }
                let layerBase = bases.min() ?? heights.min().map { $0 - 700 }
                let layerTop = tops.max() ?? heights.max().map { $0 + 700 }
                guard let layerBase, let layerTop, layerTop > layerBase else { continue }

                let amounts = cloudyLevels.compactMap {
                    scalar(data.amounts, i, $0)
                        ?? max(scalar(data.presence, i, $0) ?? 0,
                               (scalar(data.fractions, i, $0) ?? 0) / 100)
                }
                let presence = cloudyLevels.compactMap { scalar(data.presence, i, $0) }.max()
                let humidity = cloudyLevels.compactMap { scalar(data.rh, i, $0) }.max()
                let fraction = cloudyLevels.compactMap { scalar(data.fractions, i, $0) }.max()
                let status = cloudyLevels.compactMap { integer(data.statuses, i, $0) }.max() ?? 0

                let centerX = x(data.distances[i])
                let rectLeft = max(left, centerX - cellWidth / 2)
                let rectRight = min(right, centerX + cellWidth / 2)
                let terrainY = data.terrain.indices.contains(i) ? y(data.terrain[i]) : bottom
                let rectTop = max(top, y(layerTop))
                let rectBottom = min(terrainY, y(layerBase))
                guard rectRight > rectLeft, rectBottom > rectTop else { continue }
                let rect = CGRect(x: rectLeft, y: rectTop,
                                  width: rectRight - rectLeft, height: rectBottom - rectTop)
                let radius = min(4.5 * strokeScale, min(rect.width, rect.height) * 0.35)
                let shape = Path(roundedRect: rect, cornerRadius: radius)
                if let fill = cloudCellFill(amount: amounts.max(), presence: presence,
                                            rh: humidity, fraction: fraction, family: family) {
                    let topColor = fill.opacity(0.82)
                    let bottomColor = status == 3
                        ? Color(red: 0.97, green: 0.52, blue: 0.28).opacity(0.62)
                        : fill
                    context.fill(
                        shape,
                        with: .linearGradient(Gradient(colors: [topColor, bottomColor]),
                                              startPoint: CGPoint(x: rect.midX, y: rect.minY),
                                              endPoint: CGPoint(x: rect.midX, y: rect.maxY))
                    )
                }
                if status == 3 {
                    context.stroke(shape, with: .color(Color(red: 0.96, green: 0.43, blue: 0.20)),
                                   lineWidth: 1.9 * strokeScale)
                } else if status == 2 {
                    context.stroke(shape, with: .color(Color(red: 0.85, green: 0.56, blue: 0.22)),
                                   style: StrokeStyle(lineWidth: 1.4 * strokeScale,
                                                      dash: [4 * strokeScale, 3 * strokeScale]))
                }
            }
        }

        // temperature layer lines colored by level average temperature
        for l in data.levels.indices {
            var points: [CGPoint] = []
            var temps: [Double] = []
            for i in data.distances.indices {
                guard let height = data.heights[i][l] else { continue }
                points.append(CGPoint(x: x(data.distances[i]), y: y(height)))
                if let temp = data.temps[i][l] { temps.append(temp) }
            }
            guard points.count > 1 else { continue }
            var line = Path()
            line.move(to: points[0])
            points.dropFirst().forEach { line.addLine(to: $0) }
            let average = temps.isEmpty ? 0 : temps.reduce(0, +) / Double(temps.count)
            let color = average < -30 ? Color(red: 0.55, green: 0.40, blue: 0.45)
                : average < 0 ? Color(red: 0.69, green: 0.42, blue: 0.36)
                : Color(red: 0.78, green: 0.31, blue: 0.22)
            context.stroke(line, with: .color(color.opacity(0.62)), lineWidth: 1.1 * strokeScale)
            if l % 2 == 0, l < data.levels.count, let last = points.last {
                context.draw(Text("\(data.levels[l])hPa").font(.system(size: labelSize)).foregroundStyle(.secondary),
                             at: CGPoint(x: last.x - 5 * strokeScale, y: last.y - 6 * strokeScale),
                             anchor: .trailing)
            }
        }

        // required illumination threshold (ASL), dashed amber
        if data.requiredASL.count == data.distances.count {
            var line = Path()
            line.move(to: CGPoint(x: x(data.distances[0]), y: y(data.requiredASL[0])))
            for index in data.distances.indices.dropFirst() {
                line.addLine(to: CGPoint(x: x(data.distances[index]), y: y(data.requiredASL[index])))
            }
            context.stroke(line, with: .color(Color(red: 0.83, green: 0.55, blue: 0.22)),
                           style: StrokeStyle(lineWidth: 2.2 * strokeScale,
                                              dash: [7 * strokeScale, 5 * strokeScale]))
        }

        // theoretical target cloud base / top markers
        for target in data.targets {
            guard let distance = number(target["distance_km"]),
                  let base = number(target["cloud_base_asl_m"]) else { continue }
            let basePoint = CGPoint(x: x(distance), y: y(base))
            let markerSize = 8 * strokeScale
            context.fill(Path(ellipseIn: CGRect(x: basePoint.x - markerSize / 2,
                                                y: basePoint.y - markerSize / 2,
                                                width: markerSize, height: markerSize)),
                         with: .color(warm))
            if let top = number(target["cloud_top_asl_m"]) {
                let topPoint = CGPoint(x: x(distance), y: y(top))
                context.fill(Path(ellipseIn: CGRect(x: topPoint.x - markerSize / 2,
                                                    y: topPoint.y - markerSize / 2,
                                                    width: markerSize, height: markerSize)),
                             with: .color(coolAccent))
            }
        }
    }

    /// Port of the Web cloudFill (NationalCrossSection.svelte): cloud amount over
    /// presence/fraction fallback, humidity tint below the cloud threshold, family colors.
    private func cloudCellFill(amount: Double?, presence: Double?, rh: Double?,
                               fraction: Double?, family: Int) -> Color? {
        let cloudAmount = amount ?? Swift.max(presence ?? 0, (fraction ?? 0) / 100)
        let humidity = Swift.max(0, (rh ?? 0) - 55) / 45
        if cloudAmount < 0.06 {
            if let fraction, fraction <= 0 { return nil }
            if humidity < 0.06 { return nil }
            return Color(red: 112 / 255, green: 151 / 255, blue: 126 / 255)
                .opacity(min(0.34, 0.04 + humidity * 0.26))
        }
        let alpha = min(0.82, 0.10 + cloudAmount * 0.68)
        switch family {
        case 1: return Color(red: 116 / 255, green: 157 / 255, blue: 194 / 255).opacity(alpha)
        case 2: return Color(red: 105 / 255, green: 154 / 255, blue: 142 / 255).opacity(alpha)
        default: return Color(red: 126 / 255, green: 136 / 255, blue: 145 / 255).opacity(alpha)
        }
    }

    private func offsetLabel(_ value: Any?) -> String {
        guard let value = value as? NSNumber, value.doubleValue.isFinite else { return "—" }
        let minutes = Int(value.doubleValue)
        return "\(minutes > 0 ? "+" : "")\(minutes) 分"
    }

    // MARK: legend & target diagnosis

    private var legend: some View {
        VStack(alignment: .leading, spacing: 5) {
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 160), alignment: .leading)],
                      alignment: .leading, spacing: 8) {
                legendChip("高云压力族", Color(red: 116 / 255, green: 157 / 255, blue: 194 / 255))
                legendChip("中云压力族", Color(red: 105 / 255, green: 154 / 255, blue: 142 / 255))
                legendChip("低云压力族", Color(red: 126 / 255, green: 136 / 255, blue: 145 / 255))
                legendChip("湿润未饱和", Color(red: 112 / 255, green: 151 / 255, blue: 126 / 255))
                legendLine("温度层线", Color(red: 0.69, green: 0.42, blue: 0.36), dashed: false)
                legendLine("地形", Color(red: 0.41, green: 0.45, blue: 0.44), dashed: false)
                legendDot("云底", warm)
                legendDot("云顶", coolAccent)
                legendLine("理论照明基准（ASL）", Color(red: 0.83, green: 0.55, blue: 0.22), dashed: true)
                legendFrame("受光且可见候选（未评估云遮挡）", Color(red: 0.94, green: 0.42, blue: 0.21), dashed: false)
                legendFrame("达到受光阈值但不可见", Color(red: 0.83, green: 0.55, blue: 0.22), dashed: true)
            }
            Text("图层数值为对应模式（GFS/EC）各自的场；未着色不代表晴空（缺测或低于阈值），低云压力族仅在近场距离门限内排除；橙色状态未评估云体遮挡。")
                .font(.system(size: 9)).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .font(.system(size: 9)).foregroundStyle(.secondary)
    }

    private func legendChip(_ title: String, _ color: Color) -> some View {
        HStack(spacing: 4) {
            Rectangle().fill(color).frame(width: 12, height: 7).cornerRadius(1.5)
            Text(title)
        }
    }

    private func legendLine(_ title: String, _ color: Color, dashed: Bool) -> some View {
        HStack(spacing: 4) {
            Rectangle().fill(Color.clear).frame(width: 12, height: 0)
                .overlay { Rectangle().stroke(color, style: StrokeStyle(lineWidth: 1.6, dash: dashed ? [4, 3] : [])) }
                .frame(height: 7)
            Text(title)
        }
    }

    private func legendDot(_ title: String, _ color: Color) -> some View {
        HStack(spacing: 4) {
            Circle().fill(color).frame(width: 7, height: 7)
            Text(title)
        }
    }

    private func legendFrame(_ title: String, _ color: Color, dashed: Bool) -> some View {
        HStack(spacing: 4) {
            Rectangle().fill(Color.clear)
                .overlay { Rectangle().stroke(color, style: StrokeStyle(lineWidth: 1.4, dash: dashed ? [4, 3] : [])) }
                .frame(width: 11, height: 7)
            Text(title)
        }
    }

    /// Theoretical target status summary (Web target-strip parity).
    private func targetStrip(_ targets: [JSONObject]) -> some View {
        let burnable = targets.filter { $0["status"] as? String == "burnable" }
        return Group {
            if !targets.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    Text("受光与几何/地形可见候选（未评估云遮挡）")
                        .font(.system(size: 10, weight: .medium)).foregroundStyle(.primary)
                    if !burnable.isEmpty {
                        LazyVGrid(columns: [GridItem(.adaptive(minimum: 232), spacing: 6)], spacing: 6) {
                            ForEach(Array(burnable.prefix(8).enumerated()), id: \.offset) { _, target in
                                targetChip(target)
                            }
                        }
                    } else if let diagnostic = targets.first(where: { $0["status"] as? String != "burnable" }) {
                        Text("当前太阳方向没有可烧目标云：\(targetFamily(diagnostic))，\(diagnostic["status_reason"] as? String ?? "状态原因未提供")")
                            .font(.system(size: 9)).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
    }

    private func targetChip(_ target: JSONObject) -> some View {
        let family = targetFamily(target)
        let phase = (target["cloud_phase"] as? JSONObject)?["label"] as? String ?? "unknown"
        let phaseText = phase == "ice" ? "冰相" : phase == "water" ? "水相" : phase == "mixed" ? "混合相" : "未知"
        let distance = number(target["distance_km"]).map { String(format: "%.0f", $0) } ?? "—"
        let base = number(target["cloud_base_asl_m"]).map { String(format: "%.1f", $0 / 1000) } ?? "—"
        let top = number(target["cloud_top_asl_m"]).map { String(format: "%.1f", $0 / 1000) } ?? "—"
        let text = "\(family) · \(phaseText) · \(distance) km · ASL \(base)–\(top) km"
        return Text(text)
            .font(.system(size: 9)).monospacedDigit()
            .padding(.horizontal, 6).padding(.vertical, 3)
            .background(Color(red: 0.94, green: 0.42, blue: 0.21).opacity(0.08),
                        in: RoundedRectangle(cornerRadius: 4))
            .overlay { RoundedRectangle(cornerRadius: 4).stroke(Color(red: 0.94, green: 0.42, blue: 0.21).opacity(0.42), lineWidth: 1) }
            .help(target["status_reason"] as? String ?? text)
    }

    private func targetFamily(_ target: JSONObject) -> String {
        switch target["pressure_family"] as? String {
        case "high": return "高云压力族"
        case "middle": return "中云压力族"
        case "low": return "低云压力族"
        default: return target["pressure_family_label"] as? String ?? "云层"
        }
    }

    private func diagnosticChip(_ title: String, _ value: Any?, suffix: String = "") -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.system(size: 8)).foregroundStyle(.secondary)
            Text(title == "有效时次" ? (value as? String ?? "—")
                 : diagnosticValue(value, digits: 1, suffix: suffix))
                .font(.system(size: 10, weight: .medium)).monospacedDigit()
        }
    }
}

/// Structured cloud-genus / cloud-phase diagnosis (plan item point-9). Reads the backend
/// proxy objects as-is; GFS point reports carry the fixed "not resolved" phase placeholder,
/// which is shown as 未解析 instead of a guessed phase (plan item point-15).
private struct WorkGenusPhaseDiagnosis: View {
    let report: JSONObject

    var body: some View {
        WorkDiagnosticGroup(title: "云属与相态（模型代理 · 不参与评分）") {
            if let genus = report["cloud_genus_proxy"] as? JSONObject {
                let label = genus["label"] as? String ?? "unknown"
                if label != "unknown" {
                    HStack(alignment: .firstTextBaseline, spacing: 10) {
                        Text("云属代理").foregroundStyle(.secondary)
                        Spacer(minLength: 10)
                        Text(genusLabel(label))
                            .multilineTextAlignment(.trailing)
                        Text(percent(genus["confidence"])).monospacedDigit().foregroundStyle(.secondary)
                    }.font(.system(size: 10))
                    if let candidates = topCandidates(genus["candidate_scores"], limit: 3) {
                        HStack(alignment: .firstTextBaseline, spacing: 10) {
                            Text("候选 Top3").foregroundStyle(.secondary)
                            Spacer(minLength: 10)
                            Text(candidates).multilineTextAlignment(.trailing).lineLimit(2)
                        }.font(.system(size: 10))
                    }
                } else if let compatible = compatibleGenera(report) {
                    HStack(alignment: .firstTextBaseline, spacing: 10) {
                        Text("云属未定").foregroundStyle(.secondary)
                        Spacer(minLength: 10)
                        Text("压力族仅兼容 \(compatible)").multilineTextAlignment(.trailing).lineLimit(2)
                    }.font(.system(size: 10))
                }
                diagnosticRow("推断依据", genus["source"])
                diagnosticRow("评分影响", (genus["scoring_effect"] as? Bool) == false ? "不参与评分" : "—")
            } else {
                diagnosticRow("云属代理", "—")
            }
            phaseRows(report["cloud_phase_proxy"])
        }
    }

    @ViewBuilder
    private func phaseRows(_ phase: Any?) -> some View {
        if let phase = phase as? JSONObject {
            let label = phase["label"] as? String ?? "unknown"
            if label == "unknown", phase["source"] as? String == "not-resolved-for-native-burnable-target" {
                diagnosticRow("相态", "未解析（GFS 桌面点预测不解析目标云相态）")
            } else {
                let ice = phase["ice_fraction"] as? NSNumber
                let iceText = (ice != nil && ice!.doubleValue.isFinite)
                    ? " · 冰含量占比 \(Int((ice!.doubleValue * 100).rounded()))%" : ""
                diagnosticRow("相态", "\(phaseLabel(label))\(iceText)")
                diagnosticRow("相态置信度", phase["confidence"], percentValue: true)
            }
        } else if phase == nil {
            diagnosticRow("相态", "—")
        } else {
            diagnosticRow("相态", phase)
        }
    }

    private func topCandidates(_ scores: Any?, limit: Int) -> String? {
        guard let scores = scores as? JSONObject, !scores.isEmpty else { return nil }
        let ranked = scores.sorted { ($0.value as? NSNumber)?.doubleValue ?? -1 > ($1.value as? NSNumber)?.doubleValue ?? -1 }
        let top = ranked.prefix(limit).compactMap { key, value -> String? in
            guard let number = value as? NSNumber, number.doubleValue.isFinite else { return nil }
            return "\(genusLabel(key)) \(Int((number.doubleValue * 100).rounded()))%"
        }
        return top.isEmpty ? nil : top.joined(separator: " · ")
    }

    private func compatibleGenera(_ report: JSONObject) -> String? {
        guard let diagnosis = report["dominant_burnable_cloud_genus_diagnosis"] as? JSONObject,
              let genera = diagnosis["compatible_genera"] as? [String], !genera.isEmpty else { return nil }
        return genera.map(genusLabel).joined(separator: " / ")
    }
}

/// B1 天空语义：观测窗口时段条（方案 UI_REFRESH B1）——渐变窗口区间 +
/// 最佳时刻点 + 观测点时区倒计时；无窗口数据时回退文字行。
struct SkyTimeline: View {
    let report: JSONObject?
    let timezone: String
    @Environment(\.colorScheme) private var scheme

    private func minutes(_ hhmm: String) -> Int? {
        let parts = hhmm.split(separator: ":")
        guard parts.count == 2, let h = Int(parts[0]), let m = Int(parts[1]),
              (0...23).contains(h), (0...59).contains(m) else { return nil }
        return h * 60 + m
    }

    /// 观测点时区下 HH:mm 对应的时刻；早于窗口起点视为次日凌晨（跨午夜）。
    private func pointDate(_ minutesValue: Int, start: Int) -> Date {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: timezone) ?? .current
        var components = calendar.dateComponents([.year, .month, .day], from: Date())
        components.hour = minutesValue / 60
        components.minute = minutesValue % 60
        let today = calendar.date(from: components) ?? Date()
        if minutesValue >= start { return today }
        return calendar.date(byAdding: .day, value: 1, to: today) ?? today
    }

    private func countdown(_ interval: TimeInterval) -> String {
        let total = max(Int(interval) / 60, 0)
        let hours = total / 60
        let minutesValue = total % 60
        return hours > 0 ? "\(hours) 小时 \(minutesValue) 分" : "\(minutesValue) 分钟"
    }

    var body: some View {
        if let window = report?["timing_window"] as? JSONObject,
           let startRaw = window["start_time_local"] as? String,
           let endRaw = window["end_time_local"] as? String,
           let start = minutes(startRaw), let end = minutes(endRaw) {
            let bestRaw = report?["best_time_local"] as? String
            let best = bestRaw.flatMap(minutes)
            let span = (end - start <= 0) ? end - start + 1440 : end - start
            let now = Date()
            let startDate = pointDate(start, start: start)
            let endDate = pointDate(end % 1440, start: start)
            let inWindow = now >= startDate && now <= endDate
            let bestDate = best.map { pointDate($0, start: start) }
            VStack(alignment: .leading, spacing: 5) {
                GeometryReader { geo in
                    ZStack(alignment: .leading) {
                        Capsule().fill(.quaternary.opacity(0.5))
                        // 窗口区间：按窗口跨度在轴上定位（跨午夜已归一化）。
                        Capsule()
                            .fill(FSDesign.accentGradient(dark: scheme == .dark))
                            .frame(width: geo.size.width * CGFloat(span) / CGFloat(max(span, 1)))
                        if let best {
                            Circle()
                                .fill(Color.white)
                                .frame(width: 9, height: 9)
                                .overlay { Circle().strokeBorder(FSDesign.chipForeground("done", dark: scheme == .dark), lineWidth: 1.5) }
                                .offset(x: geo.size.width * CGFloat((best - start <= 0 ? best + 1440 : best) - start) / CGFloat(span) - 4.5)
                        }
                    }
                }
                .frame(height: 10)
                HStack {
                    Text(startRaw).monospacedDigit()
                    Spacer()
                    if let bestRaw, let bestDate {
                        Text(inWindow
                             ? "最佳时刻 \(bestRaw) · \(countdown(bestDate.timeIntervalSinceNow))后"
                             : "最佳时刻 \(bestRaw)")
                            .foregroundStyle(FSDesign.chipForeground("done", dark: scheme == .dark))
                    }
                    Spacer()
                    Text(endRaw).monospacedDigit()
                }
                .font(.system(size: 9)).monospacedDigit().foregroundStyle(.secondary)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("观测窗口 \(startRaw) 至 \(endRaw)\(bestRaw.map { "，最佳时刻 \($0)" } ?? "")")
            }
        } else {
            Text(L10n.tf("推荐窗口 %@", windowLabel(report)))
                .fireSkyFont(size: 10, relativeTo: .caption).foregroundStyle(.secondary)
        }
    }
}

private func metricValue(_ key: String, _ report: JSONObject?) -> String {
    guard let report else { return "—" }
    let components = report["quality_components"] as? JSONObject ?? [:]
    switch key {
    case "aod": return format(components["quality_aod550"] ?? report["quality_aod550"])
    case "cloud_layer": return cloudLayerMetricValue(report)
    case "duration": return format(report["duration_minutes"] ?? components["duration_minutes"], digits: 0)
    case "confidence": return percent(report["confidence_score"])
    case "convective": return format(report["convective_risk"])
    case "burnable": return percent(components["burnable_cloud_coverage_effective"] ?? report["burnable_cloud_coverage_effective"])
    // 0–1 比例字段统一百分比口径，与逐时台账 ratioText、Web MetricsGrid score() 一致。
    case "material": return ratioText(components["cloud_material"])
    case "brightness": return ratioText(components["brightness_color"])
    case "incoming": return format(components["incoming_aod_illumination_factor"])
    case "contrast": return format(components["observer_aod_contrast_factor"])
    default: return "—"
    }
}

private func cloudLayerMetricValue(_ report: JSONObject?) -> String {
    guard let report,
          let base = number(report["dominant_burnable_cloud_base_agl_mean_m"]),
          let top = number(report["dominant_burnable_cloud_top_agl_mean_m"]),
          top >= base else {
        return "—"
    }
    return String(format: "%.1f–%.1f km", base / 1000, top / 1000)
}

private func summaryText(_ report: JSONObject) -> String {
    if let text = report["summary"] as? String, !text.isEmpty { return text }
    if let values = report["summary"] as? [Any] {
        let lines = values.compactMap { value -> String? in
            guard let text = value as? String, !text.isEmpty else { return nil }
            return "• \(text)"
        }
        if !lines.isEmpty { return lines.joined(separator: "\n") }
    }
    return "暂无文字摘要"
}

private func phaseLabel(_ value: String) -> String {
    switch value {
    case "ice": return "冰相成分"
    case "water": return "液态水成分"
    case "mixed": return "冰水混合成分"
    default: return "相态未知"
    }
}

/// Cycle chip text, e.g. "2026-11-12 06:00Z"; model_cycles values are UTC ISO strings.
private func cycleLabel(_ model: String, _ report: JSONObject?) -> String? {
    let cycles = report?["model_cycles"] as? JSONObject
    let value = cycles?[model] as? String ?? report?["cycle_time_utc"] as? String
    guard let value, value.count >= 16 else { return nil }
    let text = value.prefix(16).replacingOccurrences(of: "T", with: " ")
    return value.contains("+00:00") || value.hasSuffix("Z") ? text + "Z" : text
}

/// One-line model source note, mirroring the Web source description.
private func modelSourceNote(_ model: String) -> String {
    model == "ec"
        ? L10n.text("EC 公共 Open Data · TCC + 压力层 RH 云柱代理 · 不含 GFS 云场 · CAMS 气溶胶",
                    "EC public Open Data · TCC + pressure-level RH cloud-column proxy · no GFS cloud field · CAMS aerosol")
        : L10n.text("GFS 0.25° 全量云场评分 · CAMS 气溶胶",
                    "GFS 0.25° full cloud-field scoring · CAMS aerosol")
}
