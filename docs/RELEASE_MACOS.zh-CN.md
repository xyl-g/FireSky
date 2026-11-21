# FireSky macOS 发布流程与 v1.0.0 发布记录

更新日期：2026-11-20

本文是 macOS 正式发布的 runbook 与 v1.0.0 的实际发布记录。2026-11-15 的
预演（未 publish、未打 tag）见
[RELEASE_V1_SIMULATION_2026-11-15.zh-CN.md](archive/RELEASE_V1_SIMULATION_2026-11-15.zh-CN.md)；
本次是首次正式 publish：tag `v1.0.0` + GitHub Release 双架构 DMG。

## 1. 版本与载荷

| 项 | 值 |
| --- | --- |
| 产品版本 | 1.0.0（`engine/Cargo.toml`、`Info.plist`、`CHANGELOG.md` 一致） |
| 二进制源码 | `f341ef8`（Native Desktop CI 全绿 run 44571959210） |
| 发布 tag | `v1.0.0`，打在发布文档提交上；与 `f341ef8` 差异仅文档（见 §3 说明） |
| 最低系统 | macOS 14.0 |
| 签名 | ad-hoc（`codesign --sign -`），无 Developer ID / 公证（用户裁定不购买签名） |

| 载荷 | 来源 | 验收 |
| --- | --- | --- |
| `FireSky-1.0.0-macos-arm64.dmg` | 本地 `build_macos_native.sh` @ f341ef8（2026-11-20） | 本地全门禁（§2）+ hdiutil VALID + 挂载校验 |
| `FireSky-1.0.0-macos-x86_64.dmg` | CI run 44571959210 `FireSky-macos-x86_64` artifact（macos-26-intel） | CI FFI bundle 冒烟 + hdiutil VALID + 本地挂载校验 |

SHA256：随 Release 发布的 `SHA256SUMS.txt` 只含两个 DMG、裸文件名，用户
下载后同目录直接 `shasum -a 256 -c SHA256SUMS.txt` 即可校验；含主二进制
哈希的完整 provenance 版存 `dist/local-release-20261120/SHA256SUMS.txt`。
DMG 哈希：

```text
7bf1b0c588d49d1de562584266e3b2e5ed9535bae55a515430b3a6b347856772  FireSky-1.0.0-macos-arm64.dmg
d3553739a050489b98cc0be15a33c19af30459bfc434d1bb554f4b31e38fda86  FireSky-1.0.0-macos-x86_64.dmg
```

Intel 载荷取 CI 的原因：本地交叉构建 x86_64 需要 x86_64 的 ecCodes 解码器
（`FIRESKY_ECCODES_LIBRARY` / `FIRESKY_ECCODES_PREFIX`），本机只有 arm64
brew 版；CI 在 Intel runner 上用同一脚本构建并冒烟，源码一致。

## 2. 发布门禁证据（2026-11-20，@ f341ef8）

| 门禁 | 结果 | 证据 |
| --- | --- | --- |
| Rust 全量（release，--locked） | 491 passed / 0 failed / 3 ignored；集成 4+2+4 | `output/release-20261120-cargo-test-full.log` |
| pytest 契约（release 引擎） | 38 passed / 1 skipped | `output/release-20261120-pytest-contract.log` |
| 原生构建 + DMG | rc=0；hdiutil verify VALID | `output/release-20261120-build-native.log` |
| native smoke（FFI/Swift 夹具/冷启动/bundle，含 clean-machine 非 ASCII 工作区） | 4/4 ok | `output/release-20261120-native-smoke.log` |
| 离线 flow（point 双模型 / regional gfs / regional ec，sandbox deny network*） | FLOW OK ×3 | `output/release-20261120-replay-flow-*.log` |
| 回放 UI（point / regional / regional-ec） | ok ×3 | `output/release-20261120-replay-ui-*.log` |
| 双 DMG 挂载校验（版本/架构/codesign/资源清单/eccodes） | 全过；x86_64 引擎另经 Rosetta 实跑 `hello` 返回完整能力清单 | `output/release-20261120-dmg-mount-verify.log` |
| GitHub CI | Native Desktop 10 jobs + 主 CI 全绿 | run 44571959210 / 44571959208 |

## 3. 发布步骤（runbook）

1. **门禁**：按 §5 复现命令全跑，全绿才继续；离线回放仅用本地缓存
   （`sandbox-exec (deny network*)`），不触发上游下载。
2. **载荷**：arm64 本地构建（`build_macos_native.sh` 已内置 DMG create +
   verify，`FIRESKY_BUILD_DMG=0` 可跳过）；Intel 取最新全绿 Native Desktop
   run 的 `FireSky-macos-x86_64` artifact，或在 Intel 机器上
   `FIRESKY_MACOS_ARCH=x86_64` 构建。集中到 `dist/local-release-<日期>/`。
3. **DMG 校验**：`hdiutil verify`；挂载后检查 `CFBundleShortVersionString`、
   `lipo -archs`（FireSky / firesky-engine / libfiresky_engine.dylib 三个
   二进制）、`codesign --verify --deep --strict`、
   `Resources/app-data/RESOURCE-MANIFEST.json`、`Resources/lib/libeccodes.dylib`。
4. **校验和**：`shasum -a 256` 生成两份——provenance 版（双 DMG + 主二进制，
   仓库相对路径，存 staging 目录）与 Release 版（仅双 DMG、裸文件名，
   上传为 `SHA256SUMS.txt`）。
5. **文档**：更新 `CHANGELOG.md` 与本文发布记录；提交时不包含无关的
   未提交改动。
6. **tag**：`git tag -a v1.0.0 -m "FireSky v1.0.0 — 首个原生正式版"`；
   `git push origin main v1.0.0`。
7. **GitHub Release**：基于 tag 创建 Release，上传双 DMG +
   `SHA256SUMS.txt`；公告必须包含无签名首次打开指引（§4）。
8. **发布后校验**：`GET /repos/xyl-g/FireSky/releases/tags/v1.0.0` 确认
   资产 `state=uploaded`、字节大小与本地一致；抽查下载并核对 SHA256。
   证据存 `output/release-20261120-publish-verify.log`。

说明：`native-desktop.yml` 带 paths 过滤（apps/engine/resources/指定脚本/
workflow 自身），纯文档提交不触发原生重建。因此发布 tag 可以打在文档提交
上，载荷以同一二进制源码（f341ef8）的最新全绿 run 为准；两者的差异必须在
本文记录（本次仅 CHANGELOG 与 docs，无二进制影响）。

## 4. 无签名发布与用户放行指引

本项目开源免费，不购买 Apple Developer ID（99 美元/年）。发布为 ad-hoc
签名：完整性可校验（`codesign --verify` 通过），但没有 Apple 身份背书，
Gatekeeper 默认拦截首次打开。公告与 README 需原样带上以下指引：

- **macOS 14**：右键 `FireSky.app` →「打开」；或系统设置 → 隐私与安全性 →
  「仍要打开」。
- **macOS 15 及以上**：右键打开不再绕过 Gatekeeper。首次双击被拦截后，
  到系统设置 → 隐私与安全性 →「仍要打开」；或终端执行：

  ```bash
  xattr -dr com.apple.quarantine /Applications/FireSky.app
  ```

- 仅从官方 Release 页下载，打开前核对 SHA256：

  ```bash
  shasum -a 256 FireSky-1.0.0-macos-arm64.dmg   # 与 SHA256SUMS.txt 比对
  ```

- 未来若购买签名：构建脚本已支持 `FIRESKY_CODESIGN_IDENTITY` /
  `FIRESKY_CODESIGN_ENTITLEMENTS`（Developer ID 时自动加
  `--options runtime --timestamp`），公证可接 `xcrun notarytool submit` +
  `xcrun stapler staple`，打包结构无需改动。

## 5. 复现命令

```bash
cargo test --manifest-path engine/Cargo.toml --locked --release
cargo build --manifest-path engine/Cargo.toml --release --locked --lib --bin firesky-engine
FIRESKY_REQUIRE_RUST_TESTS=1 \
  FIRESKY_RUST_ENGINE_BIN="$PWD/engine/target/release/firesky-engine" \
  .venv/bin/python -m pytest tests/ -q
bash scripts/build_macos_native.sh
FIRESKY_CLEAN_MACHINE_SMOKE=1 FIRESKY_APP_SMOKE=1 FIRESKY_EXPECTED_ARCH=arm64 \
  bash scripts/test_macos_native.sh
bash scripts/test_macos_replay_ui.sh "$PWD/output/release-v1-20261115/replay-point" point
bash scripts/test_macos_replay_ui.sh "$PWD/output/release-v1-20261115/replay-regional" regional
bash scripts/test_macos_replay_ui.sh "$PWD/output/release-v1-20261115/replay-regional-ec" regional

# Intel 载荷（三选一）：
# a) Intel 机器：FIRESKY_MACOS_ARCH=x86_64 bash scripts/build_macos_native.sh
# b) 本机交叉：需 x86_64 ecCodes（FIRESKY_ECCODES_LIBRARY / FIRESKY_ECCODES_PREFIX）
# c) CI artifact（本次采用）：
curl -L -H "Authorization: Bearer $TOKEN" -o macos-x86_64.zip \
  "https://api.github.com/repos/xyl-g/FireSky/actions/artifacts/<artifact-id>/zip"

# DMG 校验 + 挂载检查（本次脚本，含双架构/版本/codesign/资源清单）
bash output/release-20261120-mount-verify.sh
```

离线 flow（`sandbox-exec (deny network*)` 包装，仅回放本地缓存）：

```bash
export FIRESKY_RUST_ENGINE_BIN="$PWD/dist/native-macos/FireSky.app/Contents/Resources/firesky-engine"
for ws in replay-point replay-regional replay-regional-ec; do
  /usr/bin/sandbox-exec -p '(version 1)(allow default)(deny network*)' \
    .venv/bin/python "output/release-v1-20261115/$ws/desktop_flow.py"
done
```

## 6. v1.0.0 发布记录

- tag：`v1.0.0`（发布文档提交；二进制源码 `f341ef8`，差异仅文档）。
- Release：<https://github.com/xyl-g/FireSky/releases/tag/v1.0.0>
- 资产：`FireSky-1.0.0-macos-arm64.dmg`（208,024,708 B）、
  `FireSky-1.0.0-macos-x86_64.dmg`（219,777,474 B）、
  `SHA256SUMS.txt`（仅双 DMG、裸文件名）。
- 发布后校验证据：`output/release-20261120-publish-verify.log`
  （资产 state/大小核对与下载抽查）。
- 旧模拟载荷（2026-11-15 的 `dist/v1.0.0/` arm64 DMG）已移入废纸篓，
  避免与正式发布载荷混淆；正式载荷以 `dist/local-release-20261120/` 为准。
