"""Generate an AltStore Source v2 JSON feed for FireSky iOS releases."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile


SOURCE_NAME = "FireSky"
SOURCE_IDENTIFIER = "cn.firesky.source"
SOURCE_SUBTITLE = "火烧云物理预测（GFS / ECMWF / CAMS + 向日葵实况）"
SOURCE_URL = "https://github.com/xyl-g/FireSky/releases/latest/download/altstore-source.json"

APP_NAME = "FireSky"
APP_BUNDLE_IDENTIFIER = "cn.firesky.mobile"
APP_DEVELOPER_NAME = "xyl-g"
APP_ICON_URL = (
    "https://raw.githubusercontent.com/xyl-g/FireSky/main/"
    "apps/mobile-ios/FireSkyMobile/Assets.xcassets/AppIcon.appiconset/AppIcon.png"
)
APP_TINT_COLOR = "E06D3A"
APP_CATEGORY = "weather"
APP_MIN_OS_VERSION = "16.0"


def _normalize_iso8601_utc(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_altstore_source(
    *,
    version: str,
    download_url: str,
    size: int,
    date: str | None = None,
    notes: str | None = None,
) -> dict[str, object]:
    if not version.strip():
        raise ValueError("version must not be empty")
    if not download_url.strip():
        raise ValueError("download_url must not be empty")
    if size < 0:
        raise ValueError("size must be non-negative")

    localized_description = notes.strip() if notes and notes.strip() else f"FireSky {version} iOS release"
    iso_date = _normalize_iso8601_utc(date)

    return {
        "name": SOURCE_NAME,
        "identifier": SOURCE_IDENTIFIER,
        "subtitle": SOURCE_SUBTITLE,
        "sourceURL": SOURCE_URL,
        "apps": [
            {
                "name": APP_NAME,
                "bundleIdentifier": APP_BUNDLE_IDENTIFIER,
                "developerName": APP_DEVELOPER_NAME,
                "iconURL": APP_ICON_URL,
                "tintColor": APP_TINT_COLOR,
                "category": APP_CATEGORY,
                "versions": [
                    {
                        "version": version,
                        "date": iso_date,
                        "localizedDescription": localized_description,
                        "downloadURL": download_url,
                        "size": int(size),
                        "minOSVersion": APP_MIN_OS_VERSION,
                    }
                ],
            }
        ],
    }


def write_altstore_source(
    *,
    out_path: Path,
    version: str,
    download_url: str,
    ipa_path: Path | None = None,
    size_override: int | None = None,
    date: str | None = None,
    notes: str | None = None,
) -> Path:
    if size_override is not None:
        resolved_size = size_override
    elif ipa_path is not None:
        if not ipa_path.is_file():
            raise FileNotFoundError(f"IPA file not found: {ipa_path}")
        resolved_size = ipa_path.stat().st_size
    else:
        raise ValueError("either --ipa or --size must be provided")

    payload = build_altstore_source(
        version=version,
        download_url=download_url,
        size=resolved_size,
        date=date,
        notes=notes,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out_path


def run_self_test() -> None:
    dummy_bytes = b"PK\x03\x04" + b"firesky-altstore-selftest" * 64
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        dummy_ipa = tmp / "FireSky-2.0.0-ios-arm64.ipa"
        dummy_ipa.write_bytes(dummy_bytes)
        out_json = tmp / "altstore-source.json"

        write_altstore_source(
            out_path=out_json,
            version="2.0.0",
            download_url="https://github.com/xyl-g/FireSky/releases/download/v2.0.0/FireSky-2.0.0-ios-arm64.ipa",
            ipa_path=dummy_ipa,
            date="2026-11-26T00:00:00Z",
            notes="Test release notes",
        )

        data = json.loads(out_json.read_text(encoding="utf-8"))
        assert data["name"] == "FireSky"
        assert data["identifier"] == "cn.firesky.source"
        assert data["subtitle"] == SOURCE_SUBTITLE
        assert data["sourceURL"] == SOURCE_URL
        assert isinstance(data["apps"], list) and len(data["apps"]) == 1

        app = data["apps"][0]
        assert app["name"] == "FireSky"
        assert app["bundleIdentifier"] == "cn.firesky.mobile"
        assert app["developerName"] == "xyl-g"
        assert app["iconURL"] == APP_ICON_URL
        assert app["tintColor"] == "E06D3A"
        assert app["category"] == "weather"
        assert isinstance(app["versions"], list) and len(app["versions"]) == 1

        ver = app["versions"][0]
        assert ver["version"] == "2.0.0"
        assert ver["date"] == "2026-11-26T00:00:00Z"
        assert ver["localizedDescription"] == "Test release notes"
        assert ver["downloadURL"] == "https://github.com/xyl-g/FireSky/releases/download/v2.0.0/FireSky-2.0.0-ios-arm64.ipa"
        assert isinstance(ver["size"], int) and ver["size"] == len(dummy_bytes)
        assert ver["minOSVersion"] == "16.0"

        seeded_json = tmp / "seeded.json"
        write_altstore_source(
            out_path=seeded_json,
            version="2.0.0",
            download_url="https://github.com/xyl-g/FireSky/releases/download/v2.0.0/FireSky-2.0.0-ios-arm64.ipa",
            size_override=0,
        )
        seeded = json.loads(seeded_json.read_text(encoding="utf-8"))
        assert seeded["apps"][0]["versions"][0]["size"] == 0
        datetime.fromisoformat(seeded["apps"][0]["versions"][0]["date"].replace("Z", "+00:00"))

    print(f"self-test passed: schema verified, exact IPA size={len(dummy_bytes)} bytes")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", help="Release version string (e.g. 2.0.0)")
    parser.add_argument("--ipa", type=Path, help="Path to the .ipa file to measure byte size")
    parser.add_argument("--size", type=int, help="Explicit byte size override (useful when IPA is absent)")
    parser.add_argument("--download-url", help="Direct HTTPS download URL for the .ipa asset")
    parser.add_argument("--out", type=Path, help="Output path for altstore-source.json")
    parser.add_argument("--date", help="Optional UTC ISO 8601 timestamp (defaults to current UTC time)")
    parser.add_argument("--notes", help="Optional release notes for localizedDescription")
    parser.add_argument("--self-test", action="store_true", help="Run built-in schema and size verification")
    args = parser.parse_args(argv)

    if not args.self_test:
        missing = [
            flag
            for flag, value in (
                ("--version", args.version),
                ("--download-url", args.download_url),
                ("--out", args.out),
            )
            if not value
        ]
        if missing:
            parser.error(f"missing required arguments: {', '.join(missing)}")
        if args.ipa is None and args.size is None:
            parser.error("either --ipa or --size must be provided")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.self_test:
        run_self_test()
        return 0

    output = write_altstore_source(
        out_path=args.out,
        version=args.version,
        download_url=args.download_url,
        ipa_path=args.ipa,
        size_override=args.size,
        date=args.date,
        notes=args.notes,
    )
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
