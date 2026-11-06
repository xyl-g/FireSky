# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 李晓洋
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, version 3.

"""firesky — standalone burning-sunset prediction engine.

GFS 云柱 + 原生光线追踪天空评分（Rust），输出逐五分钟质量台账与档位。
GFS cloud columns + native ray-traced sky scoring (Rust), producing a
five-minute quality ledger with calibrated levels.
"""

from firesky.engine import FIRESKY_ALGORITHM_VERSION, predict_burn_sky
from firesky.version import FORECAST_ALGORITHM_VERSION

__all__ = [
    "FIRESKY_ALGORITHM_VERSION",
    "FORECAST_ALGORITHM_VERSION",
    "predict_burn_sky",
]
