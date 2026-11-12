# Rust 缓存保留策略

Rust 原生 `/api/v1/firesky/data/prune` 支持显式 `retention` 策略。策略只在请求
明确传入时执行；默认不会因为服务启动而删除缓存。

```json
{
  "retention": {
    "gfs_cycles": 5,
    "ecmwf_cycles": 5,
    "cams_cycles": 5,
    "satellite_days": 7,
    "report_days": 30,
    "map_days": 30
  }
}
```

## 规则

- GFS、ECMWF、CAMS 分别按周期时间从新到旧保留指定数量；只有已经完成的下载请求
  才应在同一请求中调用清理。无法从元数据识别周期的文件不会被删除。
- 卫星数据按扫描时间计算滚动 `N × 24` 小时窗口，不按下载时间或自然日清空。
  `raw/<YYYYMMDDTHHMMZ>` 和 `compact/frame-<timestamp>-*.npz` 都受该规则约束。
- 报告和地图按文件修改时间分别清理。报告目录、点/区域结果目录中的地图引用会被
  扫描，仍被有效报告引用的地图不会被删除。
- 正在使用的文件若存在 `.lock`、`.decode.lock`、`.part` 或同目录锁，会返回
  `deferred`，下次清理再处理；清理不会删除活动任务输入。
- 删除是直接释放磁盘的删除，不再把结果留在 `.trash`。用户主动导出到其他目录的
  文件不在这些根目录内，不会被触碰。

## 配置

也可以通过环境变量提供默认值，调用请求仍可显式覆盖：

`FIRESKY_GFS_RETAIN_CYCLES`、`FIRESKY_ECMWF_RETAIN_CYCLES`、
`FIRESKY_CAMS_RETAIN_CYCLES`、`FIRESKY_SATELLITE_RETAIN_DAYS`、
`FIRESKY_REPORT_RETAIN_DAYS`、`FIRESKY_MAP_RETAIN_DAYS`。

下载接口在 payload 同时带有 `retention` 时，只有所有计划输入成功后才会应用策略；
部分失败不会淘汰已有可用周期。返回值包含 `removed`、`deferred`、`failed`、
`freed_bytes` 和 `policy`，便于桌面端和共享 Web 显示真实释放结果。

当前实现不负责确定默认保留数量，也不代替跨平台安装包和真实 CAMS 验收。
