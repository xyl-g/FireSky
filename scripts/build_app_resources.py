"""Build the checked-in application-resource manifest.

The manifest covers the immutable city catalog and timezone dataset shipped to
Tauri and Docker. The timezone manifest also carries per-array identities; this
top-level manifest makes the complete package inventory auditable in one place.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


SCHEMA = "firesky-app-resources-v1"
VERSION = "2026.11.11"


def identity(path: Path) -> dict[str, object]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(block)
            digest.update(block)
    return {"bytes": size, "sha256": digest.hexdigest()}


def manifest_for(root: Path) -> dict[str, object]:
    root = root.resolve()
    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "RESOURCE-MANIFEST.json":
            continue
        relative = path.relative_to(root).as_posix()
        files.append({"path": relative, **identity(path)})

    city = root / "cities/china-city-catalog.json"
    timezone = root / "timezones/manifest.json"
    if not city.is_file() or not timezone.is_file():
        raise RuntimeError("city catalog and timezone manifest are required")

    catalog = json.loads(city.read_text(encoding="utf-8"))
    if catalog.get("city_count") != 695 or len(catalog.get("cities", [])) != 695:
        raise RuntimeError("city catalog must contain exactly 695 cities")
    timezone_manifest = json.loads(timezone.read_text(encoding="utf-8"))

    return {
        "schema": SCHEMA,
        "version": VERSION,
        "read_only": True,
        "user_data_is_external": True,
        "resources": {
            "city_catalog": {
                "path": "cities/china-city-catalog.json",
                **identity(city),
                "schema_version": catalog.get("schema_version"),
                "city_count": catalog.get("city_count"),
                "source": catalog.get("source"),
                "authoritative_source": catalog.get("authoritative_source"),
                "license_notice": "cities/CITY-CATALOG-NOTICE.md",
                "license_status": "upstream-terms-not-specified",
            },
            "timezone_source": {
                "path": "timezones/manifest.json",
                **identity(timezone),
                "contract": timezone_manifest.get("contract"),
                "exporter": timezone_manifest.get("exporter"),
                "data_version": timezone_manifest.get("data_version"),
                "data_package_version": timezone_manifest.get("data_package_version"),
                "licenses": ["timezones/CODE_LICENSE", "timezones/DATA_LICENSE"],
            },
        },
        "files": files,
    }


def build(root: Path, *, check: bool = False) -> Path:
    root = root.resolve()
    output = root / "RESOURCE-MANIFEST.json"
    manifest = manifest_for(root)
    expected = json.dumps(manifest, ensure_ascii=True, indent=2) + "\n"
    if check:
        if not output.is_file():
            raise RuntimeError(f"resource manifest missing: {output}")
        actual = output.read_text(encoding="utf-8")
        if actual != expected:
            raise RuntimeError(
                "resource manifest is stale; run "
                f"python3 scripts/build_app_resources.py --root {root}"
            )
        return output
    output.write_text(
        expected,
        encoding="utf-8",
    )
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the checked-in manifest without rewriting it",
    )
    args = parser.parse_args()
    print(build(args.root, check=args.check))
