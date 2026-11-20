import SwiftUI

/// 设置页。桌面 NSOpenPanel 选目录 → iOS 沙箱固定路径 + 文本框微调；
/// 新增「引擎通道」选择（内置模拟 / FFI），对应 EngineTransport 注入。
struct SettingsView: View {
    @EnvironmentObject private var store: Store
    var body: some View {
        Form {
            Section("外观") {
                Picker("主题", selection: $store.appearance) {
                    Text("跟随系统").tag("system")
                    Text("浅色").tag("light")
                    Text("深色").tag("dark")
                }.onChange(of: store.appearance) { _ in store.saveAppearance() }
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
            Section("海拔来源") {
                Picker("海拔来源", selection: Binding(
                    get: { store.elevationSource },
                    set: { store.setElevationSource($0) }
                )) {
                    Text("自动（HTTP 优先，SRTM 兜底）").tag("auto")
                    Text("仅 HTTP（open-meteo）").tag("http")
                    Text("SRTM 瓦片（本地时区 + 缓存/下载）").tag("srtm")
                }
                Text("自动：open-meteo 优先，失败落本地时区+SRTM；仅 HTTP：失败直接报错，不落 SRTM；SRTM：跳过 HTTP，缓存瓦片命中毫秒级，缺瓦片时按下载开关决定下载或报错。切换后对下一次位置搜索/海拔获取生效。")
                    .font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Section("引擎通道") {
                Picker("通道", selection: Binding(
                    get: { store.engineTransport },
                    set: { store.switchEngineTransport($0) }
                )) {
                    Text("内置模拟（Mock）").tag("mock")
                    Text("FFI（engine-mobile）").tag("ffi")
                }
                Text("Mock 通道在无引擎库时驱动界面全链路（模拟数据）；FFI 通道按 fsmobile.h C 契约对接进程内 engine-mobile 库，未链接时会明确报错。切换后自动重连。")
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
                    SecureField("粘贴 ADS Personal Access Token", text: $store.camsKeyDraft) {
                        store.saveCAMSKey()
                    }
                    .textFieldStyle(.roundedBorder)
                    .disabled(!store.canSaveCAMS || store.savingCAMS || store.probingDownloads)
                    if store.savingCAMS { ProgressView() }
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
            Section("本地引擎") {
                TextField("数据目录", text: $store.workspace)
                Text("iOS 沙箱不支持任意目录选择（桌面 NSOpenPanel 语义降级）：缓存根固定在应用沙箱内，可微调路径文本。")
                    .font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                Button("重新连接", systemImage: "arrow.clockwise") { Task { await store.connect() } }
                    .disabled(store.busy || store.probingDownloads)
            }
            if !store.error.isEmpty {
                Section { Text(store.error).foregroundStyle(.red).textSelection(.enabled) }
            }
        }
        .scrollContentBackground(.hidden)
        .workPageBackground()
        .disabled(store.busy)
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
