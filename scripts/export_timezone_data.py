"""Prepare versioned native timezone resources; not a runtime Python adapter.

Run in an isolated environment with timezonefinder==9.2.0. Retains the complete
dataset, overlap candidate ordering, holes, integer coordinates and licenses.
"""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import tempfile

import numpy as np
from timezonefinder import TimezoneFinder
from timezonefinder.shortcut_index import ROOT_COLUMNS, BLOCK_SIDE, SHORTCUT_LAYOUT_VERSION


def identity(path):
    with path.open("rb") as stream:
        return dict(bytes=path.stat().st_size, sha256=hashlib.file_digest(stream, "sha256").hexdigest())


def export(destination):
    if importlib.metadata.version("timezonefinder") != "9.2.0" or SHORTCUT_LAYOUT_VERSION != 3:
        raise RuntimeError("export contract requires timezonefinder 9.2.0 / shortcut layout 3")
    destination = destination.resolve()
    if destination.exists():
        raise RuntimeError("destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    finder = TimezoneFinder(in_memory=True)
    with tempfile.TemporaryDirectory(prefix=".firesky-timezones-", dir=destination.parent) as staging:
        root = Path(staging)/"dataset"
        root.mkdir()
        arrays = {}

        def save(name, value, dtype):
            value = np.ascontiguousarray(value, dtype=dtype)
            path = root/f"{name}.npy"
            np.save(path, value, allow_pickle=False)
            arrays[name] = dict(file=path.name, dtype=value.dtype.str, shape=list(value.shape), **identity(path))

        coordinates, ring_offsets, polygon_offsets = [], [0], [0]
        for boundary in range(len(finder.zone_ids)):
            rings = [finder.coords_of(boundary), *finder._holes_of_poly(boundary)]
            for ring in rings:
                coordinates.append(ring.T)
                ring_offsets.append(ring_offsets[-1]+ring.shape[1])
            polygon_offsets.append(len(ring_offsets)-1)
        save("coordinates", np.concatenate(coordinates), "<i4")
        save("ring_offsets", ring_offsets, "<u4")
        save("polygon_offsets", polygon_offsets, "<u4")
        save("zones", finder.zone_ids, "<u4")
        for name in ("table", "blocks"):
            save(name, getattr(finder.shortcuts, name), "<i4")
        for name in ("starts", "ends", "payload", "last_change"):
            save(name, getattr(finder.shortcuts, name), "<u4")
        licenses = {}
        for package, suffix, filename in (
            ("timezonefinder", "/LICENSE", "CODE_LICENSE"),
            ("timezonefinder-data", "/DATA_LICENSE", "DATA_LICENSE"),
        ):
            distribution = importlib.metadata.distribution(package)
            source = next(distribution.locate_file(f) for f in distribution.files if str(f).endswith(suffix))
            shutil.copyfile(source, root/filename)
            licenses[filename] = identity(root/filename)
        manifest = dict(contract="firesky-timezone-source-v1", exporter="timezonefinder-9.2.0",
                        data_package_version=importlib.metadata.version("timezonefinder-data"),
                        data_version=finder.data_version, scale=10_000_000, root_columns=ROOT_COLUMNS,
                        block_side=BLOCK_SIDE, timezone_names=finder.timezone_names,
                        arrays=arrays, licenses=licenses)
        (root/"manifest.json").write_text(json.dumps(manifest, ensure_ascii=True, allow_nan=False))
        root.rename(destination)
    return destination/"manifest.json"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(export(args.output))
