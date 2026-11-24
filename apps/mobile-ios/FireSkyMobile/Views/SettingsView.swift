import SwiftUI

private let SettingsCategories = ["通用", "数据", "高级"]

/// 设置页（v1.6.0 分类拆分，macOS SettingsCategory 同构）：通用 / 数据
/// （下载源 + CAMS + 下载连通性）/ 位置与海拔 / 高级（引擎通道 + 沙箱路径）。
/// 切换分类保留各表单草稿（全部常驻 store，不随视图销毁）。
struct SettingsView: View {
    @EnvironmentObject private var store: Store
    var body: some View {
        NavigationStack {
        Form {
            Picker("分类", selection: $store.settingsCategory) {
                ForEach(SettingsCategories, id: \.self) { category in
                    Text(categoryTitle(category)).tag(category)
                }
            }.pickerStyle(.segmented)
                .listRowBackground(Color.clear)
                .listRowInsets(EdgeInsets())

            if store.settingsCategory == "通用" {
                Section(L10n.text("外观", "Appearance")) {
                    Picker(L10n.text("主题", "Theme"), selection: $store.appearance) {
                        Label(L10n.text("跟随系统", "System"), systemImage: "circle.lefthalf.filled").tag("system")
                        Label(L10n.text("浅色", "Light"), systemImage: "sun.max").tag("light")
                        Label(L10n.text("深色", "Dark"), systemImage: "moon").tag("dark")
                    }.pickerStyle(.segmented)
                        .onChange(of: store.appearance) { _ in store.saveAppearance() }
                        .accessibilityIdentifier("appearance-picker")
                }
                // v1.9.5 i18n：语言偏好（跟随系统/中文/English）。
                // 写 UserDefaults 后由 RootView 的 @AppStorage 触发整树刷新。
                Section(L10n.text("语言", "Language")) {
                    Picker(L10n.text("界面语言", "Interface language"), selection: Binding(
                        get: { L10n.language.rawValue },
                        set: { L10n.language = L10n.Language(rawValue: $0) ?? .system }
                    )) {
                        Text("跟随系统 / System").tag(L10n.Language.system.rawValue)
                        Text("中文").tag(L10n.Language.chinese.rawValue)
                        Text("English").tag(L10n.Language.english.rawValue)
                    }
                }
            }

            if store.settingsCategory == "数据" {
                Section(L10n.t("下载源")) {
                    Picker(L10n.t("EC 下载源"), selection: $store.ecmwfSource) {
                        Text(L10n.t("ECMWF 官方")).tag("ecmwf")
                        Text(L10n.t("AWS 欧洲镜像")).tag("aws")
                        Text(L10n.t("Google Cloud 镜像")).tag("google")
                    }.disabled(store.probingDownloads)
                    Text(L10n.t("EC 下载源同时用于下载探测、数据准备和预测。GFS 使用 NOAA NOMADS，CAMS 使用 Copernicus ADS。"))
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
                        }.buttonStyle(.borderless)
                            .disabled(!store.online || store.savingCAMS)
                    }
                    HStack(spacing: 10) {
                        SecureField(L10n.t("粘贴 ADS Personal Access Token"), text: $store.camsKeyDraft) {
                            store.saveCAMSKey()
                        }
                        .textFieldStyle(.roundedBorder)
                        .disabled(!store.canSaveCAMS || store.savingCAMS || store.probingDownloads)
                        if store.savingCAMS { ProgressView() }
                        Button(L10n.t("保存 Key"), systemImage: "key.fill") { store.saveCAMSKey() }
                            .disabled(!store.online || !store.canSaveCAMS || store.savingCAMS || store.probingDownloads
                                      || store.camsKeyDraft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    }
                    Text(L10n.t("仅以明文保存在本机配置文件，文件权限 0600；不会写入工程或显示已有 Key。"))
                        .font(.caption).foregroundStyle(.secondary)
                    if store.camsConfiguration != nil && !store.canSaveCAMS {
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
                            tutorialStep("4", L10n.t("返回 FireSky 保存 Key"), detail: L10n.t("粘贴到上方输入框并保存，再到预测准备面板下载数据。")) {
                                EmptyView()
                            }
                            Text(L10n.t("这里只检查本地配置是否完整；Token 是否有效、数据集是否已授权，在实际下载时验证。"))
                                .font(.caption).foregroundStyle(.secondary)
                        }.padding(.top, 12).padding(.bottom, 6)
                    }
                }
                Section("下载连通性") {
                    DownloadProbeView()
                }
            }

            if store.settingsCategory == "高级" {
                Section(L10n.t("引擎通道")) {
                    Picker("通道", selection: Binding(
                        get: { store.engineTransport },
                        set: { store.switchEngineTransport($0) }
                    )) {
                        Text(L10n.t("内置模拟（Mock）")).tag("mock")
                        Text(L10n.t("FFI（engine-mobile）")).tag("ffi")
                    }
                    Text("FFI 按 fsmobile.h C 契约对接进程内 engine-mobile 库；Mock 通道在无引擎库时驱动界面全链路（模拟数据）。切换后自动重连。")
                        .font(.caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Section(L10n.t("本地引擎")) {
                    TextField(L10n.t("数据目录"), text: $store.workspace)
                    Text("iOS 沙箱不支持任意目录选择：缓存根固定在应用沙箱内，可微调路径文本。")
                        .font(.caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    Button(L10n.t("重新连接"), systemImage: "arrow.clockwise") { Task { await store.connect() } }
                        .disabled(store.busy || store.probingDownloads)
                }
                if !store.error.isEmpty {
                    Section { Text(store.error).foregroundStyle(.red).textSelection(.enabled) }
                }
            }
        }
        .scrollContentBackground(.hidden)
        .workPageBackground()
        .navigationTitle(L10n.navSettings)
        .disabled(store.busy)
        }
    }

    /// 分类显示名（状态键保持中文，标题随语言切换）。
    private func categoryTitle(_ key: String) -> String {
        switch key {
        case "通用": return L10n.catGeneral
        case "数据": return L10n.catData
        default: return L10n.catAdvanced
        }
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
