import SwiftUI

private let SettingsCategories = ["通用", "数据", "高级"]

/// 设置页（对齐 macOS v1.9.10 SettingsView：胶囊分类切换 + 运行状态摘要 + 图标行分组卡 + 保留清理 + 后台同步 + 作用域错误）。
struct SettingsView: View {
    @EnvironmentObject private var store: Store
    @Environment(\.colorScheme) private var scheme
    @Environment(\.horizontalSizeClass) private var sizeClass
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Namespace private var categoryIndicator

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                categoryBar
                    .padding(.horizontal, 16)
                    .padding(.top, 8)
                    .padding(.bottom, 10)

                ScrollViewReader { proxy in
                    ScrollView {
                        VStack(alignment: .leading, spacing: 18) {
                            if store.settingsCategory == "通用" {
                                generalSection(scrollProxy: proxy)
                            } else if store.settingsCategory == "数据" {
                                dataSection(scrollProxy: proxy)
                            } else {
                                advancedSection
                            }
                        }
                        .padding(.horizontal, 16)
                        .padding(.top, 6)
                        .padding(.bottom, 28)
                    }
                }
            }
            .workPageBackground()
            .navigationTitle(L10n.navSettings)
            .disabled(store.busy)
        }
    }

    // MARK: - 顶部胶囊分类栏

    private var categoryBar: some View {
        HStack(spacing: 4) {
            ForEach(SettingsCategories, id: \.self) { item in
                let selected = store.settingsCategory == item
                Button {
                    withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.18)) {
                        store.settingsCategory = item
                    }
                } label: {
                    HStack(spacing: 6) {
                        Image(systemName: categorySymbol(item))
                            .font(.system(size: 13, weight: selected ? .semibold : .regular))
                        Text(categoryTitle(item))
                            .font(.subheadline.weight(selected ? .semibold : .regular))
                    }
                    .frame(maxWidth: .infinity, minHeight: 40)
                    .foregroundStyle(selected ? Color.primary : Color.secondary)
                    .background {
                        if selected {
                            Capsule()
                                .fill(scheme == .dark ? Color.white.opacity(0.11) : Color.white.opacity(0.95))
                                .matchedGeometryEffect(id: "ios-settings-cat-pill", in: categoryIndicator)
                        }
                    }
                    .overlay(alignment: .bottom) {
                        if selected {
                            Capsule().fill(warm).frame(width: 22, height: 2.5)
                                .offset(y: -2)
                                .matchedGeometryEffect(id: "ios-settings-cat-line", in: categoryIndicator)
                        }
                    }
                    .contentShape(Capsule())
                }
                .buttonStyle(.plain)
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

    // MARK: - 通用

    private func generalSection(scrollProxy: ScrollViewProxy) -> some View {
        VStack(alignment: .leading, spacing: 18) {
            runtimeSummaryCard(scrollProxy: scrollProxy)

            VStack(alignment: .leading, spacing: 8) {
                sectionLabel(L10n.text("界面", "Interface"))
                VStack(spacing: 0) {
                    VStack(alignment: .leading, spacing: 10) {
                        HStack(spacing: 12) {
                            iconBadge("paintpalette")
                            VStack(alignment: .leading, spacing: 2) {
                                Text(L10n.text("外观", "Appearance")).font(.subheadline.weight(.medium))
                                Text(appearanceSubtitle).font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                        }
                        Picker(L10n.t("主题"), selection: $store.appearance) {
                            Text(L10n.t("跟随系统")).tag("system")
                            Text(L10n.t("浅色")).tag("light")
                            Text(L10n.t("深色")).tag("dark")
                        }
                        .pickerStyle(.segmented)
                        .onChange(of: store.appearance) { _ in store.saveAppearance() }
                        .accessibilityIdentifier("appearance-picker")
                    }
                    .padding(.vertical, 12)

                    Divider().opacity(0.55).padding(.leading, 46)

                    VStack(alignment: .leading, spacing: 10) {
                        HStack(spacing: 12) {
                            iconBadge("globe")
                            VStack(alignment: .leading, spacing: 2) {
                                Text(L10n.text("语言", "Language")).font(.subheadline.weight(.medium))
                                Text(store.appLanguage.label).font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                        }
                        Picker(L10n.text("界面语言", "Interface language"), selection: $store.appLanguage) {
                            Text(L10n.text("跟随系统", "System")).tag(L10n.Language.system)
                            Text("中文").tag(L10n.Language.chinese)
                            Text("English").tag(L10n.Language.english)
                        }
                        .pickerStyle(.segmented)
                    }
                    .padding(.vertical, 12)
                }
                .padding(.horizontal, 16)
                .fireSkySurface(role: .content, cornerRadius: 14)
            }

            remoteServerCard
                .id("anchor-server")
        }
    }

    private func runtimeSummaryCard(scrollProxy: ScrollViewProxy) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text(L10n.text("运行状态摘要", "Runtime Summary")).font(.subheadline.weight(.semibold))
                Spacer()
                Button {
                    withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.18)) {
                        store.settingsCategory = "数据"
                    }
                } label: {
                    Label(L10n.text("管理数据源与同步", "Manage data & sync"),
                          systemImage: "arrow.right.circle")
                        .font(.caption.weight(.medium))
                }
                .buttonStyle(.borderless)
            }
            LazyVGrid(columns: [GridItem(.flexible(), spacing: 10), GridItem(.flexible(), spacing: 10)], spacing: 10) {
                Button {
                    if reduceMotion {
                        scrollProxy.scrollTo("anchor-server", anchor: .top)
                    } else {
                        withAnimation { scrollProxy.scrollTo("anchor-server", anchor: .top) }
                    }
                } label: {
                    statusBadge(
                        title: L10n.text("服务器连接", "Server"),
                        value: store.online
                            ? L10n.text("已连接", "Connected")
                            : store.hasSavedRemoteServer
                                ? L10n.text("未连接", "Offline")
                                : L10n.text("未配置", "Not configured"),
                        healthy: store.online,
                        showChevron: true
                    )
                }
                .buttonStyle(.plain)
                statusBadge(title: L10n.text("数据源与同步", "Data & Sync"),
                            value: store.dataSyncConfigured ? L10n.text("已配置", "Configured") : L10n.text("按需准备", "On-demand"),
                            healthy: store.dataSyncConfigured || store.camsConfigured)
                statusBadge(title: "CAMS ADS",
                            value: store.camsConfigured ? L10n.text("已配置", "Configured") : L10n.t("未配置"),
                            healthy: store.camsConfigured)
                statusBadge(title: L10n.text("保留策略", "Retention"),
                            value: store.retentionConfigured ? L10n.text("已启用", "Active") : L10n.t("不限制"),
                            healthy: store.retentionConfigured)
            }
        }
        .padding(16)
        .fireSkySurface(role: .content, cornerRadius: 14)
    }

    private func statusBadge(title: String, value: String, healthy: Bool, showChevron: Bool = false) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 4) {
                Text(title).font(.caption2).foregroundStyle(.secondary)
                if showChevron {
                    Spacer(minLength: 0)
                    Image(systemName: "chevron.right")
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundStyle(.tertiary)
                }
            }
            HStack(spacing: 6) {
                Circle().fill(healthy ? Color.green : Color.orange).frame(width: 6, height: 6)
                Text(value).font(.caption.weight(.semibold)).lineLimit(1)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 12).padding(.vertical, 10)
        .background(Color.primary.opacity(scheme == .dark ? 0.045 : 0.03),
                    in: RoundedRectangle(cornerRadius: 10))
        .overlay {
            RoundedRectangle(cornerRadius: 10)
                .strokeBorder(Color.secondary.opacity(0.14), lineWidth: 1)
        }
    }

    // MARK: - 数据

    private let dataAnchors: [(String, String)] = [
        ("下载源", "anchor-sources"), ("CAMS", "anchor-cams"),
        ("连通性", "anchor-probe"), ("同步", "anchor-sync"),
        ("保留", "anchor-retention"), ("海拔", "anchor-elevation"),
    ]

    private func dataSection(scrollProxy: ScrollViewProxy) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(dataAnchors, id: \.1) { anchor in
                        Button {
                            if reduceMotion {
                                scrollProxy.scrollTo(anchor.1, anchor: .top)
                            } else {
                                withAnimation { scrollProxy.scrollTo(anchor.1, anchor: .top) }
                            }
                        } label: {
                            Text(L10n.t(anchor.0))
                                .font(.caption.weight(.medium))
                                .padding(.horizontal, 12).padding(.vertical, 7)
                                .background(Color.primary.opacity(scheme == .dark ? 0.06 : 0.045),
                                            in: Capsule())
                                .overlay {
                                    Capsule().strokeBorder(Color.secondary.opacity(0.16), lineWidth: 1)
                                }
                        }
                        .buttonStyle(.plain)
                        .foregroundStyle(.secondary)
                    }
                }
            }

            dataSourcesBlock
                .padding(16)
                .fireSkySurface(role: .content, cornerRadius: 14)
                .id("anchor-sources")

            camsBlock
                .padding(16)
                .fireSkySurface(role: .content, cornerRadius: 14)
                .id("anchor-cams")

            DownloadProbeView()
                .id("anchor-probe")

            dataSyncBlock
                .padding(16)
                .fireSkySurface(role: .content, cornerRadius: 14)
                .id("anchor-sync")

            retentionBlock
                .padding(16)
                .fireSkySurface(role: .content, cornerRadius: 14)
                .id("anchor-retention")

            elevationSourceBlock
                .padding(16)
                .fireSkySurface(role: .content, cornerRadius: 14)
                .id("anchor-elevation")
        }
    }

    private var dataSourcesBlock: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(L10n.t("下载源")).font(.headline)
            HStack {
                Text(L10n.t("EC 下载源")).font(.subheadline)
                Spacer()
                Picker(L10n.t("EC 下载源"), selection: $store.ecmwfSource) {
                    Text(L10n.t("ECMWF 官方")).tag("ecmwf")
                    Text(L10n.t("AWS 欧洲镜像")).tag("aws")
                    Text(L10n.t("Google Cloud 镜像")).tag("google")
                }
                .pickerStyle(.menu)
                .disabled(store.probingDownloads)
            }
            Text(L10n.t("EC 下载源同时用于下载探测、数据准备和预测。GFS 使用 NOAA NOMADS，CAMS 使用 Copernicus ADS。"))
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var camsBlock: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("CAMS · Copernicus ADS").font(.headline)
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: store.camsConfigured ? "checkmark.circle.fill" : "key")
                    .foregroundStyle(store.camsConfigured ? Color.green : Color.secondary)
                Text(store.camsConfigurationDescription).font(.callout).fixedSize(horizontal: false, vertical: true)
                Spacer()
                Button { Task { await store.refreshCAMSConfiguration() } } label: {
                    Image(systemName: "arrow.clockwise").frame(minWidth: 32, minHeight: 32)
                }.buttonStyle(.borderless)
                    .disabled(!store.online || store.savingCAMS)
            }
            HStack(spacing: 10) {
                SecureField(L10n.t("粘贴 ADS Personal Access Token"), text: $store.camsKeyDraft) {
                    store.saveCAMSKey()
                }
                .workInputField(cornerRadius: 8)
                .disabled(store.isRemoteMode || !store.canSaveCAMS || store.savingCAMS || store.probingDownloads)
                if store.savingCAMS { ProgressView() }
                Button(L10n.t("保存 Key"), systemImage: "key.fill") { store.saveCAMSKey() }
                    .disabled(store.isRemoteMode || !store.online || !store.canSaveCAMS || store.savingCAMS || store.probingDownloads
                              || store.camsKeyDraft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
            if store.isRemoteMode {
                Label(
                    L10n.text(
                        "远程模式下 CAMS Key 为只读，请在 Mac 端本地配置。",
                        "CAMS Key is read-only over remote HTTP; configure it locally on your Mac."
                    ),
                    systemImage: "lock.shield"
                )
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            } else {
                Text(L10n.t("仅以明文保存在本机配置文件，文件权限 0600；不会写入工程或显示已有 Key。"))
                    .font(.caption).foregroundStyle(.secondary)
            }
            if !store.isRemoteMode, store.camsConfiguration != nil && !store.canSaveCAMS {
                Text(L10n.t("环境变量正在覆盖配置。修改 CDSAPI_KEY / CDSAPI_URL 后需重新启动应用。"))
                    .font(.caption).foregroundStyle(.secondary)
            }
            if !store.camsMessage.isEmpty {
                Text(store.camsMessage).font(.callout)
                    .foregroundStyle(store.camsSaveFailed ? Color.red : Color.secondary)
            }
            DisclosureGroup(L10n.t("配置教程"), isExpanded: $store.showCAMSTutorial) {
                VStack(alignment: .leading, spacing: 14) {
                    tutorialStep("1", L10n.t("注册并登录 ADS"), detail: L10n.t("使用 ECMWF 账号登录 Atmosphere Data Store。")) {
                        Link(L10n.t("打开 ADS"), destination: URL(string: "https://ads.atmosphere.copernicus.eu/")!)
                    }
                    tutorialStep("2", L10n.t("复制 Personal Access Token"), detail: L10n.t("在官方 API 配置页复制 key 后面的 Token，不要复制 url 或整段配置。")) {
                        Link(L10n.t("获取 Token"), destination: URL(string: "https://ads.atmosphere.copernicus.eu/how-to-api")!)
                    }
                    tutorialStep("3", L10n.t("接受 CAMS 数据集条款"), detail: L10n.t("在数据集 Download 页底部接受 Terms of Use，完成网页端授权。")) {
                        Link(L10n.t("打开数据集"), destination: URL(string: "https://ads.atmosphere.copernicus.eu/datasets/cams-global-atmospheric-composition-forecasts?tab=download")!)
                    }
                    tutorialStep("4", L10n.t("返回 FireSky 保存 Key"), detail: L10n.t("粘贴到上方输入框并保存，再到预测准备窗口下载数据。")) {
                        EmptyView()
                    }
                    Text(L10n.t("这里只检查本地配置是否完整；Token 是否有效、数据集是否已授权，在实际下载时验证。"))
                        .font(.caption).foregroundStyle(.secondary)
                }.padding(.top, 12).padding(.bottom, 6)
            }
        }
    }

    private var dataSyncBlock: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(L10n.t("后台同步")).font(.headline)
            Toggle(L10n.t("启用后台同步"), isOn: $store.dataSyncEnabled)
            if store.dataSyncEnabled && !store.dataSyncRetentionConfigured {
                Label(L10n.t("未配置缓存保留策略，缓存将随同步增长；可在下方「存储与保留」中设置。"),
                      systemImage: "exclamationmark.triangle.fill")
                    .font(.caption).foregroundStyle(.orange)
                    .fixedSize(horizontal: false, vertical: true)
            }
            HStack(spacing: 10) {
                Toggle("CAMS", isOn: $store.dataSyncCAMS).disabled(!store.dataSyncEnabled)
                Toggle("GFS", isOn: $store.dataSyncGFS).disabled(!store.dataSyncEnabled)
                Toggle("EC", isOn: $store.dataSyncEC).disabled(!store.dataSyncEnabled)
            }
            .font(.subheadline)
            HStack(spacing: 12) {
                syncField(L10n.t("同步间隔"), text: $store.dataSyncInterval, suffix: L10n.t("分钟"))
                syncField(L10n.t("GFS/EC 时效上限"), text: $store.dataSyncLeadHorizon, suffix: L10n.t("小时"))
            }
            if !store.regionDefinitions.isEmpty {
                HStack {
                    Text(L10n.t("锚定区域")).font(.subheadline)
                    Spacer()
                    Picker(L10n.t("锚定区域"), selection: $store.dataSyncAnchor) {
                        Text(L10n.t("未设置")).tag("")
                        ForEach(store.regionDefinitions.indices, id: \.self) { index in
                            let region = store.regionDefinitions[index]
                            Text(region["label"] as? String ?? region["code"] as? String ?? "")
                                .tag(region["code"] as? String ?? "")
                        }
                    }.pickerStyle(.menu)
                }
            }
            Text(L10n.text(
                "监控间隔 5–1440 分钟（建议设为 10–15 分钟轻量监控上游更新，仅检测到新起报周期才下载，无更新不占带宽）；时效上限仅 GFS/EC 需要（3–240 小时、3 的倍数），CAMS 自动拉齐全部时效；锚定区域决定 CAMS 洲包络与 GFS/EC 下载范围。",
                "Polling interval 5–1440 min (10–15 min recommended for lightweight upstream monitoring; downloads only when a newer cycle is published). Lead horizon applies to GFS/EC (3–240 h, multiples of 3)."
            ))
            .font(.caption).foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)

            if !store.dataSyncMessage.isEmpty {
                Text(store.dataSyncMessage).font(.caption)
                    .foregroundStyle(store.dataSyncSaveFailed ? Color.red : Color.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            } else if store.dataSyncConfigured {
                Text(store.dataSyncEnabled ? L10n.t("后台同步已启用") : L10n.text("已配置，当前关闭", "Configured, currently off"))
                    .font(.caption).foregroundStyle(.secondary)
            }

            HStack(spacing: 12) {
                if store.savingDataSync || store.dataSyncRunning { ProgressView().controlSize(.small) }
                Spacer()
                Button(L10n.t("立即同步"), systemImage: "arrow.triangle.2.circlepath") {
                    store.runDataSyncNow()
                }
                .disabled(!store.online || store.busy || store.dataSyncRunning)
                Button(L10n.t("保存同步设置"), systemImage: "externaldrive.badge.checkmark") {
                    store.saveDataSyncSettings()
                }
                .disabled(!store.online || store.savingDataSync || store.busy)
            }
            .font(.callout)

            if store.dataSyncLoaded {
                Divider().opacity(0.4)
                VStack(alignment: .leading, spacing: 6) {
                    Text(L10n.t("同步状态")).font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                    syncSourceRow("cams", "CAMS")
                    syncSourceRow("gfs", "GFS")
                    syncSourceRow("ec", "EC")
                }
            }
        }
    }

    private func syncSourceRow(_ name: String, _ label: String) -> some View {
        let state = store.dataSyncState[name] as? JSONObject ?? [:]
        let result = state["last_result"] as? JSONObject ?? [:]
        let status = result["status"] as? String ?? ""
        let cycle = state["last_cycle"] as? String ?? ""
        let badge: String
        let color: Color
        switch status {
        case "synced": badge = L10n.t("已同步"); color = .green
        case "current": badge = L10n.t("已是最新"); color = .green
        case "failed": badge = L10n.t("失败"); color = .red
        case "cancelled": badge = L10n.t("已取消"); color = .secondary
        default: badge = L10n.t("从未同步"); color = .secondary
        }
        return HStack(spacing: 10) {
            Text(label).font(.caption.weight(.semibold)).frame(width: 44, alignment: .leading)
            Text(badge).font(.caption).foregroundStyle(color)
            if !cycle.isEmpty {
                Text(L10n.tf("最近周期 %@", cycle)).font(.caption2).foregroundStyle(.secondary)
            }
            Spacer()
        }
    }

    private var retentionBlock: some View {
        let activeRulesCount = [
            store.retentionGFS, store.retentionECMWF, store.retentionCAMS,
            store.retentionSatelliteDays, store.retentionReportDays, store.retentionMapDays,
        ].filter { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }.count

        return VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text(L10n.t("存储与保留")).font(.headline)
                Spacer()
                if activeRulesCount > 0 {
                    Text(L10n.text("已设 \(activeRulesCount) 项规则", "\(activeRulesCount) rules active"))
                        .font(.caption2.weight(.medium))
                        .foregroundStyle(warm)
                        .padding(.horizontal, 8).padding(.vertical, 3)
                        .background(warm.opacity(0.12), in: Capsule())
                }
            }
            Text(L10n.t("自动清理按周期、扫描或生成时间滚动保留；保存策略后可点击「立即按策略清理」立刻释放空间，后续也会在新数据校验成功后自动清理旧文件。占用中的文件会自动跳过。"))
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            let retentionColumns = Array(
                repeating: GridItem(.flexible(), spacing: 10),
                count: sizeClass == .compact ? 2 : 3
            )
            LazyVGrid(columns: retentionColumns, spacing: 10) {
                retentionField(L10n.t("GFS 周期"), text: $store.retentionGFS, suffix: L10n.t("个"))
                retentionField(L10n.t("EC 周期"), text: $store.retentionECMWF, suffix: L10n.t("个"))
                retentionField(L10n.t("CAMS 周期"), text: $store.retentionCAMS, suffix: L10n.t("个"))
                retentionField(L10n.t("卫星数据"), text: $store.retentionSatelliteDays, suffix: L10n.t("天"))
                retentionField(L10n.t("历史报告"), text: $store.retentionReportDays, suffix: L10n.t("天"))
                retentionField(L10n.t("报告地图"), text: $store.retentionMapDays, suffix: L10n.t("天"))
            }
            if !store.retentionMessage.isEmpty {
                Text(store.retentionMessage).font(.caption)
                    .foregroundStyle(store.retentionSaveFailed ? Color.red : Color.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            } else if store.retentionConfigured {
                Text(L10n.t("已启用自定义保留策略")).font(.caption).foregroundStyle(.secondary)
            }
            HStack(spacing: 12) {
                if store.savingRetention || store.applyingRetention { ProgressView().controlSize(.small) }
                Spacer()
                Button(L10n.text("立即按策略清理", "Clean up now"), systemImage: "sparkles") {
                    store.applyRetentionNow()
                }
                .disabled(!store.online || !store.retentionConfigured || store.busy || store.savingRetention || store.applyingRetention)
                Button(L10n.t("保存保留设置"), systemImage: "externaldrive.badge.checkmark") {
                    store.saveRetentionSettings()
                }
                .disabled(!store.online || store.savingRetention || store.applyingRetention || store.busy)
            }
            .font(.callout)
        }
    }

    private var elevationSourceBlock: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(L10n.text("海拔与时区来源", "Elevation & Timezone Source")).font(.headline)
            HStack {
                Text(L10n.text("海拔来源", "Elevation source")).font(.subheadline)
                Spacer()
                Picker(L10n.text("海拔来源", "Elevation source"), selection: Binding(
                    get: { store.elevationSource },
                    set: { store.setElevationSource($0) }
                )) {
                    Text(L10n.text("自动（HTTP 优先，SRTM 兜底）", "Auto (HTTP first, SRTM fallback)")).tag("auto")
                    Text(L10n.text("仅 HTTP（open-meteo）", "HTTP only (open-meteo)")).tag("http")
                    Text(L10n.text("SRTM 离线（本地时区 + 瓦片）", "SRTM offline (local TZ + tiles)")).tag("srtm")
                }
                .pickerStyle(.menu)
            }
            Text(L10n.text("切换后对下一次位置搜索或海拔查询生效；不会清空已显示的预测结果。",
                           "Takes effect on the next location search or metadata lookup."))
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: - 高级

    private var advancedSection: some View {
        VStack(alignment: .leading, spacing: 18) {
            remoteServerCard

            VStack(alignment: .leading, spacing: 8) {
                sectionLabel(L10n.text("连接状态", "Connection Status"))
                VStack(spacing: 0) {
                    HStack(spacing: 12) {
                        iconBadge("bolt.horizontal.circle")
                        VStack(alignment: .leading, spacing: 2) {
                            Text(L10n.text("服务端连接", "Server Connection")).font(.subheadline.weight(.medium))
                            Text(store.online
                                 ? L10n.text("已连接 · SSE 实时流优先（支持轮询自动回退）", "Connected · SSE streaming with polling fallback")
                                 : L10n.text("当前未连接", "Offline"))
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button(L10n.t("重新连接")) { Task { await store.connect() } }
                            .disabled(store.busy || store.probingDownloads || store.remoteTesting)
                    }
                    .padding(.vertical, 12)

                    if !store.remoteWorkspace.isEmpty {
                        Divider().opacity(0.55).padding(.leading, 46)
                        HStack(spacing: 12) {
                            iconBadge("folder")
                            VStack(alignment: .leading, spacing: 2) {
                                Text(L10n.text("服务端数据目录", "Server Data Directory")).font(.subheadline.weight(.medium))
                                Text(store.remoteWorkspace)
                                    .font(.caption.monospaced())
                                    .foregroundStyle(.secondary)
                                    .lineLimit(2)
                                    .textSelection(.enabled)
                            }
                            Spacer()
                        }
                        .padding(.vertical, 12)
                    }
                }
                .padding(.horizontal, 16)
                .fireSkySurface(role: .content, cornerRadius: 14)
            }

            let settingsError = store.errorText(for: .settings)
            if !settingsError.isEmpty {
                HStack(alignment: .top, spacing: 8) {
                    Text(settingsError).font(.callout).foregroundStyle(.red).textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 4)
                    Button { store.clearError() } label: {
                        Image(systemName: "xmark").font(.caption).foregroundStyle(.secondary)
                    }
                    .buttonStyle(.borderless)
                    .accessibilityLabel(L10n.close)
                }
                .padding(12)
                .background(Color.red.opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
            }
        }
    }

    private var remoteServerCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            sectionLabel(L10n.text("服务器连接（Mac / CF Tunnel / LAN）", "Server Connection (Mac / CF Tunnel / LAN)"))
            VStack(alignment: .leading, spacing: 12) {
                HStack(spacing: 12) {
                    iconBadge("server.rack")
                    VStack(alignment: .leading, spacing: 2) {
                        Text(L10n.text("FireSky 远程服务器", "FireSky Remote Server")).font(.subheadline.weight(.medium))
                        Text(L10n.text(
                            "直连 Mac / Windows / Docker HTTP 服务，共享预测计算、区域热图、卫星实况与任务历史。",
                            "Connect directly to your Mac, Windows, or Docker server for forecasts, regional maps, nowcasts, and history."
                        ))
                        .font(.caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    }
                    Spacer()
                }

                Divider().opacity(0.55)

                RemoteServerConnectionForm(showClearConfiguration: true)
            }
            .padding(16)
            .fireSkySurface(role: .content, cornerRadius: 14)
        }
    }

    // MARK: - Helpers

    private var appearanceSubtitle: String {
        switch store.appearance {
        case "light": return L10n.t("浅色")
        case "dark": return L10n.t("深色")
        default: return L10n.t("跟随系统")
        }
    }

    private func categoryTitle(_ key: String) -> String {
        switch key {
        case "通用": return L10n.catGeneral
        case "数据": return L10n.catData
        default: return L10n.catAdvanced
        }
    }

    private func categorySymbol(_ key: String) -> String {
        switch key {
        case "通用": return "paintpalette"
        case "数据": return "externaldrive"
        default: return "wrench.and.screwdriver"
        }
    }

    private func sectionLabel(_ title: String) -> some View {
        Text(title).font(.caption.weight(.semibold)).foregroundStyle(.secondary).padding(.leading, 4)
    }

    private func iconBadge(_ symbol: String) -> some View {
        Image(systemName: symbol)
            .font(.system(size: 14, weight: .medium))
            .foregroundStyle(Color.primary.opacity(0.82))
            .frame(width: 34, height: 34)
            .background(Color.primary.opacity(scheme == .dark ? 0.07 : 0.05),
                        in: RoundedRectangle(cornerRadius: 8))
    }

    private func retentionField(_ title: String, text: Binding<String>, suffix: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
            HStack(spacing: 4) {
                TextField(L10n.t("不限制"), text: text)
                    .workInputField(cornerRadius: 8)
                    .keyboardType(.decimalPad)
                Text(suffix).font(.caption2).foregroundStyle(.secondary)
            }
        }
    }

    private func syncField(_ title: String, text: Binding<String>, suffix: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.caption2).foregroundStyle(.secondary)
            HStack(spacing: 4) {
                TextField(L10n.t("未设置"), text: text)
                    .workInputField(cornerRadius: 8)
                    .keyboardType(.numberPad)
                Text(suffix).font(.caption2).foregroundStyle(.secondary)
            }
        }.frame(maxWidth: .infinity, alignment: .leading)
    }

    private func tutorialStep<Content: View>(_ number: String, _ title: String, detail: String,
                                             @ViewBuilder action: () -> Content) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Text(number).font(.caption.weight(.semibold)).foregroundStyle(.secondary).frame(width: 18)
            VStack(alignment: .leading, spacing: 5) {
                Text(title).font(.callout.weight(.medium))
                Text(detail).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                action().font(.caption)
            }
        }
    }
}
