import L from "leaflet";
import "leaflet-draw/dist/leaflet.draw.css";

let loading: Promise<void> | null = null;

export function loadRegionDrawing(): Promise<void> {
  // Leaflet.draw's published UMD build expects the same global Leaflet object.
  return loading ??= (async () => {
    Object.assign(window, { L });
    await import("leaflet-draw");
    L.drawLocal.draw.handlers.rectangle.tooltip.start = "绘制范围";
    L.drawLocal.draw.handlers.simpleshape.tooltip.end = "范围预览";
  })().catch((error: unknown) => {
    loading = null;
    throw error;
  });
}
