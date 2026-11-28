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
                     detail: store.usesGeosFP
                        ? L10n.t("GEOS-FP · NASA 免密钥")
                        : L10n.t(store.camsConfigured ? "CAMS 已配置" : "CAMS 可选，仍可使用本地数据"),
                     action: store.usesGeosFP
                        ? L10n.t("切换来源")
                        : L10n.t(store.camsConfigured ? "查看配置" : "配置 CAMS")) {
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
        if snapshots.count == 2,
           let gfsPeak = comparison.peak("gfs"),
           let ecPeak = comparison.peak("ec"),
           abs(gfsPeak - ecPeak) >= 0.45 {
            result.append(L10n.text(
                String(format: "模式分歧提示：GFS (%.2f) 与 EC (%.2f) 差值较大，临近时建议切「卫星实况」核对云区动向。", gfsPeak, ecPeak),
                String(format: "Model divergence: GFS (%.2f) and EC (%.2f) diverge noticeably; check Satellite Nowcast close to the event.", gfsPeak, ecPeak)
            ))
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

    private var withinNowcastWindow: Bool {
        guard let raw = reminderBest?.time else { return false }
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let date = iso.date(from: raw) ?? ISO8601DateFormatter().date(from: raw)
        guard let date else { return false }
        return abs(date.timeIntervalSinceNow) <= 4 * 3600
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
                VStack(alignment: .leading, spacing: 5) {
                    HStack(alignment: .center, spacing: 8) {
                        Image(systemName: statusSymbol)
                            .fireSkyFont(size: 12.5, weight: .semibold, relativeTo: .caption)
                            .foregroundStyle(warm)
                        Text(L10n.t("行动摘要"))
                            .fireSkyFont(size: 10, weight: .semibold, relativeTo: .caption)
                            .foregroundStyle(warm)
                            .padding(.horizontal, 6).padding(.vertical, 2)
                            .background(warm.opacity(0.12), in: Capsule())
                        Text(headline)
                            .fireSkyFont(size: 14, weight: .semibold, relativeTo: .title3)
                            .foregroundStyle(.primary)
                            .fixedSize(horizontal: false, vertical: true)
                            .accessibilityLabel(L10n.tf("结论：%@", headline))
                        Spacer(minLength: 8)
                        if !snapshots.isEmpty {
                            Menu {
                                Button(L10n.text("保存为追霞海报 (PNG)…", "Save Glow Poster (PNG)…")) {
                                    SummaryExport.savePoster(store: store, comparison: comparison)
                                }
                                Button(L10n.text("复制追霞海报图片", "Copy Glow Poster Image")) {
                                    SummaryExport.copyPoster(store: store, comparison: comparison)
                                }
                                Divider()
                                Button(L10n.text("保存为文本文件", "Save as text")) {
                                    SummaryExport.save(exportText, suggestedFilename: exportFilename)
                                }
                                Button(L10n.text("复制摘要", "Copy summary")) {
                                    SummaryExport.copy(exportText)
                                }
                            } label: {
                                Label(L10n.text("导出/分享", "Export"), systemImage: "square.and.arrow.up")
                            }
                            .buttonStyle(.borderless)
                            .font(.system(size: 11))
                            .help(L10n.text("导出追霞 PNG 海报卡或观测摘要文本。",
                                            "Export a shareable PNG glow poster or text summary."))
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
                    if !timingText.isEmpty || reminderBest != nil || withinNowcastWindow {
                        ViewThatFits(in: .horizontal) {
                            HStack(alignment: .firstTextBaseline, spacing: 10) {
                                if !timingText.isEmpty {
                                    Text(timingText)
                                        .fireSkyFont(size: 11.5).monospacedDigit()
                                        .foregroundStyle(.secondary)
                                        .lineLimit(1)
                                        .accessibilityLabel(L10n.tf("时间与窗口：%@", timingText))
                                }
                                if let reminderBest {
                                    WorkReminderControl(bestTime: reminderBest.time,
                                                        model: reminderBest.model,
                                                        detail: timingText)
                                }
                                Spacer(minLength: 0)
                                if withinNowcastWindow {
                                    nowcastShortcutButton
                                }
                            }
                            VStack(alignment: .leading, spacing: 4) {
                                if !timingText.isEmpty {
                                    Text(timingText)
                                        .fireSkyFont(size: 11.5).monospacedDigit()
                                        .foregroundStyle(.secondary)
                                        .fixedSize(horizontal: false, vertical: true)
                                        .accessibilityLabel(L10n.tf("时间与窗口：%@", timingText))
                                }
                                HStack(spacing: 8) {
                                    if let reminderBest {
                                        WorkReminderControl(bestTime: reminderBest.time,
                                                            model: reminderBest.model,
                                                            detail: timingText)
                                    }
                                    if withinNowcastWindow {
                                        nowcastShortcutButton
                                    }
                                }
                            }
                        }
                    }
                    if !notes.isEmpty {
                        VStack(alignment: .leading, spacing: 2) {
                            ForEach(notes, id: \.self) { note in
                                Text(note)
                                    .fireSkyFont(size: 10.5, relativeTo: .caption)
                                    .foregroundStyle(.orange)
                                    .fixedSize(horizontal: false, vertical: true)
                                    .accessibilityLabel(note)
                            }
                        }
                        .padding(.horizontal, 8).padding(.vertical, 4)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color.orange.opacity(0.09), in: RoundedRectangle(cornerRadius: 6))
                    }
                }
                .padding(.horizontal, 12).padding(.vertical, 9)
                .frame(maxWidth: .infinity, alignment: .leading)
                .fireSkySurface(role: .content, emphasized: !snapshots.isEmpty)
            }
        }
    }

    private var nowcastShortcutButton: some View {
        Button {
            store.page = .nowcast
        } label: {
            Label(
                L10n.text("4h 临近窗口 · 切卫星实况", "4h window · Satellite Nowcast"),
                systemImage: "antenna.radiowaves.left.and.right"
            )
            .font(.system(size: 10.5, weight: .medium))
        }
        .buttonStyle(.bordered)
        .controlSize(.mini)
        .tint(coolAccent)
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
        let hintText = String(format: L10n.text("依据 %1$@ 最佳时刻，提前 %2$d 分钟通知；时区 %3$@。可在此取消，也可在系统通知设置中关闭。",
                                                "Based on %1$@ best time, notify %2$d min earlier; time zone %3$@. Cancel here or in Notification settings."),
                              model.uppercased(), minutesBefore, store.timezone)
        VStack(alignment: .leading, spacing: 3) {
            switch reminders.permission {
            case .authorized:
                if isCurrentScheduled, let scheduled = reminders.scheduled {
                    HStack(spacing: 6) {
                        Image(systemName: "bell.badge.fill").foregroundStyle(warm)
                        Text(String(format: L10n.text("已设置提醒 · %1$@ 前 %2$d 分钟",
                                                       "Reminder set · %1$@ · %2$d min before"),
                                    scheduledTime(scheduled.fireDate), minutesBefore))
                            .font(.system(size: 10))
                        Button(L10n.text("取消提醒", "Cancel reminder")) { reminders.cancel() }
                            .buttonStyle(.borderless).font(.system(size: 10))
                            .accessibilityLabel(L10n.text("取消提醒", "Cancel reminder"))
                    }
                    .help(hintText)
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
                    .help(hintText)
                }
            case .denied:
                HStack(spacing: 6) {
                    Text(L10n.text("通知权限已关闭", "Notifications are off"))
                        .font(.system(size: 10)).foregroundStyle(.secondary)
                    Button(L10n.text("打开系统通知设置", "Open notification settings")) {
                        reminders.openSystemSettings()
                    }
                    .buttonStyle(.borderless).font(.system(size: 10))
                }
                .help(hintText)
            case .unknown, .notDetermined:
                Button {
                    requestAndSchedule(fireDate: fireDate)
                } label: {
                    Label(L10n.text("开启最佳时刻提醒", "Enable best-time reminder"), systemImage: "bell.badge")
                        .font(.system(size: 10))
                }
                .buttonStyle(.borderless)
                .disabled(action.busy)
                .help(hintText)
            }
            if let message = action.message {
                Text(message).font(.system(size: 9)).foregroundStyle(.orange)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
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
        VStack(alignment: .leading, spacing: 8) {
            if store.result != nil && store.resultTab != "概览" {
                analysisContext
            } else {
                WorkActionSummaryView(comparison: comparison)
                if store.result != nil {
                    VStack(alignment: .leading, spacing: 8) {
                        HStack(alignment: .firstTextBaseline, spacing: 6) {
                            Text(L10n.t("模式结果")).fireSkyFont(size: 12, weight: .semibold)
                            Text("·").foregroundStyle(.secondary)
                            // 标题跟随结果原始参数，而不是当前表单。
                            Text("\(store.resultTitle) \(L10n.t(store.resultPhenomenonLabel))")
                                .fireSkyFont(size: 11, relativeTo: .caption)
                                .foregroundStyle(.secondary)
                                .lineLimit(1)
                                .truncationMode(.tail)
                            Spacer(minLength: 0)
                        }
                        WorkStaleResultBanner()
                        HStack(alignment: .top, spacing: 14) {
                            summary("gfs").frame(maxWidth: .infinity, alignment: .leading)
                            Divider().opacity(0.5)
                            summary("ec").frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                    .padding(11)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .workSurface()
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var analysisContext: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text("\(store.resultTitle) \(L10n.t(store.resultPhenomenonLabel))")
                .fireSkyFont(size: 11, relativeTo: .caption)
                .foregroundStyle(.secondary)
                .lineLimit(1)
                .truncationMode(.tail)
            WorkStaleResultBanner()
            LazyVGrid(columns: Array(repeating: GridItem(.flexible(), alignment: .leading),
                                    count: dynamicTypeSize.isAccessibilitySize ? 1 : 2),
                      alignment: .leading, spacing: 6) {
                ForEach(ForecastComparison.models, id: \.self) { model in
                    VStack(alignment: .leading, spacing: 3) {
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
                            .fireSkyFont(size: 10.5, relativeTo: .caption)
                            .foregroundStyle(comparison.error(model) == nil ? Color.secondary : Color.red)
                            .fixedSize(horizontal: false, vertical: true)
                        if let error = comparison.error(model) {
                            Text(error).fireSkyFont(size: 10.5, relativeTo: .caption)
                                .foregroundStyle(.red).textSelection(.enabled)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    .help(modelSourceNote(model, usesGeosFP: store.usesGeosFP))
                    .accessibilityElement(children: .combine)
                }
            }
        }
        .padding(.horizontal, 12).padding(.vertical, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .workSurface()
    }

    private func summary(_ model: String) -> some View {
        let report = comparison.report(model)
        let quality = comparison.peak(model)
        let sourceNote = modelSourceNote(model, usesGeosFP: store.usesGeosFP)
        return VStack(alignment: .leading, spacing: 5) {
            HStack {
                Circle().fill(modelColor(model)).frame(width: 6, height: 6)
                Text(model.uppercased()).fireSkyFont(size: 11.5, weight: .semibold)
                Spacer(minLength: 4)
                Text(L10n.status(comparison.status(model)))
                    .fireSkyFont(size: 10.5, relativeTo: .caption)
                    .foregroundStyle(comparison.error(model) == nil ? Color.secondary : Color.red)
                    .lineLimit(1)
            }
            HStack(alignment: .top, spacing: 10) {
                VStack(alignment: .leading, spacing: 2) {
                    HStack(alignment: .firstTextBaseline, spacing: 4) {
                        Text(format(quality, digits: 2)).fireSkyFont(size: 26, weight: .semibold, relativeTo: .title)
                            .monospacedDigit().foregroundStyle(quality == nil ? Color.secondary : modelColor(model))
                            .lineLimit(1).minimumScaleFactor(0.8)
                        Text("/ 2.5").fireSkyFont(size: 10, relativeTo: .caption).foregroundStyle(.secondary)
                    }
                    Text(L10n.qualityLevel(report?["peak_quality_level"] as? String
                        ?? report?["quality_level"] as? String ?? L10n.t("峰值质量指数")))
                        .fireSkyFont(size: 10.5, relativeTo: .caption)
                        .foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
                VStack(alignment: .trailing, spacing: 2) {
                    Text(L10n.t("最佳时刻")).fireSkyFont(size: 10.5, relativeTo: .caption)
                        .foregroundStyle(.secondary)
                    Text(timeLabel(report?["best_time_local"] as? String))
                        .fireSkyFont(size: 18, weight: .semibold, relativeTo: .title3).monospacedDigit()
                }
            }
            SkyTimeline(report: report, timezone: store.timezone)
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .top, spacing: 4) {
                    if let cycle = cycleLabel(model, report) {
                        Text(L10n.tf("起报 %@", cycle))
                            .lineLimit(1).workChip()
                    }
                    pressureFamilyChip(model, report)
                    cloudGenusChip(report)
                }
                VStack(alignment: .leading, spacing: 4) {
                    if let cycle = cycleLabel(model, report) {
                        Text(L10n.tf("起报 %@", cycle)).workChip()
                    }
                    HStack(spacing: 4) {
                        pressureFamilyChip(model, report)
                        cloudGenusChip(report)
                    }
                }
            }
            .fireSkyFont(size: 10, relativeTo: .caption).foregroundStyle(.secondary)
            Text(sourceNote)
                .fireSkyFont(size: 10, relativeTo: .caption).foregroundStyle(.secondary)
                .lineLimit(1)
                .truncationMode(.tail)
                .help(sourceNote)
            if let message = comparison.error(model) {
                Text(message).fireSkyFont(size: 10.5, relativeTo: .caption)
                    .foregroundStyle(.red).fixedSize(horizontal: false, vertical: true)
                    .help(message).textSelection(.enabled)
            }
        }
        .frame(maxWidth: .infinity, alignment: .topLeading)
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

private final class ResultsOverlayState: ObservableObject {
    @Published var showDiagnostics = false
}

struct WorkResultsView: View {
    @EnvironmentObject private var store: Store
    @Environment(\.colorScheme) private var scheme
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @StateObject private var overlay = ResultsOverlayState()
    private var comparison: ForecastComparison { store.pointComparison }
    private var report: JSONObject? { comparison.report(store.selectedModel) }
    var body: some View {
        Group {
            if comparison.result == nil {
                if dynamicTypeSize.isAccessibilitySize {
                    VStack(spacing: 10) {
                        WorkHeroView()
                        firstRunEmptyCanvas
                    }
                } else {
                    ScrollView {
                        VStack(spacing: 10) {
                            WorkHeroView()
                            firstRunEmptyCanvas
                        }
                        .padding(.trailing, 2)
                    }
                    .defaultScrollAnchor(.top)
                    .scrollIndicators(.hidden)
                }
            } else if dynamicTypeSize.isAccessibilitySize {
                VStack(alignment: .leading, spacing: 10) {
                    WorkHeroView()
                    pinnedTabBar
                    tabBody
                }
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 10, pinnedViews: [.sectionHeaders]) {
                        WorkHeroView()
                        Section {
                            tabBody
                        } header: {
                            pinnedTabBar
                        }
                    }
                    .padding(.trailing, 2)
                }
                .defaultScrollAnchor(.top)
                .scrollIndicators(.hidden)
            }
        }
        .sheet(isPresented: $overlay.showDiagnostics) {
            VStack(spacing: 0) {
                HStack(spacing: 10) {
                    Text(L10n.t("诊断与时间窗"))
                        .fireSkyFont(size: 13.5, weight: .semibold)
                    Spacer()
                    WorkSegments(options: ForecastComparison.models, titles: ["GFS", "EC"], selection: $store.selectedModel)
                        .frame(width: 124)
                    Button(L10n.close) { overlay.showDiagnostics = false }
                        .buttonStyle(.borderless)
                        .keyboardShortcut(.cancelAction)
                }
                .padding(.horizontal, 16).padding(.vertical, 10)
                Divider()
                ScrollView {
                    if let message = comparison.error(store.selectedModel) {
                        Text(L10n.tf("%@ 未完成：%@", store.selectedModel.uppercased(), message))
                            .fireSkyFont(size: 12).foregroundStyle(.red)
                            .fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
                            .padding(16).frame(maxWidth: .infinity, alignment: .leading)
                    } else {
                        WorkDiagnosticView(report: report, profile: false)
                            .padding(14)
                    }
                }
            }
            .frame(minWidth: 580, minHeight: 520)
            .tint(warm)
        }
    }

    private var pinnedTabBar: some View {
        HStack(alignment: .center, spacing: 10) {
            WorkAnalysisTabs()
            Spacer(minLength: 8)
            if store.resultTab != "概览" {
                HStack(spacing: 8) {
                    Text(L10n.status(comparison.status(store.selectedModel)))
                        .fireSkyFont(size: 10, relativeTo: .caption).foregroundStyle(.secondary)
                    WorkSegments(options: ForecastComparison.models, titles: ["GFS", "EC"], selection: $store.selectedModel)
                        .frame(width: 124)
                }
            }
            if comparison.result != nil {
                Button {
                    overlay.showDiagnostics = true
                } label: {
                    Label(L10n.t("诊断"), systemImage: "info.circle")
                        .fireSkyFont(size: 11, weight: .medium, relativeTo: .caption)
                        .foregroundStyle(.secondary)
                        .padding(.horizontal, 10).padding(.vertical, 5)
                        .background(Color.primary.opacity(scheme == .dark ? 0.08 : 0.055), in: Capsule())
                        .overlay {
                            Capsule().strokeBorder(Color.primary.opacity(0.08), lineWidth: 0.5)
                        }
                }
                .buttonStyle(.plain)
                .help(L10n.t("查看技术诊断与时间窗元数据"))
            }
        }
        .padding(.vertical, 6)
        .padding(.horizontal, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .fireSkySurface(role: .navigation, cornerRadius: 12)
    }

    @ViewBuilder
    private var tabBody: some View {
        if store.resultTab == "概览" {
            VStack(spacing: 10) {
                panelCloudAndColor
                panelAtmosphericAndReliability
                WorkSpectralAndRadarRow(comparison: comparison, selectedModel: $store.selectedModel)
                WorkQualityBreakdownView(comparison: comparison)
                WorkComparisonCurveView(comparison: comparison, timezone: store.timezone)
            }
        } else if let message = comparison.error(store.selectedModel) {
            Text(L10n.tf("%@ 未完成：%@", store.selectedModel.uppercased(), message))
                .fireSkyFont(size: 12).foregroundStyle(.red)
                .fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
                .padding(16).frame(maxWidth: .infinity, alignment: .leading).workSurface()
        } else if report == nil, comparison.result != nil {
            Text(L10n.tf("%@ 暂无结果", store.selectedModel.uppercased()))
                .fireSkyFont(size: 12)
                .foregroundStyle(.secondary).padding(24)
        } else if store.resultTab == "逐时台账" {
            WorkLedgerView(report: report,
                           bestTime: report?["best_time_local"] as? String,
                           selectedTime: $store.pointSelectedTime)
        } else if store.resultTab == "大气剖面" {
            WorkProfileTabView(report: report)
        } else {
            WorkDiagnosticView(report: report, profile: false)
        }
    }

    private var panelCloudAndColor: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(L10n.t("云况与色彩"))
                .fireSkyFont(size: 10.5, weight: .semibold, relativeTo: .caption)
                .foregroundStyle(.secondary)
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .top, spacing: 0) {
                    ForEach(0..<4, id: \.self) { offset in
                        if offset > 0 { Divider().opacity(0.45) }
                        metricCell(metricSpec[offset])
                            .padding(.leading, offset == 0 ? 0 : 10)
                            .padding(.trailing, offset == 3 ? 0 : 10)
                            .frame(maxWidth: .infinity, alignment: .topLeading)
                    }
                }
                .frame(minWidth: 740)

                VStack(spacing: 8) {
                    HStack(alignment: .top, spacing: 0) {
                        metricCell(metricSpec[0]).padding(.trailing, 10).frame(maxWidth: .infinity, alignment: .topLeading)
                        Divider().opacity(0.45)
                        metricCell(metricSpec[1]).padding(.leading, 10).frame(maxWidth: .infinity, alignment: .topLeading)
                    }
                    Divider().opacity(0.45)
                    HStack(alignment: .top, spacing: 0) {
                        metricCell(metricSpec[2]).padding(.trailing, 10).frame(maxWidth: .infinity, alignment: .topLeading)
                        Divider().opacity(0.45)
                        metricCell(metricSpec[3]).padding(.leading, 10).frame(maxWidth: .infinity, alignment: .topLeading)
                    }
                }
            }
        }
        .padding(11)
        .frame(maxWidth: .infinity, alignment: .leading)
        .workSurface()
    }

    private var panelAtmosphericAndReliability: some View {
        ViewThatFits(in: .horizontal) {
            HStack(alignment: .top, spacing: 10) {
                threeColumnGroupCard(title: "大气光学", range: 4..<7)
                    .frame(minWidth: 410, maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                threeColumnGroupCard(title: "时效与可靠性", range: 7..<10)
                    .frame(minWidth: 410, maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            }
            VStack(spacing: 8) {
                threeColumnGroupCard(title: "大气光学", range: 4..<7)
                threeColumnGroupCard(title: "时效与可靠性", range: 7..<10)
            }
        }
    }

    private func threeColumnGroupCard(title: String, range: Range<Int>) -> some View {
        let specs = Array(metricSpec[range])
        return VStack(alignment: .leading, spacing: 8) {
            Text(L10n.t(title))
                .fireSkyFont(size: 10.5, weight: .semibold, relativeTo: .caption)
                .foregroundStyle(.secondary)
            HStack(alignment: .top, spacing: 0) {
                ForEach(Array(specs.enumerated()), id: \.element.0) { idx, spec in
                    if idx > 0 { Divider().opacity(0.45) }
                    metricCell(spec)
                        .padding(.leading, idx == 0 ? 0 : 10)
                        .padding(.trailing, idx == specs.count - 1 ? 0 : 10)
                        .frame(maxWidth: .infinity, alignment: .topLeading)
                }
            }
        }
        .padding(11)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .workSurface()
    }

    private func metricCell(_ spec: (String, String, String)) -> some View {
        let captionText = L10n.t(spec.0 == "aod" ? comparison.aod.caption : spec.2)
        return VStack(alignment: .leading, spacing: 4) {
            Text(L10n.t(spec.1))
                .fireSkyFont(size: 11, weight: .medium, relativeTo: .caption)
                .foregroundStyle(.primary.opacity(0.84))
                .lineLimit(1)
            if spec.0 == "aod" {
                WorkAODValue(summary: comparison.aod)
            } else if spec.0 == "cloud_layer" {
                VStack(alignment: .leading, spacing: 2.5) {
                    ForEach(ForecastComparison.models, id: \.self) { model in
                        let modelReport = comparison.report(model)
                        let genus = cloudGenusBrief(modelReport?["cloud_genus_proxy"] as? JSONObject)
                        HStack(alignment: .firstTextBaseline, spacing: 4) {
                            Text(model.uppercased())
                                .fireSkyFont(size: 9.5, weight: .semibold, relativeTo: .caption)
                                .foregroundStyle(modelColor(model))
                                .frame(width: 24, alignment: .leading)
                            Text(metricValue(spec.0, modelReport))
                                .fireSkyFont(size: 12.5, weight: .semibold)
                                .monospacedDigit()
                                .lineLimit(1).minimumScaleFactor(0.75)
                            if let genus {
                                Text(genus)
                                    .fireSkyFont(size: 9.5, relativeTo: .caption)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(1)
                                    .truncationMode(.tail)
                            }
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            } else {
                HStack(spacing: 8) {
                    ForEach(ForecastComparison.models, id: \.self) { model in
                        VStack(alignment: .leading, spacing: 1.5) {
                            Text(model.uppercased())
                                .fireSkyFont(size: 9.5, weight: .semibold, relativeTo: .caption)
                                .foregroundStyle(modelColor(model))
                            Text(metricValue(spec.0, comparison.report(model)))
                                .fireSkyFont(size: 16, weight: .semibold, relativeTo: .title3)
                                .monospacedDigit()
                                .foregroundStyle(comparison.report(model) == nil ? Color.secondary : Color.primary)
                                .lineLimit(1).minimumScaleFactor(0.8)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
            }
            Text(captionText)
                .fireSkyFont(size: 9.5, relativeTo: .caption)
                .foregroundStyle(.secondary)
                .lineLimit(1)
                .minimumScaleFactor(0.82)
                .help(captionText)
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

private func parseSpectralHexColor(_ raw: String) -> Color? {
    let cleaned = raw.trimmingCharacters(in: CharacterSet.alphanumerics.inverted)
    guard cleaned.count == 6, let value = UInt64(cleaned, radix: 16) else { return nil }
    let r = Double((value >> 16) & 0xFF) / 255.0
    let g = Double((value >> 8) & 0xFF) / 255.0
    let b = Double(value & 0xFF) / 255.0
    return Color(red: r, green: g, blue: b)
}

private func spectralHHMM(_ iso: String?) -> String? {
    guard let iso, !iso.isEmpty else { return nil }
    if let tIdx = iso.firstIndex(of: "T") {
        let afterT = iso[iso.index(after: tIdx)...]
        if afterT.count >= 5 { return String(afterT.prefix(5)) }
    }
    return iso.count >= 16 ? String(iso.dropFirst(11).prefix(5)) : nil
}

private func resolvedModelForPayload(_ key: String, comparison: ForecastComparison, selectedModel: String) -> String {
    if comparison.report(selectedModel)?[key] is JSONObject { return selectedModel }
    return ForecastComparison.models.first(where: { comparison.report($0)?[key] is JSONObject }) ?? selectedModel
}

private func availableModelsForPayload(_ key: String, comparison: ForecastComparison) -> [String] {
    ForecastComparison.models.filter { comparison.report($0)?[key] is JSONObject }
}

private struct InlineModelSwitcher: View {
    let available: [String]
    let activeModel: String
    @Binding var selectedModel: String

    var body: some View {
        if available.count > 1 {
            HStack(spacing: 4) {
                ForEach(available, id: \.self) { model in
                    let isActive = model == activeModel
                    Button {
                        selectedModel = model
                    } label: {
                        Text(model.uppercased())
                            .fireSkyFont(size: 10, weight: .semibold, relativeTo: .caption)
                            .foregroundStyle(isActive ? Color.white : modelColor(model))
                            .padding(.horizontal, 7).padding(.vertical, 2.5)
                            .background(
                                isActive ? modelColor(model) : modelColor(model).opacity(0.12),
                                in: Capsule()
                            )
                    }
                    .buttonStyle(.plain)
                }
            }
        } else {
            Text(activeModel.uppercased())
                .fireSkyFont(size: 10, weight: .semibold, relativeTo: .caption)
                .foregroundStyle(modelColor(activeModel))
                .padding(.horizontal, 7).padding(.vertical, 2.5)
                .background(modelColor(activeModel).opacity(0.14), in: Capsule())
        }
    }
}

/// Responsive dual-column row placing Spectral Glow Palette and 16-Direction Azimuth Radar side by side.
private struct WorkSpectralAndRadarRow: View {
    let comparison: ForecastComparison
    @Binding var selectedModel: String

    private var hasSpectral: Bool {
        !availableModelsForPayload("spectral_color", comparison: comparison).isEmpty
    }

    private var hasRadar: Bool {
        !availableModelsForPayload("azimuth_radar", comparison: comparison).isEmpty
    }

    var body: some View {
        if hasSpectral && hasRadar {
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .top, spacing: 10) {
                    SpectralColorBannerView(comparison: comparison, selectedModel: $selectedModel)
                        .frame(minWidth: 340, maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                    AzimuthRadarView(comparison: comparison, selectedModel: $selectedModel)
                        .frame(minWidth: 380, maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                }
                VStack(spacing: 10) {
                    SpectralColorBannerView(comparison: comparison, selectedModel: $selectedModel)
                    AzimuthRadarView(comparison: comparison, selectedModel: $selectedModel)
                }
            }
        } else if hasSpectral || hasRadar {
            VStack(spacing: 10) {
                if hasSpectral {
                    SpectralColorBannerView(comparison: comparison, selectedModel: $selectedModel)
                }
                if hasRadar {
                    AzimuthRadarView(comparison: comparison, selectedModel: $selectedModel)
                }
            }
        }
    }
}

/// Physical CIE 1931 spectral glow strip and vertical sky gradient read strictly from backend `spectral_color`.
private struct SpectralColorBannerView: View {
    let comparison: ForecastComparison
    @Binding var selectedModel: String

    private var activeModel: String {
        resolvedModelForPayload("spectral_color", comparison: comparison, selectedModel: selectedModel)
    }

    private var availableModels: [String] {
        availableModelsForPayload("spectral_color", comparison: comparison)
    }

    private var spectral: JSONObject? {
        comparison.report(activeModel)?["spectral_color"] as? JSONObject
    }

    var body: some View {
        if let spectral {
            let peakHex = spectral["peak_hex"] as? String ?? "#E25822"
            let peakColor = parseSpectralHexColor(peakHex) ?? Color(red: 0.89, green: 0.35, blue: 0.13)
            let peakLabel = L10n.text(
                spectral["peak_label_zh"] as? String ?? "",
                spectral["peak_label_en"] as? String ?? ""
            )
            let gradientColors = (spectral["sky_gradient_hex"] as? [String] ?? [])
                .compactMap(parseSpectralHexColor)
            let steps = spectral["steps"] as? [JSONObject] ?? []
            let peakTime = spectralHHMM(spectral["peak_time_local"] as? String)
                ?? spectralHHMM(comparison.report(activeModel)?["best_time_local"] as? String)

            VStack(alignment: .leading, spacing: 8) {
                HStack(alignment: .center, spacing: 8) {
                    Text(L10n.text("光谱预测云色 · CIE 1931", "Spectral Glow Palette · CIE 1931"))
                        .fireSkyFont(size: 12.5, weight: .semibold)
                    Spacer()
                    InlineModelSwitcher(
                        available: availableModels,
                        activeModel: activeModel,
                        selectedModel: $selectedModel
                    )
                }

                HStack(alignment: .center, spacing: 11) {
                    skyProfileSwatch(gradientColors: gradientColors, fallback: peakColor)

                    VStack(alignment: .leading, spacing: 7) {
                        HStack(alignment: .center, spacing: 6) {
                            RoundedRectangle(cornerRadius: 4)
                                .fill(peakColor)
                                .frame(width: 12, height: 12)
                                .overlay {
                                    RoundedRectangle(cornerRadius: 4)
                                        .strokeBorder(.white.opacity(0.45), lineWidth: 0.8)
                                }
                            Text(peakLabel.isEmpty ? peakHex : peakLabel)
                                .fireSkyFont(size: 12.5, weight: .semibold)
                            Text(peakHex)
                                .fireSkyFont(size: 10, weight: .medium, relativeTo: .caption)
                                .monospacedDigit()
                                .foregroundStyle(.secondary)
                                .padding(.horizontal, 6).padding(.vertical, 1.5)
                                .background(Color.primary.opacity(0.06), in: Capsule())
                            Spacer(minLength: 4)
                            if let peakTime {
                                Text(L10n.text("峰值 \(peakTime)", "Peak \(peakTime)"))
                                    .fireSkyFont(size: 10, weight: .medium, relativeTo: .caption)
                                    .monospacedDigit()
                                    .foregroundStyle(.secondary)
                            }
                        }

                        if !steps.isEmpty {
                            spectralTimelineSection(steps: steps, peakColor: peakColor)
                        }

                        spectralTelemetryFooter(spectral: spectral)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .padding(11)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .workSurface()
        }
    }

    private func skyProfileSwatch(gradientColors: [Color], fallback: Color) -> some View {
        let colors = gradientColors.count >= 2 ? gradientColors : [fallback.opacity(0.35), fallback]
        return ZStack {
            RoundedRectangle(cornerRadius: 10)
                .fill(LinearGradient(colors: colors, startPoint: .top, endPoint: .bottom))
                .overlay {
                    RoundedRectangle(cornerRadius: 10)
                        .strokeBorder(Color.white.opacity(0.22), lineWidth: 0.8)
                }
            VStack {
                Text(L10n.text("天顶", "Zenith"))
                    .fireSkyFont(size: 9, weight: .medium)
                    .foregroundStyle(.white.opacity(0.82))
                Spacer()
                Text(L10n.text("地平线", "Horizon"))
                    .fireSkyFont(size: 9, weight: .medium)
                    .foregroundStyle(.white.opacity(0.92))
                    .shadow(color: .black.opacity(0.45), radius: 2, y: 1)
            }
            .padding(.vertical, 7)
        }
        .frame(width: 60, height: 108)
    }

    private func spectralTimelineSection(steps: [JSONObject], peakColor: Color) -> some View {
        let peakIdx = steps.firstIndex(where: { ($0["is_peak"] as? NSNumber)?.boolValue == true }) ?? (steps.count / 2)
        return VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 1) {
                ForEach(Array(steps.enumerated()), id: \.offset) { idx, step in
                    let hex = step["hex"] as? String ?? "#E25822"
                    let cellColor = parseSpectralHexColor(hex) ?? peakColor
                    let isPeak = idx == peakIdx
                    Rectangle()
                        .fill(cellColor)
                        .frame(maxWidth: .infinity, minHeight: 18, maxHeight: 18)
                        .overlay {
                            if isPeak {
                                Rectangle()
                                    .fill(Color.white.opacity(0.92))
                                    .frame(width: 2)
                                    .shadow(color: .black.opacity(0.4), radius: 1)
                            }
                        }
                }
            }
            .clipShape(RoundedRectangle(cornerRadius: 6))
            .overlay {
                RoundedRectangle(cornerRadius: 6)
                    .strokeBorder(Color.white.opacity(0.22), lineWidth: 0.8)
            }

            spectralAxisLabels(steps: steps, peakIdx: peakIdx)
        }
    }

    private func spectralAxisLabels(steps: [JSONObject], peakIdx: Int) -> some View {
        let startText = stepAxisLabel(steps.first, fallbackPrefix: L10n.text("起", "Start"))
        let peakText = stepAxisLabel(steps[safe: peakIdx], fallbackPrefix: L10n.text("峰值", "Peak"))
        let endText = stepAxisLabel(steps.last, fallbackPrefix: L10n.text("止", "End"))
        return HStack {
            Text(startText)
            Spacer()
            if steps.count >= 3, peakIdx > 0, peakIdx < steps.count - 1 {
                Text(peakText).fontWeight(.semibold).foregroundStyle(.primary)
                Spacer()
            }
            Text(endText)
        }
        .fireSkyFont(size: 10, relativeTo: .caption)
        .foregroundStyle(.secondary)
        .monospacedDigit()
    }

    private func stepAxisLabel(_ step: JSONObject?, fallbackPrefix: String) -> String {
        guard let step else { return fallbackPrefix }
        if let hhmm = spectralHHMM(step["valid_time_local"] as? String) {
            return hhmm
        }
        if let offset = (step["offset_minutes"] as? NSNumber)?.intValue {
            return offset == 0 ? fallbackPrefix : (offset > 0 ? "+\(offset)m" : "\(offset)m")
        }
        return fallbackPrefix
    }

    private func spectralTelemetryFooter(spectral: JSONObject) -> some View {
        var parts: [String] = []
        if let solarElev = (spectral["solar_elevation_deg"] as? NSNumber)?.doubleValue {
            parts.append(String(format: L10n.text("太阳仰角 %.1f°", "Elev %.1f°"), solarElev))
        }
        if let solarAz = (spectral["solar_azimuth_deg"] as? NSNumber)?.doubleValue {
            parts.append(String(format: L10n.text("太阳方位 %.0f°", "Az %.0f°"), solarAz))
        }
        if let cloudBase = (spectral["cloud_base_km"] as? NSNumber)?.doubleValue {
            parts.append(String(format: L10n.text("云底 %.1f km", "Base %.1f km"), cloudBase))
        }
        if let aod = (spectral["aod550"] as? NSNumber)?.doubleValue {
            parts.append(String(format: "AOD %.2f", aod))
        }
        return Text(parts.joined(separator: " · "))
            .fireSkyFont(size: 10, relativeTo: .caption)
            .foregroundStyle(.secondary)
            .monospacedDigit()
            .lineLimit(2)
            .fixedSize(horizontal: false, vertical: true)
    }
}

private extension Array {
    subscript(safe index: Int) -> Element? {
        indices.contains(index) ? self[index] : nil
    }
}

/// 16-direction polar azimuth glow radar rendered from backend `azimuth_radar` (`0.0...1.0` scale).
private struct AzimuthRadarView: View {
    let comparison: ForecastComparison
    @Binding var selectedModel: String

    private struct BearingItem: Identifiable {
        let id: Double
        let deg: Double
        let label: String
        let score: Double
        let canvas: Double
        let lowBlock: Double
    }

    private var activeModel: String {
        resolvedModelForPayload("azimuth_radar", comparison: comparison, selectedModel: selectedModel)
    }

    private var availableModels: [String] {
        availableModelsForPayload("azimuth_radar", comparison: comparison)
    }

    private var radar: JSONObject? {
        comparison.report(activeModel)?["azimuth_radar"] as? JSONObject
    }

    var body: some View {
        if let radar {
            let bearings = parseBearings(radar["bearings"] as? [JSONObject] ?? [])
            let solarAz = (radar["solar_azimuth_deg"] as? NSNumber)?.doubleValue ?? 270
            let bestAz = (radar["best_azimuth_deg"] as? NSNumber)?.doubleValue ?? 270
            let bestDir = L10n.text(radar["best_direction_zh"] as? String ?? "", radar["best_direction_en"] as? String ?? "")
            let bestScore = min(1.0, max(0.0, (radar["best_score"] as? NSNumber)?.doubleValue ?? 0))
            let recommendation = L10n.text(radar["recommendation_zh"] as? String ?? "", radar["recommendation_en"] as? String ?? "")
            let topThree = Array(bearings.sorted { $0.score > $1.score }.prefix(3))

            VStack(alignment: .leading, spacing: 8) {
                HStack(alignment: .center, spacing: 8) {
                    Text(L10n.text("16 方位火烧云观测雷达", "16-Direction Azimuth Glow Radar"))
                        .fireSkyFont(size: 12.5, weight: .semibold)
                    Spacer()
                    InlineModelSwitcher(
                        available: availableModels,
                        activeModel: activeModel,
                        selectedModel: $selectedModel
                    )
                }

                HStack(alignment: .center, spacing: 12) {
                    radarCompass(bearings: bearings, solarAz: solarAz, bestAz: bestAz, bestScore: bestScore)

                    VStack(alignment: .leading, spacing: 6) {
                        HStack(spacing: 6) {
                            Label {
                                Text(String(format: L10n.text("推荐 %@ (%.0f°)", "Best %@ (%.0f°)"), bestDir, bestAz))
                                    .fireSkyFont(size: 12.5, weight: .semibold).monospacedDigit()
                            } icon: {
                                Circle().fill(Color(red: 0.88, green: 0.31, blue: 0.15)).frame(width: 7, height: 7)
                            }
                            Text(String(format: "%.0f%%", bestScore * 100.0))
                                .fireSkyFont(size: 10.5, weight: .semibold, relativeTo: .caption).monospacedDigit()
                                .foregroundStyle(.orange)
                                .padding(.horizontal, 6).padding(.vertical, 1.5)
                                .background(Color.orange.opacity(0.14), in: Capsule())
                        }

                        Label {
                            Text(String(format: L10n.text("太阳方位角 %.0f°", "Solar azimuth %.0f°"), solarAz))
                                .fireSkyFont(size: 10.5, relativeTo: .caption).foregroundStyle(.secondary).monospacedDigit()
                        } icon: {
                            Circle()
                                .fill(Color(red: 0.96, green: 0.65, blue: 0.14))
                                .overlay(Circle().strokeBorder(.white.opacity(0.8), lineWidth: 0.8))
                                .frame(width: 7, height: 7)
                        }

                        if !recommendation.isEmpty {
                            Text(recommendation)
                                .fireSkyFont(size: 10.5, relativeTo: .caption)
                                .foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }

                        if !topThree.isEmpty {
                            topBearingsList(topThree)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .padding(11)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .workSurface()
        }
    }

    private func parseBearings(_ raw: [JSONObject]) -> [BearingItem] {
        raw.compactMap { entry in
            guard let deg = (entry["deg"] as? NSNumber)?.doubleValue,
                  let score = (entry["score"] as? NSNumber)?.doubleValue else { return nil }
            let label = L10n.text(entry["label_zh"] as? String ?? "", entry["label_en"] as? String ?? "")
            let canvas = min(1.0, max(0.0, (entry["canvas"] as? NSNumber)?.doubleValue ?? 0))
            let lowBlock = min(1.0, max(0.0, (entry["low_block"] as? NSNumber)?.doubleValue ?? 0))
            return BearingItem(
                id: deg,
                deg: deg,
                label: label.isEmpty ? String(format: "%.0f°", deg) : label,
                score: min(1.0, max(0.0, score)),
                canvas: canvas,
                lowBlock: lowBlock
            )
        }
    }

    private func radarCompass(bearings: [BearingItem], solarAz: Double, bestAz: Double, bestScore: Double) -> some View {
        ZStack {
            Canvas { ctx, size in
                let center = CGPoint(x: size.width / 2, y: size.height / 2)
                let radius = min(size.width, size.height) * 0.36

                for frac in [0.33, 0.66, 1.0] {
                    let r = radius * frac
                    let rect = CGRect(x: center.x - r, y: center.y - r, width: r * 2, height: r * 2)
                    ctx.stroke(Path(ellipseIn: rect), with: .color(.secondary.opacity(frac == 1.0 ? 0.30 : 0.18)), lineWidth: 0.8)
                }

                for step in 0..<8 {
                    let rad = (Double(step) * 45.0 - 90.0) * .pi / 180.0
                    let end = CGPoint(x: center.x + cos(rad) * radius, y: center.y + sin(rad) * radius)
                    var line = Path()
                    line.move(to: center)
                    line.addLine(to: end)
                    ctx.stroke(line, with: .color(.secondary.opacity(step % 2 == 0 ? 0.24 : 0.12)), lineWidth: 0.7)
                }

                let points: [CGPoint] = bearings.map { entry in
                    let rad = (entry.deg - 90.0) * .pi / 180.0
                    let r = radius * max(0.06, entry.score)
                    return CGPoint(x: center.x + cos(rad) * r, y: center.y + sin(rad) * r)
                }
                if points.count >= 3 {
                    var poly = Path()
                    poly.addLines(points)
                    poly.closeSubpath()
                    ctx.fill(poly, with: .color(Color.orange.opacity(0.28)))
                    ctx.stroke(poly, with: .color(Color.orange), lineWidth: 1.8)
                }

                let sunRad = (solarAz - 90.0) * .pi / 180.0
                let sunPt = CGPoint(x: center.x + cos(sunRad) * radius, y: center.y + sin(sunRad) * radius)
                var sunRay = Path()
                sunRay.move(to: center)
                sunRay.addLine(to: sunPt)
                ctx.stroke(
                    sunRay,
                    with: .color(Color(red: 0.96, green: 0.65, blue: 0.14).opacity(0.75)),
                    style: StrokeStyle(lineWidth: 1.2, dash: [4, 3])
                )

                let bestRad = (bestAz - 90.0) * .pi / 180.0
                let bestR = radius * max(0.10, bestScore)
                let bestPt = CGPoint(x: center.x + cos(bestRad) * bestR, y: center.y + sin(bestRad) * bestR)
                let bestColor = Color(red: 0.88, green: 0.31, blue: 0.15)
                var bestRay = Path()
                bestRay.move(to: center)
                bestRay.addLine(to: bestPt)
                ctx.stroke(bestRay, with: .color(bestColor.opacity(0.9)), lineWidth: 1.6)
                let bestDot = CGRect(x: bestPt.x - 4.5, y: bestPt.y - 4.5, width: 9, height: 9)
                ctx.fill(Path(ellipseIn: bestDot), with: .color(bestColor))
                ctx.stroke(Path(ellipseIn: bestDot), with: .color(.white.opacity(0.9)), lineWidth: 1.0)

                let sunDot = CGRect(x: sunPt.x - 5, y: sunPt.y - 5, width: 10, height: 10)
                ctx.fill(Path(ellipseIn: sunDot), with: .color(Color(red: 0.96, green: 0.65, blue: 0.14)))
                ctx.stroke(Path(ellipseIn: sunDot), with: .color(.white.opacity(0.95)), lineWidth: 1.2)
            }
            .frame(width: 146, height: 146)

            VStack {
                Text("N").fireSkyFont(size: 9.5, weight: .semibold).foregroundStyle(.secondary)
                Spacer()
                Text("S").fireSkyFont(size: 9.5, weight: .semibold).foregroundStyle(.secondary)
            }
            HStack {
                Text("W").fireSkyFont(size: 9.5, weight: .semibold).foregroundStyle(.secondary)
                Spacer()
                Text("E").fireSkyFont(size: 9.5, weight: .semibold).foregroundStyle(.secondary)
            }
        }
        .frame(width: 150, height: 150)
    }

    private func topBearingsList(_ items: [BearingItem]) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            ForEach(items) { item in
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 6) {
                        Text(item.label)
                            .fireSkyFont(size: 10.5, weight: .semibold, relativeTo: .caption)
                            .lineLimit(1)
                            .frame(width: 50, alignment: .leading)
                        GeometryReader { geo in
                            ZStack(alignment: .leading) {
                                Capsule().fill(Color.primary.opacity(0.08))
                                Capsule().fill(Color.orange.opacity(0.85))
                                    .frame(width: max(4, geo.size.width * item.score))
                            }
                        }
                        .frame(minWidth: 72, maxWidth: 136, minHeight: 5, maxHeight: 5)
                        Text(String(format: "%.0f%%", item.score * 100.0))
                            .fireSkyFont(size: 11, weight: .medium, relativeTo: .caption)
                            .monospacedDigit()
                            .frame(width: 34, alignment: .trailing)
                    }
                    Text(String(format: L10n.text("云幕 %.0f%% · 近场低云 %.0f%%", "Canvas %.0f%% · Low %.0f%%"),
                                item.canvas * 100.0, item.lowBlock * 100.0))
                        .fireSkyFont(size: 10, relativeTo: .caption)
                        .foregroundStyle(.secondary)
                        .monospacedDigit()
                        .lineLimit(1)
                }
            }
        }
        .padding(.top, 2)
    }
}

private final class BreakdownDisclosureState: ObservableObject {
    @Published var expanded = false
}

/// Read-only unified GFS vs EC score composition panel. Every value is read directly
/// from the backend report (quality_components / report fields); nothing is recomputed.
private struct WorkQualityBreakdownView: View {
    let comparison: ForecastComparison
    @StateObject private var disclosure = BreakdownDisclosureState()

    private struct CellContent {
        let primary: String
        let detail: String
        let primaryColor: Color
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            headerBar
            Divider().opacity(0.45)
            comparisonTable
            if let limitSummary = limitingFactorSummary {
                Text(limitSummary)
                    .fireSkyFont(size: 10, relativeTo: .caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 1)
            }
            if disclosure.expanded {
                Divider().opacity(0.45)
                expandedDetails
            }
        }
        .padding(11)
        .frame(maxWidth: .infinity, alignment: .leading)
        .workSurface()
    }

    private var headerBar: some View {
        ViewThatFits(in: .horizontal) {
            HStack(alignment: .center, spacing: 10) {
                Text(L10n.t("评分组成")).fireSkyFont(size: 12.5, weight: .semibold)
                HStack(spacing: 10) {
                    ForEach(ForecastComparison.models, id: \.self) { model in
                        modelSummaryBadge(model)
                    }
                }
                Spacer(minLength: 8)
                disclosureButton
            }
            VStack(alignment: .leading, spacing: 5) {
                HStack {
                    Text(L10n.t("评分组成")).fireSkyFont(size: 12.5, weight: .semibold)
                    Spacer()
                    disclosureButton
                }
                HStack(spacing: 10) {
                    ForEach(ForecastComparison.models, id: \.self) { model in
                        modelSummaryBadge(model)
                    }
                }
            }
        }
    }

    private var disclosureButton: some View {
        Button {
            disclosure.expanded.toggle()
        } label: {
            Label(L10n.t("详情与口径"), systemImage: disclosure.expanded ? "chevron.up" : "chevron.down")
                .fireSkyFont(size: 10, weight: .medium, relativeTo: .caption)
                .foregroundStyle(.secondary)
        }
        .buttonStyle(.borderless)
    }

    @ViewBuilder
    private func modelSummaryBadge(_ model: String) -> some View {
        if let report = comparison.report(model) {
            HStack(spacing: 5) {
                Circle().fill(modelColor(model)).frame(width: 6, height: 6)
                Text(model.uppercased()).fireSkyFont(size: 10.5, weight: .semibold)
                Text("\(format(report["quality_index"], digits: 3)) / 2.5")
                    .fireSkyFont(size: 11, weight: .semibold).monospacedDigit()
                    .foregroundStyle(modelColor(model))
                if let level = report["quality_level"] as? String, !level.isEmpty {
                    Text(L10n.qualityLevel(level))
                        .fireSkyFont(size: 10, relativeTo: .caption).foregroundStyle(.secondary)
                        .lineLimit(1)
                }
            }
        } else if comparison.error(model) != nil {
            HStack(spacing: 4) {
                Circle().fill(Color.red.opacity(0.8)).frame(width: 6, height: 6)
                Text(L10n.tf("%@ 未完成", model.uppercased()))
                    .fireSkyFont(size: 10, relativeTo: .caption).foregroundStyle(.red)
            }
        } else {
            HStack(spacing: 4) {
                Circle().fill(Color.secondary.opacity(0.5)).frame(width: 6, height: 6)
                Text(L10n.tf("%@ 暂无结果", model.uppercased()))
                    .fireSkyFont(size: 10, relativeTo: .caption).foregroundStyle(.secondary)
            }
        }
    }

    private var comparisonTable: some View {
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                Text(L10n.t("评分项"))
                    .frame(width: 98, alignment: .leading)
                ForEach(ForecastComparison.models, id: \.self) { model in
                    HStack(spacing: 4) {
                        Circle().fill(modelColor(model)).frame(width: 5, height: 5)
                        Text(model.uppercased())
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .fireSkyFont(size: 10, weight: .semibold, relativeTo: .caption)
            .foregroundStyle(.secondary)
            .padding(.bottom, 5)

            ForEach(0..<6, id: \.self) { idx in
                Divider().opacity(0.38)
                metricRow(idx)
            }
        }
    }

    private func rowTitle(_ idx: Int) -> String {
        switch idx {
        case 0: return L10n.t("云量基础分")
        case 1: return L10n.t("可烧云占比")
        case 2: return L10n.t("入射遮挡")
        case 3: return L10n.t("持续时间修正")
        case 4: return L10n.t("入射 AOD 修正")
        default: return L10n.t("观测 AOD 修正")
        }
    }

    private func metricRow(_ idx: Int) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Text(rowTitle(idx))
                .fireSkyFont(size: 10.5, relativeTo: .caption)
                .foregroundStyle(.secondary)
                .frame(width: 98, alignment: .leading)
            ForEach(ForecastComparison.models, id: \.self) { model in
                modelMetricCell(idx: idx, report: comparison.report(model))
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .padding(.vertical, 5)
    }

    @ViewBuilder
    private func modelMetricCell(idx: Int, report: JSONObject?) -> some View {
        if let report {
            let content = cellContent(idx: idx, report: report)
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(content.primary)
                    .fireSkyFont(size: 11, weight: .semibold)
                    .monospacedDigit()
                    .foregroundStyle(content.primaryColor)
                    .frame(minWidth: 46, alignment: .leading)
                Text(content.detail)
                    .fireSkyFont(size: 10, relativeTo: .caption)
                    .monospacedDigit()
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .minimumScaleFactor(0.82)
            }
        } else {
            Text("—")
                .fireSkyFont(size: 10.5)
                .foregroundStyle(.secondary)
        }
    }

    private func cellContent(idx: Int, report: JSONObject) -> CellContent {
        let c = report["quality_components"] as? JSONObject ?? [:]
        switch idx {
        case 0:
            let delta = signedDelta(c["coverage_base_score"] ?? c["coverage_score"])
            let load = format(c["forward_burnable_cloud_load_effective"] ?? c["forward_burnable_cloud_load_peak"], digits: 2)
            return CellContent(
                primary: delta ?? "—",
                detail: "有效 \(ratioText(c["burnable_cloud_coverage_effective"])) · 载荷 \(load)",
                primaryColor: deltaColor(delta)
            )
        case 1:
            return CellContent(
                primary: ratioText(c["forward_burnable_cloud_ratio_peak"]),
                detail: "前方可烧 \(ratioText(c["forward_burnable_cloud_field_amount_peak"])) / 总云 \(ratioText(c["forward_view_cloud_field_amount_peak"]))",
                primaryColor: .primary
            )
        case 2:
            return CellContent(
                primary: ratioText(c["solar_incoming_block_risk_at_best_cloud"] ?? c["solar_incoming_block_risk_mean"]),
                detail: L10n.t("已计入可烧量"),
                primaryColor: .primary
            )
        case 3:
            let delta = signedDelta(c["duration_adjustment"])
            let mins = format(report["duration_minutes"] ?? c["duration_minutes"], digits: 0)
            return CellContent(
                primary: delta ?? "—",
                detail: "\(mins) 分 · ×\(format(c["duration_factor"], digits: 3))",
                primaryColor: deltaColor(delta)
            )
        case 4:
            let delta = signedDelta(c["incoming_aod_adjustment"])
            return CellContent(
                primary: delta ?? "—",
                detail: "AOD \(format(c["quality_aod550"], digits: 3)) · ×\(format(c["incoming_aod_illumination_factor"], digits: 3))",
                primaryColor: deltaColor(delta)
            )
        default:
            let delta = signedDelta(c["observer_aod_adjustment"])
            return CellContent(
                primary: delta ?? "—",
                detail: "对比 ×\(format(c["observer_aod_contrast_factor"], digits: 3))",
                primaryColor: deltaColor(delta)
            )
        }
    }

    private var limitingFactorSummary: String? {
        let gfs = ((comparison.report("gfs")?["quality_components"] as? JSONObject)?["quality_limiting_factor"] as? String)
            .flatMap { $0.isEmpty ? nil : L10n.qualityLimit($0) }
        let ec = ((comparison.report("ec")?["quality_components"] as? JSONObject)?["quality_limiting_factor"] as? String)
            .flatMap { $0.isEmpty ? nil : L10n.qualityLimit($0) }
        switch (gfs, ec) {
        case (nil, nil): return nil
        case let (g?, e?) where g == e: return "当前限制因素：\(g)"
        case let (g?, e?): return "限制因素 · GFS：\(g) · EC：\(e)"
        case let (g?, nil): return "GFS 限制因素：\(g)"
        case let (nil, e?): return "EC 限制因素：\(e)"
        }
    }

    private var expandedDetails: some View {
        let sample = comparison.report("gfs") ?? comparison.report("ec")
        let sampleComp = sample?["quality_components"] as? JSONObject ?? [:]
        let radius = valueSuffix(sampleComp["forward_field_radius_km"] ?? sample?["analysis_radius_km"], " km", digits: 0)
        let sector = valueSuffix(sampleComp["forward_sector_half_angle_deg"], "°", digits: 0, sign: "±")
        return VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 12) {
                Text("分析范围 \(radius) · 太阳方向扇区 \(sector)")
                    .fireSkyFont(size: 10, weight: .medium, relativeTo: .caption).foregroundStyle(.secondary)
                    .monospacedDigit()
            }
            ForEach(ForecastComparison.models, id: \.self) { model in
                if let r = comparison.report(model) {
                    let c = r["quality_components"] as? JSONObject ?? [:]
                    let src = (r["model_source"] as? String ?? model) == "ec" ? "EC TCC + PL-RH 代理" : "GFS 全量云场"
                    HStack(spacing: 8) {
                        Text(model.uppercased())
                            .fireSkyFont(size: 10, weight: .semibold).foregroundStyle(modelColor(model))
                            .frame(width: 28, alignment: .leading)
                        Text("原始分 \(format(c["raw_quality_index"], digits: 3)) · \(src) · 观测回程 τ \(format(c["observer_return_optical_depth_mean"], digits: 3)) · T \(format(c["observer_return_transmission_mean"], digits: 3))")
                            .fireSkyFont(size: 10, relativeTo: .caption).foregroundStyle(.secondary).monospacedDigit()
                            .lineLimit(1).minimumScaleFactor(0.8)
                    }
                }
            }
            Text("几何遮挡已包含在可烧云量中，不再重复扣分；同一视线由近及远合成（低云遮挡其后中高云）。摄影焦段、Top-K、代表云与云边界不进入主评分。")
                .fireSkyFont(size: 9.5, relativeTo: .caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.top, 2)
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
        return VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline) {
                Text("霞光质量与大气趋势").font(.system(size: 12.5, weight: .semibold))
                Text(L10n.text("GFS 实线 · EC 虚线 · 有效太阳窗内绘制", "GFS solid · EC dashed · plotted in solar window"))
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
        }.padding(11).workSurface()
    }

    private func summaryChips(points: [ForecastCurvePoint], peak: ForecastCurvePoint?) -> some View {
        let aodValues = comparison.aodCurve(timezone: timezone).map(\.quality)
        let modelValidityText: String = {
            let parts: [String] = ForecastComparison.models.compactMap { m in
                let steps = comparison.report(m)?["steps"] as? [JSONObject] ?? []
                guard !steps.isEmpty else { return nil }
                let validCount = steps.filter(ForecastComparison.isStepValid).count
                return "\(m.uppercased()) \(validCount)/\(steps.count)"
            }
            return parts.isEmpty ? "—" : parts.joined(separator: " · ")
        }()
        return HStack(spacing: 14) {
            summaryChip("峰值质量", peak.map { String(format: "%.3f / 2.5", $0.quality) } ?? "—")
            summaryChip("峰值时刻", peak.map { ObservationDate.timeLabel($0.time, timezone: timezone) } ?? "—")
            summaryChip("有效时次", modelValidityText)
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
        let validSteps = steps.filter(ForecastComparison.isStepValid)
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
                        let valid = ForecastComparison.isStepValid(step)
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
        filter.showInvalid ? allSteps : allSteps.filter(ForecastComparison.isStepValid)
    }
    private var hiddenInvalidCount: Int {
        allSteps.filter { !ForecastComparison.isStepValid($0) }.count
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
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
    }

    private func row(_ item: JSONObject) -> some View {
        let valid = ForecastComparison.isStepValid(item)
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
                Text(valid ? item["instant_quality_level"] as? String ?? "—" : L10n.t("外推窗外/未判断"))
                    .frame(width: 92, alignment: .leading)
                    .foregroundStyle(valid ? Color.primary : Color.secondary)
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
            .padding(.vertical, 7)
            .padding(.horizontal, 10)
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

private struct WorkProfileTabView: View {
    let report: JSONObject?
    @EnvironmentObject private var store: Store

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let report {
                let section = report["cross_section"] as? JSONObject
                let source = report["profile_source"] as? JSONObject
                let sourceAvailable = source?["available"] as? Bool == true
                let model = source?["model_source"] as? String ?? store.selectedModel
                HStack(alignment: .center, spacing: 8) {
                    Text(L10n.t("太阳方向大气剖面"))
                        .fireSkyFont(size: 12.5, weight: .semibold)
                    Spacer(minLength: 8)
                    if sourceAvailable {
                        Button {
                            store.queryPointProfile()
                        } label: {
                            Label(L10n.t("刷新冻结剖面"), systemImage: "waveform.path.ecg")
                                .fireSkyFont(size: 10.5, weight: .medium, relativeTo: .caption)
                        }
                        .buttonStyle(.borderless)
                        .disabled(store.busy)
                        .help(L10n.text("从已发布结果的冻结源重新读取太阳方向剖面", "Reload solar-path cross section from the frozen source"))
                    }
                }
                WorkCrossSectionView(
                    section: section,
                    genusProxy: report["cloud_genus_proxy"] as? JSONObject
                )
                if sourceAvailable && ((store.frozenProfileModel == model && store.frozenProfileState != "idle") || section == nil) {
                    FrozenProfileView(model: model) { store.queryPointProfile() }
                }
            } else {
                Text(L10n.t("等待预测"))
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, minHeight: 160)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .workSurface()
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
    let pathTransmission: [[Double?]]
    let sunlitFractions: [[Double?]]
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
        self.pathTransmission = grid("cloud_path_transmission")
        self.sunlitFractions = grid("cloud_sunlit_fraction")
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

/// 剖面缩放与交互状态容器；本机 CommandLineTools 无 SwiftUIMacros，使用 ObservableObject 代替 @State。
private final class CrossSectionZoomState: ObservableObject {
    @Published var scale = 1.0
    @Published var showIsobars = false
    @Published var hoverDistanceKM: Double? = nil
}

/// 距离×高度剖面图层组件（regional-7，区域/单点共用，方案 :123-124）。
/// 图层：暮光天空渐变、地影区与受光走廊、连续平滑云层轮廓与火烧云受光云底辉光、
/// 可选温度/等压线、地形轮廓、观测点与主可烧云底视线；缺测层不绘制、不解释成晴空。
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
    private let plotLeftMargin: Double = 44
    private let plotTopMargin: Double = 14
    private let plotBottomOffset: Double = 24

    private var plotRightMargin: Double { zoom.showIsobars ? 46 : 16 }

    private var data: CrossSectionData? { CrossSectionData(section: section) }

    private struct ColumnStratum {
        let baseASL: Double
        let topASL: Double
        let amount: Double
        let status: Int
        let pathTransmission: Double?
    }

    private struct CloudRunPoint {
        let sampleIndex: Int
        let cx: Double
        let topY: Double
        let bottomY: Double
        let stratum: ColumnStratum
    }

    private struct BurnableTargetFocus {
        let distanceKM: Double
        let baseASL: Double
    }

    private struct TakeawaySummary {
        let text: String
        let status: Int // 3 = lit & visible, 2 = occluded, 0 = none
    }

    private var canvasAccessibilityLabel: String {
        guard let data else { return L10n.text("大气剖面图不可用", "Atmospheric cross-section unavailable") }
        let low = Int(data.terrain.min() ?? 0)
        let high = Int(data.terrain.max() ?? 0)
        return L10n.text(
            "大气剖面图，\(data.distances.count) 个距离采样点，\(data.levels.count) 个气压层，地形高度 \(low) 至 \(high) 米，\(data.targets.count) 个目标云层标记",
            "Atmospheric cross-section, \(data.distances.count) distance samples, \(data.levels.count) pressure levels, terrain \(low)–\(high) m, \(data.targets.count) target markers"
        )
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            if let data {
                takeawayBanner(for: data)
                plotControls
                GeometryReader { geometry in
                    let plotWidth = geometry.size.width * CGFloat(zoomScale)
                    let plotHeight = geometry.size.height
                    ZStack(alignment: .topLeading) {
                        ScrollView(.horizontal) {
                            Canvas { context, size in
                                draw(context: context,
                                     size: size,
                                     data: data,
                                     scale: zoomScale,
                                     showIsobars: zoom.showIsobars,
                                     hoverDistanceKM: zoom.hoverDistanceKM)
                            }
                            .frame(width: plotWidth, height: plotHeight)
                            .contentShape(Rectangle())
                            .onContinuousHover { phase in
                                switch phase {
                                case .active(let location):
                                    let left = plotLeftMargin
                                    let right = Double(plotWidth) - plotRightMargin
                                    let top = plotTopMargin
                                    let bottom = Double(plotHeight) - plotBottomOffset
                                    guard right > left + 10,
                                          Double(location.x) >= left, Double(location.x) <= right,
                                          Double(location.y) >= top, Double(location.y) <= bottom else {
                                        zoom.hoverDistanceKM = nil
                                        return
                                    }
                                    let ratio = Swift.min(1.0, Swift.max(0.0, (Double(location.x) - left) / (right - left)))
                                    let maxDist = Swift.max(1.0, data.distances.last ?? 1.0)
                                    zoom.hoverDistanceKM = ratio * maxDist
                                case .ended:
                                    zoom.hoverDistanceKM = nil
                                @unknown default:
                                    zoom.hoverDistanceKM = nil
                                }
                            }
                        }
                        .frame(height: plotHeight)
                        .scrollIndicators(zoomScale > 1 ? .automatic : .hidden)

                        if let hoverDistance = zoom.hoverDistanceKM {
                            hoverOverlayCard(data: data, distanceKM: hoverDistance)
                                .padding(.leading, plotLeftMargin + 8)
                                .padding(.top, plotTopMargin + 8)
                                .allowsHitTesting(false)
                        }
                    }
                }
                .aspectRatio(plotAspectRatio / CGFloat(zoomScale), contentMode: .fit)
                .accessibilityLabel(canvasAccessibilityLabel)
                .accessibilityHint(L10n.text("使用放大和缩小按钮查看细节，或选择适配窗口恢复完整剖面。",
                                             "Use zoom buttons for detail or reset to fit the full cross-section."))
                .accessibilityAdjustableAction { direction in
                    switch direction {
                    case .increment: zoomScale = Swift.min(2.5, zoomScale + 0.25)
                    case .decrement: zoomScale = Swift.max(1, zoomScale - 0.25)
                    @unknown default: break
                    }
                }
                .accessibilityValue("\(Int((zoomScale * 100).rounded()))%")
                legend
                genusSummary
                targetStrip(data.targets)
            } else {
                Text(L10n.text("后端未返回可绘制的剖面字段；可在诊断中查看理论目标与照明基准。",
                               "No cross-section grid returned; inspect theoretical targets and illumination baseline in diagnostics."))
                    .font(.system(size: 10)).foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, minHeight: 120, alignment: .center)
            }
        }
    }

    // MARK: takeaway & controls

    private func takeawayBanner(for data: CrossSectionData) -> some View {
        let summary = buildTakeaway(for: data)
        let accent: Color = summary.status == 3
            ? Color(red: 0.96, green: 0.42, blue: 0.18)
            : summary.status == 2
                ? Color(red: 0.86, green: 0.56, blue: 0.20)
                : .secondary
        let iconName = summary.status == 3 ? "sun.horizon.fill"
            : summary.status == 2 ? "cloud.sun" : "sun.min"
        return HStack(spacing: 6) {
            Image(systemName: iconName)
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(accent)
            Text(summary.text)
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(.primary)
                .lineLimit(1)
                .truncationMode(.tail)
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(
            accent.opacity(summary.status == 0 ? 0.06 : (scheme == .dark ? 0.14 : 0.09)),
            in: RoundedRectangle(cornerRadius: 7)
        )
        .overlay {
            RoundedRectangle(cornerRadius: 7)
                .stroke(accent.opacity(summary.status == 0 ? 0.18 : 0.36), lineWidth: 0.9)
        }
        .accessibilityElement(children: .combine)
    }

    private func buildTakeaway(for data: CrossSectionData) -> TakeawaySummary {
        let azText = number(section["solar_azimuth_deg"]).map { String(format: "%.0f°", $0) } ?? "—"
        let elText = number(section["solar_elevation_deg"]).map { String(format: "%.1f°", $0) } ?? "—"
        let solarPrefix = L10n.text("太阳方位 \(azText) · 仰角 \(elText)", "Sun Az \(azText) · El \(elText)")

        var litDistances: [Double] = []
        var litBasesM: [Double] = []
        var litFamilies = Set<Int>()
        var hasOccluded = false

        for i in data.distances.indices {
            for family in 1...3 {
                guard let stratum = columnStratum(data: data, sample: i, family: family) else { continue }
                if stratum.status == 3 {
                    litDistances.append(data.distances[i])
                    litBasesM.append(stratum.baseASL)
                    litFamilies.insert(family)
                } else if stratum.status == 2 {
                    hasOccluded = true
                }
            }
        }

        if !litDistances.isEmpty,
           let minDist = litDistances.min(), let maxDist = litDistances.max(),
           let minBase = litBasesM.min(), let maxBase = litBasesM.max() {
            let familyNamesZh = [3, 2, 1].filter { litFamilies.contains($0) }.map { familyShortZh($0) }.joined(separator: "/")
            let familyNamesEn = [3, 2, 1].filter { litFamilies.contains($0) }.map { familyShortEn($0) }.joined(separator: "/")
            let distText = abs(maxDist - minDist) < 1
                ? String(format: "%.0f", minDist)
                : String(format: "%.0f–%.0f", minDist, maxDist)
            let baseText = abs(maxBase - minBase) < 150
                ? String(format: "%.1f", minBase / 1000)
                : String(format: "%.1f–%.1f", minBase / 1000, maxBase / 1000)
            let detail = L10n.text(
                "约 \(distText) km 处\(familyNamesZh)云底（\(baseText) km）进入受光区且通视",
                "\(familyNamesEn) cloud base at ~\(distText) km (\(baseText) km ASL) is sunlit & visible"
            )
            return TakeawaySummary(text: "\(solarPrefix) · \(detail)", status: 3)
        } else if hasOccluded {
            let detail = L10n.text(
                "远场云层达到受光高度，但观测视线受前段云层或地形遮挡",
                "Far-field clouds reach sunlit altitude, but sightline is occluded by foreground clouds or terrain"
            )
            return TakeawaySummary(text: "\(solarPrefix) · \(detail)", status: 2)
        } else {
            let detail = L10n.text(
                "沿太阳光路暂无同时满足受光高度与通视条件的云层",
                "No cloud layers along the solar path meet both sunlit and line-of-sight criteria"
            )
            return TakeawaySummary(text: "\(solarPrefix) · \(detail)", status: 0)
        }
    }

    private var plotControls: some View {
        let elevation = diagnosticValue(section["solar_elevation_deg"], digits: 1, suffix: "°")
        let azimuth = diagnosticValue(section["solar_azimuth_deg"], digits: 1, suffix: "°")
        let radius = diagnosticValue(section["analysis_radius_km"], digits: 1, suffix: " km")
        let validTime = timeLabel(section["valid_time_local"] as? String)
        return HStack(spacing: 8) {
            HStack(spacing: 8) {
                telemetryPill(L10n.text("仰角", "El"), elevation)
                telemetryPill(L10n.text("方位", "Az"), azimuth)
                telemetryPill(L10n.text("范围", "Range"), radius)
                telemetryPill(L10n.text("时次", "Time"), validTime)
            }
            .lineLimit(1)
            Spacer(minLength: 6)
            Button {
                zoom.showIsobars.toggle()
            } label: {
                HStack(spacing: 4) {
                    Image(systemName: "waveform.path")
                        .font(.system(size: 10, weight: .medium))
                    Text(L10n.text("等压线", "Isobars"))
                        .font(.system(size: 10.5, weight: .medium))
                }
                .padding(.horizontal, 8)
                .padding(.vertical, 3.5)
                .foregroundStyle(zoom.showIsobars ? coolAccent : Color.secondary)
                .background(
                    zoom.showIsobars ? coolAccent.opacity(scheme == .dark ? 0.22 : 0.14) : Color.secondary.opacity(0.08),
                    in: Capsule()
                )
                .overlay {
                    Capsule().stroke(
                        zoom.showIsobars ? coolAccent.opacity(0.50) : Color.secondary.opacity(0.22),
                        lineWidth: 0.9
                    )
                }
            }
            .buttonStyle(.borderless)
            .help(L10n.text("显示或隐藏等压/温度参考线", "Toggle pressure/temperature reference lines"))
            .accessibilityLabel(L10n.text("等压线", "Isobars"))
            .accessibilityValue(zoom.showIsobars ? L10n.text("已开启", "On") : L10n.text("已关闭", "Off"))

            HStack(spacing: 2) {
                Text("\(Int((zoomScale * 100).rounded()))%")
                    .font(.system(size: 10.5, weight: .medium).monospacedDigit())
                    .foregroundStyle(.secondary)
                    .frame(width: 36, alignment: .trailing)
                    .accessibilityLabel(L10n.text("剖面缩放", "Cross-section zoom"))
                    .accessibilityValue("\(Int((zoomScale * 100).rounded()))%")
                Button {
                    zoomScale = Swift.max(1, zoomScale - 0.25)
                } label: {
                    Image(systemName: "minus.magnifyingglass").frame(width: 22, height: 22)
                }
                .buttonStyle(.borderless)
                .help(L10n.text("缩小剖面", "Zoom out"))
                .accessibilityLabel(L10n.text("缩小剖面", "Zoom out"))
                .disabled(zoomScale <= 1)
                Button {
                    zoomScale = Swift.min(2.5, zoomScale + 0.25)
                } label: {
                    Image(systemName: "plus.magnifyingglass").frame(width: 22, height: 22)
                }
                .buttonStyle(.borderless)
                .help(L10n.text("放大剖面", "Zoom in"))
                .accessibilityLabel(L10n.text("放大剖面", "Zoom in"))
                .disabled(zoomScale >= 2.5)
                Button {
                    zoomScale = 1
                } label: {
                    Image(systemName: "arrow.counterclockwise").frame(width: 22, height: 22)
                }
                .buttonStyle(.borderless)
                .help(L10n.text("适配窗口", "Reset zoom"))
                .accessibilityLabel(L10n.text("适配窗口", "Reset zoom"))
                .disabled(zoomScale == 1)
            }
        }
    }

    private func telemetryPill(_ label: String, _ value: String) -> some View {
        HStack(spacing: 3) {
            Text(label)
                .font(.system(size: 9.5))
                .foregroundStyle(.secondary)
            Text(value)
                .font(.system(size: 10, weight: .medium))
                .monospacedDigit()
                .foregroundStyle(.primary)
        }
        .padding(.horizontal, 7)
        .padding(.vertical, 3)
        .background(.quaternary.opacity(0.40), in: Capsule())
        .overlay { Capsule().stroke(.secondary.opacity(0.18), lineWidth: 0.7) }
    }

    @ViewBuilder
    private func hoverOverlayCard(data: CrossSectionData, distanceKM: Double) -> some View {
        if let nearestIndex = data.distances.indices.min(by: {
            abs(data.distances[$0] - distanceKM) < abs(data.distances[$1] - distanceKM)
        }) {
            let sampleDist = data.distances[nearestIndex]
            let terrainText = data.terrain.indices.contains(nearestIndex)
                ? String(format: "%.0f m", data.terrain[nearestIndex]) : "—"
            let reqText = data.requiredASL.indices.contains(nearestIndex)
                ? String(format: "%.1f km", data.requiredASL[nearestIndex] / 1000) : "—"
            let presentStrata: [(Int, ColumnStratum)] = [1, 2, 3].compactMap { fam in
                guard let s = columnStratum(data: data, sample: nearestIndex, family: fam) else { return nil }
                return (fam, s)
            }
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 8) {
                    Text(String(format: "%.0f km", sampleDist))
                        .font(.system(size: 10.5, weight: .semibold))
                        .monospacedDigit()
                    Text("\(L10n.text("地形", "Terrain")) \(terrainText)")
                        .font(.system(size: 9.5))
                        .foregroundStyle(.secondary)
                        .monospacedDigit()
                    Text("\(L10n.text("受光基准", "Sunlit base")) \(reqText)")
                        .font(.system(size: 9.5))
                        .foregroundStyle(Color(red: 0.90, green: 0.58, blue: 0.20))
                        .monospacedDigit()
                }
                if presentStrata.isEmpty {
                    Text(L10n.text("该列无显著模式云层（缺测或低于阈值）", "No significant clouds (missing or below threshold)"))
                        .font(.system(size: 9.5))
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(presentStrata, id: \.0) { fam, stratum in
                        let statusText = stratum.status == 3
                            ? L10n.text("受光可烧", "Sunlit & visible")
                            : stratum.status == 2
                                ? L10n.text("达到受光但被遮挡", "Lit but occluded")
                                : L10n.text("地影区内", "In Earth shadow")
                        let statusColor: Color = stratum.status == 3
                            ? Color(red: 0.96, green: 0.42, blue: 0.18)
                            : stratum.status == 2
                                ? Color(red: 0.88, green: 0.58, blue: 0.20)
                                : .secondary
                        let transmissionText = stratum.pathTransmission.map {
                            " · T_c \(Int(($0 * 100).rounded()))%"
                        } ?? ""
                        HStack(spacing: 5) {
                            Circle().fill(statusColor).frame(width: 5.5, height: 5.5)
                            Text("\(familyShortZh(fam)) \(String(format: "%.1f–%.1f km", stratum.baseASL / 1000, stratum.topASL / 1000))")
                                .font(.system(size: 9.5, weight: .medium))
                                .monospacedDigit()
                            Text("· \(statusText)\(transmissionText)")
                                .font(.system(size: 9.5))
                                .foregroundStyle(statusColor)
                        }
                    }
                }
            }
            .padding(.horizontal, 9)
            .padding(.vertical, 6)
            .fireSkySurface(role: .overlay, cornerRadius: 8)
        }
    }

    @ViewBuilder
    private var genusSummary: some View {
        if let genusProxy {
            let label = genusProxy["label"] as? String ?? "unknown"
            let compatible = (genusProxy["compatible_genera"] as? [String] ?? [])
                .map(genusLabel).joined(separator: "、")
            let candidates = genusCandidates(genusProxy["candidate_scores"])
            let confText: String = {
                guard let confidence = genusProxy["confidence"] as? NSNumber,
                      confidence.doubleValue.isFinite else { return "" }
                return " \(Int((confidence.doubleValue * 100).rounded()))%"
            }()
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Image(systemName: "cloud").foregroundStyle(coolAccent).font(.system(size: 10.5))
                Text("云属代理").font(.system(size: 10, weight: .semibold))
                if label != "unknown" {
                    Text("\(genusLabel(label))\(confText)").font(.system(size: 10, weight: .semibold))
                } else {
                    Text(compatible.isEmpty ? "未定" : "兼容：\(compatible)")
                        .font(.system(size: 10, weight: .medium))
                }
                if let candidates {
                    Text("· 候选：\(candidates)")
                        .font(.system(size: 9.5))
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .truncationMode(.tail)
                }
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 9)
            .padding(.vertical, 5)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.secondary.opacity(0.055), in: RoundedRectangle(cornerRadius: 6))
            .help("依据压力层、相态与云层几何的模型代理，不代表卫星或人工云属观测。")
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

    // MARK: stratum & geometry helpers

    private func scalar(_ grid: [[Double?]], _ sample: Int, _ level: Int) -> Double? {
        guard grid.indices.contains(sample), grid[sample].indices.contains(level) else { return nil }
        return grid[sample][level]
    }

    private func integer(_ grid: [[Int]], _ sample: Int, _ level: Int) -> Int? {
        guard grid.indices.contains(sample), grid[sample].indices.contains(level) else { return nil }
        return grid[sample][level]
    }

    private func columnStratum(data: CrossSectionData, sample: Int, family: Int) -> ColumnStratum? {
        let cloudyLevels = data.levels.indices.filter { level in
            guard integer(data.familyCodes, sample, level) == family else { return false }
            let amount = scalar(data.amounts, sample, level)
                ?? Swift.max(scalar(data.presence, sample, level) ?? 0,
                             (scalar(data.fractions, sample, level) ?? 0) / 100)
            return amount >= 0.06
        }
        guard !cloudyLevels.isEmpty else { return nil }

        let bases = cloudyLevels.compactMap { scalar(data.layerBases, sample, $0) }
        let tops = cloudyLevels.compactMap { scalar(data.layerTops, sample, $0) }
        let heights = cloudyLevels.compactMap { scalar(data.heights, sample, $0) }
        guard let layerBase = bases.min() ?? heights.min().map({ $0 - 700 }),
              let layerTop = tops.max() ?? heights.max().map({ $0 + 700 }),
              layerTop > layerBase else { return nil }

        let amountMax = cloudyLevels.compactMap {
            scalar(data.amounts, sample, $0)
                ?? Swift.max(scalar(data.presence, sample, $0) ?? 0,
                             (scalar(data.fractions, sample, $0) ?? 0) / 100)
        }.max() ?? 0.2
        let status = cloudyLevels.compactMap { integer(data.statuses, sample, $0) }.max() ?? 0
        var transmissionTotal = 0.0
        var transmissionWeight = 0.0
        for level in cloudyLevels {
            guard let lit = scalar(data.sunlitFractions, sample, level), lit > 0,
                  let transmission = scalar(data.pathTransmission, sample, level) else { continue }
            let amount = scalar(data.amounts, sample, level)
                ?? Swift.max(scalar(data.presence, sample, level) ?? 0,
                             (scalar(data.fractions, sample, level) ?? 0) / 100)
            let weight = Swift.max(0.06, amount) * lit
            transmissionTotal += transmission * weight
            transmissionWeight += weight
        }
        let pathTransmission = transmissionWeight > 0 ? transmissionTotal / transmissionWeight : nil
        return ColumnStratum(baseASL: layerBase, topASL: layerTop, amount: amountMax,
                             status: status, pathTransmission: pathTransmission)
    }

    private func familyShortZh(_ family: Int) -> String {
        switch family {
        case 1: return "高"
        case 2: return "中"
        default: return "低"
        }
    }

    private func familyShortEn(_ family: Int) -> String {
        switch family {
        case 1: return "High"
        case 2: return "Mid"
        default: return "Low"
        }
    }

    private func midpoint(_ a: CGPoint, _ b: CGPoint) -> CGPoint {
        CGPoint(x: (a.x + b.x) * 0.5, y: (a.y + b.y) * 0.5)
    }

    private func smoothOpenCurve(points: [CGPoint]) -> Path {
        var path = Path()
        guard let first = points.first else { return path }
        path.move(to: first)
        guard points.count > 1 else { return path }
        if points.count == 2 {
            path.addLine(to: points[1])
            return path
        }
        path.addLine(to: midpoint(points[0], points[1]))
        for idx in 1 ..< (points.count - 1) {
            path.addQuadCurve(to: midpoint(points[idx], points[idx + 1]), control: points[idx])
        }
        path.addLine(to: points[points.count - 1])
        return path
    }

    private func appendSmoothSegment(to path: inout Path, points: [CGPoint]) {
        guard points.count > 1 else { return }
        if points.count == 2 {
            path.addLine(to: points[1])
            return
        }
        path.addLine(to: midpoint(points[0], points[1]))
        for idx in 1 ..< (points.count - 1) {
            path.addQuadCurve(to: midpoint(points[idx], points[idx + 1]), control: points[idx])
        }
        path.addLine(to: points[points.count - 1])
    }

    private func cloudSilhouettePath(run: [CloudRunPoint], left: Double, right: Double, columnSpan: Double) -> (closed: Path, topEdge: Path, bottomEdge: Path, leftX: Double, rightX: Double) {
        let taper = Swift.min(columnSpan * 0.45, (right - left) * 0.06)
        let first = run[0]
        let last = run[run.count - 1]
        let leftX = Swift.max(left, first.cx - taper)
        let rightX = Swift.min(right, last.cx + taper)
        let leftMidY = (first.topY + first.bottomY) * 0.5
        let rightMidY = (last.topY + last.bottomY) * 0.5

        var topPts: [CGPoint] = [CGPoint(x: leftX, y: leftMidY)]
        for p in run { topPts.append(CGPoint(x: p.cx, y: p.topY)) }
        topPts.append(CGPoint(x: rightX, y: rightMidY))

        var botPts: [CGPoint] = [CGPoint(x: rightX, y: rightMidY)]
        for p in run.reversed() { botPts.append(CGPoint(x: p.cx, y: p.bottomY)) }
        botPts.append(CGPoint(x: leftX, y: leftMidY))

        var closed = Path()
        closed.move(to: topPts[0])
        appendSmoothSegment(to: &closed, points: topPts)
        appendSmoothSegment(to: &closed, points: botPts)
        closed.closeSubpath()

        let topEdge = smoothOpenCurve(points: topPts)
        let bottomEdge = smoothOpenCurve(points: botPts.reversed())
        return (closed, topEdge, bottomEdge, leftX, rightX)
    }

    // MARK: drawing

    private func draw(context: GraphicsContext, size: CGSize, data: CrossSectionData,
                      scale: Double, showIsobars: Bool, hoverDistanceKM: Double?) {
        let left = plotLeftMargin
        let right = size.width - (showIsobars ? 46.0 : 16.0)
        let top = plotTopMargin
        let bottom = size.height - plotBottomOffset
        guard bottom > top + 20, right > left + 20 else { return }

        let maxHeight = 18_000.0
        let strokeScale = CGFloat(scale)
        let labelSize = CGFloat(Swift.min(13, Swift.max(9.5, 9.5 * scale)))
        let maxDistance = Swift.max(1, data.distances.last ?? 1)
        let x = { (distance: Double) in left + Swift.min(Swift.max(distance, 0), maxDistance) / maxDistance * (right - left) }
        let y = { (height: Double) in top + (1 - Swift.min(Swift.max(height, 0), maxHeight) / maxHeight) * (bottom - top) }

        let plotRect = CGRect(x: left, y: top, width: right - left, height: bottom - top)
        let plotPath = Path(roundedRect: plotRect, cornerRadius: 10)
        let hasRequired = data.requiredASL.count == data.distances.count
        let reqPoints: [CGPoint] = hasRequired
            ? data.distances.indices.map { CGPoint(x: x(data.distances[$0]), y: y(data.requiredASL[$0])) }
            : []

        context.drawLayer { layer in
            layer.clip(to: plotPath)

            // Layer 1 — Twilight sky backdrop (vertical twilight + horizontal warming toward solar horizon on right)
            let verticalSky = Gradient(colors: scheme == .dark
                ? [Color(red: 0.06, green: 0.10, blue: 0.19),
                   Color(red: 0.12, green: 0.17, blue: 0.27),
                   Color(red: 0.22, green: 0.19, blue: 0.22)]
                : [Color(red: 0.80, green: 0.88, blue: 0.96),
                   Color(red: 0.91, green: 0.94, blue: 0.98),
                   Color(red: 0.98, green: 0.92, blue: 0.86)])
            layer.fill(
                plotPath,
                with: .linearGradient(verticalSky,
                                      startPoint: CGPoint(x: left, y: top),
                                      endPoint: CGPoint(x: left, y: bottom))
            )
            let horizonGlow = Gradient(colors: [
                Color.clear,
                Color(red: 0.98, green: 0.63, blue: 0.34).opacity(scheme == .dark ? 0.18 : 0.20)
            ])
            layer.fill(
                plotPath,
                with: .linearGradient(horizonGlow,
                                      startPoint: CGPoint(x: left + (right - left) * 0.38, y: top + (bottom - top) * 0.25),
                                      endPoint: CGPoint(x: right, y: bottom))
            )

            // Layer 2 — Earth Shadow (below requiredASL) & Sunlit Corridor (above requiredASL) + subtle rays
            if reqPoints.count > 1 {
                var shadowPath = Path()
                shadowPath.move(to: reqPoints[0])
                appendSmoothSegment(to: &shadowPath, points: reqPoints)
                shadowPath.addLine(to: CGPoint(x: right, y: bottom))
                shadowPath.addLine(to: CGPoint(x: left, y: bottom))
                shadowPath.closeSubpath()
                let shadowColor = scheme == .dark
                    ? Color(red: 0.03, green: 0.06, blue: 0.13).opacity(0.28)
                    : Color(red: 0.24, green: 0.32, blue: 0.45).opacity(0.12)
                layer.fill(shadowPath, with: .color(shadowColor))

                var sunlitPath = Path()
                sunlitPath.move(to: CGPoint(x: left, y: top))
                sunlitPath.addLine(to: CGPoint(x: right, y: top))
                sunlitPath.addLine(to: reqPoints[reqPoints.count - 1])
                appendSmoothSegment(to: &sunlitPath, points: reqPoints.reversed())
                sunlitPath.closeSubpath()

                let sunlitGrad = Gradient(colors: [
                    Color(red: 0.98, green: 0.68, blue: 0.30).opacity(0.02),
                    Color(red: 0.98, green: 0.60, blue: 0.24).opacity(scheme == .dark ? 0.16 : 0.15)
                ])
                layer.fill(
                    sunlitPath,
                    with: .linearGradient(sunlitGrad,
                                          startPoint: CGPoint(x: left, y: top),
                                          endPoint: CGPoint(x: right, y: bottom))
                )

                layer.drawLayer { rayLayer in
                    rayLayer.clip(to: sunlitPath)
                    let width = right - left
                    let height = bottom - top
                    for idx in 0..<4 {
                        let offset = Double(idx) * 0.19
                        let xTop = right - width * (0.04 + offset)
                        let rayW = width * 0.065
                        let slant = width * 0.34
                        var ray = Path()
                        ray.move(to: CGPoint(x: xTop, y: top - 4))
                        ray.addLine(to: CGPoint(x: xTop + rayW, y: top - 4))
                        ray.addLine(to: CGPoint(x: xTop + rayW - slant, y: bottom + 4))
                        ray.addLine(to: CGPoint(x: xTop - slant, y: bottom + 4))
                        ray.closeSubpath()
                        rayLayer.fill(
                            ray,
                            with: .linearGradient(
                                Gradient(colors: [
                                    Color(red: 1.0, green: 0.84, blue: 0.56).opacity(0.07),
                                    Color(red: 1.0, green: 0.72, blue: 0.38).opacity(0.0)
                                ]),
                                startPoint: CGPoint(x: right, y: top),
                                endPoint: CGPoint(x: left, y: top + height * 0.85)
                            )
                        )
                    }
                }
            }

            // Layer 3 — Altitude & dashed distance gridlines (+ optional Isobars)
            for km in stride(from: 0.0, through: 18_000.0, by: 3000.0) {
                let lineY = y(km)
                var grid = Path()
                grid.move(to: CGPoint(x: left, y: lineY))
                grid.addLine(to: CGPoint(x: right, y: lineY))
                layer.stroke(grid, with: .color(Color.secondary.opacity(km == 0 ? 0.08 : 0.15)), lineWidth: 0.6)
            }
            let distStep = maxDistance <= 120 ? 25.0 : maxDistance <= 250 ? 50.0 : 100.0
            for dist in stride(from: 0.0, through: maxDistance, by: distStep) where dist > 0 {
                let lineX = x(dist)
                var grid = Path()
                grid.move(to: CGPoint(x: lineX, y: top))
                grid.addLine(to: CGPoint(x: lineX, y: bottom))
                layer.stroke(grid, with: .color(Color.secondary.opacity(0.12)),
                             style: StrokeStyle(lineWidth: 0.6, dash: [3, 4]))
            }

            if showIsobars {
                for l in data.levels.indices {
                    var pts: [CGPoint] = []
                    var temps: [Double] = []
                    for i in data.distances.indices {
                        guard let h = scalar(data.heights, i, l) else { continue }
                        pts.append(CGPoint(x: x(data.distances[i]), y: y(h)))
                        if let temp = scalar(data.temps, i, l) { temps.append(temp) }
                    }
                    guard pts.count > 1 else { continue }
                    let isoPath = smoothOpenCurve(points: pts)
                    let avg = temps.isEmpty ? 0 : temps.reduce(0, +) / Double(temps.count)
                    let isoColor = avg < -30 ? Color(red: 0.48, green: 0.56, blue: 0.72)
                        : avg < 0 ? Color(red: 0.68, green: 0.50, blue: 0.46)
                        : Color(red: 0.80, green: 0.42, blue: 0.30)
                    layer.stroke(isoPath, with: .color(isoColor.opacity(0.35)), lineWidth: 1.0)
                }
            }

            // Layer 4 — Continuous organic cloud strata (low 3 -> mid 2 -> high 1)
            let sampleCount = Swift.max(1, data.distances.count - 1)
            let columnSpan = (right - left) / Double(sampleCount)

            for family in [3, 2, 1] {
                var runs: [[CloudRunPoint]] = []
                var currentRun: [CloudRunPoint] = []

                for i in data.distances.indices {
                    if let stratum = columnStratum(data: data, sample: i, family: family) {
                        let cx = x(data.distances[i])
                        let terrainY = data.terrain.indices.contains(i) ? y(data.terrain[i]) : bottom
                        var botY = Swift.min(terrainY - 1, y(stratum.baseASL))
                        var topY = Swift.max(top + 2, y(stratum.topASL))
                        if topY > botY - 2 {
                            let mid = Swift.min(Swift.max((topY + botY) * 0.5, top + 3), bottom - 3)
                            topY = mid - 1.5
                            botY = mid + 1.5
                        }
                        currentRun.append(CloudRunPoint(sampleIndex: i, cx: cx, topY: topY, bottomY: botY, stratum: stratum))
                    } else if !currentRun.isEmpty {
                        runs.append(currentRun)
                        currentRun = []
                    }
                }
                if !currentRun.isEmpty { runs.append(currentRun) }

                for run in runs {
                    let shape = cloudSilhouettePath(run: run, left: left, right: right, columnSpan: columnSpan)
                    let avgAmount = run.map(\.stratum.amount).reduce(0, +) / Double(run.count)
                    let alpha = Swift.min(0.86, 0.42 + avgAmount * 0.48)

                    let topColor: Color
                    let botColor: Color
                    switch family {
                    case 1:
                        topColor = (scheme == .dark ? Color(red: 0.78, green: 0.88, blue: 0.97) : Color(red: 0.92, green: 0.96, blue: 1.00)).opacity(alpha)
                        botColor = (scheme == .dark ? Color(red: 0.54, green: 0.70, blue: 0.87) : Color(red: 0.64, green: 0.78, blue: 0.92)).opacity(alpha * 0.92)
                    case 2:
                        topColor = (scheme == .dark ? Color(red: 0.56, green: 0.79, blue: 0.82) : Color(red: 0.68, green: 0.86, blue: 0.88)).opacity(alpha)
                        botColor = (scheme == .dark ? Color(red: 0.36, green: 0.60, blue: 0.65) : Color(red: 0.44, green: 0.67, blue: 0.72)).opacity(alpha * 0.94)
                    default:
                        topColor = (scheme == .dark ? Color(red: 0.50, green: 0.55, blue: 0.61) : Color(red: 0.62, green: 0.67, blue: 0.72)).opacity(alpha)
                        botColor = (scheme == .dark ? Color(red: 0.33, green: 0.38, blue: 0.44) : Color(red: 0.44, green: 0.49, blue: 0.54)).opacity(alpha * 0.96)
                    }

                    let minTopY = run.map(\.topY).min() ?? top
                    let maxBotY = run.map(\.bottomY).max() ?? bottom
                    layer.fill(
                        shape.closed,
                        with: .linearGradient(Gradient(colors: [topColor, botColor]),
                                              startPoint: CGPoint(x: shape.leftX, y: minTopY),
                                              endPoint: CGPoint(x: shape.leftX, y: maxBotY))
                    )
                    layer.stroke(shape.topEdge,
                                 with: .color(Color.white.opacity(scheme == .dark ? 0.25 : 0.45)),
                                 lineWidth: 1.0)

                    var hatchStart = 0
                    while hatchStart < run.count {
                        let attenuated = run[hatchStart].stratum.status == 3
                            && (run[hatchStart].stratum.pathTransmission ?? 1.0) < 0.995
                        var hatchEnd = hatchStart
                        while hatchEnd + 1 < run.count {
                            let next = run[hatchEnd + 1].stratum.status == 3
                                && (run[hatchEnd + 1].stratum.pathTransmission ?? 1.0) < 0.995
                            guard next == attenuated else { break }
                            hatchEnd += 1
                        }
                        if attenuated {
                            let hatchLeft = hatchStart == 0 ? shape.leftX : (run[hatchStart - 1].cx + run[hatchStart].cx) * 0.5
                            let hatchRight = hatchEnd == run.count - 1 ? shape.rightX : (run[hatchEnd].cx + run[hatchEnd + 1].cx) * 0.5
                            var hatch = Path()
                            let hatchHeight = bottom - top
                            for startX in stride(from: hatchLeft - hatchHeight, through: hatchRight, by: 9) {
                                hatch.move(to: CGPoint(x: startX, y: bottom))
                                hatch.addLine(to: CGPoint(x: startX + hatchHeight, y: top))
                            }
                            layer.drawLayer { hatchLayer in
                                hatchLayer.clip(to: shape.closed)
                                hatchLayer.clip(to: Path(CGRect(x: hatchLeft, y: top,
                                                              width: Swift.max(1, hatchRight - hatchLeft),
                                                              height: hatchHeight)))
                                hatchLayer.stroke(hatch,
                                                  with: .color(Color(red: 0.98, green: 0.79, blue: 0.52)
                                                    .opacity(scheme == .dark ? 0.34 : 0.42)),
                                                  lineWidth: 0.8)
                            }
                        }
                        hatchStart = hatchEnd + 1
                    }

                    // Fire-sky illuminated base underglow (status == 3) & lit-but-occluded (status == 2)
                    var subStart = 0
                    while subStart < run.count {
                        let st = run[subStart].stratum.status
                        var subEnd = subStart
                        while subEnd + 1 < run.count && run[subEnd + 1].stratum.status == st {
                            subEnd += 1
                        }
                        if st == 3 || st == 2 {
                            let subLeft = subStart == 0 ? shape.leftX : (run[subStart - 1].cx + run[subStart].cx) * 0.5
                            let subRight = subEnd == run.count - 1 ? shape.rightX : (run[subEnd].cx + run[subEnd + 1].cx) * 0.5
                            let subRun = Array(run[subStart...subEnd])
                            let subMinTop = subRun.map(\.topY).min() ?? minTopY
                            let subMaxBot = subRun.map(\.bottomY).max() ?? maxBotY

                            var subBotPts: [CGPoint] = []
                            if subStart == 0 {
                                subBotPts.append(CGPoint(x: shape.leftX, y: (run[0].topY + run[0].bottomY) * 0.5))
                            } else {
                                subBotPts.append(CGPoint(x: subLeft, y: (run[subStart - 1].bottomY + run[subStart].bottomY) * 0.5))
                            }
                            for p in subRun { subBotPts.append(CGPoint(x: p.cx, y: p.bottomY)) }
                            if subEnd == run.count - 1 {
                                subBotPts.append(CGPoint(x: shape.rightX, y: (run[subEnd].topY + run[subEnd].bottomY) * 0.5))
                            } else {
                                subBotPts.append(CGPoint(x: subRight, y: (run[subEnd].bottomY + run[subEnd + 1].bottomY) * 0.5))
                            }
                            let subBotCurve = smoothOpenCurve(points: subBotPts)

                            layer.drawLayer { cloudLayer in
                                cloudLayer.clip(to: shape.closed)
                                let bandRect = CGRect(x: subLeft, y: top, width: Swift.max(1, subRight - subLeft), height: bottom - top)
                                if st == 3 {
                                    let fireGlow = Gradient(stops: [
                                        .init(color: Color.clear, location: 0.0),
                                        .init(color: Color(red: 1.0, green: 0.620, blue: 0.267).opacity(0.42), location: 0.55),
                                        .init(color: Color(red: 0.961, green: 0.365, blue: 0.157).opacity(0.86), location: 1.0)
                                    ])
                                    cloudLayer.fill(
                                        Path(bandRect),
                                        with: .linearGradient(fireGlow,
                                                              startPoint: CGPoint(x: bandRect.midX, y: subMinTop),
                                                              endPoint: CGPoint(x: bandRect.midX, y: subMaxBot))
                                    )
                                } else {
                                    cloudLayer.fill(
                                        Path(bandRect),
                                        with: .color(Color(red: 0.90, green: 0.60, blue: 0.22).opacity(0.16))
                                    )
                                }
                            }

                            if st == 3 {
                                layer.stroke(subBotCurve,
                                             with: .color(Color(red: 0.98, green: 0.45, blue: 0.18).opacity(0.30)),
                                             style: StrokeStyle(lineWidth: 5.0 * Swift.min(1.3, strokeScale), lineCap: .round, lineJoin: .round))
                                layer.stroke(subBotCurve,
                                             with: .color(Color(red: 1.0, green: 0.54, blue: 0.20)),
                                             style: StrokeStyle(lineWidth: 2.2, lineCap: .round, lineJoin: .round))
                            } else {
                                layer.stroke(subBotCurve,
                                             with: .color(Color(red: 0.88, green: 0.58, blue: 0.20).opacity(0.88)),
                                             style: StrokeStyle(lineWidth: 1.2, lineCap: .round, dash: [4, 3]))
                            }
                        }
                        subStart = subEnd + 1
                    }
                }
            }

            // Layer 5 — Smooth requiredASL illumination threshold curve + inline pill
            if reqPoints.count > 1 {
                let reqCurve = smoothOpenCurve(points: reqPoints)
                layer.stroke(reqCurve,
                             with: .color(Color(red: 0.96, green: 0.66, blue: 0.24).opacity(0.20)),
                             lineWidth: 5.0)
                layer.stroke(reqCurve,
                             with: .color(Color(red: 0.92, green: 0.59, blue: 0.19)),
                             style: StrokeStyle(lineWidth: 1.8, lineCap: .round, dash: [7, 4]))

                let pillIdx = Swift.min(reqPoints.count - 1, Swift.max(0, Int(Double(reqPoints.count) * 0.72)))
                let anchorPt = reqPoints[pillIdx]
                let pillText = Text(L10n.text("受光高度线", "Sunlit Base"))
                    .font(.system(size: 8.5, weight: .semibold))
                    .foregroundStyle(Color(red: 0.96, green: 0.72, blue: 0.34))
                let pillResolved = layer.resolve(pillText)
                let pillTextSize = pillResolved.measure(in: size)
                let pillW = pillTextSize.width + 10
                let pillH = pillTextSize.height + 4
                let pillX = Swift.min(right - pillW - 6, Swift.max(left + 6, anchorPt.x - pillW * 0.5))
                let pillY = Swift.max(top + 6, Swift.min(bottom - pillH - 6, anchorPt.y - pillH - 6))
                let pillRect = CGRect(x: pillX, y: pillY, width: pillW, height: pillH)
                layer.fill(Path(roundedRect: pillRect, cornerRadius: pillH * 0.5),
                           with: .color((scheme == .dark ? Color.black : Color.white).opacity(0.55)))
                layer.stroke(Path(roundedRect: pillRect, cornerRadius: pillH * 0.5),
                             with: .color(Color(red: 0.92, green: 0.59, blue: 0.19).opacity(0.45)),
                             lineWidth: 0.7)
                layer.draw(pillResolved, at: CGPoint(x: pillRect.midX, y: pillRect.midY), anchor: .center)
            }

            // Layer 6 — Smooth mountain/terrain silhouette
            if data.terrain.count == data.distances.count {
                let terrainPts = data.distances.indices.map { CGPoint(x: x(data.distances[$0]), y: y(data.terrain[$0])) }
                var fillPath = Path()
                if let firstT = terrainPts.first {
                    fillPath.move(to: firstT)
                    appendSmoothSegment(to: &fillPath, points: terrainPts)
                    fillPath.addLine(to: CGPoint(x: right, y: bottom))
                    fillPath.addLine(to: CGPoint(x: left, y: bottom))
                    fillPath.closeSubpath()
                }
                let terrainMinY = terrainPts.map(\.y).min() ?? CGFloat(bottom)
                let terrainGrad = Gradient(colors: scheme == .dark
                    ? [Color(red: 0.34, green: 0.40, blue: 0.42).opacity(0.76),
                       Color(red: 0.15, green: 0.18, blue: 0.22).opacity(0.92)]
                    : [Color(red: 0.44, green: 0.49, blue: 0.46).opacity(0.48),
                       Color(red: 0.28, green: 0.33, blue: 0.32).opacity(0.68)])
                layer.fill(fillPath,
                           with: .linearGradient(terrainGrad,
                                                 startPoint: CGPoint(x: left, y: terrainMinY),
                                                 endPoint: CGPoint(x: left, y: bottom)))
                let ridgeCurve = smoothOpenCurve(points: terrainPts)
                layer.stroke(ridgeCurve,
                             with: .color(Color(red: 0.45, green: 0.51, blue: 0.49)),
                             lineWidth: 1.6)
            }

            // Layer 7 — Observer beacon, right-edge sun cue, & observer sightline to primary burnable target
            let observerY = data.terrain.isEmpty ? bottom : y(data.terrain[0])
            let observer = CGPoint(x: x(0), y: Swift.min(bottom - 5, Swift.max(top + 10, observerY)))

            if let targetFocus = primaryBurnableFocus(data: data) {
                let targetPt = CGPoint(x: x(targetFocus.distanceKM), y: y(targetFocus.baseASL))
                var sightline = Path()
                sightline.move(to: observer)
                sightline.addLine(to: targetPt)
                layer.stroke(sightline,
                             with: .color(Color(red: 0.99, green: 0.62, blue: 0.28).opacity(0.62)),
                             style: StrokeStyle(lineWidth: 1.3, dash: [5, 4]))

                let ringOuter = CGRect(x: targetPt.x - 8, y: targetPt.y - 8, width: 16, height: 16)
                layer.fill(Path(ellipseIn: ringOuter), with: .color(Color(red: 0.98, green: 0.45, blue: 0.18).opacity(0.28)))
                let ringInner = CGRect(x: targetPt.x - 4.2, y: targetPt.y - 4.2, width: 8.4, height: 8.4)
                layer.fill(Path(ellipseIn: ringInner), with: .color(Color(red: 1.0, green: 0.56, blue: 0.22)))
                layer.stroke(Path(ellipseIn: ringInner), with: .color(.white.opacity(0.95)), lineWidth: 1.3)

                let baseKM = targetFocus.baseASL / 1000
                let targetText = Text(L10n.text(String(format: "可烧云底 %.1fkm", baseKM),
                                                String(format: "Lit base %.1fkm", baseKM)))
                    .font(.system(size: 9, weight: .semibold))
                    .foregroundStyle(.white)
                let resolvedTarget = layer.resolve(targetText)
                let tSize = resolvedTarget.measure(in: size)
                let tW = tSize.width + 10
                let tH = tSize.height + 4
                let tX = Swift.min(right - tW - 6, Swift.max(left + 6, targetPt.x - tW * 0.5))
                let tY = targetPt.y - tH - 10 >= top + 6 ? targetPt.y - tH - 10 : Swift.min(bottom - tH - 6, targetPt.y + 10)
                let tRect = CGRect(x: tX, y: tY, width: tW, height: tH)
                layer.fill(Path(roundedRect: tRect, cornerRadius: tH * 0.5),
                           with: .color(Color(red: 0.92, green: 0.36, blue: 0.16).opacity(0.88)))
                layer.draw(resolvedTarget, at: CGPoint(x: tRect.midX, y: tRect.midY), anchor: .center)
            }

            let haloRect = CGRect(x: observer.x - 8, y: observer.y - 8, width: 16, height: 16)
            layer.fill(Path(ellipseIn: haloRect), with: .color(Color(red: 0.96, green: 0.48, blue: 0.24).opacity(0.28)))
            let dotRect = CGRect(x: observer.x - 4.5, y: observer.y - 4.5, width: 9, height: 9)
            layer.fill(Path(ellipseIn: dotRect), with: .color(Color(red: 0.96, green: 0.48, blue: 0.24)))
            layer.stroke(Path(ellipseIn: dotRect), with: .color(.white.opacity(0.92)), lineWidth: 1.4)
            layer.draw(
                Text(L10n.text("观测点", "Observer"))
                    .font(.system(size: labelSize, weight: .semibold))
                    .foregroundStyle(.primary),
                at: CGPoint(x: observer.x + 10, y: Swift.max(top + 10, observer.y - 9)),
                anchor: .leading
            )

            let sunCueText: String = {
                if let az = number(section["solar_azimuth_deg"]) {
                    return L10n.text(String(format: "☀ 太阳方向 %.0f°", az), String(format: "☀ Sun %.0f°", az))
                }
                return L10n.text("☀ 太阳方向", "☀ Sun")
            }()
            layer.draw(
                Text(sunCueText)
                    .font(.system(size: 9.5, weight: .semibold))
                    .foregroundStyle(Color(red: 0.98, green: 0.72, blue: 0.34)),
                at: CGPoint(x: right - 10, y: top + 12),
                anchor: .trailing
            )

            // Layer 8 — Vertical hover crosshair
            if let hoverDistanceKM {
                let hx = x(hoverDistanceKM)
                var crosshair = Path()
                crosshair.move(to: CGPoint(x: hx, y: top))
                crosshair.addLine(to: CGPoint(x: hx, y: bottom))
                layer.stroke(crosshair,
                             with: .color(Color.primary.opacity(0.45)),
                             style: StrokeStyle(lineWidth: 1.0, dash: [3, 3]))
            }
        }

        // Crisp rounded plot border
        context.stroke(plotPath,
                       with: .color(Color.secondary.opacity(scheme == .dark ? 0.28 : 0.22)),
                       lineWidth: 0.9)

        // Outside axis labels: Altitude (left)
        for km in stride(from: 0.0, through: 18_000.0, by: 3000.0) {
            context.draw(
                Text("\(Int(km / 1000))km").font(.system(size: labelSize)).foregroundStyle(.secondary),
                at: CGPoint(x: left - 6, y: y(km)),
                anchor: .trailing
            )
        }

        // Outside axis labels: Distance (bottom, skip 0km)
        let distStep = maxDistance <= 120 ? 25.0 : maxDistance <= 250 ? 50.0 : 100.0
        for dist in stride(from: 0.0, through: maxDistance, by: distStep) where dist > 0 {
            let lx = x(dist)
            context.draw(
                Text("\(Int(dist))km").font(.system(size: labelSize)).foregroundStyle(.secondary),
                at: CGPoint(x: lx, y: bottom + 10),
                anchor: dist >= maxDistance - distStep * 0.25 ? .trailing : .center
            )
        }

        // Outside axis labels: Isobars (right)
        if showIsobars {
            var lastLabelY = -Double.infinity
            for l in data.levels.indices {
                let heightsForLevel = data.distances.indices.compactMap { scalar(data.heights, $0, l) }
                guard let lastH = heightsForLevel.last else { continue }
                let lineY = y(lastH)
                guard lineY >= top + 4, lineY <= bottom - 4, abs(lineY - lastLabelY) >= 13 else { continue }
                lastLabelY = lineY
                context.draw(
                    Text("\(data.levels[l])hPa")
                        .font(.system(size: Swift.max(8.5, labelSize - 0.5)))
                        .foregroundStyle(.secondary),
                    at: CGPoint(x: right + 5, y: lineY),
                    anchor: .leading
                )
            }
        }
    }

    private func primaryBurnableFocus(data: CrossSectionData) -> BurnableTargetFocus? {
        for target in data.targets where target["status"] as? String == "burnable" {
            if let dist = number(target["distance_km"]),
               let base = number(target["cloud_base_asl_m"]) {
                return BurnableTargetFocus(distanceKM: dist, baseASL: base)
            }
        }
        // Fallback: midpoint of the strongest status == 3 base
        var litPoints: [(dist: Double, base: Double, amount: Double)] = []
        for i in data.distances.indices {
            for family in 1...3 {
                if let s = columnStratum(data: data, sample: i, family: family), s.status == 3 {
                    litPoints.append((data.distances[i], s.baseASL, s.amount))
                }
            }
        }
        guard !litPoints.isEmpty else { return nil }
        let sorted = litPoints.sorted { $0.dist < $1.dist }
        let mid = sorted[sorted.count / 2]
        return BurnableTargetFocus(distanceKM: mid.dist, baseASL: mid.base)
    }

    private func offsetLabel(_ value: Any?) -> String {
        guard let value = value as? NSNumber, value.doubleValue.isFinite else { return "—" }
        let minutes = Int(value.doubleValue)
        return "\(minutes > 0 ? "+" : "")\(minutes) 分"
    }

    // MARK: legend & target diagnosis

    private var legend: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 12) {
                legendFireBase(L10n.text("火烧云受光云底", "Lit fire-sky base"))
                legendCloudStrata(L10n.text("高/中/低云层", "High/Mid/Low clouds"))
                legendCorridor(L10n.text("受光区/地影区", "Sunlit / Shadow"))
                legendLine(L10n.text("受光高度线", "Sunlit threshold"),
                           Color(red: 0.92, green: 0.59, blue: 0.19), dashed: true)
                legendLine(L10n.text("受光被遮挡", "Lit but occluded"),
                           Color(red: 0.88, green: 0.58, blue: 0.20), dashed: true)
                legendTerrainSight(L10n.text("地形与视线", "Terrain & sightline"))
                legendPathTransmission(L10n.text("路径透射 T_c", "Path transmission T_c"))
            }
            .font(.system(size: 9.5))
            .foregroundStyle(.secondary)
            .padding(.vertical, 2)
        }
        .help(L10n.text("未着色不代表晴空；缺测层不绘制", "Unshaded does not imply clear sky; missing layers are omitted"))
    }

    private func legendFireBase(_ title: String) -> some View {
        HStack(spacing: 4) {
            RoundedRectangle(cornerRadius: 2)
                .fill(LinearGradient(colors: [Color(red: 0.72, green: 0.84, blue: 0.94),
                                              Color(red: 0.961, green: 0.365, blue: 0.157)],
                                     startPoint: .top, endPoint: .bottom))
                .frame(width: 13, height: 8)
                .overlay(alignment: .bottom) {
                    Rectangle().fill(Color(red: 1.0, green: 0.54, blue: 0.20)).frame(height: 1.8)
                }
            Text(title)
        }
    }

    private func legendCloudStrata(_ title: String) -> some View {
        HStack(spacing: 3) {
            Capsule().fill(Color(red: 0.72, green: 0.85, blue: 0.96)).frame(width: 6, height: 7)
            Capsule().fill(Color(red: 0.48, green: 0.74, blue: 0.78)).frame(width: 6, height: 7)
            Capsule().fill(Color(red: 0.46, green: 0.51, blue: 0.57)).frame(width: 6, height: 7)
            Text(title)
        }
    }

    private func legendCorridor(_ title: String) -> some View {
        HStack(spacing: 4) {
            RoundedRectangle(cornerRadius: 2)
                .fill(LinearGradient(colors: [Color(red: 0.98, green: 0.65, blue: 0.26).opacity(0.35),
                                              Color(red: 0.12, green: 0.18, blue: 0.30).opacity(0.45)],
                                     startPoint: .top, endPoint: .bottom))
                .frame(width: 13, height: 8)
            Text(title)
        }
    }

    private func legendTerrainSight(_ title: String) -> some View {
        HStack(spacing: 4) {
            ZStack(alignment: .bottomLeading) {
                Rectangle().fill(Color(red: 0.41, green: 0.46, blue: 0.45).opacity(0.7)).frame(width: 13, height: 4)
                Path { p in
                    p.move(to: CGPoint(x: 1, y: 6))
                    p.addLine(to: CGPoint(x: 12, y: 1))
                }
                .stroke(Color(red: 0.99, green: 0.62, blue: 0.28), style: StrokeStyle(lineWidth: 1.2, dash: [2.5, 2]))
            }
            .frame(width: 13, height: 8)
            Text(title)
        }
    }

    private func legendPathTransmission(_ title: String) -> some View {
        HStack(spacing: 4) {
            ZStack {
                RoundedRectangle(cornerRadius: 1.5)
                    .fill(Color(red: 0.36, green: 0.48, blue: 0.55).opacity(0.42))
                Path { path in
                    path.move(to: CGPoint(x: 1, y: 8))
                    path.addLine(to: CGPoint(x: 8, y: 1))
                    path.move(to: CGPoint(x: 7, y: 8))
                    path.addLine(to: CGPoint(x: 14, y: 1))
                }
                .stroke(Color(red: 0.98, green: 0.79, blue: 0.52).opacity(0.9), lineWidth: 0.8)
            }
            .frame(width: 15, height: 9)
            Text(title)
        }
    }

    private func legendLine(_ title: String, _ color: Color, dashed: Bool) -> some View {
        HStack(spacing: 4) {
            Rectangle().fill(Color.clear).frame(width: 13, height: 0)
                .overlay { Rectangle().stroke(color, style: StrokeStyle(lineWidth: 1.5, dash: dashed ? [3.5, 2.5] : [])) }
                .frame(height: 7)
            Text(title)
        }
    }

    /// Theoretical target status summary (Web target-strip parity).
    private func targetStrip(_ targets: [JSONObject]) -> some View {
        let burnable = targets.filter { $0["status"] as? String == "burnable" }
        return Group {
            if !targets.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    Text(L10n.text("受光与几何/地形可见候选（候选状态未计路径消光）",
                                   "Sunlit & terrain-visible candidates (path extinction shown separately)"))
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
            VStack(alignment: .leading, spacing: 4) {
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
                                .frame(width: 8, height: 8)
                                .overlay { Circle().strokeBorder(FSDesign.chipForeground("done", dark: scheme == .dark), lineWidth: 1.5) }
                                .offset(x: geo.size.width * CGFloat((best - start <= 0 ? best + 1440 : best) - start) / CGFloat(span) - 4.0)
                        }
                    }
                }
                .frame(height: 8)
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
private func modelSourceNote(_ model: String, usesGeosFP: Bool = false) -> String {
    if usesGeosFP {
        return model == "ec"
            ? L10n.text("EC 公共 Open Data · TCC + 压力层 RH 云柱代理 · 不含 GFS 云场 · GEOS-FP 气溶胶",
                        "EC public Open Data · TCC + pressure-level RH cloud-column proxy · no GFS cloud field · GEOS-FP aerosol")
            : L10n.text("GFS 0.25° 全量云场评分 · GEOS-FP 气溶胶",
                        "GFS 0.25° full cloud-field scoring · GEOS-FP aerosol")
    }
    return model == "ec"
        ? L10n.text("EC 公共 Open Data · TCC + 压力层 RH 云柱代理 · 不含 GFS 云场 · CAMS 气溶胶",
                    "EC public Open Data · TCC + pressure-level RH cloud-column proxy · no GFS cloud field · CAMS aerosol")
        : L10n.text("GFS 0.25° 全量云场评分 · CAMS 气溶胶",
                    "GFS 0.25° full cloud-field scoring · CAMS aerosol")
}
