# 历史缓存离线回放与停止交接

日期：2026-11-12。仓库：`/Users/xiaoyang/晓洋站点/firesky`。

## 用户最新要求

1. ADS 很慢，下载验收放到最后；先使用已经保存的数据验证其他流程。
2. 当前功能运行成功后停止，写好文档，后续由其他 agent 接续。

本批范围为历史缓存点/区域预测、结果发布与恢复、macOS 冻结剖面查询。
收尾后停止，不后台等待或启动下载，不自动开展下一项。Tauri、Windows/Linux
仍暂停，Docker 最后考虑。本文的后续建议不是当前 agent 的继续执行指令。

## 可复用的数据和回放口径

- 应用默认 workspace `~/Library/Application Support/FireSky/Data` 只有少量新缓存；
  较完整的历史输入在仓库 `.cache`：GFS 约 1.7 GiB、EC 约 1.9 GiB、CAMS 约
  127 MiB，另有 HSD 历史数据。
- 选择杭州 **2026-11-09 晚霞**：GFS 固定 `2026-11-07T18:00:00Z`，EC 固定
  `2026-11-07T12:00:00Z`；CAMS 按目标有效时刻与覆盖范围从已有 GRIB 中选择。
  没有手工注入 AOD，没有改原始文件的有效时刻，没有把历史回放标成今日预报。
- 11 月 10 日的 GFS 缺 7/11/12 小时时效，因此没有强行放行该日期；改用完整
  的 11 月 9 日输入。原始输入完整性检查、单次确认及过旧周期确认仍执行。
- 输出与任务历史写入 `output/offline-replay-20261112/{point,regional}`，不写入
  用户应用 workspace；不删除已有气象缓存。
- Rust 子进程由 macOS `sandbox-exec` 禁止网络；不调用 `prepare` / `publications`
  或下载接口。重启恢复阶段额外禁止读取 GFS/EC/CAMS/HSD 原始缓存目录。

## 本批修复

1. **EC 区域地形缓存路径**：`desktop_context::terrain` 原先只查缓存根目录的
   `ifs-basic-v2-*.grib2`，生产下载器实际按日期/周期分目录。改为复用
   `ecmwf_data::path`，兼容旧平铺缓存。新增两个布局回归测试：旧二进制下
   日期目录布局失败、旧平铺布局通过；修复后二者均通过。
2. **GFS 点查询坐标**：中国区域点可对齐到 0.25 度网格。界面原先用输入坐标
   `(30.2741,120.1551)` 查询实际冻结在 `(30.25,120.25)` 的 GFS 结果，被后端
   拒绝。`ProfileQueryBuilder.pointRequest` 现在优先使用已发布
   `cross_section.observer` 的坐标，EC 仍使用其独立精确观测坐标。
3. **冻结查询时戳精度**：Foundation 重新格式化时只输出毫秒，丢失事件锚点
   的微秒，导致 Rust 拒绝“不是五分钟事件网格”。现在由 Foundation 转换 UTC
   整秒，并保留已验证源时戳的小数部分；测试覆盖六位小数及北京时间到 UTC。
4. **剖面失败原因**：先处理 `status/reason`，再验证 ready 响应的周期，避免
   将缺失、过期等响应误报为周期不匹配。
5. **任务历史时间戳**：`NativeTaskHistoryItem` 原先不接受后端的毫秒时间戳，
   如 `2026-11-12T12:27:33.706Z`，会丢弃任务。改为兼容带/不带小数秒格式；
   测试覆盖真实后端格式、带时区偏移的六位小数和非法输入。

Qwen Code 之前已经实现点/实况查询入口，本批保留并修复其实际运行问题，
不是重新实现后端。点查询固定最佳时刻，实况可选已发布步骤；查询摘要不会替换
已有主剖面图，点预测尚无任意时刻选择器。

## 已验证

- **最终 macOS bundle 内的引擎**：点与区域均通过 `check -> forecast ->
  result_restore -> 关闭进程 -> 重启 -> result_restore`，恢复结果完全相等。
  重启进程被禁止读取原始输入时仍可恢复；两个模式的 `profile_query` 均 ready。
- 点预测发布两份真实输入生成的 HTML 报告，已读取并记录 SHA-256。
- 区域采用杭州附近四个 0.25 度格点，GFS/EC 各 `success_count=4`，各有 20 个
  事件帧；这是小区域验收，不是全国范围性能验收。
- macOS 截图测试使用真实历史结果，通过 Swift `NativeTaskHistoryItem` 和
  `Store.restoreTaskResult` 恢复，再执行 `Store.queryPointProfile` / 区域查询，
  经 Rust FFI 返回 ready；保留宽/窄窗口截图和日志。属于程序驱动的 UI 状态与
  渲染验证，不冒充人工鼠标逐项走查。
- Rust `cargo test --lib`：最终工作区 **214 通过、3 忽略**。此前 211 通过批次
  与最终批次不累加。新增 EC 布局回归和上下文/prepare 测试共 **42 通过**，
  该定向运行发生在并行 AOD 改动进入工作区之前。
- Swift fixtures 通过，含坐标与微秒回归；历史时间戳回归在 UI 回放测试运行。
- macOS ARM64 release 重新打包，ad-hoc 签名校验通过；C FFI、Swift FFI、Swift
  fixtures、冷启动 PNG 与 bundle smoke 均通过。没有 Developer ID 签名/公证。

最终回放的 bundle 引擎 `source_id`：
`d0d5fe30690d5c101474db8e70295b526667e6ec1da6a720c623b61f8a15f960`。
`point/evidence/hello.json` 和 `regional/evidence/hello.json` 保存实际运行身份，
`summary.json`、`forecast.json`、`restore*.json`、`profiles.json`、`reports.json`
保存对应结果。二进制/动态库重签名改变字节 SHA，不改变编译期 source_id。

## 复现

在仓库根目录执行；工具只回放本地数据，缺失即失败，不会补下载：

```bash
python3 scripts/verify_cached_workflow.py \
  --engine dist/native-macos/FireSky.app/Contents/Resources/firesky-engine \
  --workspace output/offline-replay-20261112/point \
  --request output/offline-replay-20261112/point-request.json

python3 scripts/verify_cached_workflow.py \
  --engine dist/native-macos/FireSky.app/Contents/Resources/firesky-engine \
  --workspace output/offline-replay-20261112/regional --kind regional \
  --request output/offline-replay-20261112/regional-request.json

bash scripts/test_macos_replay_ui.sh "$PWD/output/offline-replay-20261112/point" point
bash scripts/test_macos_replay_ui.sh "$PWD/output/offline-replay-20261112/regional" regional
```

UI 查询冻结源目前仍有 1800 秒 TTL，截图回放应紧接计算脚本执行。长期结果快照
恢复与动态剖面源保留是不同生命周期；不能把快照可恢复理解为源张量永久可查询。

## 留给后续 agent

- **完整真实实况回放仍缺输入**：11 月 7 日场景缺对应 CAMS 覆盖；11 月 8 日
  场景已有 CAMS，但 GFS 缺 f023/f024，所选参考时刻仅选到一帧卫星数据。
  保存了 `nowcast/evidence/check.json`，不改时戳、不造先验、不降低两帧要求。
  `nowcast-request.json` 是诊断请求，不是成功用例。
- 新 ADS 任务提交/排队/下载、最新 GFS/EC 下载、上游超时/重试、真实取消/失败、
  完整实况及地图恢复、长期保留和真实满盘场景仍需后续单独验收。
- 需要继续下载时，尊重用户“下载最后”的安排；先复核已有 ADS 任务是否完成，
  不重复提交相同任务。CAMS 生产路径仍强制不走代理；实验代理下载不代表已接线。
- macOS clean-machine、其他架构和签名/公证未完成，整体迁移不宣称结束。
- 工作期间另有并行改动新增 `engine/src/earth_engine_aod.rs` 及 `lib.rs/rpc.rs`
  接线。本批没有编辑或回退这些改动，最终 bundle 来自当时整个工作区；仅完成上述
  回放和本地门禁，不代表 Earth Engine 服务、授权或真实数据链路已验收。
- 工作区已有大量未提交修改；本批没有提交/推送、清理/reset 或删除 Python 产品源。
  后续应先看 `git status`、本文件及实际运行身份，再决定接续范围。
