import AppKit
import SwiftUI

struct PreflightView: View {
    @EnvironmentObject private var store: Store
    @Environment(\.colorScheme) private var scheme
    private var isNowcast: Bool { store.requestKind == .nowcast }
    private var confirmTitle: String { isNowcast ? "确定分析" : "确定预测" }
    private var actions: PreparationActions {
        PreparationActions(ready: store.ready, busy: store.busy, hasSession: store.session != nil)
    }
    private var summary: String {
        if !store.ready {
            let missing = store.rows.filter { !$0.ready }.map(\.title).joined(separator: "、")
            return missing.isEmpty ? store.status : "需下载 \(missing)"
        }
        let updates = store.rows.filter(\.old).map(\.title).joined(separator: "、")
        return updates.isEmpty ? (isNowcast ? "数据已齐，可开始分析" : "数据已齐，可开始预测") : "数据已齐，可更新 \(updates)"
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                VStack(alignment: .leading, spacing: 6) {
                    Text(isNowcast ? "实况准备" : "预测准备").font(.title2.weight(.semibold))
                    Text(store.name).font(.callout).foregroundStyle(.secondary)
                }
                Spacer()
                Button { store.refreshCheck() } label: { Image(systemName: "arrow.clockwise") }
                    .help("重新检查").buttonStyle(.borderless).disabled(store.busy)
            }.padding(.bottom, 16)
            if store.rows.isEmpty {
                HStack { ProgressView().controlSize(.small); Text("正在检查本地数据") }
                    .frame(maxWidth: .infinity, minHeight: 180)
            } else {
                ForEach(store.rows) { row in
                    HStack(spacing: 12) {
                        Image(systemName: row.source == "hsd" ? "antenna.radiowaves.left.and.right" : "externaldrive")
                            .font(.title3).foregroundStyle(.secondary).frame(width: 32)
                        VStack(alignment: .leading, spacing: 4) {
                            Text(row.title).font(.body.weight(.medium))
                            Text(row.detail).font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Label(row.status, systemImage: !row.ready ? "arrow.down.circle" : row.old ? "arrow.clockwise" : "checkmark.circle.fill")
                            .font(.callout).foregroundStyle(!row.ready || row.old ? Color.primary : Color.green)
                    }.frame(height: 64)
                    if row.id != store.rows.last?.id { Divider() }
                }
            }
            VStack(alignment: .leading, spacing: 8) {
                if store.checking {
                    ProgressView(value: store.percent, total: 100)
                    Text(store.status).font(.caption).foregroundStyle(.secondary)
                } else if !store.error.isEmpty {
                    Text(store.error).font(.callout).foregroundStyle(.red).lineLimit(3).textSelection(.enabled)
                } else {
                    HStack {
                        Label(summary, systemImage: store.ready ? "checkmark.circle" : "arrow.down.circle")
                            .fontWeight(.medium)
                        if store.publicationsPending { ProgressView().controlSize(.mini) }
                    }.font(.callout).foregroundStyle(.primary)
                }
            }.frame(minHeight: 38, alignment: .leading).padding(.top, 12)
            HStack {
                Button("取消") { store.closePreflight() }.keyboardShortcut(.cancelAction)
                Spacer()
                if actions.primary == .download {
                    Button("下载缺失数据", systemImage: "arrow.down.to.line") { store.prepare() }
                        .buttonStyle(.borderedProminent).disabled(!actions.canDownload)
                        .foregroundStyle(scheme == .dark ? Color.black : Color.white)
                        .keyboardShortcut(.defaultAction)
                    Button(confirmTitle) {}.disabled(true)
                        .help("需先补齐本次数据")
                        .accessibilityHint("需先补齐本次数据")
                } else {
                    Button("更新数据", systemImage: "arrow.down.to.line") { store.prepare() }
                        .disabled(!actions.canDownload)
                    Button(confirmTitle) { store.confirm() }
                        .buttonStyle(.borderedProminent).disabled(!actions.canForecast)
                        .foregroundStyle(scheme == .dark ? Color.black : Color.white)
                        .keyboardShortcut(.defaultAction)
                }
            }.padding(.top, 14)
                .animation(.easeInOut(duration: 0.18), value: store.ready)
        }.padding(26).frame(width: 490)
            .background(Color(nsColor: .windowBackgroundColor))
    }
}

struct InventoryView: View {
    @EnvironmentObject private var store: Store
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                HStack {
                    Text("本地数据").font(.title.weight(.semibold))
                    Spacer()
                    Button("按本次位置下载", systemImage: "arrow.down") {
                        store.page = .point
                        store.beginCheck()
                    }.disabled(store.busy)
                    Button { Task { await store.loadInventory() } } label: {
                        Image(systemName: "arrow.clockwise")
                    }.help("刷新本地清单")
                }
                DownloadProbeView()
                Divider()
                ForEach(Array(store.inventory.enumerated()), id: \.offset) { _, dataset in
                    let source = dataset["source"] as? String ?? ""
                    let title = source == "ecmwf" ? "EC" : source.uppercased()
                    let cycles = dataset["cycles"] as? JSONObject ?? [:]
                    VStack(alignment: .leading, spacing: 12) {
                        HStack {
                            Image(systemName: "externaldrive").font(.title2).foregroundStyle(.secondary)
                            Text(title).font(.headline)
                            Spacer()
                            Text("\(dataset["total_file_count"] as? Int ?? 0) 个文件")
                            Text(String(format: "%.2f GB", (dataset["total_storage_mb"] as? Double ?? 0) / 1024))
                                .foregroundStyle(.secondary).monospacedDigit()
                            Button {
                                if let path = dataset["path"] as? String {
                                    NSWorkspace.shared.open(URL(fileURLWithPath: path))
                                }
                            } label: { Image(systemName: "folder") }.help("在访达中打开")
                        }
                        DisclosureGroup("\(cycles.count) 个周期", isExpanded: Binding(
                            get: { store.expandedInventory.contains(source) },
                            set: { if $0 { store.expandedInventory.insert(source) } else { store.expandedInventory.remove(source) } }
                        )) {
                            ForEach(cycles.keys.sorted().reversed(), id: \.self) { key in
                                let item = cycles[key] as? JSONObject ?? [:]
                                HStack {
                                    Text(key).monospaced()
                                    Spacer()
                                    Text("\(item["files"] as? Int ?? 0) 个文件").foregroundStyle(.secondary)
                                }.font(.callout).padding(.vertical, 5)
                            }
                        }
                    }
                    Divider()
                }
                if store.inventory.isEmpty && store.error.isEmpty { ProgressView().frame(maxWidth: .infinity) }
                if !store.error.isEmpty { Text(store.error).foregroundStyle(.red) }
                Divider().padding(.vertical, 8)
                NativeTaskHistoryView()
            }.padding(30)
        }
    }
}

struct SettingsView: View {
    @EnvironmentObject private var store: Store
    var body: some View {
        Form {
            Section("外观") {
                Picker("主题", selection: $store.appearance) {
                    Text("跟随系统").tag("system")
                    Text("浅色").tag("light")
                    Text("深色").tag("dark")
                }.onChange(of: store.appearance) { _, _ in store.saveAppearance() }
            }
            Section("数据源") {
                Picker("EC 下载源", selection: $store.ecmwfSource) {
                    Text("ECMWF 官方").tag("ecmwf")
                    Text("AWS 欧洲镜像").tag("aws")
                    Text("Google Cloud 镜像").tag("google")
                }.disabled(store.probingDownloads)
                Text("EC 下载源会同时用于下载探测、数据准备和后续预测。GFS 使用 NOAA NOMADS，CAMS 使用 Copernicus ADS，当前没有可切换的官方镜像。")
                    .font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Section("CAMS · Copernicus ADS") {
                HStack(alignment: .top, spacing: 10) {
                    Image(systemName: store.camsConfigured ? "checkmark.circle.fill" : "key")
                        .foregroundStyle(store.camsConfigured ? Color.green : Color.secondary)
                    Text(store.camsConfigurationDescription).fixedSize(horizontal: false, vertical: true)
                    Spacer()
                    Button { Task { await store.refreshCAMSConfiguration() } } label: {
                        Image(systemName: "arrow.clockwise")
                    }.buttonStyle(.borderless).help("重新检查 CAMS 配置")
                        .disabled(!store.online || store.savingCAMS)
                }
                HStack(spacing: 10) {
                    SecureField("粘贴 ADS Personal Access Token", text: $store.camsKeyDraft)
                        .textFieldStyle(.roundedBorder).onSubmit { store.saveCAMSKey() }
                        .disabled(!store.canSaveCAMS || store.savingCAMS || store.probingDownloads)
                    if store.savingCAMS { ProgressView().controlSize(.small) }
                    Button("保存 Key", systemImage: "key.fill") { store.saveCAMSKey() }
                        .disabled(!store.online || !store.canSaveCAMS || store.savingCAMS || store.probingDownloads
                                  || store.camsKeyDraft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
                Text("仅以明文保存在本机配置文件，文件权限 0600；不会写入工程或显示已有 Key。")
                    .font(.caption).foregroundStyle(.secondary)
                if store.camsConfiguration != nil && !store.canSaveCAMS {
                    Text("环境变量正在覆盖配置。修改 CDSAPI_KEY / CDSAPI_URL 后需重新启动应用。")
                        .font(.caption).foregroundStyle(.secondary)
                }
                if !store.camsMessage.isEmpty {
                    Text(store.camsMessage).font(.callout)
                        .foregroundStyle(store.camsSaveFailed ? Color.red : Color.secondary)
                }
                DisclosureGroup("配置教程", isExpanded: $store.showCAMSTutorial) {
                    VStack(alignment: .leading, spacing: 14) {
                        tutorialStep("1", "注册并登录 ADS", detail: "使用 ECMWF 账号登录 Atmosphere Data Store。") {
                            Link("打开 ADS", destination: URL(string: "https://ads.atmosphere.copernicus.eu/")!)
                        }
                        tutorialStep("2", "复制 Personal Access Token", detail: "在官方 API 配置页复制 key 后面的 Token，不要复制 url 或整段配置。") {
                            Link("获取 Token", destination: URL(string: "https://ads.atmosphere.copernicus.eu/how-to-api")!)
                        }
                        tutorialStep("3", "接受 CAMS 数据集条款", detail: "在数据集 Download 页底部接受 Terms of Use，完成网页端授权。") {
                            Link("打开数据集", destination: URL(string: "https://ads.atmosphere.copernicus.eu/datasets/cams-global-atmospheric-composition-forecasts?tab=download")!)
                        }
                        tutorialStep("4", "返回 FireSky 保存 Key", detail: "粘贴到上方输入框并保存，再到预测准备窗口下载数据。") {
                            EmptyView()
                        }
                        Text("这里只检查本地配置是否完整；Token 是否有效、数据集是否已授权，在实际下载时验证。")
                            .font(.caption).foregroundStyle(.secondary)
                    }.padding(.top, 12).padding(.bottom, 6)
                }
            }
            Section("缓存与报告保留") {
                Text("留空表示不自动清理。周期按数据源分别计算；卫星、报告和地图按扫描或生成时间滚动保留。新数据校验成功后才会清理旧文件。")
                    .font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 12) {
                    retentionField("GFS 周期", text: $store.retentionGFS, suffix: "个")
                    retentionField("EC 周期", text: $store.retentionECMWF, suffix: "个")
                    retentionField("CAMS 周期", text: $store.retentionCAMS, suffix: "个")
                }
                HStack(spacing: 12) {
                    retentionField("卫星数据", text: $store.retentionSatelliteDays, suffix: "天")
                    retentionField("历史报告", text: $store.retentionReportDays, suffix: "天")
                    retentionField("报告地图", text: $store.retentionMapDays, suffix: "天")
                }
                HStack {
                    if store.savingRetention { ProgressView().controlSize(.small) }
                    if !store.retentionMessage.isEmpty {
                        Text(store.retentionMessage)
                            .font(.caption)
                            .foregroundStyle(store.retentionSaveFailed ? Color.red : Color.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    } else if store.retentionConfigured {
                        Text("已启用自定义保留策略").font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button("保存保留设置", systemImage: "externaldrive.badge.checkmark") {
                        store.saveRetentionSettings()
                    }
                    .disabled(!store.online || store.savingRetention || store.busy)
                }
            }
            Section("本地引擎") {
                TextField("数据目录", text: $store.workspace)
                TextField("Rust 引擎（留空使用内置）", text: $store.engineBinary)
                HStack {
                    Button("选择目录", systemImage: "folder") {
                        let panel = NSOpenPanel()
                        panel.canChooseFiles = false
                        panel.canChooseDirectories = true
                        if panel.runModal() == .OK, let url = panel.url {
                            store.workspace = url.path
                        }
                    }
                    Spacer()
                    Button("重新连接", systemImage: "arrow.clockwise") { Task { await store.connect() } }
                        .disabled(store.busy || store.probingDownloads)
                }
            }
            if !store.error.isEmpty {
                Section { Text(store.error).foregroundStyle(.red).textSelection(.enabled) }
            }
        }.formStyle(.grouped).padding(16).disabled(store.busy)
    }

    private func retentionField(_ title: String, text: Binding<String>, suffix: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            HStack(spacing: 5) {
                TextField("不限制", text: text)
                    .textFieldStyle(.roundedBorder)
                    .frame(minWidth: 64)
                Text(suffix).font(.caption).foregroundStyle(.secondary)
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
