import AppKit
import MapKit
import SwiftUI

struct ForecastMap: NSViewRepresentable {
    @ObservedObject var store: Store
    var satellite = false
    var mini = false
    var model: String?

    func makeCoordinator() -> Coordinator { Coordinator(store) }

    func makeNSView(context: Context) -> WheelZoomMapView {
        let map = WheelZoomMapView(frame: NSRect(x: 0, y: 0, width: 600, height: 400))
        map.isZoomEnabled = true
        map.isScrollEnabled = true
        map.isRotateEnabled = false
        map.isPitchEnabled = false
        if store.page == .regional {
            map.cameraZoomRange = MKMapView.CameraZoomRange(minCenterCoordinateDistance: 5_000,
                                                           maxCenterCoordinateDistance: 30_000_000)
        }
        map.showsCompass = !mini
        map.showsScale = !mini
        map.pointOfInterestFilter = .excludingAll
        if satellite {
            map.preferredConfiguration = MKHybridMapConfiguration()
        } else {
            let configuration = MKStandardMapConfiguration(elevationStyle: .flat, emphasisStyle: .muted)
            configuration.pointOfInterestFilter = .excludingAll
            map.preferredConfiguration = configuration
        }
        if store.page == .regional {
            let initialAreas = store.previewAreas.isEmpty ? [[73.0, 18.0, 136.0, 54.0]] : store.previewAreas
            if let target = store.regionalCamera, target.distance.isFinite, target.distance >= 100 {
                map.setCamera(MKMapCamera(lookingAtCenter: CLLocationCoordinate2D(latitude: target.lat, longitude: target.lon),
                                          fromDistance: target.distance, pitch: 0, heading: 0), animated: false)
            } else if let rect = RegionalMapLayers.fitMapRect(areas: initialAreas) {
                map.setVisibleMapRect(rect, edgePadding: NSEdgeInsets(top: 24, left: 24, bottom: 24, right: 24), animated: false)
            } else if let region = RegionalMapLayers.fitRegion(areas: initialAreas, paddingDegrees: 1.0) {
                map.setRegion(region, animated: false)
            }
        } else {
            map.setRegion(MKCoordinateRegion(
                center: CLLocationCoordinate2D(latitude: store.lat, longitude: store.lon),
                span: MKCoordinateSpan(latitudeDelta: mini ? 0.025 : 3, longitudeDelta: mini ? 0.035 : 4)
            ), animated: false)
        }
        let coordinator = context.coordinator
        map.onLayoutReady = { [weak coordinator, weak map] in
            guard let coordinator, let map,
                  coordinator.store.page == .regional,
                  let pending = coordinator.pendingFitAreas,
                  map.window != nil, map.bounds.width > 10, map.bounds.height > 10 else { return }
            coordinator.applyFit(map, areas: pending)
            coordinator.pendingFitAreas = nil
            coordinator.cameraReady = true
            coordinator.publishCamera(map)
        }
        let click = NSClickGestureRecognizer(target: context.coordinator, action: #selector(Coordinator.click(_:)))
        map.addGestureRecognizer(click)
        map.drawing.onCommit = { [weak store] start, end in
            guard let a = GridCoordinate(lat: start.latitude, lon: start.longitude),
                  let b = GridCoordinate(lat: end.latitude, lon: end.longitude),
                  let bounds = GridBounds.drawn(from: a, to: b) else { return }
            store?.applyDrawnRegion(bounds)
        }
        map.drawing.onCancel = { [weak store] in store?.editingRegionOnMap = false }
        map.delegate = context.coordinator
        return map
    }

    func updateNSView(_ map: WheelZoomMapView, context: Context) {
        let coordinator = context.coordinator
        coordinator.model = model ?? store.selectedModel
        let wasHidden = map.drawing.isHidden
        map.drawing.isHidden = store.page != .regional || !store.editingRegionOnMap || store.busy
        if !map.drawing.isHidden {
            map.addSubview(map.drawing, positioned: .above, relativeTo: nil)
            if wasHidden {
                DispatchQueue.main.async { [weak map] in
                    guard let map, !map.drawing.isHidden else { return }
                    map.window?.makeFirstResponder(map.drawing)
                }
            }
        }
        let areas = store.previewAreas.filter {
            guard let bounds = GridBounds($0) else { return false }
            return bounds.longitudeSpan > 0 && bounds.north > bounds.south
        }
        if store.page == .regional {
            coordinator.updateRegional(map, areas: areas)
            return
        }
        let signature = "\(store.page)-\(store.lat)-\(store.lon)"
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

    static func dismantleNSView(_ map: WheelZoomMapView, coordinator: Coordinator) {
        coordinator.renderTask?.cancel()
        coordinator.cameraReady = false
        map.delegate = nil
    }

    @MainActor final class Coordinator: NSObject, MKMapViewDelegate {
        let store: Store
        var signature = ""
        var lastLat = Double.nan
        var lastLon = Double.nan
        var lastBounds = ""
        var model = "gfs"
        var styles: [ObjectIdentifier: RegionalMapLayers.Style] = [:]
        var renderTask: Task<Void, Never>?
        var fitVersion = -1
        var focusVersion = 0
        var selection: GridCoordinate?
        var selectionOverlay: MKPolygon?
        var cameraReady = false
        var pendingFitAreas: [[Double]]?
        init(_ store: Store) { self.store = store }

        @objc func click(_ gesture: NSClickGestureRecognizer) {
            guard let map = gesture.view as? MKMapView, !store.busy else { return }
            let coordinate = map.convert(gesture.location(in: map), toCoordinateFrom: map)
            if store.page == .regional {
                guard !store.editingRegionOnMap else { return }
                if let hit = store.regionalForecast.model(model).hit(lat: coordinate.latitude, lon: coordinate.longitude) {
                    store.selectedRegionalCoordinate = hit
                    store.showRegionalInspector = true
                }
            } else {
                store.selectPoint(lat: coordinate.latitude, lon: coordinate.longitude)
            }
        }

        func applyFit(_ map: MKMapView, areas: [[Double]]) {
            if map.bounds.width > 10, map.bounds.height > 10,
               let rect = RegionalMapLayers.fitMapRect(areas: areas) {
                map.setVisibleMapRect(rect, edgePadding: NSEdgeInsets(top: 24, left: 24, bottom: 24, right: 24), animated: false)
            } else if let region = RegionalMapLayers.fitRegion(areas: areas, paddingDegrees: 1.0) {
                map.setRegion(region, animated: false)
            }
        }

        func updateRegional(_ map: MKMapView, areas: [[Double]]) {
            cameraReady = false
            let effectiveAreas = areas.isEmpty ? [[73.0, 18.0, 136.0, 54.0]] : areas
            let bounds = "\(effectiveAreas)"
            let first = lastBounds.isEmpty
            if lastBounds != bounds || fitVersion != store.regionalFitVersion {
                let shouldFit = !first || store.regionalCamera == nil || fitVersion != store.regionalFitVersion
                lastBounds = bounds
                fitVersion = store.regionalFitVersion
                if shouldFit {
                    applyFit(map, areas: effectiveAreas)
                    if map.window != nil && map.bounds.width > 10 && map.bounds.height > 10 {
                        pendingFitAreas = nil
                        cameraReady = true
                        publishCamera(map)
                    } else {
                        pendingFitAreas = effectiveAreas
                        cameraReady = false
                    }
                } else {
                    applyCamera(map)
                    cameraReady = true
                }
            } else if let pending = pendingFitAreas,
                      map.window != nil, map.bounds.width > 10, map.bounds.height > 10 {
                applyFit(map, areas: pending)
                pendingFitAreas = nil
                cameraReady = true
                publishCamera(map)
            } else {
                applyCamera(map)
                cameraReady = true
            }
            if focusVersion != store.regionalFocusVersion {
                focusVersion = store.regionalFocusVersion
                if let coordinate = store.selectedRegionalCoordinate {
                    map.setCamera(MKMapCamera(lookingAtCenter: CLLocationCoordinate2D(latitude: coordinate.lat, longitude: coordinate.lon),
                                              fromDistance: min(map.camera.centerCoordinateDistance, 350_000), pitch: 0, heading: 0), animated: false)
                    publishCamera(map)
                }
            }
            if selection != store.selectedRegionalCoordinate {
                selection = store.selectedRegionalCoordinate
                updateSelection(map)
            }
            if pendingFitAreas == nil {
                cameraReady = true
            }
            let showGrid = store.showRegionalGrid
            let showSamples = store.showRegionalSamples
            let showSmooth = store.showRegionalSmooth
            let next = "\(bounds)-\(model)-\(store.resultVersion)-\(showGrid)-\(showSamples)-\(showSmooth)"
            guard signature != next else { return }
            signature = next
            renderTask?.cancel()
            map.removeOverlays(map.overlays)
            styles = [:]
            selectionOverlay = nil
            let cells = store.regionalForecast.model(model).cells
            renderTask = Task { [weak self, weak map] in
                let worker = Task.detached(priority: .userInitiated) {
                    try RegionalMapLayers.build(areas: areas, cells: cells, showGrid: showGrid, smooth: showSmooth)
                }
                let layers = await withTaskCancellationHandler {
                    try? await worker.value
                } onCancel: { worker.cancel() }
                guard !Task.isCancelled, let self, let map, let layers, self.signature == next else { return }
                map.removeOverlays(map.overlays)
                self.styles = layers.styles
                map.addOverlays(layers.overlays, level: .aboveRoads)
                self.selectionOverlay = nil
                self.updateSelection(map)
                self.updateSampleAnnotations(map, cells: cells, show: showSamples)
            }
        }

        /// 采样格点标注（regional-6）：与网格线开关并存，复用 points[]，上限 2000 截断。
        private func updateSampleAnnotations(_ map: MKMapView, cells: [RegionalCell], show: Bool) {
            map.removeAnnotations(map.annotations)
            guard show, !cells.isEmpty else { return }
            let step = max(1, (cells.count + 1999) / 2000)
            let markers = cells.enumerated().compactMap { offset, cell in
                offset % step == 0 ? RegionalSampleAnnotation(cell: cell) : nil
            }
            map.addAnnotations(markers)
        }

        @objc private func sampleClicked(_ gesture: NSClickGestureRecognizer) {
            guard let view = gesture.view as? MKAnnotationView,
                  let sample = view.annotation as? RegionalSampleAnnotation else { return }
            store.focusRegionalCell(sample.cell.coordinate)
        }

        private func updateSelection(_ map: MKMapView) {
            if let selectionOverlay { map.removeOverlay(selectionOverlay) }
            selectionOverlay = nil
            guard let selection else { return }
            let polygon = RegionalMapLayers.polygon(west: selection.lon - 0.125, south: selection.lat - 0.125,
                                                    east: selection.lon + 0.125, north: selection.lat + 0.125)
            selectionOverlay = polygon
            map.addOverlay(polygon, level: .aboveLabels)
        }

        private func camera(_ map: MKMapView) -> RegionalCamera {
            RegionalCamera(lat: map.centerCoordinate.latitude, lon: map.centerCoordinate.longitude,
                           distance: map.camera.centerCoordinateDistance)
        }
        private func applyCamera(_ map: MKMapView) {
            guard let target = store.regionalCamera, target.distance.isFinite, target.distance >= 100,
                  !target.matches(camera(map)) else { return }
            map.setCamera(MKMapCamera(lookingAtCenter: CLLocationCoordinate2D(latitude: target.lat, longitude: target.lon),
                                     fromDistance: target.distance, pitch: 0, heading: 0), animated: false)
        }
        func publishCamera(_ map: MKMapView) {
            let value = camera(map)
            guard value.distance.isFinite, value.distance >= 100 else { return }
            Task { @MainActor [weak self] in
                guard let self, self.cameraReady, self.store.page == .regional,
                      self.store.regionalCamera?.matches(value) != true else { return }
                self.store.regionalCamera = value
            }
        }
        func mapView(_ mapView: MKMapView, regionDidChangeAnimated animated: Bool) {
            if store.page == .regional, cameraReady, mapView.window != nil { publishCamera(mapView) }
        }

        func mapView(_ mapView: MKMapView, rendererFor overlay: MKOverlay) -> MKOverlayRenderer {
            if let smooth = overlay as? RegionalSmoothOverlay {
                return RegionalSmoothRenderer(overlay: smooth)
            }
            if let line = overlay as? MKMultiPolyline {
                let renderer = RegionalGridRenderer(multiPolyline: line)
                renderer.strokeColor = .labelColor.withAlphaComponent(0.16)
                renderer.lineWidth = 0.5
                return renderer
            }
            if let polygon = overlay as? MKPolygon {
                let renderer = MKPolygonRenderer(polygon: polygon)
                renderer.fillColor = .clear
                renderer.strokeColor = .systemOrange
                renderer.lineWidth = 1.5
                return renderer
            }
            guard let polygons = overlay as? MKMultiPolygon else { return MKOverlayRenderer(overlay: overlay) }
            let renderer = RegionalFieldRenderer(multiPolygon: polygons)
            switch styles[ObjectIdentifier(overlay)] {
            case .value(let fill):
                renderer.fillsCells = true
                renderer.fillColor = fill.color
                renderer.strokeColor = .clear
                renderer.lineWidth = 0
            case .missing:
                renderer.fillColor = .clear
                renderer.strokeColor = NSColor(white: 0.5, alpha: 0.35)
                renderer.lineDashPattern = [2, 2]
                renderer.lineWidth = 0.7
            default:
                renderer.fillColor = .clear
                renderer.strokeColor = NSColor(white: 0.5, alpha: 0.4)
                renderer.lineWidth = 0.8
            }
            return renderer
        }

        func mapView(_ mapView: MKMapView, viewFor annotation: MKAnnotation) -> MKAnnotationView? {
            guard let sample = annotation as? RegionalSampleAnnotation else { return nil }
            let identifier = "regional-sample"
            let view = (mapView.dequeueReusableAnnotationView(withIdentifier: identifier) as? RegionalSampleMarkerView)
                ?? RegionalSampleMarkerView(annotation: sample, reuseIdentifier: identifier)
            view.annotation = sample
            view.bounds = NSRect(x: 0, y: 0, width: 7, height: 7)
            view.toolTip = sample.tooltip
            if view.gestureRecognizers.isEmpty {
                view.addGestureRecognizer(NSClickGestureRecognizer(target: self, action: #selector(sampleClicked(_:))))
            }
            return view
        }
    }
}

final class WheelZoomMapView: MKMapView {
    private var wheelMonitor: Any?
    let drawing = RegionDrawingView()
    var onLayoutReady: (() -> Void)?

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        drawing.map = self
        drawing.isHidden = true
        drawing.frame = bounds
        drawing.autoresizingMask = [.width, .height]
        addSubview(drawing)
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        if let wheelMonitor { NSEvent.removeMonitor(wheelMonitor) }
        wheelMonitor = nil
        guard window != nil else { return }
        DispatchQueue.main.async { [weak self] in
            self?.onLayoutReady?()
        }
        // MapKit's internal views can consume wheel events before the outer map.
        wheelMonitor = NSEvent.addLocalMonitorForEvents(matching: .scrollWheel) { [weak self] event in
            guard let self, let window = self.window, event.window === window,
                  window.attachedSheet == nil, self.isZoomEnabled,
                  !self.isHiddenOrHasHiddenAncestor else { return event }
            let location = self.convert(event.locationInWindow, from: nil)
            guard self.visibleRect.contains(location),
                  let content = window.contentView,
                  let hit = content.hitTest(content.superview?.convert(event.locationInWindow, from: nil)
                                            ?? event.locationInWindow),
                  hit === self || hit.isDescendant(of: self) else { return event }
            if event.scrollingDeltaY != 0 { self.zoom(event, at: location) }
            // Consume vertical and horizontal deltas here so the containing rail stays put.
            return nil
        }
    }

    override func layout() {
        super.layout()
        if window != nil && bounds.width > 10 && bounds.height > 10 {
            onLayoutReady?()
        }
    }

    deinit {
        if let wheelMonitor { NSEvent.removeMonitor(wheelMonitor) }
    }

    private func zoom(_ event: NSEvent, at location: NSPoint) {
        let current = visibleMapRect
        let anchor = MKMapPoint(convert(location, toCoordinateFrom: self))
        guard let rect = MapZoom.rect(
            CGRect(x: current.origin.x, y: current.origin.y, width: current.width, height: current.height),
            anchor: CGPoint(x: anchor.x, y: anchor.y),
            delta: Double(event.scrollingDeltaY), precise: event.hasPreciseScrollingDeltas,
            worldSize: MKMapRect.world.width
        ) else { return }
        setVisibleMapRect(MKMapRect(x: rect.minX, y: rect.minY, width: rect.width, height: rect.height), animated: false)
    }
}

final class RegionDrawingView: NSView {
    weak var map: MKMapView?
    var onCommit: ((CLLocationCoordinate2D, CLLocationCoordinate2D) -> Void)?
    var onCancel: (() -> Void)?
    private var start: NSPoint?
    private var end: NSPoint?
    override var acceptsFirstResponder: Bool { true }
    override func resetCursorRects() { addCursorRect(bounds, cursor: .crosshair) }
    override func mouseDown(with event: NSEvent) {
        window?.makeFirstResponder(self)
        start = convert(event.locationInWindow, from: nil)
        end = start
        needsDisplay = true
    }
    override func mouseDragged(with event: NSEvent) {
        end = convert(event.locationInWindow, from: nil)
        needsDisplay = true
    }
    override func mouseUp(with event: NSEvent) {
        defer { start = nil; end = nil; needsDisplay = true; window?.makeFirstResponder(map) }
        guard let start, let map else { return }
        let end = convert(event.locationInWindow, from: nil)
        guard hypot(end.x - start.x, end.y - start.y) >= 5 else { return }
        onCommit?(map.convert(start, toCoordinateFrom: self), map.convert(end, toCoordinateFrom: self))
    }
    override func cancelOperation(_ sender: Any?) {
        start = nil; end = nil; needsDisplay = true
        onCancel?()
        window?.makeFirstResponder(map)
    }
    override func draw(_ dirtyRect: NSRect) {
        guard let start, let end else { return }
        let path = NSBezierPath(rect: NSRect(x: min(start.x, end.x), y: min(start.y, end.y),
                                             width: abs(end.x - start.x), height: abs(end.y - start.y)))
        NSColor.systemTeal.withAlphaComponent(0.16).setFill()
        path.fill()
        NSColor.systemTeal.setStroke()
        path.lineWidth = 2
        path.stroke()
    }
}
