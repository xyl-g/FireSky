import L from "leaflet";
import { gridColor } from "./map-grid";
import type { GridCell } from "./map-grid";

/** One viewport canvas for all cells, following desktop/assets/map.html:608-697. */
export class ForecastGridLayer extends L.Layer {
  private owner: L.Map | null = null;
  private canvas: HTMLCanvasElement | null = null;
  private cells: readonly GridCell[] = [];
  private spacing = 0.25;
  painted = 0;

  constructor(private onError: () => void) { super(); }

  setData(cells: readonly GridCell[], spacing: number): void {
    this.cells = cells;
    this.spacing = spacing;
    this.draw();
  }

  onAdd(owner: L.Map): this {
    this.owner = owner;
    // Keep the same canvas across setGrid / clear / setGrid cycles.
    this.canvas ??= L.DomUtil.create("canvas", "firesky-forecast-grid");
    this.canvas.style.pointerEvents = "none";
    this.canvas.setAttribute("aria-hidden", "true");
    owner.getPanes().overlayPane.appendChild(this.canvas);
    owner.on("move zoom resize viewreset", this.draw, this);
    this.draw();
    return this;
  }

  onRemove(owner: L.Map): this {
    owner.off("move zoom resize viewreset", this.draw, this);
    this.canvas?.remove();
    this.owner = null;
    return this;
  }

  draw(): void {
    const map = this.owner, canvas = this.canvas;
    if (!map || !canvas) return;
    const size = map.getSize(), ratio = window.devicePixelRatio || 1;
    canvas.width = Math.round(size.x * ratio);
    canvas.height = Math.round(size.y * ratio);
    canvas.style.width = `${size.x}px`;
    canvas.style.height = `${size.y}px`;
    L.DomUtil.setPosition(canvas, map.containerPointToLayerPoint([0, 0]));
    const ctx = canvas.getContext("2d");
    if (!ctx) { this.onError(); return; }
    ctx.scale(ratio, ratio);
    const bounds = map.getBounds(), half = this.spacing / 2;
    this.painted = 0;
    for (const cell of this.cells) {
      const [lat, lon, value] = cell;
      if (lat + half < bounds.getSouth() || lat - half > bounds.getNorth() ||
          lon + half < bounds.getWest() || lon - half > bounds.getEast()) continue;
      const mercatorLat = (value: number) => Math.max(-85.05, Math.min(85.05, value));
      const nw = map.latLngToContainerPoint([mercatorLat(lat + half), lon - half]);
      const se = map.latLngToContainerPoint([mercatorLat(lat - half), lon + half]);
      const width = Math.max(1, se.x - nw.x), height = Math.max(1, se.y - nw.y);
      ctx.globalAlpha = value === null ? 0.25 : 0.65;
      ctx.fillStyle = gridColor(value);
      ctx.fillRect(nw.x, nw.y, width, height);
      if (width >= 5 && height >= 5) {
        ctx.globalAlpha = 0.5;
        ctx.strokeStyle = value === null ? "#f1f3f5" : "#ffffff";
        ctx.lineWidth = 0.5;
        ctx.strokeRect(nw.x, nw.y, width, height);
        if (value === null) {
          ctx.beginPath(); ctx.moveTo(nw.x, nw.y); ctx.lineTo(se.x, se.y); ctx.stroke();
        }
      }
      this.painted += 1;
    }
    ctx.globalAlpha = 1;
  }
}
