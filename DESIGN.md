# FireSky Design System

FireSky 是一款围绕「朝霞 / 晚霞观测窗口」的天气
工作台，多端（macOS AppKit+SwiftUI / Windows WinUI / Android Compose /
iOS SwiftUI / 共享 Web Svelte）共用同一套视觉识别。设计基调是**黄昏观测**
——铜橙作为强调色（brand accent），冷静蓝（macOS 主基准）/青绿（历史移动与 Web 兼容色）作次级与第二模型色，天空渐变
做页面底色；数据可视化只使用三个语义图表色（覆盖/照度/透过率），绝不引入
新的数据色相。深浅双主题一等公民：所有 token 都成对给出 light/dark 值，
界面默认中文（简体），可切换 English，数值用等宽字体。

2026-11-28 平台对齐方向：**macOS 是桌面 UI 基准，iOS 是移动 UI 基准**。
Windows 尽量对齐 macOS 的信息结构、视觉语义、内容密度与操作顺序；Android
尽量对齐 iOS 的导航、信息层级、状态表现与触控流程。四个原生客户端使用同一
后端合同和业务语义，同时保留各平台原生控件、键盘习惯、辅助功能和触控尺寸。
界面验收记录见 [macOS 图册说明](docs/ui/macos/README.md)；截图与图库索引是本机
测试产物，因体积策略不纳入版本库，不能作为源码包中的可视图库链接。
每个平台都需验证浅/深主题、窄/宽窗口或屏幕、空/加载/部分/失败/成功状态；
移动端另需验证系统大字、滚动和主要操作按钮可用性。截图与 mock 结果只证明 UI
表现，不证明真实天气数据链路。

```yaml
colors:
  brand.warm.light: "#D25A36"
  brand.warm.dark: "#FF9B6A"
  brand.coolAccent.light: "#4F76B8"
  brand.coolAccent.dark: "#8EB7FF"
  brand.teal: "#428F8A" # 兼容旧 Web/移动辅助色；macOS 基准统一使用 coolAccent
  background.gradient.light.start: "#EEF3F8"
  background.gradient.light.mid: "#F8F5F0"
  background.gradient.light.end: "#EBF1F6"
  background.gradient.dark.start: "#0B1222"
  background.gradient.dark.mid: "#121B2E"
  background.gradient.dark.end: "#080E18"
  surface.light: "#FBFCFE"
  surface.dark: "#1B2435"
  surfaceVariant.light: "#F2F6FA"
  surfaceVariant.dark: "#232E40"
  onBackground.light: "#1B2224"
  onBackground.dark: "#E6EDF0"
  success.light: "#1F6E5A"
  success.dark: "#63D6AB"
  warning.light: "#8A6321"
  warning.dark: "#FFC273"
  danger.light: "#B83E38"
  danger.dark: "#FF8A7A"
  chart.coverage.dark: "#33D499"
  chart.coverage.light: "#05664A"
  chart.illumination.dark: "#FABF24"
  chart.illumination.light: "#8F5405"
  chart.transmission.dark: "#38BDF7"
  chart.transmission.light: "#0A5B8F"
  selection.light: "#BD523D1F"
  selection.dark: "#FF8C611F"
  accent.gradient.dark.start: "#FF9B6A"
  accent.gradient.dark.end: "#FFC273"
  accent.gradient.light.start: "#B84B2E"
  accent.gradient.light.end: "#9B401F"
  action.foreground.light: "#FFFFFF"
  action.foreground.dark: "#241810"
  card.border.dark: "#4B5D78"
  card.border.light: "#C9D5E1"

typography:
  display.numeric: "32px mono medium（百分比/时刻/倒计时等关键数字）"
  body.mobile: "15-17sp/pt system（新控件支持系统文字缩放，旧结果表逐批迁移）"
  body.desktop: "12-13pt system (SF Pro / Segoe UI，macOS 逐步以 13pt 与动态字号为基准)"
  heading.page: "20-22pt semibold/bold"
  heading.card: "13-14pt semibold"
  caption: "desktop 10-11pt / mobile 12-13sp/pt secondary"
  numeric: "monospaced digit（等宽数字，时间/百分比/字节）"

rounded:
  card.desktop: 16 # 主内容卡 16pt，分组/浮层 12pt（同心圆角）
  card.mobile: 8
  control: 8
  chip: 6

spacing:
  page.padding: 16-20
  card.padding: 14-18
  element.gap: 6-14
  row.height.list: 44-46
  button.height: "desktop 36-42 / iOS >=44 / Android >=48"

motion:
  interactive: 180ms ease-in-out（preflight 展开等 UI 状态切换）
  progress.undetermined: 系统默认转轮（不自制动画）
```

## Colors

- 强调色是铜橙 `brand.warm`：浅色 `#D25A36`，深色 `#FF9B6A`。
  小号文字优先使用系统正文色或语义前景色。主按钮、选中态、进度强调、
  观测时间轴高亮全部只用它。
- 青绿 `brand.teal` 仅作次级语义（辅助参数、次要 chip），不与暖橙争夺强调。
- 语义色三对：success（就绪/已下载）、warning（未核验/注意）、danger
  （失败/删除）。删除类破坏性按钮用 danger 实底。
- 图表色三组固定语义对：`chart.coverage`（覆盖率）、`chart.illumination`
  （照度）、`chart.transmission`（透过率），dark/light 各自独立取值——
  任何新图表必须复用这三组，禁止新色相。
- 页面底色是三段轻微色温变化，light 为冷瓷白、暖白，dark 为夜空色，
  用作整体氛围而非表面色；卡片表面用 `surface`/`surfaceVariant`。
- 文字对比：正文对背景 ≥ WCAG AA；caption 一律带 60% 不透明度且只放次要
  信息，不得承载可执行操作。

## Typography

- 数值（时间戳、百分比、字节量、坐标）一律等宽字体（monospacedDigit），
  与中文正文混排。
- 页面大标题 22px bold；卡片标题 13-14px semibold；正文 12px（桌面）/
  12-14px（移动）；说明文字 10-11px caption。禁止小于 9px 的文字。
- 默认界面文案为简体中文，英文模式使用对应翻译；GFS / ECMWF / CAMS /
  SRTM 等专有名词保持原文。

## Layout

- 桌面侧栏宽 196px、行高 46px；移动端底部导航 + 每页 16px 水平 padding。
- 卡片间距 12-14px；卡片内垂直 10-15px、水平 15-20px。
- 桌面主按钮 40-44pt、列表行 46pt；手机交互区域 iOS ≥44pt、Android ≥48dp，
  文字放大时允许多行并增长高度，不以固定高度截断。
- 长路径/长错误必须可截断（middle truncation）且提供复制；错误文字保持
  可选择（text selection）。

## Elevation

- 移动端数据表面使用稳定不透明 surface 和细描边；玻璃材质集中于系统导航、
  工具栏和浮层。桌面遵循 `MACOS_LIQUID_GLASS_UX_HANDOFF.zh-CN.md` 的材质分层。
  不使用多重阴影。
- 弹窗（确认/引导）统一平台默认模态，不自制弹窗样式。

## Shapes

- 卡片、按钮/输入 8pt/dp，小 chip 6pt/dp；手机图标按钮仍保留 44/48 的触控区域。

## Motion

- 交互状态切换统一 180ms ease-in-out；进度用系统组件（线性/转轮），
  不自制动画；不使用装饰性动画。

## Voice & Content

- 文案口径：直接陈述状态与下一步，不拟人、不感叹。错误消息三段式——
  发生了什么 + 可执行的下一步 + 细节可复制。
- 关键口径（必须全端一致）：
  - 「旧日期仅本地缓存 ·」前缀用于 past-local-cache-only 策略行；
  - 「CAMS 全球大气数据仅覆盖未来 5 天内的日期；所选 X 超出时效窗口」
    用于超窗拒绝；
  - 删除确认必须给出具体范围（源/周期、文件数、预计释放 MB）并注明
    「占用中的文件会跳过；报告与任务记录不受影响」；
  - 「恢复已发布结果 / 恢复参数并重新检查 / 删除记录」三动作语义固定；
  - 时间一律本地时区显示（MM-dd HH:mm），原始 UTC 放入 tooltip/help。

## Components

- 主行动按钮 AccentButton：`accent.gradient`（dark `#FF9B6A` → `#FFC273`，light `#B84B2E` → `#9B401F`）+ 圆角 8；dark 深字 / light 白字；禁用态改 30% 灰。破坏性按钮仅用于真实删除/卸载操作，用 danger 实底；次级动作 Outlined / borderless。
- 状态徽标 StatusChip：语义色 14% 底 + 图标（11 semibold）+ 文字（11 medium）+ 语义边框，圆角 6、内边距 4×8；五态 `done` / `running` / `failed` / `interrupted` / `pending`。替换一切裸彩色状态文字（v1.9.0 起历史/预检行已接入）。
- 分段选择器（Segmented）：浅色背景 5% 黑、选中项高对比白/浮层底（accent 时主题色 14%）；用于枚举 ≤4 项。
- 列表行：状态图标（checkmark/exclamation/stop/pause/clock 五态）+ 标题 + 状态字 + 时间行；行内动作图标按钮 28–30px。
- 卡片与边框：内容卡采用稳定表面 + `card.border`（强调态才使用品牌色，高对比模式增强边界）；标题行右侧放该卡片唯一主动作，次级动作 borderless。
- 空态：图标 + 一句话说明 + 条件提示（在线/离线口径），首次空态不堆叠空数据占位卡片。
- 「加载更早任务」：历史页底部居中次级按钮，分页游标 `has_more` 时显示。

## Do's and Don'ts

- Do：所有新 UI 先查本文件的 token 值与文案口径；深浅双主题同时验证；
  每屏提供空态/加载/错误三种非理想态。
- Do：跨端文案逐字一致（同一功能同一句话），允许字体度量差异。
- Don't：不引入 brand.warm/teal/三组图表色之外的数据色相；不自制进度
  动画；不用 emoji 承载状态语义；不给 caption 配可点击操作；不隐藏
  错误细节（保持可复制）。
- Don't：不加新的隐藏目录/隐藏设置（可见目录原则，2026-11-20 方案 §8）。
