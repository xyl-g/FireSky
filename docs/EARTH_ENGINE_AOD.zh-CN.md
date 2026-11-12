# Earth Engine CAMS AOD 接入

本路径是显式的数据导入和 Rust 计算接口。它取得 CAMS 经 Earth Engine
分发的多波段 AOD，不会把估算值写成 `cams-optics-v2` GRIB，也不会自动
替代当前预测路径要求的完整 CAMS `aerext532` 廓线。

## 下载

Python Earth Engine SDK 负责 OAuth、影像选择及小区域原生网格导出。
依赖为 `earthengine-api`、`numpy`、`requests`；可以使用已有的 Earth Engine
环境运行，不要求预测引擎安装这些 Python 包。

```bash
python scripts/download_gee_cams_aod.py \
  --project YOUR_EARTH_ENGINE_PROJECT \
  --target-utc 2026-11-12T09:00:00Z \
  --area 31 120 30 121 --point 30.27 120.15 \
  --output-dir output/gee-aod-example
```

选择规则是先精确匹配有效时刻，再选择最短 `model_forecast_hour`。
无影像时直接报错，不自动退到其他时刻。导出保留原始 CRS/WKT、仿射
变换和网格，不把 0.4 度源数据包装为 0.25 度数据。这里的仿射变换
使用 Earth Engine 的六参数顺序。

下载文件：

- `aod.npy`：原始结构化数组，包含 469/550/670 nm AOD、经纬度和有效掩码。
- `aod-input.json`：`gee-cams-aod-v1` 快照，包含原始 AOD、网格、起报时次、
  有效时刻、预报时效及 NPY 的 SHA-256。可选 `point` 为同一影像的点采样。

集合为 `ECMWF/CAMS/NRT`，波段为
`total_aerosol_optical_depth_at_{469,550,670}nm_surface`。
凭据由 SDK 从用户配置读取，不写入导出文件。小区域导出限制为 32 MiB、
最多 100000 个网格点；大区域应另行设计分块策略。

## Rust 计算

新接口通过现有 `firesky-engine call` 和 RPC 提供：

```bash
engine/target/debug/firesky-engine call calculate_earth_engine_aod \
  --input output/gee-aod-example/calculate-params.json
```

参数示例：

```json
{
  "input_path": "output/gee-aod-example/aod-input.json",
  "output_dir": "output/gee-aod-example/calculated",
  "scale_heights_m": [1000, 2000, 4000],
  "heights_agl_m": [0, 500, 1000, 2000, 4000, 8000]
}
```

省略两个高度数组时只计算光谱量，不生成任何垂直廓线。
计算输出为 `aod-calculated.json`，契约为 `rust-gee-aod-calculated-v1`。
引擎验证时间关系、网格形状、输入来源和原始 NPY 的文件哈希。
Rust 读取 JSON 中的数组；NPY 保留为原始证据，并非由 Rust 二次解码后
逐值比对。源数据和输入 JSON 的身份均写入结果。

光谱换算使用 469/550 nm 的 Angstrom 幂律得到 AOD532，同时计算
550/670 nm 的斜率及两者差值，保留光谱非幂律行为的诊断信息。
不硬裁剪斜率，不把缺测或单波段为零的数据补成正常值。三个波段全部
为零时，AOD532 为零、斜率为 null。

只有显式提供高度参数时，才用 `beta532(z)=AOD532/H*exp(-z/H)` 生成
指数廓线场景。高度为距当地地面的 AGL 米，不是气压层，也不是海拔。
这是模型假设，不是取到的 CAMS 消光数据：

- `aod550`：CAMS 数值预报，经 Earth Engine 分发。
- `aod532_estimate`：多波段 AOD 的光谱估算，无量纲。
- `assumed_profile_scenarios`：显式假设的消光廓线，单位 m^-1。
- `usable_as_cams_profile=false`：不得直接作为当前严格预测输入。

## 与预测主链的边界

目前已接入的是数据准备和计算 RPC，尚未改动桌面数据源设置、
`desktop_prepare`、`assessment::Catalog` 或 native scorer 的严格输入合同。

后续预测接入应使用单独的气溶胶来源/质量类型，分别表达完整 CAMS
廓线、混合来源廓线和假设廓线。保留以下约束：

1. 完整 CAMS 模式继续要求匹配有效时刻的真实 11 层廓线。
2. 估算模式必须显式启用，发布来源、假设、起报时次及有效时间。
3. 指数 AGL 廓线需要在地形和模型层高度上采样，不能直接复制成
   当前的 11 个标准气压层数组。
4. 当前 scorer 使用 AOD550 归一化廓线形状。接入 AOD532 廓线时必须
   明确计算波长，避免把 532 nm 的柱积分再解释成 550 nm 的实测量。
5. 调度器按事件所需时刻准备数据；不以集合中最晚影像替代实际事件时刻。

本地实现使用 Python 做 EE 访问、Rust 做计算；尚不是纯 Rust OAuth/EE
下载客户端，也不是已经上线的自动预测备用源。
