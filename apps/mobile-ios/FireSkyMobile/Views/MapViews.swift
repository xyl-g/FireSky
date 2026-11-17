import MapKit
import SwiftUI

/// ForecastMap：单点地图选点（NSViewRepresentable → UIViewRepresentable）。
/// 区域绘制/网格 overlay 属区域预测，v1 不迁；滚轮缩放（桌面）降级为
/// 双指捏合（MKMapView 原生）+ 锚点缩放按钮（MapZoom.rect 承担同一几何语义）。
struct ForecastMap: View {
    @ObservedObject var store: Store
    var mini = false
    @StateObject private var proxy = MapProxy()

    var body: some View {
        InnerForecastMap(store: store, mini: mini, proxy: proxy)
            .overlay(alignment: .bottomTrailing) {
                MapZoomControls(proxy: proxy).padding(10)
            }
    }
}

private struct InnerForecastMap: UIViewRepresentable {
    @ObservedObject var store: Store
    var mini: Bool
    let proxy: MapProxy

    func makeCoordinator() -> Coordinator { Coordinator(store) }

    func makeUIView(context: Context) -> MKMapView {
        let map = MKMapView(frame: .zero)
        map.isZoomEnabled = true
        map.isScrollEnabled = true
        map.isRotateEnabled = false
        map.isPitchEnabled = false
        map.showsCompass = !mini
        map.showsScale = !mini
        map.pointOfInterestFilter = .excludingAll
        let configuration = MKStandardMapConfiguration(elevationStyle: .flat, emphasisStyle: .muted)
        configuration.pointOfInterestFilter = .excludingAll
        map.preferredConfiguration = configuration
        map.setRegion(MKCoordinateRegion(
            center: CLLocationCoordinate2D(latitude: store.lat, longitude: store.lon),
            span: MKCoordinateSpan(latitudeDelta: mini ? 0.025 : 3, longitudeDelta: mini ? 0.035 : 4)
        ), animated: false)
        let tap = UITapGestureRecognizer(target: context.coordinator,
                                         action: #selector(Coordinator.tap(_:)))
        map.addGestureRecognizer(tap)
        proxy.map = map
        return map
    }

    func updateUIView(_ map: MKMapView, context: Context) {
        let coordinator = context.coordinator
        let signature = "\(store.lat)-\(store.lon)-\(store.name)"
        guard signature != coordinator.signature else { return }
        coordinator.signature = signature
        let point = CLLocationCoordinate2D(latitude: store.lat, longitude: store.lon)
        map.removeAnnotations(map.annotations)
        let pin = MKPointAnnotation()
        pin.coordinate = point
        pin.title = store.name
        map.addAnnotation(pin)
        if coordinator.lastLat != store.lat || coordinator.lastLon != store.lon {
            map.setCenter(point, animated: true)
            coordinator.lastLat = store.lat
            coordinator.lastLon = store.lon
        }
    }

    @MainActor final class Coordinator: NSObject {
        let store: Store
        var signature = ""
        var lastLat = Double.nan
        var lastLon = Double.nan
        init(_ store: Store) { self.store = store }

        @objc func tap(_ gesture: UITapGestureRecognizer) {
            guard let map = gesture.view as? MKMapView, !store.busy else { return }
            let coordinate = map.convert(gesture.location(in: map), toCoordinateFrom: map)
            store.selectPoint(lat: coordinate.latitude, lon: coordinate.longitude)
        }
    }
}

/// 桌面 WheelZoomMapView 锚点缩放的移动替代：以 MapZoom.rect（原样迁移）为
/// 几何核心的 +/- 按钮；捏合手势仍交给 MKMapView 原生处理。
final class MapProxy: ObservableObject {
    weak var map: MKMapView?

    func zoom(_ delta: Double) {
        guard let map else { return }
        let current = map.visibleMapRect
        let anchor = MKMapPoint(map.centerCoordinate)
        guard let rect = MapZoom.rect(
            CGRect(x: current.origin.x, y: current.origin.y, width: current.width, height: current.height),
            anchor: CGPoint(x: anchor.x, y: anchor.y),
            delta: delta, precise: false, worldSize: MKMapRect.world.width
        ) else { return }
        map.setVisibleMapRect(MKMapRect(x: rect.minX, y: rect.minY, width: rect.width, height: rect.height), animated: false)
    }
}

private struct MapZoomControls: View {
    let proxy: MapProxy
    var body: some View {
        VStack(spacing: 8) {
            zoomButton("plus", delta: 1, accessibility: "放大地图")
            zoomButton("minus", delta: -1, accessibility: "缩小地图")
        }
    }

    private func zoomButton(_ symbol: String, delta: Double, accessibility: String) -> some View {
        Button { proxy.zoom(delta) } label: {
            Image(systemName: symbol)
                .font(.system(size: 13, weight: .semibold))
                .frame(width: 32, height: 32)
                .background(.regularMaterial, in: Circle())
                .overlay { Circle().stroke(.secondary.opacity(0.3), lineWidth: 0.8) }
        }.buttonStyle(.plain)
            .accessibilityLabel(accessibility)
    }
}

/// 「大地图」选点弹层（桌面 900×620 sheet → 移动端近全屏 sheet）。
struct MapPickerSheet: View {
    @EnvironmentObject private var store: Store
    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text("地图选点").font(.headline)
                Spacer()
                Button("完成") { store.showMapPicker = false }
            }.padding(16)
            ForecastMap(store: store)
        }
    }
}
