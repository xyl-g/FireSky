import Charts
import SwiftUI

/// 单点工作台（对齐 macOS v1.9.10 Workbench.swift）。
/// iPad（regular）双栏，iPhone（compact）单列滚动 + 底部安全区主操作栏。
struct PointWorkbenchView: View {
    @EnvironmentObject private var store: Store
    @Environment(\.colorScheme) private var scheme
    @Environment(\.horizontalSizeClass) private var sizeClass
    @Environment(\.dynamicTypeSize) private var typeSize
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Namespace private var modeIndicator

    private var isRegularPoint: Bool {
        store.activeWorkbenchMode == .point && sizeClass == .regular && !typeSize.isAccessibilitySize
    }

    var body: some View {
        Group {
            if isRegularPoint {
                VStack(alignment: .leading, spacing: 10) {
                    workbenchHeaderBar
                        .padding(.horizontal, 18)
                        .padding(.top, 12)
                    if store.needsRemoteOnboarding {
                        ScrollView {
                            RemoteOnboardingCard()
                                .padding(.horizontal, 18)
                                .padding(.bottom, 18)
                        }
                    } else {
                        HStack(alignment: .top, spacing: 16) {
                            ScrollView { VStack(spacing: 12) { controls; runControls }.padding(1) }.frame(width: 320)
                            ScrollView {
                                VStack(spacing: 12) { hero; results }.padding(1)
                            }.frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
                        }
                        .padding(.horizontal, 18)
                        .padding(.bottom, 18)
                    }
                }
            } else {
                ScrollView {
                    VStack(spacing: 14) {
                        workbenchHeaderBar
                        if store.needsRemoteOnboarding {
                            RemoteOnboardingCard()
                        } else if store.activeWorkbenchMode == .regional {
                            RegionalWorkbenchView()
                        } else if store.activeWorkbenchMode == .nowcast {
                            NowcastWorkbenchView()
                        } else {
                            controls
                            hero
                            results
                        }
                    }.padding(.horizontal, 16).padding(.vertical, 12)
                }
                .safeAreaInset(edge: .bottom, spacing: 0) {
                    if store.activeWorkbenchMode == .point && !store.needsRemoteOnboarding {
                        VStack(spacing: 0) {
                            Divider().opacity(0.45)
                            runControls.padding(.horizontal, 16).padding(.vertical, 10)
                        }
                        .background(.regularMaterial)
                    }
                }
            }
        }
        .workPageBackground()
    }

    @ViewBuilder
    private var workbenchHeaderBar: some View {
        VStack(alignment: .leading, spacing: 8) {
            if store.isRemoteMode && !store.needsRemoteOnboarding {
                HStack {
                    remoteServerPill
                    Spacer(minLength: 0)
                }
            }
            if !store.needsRemoteOnboarding {
                workbenchModeSwitcher
            }
        }
    }

    private var workbenchModeSwitcher: some View {
        HStack(spacing: 4) {
            ForEach(WorkbenchMode.allCases) { mode in
                let selected = store.workbenchMode == mode.rawValue
                Button {
                    withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.18)) {
                        store.switchWorkbenchMode(mode)
                    }
                } label: {
                    HStack(spacing: 5) {
                        Image(systemName: mode.symbol)
                            .font(.system(size: 12, weight: selected ? .semibold : .regular))
                        Text(mode.title)
                            .font(.subheadline.weight(selected ? .semibold : .regular))
                            .lineLimit(1)
                            .minimumScaleFactor(0.85)
                    }
                    .frame(maxWidth: .infinity, minHeight: 38)
                    .foregroundStyle(selected ? Color.primary : Color.secondary)
                    .background {
                        if selected {
                            Capsule()
                                .fill(scheme == .dark ? Color.white.opacity(0.11) : Color.white.opacity(0.95))
                                .matchedGeometryEffect(id: "ios-wb-mode-pill", in: modeIndicator)
                        }
                    }
                    .overlay(alignment: .bottom) {
                        if selected {
                            Capsule().fill(warm).frame(width: 22, height: 2.5)
                                .offset(y: -2)
                                .matchedGeometryEffect(id: "ios-wb-mode-line", in: modeIndicator)
                        }
                    }
                    .contentShape(Capsule())
                }
                .buttonStyle(.plain)
                .disabled(store.busy)
                .accessibilityIdentifier("workbench-mode-\(mode.rawValue)")
                .accessibilityAddTraits(selected ? .isSelected : [])
            }
        }
        .padding(4)
        .background(
            Capsule().fill(scheme == .dark ? Color(0x16, 0x1E, 0x2E) : Color(0xEA, 0xF0, 0xF6))
        )
        .overlay {
            Capsule().strokeBorder(FSDesign.surfaceBorder(role: .grouped, dark: scheme == .dark, emphasized: false), lineWidth: 1)
        }
    }

    private var remoteServerPill: some View {
        let dark = scheme == .dark
        return Button {
            store.settingsCategory = "通用"
            store.page = .settings
        } label: {
            HStack(spacing: 7) {
                Circle()
                    .fill(store.online ? Color.green : Color.orange)
                    .frame(width: 7, height: 7)
                Label(store.remoteServerHostLabel, systemImage: "network")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.primary)
                    .lineLimit(1)
                if !store.remoteWorkspace.isEmpty {
                    Text("· \(URL(fileURLWithPath: store.remoteWorkspace).lastPathComponent)")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 6)
            .background(FSDesign.surfaceFill(role: .grouped, dark: dark), in: Capsule())
            .overlay {
                Capsule().strokeBorder(FSDesign.surfaceBorder(role: .grouped, dark: dark, emphasized: store.online), lineWidth: 1)
            }
        }
        .buttonStyle(.plain)
    }

    private var controls: some View {
        Group {
            WorkLocationView()
            WorkParametersView()
        }
    }

    private var runControls: some View {
        VStack(spacing: 8) {
            AccentButton(
                title: L10n.start,
                systemImage: "sparkles",
                staleIndicator: store.resultParametersStale,
                disabled: store.busy || !store.online
            ) {
                store.beginCheck()
            }
            if store.busy {
                VStack(spacing: 6) {
                    ProgressView(value: store.percent, total: 100)
                    HStack {
                        Text(store.status).font(.caption2).foregroundStyle(.secondary).lineLimit(2)
                        Spacer()
                        Button { store.cancel() } label: {
                            Image(systemName: "xmark").frame(minWidth: 32, minHeight: 32)
                        }
                        .accessibilityLabel(L10n.cancel)
                    }
                }
            }
            if !store.online && !store.error.isEmpty {
                HStack(alignment: .top, spacing: 8) {
                    VStack(alignment: .leading, spacing: 4) {
                        Label(L10n.text("引擎未连接", "Engine offline"), systemImage: "exclamationmark.triangle")
                            .font(.caption).foregroundStyle(.red)
                        Button(L10n.reconnect) { Task { await store.connect() } }
                            .buttonStyle(.borderless).font(.caption)
                    }
                    Spacer()
                }
            }
        }
    }

    private var hero: some View { WorkHeroView() }
    private var results: some View { WorkResultsView() }
}

/// 观测点卡片（观测点管理 + 地图选点 + 收藏）。
struct WorkLocationView: View {
    @EnvironmentObject private var store: Store
    @Environment(\.colorScheme) private var scheme
    @Environment(\.dynamicTypeSize) private var typeSize
    var body: some View {
        let dark = scheme == .dark
        VStack(alignment: .leading, spacing: 11) {
            let headerLayout = typeSize.isAccessibilitySize
                ? AnyLayout(VStackLayout(alignment: .leading, spacing: 10))
                : AnyLayout(HStackLayout(spacing: 8))
            headerLayout {
                Text(L10n.t("观测点")).font(.headline)
                if !typeSize.isAccessibilitySize { Spacer() }
                WorkSegments(options: ["地图", "收藏"], titles: [L10n.t("地图"), L10n.t("收藏")], selection: $store.locationTab)
                    .frame(maxWidth: typeSize.isAccessibilitySize ? .infinity : 180)
            }
            HStack(spacing: 8) {
                HStack(spacing: 7) {
                    Image(systemName: "magnifyingglass")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                    TextField(L10n.text("搜索地名，如「西湖」或「杭州」", "Search a place"), text: $store.query) {
                        store.search()
                    }
                    .textFieldStyle(.plain)
                    .submitLabel(.search)
                    if !store.query.isEmpty {
                        Button {
                            store.query = ""
                        } label: {
                            Image(systemName: "xmark.circle.fill")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel(L10n.text("清除搜索", "Clear search"))
                    }
                }
                .padding(.horizontal, 11)
                .frame(minHeight: 42)
                .background(
                    FSDesign.surfaceFill(role: .grouped, dark: dark),
                    in: RoundedRectangle(cornerRadius: 10)
                )
                .overlay {
                    RoundedRectangle(cornerRadius: 10)
                        .strokeBorder(FSDesign.surfaceBorder(role: .grouped, dark: dark, emphasized: false), lineWidth: 1)
                }

                Button { store.search() } label: {
                    Group {
                        if store.searching { ProgressView().controlSize(.small) }
                        else { Image(systemName: "arrow.forward").font(.system(size: 15, weight: .semibold)) }
                    }
                    .frame(width: 42, height: 42)
                    .background(
                        FSDesign.surfaceFill(role: .grouped, dark: dark),
                        in: RoundedRectangle(cornerRadius: 10)
                    )
                    .overlay {
                        RoundedRectangle(cornerRadius: 10)
                            .strokeBorder(FSDesign.surfaceBorder(role: .grouped, dark: dark, emphasized: false), lineWidth: 1)
                    }
                }
                .buttonStyle(.plain)
                .foregroundStyle(warm)
                .frame(minWidth: 44, minHeight: 44)
                .disabled(store.searching)
                .accessibilityLabel(L10n.text("搜索", "Search"))

                Button { store.locate() } label: {
                    Group {
                        if store.locating { ProgressView().controlSize(.small) }
                        else { Image(systemName: "location").font(.system(size: 15, weight: .medium)) }
                    }
                    .frame(width: 42, height: 42)
                    .background(
                        FSDesign.surfaceFill(role: .grouped, dark: dark),
                        in: RoundedRectangle(cornerRadius: 10)
                    )
                    .overlay {
                        RoundedRectangle(cornerRadius: 10)
                            .strokeBorder(FSDesign.surfaceBorder(role: .grouped, dark: dark, emphasized: false), lineWidth: 1)
                    }
                }
                .buttonStyle(.plain)
                .foregroundStyle(.primary)
                .frame(minWidth: 44, minHeight: 44)
                .disabled(store.locating)
                .accessibilityLabel(L10n.text("获取当前位置", "Use current location"))
            }
            if store.locationTab == "地图" {
                ForecastMap(store: store, mini: true).frame(height: 196)
                    .clipShape(RoundedRectangle(cornerRadius: 10))
                    .overlay {
                        RoundedRectangle(cornerRadius: 10)
                            .strokeBorder(FSDesign.surfaceBorder(role: .grouped, dark: dark, emphasized: false), lineWidth: 1)
                    }
            } else {
                ScrollView {
                    if store.favorites.isEmpty {
                        VStack(spacing: 6) {
                            Image(systemName: "star").font(.title3).foregroundStyle(.secondary)
                            Text(L10n.t("还没有收藏位置"))
                                .font(.caption.weight(.medium))
                            Text(L10n.text("暂无收藏，可在地图选点后收藏。", "No favorites yet; save one from the map."))
                                .font(.caption2).foregroundStyle(.secondary)
                                .multilineTextAlignment(.center)
                        }
                        .padding(.horizontal, 16)
                        .frame(maxWidth: .infinity, minHeight: 196)
                    } else {
                        VStack(spacing: 0) {
                            ForEach(Array(store.favorites.enumerated()), id: \.offset) { index, item in
                                HStack(spacing: 8) {
                                    Button(item["name"] as? String ?? L10n.text("收藏位置", "Favorite")) {
                                        store.applyLocation(item)
                                    }
                                    .buttonStyle(.borderless)
                                    .frame(maxWidth: .infinity, minHeight: 36, alignment: .leading)
                                    Button(role: .destructive) {
                                        store.removeFavorite(item)
                                    } label: {
                                        Image(systemName: "trash").font(.caption).foregroundStyle(.secondary)
                                            .frame(minWidth: 32, minHeight: 32)
                                    }
                                    .buttonStyle(.borderless)
                                }
                                .padding(.horizontal, 10)
                                if index < store.favorites.count - 1 {
                                    Divider().opacity(0.45).padding(.leading, 10)
                                }
                            }
                        }
                    }
                }
                .frame(height: 196)
                .background(
                    FSDesign.surfaceFill(role: .grouped, dark: dark),
                    in: RoundedRectangle(cornerRadius: 10)
                )
                .overlay {
                    RoundedRectangle(cornerRadius: 10)
                        .strokeBorder(FSDesign.surfaceBorder(role: .grouped, dark: dark, emphasized: false), lineWidth: 1)
                }
            }
            HStack(spacing: 9) {
                Circle().fill(store.metadataReady ? warm : Color.orange).frame(width: 8, height: 8)
                VStack(alignment: .leading, spacing: 3) {
                    Text(store.name).font(.subheadline.weight(.semibold))
                    Text(String(format: "%.4f° N, %.4f° E", store.lat, store.lon))
                        .font(.caption).monospacedDigit().foregroundStyle(.secondary)
                }
            }
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 6) {
                    locationMetadataChips
                }
                VStack(alignment: .leading, spacing: 6) {
                    locationMetadataChips
                }
            }
            .font(.caption).foregroundStyle(.secondary)
            HStack {
                Button(L10n.t("收藏当前点"), systemImage: "star") { store.starLocation() }
                Spacer()
                Button(L10n.t("大地图"), systemImage: "arrow.up.left.and.arrow.down.right") { store.showMapPicker = true }
            }.buttonStyle(.borderless).font(.caption)
        }.padding(15).workSurface().disabled(store.busy)
    }

    @ViewBuilder
    private var locationMetadataChips: some View {
        Text(store.metadataReady
             ? String(format: L10n.text("海拔 %.0f m", "Elevation %.0f m"), store.elevation)
             : L10n.text("海拔未确认", "Elevation unconfirmed"))
            .lineLimit(1)
            .workChip()
        Text(store.timezone).lineLimit(1).workChip()
    }
}

/// 观测参数卡片（目标日期 + 现象 + 手动坐标编辑 + 页面作用域错误）。
struct WorkParametersView: View {
    @EnvironmentObject private var store: Store
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(L10n.t("观测参数")).font(.headline)
                Spacer()
                Button(L10n.reset) { store.chooseDate("今天"); store.phenomenon = "dusk" }
                    .buttonStyle(.borderless).font(.caption)
            }
            Text(L10n.t("目标日期")).font(.caption2).foregroundStyle(.secondary)
            WorkSegments(options: ["今天", "明天", "自定义"],
                         titles: [L10n.t("今天"), L10n.t("明天"), L10n.t("自定义")],
                         selection: Binding(get: { store.dateTab }, set: { store.chooseDate($0) }))
            if store.dateTab == "自定义" {
                WorkDateButton()
            }
            Text(L10n.t("观测现象")).font(.caption2).foregroundStyle(.secondary)
            WorkSegments(options: ["dawn", "dusk"], titles: [L10n.t("朝霞"), L10n.t("晚霞")],
                         selection: $store.phenomenon, accented: true)
            Button(L10n.t("编辑坐标"), systemImage: "slider.horizontal.3") { store.editingCoordinates.toggle() }
                .buttonStyle(.borderless).font(.caption).frame(maxWidth: .infinity)
            if store.editingCoordinates {
                VStack(spacing: 8) {
                    TextField(L10n.t("名称"), text: $store.name)
                        .workInputField(cornerRadius: 8)
                    number(L10n.t("纬度"), $store.lat)
                    number(L10n.t("经度"), $store.lon)
                    number(L10n.text("海拔 (m)", "Elevation (m)"), $store.elevation)
                    TextField(L10n.t("时区"), text: $store.timezone)
                        .workInputField(cornerRadius: 8)
                    Button(L10n.t("获取海拔与时区")) { store.lookupMetadata() }.buttonStyle(.borderless)
                    Toggle(L10n.text("已确认位置数据", "Location data confirmed"), isOn: $store.metadataReady).font(.caption)
                }
            }
            let pageError = store.errorText(for: .point)
            if !pageError.isEmpty {
                HStack(alignment: .top, spacing: 8) {
                    Text(pageError).font(.caption).foregroundStyle(.red).textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 4)
                    Button { store.clearError() } label: {
                        Image(systemName: "xmark").font(.caption).foregroundStyle(.secondary)
                    }
                    .buttonStyle(.borderless)
                    .accessibilityLabel(L10n.close)
                }
                .padding(10)
                .background(Color.red.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
            }
        }.padding(15).workSurface().disabled(store.busy)
    }

    private func number(_ title: String, _ value: Binding<Double>) -> some View {
        HStack {
            Text(title).foregroundStyle(.secondary)
            Spacer()
            TextField(title, value: value, format: .number).labelsHidden().frame(width: 130)
                .keyboardType(.numbersAndPunctuation)
                .multilineTextAlignment(.trailing)
                .workInputField(cornerRadius: 8)
        }
    }
}

struct WorkDateButton: View {
    @EnvironmentObject private var store: Store
    var body: some View {
        Button {
            store.calendarMonth = store.date
            store.showDatePicker.toggle()
        } label: {
            HStack(spacing: 8) {
                Image(systemName: "calendar").foregroundStyle(warm)
                Text(store.targetDateLabel).monospacedDigit()
                Spacer(minLength: 4)
                Image(systemName: "chevron.down").font(.system(size: 9, weight: .medium)).foregroundStyle(.secondary)
            }.font(.caption).padding(.horizontal, 11).frame(minHeight: 38)
                .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 8))
                .contentShape(Rectangle())
        }.buttonStyle(.plain)
            .accessibilityLabel("\(L10n.t("目标日期")) \(store.targetDateLabel)")
            .popover(isPresented: $store.showDatePicker, arrowEdge: .bottom) { WorkCalendarView() }
    }
}

private struct WorkCalendarView: View {
    @EnvironmentObject private var store: Store
    @Environment(\.colorScheme) private var scheme
    private var month: ObservationMonth { ObservationMonth(containing: store.calendarMonth, timezone: store.timezone) }
    private let columns = Array(repeating: GridItem(.fixed(32), spacing: 4), count: 7)
    var body: some View {
        VStack(spacing: 12) {
            HStack {
                Text(month.title).font(.subheadline.weight(.semibold))
                Spacer()
                Button { store.calendarMonth = month.moved(-1) } label: {
                    Image(systemName: "chevron.left").frame(width: 30, height: 30)
                }
                Button { store.calendarMonth = month.moved(1) } label: {
                    Image(systemName: "chevron.right").frame(width: 30, height: 30)
                }
            }.buttonStyle(.plain)
            LazyVGrid(columns: columns, spacing: 4) {
                ForEach(["一", "二", "三", "四", "五", "六", "日"], id: \.self) { day in
                    Text(day).font(.caption2).foregroundStyle(.secondary).frame(width: 32, height: 22)
                }
                ForEach(month.days, id: \.self) { day in
                    let selected = month.calendar.isDate(day, inSameDayAs: store.date)
                    let current = month.calendar.isDate(day, equalTo: month.start, toGranularity: .month)
                    let today = month.calendar.isDate(day, inSameDayAs: .now)
                    Button { select(day) } label: {
                        Text("\(month.calendar.component(.day, from: day))")
                            .font(.caption.weight(selected ? .semibold : .regular)).monospacedDigit()
                            .foregroundStyle(selected ? (scheme == .dark ? Color.black : Color.white)
                                             : current ? Color.primary : Color.secondary.opacity(0.55))
                            .frame(width: 32, height: 32)
                            .background(selected ? warm : .clear, in: Circle())
                            .overlay { Circle().stroke(today && !selected ? warm.opacity(0.7) : .clear, lineWidth: 1) }
                            .contentShape(Circle())
                    }.buttonStyle(.plain).accessibilityLabel(ObservationDate.label(day, timezone: store.timezone))
                        .accessibilityAddTraits(selected ? .isSelected : [])
                }
            }
            Divider()
            HStack {
                Button(L10n.t("今天")) { select(.now) }.buttonStyle(.plain).foregroundStyle(warm)
                Spacer()
                Button { store.showDatePicker = false } label: {
                    Image(systemName: "xmark").frame(width: 30, height: 30)
                }.buttonStyle(.plain)
            }.font(.caption)
        }.padding(14).frame(width: 274)
            .tint(warm).background(.regularMaterial)
    }

    private func select(_ day: Date) {
        store.date = day
        store.showDatePicker = false
    }
}

/// 行动摘要（仅在无结果时展示首次引导卡，有结果时展示双模型结论摘要）。
private struct WorkActionSummaryView: View {
    @EnvironmentObject private var store: Store
    @Environment(\.colorScheme) private var scheme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
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
            return ModelSnapshot(model: model, level: level,
                                 best: timeLabel(report["best_time_local"] as? String),
                                 window: windowLabel(report),
                                 limit: rawLimit?.isEmpty == true ? nil : rawLimit)
        }
    }

    private var failedModels: [String] {
        ForecastComparison.models.filter { comparison.error($0) != nil }
    }

    private var levelsAgree: Bool {
        snapshots.count == 2 && Set(snapshots.map(\.level)).count == 1
    }

    private var nearbyNowcastTime: String? {
        let preferred = [store.selectedModel] + ForecastComparison.models.filter { $0 != store.selectedModel }
        let now = Date()
        for model in preferred {
            guard let raw = comparison.report(model)?["best_time_local"] as? String,
                  let parsed = parseLocalTimestamp(raw, timezone: store.timezone),
                  abs(parsed.timeIntervalSince(now)) <= 4 * 3600 else {
                continue
            }
            return ObservationDate.timeLabel(parsed, timezone: store.timezone)
        }
        return nil
    }

    private var headline: String {
        if snapshots.isEmpty {
            return failedModels.isEmpty
                ? L10n.t("完成一次预测后，这里会给出是否值得外出、最佳时刻和主要限制。")
                : L10n.t("两个模式都未完成，暂不能给出观测建议。")
        }
        if snapshots.count == 1 {
            return L10n.tf("仅 %@ 可用：%@", snapshots[0].model.uppercased(), L10n.qualityLevel(snapshots[0].level))
        }
        return levelsAgree
            ? L10n.tf("两个模式结论一致：%@", L10n.qualityLevel(snapshots[0].level))
            : L10n.tf("两个模式结论不同：GFS %@ · EC %@",
                      L10n.qualityLevel(snapshots[0].level), L10n.qualityLevel(snapshots[1].level))
    }

    private var timingText: String {
        guard let first = snapshots.first else { return "" }
        if levelsAgree, snapshots.allSatisfy({ $0.best == first.best && $0.window == first.window }) {
            return L10n.tf("最佳 %@ · 推荐窗口 %@", first.best, first.window)
        }
        return snapshots.map {
            L10n.tf("%@ 最佳 %@ · 窗口 %@", $0.model.uppercased(), $0.best, $0.window)
        }.joined(separator: "  ·  ")
    }

    var body: some View {
        if snapshots.isEmpty && failedModels.isEmpty && comparison.result == nil {
            WorkFirstRunGuide()
        } else {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 8) {
                    Image(systemName: levelsAgree && failedModels.isEmpty ? "checkmark.circle.fill" : "exclamationmark.triangle")
                        .foregroundStyle(warm)
                    Text(L10n.t("行动摘要")).font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                    Spacer()
                }
                Text(headline).font(.headline).fixedSize(horizontal: false, vertical: true)
                if !timingText.isEmpty {
                    Text(timingText).font(.caption).monospacedDigit().foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let nearbyTime = nearbyNowcastTime {
                    Button {
                        withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.18)) {
                            store.switchWorkbenchMode(.nowcast)
                        }
                    } label: {
                        HStack(spacing: 5) {
                            Image(systemName: "antenna.radiowaves.left.and.right")
                            Text(L10n.text(
                                "最佳时刻临近（\(nearbyTime)）· 切换卫星实况",
                                "Near window (\(nearbyTime)) · Open Satellite Nowcast"
                            ))
                        }
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(coolAccent)
                        .padding(.horizontal, 10)
                        .padding(.vertical, 6)
                        .background(coolAccent.opacity(scheme == .dark ? 0.16 : 0.12), in: Capsule())
                        .overlay {
                            Capsule().strokeBorder(coolAccent.opacity(0.35), lineWidth: 1)
                        }
                    }
                    .buttonStyle(.plain)
                    .disabled(store.busy)
                }
                ForEach(failedModels, id: \.self) { model in
                    Text(L10n.tf("%@ 未完成：%@", model.uppercased(), comparison.error(model) ?? L10n.t("未知原因")))
                        .font(.caption).foregroundStyle(.orange)
                }
                ForEach(snapshots, id: \.model) { snapshot in
                    if let limit = snapshot.limit {
                        Text(L10n.tf("%@ 主要限制：%@", snapshot.model.uppercased(), L10n.qualityLimit(limit)))
                            .font(.caption).foregroundStyle(.orange)
                    }
                }
                if store.resultParametersStale {
                    Text(L10n.t("当前结果对应旧参数，可点「按新参数重算」更新。"))
                        .font(.caption).foregroundStyle(.orange)
                }
            }
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .fireSkySurface(role: .content, emphasized: !snapshots.isEmpty)
        }
    }
}

private struct WorkFirstRunGuide: View {
    @EnvironmentObject private var store: Store
    @Environment(\.colorScheme) private var scheme
    @Environment(\.horizontalSizeClass) private var sizeClass
    @Environment(\.dynamicTypeSize) private var typeSize

    private var useCompactStack: Bool {
        sizeClass == .compact || typeSize.isAccessibilitySize
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Image(systemName: "sparkles").foregroundStyle(warm)
                Text(L10n.text("首次预测 · 准备步骤", "First forecast · steps")).font(.subheadline.weight(.semibold))
                Spacer()
            }
            if useCompactStack {
                VStack(spacing: 8) {
                    compactStepRow("01", "map", L10n.text("选好地点", "Choose a place"),
                                   L10n.text("搜索、地图选点或收藏", "Search, map, or favorite")) {
                        store.showMapPicker = true
                    }
                    compactStepRow("02", "key.horizontal", L10n.text("数据源", "Data sources"),
                                   store.camsConfigured ? L10n.text("CAMS 已配置", "CAMS ready") : L10n.text("CAMS 可选配置", "CAMS optional")) {
                        store.openCAMSSetup()
                    }
                    compactStepRow("03", "arrow.down.circle", L10n.text("预检与预测", "Check & forecast"),
                                   L10n.text("核对缺失数据并计算", "Verify missing files & run")) {
                        store.beginCheck()
                    }
                }
            } else {
                LazyVGrid(columns: [GridItem(.flexible(), spacing: 8), GridItem(.flexible(), spacing: 8), GridItem(.flexible(), spacing: 8)], spacing: 8) {
                    stepCard("01", "map", L10n.text("选好地点", "Choose a place"),
                             L10n.text("搜索、地图选点或收藏", "Search, map, or favorite")) {
                        store.showMapPicker = true
                    }
                    stepCard("02", "key.horizontal", L10n.text("数据源", "Data sources"),
                             store.camsConfigured ? L10n.text("CAMS 已配置", "CAMS ready") : L10n.text("CAMS 可选配置", "CAMS optional")) {
                        store.openCAMSSetup()
                    }
                    stepCard("03", "arrow.down.circle", L10n.text("预检与预测", "Check & forecast"),
                             L10n.text("核对缺失数据并计算", "Verify missing files & run")) {
                        store.beginCheck()
                    }
                }
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .fireSkySurface(role: .content, emphasized: true)
    }

    private func compactStepRow(_ number: String, _ symbol: String, _ title: String, _ detail: String, action: @escaping () -> Void) -> some View {
        let dark = scheme == .dark
        return Button(action: action) {
            HStack(spacing: 10) {
                Image(systemName: symbol)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(warm)
                    .frame(width: 30, height: 30)
                    .background(warm.opacity(dark ? 0.14 : 0.10), in: RoundedRectangle(cornerRadius: 8))
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 5) {
                        Text(number).font(.caption2.weight(.bold)).monospacedDigit().foregroundStyle(.secondary)
                        Text(title).font(.caption.weight(.semibold)).foregroundStyle(.primary)
                    }
                    Text(detail).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                }
                Spacer(minLength: 4)
                Image(systemName: "chevron.right").font(.caption2.weight(.semibold)).foregroundStyle(.tertiary)
            }
            .padding(.horizontal, 10).padding(.vertical, 8)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(FSDesign.surfaceFill(role: .grouped, dark: dark), in: RoundedRectangle(cornerRadius: 10))
            .overlay {
                RoundedRectangle(cornerRadius: 10)
                    .strokeBorder(FSDesign.surfaceBorder(role: .grouped, dark: dark, emphasized: false), lineWidth: 1)
            }
        }
        .buttonStyle(.plain)
    }

    private func stepCard(_ number: String, _ symbol: String, _ title: String, _ detail: String, action: @escaping () -> Void) -> some View {
        let dark = scheme == .dark
        return Button(action: action) {
            VStack(alignment: .leading, spacing: 5) {
                HStack(spacing: 6) {
                    Image(systemName: symbol).font(.caption.weight(.semibold)).foregroundStyle(warm)
                    Text(number).font(.caption2.weight(.bold)).monospacedDigit().foregroundStyle(.secondary)
                    Spacer()
                }
                Text(title).font(.caption.weight(.semibold)).foregroundStyle(.primary)
                Text(detail).font(.caption2).foregroundStyle(.secondary).lineLimit(2)
            }
            .padding(10)
            .frame(maxWidth: .infinity, minHeight: 74, alignment: .topLeading)
            .background(FSDesign.surfaceFill(role: .grouped, dark: dark), in: RoundedRectangle(cornerRadius: 10))
            .overlay {
                RoundedRectangle(cornerRadius: 10)
                    .strokeBorder(FSDesign.surfaceBorder(role: .grouped, dark: dark, emphasized: false), lineWidth: 1)
            }
        }
        .buttonStyle(.plain)
    }
}

/// 结果概览 hero（GFS/EC 双模型摘要 + 云属代理 chip + 时间轴）。
struct WorkHeroView: View {
    @EnvironmentObject private var store: Store
    @Environment(\.horizontalSizeClass) private var sizeClass
    @Environment(\.dynamicTypeSize) private var typeSize
    private var comparison: ForecastComparison { store.pointComparison }
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            WorkActionSummaryView(comparison: comparison)
            if comparison.result != nil {
                VStack(alignment: .leading, spacing: 12) {
                    Text("\(store.resultTitle) \(L10n.t(store.resultPhenomenonLabel))")
                        .font(.caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    WorkStaleResultBanner()
                    if sizeClass == .compact || typeSize.isAccessibilitySize {
                        VStack(alignment: .leading, spacing: 14) {
                            summary("gfs")
                            Divider().opacity(0.5)
                            summary("ec")
                        }
                    } else {
                        HStack(alignment: .top, spacing: 14) {
                            summary("gfs")
                            Divider().frame(minHeight: 140).opacity(0.5)
                            summary("ec")
                        }
                    }
                    if hasAnyGenusProxy {
                        Text(L10n.text("云属标记为模型代理（依据压力层、相态与几何），不代表卫星或人工云属观测。",
                                       "Cloud genus badges are model proxies (pressure/phase/geometry), not satellite or human observations."))
                            .font(.caption2).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .padding(16).frame(maxWidth: .infinity, alignment: .leading).workSurface(emphasized: true)
            }
        }
    }

    private var hasAnyGenusProxy: Bool {
        ForecastComparison.models.contains {
            cloudGenusBrief(comparison.report($0)?["cloud_genus_proxy"] as? JSONObject) != nil
        }
    }

    private func summary(_ model: String) -> some View {
        let report = comparison.report(model)
        let quality = comparison.peak(model)
        return VStack(alignment: .leading, spacing: 8) {
            HStack {
                Circle().fill(modelColor(model)).frame(width: 6, height: 6)
                Text(model.uppercased()).font(.caption.weight(.semibold))
                Spacer(minLength: 4)
                Text(L10n.status(comparison.status(model))).font(.caption2)
                    .foregroundStyle(comparison.error(model) == nil ? Color.secondary : Color.red)
            }
            HStack(alignment: .top, spacing: 8) {
                VStack(alignment: .leading, spacing: 4) {
                    HStack(alignment: .firstTextBaseline, spacing: 4) {
                        Text(format(quality, digits: 2)).font(.system(size: 28, weight: .semibold))
                            .monospacedDigit().foregroundStyle(quality == nil ? Color.secondary : modelColor(model))
                            .lineLimit(1).minimumScaleFactor(0.75)
                        Text("/ 2.5").font(.caption2).foregroundStyle(.secondary)
                    }
                    Text(L10n.qualityLevel(report?["peak_quality_level"] as? String
                                           ?? report?["quality_level"] as? String
                                           ?? L10n.text("峰值质量指数", "Peak quality index")))
                        .font(.caption2).foregroundStyle(.secondary).lineLimit(2)
                }
                Spacer(minLength: 0)
                VStack(alignment: .leading, spacing: 4) {
                    Text(L10n.t("最佳时刻")).font(.caption2).foregroundStyle(.secondary)
                    Text(timeLabel(report?["best_time_local"] as? String))
                        .font(.title3.weight(.semibold)).monospacedDigit()
                }
            }
            SkyTimeline(report: report, timezone: store.timezone)
            if let spectral = report?["spectral_color"] as? JSONObject {
                SpectralColorStripView(spectral: spectral)
            }
            VStack(alignment: .leading, spacing: 4) {
                if let cycle = cycleLabel(model, report) {
                    Text(L10n.tf("起报 %@", cycle)).lineLimit(1).workChip()
                }
                Text(L10n.t(report == nil ? "未预测" : familyLabel(report?["dominant_burnable_pressure_family"])))
                    .lineLimit(1).workChip()
                if let genus = cloudGenusBrief(report?["cloud_genus_proxy"] as? JSONObject) {
                    Text(genus).lineLimit(1).workChip()
                        .accessibilityLabel(L10n.text("\(genus)，模型云属代理，非卫星或人工观测",
                                                      "\(genus), model proxy, not satellite or human observation"))
                }
            }
            .font(.caption2).foregroundStyle(.secondary)
            if let message = comparison.error(model) {
                Text(message).font(.caption2).foregroundStyle(.red).lineLimit(2).textSelection(.enabled)
            }
        }.frame(maxWidth: .infinity, minHeight: 140, alignment: .topLeading)
            .accessibilityElement(children: .contain).accessibilityLabel(model.uppercased())
    }
}

/// 指标卡规格：键与 macOS metricSpec 10 项完全对齐。
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

private func groupTitle(_ key: String) -> String {
    switch key {
    case "cloud": return L10n.t("云况与色彩")
    case "optics": return L10n.t("大气光学")
    default: return L10n.t("时效与可靠性")
    }
}

/// 结果区：无结果时显示干净空态（不显示 10 张空「—」卡）；有结果时显示分析标签页 + 指标卡 + 图表。
struct WorkResultsView: View {
    @EnvironmentObject private var store: Store
    @Environment(\.horizontalSizeClass) private var sizeClass
    @Environment(\.dynamicTypeSize) private var typeSize
    @Environment(\.colorScheme) private var scheme
    private var comparison: ForecastComparison { store.pointComparison }
    private var report: JSONObject? { comparison.report(store.selectedModel) }
    private let groupKeys = [("cloud", 0..<4), ("optics", 4..<7), ("reliability", 7..<10)]

    private var availableRadarModels: [String] {
        ForecastComparison.models.filter { comparison.report($0)?["azimuth_radar"] is JSONObject }
    }

    private var overviewRadarReport: (model: String, radar: JSONObject)? {
        if let radar = comparison.report(store.selectedModel)?["azimuth_radar"] as? JSONObject {
            return (store.selectedModel, radar)
        }
        for model in ForecastComparison.models {
            if let radar = comparison.report(model)?["azimuth_radar"] as? JSONObject {
                return (model, radar)
            }
        }
        return nil
    }

    var body: some View {
        if comparison.result == nil {
            firstRunEmptyCanvas
        } else {
            VStack(spacing: 12) {
                HStack(alignment: .center, spacing: 8) {
                    WorkAnalysisTabs()
                    Spacer(minLength: 4)
                    Button {
                        store.showPointDiagnostics = true
                    } label: {
                        Label(L10n.t("诊断"), systemImage: "info.circle")
                            .font(.caption.weight(.medium))
                            .foregroundStyle(.secondary)
                            .padding(.horizontal, 10)
                            .padding(.vertical, 6)
                            .background(Color.primary.opacity(scheme == .dark ? 0.07 : 0.05), in: Capsule())
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("point-diagnostics-button")
                }
                if store.resultTab != "概览" {
                    HStack {
                        WorkSegments(options: ForecastComparison.models, titles: ["GFS", "EC"], selection: $store.selectedModel)
                            .frame(width: 150)
                        Spacer()
                        Text(L10n.status(comparison.status(store.selectedModel)))
                            .font(.caption2).foregroundStyle(.secondary)
                    }
                }
                if sizeClass == .regular {
                    ScrollView { resultsContent }
                } else {
                    resultsContent
                }
            }
            .sheet(isPresented: $store.showPointDiagnostics) {
                NavigationStack {
                    ScrollView {
                        VStack(spacing: 12) {
                            WorkSegments(options: ForecastComparison.models, titles: ["GFS", "EC"], selection: $store.selectedModel)
                                .frame(width: 160)
                                .frame(maxWidth: .infinity, alignment: .leading)
                            if let message = comparison.error(store.selectedModel) {
                                Text(L10n.tf("%@ 未完成：%@", store.selectedModel.uppercased(), message))
                                    .font(.callout).foregroundStyle(.red).textSelection(.enabled)
                                    .padding(16).frame(maxWidth: .infinity, alignment: .leading).workSurface()
                            } else {
                                WorkDiagnosticView(report: report, profile: false, showHeader: false)
                            }
                        }
                        .padding(16)
                    }
                    .workPageBackground()
                    .navigationTitle(L10n.t("诊断与时间窗"))
                    .navigationBarTitleDisplayMode(.inline)
                    .toolbar {
                        ToolbarItem(placement: .topBarTrailing) {
                            Button(L10n.close) { store.showPointDiagnostics = false }
                        }
                    }
                }
                .tint(warm)
            }
        }
    }

    private var resultsContent: some View {
        VStack(spacing: 12) {
            if store.resultTab == "概览" {
                ForEach(groupKeys, id: \.0) { key, range in
                    VStack(alignment: .leading, spacing: 7) {
                        Text(groupTitle(key)).font(.caption.weight(.medium)).foregroundStyle(.secondary)
                        LazyVGrid(columns: columnsForGroup(key), spacing: 10) {
                            ForEach(Array(metricSpec[range]), id: \.0) { spec in
                                metricCard(spec)
                            }
                        }
                    }
                }
                WorkQualityBreakdownView(comparison: comparison)
                WorkComparisonCurveView(comparison: comparison, timezone: store.timezone)
                if let radarEntry = overviewRadarReport {
                    AzimuthRadarCard(
                        radar: radarEntry.radar,
                        modelLabel: radarEntry.model.uppercased(),
                        availableModels: availableRadarModels,
                        selectedModel: $store.selectedModel
                    )
                }
            } else if let message = comparison.error(store.selectedModel) {
                Text(L10n.tf("%@ 未完成：%@", store.selectedModel.uppercased(), message))
                    .font(.callout).foregroundStyle(.red).textSelection(.enabled)
                    .padding(18).frame(maxWidth: .infinity, alignment: .leading).workSurface()
            } else if report == nil, comparison.result != nil {
                Text(L10n.tf("%@ 暂无结果", store.selectedModel.uppercased()))
                    .foregroundStyle(.secondary).padding(30)
            } else if store.resultTab == "大气剖面" {
                WorkProfileTabView(report: report)
            } else if store.resultTab == "逐时台账" {
                WorkLedgerView(report: report,
                               bestTime: report?["best_time_local"] as? String,
                               selectedTime: $store.pointSelectedTime)
            } else {
                WorkDiagnosticView(report: report, profile: false)
            }
        }
    }

    private var firstRunEmptyCanvas: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 14) {
                FireSkyMarkScene(size: 52)
                VStack(alignment: .leading, spacing: 5) {
                    Text(L10n.text("准备就绪，开始首次朝晚霞预测", "Ready for your first twilight forecast"))
                        .font(.subheadline.weight(.semibold))
                    Text(L10n.text("预测完成后，这里会展示双模式指数、大气垂直剖面和逐时台账。",
                                   "Once complete, you will see GFS/EC scores, the atmospheric cross-section, and hourly ledger here."))
                        .font(.caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 4)
            }
            let previewColumns = (sizeClass == .regular && !typeSize.isAccessibilitySize)
                ? [GridItem(.flexible(), spacing: 10), GridItem(.flexible(), spacing: 10), GridItem(.flexible(), spacing: 10)]
                : [GridItem(.flexible(), spacing: 10)]
            LazyVGrid(columns: previewColumns, spacing: 8) {
                emptyPreviewItem(
                    symbol: "chart.bar.xaxis",
                    title: L10n.text("双模式独立对比", "Independent GFS & EC"),
                    detail: L10n.text("GFS 与 EC 各自评分（0–2.5），不平均、不互相填补。",
                                      "GFS and EC scored independently on 0–2.5 without blending.")
                )
                emptyPreviewItem(
                    symbol: "cloud.sun",
                    title: L10n.text("太阳方向大气剖面", "Solar Atmospheric Profile"),
                    detail: L10n.text("查看高中低云层、地形遮挡与受光高度。",
                                      "Inspect cloud layers, terrain masking, and illuminated altitudes.")
                )
                emptyPreviewItem(
                    symbol: "clock.badge.checkmark",
                    title: L10n.text("最佳时刻与窗口", "Best Time & Window"),
                    detail: L10n.text("给出推荐时段与主要限制因素。",
                                      "Recommended viewing window and limiting factors.")
                )
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .workSurface()
    }

    private func emptyPreviewItem(symbol: String, title: String, detail: String) -> some View {
        let dark = scheme == .dark
        return VStack(alignment: .leading, spacing: 5) {
            HStack(spacing: 7) {
                Image(systemName: symbol).font(.caption.weight(.medium)).foregroundStyle(warm)
                Text(title).font(.caption.weight(.semibold))
            }
            Text(detail).font(.caption2).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(11)
        .frame(maxWidth: .infinity, minHeight: sizeClass == .regular ? 76 : 0, alignment: .topLeading)
        .background(FSDesign.surfaceFill(role: .grouped, dark: dark), in: RoundedRectangle(cornerRadius: 10))
        .overlay {
            RoundedRectangle(cornerRadius: 10)
                .strokeBorder(FSDesign.surfaceBorder(role: .grouped, dark: dark, emphasized: false), lineWidth: 1)
        }
    }

    private func columnsForGroup(_ key: String) -> [GridItem] {
        if typeSize.isAccessibilitySize {
            return [GridItem(.flexible())]
        }
        if sizeClass == .regular {
            // "cloud" has 4 cards -> 2x2 balanced grid; "optics"/"reliability" have 3 cards -> 3x1.
            if key == "cloud" {
                return [GridItem(.flexible(), spacing: 10), GridItem(.flexible(), spacing: 10)]
            }
            return [GridItem(.flexible(), spacing: 10), GridItem(.flexible(), spacing: 10), GridItem(.flexible(), spacing: 10)]
        }
        return [GridItem(.flexible(), spacing: 10), GridItem(.flexible(), spacing: 10)]
    }

    private var columns: [GridItem] {
        if typeSize.isAccessibilitySize {
            return [GridItem(.flexible())]
        }
        if sizeClass == .regular {
            return [GridItem(.flexible(), spacing: 10), GridItem(.flexible(), spacing: 10), GridItem(.flexible(), spacing: 10)]
        }
        return [GridItem(.flexible(), spacing: 10), GridItem(.flexible(), spacing: 10)]
    }

    private func metricCard(_ spec: (String, String, String)) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(L10n.t(spec.1)).font(.caption).foregroundStyle(.primary.opacity(0.85))
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
                                    .font(.caption2.weight(.medium))
                                    .foregroundStyle(modelColor(model)).frame(width: 26, alignment: .leading)
                                Text(metricValue(spec.0, modelReport))
                                    .font(.subheadline.weight(.semibold)).monospacedDigit()
                                    .lineLimit(1).minimumScaleFactor(0.75)
                            }
                            if let genus {
                                Text(genus)
                                    .font(.caption2).foregroundStyle(.secondary)
                                    .padding(.leading, 31)
                                    .lineLimit(1)
                            }
                        }
                    }
                }.frame(maxWidth: .infinity, alignment: .leading)
            } else {
                HStack(spacing: 10) {
                    ForEach(ForecastComparison.models, id: \.self) { model in
                        VStack(alignment: .leading, spacing: 3) {
                            Text(model.uppercased()).font(.caption2.weight(.medium))
                                .foregroundStyle(modelColor(model))
                            Text(metricValue(spec.0, comparison.report(model)))
                                .font(.title3.weight(.semibold)).monospacedDigit()
                                .foregroundStyle(comparison.report(model) == nil ? Color.secondary : Color.primary)
                                .lineLimit(1).minimumScaleFactor(0.8)
                        }.frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
            }
            Text(spec.0 == "aod" ? comparison.aod.caption
                 : spec.0 == "cloud_layer" ? L10n.text("云底–云顶 AGL · 云属为模型代理", "Cloud base–top AGL · model genus proxy")
                 : L10n.t(spec.2))
                .font(.caption2).foregroundStyle(.secondary).lineLimit(2)
        }.padding(12).frame(maxWidth: .infinity, minHeight: 118, alignment: .topLeading).workSurface()
    }
}

private struct WorkAnalysisTabs: View {
    @EnvironmentObject private var store: Store
    @Environment(\.colorScheme) private var scheme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Namespace private var tabIndicator
    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 4) {
                ForEach(["概览", "大气剖面", "逐时台账"], id: \.self) { tab in
                    let selected = store.resultTab == tab
                    Button {
                        withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.18)) { store.resultTab = tab }
                    } label: {
                        Text(L10n.t(tab))
                            .font(.caption.weight(selected ? .semibold : .regular))
                            .foregroundStyle(selected ? Color.primary : Color.secondary)
                            .padding(.horizontal, 12).padding(.vertical, 8)
                            .frame(minHeight: 36)
                            .background {
                                if selected {
                                    Capsule()
                                        .fill(scheme == .dark ? Color.white.opacity(0.11) : Color.white.opacity(0.95))
                                        .matchedGeometryEffect(id: "active-tab-pill", in: tabIndicator)
                                }
                            }
                            .overlay(alignment: .bottom) {
                                if selected {
                                    Capsule().fill(warm).frame(width: 20, height: 2.5)
                                        .offset(y: -2)
                                        .matchedGeometryEffect(id: "active-tab-line", in: tabIndicator)
                                }
                            }
                            .contentShape(Capsule())
                    }
                    .buttonStyle(.plain)
                    .accessibilityAddTraits(selected ? .isSelected : [])
                }
            }
            .padding(3)
            .background(
                Capsule().fill(scheme == .dark ? Color(0x16, 0x1E, 0x2E) : Color(0xEA, 0xF0, 0xF6))
            )
            .overlay {
                Capsule().strokeBorder(FSDesign.surfaceBorder(role: .grouped, dark: scheme == .dark, emphasized: false), lineWidth: 1)
            }
        }
    }
}

/// 专用大气剖面标签页卡：仅展示太阳方向大气剖面与可选冻结剖面刷新（不含诊断分组）。
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
                        .font(.subheadline.weight(.semibold))
                    Spacer(minLength: 8)
                    if sourceAvailable {
                        Button {
                            store.queryPointProfile()
                        } label: {
                            Label(L10n.t("刷新冻结剖面"), systemImage: "waveform.path.ecg")
                                .font(.caption.weight(.medium))
                        }
                        .buttonStyle(.borderless)
                        .disabled(store.busy)
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
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .workSurface()
    }
}

/// 统一三栏「评分组成」（评分项 | GFS | EC）：只读展示后端 `quality_components`。
private struct WorkQualityBreakdownView: View {
    let comparison: ForecastComparison
    @State private var detailsExpanded = false
    @Environment(\.horizontalSizeClass) private var sizeClass

    private struct CellContent {
        let primary: String
        let detail: String
        let primaryColor: Color
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            headerBar
            Divider().opacity(0.45)
            comparisonTable
            if let limitSummary = limitingFactorSummary {
                Text(limitSummary)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.tail)
                    .padding(.top, 1)
            }
            Divider().opacity(0.45)
            DisclosureGroup(isExpanded: $detailsExpanded) {
                expandedDetails
                    .padding(.top, 6)
            } label: {
                Text(L10n.t("详情与口径"))
                    .font(.caption.weight(.medium))
                    .foregroundStyle(.secondary)
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .workSurface()
    }

    private var headerBar: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .center, spacing: 8) {
                Text(L10n.t("评分组成")).font(.subheadline.weight(.semibold))
                Spacer(minLength: 4)
                if sizeClass == .regular {
                    HStack(spacing: 12) {
                        ForEach(ForecastComparison.models, id: \.self) { model in
                            modelSummaryBadge(model)
                        }
                    }
                }
            }
            if sizeClass != .regular {
                HStack(spacing: 10) {
                    ForEach(ForecastComparison.models, id: \.self) { model in
                        modelSummaryBadge(model)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func modelSummaryBadge(_ model: String) -> some View {
        if let report = comparison.report(model) {
            HStack(spacing: 4) {
                Circle().fill(modelColor(model)).frame(width: 6, height: 6)
                Text(model.uppercased()).font(.caption2.weight(.semibold))
                Text("\(format(report["quality_index"] ?? report["peak_quality_index"], digits: 3)) / 2.5")
                    .font(.caption.weight(.semibold)).monospacedDigit()
                    .foregroundStyle(modelColor(model))
                if let level = report["quality_level"] as? String ?? report["peak_quality_level"] as? String, !level.isEmpty {
                    Text(L10n.qualityLevel(level))
                        .font(.caption2).foregroundStyle(.secondary)
                        .lineLimit(1)
                }
            }
        } else if comparison.error(model) != nil {
            HStack(spacing: 4) {
                Circle().fill(Color.red.opacity(0.8)).frame(width: 6, height: 6)
                Text(L10n.tf("%@ 未完成", model.uppercased()))
                    .font(.caption2).foregroundStyle(.red)
            }
        } else {
            HStack(spacing: 4) {
                Circle().fill(Color.secondary.opacity(0.5)).frame(width: 6, height: 6)
                Text(L10n.tf("%@ 暂无结果", model.uppercased()))
                    .font(.caption2).foregroundStyle(.secondary)
            }
        }
    }

    private var comparisonTable: some View {
        let labelWidth: CGFloat = sizeClass == .regular ? 104 : 78
        return VStack(spacing: 0) {
            HStack(spacing: 8) {
                Text(L10n.t("评分项"))
                    .frame(width: labelWidth, alignment: .leading)
                ForEach(ForecastComparison.models, id: \.self) { model in
                    HStack(spacing: 4) {
                        Circle().fill(modelColor(model)).frame(width: 5, height: 5)
                        Text(model.uppercased())
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .font(.caption2.weight(.semibold))
            .foregroundStyle(.secondary)
            .padding(.bottom, 5)

            ForEach(0..<6, id: \.self) { idx in
                Divider().opacity(0.38)
                metricRow(idx, labelWidth: labelWidth)
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

    private func metricRow(_ idx: Int, labelWidth: CGFloat) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(rowTitle(idx))
                .font(.caption2)
                .foregroundStyle(.secondary)
                .frame(width: labelWidth, alignment: .leading)
                .lineLimit(2)
            ForEach(ForecastComparison.models, id: \.self) { model in
                modelMetricCell(idx: idx, report: comparison.report(model))
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .padding(.vertical, 6)
    }

    @ViewBuilder
    private func modelMetricCell(idx: Int, report: JSONObject?) -> some View {
        if let report {
            let content = cellContent(idx: idx, report: report)
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .firstTextBaseline, spacing: 5) {
                    Text(content.primary)
                        .font(.caption.weight(.semibold))
                        .monospacedDigit()
                        .foregroundStyle(content.primaryColor)
                    Text(content.detail)
                        .font(.caption2)
                        .monospacedDigit()
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .minimumScaleFactor(0.8)
                }
                VStack(alignment: .leading, spacing: 1.5) {
                    Text(content.primary)
                        .font(.caption.weight(.semibold))
                        .monospacedDigit()
                        .foregroundStyle(content.primaryColor)
                    Text(content.detail)
                        .font(.system(size: 10))
                        .monospacedDigit()
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .minimumScaleFactor(0.75)
                }
            }
        } else {
            Text("—")
                .font(.caption)
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
                detail: "前方 \(ratioText(c["forward_burnable_cloud_field_amount_peak"])) / 总 \(ratioText(c["forward_view_cloud_field_amount_peak"]))",
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
            Text("分析范围 \(radius) · 太阳方向扇区 \(sector)")
                .font(.caption2.weight(.medium)).foregroundStyle(.secondary).monospacedDigit()
            ForEach(ForecastComparison.models, id: \.self) { model in
                if let r = comparison.report(model) {
                    let c = r["quality_components"] as? JSONObject ?? [:]
                    let src = (r["model_source"] as? String ?? model) == "ec" ? "EC TCC + PL-RH 代理" : "GFS 全量云场"
                    HStack(spacing: 6) {
                        Text(model.uppercased())
                            .font(.caption2.weight(.semibold)).foregroundStyle(modelColor(model))
                            .frame(width: 28, alignment: .leading)
                        Text("原始分 \(format(c["raw_quality_index"], digits: 3)) · \(src) · 观测回程 τ \(format(c["observer_return_optical_depth_mean"], digits: 3)) · T \(format(c["observer_return_transmission_mean"], digits: 3))")
                            .font(.caption2).foregroundStyle(.secondary).monospacedDigit()
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            Text("几何遮挡已包含在可烧云量中，不再重复扣分；同一视线由近及远合成（低云遮挡其后中高云）。摄影焦段、Top-K、代表云与云边界不进入主评分。")
                .font(.caption2).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func signedDelta(_ value: Any?) -> String? {
        guard let number = value as? NSNumber, number.doubleValue.isFinite else { return nil }
        if abs(number.doubleValue) < 0.0005 { return "0.000" }
        return String(format: "%+.3f", number.doubleValue)
    }

    private func deltaColor(_ delta: String?) -> Color {
        guard let delta, delta != "0.000" else { return Color.secondary }
        return delta.hasPrefix("-") ? Color.red : Color.green
    }

    private func valueSuffix(_ value: Any?, _ suffix: String, digits: Int, sign: String = "") -> String {
        guard let number = value as? NSNumber, number.doubleValue.isFinite else { return "—" }
        return "\(sign)\(String(format: "%.\(digits)f", number.doubleValue))\(suffix)"
    }
}

struct WorkComparisonCurveView: View {
    @EnvironmentObject private var store: Store
    let comparison: ForecastComparison
    let timezone: String

    var body: some View {
        let points = comparison.curve(timezone: timezone)
        let ratios = comparison.ratioCurve(timezone: timezone)
        let aod = comparison.aodCurve(timezone: timezone)
        let peak = points.max { $0.quality < $1.quality }
        let selectedDate = Self.time(points, matching: store.pointSelectedTime)

        return VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                Text(L10n.text("霞光质量与大气趋势", "Glow quality & atmospheric trends")).font(.subheadline.weight(.semibold))
                Spacer()
                HStack(spacing: 10) {
                    ForEach(ForecastComparison.models, id: \.self) { model in
                        HStack(spacing: 5) {
                            Rectangle().fill(modelColor(model)).frame(width: 14, height: 2)
                            Text(model.uppercased())
                        }.font(.caption2).foregroundStyle(.secondary)
                    }
                }
            }
            Chart {
                ForEach(points) { point in
                    LineMark(x: .value("时间", point.time), y: .value("质量指数", point.quality),
                             series: .value("曲线", point.series))
                        .foregroundStyle(modelColor(point.model))
                        .lineStyle(StrokeStyle(lineWidth: 2, dash: point.model == "ec" ? [4, 3] : []))
                    PointMark(x: .value("时间", point.time), y: .value("质量指数", point.quality))
                        .foregroundStyle(modelColor(point.model)).symbolSize(8)
                }
                if let selectedDate {
                    RuleMark(x: .value("选中时刻", selectedDate))
                        .foregroundStyle(Color.secondary.opacity(0.55))
                        .lineStyle(StrokeStyle(lineWidth: 1, dash: [3, 3]))
                }
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
            .frame(height: 176)
            .overlay {
                if points.isEmpty {
                    Text(comparison.result == nil ? L10n.t("等待预测") : L10n.text("暂无质量曲线", "No quality curve"))
                        .font(.caption).foregroundStyle(.secondary)
                }
            }

            if !ratios.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    HStack(spacing: 10) {
                        ForEach(["coverage", "ratio", "transmission"], id: \.self) { kind in
                            HStack(spacing: 4) {
                                Rectangle().fill(ratioSeriesColor(kind)).frame(width: 10, height: 2)
                                Text(ratioSeriesLabel(kind))
                            }.font(.caption2).foregroundStyle(.secondary)
                        }
                    }
                    Chart(ratios) { point in
                        LineMark(x: .value("时间", point.time), y: .value("条件占比", point.value),
                                 series: .value("曲线", point.series))
                            .foregroundStyle(ratioSeriesColor(point.kind))
                            .lineStyle(StrokeStyle(lineWidth: 1.7, dash: point.model == "ec" ? [4, 3] : []))
                    }
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
                    .frame(height: 120)
                }
            }

            if !aod.isEmpty {
                let values = aod.map(\.quality)
                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        Text("AOD 550").font(.caption.weight(.semibold))
                        Text(String(format: "%.3f–%.3f · %@", values.min() ?? 0, values.max() ?? 0, comparison.aod.source))
                            .font(.caption2).foregroundStyle(.secondary)
                    }
                    Chart(aod) { point in
                        LineMark(x: .value("时间", point.time), y: .value("AOD 550", point.quality),
                                 series: .value("曲线", point.series))
                            .foregroundStyle(modelColor(point.model))
                            .lineStyle(StrokeStyle(lineWidth: 1.7, dash: point.model == "ec" ? [4, 3] : []))
                    }
                    .chartYScale(domain: Self.aodDomain(aod))
                    .chartXAxis {
                        AxisMarks(values: .automatic(desiredCount: 4)) { value in
                            AxisGridLine()
                            AxisValueLabel {
                                if let date = value.as(Date.self) { Text(ObservationDate.timeLabel(date, timezone: timezone)) }
                            }
                        }
                    }
                    .frame(height: 108)
                }
            }

            timelineStrip(peakLocal: peak?.local)
        }.padding(16).workSurface()
    }

    private func timelineStrip(peakLocal: String?) -> some View {
        let steps = comparison.report(store.selectedModel)?["steps"] as? [JSONObject] ?? []
        let validSteps = steps.filter(ForecastComparison.isStepValid)
        return Group {
            if !steps.isEmpty {
                VStack(alignment: .leading, spacing: 5) {
                    Text("\(L10n.text("事件时间轴", "Event timeline")) (\(store.selectedModel.uppercased()))")
                        .font(.caption2.weight(.semibold)).foregroundStyle(.secondary)
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
                            } else {
                                RoundedRectangle(cornerRadius: 2)
                                    .stroke(Color.secondary.opacity(0.4), style: StrokeStyle(lineWidth: 1, dash: [2, 2]))
                                    .frame(height: 10)
                            }
                        }
                    }
                    if let first = validSteps.first?["valid_time_local"] as? String,
                       let last = validSteps.last?["valid_time_local"] as? String {
                        HStack {
                            Text(timeLabel(first)).monospacedDigit()
                            Spacer()
                            Text(timeLabel(last)).monospacedDigit()
                        }.font(.caption2).foregroundStyle(.secondary)
                    }
                }
            }
        }
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

final class LedgerFilterState: ObservableObject {
    @Published var showInvalid = false
}

/// 逐时台账：默认隐藏 `valid == false` 的未判断/缺测行，并提供内联切换按钮。
struct WorkLedgerView: View {
    @StateObject private var filter = LedgerFilterState()
    let report: JSONObject?
    let bestTime: String?
    @Binding var selectedTime: String?

    init(report: JSONObject?, bestTime: String? = nil, selectedTime: Binding<String?> = .constant(nil)) {
        self.report = report
        self.bestTime = bestTime
        _selectedTime = selectedTime
    }

    private var allSteps: [JSONObject] { report?["steps"] as? [JSONObject] ?? [] }
    var visibleSteps: [JSONObject] {
        Self.filteredSteps(from: allSteps, showInvalid: filter.showInvalid)
    }
    var hiddenInvalidCount: Int {
        Self.hiddenCount(in: allSteps)
    }

    static func filteredSteps(from steps: [JSONObject], showInvalid: Bool) -> [JSONObject] {
        showInvalid ? steps : steps.filter(ForecastComparison.isStepValid)
    }

    static func hiddenCount(in steps: [JSONObject]) -> Int {
        steps.filter { !ForecastComparison.isStepValid($0) }.count
    }

    static func toggleButtonTitle(hiddenCount: Int, showInvalid: Bool) -> String {
        showInvalid
            ? L10n.text("隐藏未判断时次", "Hide unevaluated")
            : L10n.text("显示 \(hiddenCount) 个未判断时次", "Show \(hiddenCount) unevaluated")
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
                .frame(minWidth: 1120, alignment: .leading)
            }
            Divider().opacity(0.45)
            HStack(spacing: 10) {
                Text(L10n.text("云底–云顶为海拔 ASL，云层 AGL 为离地；默认隐藏未判断时次，— 表示缺测。",
                               "ASL base–top and AGL layer shown; unevaluated rows hidden by default."))
                    .font(.caption2).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 8)
                if hiddenInvalidCount > 0 {
                    Button {
                        filter.showInvalid.toggle()
                    } label: {
                        Label(
                            Self.toggleButtonTitle(hiddenCount: hiddenInvalidCount, showInvalid: filter.showInvalid),
                            systemImage: filter.showInvalid ? "eye.slash" : "eye"
                        )
                        .font(.caption.weight(.medium))
                        .padding(.horizontal, 10)
                        .padding(.vertical, 5)
                        .background(Color.secondary.opacity(0.10), in: Capsule())
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, 14).padding(.vertical, 8)
        }.workSurface()
    }

    private var header: some View {
        HStack(spacing: 0) {
            Text(L10n.t("时间")).frame(width: 82, alignment: .leading)
            Text(L10n.t("质量")).frame(width: 64, alignment: .leading)
            Text(L10n.t("档位")).frame(width: 96, alignment: .leading)
            Text(L10n.t("可烧")).frame(width: 48, alignment: .leading)
            Text(L10n.t("可烧云量")).frame(width: 78, alignment: .leading)
            Text(L10n.t("主导压力族")).frame(width: 92, alignment: .leading)
            Text(L10n.t("云底–云顶 ASL")).frame(width: 110, alignment: .leading)
            Text(L10n.t("云层 AGL")).frame(width: 106, alignment: .leading)
            Text(L10n.t("亮度颜色")).frame(width: 68, alignment: .leading)
            Text(L10n.t("可烧比例")).frame(width: 68, alignment: .leading)
            Text(L10n.t("回程透过率")).frame(width: 76, alignment: .leading)
            Text(L10n.t("入射阻挡")).frame(width: 68, alignment: .leading)
            Text("AOD 550").frame(width: 68, alignment: .leading)
            Text(L10n.t("太阳仰角")).frame(width: 74, alignment: .leading)
            Text(L10n.t("场景")).frame(width: 120, alignment: .leading)
        }
        .font(.caption2.weight(.semibold))
        .foregroundStyle(.secondary)
        .padding(.horizontal, 14).padding(.vertical, 12)
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
                        Image(systemName: "star.fill").font(.system(size: 9)).foregroundStyle(warm)
                    }
                }
                .frame(width: 82, alignment: .leading)
                Text(valid ? format(item["instant_quality_index"], digits: 3) : "—").frame(width: 64, alignment: .leading)
                Text(valid ? item["instant_quality_level"] as? String ?? "—" : L10n.t("外推窗外/未判断"))
                    .foregroundStyle(valid ? Color.primary : Color.secondary)
                    .frame(width: 96, alignment: .leading)
                Image(systemName: !valid || burnable == nil ? "minus"
                      : burnable == true ? "checkmark" : "xmark")
                    .foregroundStyle(!valid || burnable == nil ? Color.secondary : burnable == true ? coolAccent : Color.orange)
                    .frame(width: 48, alignment: .leading)
                Text(valid ? percent(item["burnable_cloud_coverage"]) : "—").frame(width: 78, alignment: .leading)
                Text(valid ? familyLabel(item["dominant_burnable_pressure_family"]) : "—").frame(width: 92, alignment: .leading)
                Text(valid ? aslRangeLabel(base: baseASL, top: topASL) : "—").frame(width: 110, alignment: .leading)
                Text(valid ? cloudLayerLabel(base: base, top: top) : "—").frame(width: 106, alignment: .leading)
                Text(valid ? ratioText(item["brightness_color"]) : "—").frame(width: 68, alignment: .leading)
                Text(valid ? ratioText(item["forward_burnable_cloud_ratio"]) : "—").frame(width: 68, alignment: .leading)
                Text(valid ? ratioText(item["observer_return_transmission"]) : "—").frame(width: 76, alignment: .leading)
                Text(valid ? ratioText(item["solar_incoming_block_risk"]) : "—").frame(width: 68, alignment: .leading)
                Text(valid ? format(item["quality_aod550"], digits: 2) : "—").frame(width: 68, alignment: .leading)
                Text(valid ? format(item["solar_elevation_deg"], digits: 1) : "—").frame(width: 74, alignment: .leading)
                Text(valid ? sceneLabel(item["selected_regime"]) : "—").frame(width: 120, alignment: .leading)
            }
            .font(.caption2).monospacedDigit()
            .padding(.horizontal, 14).padding(.vertical, 11)
            .contentShape(Rectangle())
            .background(selected ? warm.opacity(0.10) : Color.clear, in: RoundedRectangle(cornerRadius: 6))
        }
        .buttonStyle(.plain)
        .disabled(!valid || local.isEmpty)
    }

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

/// 诊断与时间窗 / 大气剖面视图（接入 CrossSectionFigureView + 云属代理诊断 + 内部码抑制）。
struct WorkDiagnosticView: View {
    let report: JSONObject?
    let profile: Bool
    var waiting = L10n.t("等待预测")
    var showHeader = true

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if showHeader {
                Text(profile ? L10n.text("太阳方向大气剖面", "Sun-direction atmospheric profile")
                     : L10n.text("诊断与时间窗", "Diagnostics & timing"))
                    .font(.subheadline.weight(.semibold))
            }
            if let report {
                if !profile {
                    Text(summaryText(report))
                        .font(.callout)
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if profile {
                    WorkCrossSectionView(
                        section: report["cross_section"] as? JSONObject,
                        genusProxy: report["cloud_genus_proxy"] as? JSONObject
                    )
                    if let radar = report["azimuth_radar"] as? JSONObject {
                        AzimuthRadarCard(radar: radar)
                    }
                }
                WorkDiagnosticGroup(title: L10n.text("运行与时间", "Runtime & Timing")) {
                    diagnosticRow(L10n.text("预测周期", "Cycle"), report["cycle_time_utc"])
                    diagnosticRow(L10n.text("本地锚点", "Anchor"), report["anchor_local"])
                    diagnosticRow(L10n.t("时区"), report["timezone_name"])
                    diagnosticRow(L10n.text("模式", "Mode"), report["forecast_mode"])
                    diagnosticRow(L10n.text("选中场景", "Regime"), sceneLabel(report["selected_regime"]))
                    diagnosticRow(L10n.t("持续时长"), report["duration_minutes"], digits: 0, suffix: " 分钟")
                }
                if let cache = report["cache"] as? JSONObject {
                    WorkDiagnosticGroup(title: L10n.text("缓存状态", "Cache")) {
                        diagnosticRow(L10n.text("状态", "Status"), cache["status"])
                        diagnosticRow(L10n.text("生成时间（UTC）", "Generated (UTC)"), cache["generated_at_utc"])
                    }
                }
                WorkGenusPhaseDiagnosis(report: report)
                if report["nowcast"] as? JSONObject == nil {
                    WorkSharedDiagnosis(report: report)
                }
                if let aerosol = report["aerosol"] as? JSONObject {
                    WorkDiagnosticGroup(title: L10n.text("气溶胶来源", "Aerosol")) {
                        diagnosticRow("AOD 550", aerosol["aod550"], digits: 3)
                        diagnosticRow(L10n.text("来源", "Source"), aerosol["source"])
                        diagnosticRow(L10n.text("数据质量", "Quality"), aerosol["quality"])
                    }
                }
                if let map = report["map_capability"] as? JSONObject,
                   map["available"] as? Bool == false,
                   let rawReason = map["reason"] as? String,
                   rawReason != "native-nowcast-map-publication-not-requested" {
                    let message = friendlyEngineMessage(rawReason)
                    if !message.isEmpty {
                        Label(message, systemImage: "map")
                            .font(.caption2).foregroundStyle(.secondary)
                    }
                }
            } else {
                Text(waiting).foregroundStyle(.secondary).frame(maxWidth: .infinity, minHeight: 120)
            }
        }.padding(16).frame(maxWidth: .infinity, alignment: .leading).workSurface()
    }
}

// MARK: - v2.0.0 色谱条、16 方位霞光雷达与时间解析助手

private func parseLocalTimestamp(_ raw: String, timezone: String) -> Date? {
    let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !trimmed.isEmpty else { return nil }
    if let date = ObservationDate.timestamp(trimmed) {
        return date
    }
    let tz = TimeZone(identifier: timezone) ?? .current
    for pattern in ["yyyy-MM-dd'T'HH:mm:ss", "yyyy-MM-dd HH:mm:ss", "yyyy-MM-dd'T'HH:mm", "yyyy-MM-dd HH:mm"] {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = tz
        formatter.dateFormat = pattern
        if let date = formatter.date(from: trimmed) {
            return date
        }
    }
    return nil
}

private func parseHexColor(_ hex: String?) -> Color? {
    guard let hex else { return nil }
    let cleaned = hex.trimmingCharacters(in: .whitespacesAndNewlines)
        .replacingOccurrences(of: "#", with: "")
    guard cleaned.count == 6, let value = UInt64(cleaned, radix: 16) else { return nil }
    let r = Double((value >> 16) & 0xFF) / 255.0
    let g = Double((value >> 8) & 0xFF) / 255.0
    let b = Double(value & 0xFF) / 255.0
    return Color(red: r, green: g, blue: b)
}

/// 霞光光谱色条与峰值色点（`report["spectral_color"]`）。
private struct SpectralColorStripView: View {
    let spectral: JSONObject
    @Environment(\.colorScheme) private var scheme

    private var gradientColors: [Color] {
        let parsed = (spectral["sky_gradient_hex"] as? [String] ?? []).compactMap(parseHexColor)
        if parsed.count >= 2 { return parsed }
        if let single = parsed.first { return [single.opacity(0.65), single] }
        let fallback = peakColor
        return [fallback.opacity(0.45), fallback]
    }

    private var peakColor: Color {
        parseHexColor(spectral["peak_hex"] as? String) ?? warm
    }

    private var peakLabel: String {
        let zh = (spectral["peak_label_zh"] as? String ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let en = (spectral["peak_label_en"] as? String ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if L10n.effective == .english {
            return en.isEmpty ? (zh.isEmpty ? "—" : zh) : en
        }
        return zh.isEmpty ? (en.isEmpty ? "—" : en) : zh
    }

    private var solarText: String? {
        let az = number(spectral["solar_azimuth_deg"])
        let el = number(spectral["solar_elevation_deg"])
        switch (az, el) {
        case let (a?, e?):
            return L10n.text(
                String(format: "太阳方位 %.0f° · 仰角 %.1f°", a, e),
                String(format: "Az %.0f° · El %.1f°", a, e)
            )
        case let (a?, nil):
            return L10n.text(String(format: "太阳方位 %.0f°", a), String(format: "Az %.0f°", a))
        case let (nil, e?):
            return L10n.text(String(format: "太阳仰角 %.1f°", e), String(format: "El %.1f°", e))
        case (nil, nil):
            return nil
        }
    }

    var body: some View {
        let dark = scheme == .dark
        HStack(spacing: 10) {
            RoundedRectangle(cornerRadius: 5)
                .fill(
                    LinearGradient(
                        colors: gradientColors,
                        startPoint: .top,
                        endPoint: .bottom
                    )
                )
                .frame(width: 22, height: 34)
                .overlay {
                    RoundedRectangle(cornerRadius: 5)
                        .strokeBorder(Color.primary.opacity(dark ? 0.22 : 0.14), lineWidth: 0.8)
                }

            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Circle()
                        .fill(peakColor)
                        .frame(width: 9, height: 9)
                        .overlay {
                            Circle().strokeBorder(Color.white.opacity(0.85), lineWidth: 0.8)
                        }
                    Text(peakLabel)
                        .font(.caption.weight(.semibold))
                        .lineLimit(1)
                    if let hex = spectral["peak_hex"] as? String, !hex.isEmpty {
                        Text(hex.uppercased())
                            .font(.caption2.monospaced())
                            .foregroundStyle(.secondary)
                    }
                }
                if let solarText {
                    Text(solarText)
                        .font(.caption2)
                        .monospacedDigit()
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 7)
        .background(FSDesign.surfaceFill(role: .grouped, dark: dark), in: RoundedRectangle(cornerRadius: 9))
        .overlay {
            RoundedRectangle(cornerRadius: 9)
                .strokeBorder(FSDesign.surfaceBorder(role: .grouped, dark: dark, emphasized: false), lineWidth: 1)
        }
        .accessibilityElement(children: .combine)
    }
}

/// 16 方位极坐标霞光雷达（`report["azimuth_radar"]`）。
private struct AzimuthRadarCard: View {
    let radar: JSONObject
    var modelLabel: String? = nil
    var availableModels: [String] = []
    var selectedModel: Binding<String>? = nil

    @Environment(\.colorScheme) private var scheme

    private struct BearingSample {
        let deg: Double
        let labelZh: String
        let labelEn: String
        let score: Double
        let canvas: Double
        let lowBlock: Double
    }

    private var bearings: [BearingSample] {
        let raw = radar["bearings"] as? [JSONObject] ?? []
        return raw.compactMap { item -> BearingSample? in
            guard let deg = number(item["deg"]),
                  let score = number(item["score"]) else { return nil }
            return BearingSample(
                deg: deg,
                labelZh: item["label_zh"] as? String ?? "",
                labelEn: item["label_en"] as? String ?? "",
                score: min(max(score, 0), 1),
                canvas: min(max(number(item["canvas"]) ?? 0, 0), 1),
                lowBlock: min(max(number(item["low_block"]) ?? 0, 0), 1)
            )
        }
        .sorted { $0.deg < $1.deg }
    }

    private var recommendationText: String {
        let zh = (radar["recommendation_zh"] as? String ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let en = (radar["recommendation_en"] as? String ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if L10n.effective == .english {
            return en.isEmpty ? zh : en
        }
        return zh.isEmpty ? en : zh
    }

    private var bestDirectionLabel: String? {
        let zh = (radar["best_direction_zh"] as? String ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let en = (radar["best_direction_en"] as? String ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let dir = L10n.effective == .english ? (en.isEmpty ? zh : en) : (zh.isEmpty ? en : zh)
        guard !dir.isEmpty else { return nil }
        if let deg = number(radar["best_azimuth_deg"]) {
            return String(format: "%@ (%.0f°)", dir, deg)
        }
        return dir
    }

    var body: some View {
        let samples = bearings
        let solarAzimuth = number(radar["solar_azimuth_deg"])
        let bestAzimuth = number(radar["best_azimuth_deg"])
        let bestScore = number(radar["best_score"])
        let dark = scheme == .dark

        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Text(L10n.text("16 方位霞光雷达", "16-Bearing Glow Radar"))
                    .font(.subheadline.weight(.semibold))
                if let modelLabel, !modelLabel.isEmpty, availableModels.count <= 1 {
                    Text(modelLabel)
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(modelColor(modelLabel.lowercased()))
                        .padding(.horizontal, 7)
                        .padding(.vertical, 2)
                        .background(modelColor(modelLabel.lowercased()).opacity(0.14), in: Capsule())
                }
                Spacer(minLength: 4)
                if availableModels.count > 1, let selectedModel {
                    WorkSegments(
                        options: availableModels,
                        titles: availableModels.map { $0.uppercased() },
                        selection: selectedModel
                    )
                    .frame(width: 120)
                } else if let bestDirectionLabel {
                    Text(bestDirectionLabel)
                        .font(.caption2.weight(.semibold))
                        .monospacedDigit()
                        .foregroundStyle(warm)
                        .workChip()
                }
            }

            if !samples.isEmpty {
                Canvas { context, size in
                    let center = CGPoint(x: size.width / 2, y: size.height / 2)
                    let radius = max(24, min(size.width, size.height) / 2 - 22)

                    func point(deg: Double, ratio: Double) -> CGPoint {
                        let rad = (deg - 90.0) * Double.pi / 180.0
                        let r = radius * ratio
                        return CGPoint(
                            x: center.x + CGFloat(cos(rad) * r),
                            y: center.y + CGFloat(sin(rad) * r)
                        )
                    }

                    // Concentric rings at 0.5 and 1.0
                    let innerRect = CGRect(
                        x: center.x - radius * 0.5,
                        y: center.y - radius * 0.5,
                        width: radius,
                        height: radius
                    )
                    context.stroke(
                        Path(ellipseIn: innerRect),
                        with: .color(Color.secondary.opacity(dark ? 0.34 : 0.26)),
                        style: StrokeStyle(lineWidth: 0.9, dash: [3, 3])
                    )

                    let outerRect = CGRect(
                        x: center.x - radius,
                        y: center.y - radius,
                        width: radius * 2,
                        height: radius * 2
                    )
                    context.stroke(
                        Path(ellipseIn: outerRect),
                        with: .color(Color.secondary.opacity(dark ? 0.44 : 0.34)),
                        lineWidth: 1.0
                    )

                    // Radial spokes (8 primary/intercardinal directions)
                    for stepDeg in stride(from: 0.0, to: 360.0, by: 45.0) {
                        var spoke = Path()
                        spoke.move(to: center)
                        spoke.addLine(to: point(deg: stepDeg, ratio: 1.0))
                        context.stroke(
                            spoke,
                            with: .color(Color.secondary.opacity(dark ? 0.22 : 0.16)),
                            lineWidth: 0.7
                        )
                    }

                    // Cardinal labels: N / E / S / W
                    let cardinals: [(Double, String)] = [(0, "N"), (90, "E"), (180, "S"), (270, "W")]
                    for (deg, title) in cardinals {
                        let labelPt = point(deg: deg, ratio: 1.0)
                        let rad = (deg - 90.0) * Double.pi / 180.0
                        let offsetPt = CGPoint(
                            x: labelPt.x + CGFloat(cos(rad)) * 12,
                            y: labelPt.y + CGFloat(sin(rad)) * 12
                        )
                        context.draw(
                            Text(title).font(.caption2.weight(.semibold)).foregroundColor(.secondary),
                            at: offsetPt,
                            anchor: .center
                        )
                    }

                    // 16-bearing glow score polygon
                    var polygon = Path()
                    for (idx, sample) in samples.enumerated() {
                        let pt = point(deg: sample.deg, ratio: sample.score)
                        if idx == 0 {
                            polygon.move(to: pt)
                        } else {
                            polygon.addLine(to: pt)
                        }
                    }
                    polygon.closeSubpath()
                    context.fill(polygon, with: .color(warm.opacity(dark ? 0.30 : 0.24)))
                    context.stroke(polygon, with: .color(warm), lineWidth: 2.0)

                    // Best azimuth vertex highlight
                    if let bestSample = samples.max(by: { $0.score < $1.score }) {
                        let bestDeg = bestAzimuth ?? bestSample.deg
                        let bestRatio = bestScore ?? bestSample.score
                        let bestPt = point(deg: bestDeg, ratio: min(max(bestRatio, 0), 1))
                        let dotRect = CGRect(x: bestPt.x - 4, y: bestPt.y - 4, width: 8, height: 8)
                        context.fill(Path(ellipseIn: dotRect), with: .color(warm))
                        context.stroke(Path(ellipseIn: dotRect), with: .color(.white.opacity(0.9)), lineWidth: 1.2)
                    }

                    // Solar azimuth marker
                    if let solarAzimuth {
                        let sunColor = Color(red: 1.0, green: 0.74, blue: 0.22)
                        let edgePt = point(deg: solarAzimuth, ratio: 1.0)
                        var ray = Path()
                        ray.move(to: center)
                        ray.addLine(to: edgePt)
                        context.stroke(
                            ray,
                            with: .color(sunColor.opacity(0.85)),
                            style: StrokeStyle(lineWidth: 1.4, dash: [4, 3])
                        )
                        let haloRect = CGRect(x: edgePt.x - 7, y: edgePt.y - 7, width: 14, height: 14)
                        context.fill(Path(ellipseIn: haloRect), with: .color(sunColor.opacity(0.28)))
                        let sunRect = CGRect(x: edgePt.x - 4.2, y: edgePt.y - 4.2, width: 8.4, height: 8.4)
                        context.fill(Path(ellipseIn: sunRect), with: .color(sunColor))
                        context.stroke(Path(ellipseIn: sunRect), with: .color(.white.opacity(0.95)), lineWidth: 1.1)
                    }
                }
                .frame(maxWidth: .infinity)
                .frame(height: 220)
                .accessibilityLabel(
                    L10n.text(
                        "16 方位霞光雷达图，\(recommendationText)",
                        "16-bearing glow radar, \(recommendationText)"
                    )
                )

                HStack(spacing: 12) {
                    HStack(spacing: 5) {
                        RoundedRectangle(cornerRadius: 2)
                            .fill(warm.opacity(0.55))
                            .frame(width: 12, height: 8)
                        Text(L10n.text("推荐评分", "Glow score"))
                    }
                    if let solarAzimuth {
                        HStack(spacing: 5) {
                            Circle()
                                .fill(Color(red: 1.0, green: 0.74, blue: 0.22))
                                .frame(width: 8, height: 8)
                            Text(L10n.text(
                                String(format: "太阳方位 %.0f°", solarAzimuth),
                                String(format: "Sun %.0f°", solarAzimuth)
                            ))
                            .monospacedDigit()
                        }
                    }
                    Spacer(minLength: 0)
                    if let bestScore {
                        Text(String(format: L10n.text("峰值 %.2f", "Peak %.2f"), bestScore))
                            .monospacedDigit()
                    }
                }
                .font(.caption2)
                .foregroundStyle(.secondary)
            }

            if !recommendationText.isEmpty {
                Text(recommendationText)
                    .font(.caption)
                    .foregroundStyle(.primary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .workSurface()
    }
}
