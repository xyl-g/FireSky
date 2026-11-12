"""Export native-grid CAMS AOD bands for the Rust earth_engine_aod adapter."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import logging
import math
import os
from pathlib import Path
import re
import sys

COLLECTION = "ECMWF/CAMS/NRT"
BANDS = [f"total_aerosol_optical_depth_at_{nm}nm_surface" for nm in (469, 550, 670)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=os.environ.get("GOOGLE_CLOUD_PROJECT"))
    parser.add_argument("--target-utc", required=True)
    parser.add_argument("--area", nargs=4, type=float, required=True, metavar=("N", "W", "S", "E"))
    parser.add_argument("--point", nargs=2, type=float, metavar=("LAT", "LON"))
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if not args.project:
        parser.error("--project or GOOGLE_CLOUD_PROJECT is required")
    target = datetime.fromisoformat(args.target_utc.replace("Z", "+00:00"))
    if target.utcoffset() != timedelta(0):
        parser.error("--target-utc must explicitly use UTC")
    n, w, s, e = args.area
    if not all(map(math.isfinite, args.area)) or not (-90 <= s < n <= 90 and -180 <= w < e <= 180):
        parser.error("--area must be an ordinary north/west/south/east rectangle")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any((output / name).exists() for name in ("aod.npy", "aod-input.json")):
        parser.error("output already contains an export; choose another directory")
    import ee
    import numpy as np
    import requests

    logging.disable(logging.CRITICAL)
    ee.Initialize(project=args.project)
    target_ms = int(target.timestamp() * 1000)
    candidates = (ee.ImageCollection(COLLECTION)
        .filter(ee.Filter.eq("system:time_start", target_ms))
        .filter(ee.Filter.gte("model_forecast_hour", 0)))
    count = candidates.size().getInfo()
    if count == 0:
        raise ValueError("no CAMS image exists at the exact requested valid time")
    image = ee.Image(candidates.sort("model_forecast_hour", True).first()).select(BANDS)
    info = image.toDictionary(["system:time_start", "model_forecast_hour"]).getInfo()
    hour = float(info["model_forecast_hour"])
    if not hour.is_integer() or not 0 <= hour <= 120 or info["system:time_start"] != target_ms:
        raise ValueError("selected image violates the run/valid-time contract")
    run = target - timedelta(hours=hour)
    projection = image.select(BANDS[0]).projection()
    grid = projection.getInfo()
    grid["crs"] = grid.get("crs") or grid.get("wkt")
    if not grid["crs"]:
        raise ValueError("Earth Engine projection has neither a CRS identifier nor WKT")
    region = ee.Geometry.Rectangle([w, s, e, n], proj="EPSG:4326", geodesic=False)
    mask = image.mask().reduce(ee.Reducer.min()).rename("valid_mask").unmask(0)
    coordinates = ee.Image.pixelLonLat().reproject(projection)
    export = image.unmask(-9999).addBands(coordinates).addBands(mask)
    image_id = image.id().getInfo()
    print(json.dumps({"event":"selected","image_id":image_id,"valid_time_utc":target.isoformat(),
        "run_time_utc":run.isoformat(),"forecast_hour":int(hour),"candidate_images":count}), flush=True)
    url = export.getDownloadURL({"region":region,"crs":grid["crs"],
        "crs_transform":grid["transform"],"format":"NPY"})
    with requests.get(url, timeout=(15, 120), stream=True) as response:
        response.raise_for_status()
        chunks = []
        size = 0
        for chunk in response.iter_content(1024 * 1024):
            size += len(chunk)
            if size > 32 * 1024 * 1024:
                raise ValueError("Earth Engine export exceeds 32 MiB")
            chunks.append(chunk)
    raw = b"".join(chunks)
    array = np.load(io.BytesIO(raw), allow_pickle=False)
    if array.ndim != 2 or not array.size or array.size > 100000:
        raise ValueError("export must be a nonempty 2D grid of at most 100000 cells")
    if not set(BANDS + ["longitude", "latitude", "valid_mask"]).issubset(array.dtype.names or ()):
        raise ValueError("export is missing required bands or coordinates")
    cells = []
    for row in array.ravel():
        cell = {"lat":float(row["latitude"]),"lon":float(row["longitude"])}
        valid = float(row["valid_mask"]) > 0
        for nm, band in zip((469, 550, 670), BANDS):
            value = float(row[band])
            cell[f"aod{nm}"] = value if valid and math.isfinite(value) and value >= 0 else None
        cells.append(cell)
    point = None
    if args.point:
        lat, lon = args.point
        if not (s <= lat <= n and w <= lon <= e):
            parser.error("--point must lie within --area")
        values = image.reduceRegion(ee.Reducer.first(), ee.Geometry.Point([lon,lat]),
            crs=grid["crs"], crsTransform=grid["transform"], maxPixels=100).getInfo()
        point = {"lat":lat,"lon":lon,**{f"aod{nm}":values.get(band) for nm,band in zip((469,550,670),BANDS)}}
    document = {"schema":"gee-cams-aod-v1","collection":COLLECTION,"image_id":image_id,
        "valid_time_utc":target.isoformat(),"run_time_utc":run.isoformat(),"forecast_hour":int(hour),
        "area":args.area,"crs":grid["crs"],"crs_transform":grid["transform"],"shape":list(array.shape),
        "bands":BANDS,"raw_npy_identity":{"bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest()},
        "cells":cells,"point":point}
    (output / "aod.npy").write_bytes(raw)
    text = json.dumps(document,indent=2,allow_nan=False)
    temporary = output / "aod-input.tmp"
    temporary.write_text(text)
    temporary.replace(output / "aod-input.json")
    print(json.dumps({"event":"download_complete","bytes":len(raw),"cells":len(cells),
        "point":point,"input_path":str(output / "aod-input.json")}), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        message = re.sub(r"https?://[^\s\"<>]+", "[URL]", str(exc))
        print(f"{type(exc).__name__}: {message}", file=sys.stderr)
        raise SystemExit(1)
