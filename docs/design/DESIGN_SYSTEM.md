# DocMind 设计系统（v1 · 2026-11）

面向 DocMind 产品 UI（文档问答 + 合同核验）的执行规范。它不是组件库文档，而是写代码前必须遵守的决策与验收清单。

## 定位

- **产品性格**：Precision & Density（精确、克制、信息密度优先）。
- **视觉世界**：纸 + 墨 + 一个强调色。画布是中性纸面，结构由灰度与边框建立，颜色只表达动作与状态。
- **模式**：Preserve（保留现有信息架构、路由、表单与品牌资产，逐批收敛视觉与可访问性）。
- **明确拒绝**：紫色系（purple/violet/indigo/fuchsia/pink）、装饰性渐变、玻璃拟态、超大圆角、emoji 当图标、彩虹 KPI、只用颜色表达状态。

参考来源（只取规则，不复制代码）：

- [Dammyjay93/interface-design](https://github.com/Dammyjay93/interface-design)（MIT）：信息密度、深度策略、状态与工艺检查；本地 vendor 于 `_scratch/vendor/interface-design`。
- [Ant Design DESIGN.md](https://github.com/ant-design/ant-design/blob/master/DESIGN.md)（MIT）：14px 基准、4px 网格、32px 控件、6/8px 圆角、400/600 字重。
- [IBM Carbon](https://carbondesignsystem.com/)（Apache-2.0）：空态/错误态、数据表格、筛选模式、数据可视化色板。
- [Vercel Web Interface Guidelines](https://github.com/vercel-labs/web-interface-guidelines)（MIT）：URL 状态、焦点可见、图标按钮可访问名、状态不只靠颜色、移动端 44px。
- [garden-skills web-design-engineer](https://github.com/ConardLi/garden-skills)（MIT）：Design Read、redesign 协议、反 AI 陈词滥调；本地 vendor 于 `_scratch/vendor/garden-skills`。

调研快照：`_scratch/wt-ui5/.scratch/ui-research/`（同类产品界面与截图索引）、`_scratch/vendor/interface-design`。

## Tokens

### 颜色

| 角色 | 值 | 用途 |
|---|---|---|
| action / brand | `blue-600 #2563eb` | 唯一动作色：主按钮、链接、选中态、焦点环 |
| action-strong | `blue-700 #1d4ed8` | 品牌标识底色、深色强调 |
| ink | `slate-900 #0f172a` | 主文字、深色主按钮（登录） |
| secondary | `slate-600 #475569` | 次级文字 |
| muted | `slate-500 #64748b` | 元信息（白底 ≥4.5:1） |
| faint | `slate-400 #94a3b8` | 仅深色底或非信息性图形 |
| border | `rgba(15, 23, 42, 0.08–0.12)` | 结构边框（低对比） |
| success | `emerald-700 #047857` | 成功状态文字 |
| warning | `amber-700 #b45309` | 警告状态文字 |
| danger | `red-600 #dc2626` | 危险/阻断状态文字 |

规则：

1. **一个动作色**。彩色只用于动作与语义状态；中性色建立结构。
2. **状态三重表达**：文字 + 图标/形状 + 颜色，不允许只靠颜色。
3. **图表分类色与状态色分离**，分类序列 ≤6 色，且不包含紫/品红。
4. 白底信息文字 ≥4.5:1；大字与 UI 边界 ≥3:1。

### 间距与尺寸

- 4px 网格：4 / 8 / 12 / 16 / 24 / 32 / 48。
- 控件高度：compact 28 / default 32 / large 40；触控主操作 44×44。
- 表格行高：紧凑 32 / 默认 40；数字列 `font-variant-numeric: tabular-nums`。
- 圆角阶梯：控件 6 / 卡片 8 / 弹层 10；不再新增 16px 以上的工作台卡片圆角。
- 深度策略：**borders-first**。工作台与数据界面用 1px 低对比边框分层，不叠加彩色大阴影；营销面（登录品牌区）可保留氛围光，但不进入产品工作台。

### 字体

- 字体栈：系统 UI 栈（`-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif`），不引入第二套展示字体。
- 字号阶梯（14px 基准）：11（caption）/ 12（meta）/ 13（compact）/ 14（body）/ 16（h4）/ 18（h3）/ 22（h2）/ 28（h1）/ 44+（display）。
- 字重只用 400 / 500 / 600；层级优先用字重、颜色与留白，而不是继续放大字号。
- 标题 `text-wrap: balance`，正文 `text-wrap: pretty`；长文本必须 `truncate` / `line-clamp` / `break-words`。

## 交互与状态

- 每个交互元素必须定义 default / hover / active / focus-visible / disabled；数据区块必须定义 loading / empty / error。
- 焦点环：全局 2px `#2563eb` + 2px offset；sticky 头尾不得遮挡焦点。
- 动效 <300ms，只动 transform/opacity；`cubic-bezier(0.23, 1, 0.32, 1)`；尊重 `prefers-reduced-motion`。
- 空态回答三件事：为什么空、能做什么、下一步点哪里；空表不渲染表头。
- 错误态给出失败对象、可能原因、恢复路径，禁止只有「加载失败 + 重试」。
- 列表筛选/分页/展开态写入 URL；整行点击必须同时支持键盘（链接或 Enter/Space）。
- 破坏性操作必须确认或可撤销。
- 移动端：375 起测；主操作 ≥44px；输入字号 ≥16px；固定底栏处理 safe-area。

## 页面级约束

| 页面 | 必须遵守 |
|---|---|
| 登录 | 品牌区可用氛围渐变；表单卡片为纸面 + 细边框；动作按钮为墨色或品牌蓝，不使用彩虹渐变 |
| Chat | 回答必须带来源；来源卡可定位原文（后续）；流式区域 `aria-live`；输入区移动端不溢出 |
| Agent | 无 emoji 图标；工具数读 store；计划/审批/回放状态可见；审批文案说明批准与拒绝的后果 |
| 知识库 | 表格数字列 `tabular-nums` + 右对齐；空态替换表头；筛选进 URL |
| 合同核验 | 结论先行（结论横幅 + 计数 + 真实进度）；核验项条款锚定；证据可定位；三栏工作台为后续目标 |
| Dashboard | KPI 卡中性面 + 单一强调色；禁止彩虹渐变；图表走统一 chartTheme，深色模式可读 |

## 验收（提交前自检）

1. `npm run check:no-purple` 通过：按色相扫描源码中的十六进制/RGB 颜色（hue 245–335、饱和度 >15%）与动态 HSL，并拦截 purple/violet/indigo/fuchsia/pink 关键词。
2. 新增文字对比度：白底 ≥4.5:1（可用 #2563eb / #047857 / #b45309 / #dc2626 / #64748b）。
3. 键盘可完成主流程；焦点环可见且不被遮挡；图标按钮有 `aria-label`。
4. 375 / 768 / 1440 无意外横向滚动；触控目标 ≥44px。
5. 无 emoji 充当图标；无装饰性渐变；状态不只用颜色。
6. 数字列等宽；空态/错误态不被遗漏。

## 与现状的差距（按批收口）

- 工作台页面仍混用 `rounded-3xl`、`backdrop-blur`、彩色阴影（Chat 侧栏/头部、登录卡、Dashboard 横幅与 KPI）；Reviews/Dashboard 批次按 borders-first 收敛。
- Dashboard 6 张 KPI 卡仍是彩虹渐变（org 头像与图表分类色已去紫）。
- 全站仍有约 169 处 `text-gray-400/slate-400` 信息文字需按背景逐一提升对比度。
- Agent 组件内仍有硬编码中文待接 i18n。
