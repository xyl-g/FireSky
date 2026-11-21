# FireSky UI 设计优化方案（四端）

日期：2026-11-21
基点：v1.8.1（8bf0090 后续走查修复），以四端快照走查与视觉验收记录为证据。
状态：**设计方案（待评审）**——P1 阶段落地后 DESIGN.md 同步升级为 v2。
参考：Google Labs `design.md` 格式规范（DESIGN.md 已是仓库现状基线）。

## 1. 现状诊断（走查证据 → 问题分级）

证据来源：2026-11-20/21 共 5 轮快照视觉验收（macOS 数据/历史/设置、
Android 四页、iOS 四页、GUI 工作区数据页）与 OCR 记录。

### P0（用户可感知的"廉价感"主因）

| 编号 | 问题 | 证据 |
| --- | --- | --- |
| D1 | **状态用裸文字**：「已完成/需下载/运行中」是彩色文字直接贴在行内，无容器；信息密度高的页面（历史/预检）读起来像调试输出 | 历史页每行 2 处彩色字、预检行 1 处 |
| D2 | **强调色平淡**：暖橙单色实底（#BD523D 浅色）在大按钮/进度条上显得暗哑；主行动按钮与次级按钮仅"实底 vs 描边"差异，无层次 | 工作台「开始预测」、数据页按钮 |
| D3 | **排版无节奏**：页面标题 22px bold 之外全部挤在 10-13px；结果概览的百分比/时刻等关键数字与普通说明同字号；标题与内容的间距全靠 Arrangement.spacedBy 均匀分布 | 四端所有页面 |
| D4 | **卡片无边界系统**：桌面卡片靠 0.72 半透明 surface + Divider 分隔，深色主题下卡片与背景几乎融合；移动端 surfaceVariant 差异仅 8% | macOS 数据页/设置页、Android 设置 |
| D5 | **深色主题底色杂**：三段渐变（深青→暗紫→墨绿）混入紫色相，与暖橙/青绿主色相冲突，暗环境下发灰 | macOS/桌面深色快照 |
| D6 | **空态无品牌**：「暂无任务记录」纯文字（iOS 有系统图标），四端空态形态不一致 | 历史页/库存页空态 |

### P1（一致性/效率）

| 编号 | 问题 |
| --- | --- |
| D7 | 设置-数据分类 6 大块仅靠 Divider 堆叠，滚动 5+ 屏无锚点导航 |
| D8 | 图表三色（coverage/illumination/transmission）仅用于曲线，未形成"天空语义"的组件级语言（如时段条、日落倒计时） |
| D9 | 历史行内 3 个图标按钮（恢复/重试/删除）与文本同基线排布，命中区 30px 且无分组间隔 |
| D10 | 四端图标体系混用（SF Symbols / Material Icons / Lucide），同语义图标形状不同 |

### P2（后续增强）
- Windows WinUI 的 token 对齐（Windows 端 UI 刚恢复，随 P1 完成后对齐）
- 空态品牌插画（BrandMark 衍生图形）
- 结果页数据可视化的"天空语义"重绘（时段条、太阳路径）

## 2. 设计方向：「黄昏观测台」

保持现有气质基调（黄昏、天空、专业气象），把"丑"的根源转为三条可执行原则：

1. **状态有容器**：任何状态语义（就绪/警告/失败/运行中）一律用「徽标
   chip」表达（圆角 6、icon + 文字、语义色 12% 底），不再裸文字。
2. **强调有光感**：主行动与进度使用暖橙→琥珀的**强调渐变**
   （135°，dark: #FF8C61 → #FABF24；light: #E06A3C → #C86A2E），让"开始
   预测/下载/迁移"这类主行动在视觉上发亮；次级动作维持描边。
3. **数字有层级**：结果概览、倒计时、百分比等关键数字用 **32px mono
   display 层**；正文 13px；说明 11px@60%——三级分明，段落间距 4/8/16
   三档固定。

## 3. Token 升级（DESIGN.md v2 草案）

在现有 DESIGN.md 基础上**只增不改语义**（现有引用不受影响）：

```yaml
colors:  # 新增
  accent.gradient.dark.start: "#FF8C61"
  accent.gradient.dark.end: "#FABF24"
  accent.gradient.light.start: "#E06A3C"
  accent.gradient.light.end: "#C86A2E"
  chip.success.dark.bg: "#33CC70 @ 14%"
  chip.warning.dark.bg: "#FFB340 @ 14%"
  chip.danger.dark.bg: "#FF6B52 @ 14%"
  chip.success.light.bg: "#3B9C57 @ 12%"
  chip.warning.light.bg: "#C9A23C @ 12%"
  chip.danger.light.bg: "#C4574A @ 12%"
  card.border.dark: "#FFFFFF @ 8%"
  card.border.light: "#1B2224 @ 6%"
  surface.raised.dark: "#1E2A2C"
  surface.raised.light: "#F7FAFA"

typography:  # 新增 display 层
  display.numeric: "32px mono medium（百分比/时刻/倒计时）"
  heading.page.desktop: "28px bold（原 22px）"
  heading.page.mobile: "24px bold"

spacing:  # 固定三档节奏（新增）
  scale: [4, 8, 16, 24]

rounded:  # 新增徽标
  chip: 6
```

深色背景渐变修正（D5）：`dark.mid` 从 `#211E24`（紫）改为 `#16221F`
（青绿同族），`dark.start/end` 相应微调，去除紫相。

## 4. 组件规范（四端同构）

### 4.1 状态徽标 StatusChip（D1）
- 结构：`HStack { SF/Material 图标(12) + 文字(11 medium) }`，底色语义色
  12-14%，圆角 6，内边距 3×8。
- 五态映射：done→success Check、running→primary Clock（可带 1s 呼吸
  不做动画）、failed→danger Exclamation、interrupted→warning Pause、
  pending→secondary Circle。
- 替换点：历史行状态字、预检行状态字、数据页「已下载/需下载」。

### 4.2 主按钮 AccentButton（D2）
- 背景 `accent.gradient`、圆角 8、高 44（桌面）/40（移动）、文字白
  （light 用深字）；按压态整体 90% 不透明度；禁用态去渐变改 30% 灰。
- 替换点：开始预测、下载缺失数据、确定预测、下载区域地形、迁移、
  下载并安装。

### 4.3 卡片 Card（D4）
- 表面 `surface`，1px hairline `card.border`，圆角 10，内边距 16；
  标题行（13 semibold + 右侧唯一主动作）与内容区间距 12。
- 悬停（桌面）：surface→surface.raised。

### 4.4 关键数字（D3）
- 结果概览顶行：时刻/覆盖率等关键值用 `display.numeric`（32 mono），
  下方 caption 说明；移除同级混排。

### 4.5 历史行（D9）
- 动作图标从行尾移出，改为行尾一个「…」菜单（macOS Menu /
  移动 DropdownMenu），命中区 36；危险项（删除）在菜单内红色。
- 状态徽标替换文字；时间行合并进徽标右侧 caption。

### 4.6 空态 EmptyState（D6）
- 统一组件：BrandMark 24% 透明度小图（32px）+ 标题（13 medium）+ 说明
  （11@60%）+ 可选主行动按钮；四端同一文案与层级。

### 4.7 设置锚点（D7）
- macOS 数据分类顶部加锚点 chips（下载源 / CAMS / 连通性 / 同步 /
  保留 / 目录），点击滚动到对应块（ScrollViewReader）。

## 5. 四端映射

| Token/组件 | macOS/iOS（SwiftUI） | Android（Compose） | Web（Svelte） |
| --- | --- | --- | --- |
| accent.gradient | LinearGradient(start:.topLeading,end:.bottomTrailing) | Brush.linearGradient | CSS linear-gradient |
| StatusChip | 新组件 StatusChip.swift（History/Preflight 复用） | StatusChip.kt | StatusChip.svelte |
| card.border | .overlay(RoundedRectangle.stroke) | Modifier.border(1.dp, ...) | CSS border |
| display.numeric | .font(.system(size:32,weight:.medium).monospacedDigit()) | 32.sp FontFamily.Monospace | 32px mono |
| 深色渐变修正 | Workbench.swift background | Theme.kt background | app.css --bg-grad |
| 图标统一 | SF Symbols | Material Icons | Lucide |

Windows（WinUI）随 P2 对齐同一 token 表（XAML ResourceDictionary）。

## 6. 实施阶段

| 阶段 | 内容 | 验收 |
| --- | --- | --- |
| P1（v1.9.0） | token v2 落地四端（渐变/徽标/卡片边框/深色渐变修正）+ StatusChip 替换三处 + AccentButton 替换主按钮 + 历史行菜单化 | 四端关键页 before/after 快照对比（视觉验收）；DESIGN.md 升 v2 且 lint errors=0；全量回归 |
| P2（v1.9.x） | display.numeric 结果概览重排 + 设置锚点 + 空态统一组件 + 历史行菜单化 | 同上口径 |
| P3 | Windows token 对齐 + 天空语义可视化重绘 + 品牌插画 | 单独立项评审 |

## 7. 验收口径（每阶段通用）

1. 四端关键页快照（工作台/数据/历史/设置）深浅双主题 + 1440×900 /
   1080×2400 / 1206×2622 三尺寸；
2. DESIGN.md v2 lint errors=0，新增 token 全部有四端实现引用（grep 验证）；
3. 现有功能回归全绿（engine/三端测试/打包冒烟）；
4. 对比度：新增文字/背景组合全部 WCAG AA（lint 自动检查）。

## 8. 风险与边界

- 不改变任何功能行为与信息架构（纯视觉层）；预检/删除确认等文案口径
  保持 v1.8.x 逐字一致。
- Windows/WinUI 端 P1 暂不动（刚恢复，先稳构建），P2 对齐。
- DESIGN.md v2 为增量新增（现有 token 语义不变），避免破坏已发布 UI。
