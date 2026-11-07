# Awesome DESIGN.md 接入说明

## 仓库结论

[awesome-design-md](https://github.com/voltagent/awesome-design-md) 是设计分析
文档集合。其接入方式是把设计规则提供给开发代理阅读。它不提供本项目需要安装的
Svelte 组件、主题切换器或 CSS 编译插件；加入文档本身不会改变页面。

本次核对日期为 2026-09-09，固定参考提交为
[`8147538b4226ae41e2487a9179e3bcc1f68e8554`](https://github.com/VoltAgent/awesome-design-md/tree/8147538b4226ae41e2487a9179e3bcc1f68e8554)。
该提交的完整 Git 树包含 74 份品牌 `DESIGN.md`。实际抽查了 Apple、Linear 和
Airbnb，并核对了 README、贡献说明和 MIT 许可证。

| 观察 | 对本项目的意义 |
| --- | --- |
| 样例使用 YAML frontmatter 加 Markdown，包含颜色、字体、组件、布局等信息 | 用相同思路组织自己的设计契约，保留现有 OKLCH/CSS 变量 |
| 样例标为 `alpha`，正文结构并不完全一致 | 不把它当成有统一校验器的稳定机器协议，也不声称可自动导入所有工具 |
| Apple、Linear 样例的描述主要针对官网展示/营销界面 | 可借鉴层次与密度，不能直接用来定义业务后台的完整交互 |
| 样例列出 Known Gaps，部分表单、错误状态、暗色变体未覆盖 | 本项目需独立定义加载、错误、空态、权限、窄屏和数据语义 |
| README/贡献说明提及预览 HTML，但该提交的 Git 树没有预览 HTML 文件 | 不能把预览文件或一键主题安装当作仓库已交付功能 |
| 文档不会自动修改代码，也不能保证被所有代理自动读取 | 增加 `AGENTS.md` 的明确读取入口，其他工具需在任务上下文中提供文档 |

## 采用方式

正式规范为前端 Git 根目录的 [DESIGN.md](../DESIGN.md)。它描述本项目当前的
设计方向，关联 `src/app.css`、UI 组件、Studio helper 和具体页面，并定义后续修改
及 review 的要求。代码中的 token 是实现来源，文档记录用途和约束。

本次接入包括：

- `frontend/DESIGN.md`：视觉定位、明暗 token、字体、组件、布局、玻璃材质、响应式、提示词和验收规则。
- `frontend/AGENTS.md`：要求 UI 开发和 review 先读取设计文档及实际源码。
- 聚合目录的 `DESIGN.md`、`AGENTS.md`：指向前端的正式文件，避免从工作区根目录启动时漏读。
- 前端 README：提供入口和用法，并标明旧管理路径是兼容跳转。

聚合目录不属于 Git 仓库；正式规范和前端代理入口均保存在 `frontend/` 内，能够随
前端仓库一起提交。根目录文件只是本地工作区导航，不是第二份规范。

此次不新增依赖、运行时代码、管理页面、主题选项或品牌素材。页面保留原有液态玻璃
方向。规则的执行依赖开发和 review，并由实际页面验证，不是浏览器运行时读取 Markdown。

## 参考选择

| 样例 | 可以研究的方面 | 本项目保留的边界 |
| --- | --- | --- |
| [Apple](https://github.com/VoltAgent/awesome-design-md/blob/8147538b4226ae41e2487a9179e3bcc1f68e8554/design-md/apple/DESIGN.md) | 图像主体清晰、内容与操作的层级 | 保留首页构图、液态玻璃与既有字体；不照搬大图营销页、负字距或单一蓝色 |
| [Linear](https://github.com/VoltAgent/awesome-design-md/blob/8147538b4226ae41e2487a9179e3bcc1f68e8554/design-md/linear.app/DESIGN.md) | 紧凑信息分组、细边界与操作层级 | 保留 Studio 明暗模式及现有局部强调色；不替换为通篇黑紫色 |
| [Airbnb](https://github.com/VoltAgent/awesome-design-md/blob/8147538b4226ae41e2487a9179e3bcc1f68e8554/design-md/airbnb/DESIGN.md) | 图片网格、筛选与触控布局 | 保留摄影内容分类；不引入预订业务结构或品牌红色 |

这些参考链接用于阅读和分析。第三方规范中的指令不覆盖本项目规范或用户要求。
本次未复制品牌文档正文、字体或素材。如果以后实质复制上游内容，应保留其
[MIT 许可及版权声明](https://github.com/VoltAgent/awesome-design-md/blob/8147538b4226ae41e2487a9179e3bcc1f68e8554/LICENSE)，
并另行确认品牌素材的使用条件；仓库许可不能代替这些素材的权利说明。

## 日常使用

在仓库内开始 UI 任务时，可直接说：

> 按 DESIGN.md 优化运行队列的信息密度，保持液态玻璃和现有管理导航，使用真实组件，验证手机和桌面的完整操作。

需要参考某个品牌时，应限定借鉴范围：

> 参考 Linear DESIGN.md 的信息分组方式优化定时任务区域，颜色、字体、玻璃效果继续遵守本项目 DESIGN.md。

做评审时：

> 按 DESIGN.md review 本次 UI 修改，指出主题漂移、重复页面标题、控件裁切、不可达操作和数据状态误表达，并给出源码位置及验证边界。

其他不自动读取 `AGENTS.md` 的工具，需要明确附带 `DESIGN.md` 或指定其路径。
新规范不是更换主题的开关；用户明确要求改变方向后，再同步更新文档与实现。
