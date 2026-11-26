import SwiftUI
import UIKit

#if canImport(AVFoundation) && !targetEnvironment(macCatalyst)
import AVFoundation

/// 二维码扫描弹窗：扫描 `firesky://connect?url=...&token=...` 配对链接。
struct QRScannerSheet: View {
    @Environment(\.dismiss) private var dismiss
    let onScan: (String) -> Void

    @State private var authStatus: AVAuthorizationStatus = AVCaptureDevice.authorizationStatus(for: .video)
    @State private var cameraUnavailable = AVCaptureDevice.default(for: .video) == nil
    @State private var didScan = false

    var body: some View {
        NavigationStack {
            ZStack {
                Color.black.ignoresSafeArea()
                content
            }
            .navigationTitle(L10n.text("扫码配对", "Scan Pairing QR"))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(L10n.close) { dismiss() }
                }
            }
            .task {
                if authStatus == .notDetermined {
                    let granted = await AVCaptureDevice.requestAccess(for: .video)
                    authStatus = granted ? .authorized : .denied
                }
                cameraUnavailable = AVCaptureDevice.default(for: .video) == nil
            }
        }
    }

    @ViewBuilder
    private var content: some View {
        switch authStatus {
        case .authorized:
            if cameraUnavailable {
                unavailableView(
                    symbol: "video.slash",
                    title: L10n.text("当前设备无可用相机", "Camera Unavailable"),
                    message: L10n.text(
                        "请关闭弹窗后使用「粘贴配对链接」，或手动填入服务器地址与令牌。",
                        "Close this sheet and use Paste Link, or enter the server URL and token manually."
                    ),
                    showSettingsButton: false
                )
            } else {
                ZStack {
                    QRCapturePreviewView { code in
                        guard !didScan else { return }
                        didScan = true
                        onScan(code)
                        dismiss()
                    }
                    .ignoresSafeArea()

                    reticleOverlay
                }
            }
        case .notDetermined:
            VStack(spacing: 12) {
                ProgressView()
                    .tint(.white)
                Text(L10n.text("正在请求相机权限…", "Requesting camera access…"))
                    .font(.subheadline)
                    .foregroundStyle(.white.opacity(0.85))
            }
            .padding(24)
        case .denied, .restricted:
            unavailableView(
                symbol: "camera.badge.ellipsis",
                title: L10n.text("需要相机权限", "Camera Access Needed"),
                message: L10n.text(
                    "请在 iOS 设置中允许 FireSky 访问相机以扫描配对二维码，或返回使用「粘贴配对链接」。",
                    "Allow FireSky to access the camera in iOS Settings to scan pairing QR codes, or use Paste Link."
                ),
                showSettingsButton: true
            )
        @unknown default:
            unavailableView(
                symbol: "camera",
                title: L10n.text("相机不可用", "Camera Unavailable"),
                message: L10n.text("请使用「粘贴配对链接」完成配对。", "Use Paste Link to pair."),
                showSettingsButton: false
            )
        }
    }

    private var reticleOverlay: some View {
        VStack(spacing: 20) {
            Spacer()
            RoundedRectangle(cornerRadius: 18)
                .strokeBorder(warm, lineWidth: 2.5)
                .frame(width: 230, height: 230)
                .background(Color.white.opacity(0.03), in: RoundedRectangle(cornerRadius: 18))
            Text(L10n.text(
                "对准 Mac 或 Docker 端展示的 FireSky 配对二维码",
                "Point the camera at the FireSky pairing QR code"
            ))
            .font(.caption.weight(.medium))
            .foregroundStyle(.white)
            .multilineTextAlignment(.center)
            .padding(.horizontal, 16)
            .padding(.vertical, 8)
            .background(Color.black.opacity(0.6), in: Capsule())
            Spacer()
        }
        .padding(24)
        .allowsHitTesting(false)
    }

    private func unavailableView(
        symbol: String,
        title: String,
        message: String,
        showSettingsButton: Bool
    ) -> some View {
        VStack(spacing: 14) {
            Image(systemName: symbol)
                .font(.system(size: 36))
                .foregroundStyle(warm)
            Text(title)
                .font(.headline)
                .foregroundStyle(.white)
            Text(message)
                .font(.caption)
                .foregroundStyle(.white.opacity(0.8))
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
            if showSettingsButton, let settingsURL = URL(string: UIApplication.openSettingsURLString) {
                Button(L10n.text("前往系统设置", "Open Settings")) {
                    UIApplication.shared.open(settingsURL)
                }
                .buttonStyle(.borderedProminent)
                .tint(warm)
            }
        }
        .padding(28)
    }
}

private struct QRCapturePreviewView: UIViewRepresentable {
    let onScan: (String) -> Void

    func makeCoordinator() -> Coordinator {
        Coordinator(onScan: onScan)
    }

    func makeUIView(context: Context) -> CameraContainerView {
        let view = CameraContainerView()
        context.coordinator.configure(in: view)
        return view
    }

    func updateUIView(_ uiView: CameraContainerView, context: Context) {}

    static func dismantleUIView(_ uiView: CameraContainerView, coordinator: Coordinator) {
        coordinator.stop()
    }

    final class CameraContainerView: UIView {
        var previewLayer: AVCaptureVideoPreviewLayer?

        override func layoutSubviews() {
            super.layoutSubviews()
            previewLayer?.frame = bounds
        }
    }

    final class Coordinator: NSObject, AVCaptureMetadataOutputObjectsDelegate {
        private let onScan: (String) -> Void
        private let session = AVCaptureSession()
        private let sessionQueue = DispatchQueue(label: "cn.firesky.mobile.qrscanner")
        private var configured = false
        private var didEmit = false

        init(onScan: @escaping (String) -> Void) {
            self.onScan = onScan
        }

        func configure(in view: CameraContainerView) {
            let previewLayer = AVCaptureVideoPreviewLayer(session: session)
            previewLayer.videoGravity = .resizeAspectFill
            previewLayer.frame = view.bounds
            view.layer.addSublayer(previewLayer)
            view.previewLayer = previewLayer

            sessionQueue.async { [weak self] in
                guard let self, !self.configured else { return }
                self.configured = true
                guard let device = AVCaptureDevice.default(for: .video),
                      let input = try? AVCaptureDeviceInput(device: device),
                      self.session.canAddInput(input) else {
                    return
                }
                self.session.beginConfiguration()
                self.session.addInput(input)

                let output = AVCaptureMetadataOutput()
                if self.session.canAddOutput(output) {
                    self.session.addOutput(output)
                    output.setMetadataObjectsDelegate(self, queue: .main)
                    if output.availableMetadataObjectTypes.contains(.qr) {
                        output.metadataObjectTypes = [.qr]
                    }
                }
                self.session.commitConfiguration()
                if !self.session.isRunning {
                    self.session.startRunning()
                }
            }
        }

        func stop() {
            sessionQueue.async { [weak self] in
                guard let self, self.session.isRunning else { return }
                self.session.stopRunning()
            }
        }

        func metadataOutput(
            _ output: AVCaptureMetadataOutput,
            didOutput metadataObjects: [AVMetadataObject],
            from connection: AVCaptureConnection
        ) {
            guard !didEmit,
                  let readable = metadataObjects.first as? AVMetadataMachineReadableCodeObject,
                  readable.type == .qr,
                  let value = readable.stringValue?.trimmingCharacters(in: .whitespacesAndNewlines),
                  !value.isEmpty else {
                return
            }
            didEmit = true
            stop()
            DispatchQueue.main.async { [onScan] in
                onScan(value)
            }
        }
    }
}
#else
/// Mac Catalyst 等不支持 AVFoundation 相机的环境回落视图。
struct QRScannerSheet: View {
    @Environment(\.dismiss) private var dismiss
    let onScan: (String) -> Void

    var body: some View {
        NavigationStack {
            VStack(spacing: 14) {
                Image(systemName: "qrcode.viewfinder")
                    .font(.system(size: 36))
                    .foregroundStyle(warm)
                Text(L10n.text("当前平台不支持相机扫码", "Camera scanning is unavailable here"))
                    .font(.headline)
                Text(L10n.text("请使用「粘贴配对链接」导入配对信息。", "Use Paste Link to import your pairing URI."))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .padding(28)
            .navigationTitle(L10n.text("扫码配对", "Scan Pairing QR"))
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(L10n.close) { dismiss() }
                }
            }
        }
    }
}
#endif
