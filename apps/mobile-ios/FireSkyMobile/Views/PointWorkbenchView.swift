import Charts
import SwiftUI

/// 单点工作台（对齐 macOS v1.9.10 Workbench.swift）。
/// iPad（regular）双栏，iPhone（compact）单列滚动 + 底部安全区主操作栏。
struct PointWorkbenchView: View {
    @EnvironmentObject private var store: Store
    @Environment(\.colorScheme) private var scheme
    @Environment(\.horizontalSizeClass) private var sizeClass
    @Environment(\.dynamicTypeSize) private var typeSize

    var body: some View {
        Group {
            if sizeClass == .regular && !typeSize.isAccessibilitySize {
                VStack(alignment: .leading, spacing: 10) {
                    if store.isRemoteMode {
                        remoteServerPill
                            .padding(.horizontal, 18)
                            .padding(.top, 12)
                    }
                    HStack(alignment: .top, spacing: 16) {
                        ScrollView { VStack(spacing: 12) { controls; runControls }.padding(1) }.frame(width: 320)
                        ScrollView {
                            VStack(spacing: 12) { hero; results }.padding(1)
                        }.frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
                    }
                    .padding(.horizontal, 18)
                    .padding(.bottom, 18)
                    .padding(.top, store.isRemoteMode ? 0 : 18)
                }
            } else {
                ScrollView {
                    VStack(spacing: 14) {
                        if store.isRemoteMode {
                            remoteServerPill
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        controls
                        hero
                        results
                    }.padding(.horizontal, 16).padding(.vertical, 12)
                }
                .safeAreaInset(edge: .bottom, spacing: 0) {
                    VStack(spacing: 0) {
                        Divider().opacity(0.45)
                        runControls.padding(.horizontal, 16).padding(.vertical, 10)
                    }
                    .background(.regularMaterial)
                }
            }
        }
        .workPageBackground()
    }

    private var remoteServerPill: some View {
        let dark = scheme == .dark
        return Button {
            store.settingsCategory = "高级"
            store.page = .settings
        } label: {
            HStack(spacing: 7) {
                Circle()
                    .fill(store.online ? Color.green : Color.orange)
                    .frame(width: 7, height: 7)
                Label(L10n.text("远程 Mac", "Remote Mac"), systemImage: "network")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.primary)
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
            AccentButton(title: L10n.start, systemImage: "sparkles", disabled: store.busy || !store.online) {
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

private struct WorkDateButton: View {
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

    var body: some View {
        if comparison.result == nil {
            firstRunEmptyCanvas
        } else {
            VStack(spacing: 12) {
                WorkAnalysisTabs()
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
        }
    }

    private var resultsContent: some View {
        VStack(spacing: 12) {
            if store.resultTab == "概览" {
                ForEach(groupKeys, id: \.0) { key, range in
                    VStack(alignment: .leading, spacing: 7) {
                        Text(groupTitle(key)).font(.caption.weight(.medium)).foregroundStyle(.secondary)
                        LazyVGrid(columns: columns, spacing: 10) {
                            ForEach(Array(metricSpec[range]), id: \.0) { spec in
                                metricCard(spec)
                            }
                        }
                    }
                }
                WorkComparisonCurveView(comparison: comparison, timezone: store.timezone)
            } else if let message = comparison.error(store.selectedModel) {
                Text(L10n.tf("%@ 未完成：%@", store.selectedModel.uppercased(), message))
                    .font(.callout).foregroundStyle(.red).textSelection(.enabled)
                    .padding(18).frame(maxWidth: .infinity, alignment: .leading).workSurface()
            } else if report == nil, comparison.result != nil {
                Text(L10n.tf("%@ 暂无结果", store.selectedModel.uppercased()))
                    .foregroundStyle(.secondary).padding(30)
            } else if store.resultTab == "逐时台账" {
                WorkLedgerView(report: report,
                               bestTime: report?["best_time_local"] as? String,
                               selectedTime: $store.pointSelectedTime)
            } else {
                WorkDiagnosticView(report: report, profile: store.resultTab == "大气剖面")
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
                ForEach(["概览", "大气剖面", "诊断与时间窗", "逐时台账"], id: \.self) { tab in
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
        let validSteps = steps.filter { $0["valid"] as? Bool != false }
        return Group {
            if !steps.isEmpty {
                VStack(alignment: .leading, spacing: 5) {
                    Text("\(L10n.text("事件时间轴", "Event timeline")) (\(store.selectedModel.uppercased()))")
                        .font(.caption2.weight(.semibold)).foregroundStyle(.secondary)
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
        showInvalid ? steps : steps.filter { $0["valid"] as? Bool != false }
    }

    static func hiddenCount(in steps: [JSONObject]) -> Int {
        steps.filter { $0["valid"] as? Bool == false }.count
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
                .frame(minWidth: 960, alignment: .leading)
            }
            Divider().opacity(0.45)
            HStack(spacing: 10) {
                Text(L10n.text("默认仅展示有效判断时次；— 表示缺测。",
                               "Unevaluated rows are hidden by default; — indicates missing."))
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
                        .frame(minHeight: 36)
                    }
                    .buttonStyle(.borderless)
                }
            }
            .padding(.horizontal, 14).padding(.vertical, 8)
        }.workSurface()
    }

    private var header: some View {
        HStack(spacing: 0) {
            Text(L10n.t("时间")).frame(width: 82, alignment: .leading)
            Text(L10n.t("质量")).frame(width: 64, alignment: .leading)
            Text(L10n.t("档位")).frame(width: 86, alignment: .leading)
            Text(L10n.t("可烧")).frame(width: 48, alignment: .leading)
            Text(L10n.t("可烧云量")).frame(width: 78, alignment: .leading)
            Text(L10n.t("主导压力族")).frame(width: 92, alignment: .leading)
            Text(L10n.t("云层 AGL")).frame(width: 106, alignment: .leading)
            Text(L10n.t("亮度颜色")).frame(width: 68, alignment: .leading)
            Text(L10n.t("可烧比例")).frame(width: 68, alignment: .leading)
            Text(L10n.t("回程透过率")).frame(width: 76, alignment: .leading)
            Text("AOD 550").frame(width: 68, alignment: .leading)
            Text(L10n.t("太阳仰角")).frame(width: 74, alignment: .leading)
            Text(L10n.t("场景")).frame(width: 120, alignment: .leading)
        }
        .font(.caption2.weight(.semibold))
        .foregroundStyle(.secondary)
        .padding(.horizontal, 14).padding(.vertical, 12)
    }

    private func row(_ item: JSONObject) -> some View {
        let valid = item["valid"] as? Bool != false
        let burnable = item["physically_burnable"] as? Bool
        let base = item["dominant_burnable_cloud_base_agl_mean_m"]
        let top = item["dominant_burnable_cloud_top_agl_mean_m"]
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
                Text(valid ? item["instant_quality_level"] as? String ?? "—" : L10n.t("缺测")).frame(width: 86, alignment: .leading)
                Image(systemName: !valid || burnable == nil ? "minus"
                      : burnable == true ? "checkmark" : "xmark")
                    .foregroundStyle(!valid || burnable == nil ? Color.secondary : burnable == true ? coolAccent : Color.orange)
                    .frame(width: 48, alignment: .leading)
                Text(valid ? percent(item["burnable_cloud_coverage"]) : "—").frame(width: 78, alignment: .leading)
                Text(valid ? familyLabel(item["dominant_burnable_pressure_family"]) : "—").frame(width: 92, alignment: .leading)
                Text(valid ? cloudLayerLabel(base: base, top: top) : "—").frame(width: 106, alignment: .leading)
                Text(valid ? ratioText(item["brightness_color"]) : "—").frame(width: 68, alignment: .leading)
                Text(valid ? ratioText(item["forward_burnable_cloud_ratio"]) : "—").frame(width: 68, alignment: .leading)
                Text(valid ? ratioText(item["observer_return_transmission"]) : "—").frame(width: 76, alignment: .leading)
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

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(profile ? L10n.text("太阳方向大气剖面", "Sun-direction atmospheric profile")
                 : L10n.text("诊断与时间窗", "Diagnostics & timing"))
                .font(.subheadline.weight(.semibold))
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
