"""CLI: python -m firesky predict --lat .. --lon .. --date .. [--phenomenon dusk]"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from firesky.engine import predict_burn_sky


def _default_cycle(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    candidate = now - timedelta(hours=5, minutes=45)
    cycle_hour = (candidate.hour // 6) * 6
    return candidate.replace(hour=cycle_hour, minute=0, second=0, microsecond=0)


def main() -> None:
    parser = argparse.ArgumentParser(prog="firesky", description="firesky — burning-sunset prediction engine")
    sub = parser.add_subparsers(dest="command", required=True)
    predict = sub.add_parser("predict", help="预测一次火烧云事件")
    predict.add_argument("--lat", type=float, required=True)
    predict.add_argument("--lon", type=float, required=True)
    predict.add_argument("--date", dest="target_date", required=True, help="YYYY-MM-DD")
    predict.add_argument("--phenomenon", choices=("dawn", "dusk"), default="dusk")
    predict.add_argument("--cycle", default=None, help="ISO UTC 周期，缺省取最近可用 GFS 周期")
    predict.add_argument("--elevation-m", type=float, default=0.0)
    predict.add_argument("--name", default="观测点")
    predict.add_argument("--cams-cache-dir", default=".cache/cams")
    predict.add_argument("--gfs-cache-dir", default=".cache/firesky/gfs")
    predict.add_argument("--radius-km", type=float, default=1000.0)
    args = parser.parse_args()

    cycle = (
        datetime.fromisoformat(args.cycle)
        if args.cycle
        else _default_cycle()
    )
    if cycle.tzinfo is None:
        cycle = cycle.replace(tzinfo=timezone.utc)
    target = datetime.strptime(args.target_date, "%Y-%m-%d").date()
    result = predict_burn_sky(
        [
            {
                "name": args.name,
                "lat": args.lat,
                "lon": args.lon,
                "elevation_m": args.elevation_m,
                "timezone_name": "Asia/Shanghai",
            }
        ],
        target,
        args.phenomenon,
        cycle,
        cams_cache_dir=Path(args.cams_cache_dir),
        gfs_cache_dir=Path(args.gfs_cache_dir),
        radius_km=args.radius_km,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
