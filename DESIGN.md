# FireSky Design System

Overview paragraphs first: FireSky 是一款围绕「朝霞 / 晚霞观测窗口」的天气
工作台，四端（macOS AppKit+SwiftUI / Windows WinUI / Android Compose /
iOS SwiftUI / 共享 Web Svelte）共用同一套视觉识别。设计基调是**黄昏观测**
——暖橙作为唯一强调色（brand accent），青绿作次级（辅助信息），天空渐变
做页面底色；数据可视化只使用三个语义图表色（覆盖/照度/透过率），绝不引入
新的数据色相。深浅双主题一等公民：所有 token 都成对给出 light/dark 值，
界面文字一律中文（简体），数值用等宽字体。

```yaml
colors:
  brand.warm.light: "#BD523D"
  brand.warm.dark: "#FF8C61"
  brand.teal: "#428F8A"
  background.gradient.light.start: "#E3EDEF"
  background.gradient.light.mid: "#F5EBE7"
  background.gradient.light.end: "#E3F0EB"
  background.gradient.dark.start: "#121A1C"
  background.gradient.dark.mid: "#211E24"
  background.gradient.dark.end: "#141F1D"
  surface.light: "#E3EDEF"
  surface.dark: "#121A1C"
  surfaceVariant.light: "#EDF3F2"
  surfaceVariant.dark: "#1A2526"
  onBackground.light: "#1B2224"
  onBackground.dark: "#E6EDF0"
  success.light: "#3B9C57"
  success.dark: "#33CC70"
  warning.light: "#C9A23C"
  warning.dark: "#FFB340"
  danger.light: "#C4574A"
  danger.dark: "#FF6B52"
  chart.coverage.dark: "#33D499"
  chart.coverage.light: "#05664A"
  chart.illumination.dark: "#FABF24"
  chart.illumination.light: "#8F5405"
  chart.transmission.dark: "#38BDF7"
  chart.transmission.light: "#0A5B8F"
  selection.light: "#BD523D1F"
  selection.dark: "#FF8C611F"

typography:
  body.mobile: "12-14px sans-serif（Roboto / SF / system）"
  body.desktop: "12px system (SF Pro / Segoe UI)"
  heading.page: "22px bold"
  heading.card: "13-14px semibold"
  caption: "10-11px regular @ 60% onBackground"
  numeric: "monospaced digit（等宽数字，时间/百分比/字节）"

rounded:
  card: 10
  control: 8
  chip: 6

spacing:
  page.padding: 16
  card.padding: 15-20
  element.gap: 6-14
  row.height.list: 46
  button.height: 40-44

motion:
  interactive: 180ms ease-in-out（preflight 展开等 UI 状态切换）
  progress.undetermined: 系统默认转轮（不自制动画）
```

## Colors

- 唯一强调色是暖橙 `brand.warm`：浅色主题用更深的 `#BD523D`（在浅背景上
  保持对比度），深色主题用更亮的 `#FF8C61`。主按钮、选中态、进度强调、
  观测时间轴高亮全部只用它。
- 青绿 `brand.teal` 仅作次级语义（辅助参数、次要 chip），不与暖橙争夺强调。
- 语义色三对：success（就绪/已下载）、warning（未核验/注意）、danger
  （失败/删除）。删除类破坏性按钮用 danger 实底。
- 图表色三组固定语义对：`chart.coverage`（覆盖率）、`chart.illumination`
  （照度）、`chart.transmission`（透过率），dark/light 各自独立取值——
  任何新图表必须复用这三组，禁止新色相。
- 页面底色是三段天空渐变（light：冷青→暖米→浅绿；dark：深青→暗紫→墨绿），
  用作整体氛围而非表面色；卡片表面用 `surface`/`surfaceVariant`。
- 文字对比：正文对背景 ≥ WCAG AA；caption 一律带 60% 不透明度且只放次要
  信息，不得承载可执行操作。

## Typography

- 数值（时间戳、百分比、字节量、坐标）一律等宽字体（monospacedDigit），
  与中文正文混排。
- 页面大标题 22px bold；卡片标题 13-14px semibold；正文 12px（桌面）/
  12-14px（移动）；说明文字 10-11px caption。禁止小于 9px 的文字。
- 全部界面文案为简体中文；英文仅出现在专有名词（GFS / ECMWF / CAMS /
  SRTM / Mock / FFI）。

## Layout

- 桌面侧栏宽 196px、行高 46px；移动端底部导航 + 每页 16px 水平 padding。
- 卡片间距 12-14px；卡片内垂直 10-15px、水平 15-20px。
- 控件高度：主按钮 40-44px、列表行 46px、文本输入 ≥ 36px。
- 长路径/长错误必须可截断（middle truncation）且提供复制；错误文字保持
  可选择（text selection）。

## Elevation

- 移动端卡片用 Material Surface（surfaceVariant 10% 差异），桌面/Web 用
  半透明 surface（0.72 opacity）+ 8-10px 圆角；不使用多重阴影。
- 弹窗（确认/引导）统一平台默认模态，不自制弹窗样式。

## Shapes

- 卡片 10px 圆角、按钮/输入 8px、小 chip 6px；图标按钮最小可点面积 28-30px。

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

- 主按钮：实底 `brand.warm` + 白字（light）或深字（dark）；破坏性按钮
  danger 实底；次级动作 Outlined/borderless。
- 分段选择器（Segmented）：浅色背景 5% 黑、选中项白底（accent 时主题
  色 16%）；用于枚举 ≤4 项。
- 列表行：状态图标（checkmark/exclamation/stop/pause/clock 五态）+ 标题 +
  状态字 + 时间行；行内动作图标按钮 28-30px。
- 卡片标题行右侧放该卡片的唯一主动作；次级动作 borderless。
- 空态：图标 + 一句话说明 + 条件提示（在线/离线口径）。

## Do's and Don'ts

- Do：所有新 UI 先查本文件的 token 值与文案口径；深浅双主题同时验证；
  每屏提供空态/加载/错误三种非理想态。
- Do：跨端文案逐字一致（同一功能同一句话），允许字体度量差异。
- Don't：不引入 brand.warm/teal/三组图表色之外的数据色相；不自制进度
  动画；不用 emoji 承载状态语义；不给 caption 配可点击操作；不隐藏
  错误细节（保持可复制）。
- Don't：不加新的隐藏目录/隐藏设置（可见目录原则，2026-11-20 方案 §8）。
