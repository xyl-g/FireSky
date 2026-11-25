import SwiftUI

/// 可复用的远程服务器连接表单（同时用于首次引导卡与设置页，避免重复代码）。
struct RemoteServerConnectionForm: View {
    @EnvironmentObject private var store: Store
    @Environment(\.colorScheme) private var scheme

    var showClearConfiguration: Bool = false

    private var urlIsEmpty: Bool {
        store.remoteServerURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            VStack(alignment: .leading, spacing: 6) {
                Text(L10n.text("服务器地址", "Server URL"))
                    .font(.caption.weight(.medium))
                    .foregroundStyle(.secondary)
                TextField("https://firesky.example.com 或 http://192.168.1.x:8765", text: $store.remoteServerURL)
                    .keyboardType(.URL)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .workInputField(cornerRadius: 8)
                    .font(.callout.monospaced())
            }

            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    Text(L10n.text("访问令牌（Bearer Token）", "Access Token (Bearer)"))
                        .font(.caption.weight(.medium))
                        .foregroundStyle(.secondary)
                    Spacer()
                    if store.remoteTokenSaved {
                        Button(L10n.text("清除令牌", "Clear token")) {
                            store.clearRemoteToken()
                        }
                        .font(.caption)
                        .buttonStyle(.borderless)
                        .foregroundStyle(.red)
                        .disabled(store.remoteTesting || store.busy)
                    }
                }
                SecureField(
                    store.remoteTokenSaved
                        ? L10n.text("已保存令牌（留空保持不变）", "Token saved (leave blank to keep)")
                        : L10n.text("粘贴 Mac / Docker 生成的访问令牌", "Paste Mac / Docker access token"),
                    text: $store.remoteTokenDraft
                )
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .workInputField(cornerRadius: 8)
            }

            HStack(spacing: 10) {
                Button {
                    Task { await store.testRemoteServer() }
                } label: {
                    HStack(spacing: 6) {
                        if store.remoteTesting {
                            ProgressView().controlSize(.small)
                        } else {
                            Image(systemName: "antenna.radiowaves.left.and.right")
                        }
                        Text(L10n.text("测试连接", "Test Connection"))
                    }
                    .frame(maxWidth: .infinity, minHeight: 36)
                }
                .buttonStyle(.bordered)
                .disabled(store.remoteTesting || store.busy || store.probingDownloads || urlIsEmpty)

                Button {
                    Task { await store.saveAndConnectRemoteServer() }
                } label: {
                    HStack(spacing: 6) {
                        Image(systemName: "checkmark.circle.fill")
                        Text(L10n.text("保存并连接", "Save & Connect"))
                    }
                    .frame(maxWidth: .infinity, minHeight: 36)
                }
                .buttonStyle(.borderedProminent)
                .tint(warm)
                .disabled(store.remoteTesting || store.busy || store.probingDownloads || urlIsEmpty)
            }
            .font(.subheadline.weight(.medium))

            if !store.remoteTestMessage.isEmpty {
                HStack(alignment: .top, spacing: 8) {
                    Image(systemName: store.remoteTestOK ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                        .foregroundStyle(store.remoteTestOK ? Color.green : Color.red)
                    Text(store.remoteTestMessage)
                        .font(.caption)
                        .foregroundStyle(store.remoteTestOK ? Color.green : Color.red)
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 0)
                }
                .padding(10)
                .background(
                    (store.remoteTestOK ? Color.green : Color.red).opacity(0.09),
                    in: RoundedRectangle(cornerRadius: 8)
                )
            }

            if showClearConfiguration && store.hasSavedRemoteServer {
                Button(role: .destructive) {
                    store.clearRemoteServerConfiguration()
                } label: {
                    Label(L10n.text("断开并清除服务器配置", "Disconnect & Clear Server"), systemImage: "trash")
                        .font(.caption)
                }
                .buttonStyle(.borderless)
                .disabled(store.busy || store.remoteTesting || store.probingDownloads)
            }
        }
    }
}

/// 首次启动远程配对引导卡（无 remoteServerURL 且非 -UITestMockMode 时展示）。
struct RemoteOnboardingCard: View {
    @EnvironmentObject private var store: Store
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let dark = scheme == .dark
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 12) {
                FireSkyMarkScene(size: 46)
                VStack(alignment: .leading, spacing: 3) {
                    Text(L10n.text("连接 FireSky 服务器", "Connect to FireSky Server"))
                        .font(.headline)
                    Text(L10n.text(
                        "手机端为轻量远程客户端，直连你的 Mac / Windows / Docker 服务端，实时同步预测、区域热图与卫星实况。",
                        "Mobile acts as a thin client connecting to your Mac, Windows, or Docker FireSky server."
                    ))
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
            }

            VStack(alignment: .leading, spacing: 7) {
                pairingStep("01", L10n.text(
                    "在 Mac「设置 → 高级 → 局域网与隧道服务」或 Docker 开启 HTTP 服务",
                    "Enable the HTTP service in Mac Settings → Advanced → LAN & Tunnel (or Docker)"
                ), dark: dark)
                pairingStep("02", L10n.text(
                    "填入 Cloudflare Tunnel HTTPS 或局域网地址，以及 Bearer 访问令牌",
                    "Enter your Cloudflare Tunnel HTTPS or LAN URL and Bearer token"
                ), dark: dark)
                pairingStep("03", L10n.text(
                    "点击「测试连接」验证后，「保存并连接」即可开始预测",
                    "Tap Test Connection, then Save & Connect to begin forecasting"
                ), dark: dark)
            }

            Divider().opacity(0.45)

            RemoteServerConnectionForm()
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .fireSkySurface(role: .content, emphasized: true)
    }

    private func pairingStep(_ num: String, _ text: String, dark: Bool) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Text(num)
                .font(.caption2.weight(.bold))
                .monospacedDigit()
                .foregroundStyle(warm)
                .padding(.horizontal, 6)
                .padding(.vertical, 2)
                .background(warm.opacity(dark ? 0.16 : 0.12), in: Capsule())
            Text(text)
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}
