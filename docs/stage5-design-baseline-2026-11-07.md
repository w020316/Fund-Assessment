# 阶段五 · 8 维度设计基线审计报告（2026-11-07）

审计人：高见远（架构师）· 团队 software-stage5-design
审计范围：`web/static/index.html`（8172 行，内联样式约 960 行）、`design-system/`（tokens.css / components.css / prototype.html / style-validation.html）、`web/static/css/`（hud.css / aurora-theme.css / stage6.css）、`web/static/js/`（5 个文件）。对比度数值为实际计算结果（WCAG 相对亮度公式）。

---

## 一、8 维度基线评分表

| 维度 | 基线分 | 主要依据 | 与 9 分标准的差距 |
|---|---|---|---|
| 1. 排版设计 | 5.5 | Aurora 引入 display/body/mono 三族字体与 15 级字阶（tokens.css:112-128），字距在 components.css 有 13 处；但存量侧 index.html 全部为 px 绝对单位、无任何流式字阶，font-size 分布严重失衡（12px×178、11px×122、13px×81，即 381 处 ≤13px，含 5 处 10px），letter-spacing 仅 6 处、line-height 仅 21 处（多数依赖全局 1.6 默认） | 无 rem/clamp 流式排版、无字阶工具类落地到存量、10px 违反可读下限、数字未全量 tabular-nums（部分组件有，如 aurora-theme.css:135） |
| 2. 留白运用 | 5.0 | tokens.css 定义了完整 8px 栅格（0/8/16/24/32/40/48/64/80/96 + 唯一 4px 子栅格），aurora-theme 接入层遵守（16/20/24/32）；但存量侧间距值散乱：padding:10px×47、14px×4、6px×17、3px×10、5px×4、1px×8，未走任何倍数体系；无宏观分区留白（页面 section 间距无节奏） | 8px 栅格只在 token 文件里"存在"，未注入存量；间距是"逐处手拍"而非"体系推导" |
| 3. 视觉层级 | 6.0 | IA 已 10→5 聚合且有 STAGE5_PAGE_MAP 兼容映射；卡片标题有渐变竖标（aurora-theme.css:181）；涨跌/徽章语义清晰；但 1016 处内联 style 削弱层级控制力，z-index 分布为 0/1/100/200/300/1000/9998/9999/10000 共 9 档随意值（index.html 内），而 tokens.css:103-109 已定义 --qf-z-* 6 档却未被存量引用 | 层级靠临时数字而非 token；大量 JS 模板内联样式使"核心内容优先级"无法全局调度 |
| 4. 色彩系统 | 6.5 | 暗色（当前默认 hud-theme+Aurora）实测全过 AA：正文 16.63、次级 9.39、弱化 6.72、涨 #F87171 6.73、跌 #34D399 9.68、按钮深字/青底 12.96、蓝端最低 5.26；但①亮色回退 FAIL：弱化 #6e7681/卡片 3.77、三级 #586069 2.71、白字/琥珀 logo 2.15；②index.html 内 96 处硬编码 hex、js 目录 84 处；③token 三处重复定义（tokens.css / aurora-theme.css:19-91 / hud.css:16-53，且 hud.css 的 --hud-accent #22D3EE 与 Aurora #22E3FF 是近似但不同的两个青色）；④meta theme-color #0d1117 与 Aurora 底 #04070E 不一致 | 亮色回退不达标违反"必须有亮色回退"约定；token 真源不唯一 |
| 5. 动效设计 | 6.0 | 时长硬约束 200–500ms 已 token 化（--qf-dur-1..5），4 条标准贝塞尔曲线定义完整；reduced-motion 三处覆盖（stage6.css:351 全局、aurora-theme.css:385、hud.css:152）；但①index.html:786 skeleton-shimmer 动画 `left` 属性（非合成器属性，掉帧风险，Aurora 版 qf-shimmer 用 background-position 是正确写法却未替换存量）；②index.html:444 ticker-scroll 30s linear infinite 无暂停机制；③tabActivePulse（index.html:770）纯装饰无限脉冲，目的性不足；④17 处 transition 里普遍 `all`；全库 0 处 will-change | 无入场编排/滚动驱动动效；`left` 动画与 60fps 硬标准冲突 |
| 6. 微交互 | 6.5 | :active scale(.98/.94)（aurora-theme.css:232、index.html:764）、btn-primary 渐变位移动画、表格行 hover、全局 focus-visible（aurora-theme.css:349）、toast 进场 320ms ease-enter、触摸目标 48px 伪元素扩大命中区（stage6.css:311-319）都到位；但大量 JS 内联生成的元素无 hover/active 反馈，无按键涟漪/成功时刻的强调动效 | 反馈"有"但不"愉悦"，缺乏获奖级的 signature interaction |
| 7. 响应式表现 | 7.0 | 全项目最强维度：44px 基线 + 移动 48px（stage6.css:332 coarse pointer 批量、hud.css:207-223 Token 横幅/详情面板专项）、16px 输入字号防 iOS 缩放（aurora-theme.css:376）、移动端持仓卡片化、CLS min-height 预留（stage6.css:100-102）、375px 长名省略、横屏专门处理（index.html:629）、指数条 ticker 降级；断点清单：存量 1680/1681/1200/1024/768/640/360/横屏 + pointer:coarse + prefers-reduced-motion | ①1440/1920/1366 只有 1680 一条"补救"规则（index.html:185），无正面大屏设计，1920 下内容全宽拉伸；②断点体系两套并存（存量 @media 与 components.css @container 未打通） |
| 8. 原创性 | 6.0 | Aurora Terminal 方向自洽（深空底 + 霓虹描边 + CJK 正文 + tabular-nums），命名体系（qf-*）与"只描边不填充"霓虹纪律有辨识度，与通用 dashboard 模板拉开差距；但原型特性（容器查询组件轨、页面转场 qf-page-in、移动端 sheet）未落地到生产页，存量页面在 hud-theme 之外仍是"GitHub 暗色 admin"观感 | 设计推导完整但"转译完成度"只有约一半 |

**加权基线总分 ≈ 6.1 / 10**（获奖线 9 分，当前主瓶颈：排版/留白未体系化、亮色回退可及性 FAIL、动效合规细节）。

---

## 二、差距清单

**P0（违反硬性标准）**
1. `web/static/index.html:20-26`（:root 亮色回退变量）——移除 hud-theme 后弱化文本 3.77、三级文本 2.71、白字/琥珀 2.15，AA FAIL。修复：把 --text-muted 提到 #8B9DB4 档、--text-tertiary ≥#6E839A、logo 图标字改深色 #04070E（对 #f59e0b 为 8.05）。影响：色彩/响应式(主题回退)。
2. `web/static/index.html:786` skeleton-shimmer 动画 `left` 属性，违反 60fps 纪律。修复：改用 Aurora 的 background-position 写法（aurora-theme.css:327-329 已有现成实现，直接删存量版）。影响：动效。
3. `web/static/index.html:9` meta theme-color #0d1117 ≠ Aurora 底色 #04070E，移动端浏览器框与页面色断裂。修复：与 hud-theme 联动或改 #04070E。影响：色彩/原创性。
4. `web/static/js/wizard.js:54` `var(--color-up,#34d399)` ——本应用 --color-up 是红色，done 态却回退绿色，变量语义错位；`signal-progress.js:40` 硬编码 #ef4444（旧红，Aurora 语义红应为 #F87171）。修复：done 改 --color-down，红统一 #F87171。影响：色彩。
5. `web/static/index.html` z-index 9 档随意值（9998/9999/10000 等）。修复：收敛到 --qf-z-* 6 档（30/40/60/90/100），toast 10000→var(--qf-z-toast)。影响：视觉层级。

**P1（体系化缺失）**
6. `web/static/index.html`（381 处 ≤13px、5 处 10px）——建立最小可读下限 12px，10px 全部升 11px+ 并加字距；把 tokens.css 的 15 级字阶映射为存量可用工具类。影响：排版/视觉层级。
7. token 真源三处重复（tokens.css、aurora-theme.css:19-91、hud.css:16-53）且青色不一致（#22D3EE vs #22E3FF）。修复：aurora-theme 改为 `@import` 或直接引用 tokens.css，hud.css 旧值删除由 Aurora 覆盖。影响：色彩/原创性。
8. 存量间距非 8px 倍数（10/14/6/3/5px 共 70+ 处 padding）。修复：逐组件归栅（10→8 或 12、14→16、3/5→4），一次只动 padding/gap 不动布局。影响：留白。
9. index.html 96 处 + js 84 处硬编码 hex。修复：优先换 var() 引用；确实语义独立的（图表系列色）集中为注释清单。影响：色彩。
10. `web/static/index.html:444` ticker-scroll 无暂停：hover/focus 暂停 + reduced-motion 下静态。影响：动效/微交互。
11. 大屏无正面设计：1366/1440/1920 仅 1680 一条规则（index.html:185）。修复：1440 引入双列仪表分区，1920 加 content max-width 上限（如 1600px）+ 留白放大。影响：响应式/留白。
12. `transition:all` 遍布（--transition 定义于 index.html:33）。修复：改为显式属性列表（aurora-theme 已示范）。影响：动效。

**P2（冲奖增量）**
13. tabActivePulse（index.html:770）装饰性无限脉冲——改为仅首次激活播一次的 emphasis 曲线动画。影响：动效/原创性。
14. 原型特性未落地：qf-page-in 页面转场（components.css:424）、@container 组件轨、移动 sheet——在 switchPage 处接 240ms ease-enter 转场。影响：动效/原创性。
15. JS 模板 1016 处内联 style 渐进式类名化（随组件迁移做，不单独立项）。影响：视觉层级/可维护性。
16. 设计未覆盖滚动编排：首屏卡片无 stagger 入场。建议 section 级 40ms stagger，仅 transform/opacity。影响：动效/微交互。

---

## 三、三轮迭代计划

**R1 可及性止血 + 真源统一（P0-1~5、P1-7）** → 目标：色彩 7.5 / 动效 7.0，总分 ≈ 6.8
内容：亮色回退对比度修复、shimmer 合成器化、theme-color、JS 语义色错位、z-index 收敛、token 去重。
**R2 排版与留白体系化（P1-6/8/9/12）** → 目标：排版 7.5 / 留白 7.0 / 层级 7.5，总分 ≈ 7.8
内容：字阶工具类落地、10px 清零、间距归 8px 栅格（分页面渐进）、硬编码 hex 清扫、transition 显式化。
**R3 冲奖动效与大屏设计（P1-10/11、P2-13/14/16）** → 目标：动效 8.5 / 微交互 8.0 / 响应式 8.0 / 原创性 8.0，总分 ≈ 8.5+
内容：ticker 可控、页面转场、stagger 编排、signature 微交互、1440/1920 正面设计。

**统一复测场景（每轮末跑同一矩阵）**：页面 = 总览/持仓/市场/分析/系统 5 个聚合页 × 宽度 = 1920/1366/768/375 × 主题 = hud-theme 开/关（亮色回退）× reduced-motion 开/关；工具 = axe（对比度/触摸目标/焦点）、Lighthouse CLS、逐档截图 diff。每轮只允许新增"过线项"，不得回退已过线项。

---

## 四、不可破坏项清单
1. **红涨绿跌**：--color-up=#F87171（红）/ --color-down=#34D399（绿），且涨必须与 ▲/+ 共现；任何"done=绿用 color-up"类语义错位必须修正而不是改色值。
2. **hud-theme 回退契约**：所有主题覆写必须挂在 body.hud-theme 之下（aurora-theme.css 作用域铁律），移除类即整体回退；回退态也必须过 AA（当前不过，是欠账不是豁免）。
3. **STAGE5 锚点约定**：新增代码放 web/static/{css,js}/ 并带 `/* ===== STAGE5: xxx ===== */` 锚点；禁止改回 index.html 内联区（除 P0 修复最小 diff）。
4. **IA 10→5 结构与 STAGE5_PAGE_MAP**：5 个一级聚合容器 + 父子联动映射不得重排。
5. **CSS 加载顺序**：hud.css → stage6.css → aurora-theme.css（aurora 必须最后覆盖视觉 token）。
6. **components.css 容器查询契约**：.qf-ds 断点走 @container 而非 @media，根容器宽度须等于视口宽。
7. **动效铁律**：交互动效 200–500ms、仅 transform/opacity、prefers-reduced-motion 全量关闭。
