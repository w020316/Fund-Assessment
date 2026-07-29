# QuantFlow Pro 前端设计系统规范（规划文档）

> 文档日期：2026-07-29
> 文档性质：**规划文档（未实施）**，仅作为前端设计规范沉淀与后续重构依据，不立即落地到代码。
> 数据来源：`web/static/index.html` 中 `<style>` 段落实际 CSS 变量与组件样式提取。
> 当前主题：**暗色主题（Dark Only）**，无明色模式切换。

---

## 设计原则

1. **数据密度优先**：金融数据型应用，优先在单位面积内呈现更多信息，而非留白。
2. **状态语义化**：涨跌、风险、健康度等状态必须通过颜色 + 文字双通道表达，不依赖单一颜色。
3. **克制使用强调色**：仅主操作、激活态、关键指标使用强调色，避免色彩噪音。
4. **稳定优先**：动效服务于"感知响应"，不干扰阅读；加载态采用静态占位，避免扫光等躁动动画。

---

## A. 色彩系统

### A.1 主色（Primary）

| 角色 | 变量 | 色值 | 使用场景 | 占比规范 |
| --- | --- | --- | --- | --- |
| 主色 | `--accent` | `#f59e0b`（琥珀 Amber） | 主按钮背景、激活导航左边框、Logo 图标背景、链接强调、温度计高位段 | 单屏可见面积 ≤ 8% |
| 主色-hover | `--accent-hover`（建议补全） | `#d97706` | 主按钮悬浮态 | — |

**说明**：
- 当前 `--accent-blue` 与 `--accent-purple` 变量名虽然含 "blue/purple"，但实际色值均为 `#f59e0b`（与主色相同），属于**命名误导**，建议在重构时统一为 `--accent` 单一变量，或赋予真实差异色值。
- 主色在暗色背景上对比度足够（WCAG AA ≥ 4.5:1 对浅色文字），用于文字时建议搭配 `#0d1117` 深色文字（如 Logo 图标内文字）。

### A.2 辅助色（Secondary）

| 角色 | 变量 | 色值 | 使用场景 |
| --- | --- | --- | --- |
| 辅助色-青 | `--accent-cyan` | `#06b6d4` | 数据可视化辅助、次级强调（当前实际使用较少） |
| 辅助色-蓝（建议补全） | `--color-info` | `#3b82f6` | 信息提示、Info 类 Toast 边框、信息类预警 |

**说明**：当前项目辅助色体系不完整，`--accent-cyan` 定义后使用场景有限，建议规划时明确"主色 + 1 个辅助色"的双色体系，避免色彩冗余。

### A.3 中性色（Neutral）

#### A.3.1 背景层级（5 级，由深到浅）

| 层级 | 变量 | 色值 | 使用场景 |
| --- | --- | --- | --- |
| L1 最深层 | `--bg-primary` | `#0d1117` | 页面主背景、输入框背景、最底层画布 |
| L2 卡片层 | `--bg-card` | `#161b22` | 卡片背景、顶栏背景、侧边栏背景、Modal 背景 |
| L3 悬浮层 | `--bg-card-hover` | `#1c2330` | 卡片悬浮态（建议） |
| L4 半透明叠加层 | — | `rgba(255,255,255,0.03)` | 表头背景、Tab 容器背景、表格斑马纹 |
| L5 弱半透明叠加层 | — | `rgba(255,255,255,0.02)` | 表格行 hover、分析师卡片背景、新闻滚动背景 |

**说明**：L4/L5 当前通过 `rgba(255,255,255,0.0x)` 半透明白色实现叠加效果，依赖底层背景色。建议重构时补充显式变量 `--bg-elevated-1` / `--bg-elevated-2` 以解耦。

#### A.3.2 文字层级（3 级）

| 层级 | 变量 | 色值 | 使用场景 | 对比度（vs L1 背景） |
| --- | --- | --- | --- | --- |
| T1 主文字 | `--text-primary` | `#e6edf3` | 标题、正文、表格主数据、按钮深色文字 | ≈ 13.6:1（AAA） |
| T2 次文字 | `--text-secondary` | `#8b949e` | 表头、标签、说明文字、导航默认态 | ≈ 4.7:1（AA） |
| T3 弱文字 | `--text-muted` | `#6e7681` | 辅助提示、时间戳、版本号、占位符 | ≈ 3.0:1（仅用于 ≥14px 非关键信息） |

#### A.3.3 边框 / 分割线

| 角色 | 变量 | 色值 | 使用场景 |
| --- | --- | --- | --- |
| 标准边框 | `--border` | `#30363d` | 卡片边框、表格行分割线、Modal 头部分割 |
| 浅边框 | `--border-light` | `#484f58` | 输入框边框、Outline 按钮边框、滚动条 thumb |
| 半透明分割线 | — | `rgba(0,0,0,0.6)` | Modal 遮罩 |
| 半透明强调边框 | — | `rgba(239,68,68,0.15)` / `rgba(34,197,94,0.15)` | 辩论面板多空两侧边框 |

### A.4 语义色

#### A.4.1 涨跌色（金融场景，红涨绿跌，符合 A 股习惯）

| 角色 | 变量 | 色值 | 使用场景 |
| --- | --- | --- | --- |
| 涨 / 红 | `--color-up` | `#ef4444` | 上涨数值、买入信号、多头辩论、危险按钮、健康错误态 |
| 跌 / 绿 | `--color-down` | `#22c55e` | 下跌数值、卖出信号、空头辩论、成功按钮、健康正常态、连接状态点 |
| 平 / 中性 | `--color-flat` | `#94a3b8` | 持平数值、持有信号、中性情绪、温度计中段 |

**说明**：当前 `--color-critical` 与 `--color-up` 色值相同（均为 `#ef4444`），语义复用，建议保留以备未来差异化（如严重错误使用更深红色 `#dc2626`）。

#### A.4.2 状态语义色

| 角色 | 变量 | 色值 | 使用场景 |
| --- | --- | --- | --- |
| 信息 | `--color-info` | `#3b82f6` | 信息类预警边框、Info 类 Toast 边框 |
| 警告 | `--color-warning` | `#f59e0b` | 警告类预警、温度计高位、校验失败提示文字 |
| 严重 / 错误 | `--color-critical` | `#ef4444` | 严重预警、健康错误态（与涨色复用） |

#### A.4.3 Toast 反馈色（深色变体，用于通知组件）

| 角色 | 背景 | 文字 | 边框 |
| --- | --- | --- | --- |
| 成功 | `#065f46` | `#6ee7b7` | `#059669` |
| 错误 | `#7f1d1d` | `#fca5a5` | `#dc2626` |
| 信息 | `#1e3a5f` | `#93c5fd` | `#2563eb` |

### A.5 暗色模式色板（当前为唯一主题，完整色值汇总）

```css
:root {
  /* 背景层级 */
  --bg-primary: #0d1117;      /* L1 最深层 */
  --bg-card: #161b22;         /* L2 卡片层 */
  --bg-card-hover: #1c2330;   /* L3 悬浮层 */

  /* 边框 */
  --border: #30363d;
  --border-light: #484f58;

  /* 文字层级 */
  --text-primary: #e6edf3;    /* T1 */
  --text-secondary: #8b949e;  /* T2 */
  --text-muted: #6e7681;      /* T3 */

  /* 主色 / 辅助色 */
  --accent: #f59e0b;
  --accent-blue: #f59e0b;     /* 命名误导，实为主色 */
  --accent-cyan: #06b6d4;
  --accent-purple: #f59e0b;   /* 命名误导，实为主色 */

  /* 语义色 */
  --color-up: #ef4444;        /* 涨/红 */
  --color-down: #22c55e;      /* 跌/绿 */
  --color-flat: #94a3b8;      /* 平 */
  --color-info: #3b82f6;
  --color-warning: #f59e0b;
  --color-critical: #ef4444;

  /* 圆角 / 阴影 / 过渡 */
  --radius: 8px;
  --radius-lg: 12px;
  --shadow: 0 4px 24px rgba(0,0,0,0.3);
  --transition: all 0.2s ease;
}
```

#### A.5.1 明色模式色板（规划建议，当前未实现）

> 以下为未来支持明色模式时的推荐色值，**当前未实施**。

| 角色 | 暗色（当前） | 明色（规划） |
| --- | --- | --- |
| `--bg-primary` | `#0d1117` | `#ffffff` |
| `--bg-card` | `#161b22` | `#f6f8fa` |
| `--bg-card-hover` | `#1c2330` | `#eaeef2` |
| `--border` | `#30363d` | `#d0d7de` |
| `--border-light` | `#484f58` | `#afb8c1` |
| `--text-primary` | `#e6edf3` | `#1f2328` |
| `--text-secondary` | `#8b949e` | `#59636e` |
| `--text-muted` | `#6e7681` | `#818b98` |
| `--accent` | `#f59e0b` | `#d97706`（明色模式加深以保证对比度） |

---

## B. 排版规范

### B.1 字体家族

| 用途 | 字体栈 | 说明 |
| --- | --- | --- |
| 标题 / 正文（默认） | `-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif` | 系统字体优先，跨平台一致；中文走苹方/雅黑 |
| 数字（规划建议） | `'SF Mono', 'JetBrains Mono', Consolas, 'Roboto Mono', monospace` | 当前未独立定义数字字体，建议表格数值采用等宽字体以利于对齐 |
| 代码（规划建议） | `'SF Mono', 'JetBrains Mono', Consolas, monospace` | 同上 |

### B.2 字体层级

| 层级 | Token | 字号 | 字重 | 行高 | 使用场景 |
| --- | --- | --- | --- | --- | --- |
| H1 | `.thermo-score` | 32px | 700 | — | 温度计分数（仅特殊指标） |
| H2 | — | 24px | 700 | — | 可用现金等关键数字 |
| H3 | `.decision-label` | 22px | 700 | 1.0 | 决策卡片标签（买入/卖出/持有） |
| H4 | `.logo` | 18px | 700 | — | 顶部 Logo |
| H5 | `.card-title` / `.modal-header` | 15px | 600 | — | 卡片标题、Modal 标题、指数价格 |
| H6 | `.nav-item` / body | 14px | 400/500 | 1.6 | 导航项、正文 |
| Body | `body` | 14px | 400 | 1.6 | 默认正文 |
| Body-sm | `.btn` / `.input` / `.tab` / `td` | 13px | 400/500 | — | 按钮、输入框、表格、Tab |
| Caption | `th` / `.form-label` / `.signal` | 12px | 500/600 | — | 表头、表单标签、信号标签 |
| Caption-xs | hint / `.data-quality-badge` | 11px | 500/600 | — | 辅助提示、徽章 |

### B.3 行高 / 字重 / 字间距规范

| 属性 | 规范 | 说明 |
| --- | --- | --- |
| 行高（正文） | `1.6` | 当前 `body` 默认值，保证中文可读性 |
| 行高（紧凑） | `1.5` | 用于卡片内 12px 小字（如 `.analyst-opinion`） |
| 行高（标题） | `1.2` | 建议大号标题（≥22px）采用，当前未显式定义 |
| 字重-常规 | `400` | 正文 |
| 字重-中等 | `500` | 按钮、表头、Tab、导航默认 |
| 字重-半粗 | `600` | 卡片标题、Modal 标题、激活态文字 |
| 字重-粗体 | `700` | Logo、温度计分数、决策标签 |
| 字间距 | `0.3px`（表头）/ `0.5px`（徽章、Logo 图标）/ `1px`（决策标签、辩论 VS） | 仅用于大写或紧凑场景 |

---

## C. 组件设计

### C.1 按钮（Button）

#### 类型

| 类型 | 类名 | 背景 | 文字 | 边框 | 使用场景 |
| --- | --- | --- | --- | --- | --- |
| Primary | `.btn-primary` | `var(--accent)` `#f59e0b` | `#0d1117` | 无 | 主操作（检索、分析、确认） |
| Success | `.btn-success` | `var(--color-down)` `#22c55e` | `#fff` | 无 | 买入、成功操作 |
| Danger | `.btn-danger` | `var(--color-up)` `#ef4444` | `#fff` | 无 | 卖出、删除、危险操作 |
| Outline | `.btn-outline` | `transparent` | `var(--text-secondary)` | `1px solid var(--border-light)` | 次要操作（测试通知、取消） |
| Secondary（规划建议） | `.btn-secondary`（未实现） | `var(--bg-card-hover)` | `var(--text-primary)` | `1px solid var(--border)` | 次要操作，建议补充 |
| Ghost（规划建议） | `.btn-ghost`（未实现） | `transparent` | `var(--text-secondary)` | 无 | 极弱操作（图标按钮） |

#### 尺寸

| 尺寸 | 类名 | padding | font-size | 说明 |
| --- | --- | --- | --- | --- |
| 默认 | `.btn` | `8px 20px` | 13px | 标准操作按钮 |
| 小 | `.btn-sm` | `6px 12px` | 12px | 表格内操作、紧凑区域 |
| 大（规划建议） | `.btn-lg`（未实现） | `10px 28px` | 15px | 主流程 CTA，建议补充 |

#### 状态

| 状态 | 表现 | 说明 |
| --- | --- | --- |
| 默认 | 见类型表 | — |
| Hover | Primary：背景变 `#d97706`；Outline：边框/文字变主色；Success/Danger：`opacity:0.9` | 当前 Success/Danger 用透明度处理，建议补充显式 hover 色值 |
| Active / 按压 | 未显式定义 | 建议补充 `transform: translateY(1px)` 或 `filter: brightness(0.95)` |
| Disabled | 未显式定义 | 建议补充 `opacity: 0.5; cursor: not-allowed;` |
| Focus | 未显式定义 | 建议补充 `outline: 2px solid var(--accent-cyan); outline-offset: 2px;` 以满足无障碍 |

### C.2 表单（Form）

#### 输入框 / 下拉框

| 元素 | 类名 | 规格 |
| --- | --- | --- |
| Input | `.input` | `background: var(--bg-primary); border: 1px solid var(--border); border-radius: 6px; padding: 8px 12px; color: var(--text-primary); font-size: 13px; width: 100%;` |
| Select | `.select` | 同 Input，附加 `cursor: pointer` |
| Form group | `.form-group` | `margin-bottom: 14px` |
| Form label | `.form-label` | `font-size: 12px; color: var(--text-secondary); font-weight: 500; margin-bottom: 6px;` |

#### 校验态（规划建议，当前未实现）

| 状态 | 表现（建议） |
| --- | --- |
| 默认 | 边框 `var(--border)` |
| Focus | 边框 `var(--accent-blue)`（当前已实现） |
| 校验成功 | 边框 `var(--color-down)`，右侧绿色对勾图标 |
| 校验失败 | 边框 `var(--color-up)`，下方 12px 红色错误文字 |
| 禁用 | `opacity: 0.5; cursor: not-allowed;` |

### C.3 卡片（Card）

| 类型 | 类名 | 规格 |
| --- | --- | --- |
| 标准卡片 | `.card` | `background: var(--bg-card); border: 1px solid var(--border); border-radius: var(--radius-lg) 12px; padding: 20px;` hover：`border-color: var(--border-light)` |
| 带标题卡片 | `.card` + `.card-title` | 标题 15px/600，左侧带 3px×16px 主色装饰条（`::before`） |
| 紧凑卡片（规划建议） | `.card-compact`（未实现） | `padding: 12px; border-radius: var(--radius) 8px;` |
| 分析师卡片 | `.analyst-card` | `padding: 10px 12px; border: 1px solid var(--border); border-radius: 8px; background: rgba(255,255,255,0.02);` |
| 决策卡片 | `.decision-card` | `text-align: center; padding: 24px; border-radius: var(--radius-lg); border: 2px solid;` 三种变体：buy（红边）/sell（绿边）/hold（灰边） |

### C.4 表格（Table）

| 特性 | 实现 | 说明 |
| --- | --- | --- |
| 斑马纹 | `tr:nth-child(even) td { background: rgba(255,255,255,0.01); }` | 极弱半透明，暗色背景下几乎不可见，建议加深至 `0.03` |
| 固定表头 | `th { position: sticky; top: 0; }` | 已实现，表头背景 `rgba(255,255,255,0.03)` |
| 行 hover | `tr:hover td { background: rgba(255,255,255,0.02); }` | 已实现 |
| 表头样式 | `font-size: 12px; font-weight: 500; letter-spacing: 0.3px; color: var(--text-secondary);` | — |
| 单元格样式 | `padding: 10px 12px; border-bottom: 1px solid var(--border); font-size: 13px;` | — |
| 横向滚动 | `.table-wrap { overflow-x: auto; }` | 已实现 |
| 纵向滚动 | `.table-wrap { max-height: 400px; overflow-y: auto; }` | 内联样式按需添加 |

### C.5 反馈（Feedback）

#### Toast

| 属性 | 值 |
| --- | --- |
| 容器 | `.toast-container`：`position: fixed; top: 60px; right: 20px; z-index: 9999;` 纵向排列，间距 8px |
| 单条 | `.toast`：`padding: 12px 20px; border-radius: 8px; font-size: 13px; font-weight: 500; min-width: 240px; box-shadow: var(--shadow);` |
| 动画 | `toast-in 0.3s ease`（从右侧 40px 滑入 + 淡入） |
| 变体 | success / error / info（色值见 A.4.3） |
| 持续时间 | JS 控制，建议规范：success/info 3s，error 5s |

#### Modal

| 属性 | 值 |
| --- | --- |
| 遮罩 | `.modal-mask`：`position: fixed; inset: 0; background: rgba(0,0,0,0.6); z-index: 9998;` 居中布局 |
| 容器 | `.modal-box`：`background: var(--bg-card); border: 1px solid var(--border); border-radius: var(--radius-lg); box-shadow: var(--shadow);` |
| 头部 | `.modal-header`：`padding: 14px 20px; border-bottom: 1px solid var(--border); font-size: 15px; font-weight: 600;` |
| 关闭按钮 | `.modal-close`：`font-size: 22px; color: var(--text-muted);` hover 变 `var(--text-primary)` |
| 内容区 | `.modal-body`：`padding: 20px` |
| 动画 | **未实现**，建议补充淡入 + 轻微缩放（`scale(0.96) → 1`，0.2s ease） |

#### Skeleton（骨架屏，规划建议）

> 当前**未实现**，仅 `.pulse-loading { position: relative; }` 静态占位。

| 属性 | 建议值 |
| --- | --- |
| 背景 | `linear-gradient(90deg, var(--bg-card) 25%, var(--bg-card-hover) 50%, var(--bg-card) 75%); background-size: 200% 100%;` |
| 动画 | `skeleton-shimmer 1.5s infinite;`（左→右扫光） |
| 圆角 | 与目标组件一致（卡片 12px、文本行 4px） |

> 注：项目原则为"加载态静态占位、无扫光"，Skeleton 是否引入需产品决策。

#### Loading 文案

| 场景 | 当前文案 |
| --- | --- |
| AI 检索 | `AI 检索中（Tavily + LLM 总结，约 10-20 秒）...` |
| 五信号融合 | `五信号融合分析中...` |

建议规范：`<动作>中（预计 <时长>）...`，避免暴露技术栈细节（如 Tavily/LLM）给终端用户。

---

## D. 响应式布局规则

### D.1 断点

| 断点 | 宽度 | 目标设备 | 当前实现 |
| --- | --- | --- | --- |
| xs | `≤ 375px` | 小屏手机 | **未显式处理**，依赖 768px 断点回退 |
| sm | `≤ 768px` | 平板竖屏 / 手机 | 已实现：侧边栏隐藏、网格单列、底部 Tab 显示 |
| md | `≤ 1024px` | 平板横屏 / 小笔电 | 已实现：grid-4→2列、grid-3→2列、辩论面板单列 |
| lg | `≤ 1440px` | 标准桌面 | **未显式处理**，默认布局适用 |
| xl | `> 1440px` | 宽屏桌面 | **未显式处理**，建议补充内容区最大宽度限制 |

> 当前仅 2 个断点（768px / 1024px），建议补全 375px 与 1440px 断点。

### D.2 栅格规范

| 类名 | 默认列数 | 1024px | 768px | 间距 |
| --- | --- | --- | --- | --- |
| `.grid-2` | 2 列 | 2 列 | 1 列 | 16px |
| `.grid-3` | 3 列 | 2 列 | 1 列 | 16px |
| `.grid-4` | 4 列 | 2 列 | 1 列 | 16px |
| `.analyst-grid` | `auto-fit, minmax(220px, 1fr)` | 自适应 | 自适应 | 10px |
| `.debate-panel` | `1fr auto 1fr` | `1fr`（堆叠） | `1fr`（堆叠） | 16px |

**栅格容器**：`.content { flex: 1; overflow-y: auto; padding: 20px; }`，768px 断点下 padding 收窄至 12px。

### D.3 导航适配

| 断点 | 顶栏 | 侧边栏 | 底部 Tab | 指数条 |
| --- | --- | --- | --- | --- |
| 默认（>1024px） | 52px 高，Logo + 指数条 + 右侧状态 | 180px 宽，左侧固定 | 隐藏 | 显示 |
| ≤ 1024px | 同上 | 显示 | 隐藏 | 显示 |
| ≤ 768px | 同上 | **隐藏** | **显示**（`.bottom-tab-bar`） | **隐藏** |

**说明**：移动端通过底部 Tab 替代侧边栏导航，内容区底部预留 70px padding 避免遮挡。

### D.4 布局结构

```
.app (flex column, 100vh)
├── .topbar (52px, 固定)
├── .main-layout (flex row, flex:1)
│   ├── .sidebar (180px, ≥768px 显示)
│   └── .content (flex:1, padding:20px)
└── .bottom-tab-bar (≤768px 显示, 固定底部)
```

---

## E. 交互规范

### E.1 动画时长

| 时长 | 用途 | 示例 |
| --- | --- | --- |
| `0.2s` | 微交互（默认过渡） | `--transition: all 0.2s ease;` 适用于 hover、focus、边框/颜色变化 |
| `0.3s` | 入场动画 | Toast 滑入（`toast-in 0.3s ease`） |
| `0.6s` | 数据变化过渡 | 温度计指针移动（`left 0.6s ease`）、进度条填充（`width 0.6s ease`） |
| `2s` | 状态脉冲 | 连接状态点闪烁（`pulse-dot 2s infinite`） |
| `30s` | 长循环动画 | 新闻滚动（`ticker-scroll 30s linear infinite`） |

### E.2 缓动函数

| 缓动 | 用途 | 说明 |
| --- | --- | --- |
| `ease` | 默认微交互 | `--transition` 使用，适合 hover/边框/颜色 |
| `ease` | 入场动画 | Toast 使用 |
| `linear` | 循环动画 | 新闻滚动使用，保证匀速 |
| `cubic-bezier(0.4, 0, 0.2, 1)`（规划建议） | Material 标准缓动 | 建议用于 Modal 入场、卡片展开等场景，当前未使用 |

### E.3 状态规范

| 状态 | 交互表现 | 视觉表现 |
| --- | --- | --- |
| 悬浮（Hover） | 鼠标移入 | 边框变浅 / 背景叠加半透明白 / 文字变亮 / 主色按钮变深（`#d97706`） |
| 按压（Active） | 鼠标按下 | **未显式定义**，建议 `transform: translateY(1px)` 或 `filter: brightness(0.95)` |
| 禁用（Disabled） | 不可点击 | **未显式定义**，建议 `opacity: 0.5; cursor: not-allowed; pointer-events: none;` |
| 聚焦（Focus） | 键盘导航 | 输入框边框变主色（已实现）；按钮无 focus 样式，**建议补充 outline** |
| 激活（Active 态） | 当前选中 | 导航项：左边框 3px 主色 + 文字主色 + 背景半透明主色（`rgba(245,158,11,0.08)`）；Tab：背景主色 + 白字 |
| 加载（Loading） | 数据请求中 | 静态文字占位（如"分析中..."），**无扫光/旋转动画**（项目原则） |

### E.4 加载态规范

| 场景 | 当前方式 | 规范建议 |
| --- | --- | --- |
| 卡片数据加载 | 文字占位（"分析中..."） | 保留文字占位；如需更优体验，可引入 Skeleton（见 C.5） |
| 表格数据加载 | 无显式处理 | 建议表体显示 `<tr><td colspan=N>加载中...</td></tr>` |
| 按钮请求中 | 无显式处理 | 建议按钮文字变更为"处理中..."并 `disabled`，防止重复提交 |
| 全局加载 | 无 | 当前无全局 loading 指示器，建议在顶栏添加进度条 |

### E.5 光标规范

| 元素 | cursor |
| --- | --- |
| 按钮、导航项、Tab、可点击项 | `pointer` |
| 输入框、文本域 | `text` |
| 下拉框 | `pointer`（`.select { cursor: pointer; }`） |
| 禁用元素 | `not-allowed`（建议补充） |
| 默认 | `default` |

---

## 附录：当前实现差距汇总

以下为对照本规范后，当前 `index.html` 实现中存在的差距，供后续重构参考（**不在本规划文档范围内实施**）：

1. **变量命名误导**：`--accent-blue` / `--accent-purple` 实际色值为主色 `#f59e0b`，建议统一或赋予真实差异色值。
2. **辅助色体系不完整**：仅 `--accent-cyan` 独立，缺乏成体系的辅助色定义。
3. **按钮类型缺失**：无 Secondary / Ghost / Large 变体；Success/Danger 的 hover 仅用透明度，无显式色值。
4. **按钮状态缺失**：无 active / disabled / focus 显式样式。
5. **表单校验态缺失**：仅 focus 态有边框反馈，无成功/失败/禁用态。
6. **断点不全**：仅 768px / 1024px 两个断点，缺 375px / 1440px。
7. **明色模式未实现**：当前为暗色单主题。
8. **数字字体未独立**：表格数值使用正文字体，未采用等宽字体对齐。
9. **Modal 无入场动画**：仅 Toast 有动画，Modal 直接显示。
10. **Skeleton 未实现**：加载态仅文字占位。
11. **斑马纹过弱**：`rgba(255,255,255,0.01)` 在暗色背景下几乎不可见。
12. **按压/禁用/聚焦态不完整**：按钮缺乏 active/disabled/focus 样式。

---

*文档结束。本规范基于 `web/static/index.html` 当前实现提取，作为前端设计沉淀与后续重构依据。*
