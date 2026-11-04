"""金融技能库

参考 Vibe-Trading / 中文金融分析 skill 的组织方式,把项目已有能力登记成
"可检索、可调度"的技能,供一句话调度器匹配。

原则:
- 每条技能必须对应真实存在的 API 路径,不登记未实现的能力
- 写操作(交易/改配置)显式标记 read_only=False,调度器默认不自动执行
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Skill:
    id: str
    name: str
    category: str
    description: str
    method: str
    api: str
    keywords: tuple[str, ...]
    read_only: bool = True
    auth: bool = False

    def to_dict(self) -> dict:
        data = asdict(self)
        data["keywords"] = list(self.keywords)
        return data


CATEGORY_ORDER: tuple[str, ...] = (
    "市场复盘",
    "行情数据",
    "个股研究",
    "策略选股",
    "回测验证",
    "基金组合",
    "持仓交易",
    "风险监控",
    "资讯话术",
)

SKILLS: tuple[Skill, ...] = (
    # ── 市场复盘 ──
    Skill(
        id="daily_review",
        name="短线复盘看板",
        category="市场复盘",
        description="涨跌家数、涨停/跌停/炸板、连板梯队、情绪温度与次日判断",
        method="GET",
        api="/api/review/board",
        keywords=("复盘", "盘面", "涨停", "跌停", "炸板", "连板", "情绪", "今天怎么样", "大盘怎么样"),
    ),
    Skill(
        id="sentiment_cycle",
        name="近5日情绪周期",
        category="市场复盘",
        description="近 N 个交易日涨停家数与最高连板变化,判断升温/降温/震荡",
        method="GET",
        api="/api/review/sentiment",
        keywords=("情绪周期", "5日", "五天", "热度", "周期", "升温", "降温"),
    ),
    Skill(
        id="first_board",
        name="首板分析",
        category="市场复盘",
        description="当日首板按封板时间/封单/炸板次数评分,给出跟踪级别",
        method="GET",
        api="/api/review/first_board",
        keywords=("首板", "首板分析", "封板", "封单", "打板", "晋级"),
    ),
    Skill(
        id="three_question",
        name="出手三问",
        category="市场复盘",
        description="身份/断板/竞价三问全过才允许出手,任一问不过直接放弃",
        method="POST",
        api="/api/review/three_question",
        keywords=("三问", "能不能买", "该不该买", "弱转强", "断板", "竞价", "出手"),
    ),

    # ── 行情数据 ──
    Skill(
        id="index_quote",
        name="指数实时行情",
        category="行情数据",
        description="上证/深证/创业板实时指数",
        method="GET",
        api="/api/market/index_realtime",
        keywords=("指数", "上证", "深证", "创业板", "大盘点位"),
    ),
    Skill(
        id="stock_quote",
        name="个股实时行情",
        category="行情数据",
        description="个股最新价、涨跌幅、成交额、换手率",
        method="GET",
        api="/api/market/stock_realtime",
        keywords=("股价", "现价", "行情", "报价", "多少钱"),
    ),
    Skill(
        id="kline",
        name="K线数据",
        category="行情数据",
        description="日/周/月 K 线与近期走势",
        method="GET",
        api="/api/market/stock_kline",
        keywords=("K线", "kline", "走势", "日线", "周线"),
    ),
    Skill(
        id="sector_ranking",
        name="板块排行",
        category="行情数据",
        description="行业/概念板块涨幅与主力净流入排名",
        method="GET",
        api="/api/market/sector_ranking",
        keywords=("板块", "行业", "概念", "轮动", "热点板块"),
    ),
    Skill(
        id="northbound",
        name="北向资金",
        category="行情数据",
        description="北向资金净流入",
        method="GET",
        api="/api/market/northbound",
        keywords=("北向", "外资", "资金流入"),
    ),
    Skill(
        id="dragon_tiger",
        name="龙虎榜",
        category="行情数据",
        description="龙虎榜个股与席位资金",
        method="GET",
        api="/api/market/dragon_tiger",
        keywords=("龙虎榜", "游资", "席位", "机构买入"),
    ),
    Skill(
        id="hot_stocks",
        name="热门股榜",
        category="行情数据",
        description="涨幅榜/人气榜",
        method="GET",
        api="/api/market/hot_stocks",
        keywords=("热门股", "涨幅榜", "人气榜", "龙头"),
    ),

    # ── 个股研究 ──
    Skill(
        id="multi_agent_analysis",
        name="多智能体深度分析",
        category="个股研究",
        description="7 角色分析 + 多空辩论 + 风险辩论 + 组合经理决策",
        method="POST",
        api="/api/agent/analyze",
        keywords=("分析", "深度分析", "智能体", "多空辩论", "研究报告", "值不值得买"),
        auth=True,
    ),
    Skill(
        id="quick_analysis",
        name="快速分析",
        category="个股研究",
        description="单只股票的快速评分与结论",
        method="POST",
        api="/api/agent/quick_analysis",
        keywords=("快速分析", "简单分析", "评分"),
        auth=True,
    ),
    Skill(
        id="strategy_analyze",
        name="策略综合评分",
        category="个股研究",
        description="五维评分/因子模型/技术面综合评估",
        method="POST",
        api="/api/strategy/analyze",
        keywords=("策略分析", "五维", "技术面", "综合评分"),
        auth=True,
    ),
    Skill(
        id="capital_flow",
        name="个股资金流",
        category="个股研究",
        description="主力/超大单/大单净流入",
        method="GET",
        api="/api/monitor/capital_flow",
        keywords=("资金流", "主力", "净流入", "大单"),
    ),
    Skill(
        id="financial_snapshot",
        name="财务快照",
        category="个股研究",
        description="PE/PB/ROE 等核心财务指标",
        method="GET",
        api="/api/market/stock_detail",
        keywords=("财务", "估值", "pe", "pb", "roe", "市盈率"),
    ),
    Skill(
        id="stock_news",
        name="个股新闻",
        category="个股研究",
        description="个股相关新闻与公告",
        method="GET",
        api="/api/market/news",
        keywords=("新闻", "公告", "消息面", "利好", "利空"),
    ),

    # ── 策略选股 ──
    Skill(
        id="limit_up_scan",
        name="涨停板扫描",
        category="策略选股",
        description="涨停原因、封板质量、晋级概率",
        method="GET",
        api="/api/strategy/scan/limit_up",
        keywords=("涨停扫描", "涨停板", "打板", "晋级概率"),
    ),
    Skill(
        id="new_high_scan",
        name="新高扫描",
        category="策略选股",
        description="突破阶段新高的个股",
        method="GET",
        api="/api/strategy/scan/new_high",
        keywords=("新高", "突破", "创新高"),
    ),
    Skill(
        id="cb_t0_scan",
        name="可转债 T+0",
        category="策略选股",
        description="正股涨停联动与可转债溢价机会",
        method="GET",
        api="/api/strategy/scan/cb",
        keywords=("可转债", "转债", "t+0", "溢价"),
    ),
    Skill(
        id="trading_quant",
        name="五维量化评分",
        category="策略选股",
        description="技术/资金/基本面/新闻/情绪五维评分",
        method="POST",
        api="/api/strategy/analyze",
        keywords=("量化", "打分", "选股", "评分模型"),
        auth=True,
    ),

    # ── 回测验证 ──
    Skill(
        id="backtest",
        name="策略回测",
        category="回测验证",
        description="按策略跑历史回测,返回收益/回撤/夏普/胜率",
        method="POST",
        api="/api/strategy/backtest",
        keywords=("回测", "历史收益", "最大回撤", "夏普"),
        auth=True,
    ),
    Skill(
        id="walk_forward",
        name="Walk-Forward 验证",
        category="回测验证",
        description="滚动训练/测试切分,检验策略是否过拟合",
        method="POST",
        api="/api/strategy/validate",
        keywords=("walk-forward", "滚动验证", "过拟合", "样本外"),
    ),
    Skill(
        id="monte_carlo",
        name="蒙特卡洛验证",
        category="回测验证",
        description="对收益序列重采样,估计收益与回撤分布",
        method="POST",
        api="/api/strategy/validate",
        keywords=("蒙特卡洛", "monte carlo", "随机模拟", "置信区间"),
    ),

    # ── 基金组合 ──
    Skill(
        id="fund_positions",
        name="基金持仓",
        category="基金组合",
        description="已录入基金持仓与盈亏",
        method="GET",
        api="/api/fund/positions",
        keywords=("基金持仓", "我的基金", "仓位"),
        auth=True,
    ),
    Skill(
        id="fund_advice",
        name="基金建议",
        category="基金组合",
        description="大盘/板块/盈亏三信号生成加减仓建议",
        method="GET",
        api="/api/fund/advice-v2",
        keywords=("基金建议", "加仓", "减仓", "止盈", "基金怎么办"),
        auth=True,
    ),
    Skill(
        id="fund_search",
        name="基金搜索",
        category="基金组合",
        description="按名称/代码搜索基金",
        method="GET",
        api="/api/fund/search",
        keywords=("基金搜索", "找基金", "基金代码"),
    ),
    Skill(
        id="portfolio_risk",
        name="组合风险",
        category="基金组合",
        description="波动率/回撤/集中度等组合风险指标",
        method="GET",
        api="/api/portfolio/risk",
        keywords=("组合风险", "集中度", "波动率", "回撤"),
        auth=True,
    ),
    Skill(
        id="portfolio_xray",
        name="持仓重叠 X-Ray",
        category="基金组合",
        description="基金前十大重仓重叠度与集中度预警",
        method="GET",
        api="/api/portfolio/xray",
        keywords=("重叠", "xray", "穿透", "重仓"),
        auth=True,
    ),
    Skill(
        id="rebalance",
        name="再平衡建议",
        category="基金组合",
        description="按目标权重生成再平衡建议单",
        method="GET",
        api="/api/portfolio/rebalance/preview",
        keywords=("再平衡", "调仓", "目标权重"),
        auth=True,
    ),

    # ── 持仓交易 ──
    Skill(
        id="positions",
        name="股票持仓",
        category="持仓交易",
        description="股票持仓市值与盈亏",
        method="GET",
        api="/api/dashboard/positions",
        keywords=("持仓", "股票仓位", "盈亏"),
        auth=True,
    ),
    Skill(
        id="watchboard",
        name="组合盯盘",
        category="持仓交易",
        description="盘中估值与异动监控",
        method="GET",
        api="/api/portfolio/watchboard",
        keywords=("盯盘", "盘中", "估值", "异动"),
        auth=True,
    ),
    Skill(
        id="trade_order",
        name="交易下单",
        category="持仓交易",
        description="模拟盘买卖(写操作,需二次确认)",
        method="POST",
        api="/api/trade/buy",
        keywords=("买入", "卖出", "下单", "交易"),
        read_only=False,
        auth=True,
    ),

    # ── 风险监控 ──
    Skill(
        id="risk_status",
        name="风控状态",
        category="风险监控",
        description="四级风险等级与风险提示",
        method="GET",
        api="/api/dashboard/risk",
        keywords=("风险等级", "风控", "能不能买", "仓位上限"),
        auth=True,
    ),
    Skill(
        id="alerts",
        name="监控预警",
        category="风险监控",
        description="告警规则与最近告警",
        method="GET",
        api="/api/monitor/alerts",
        keywords=("预警", "告警", "提醒"),
        auth=True,
    ),
    Skill(
        id="data_health",
        name="数据源健康",
        category="风险监控",
        description="各数据源成功率与降级状态",
        method="GET",
        api="/api/data/health",
        keywords=("数据源", "数据质量", "健康度", "降级"),
    ),

    # ── 资讯话术 ──
    Skill(
        id="global_news",
        name="财经资讯",
        category="资讯话术",
        description="市场级新闻与快讯",
        method="GET",
        api="/api/news/feed",
        keywords=("资讯", "快讯", "财经新闻", "消息"),
    ),
    Skill(
        id="research_reports",
        name="研报查询",
        category="资讯话术",
        description="个股/行业研报",
        method="GET",
        api="/api/market/research_reports",
        keywords=("研报", "券商报告", "机构观点"),
    ),
    Skill(
        id="script_library",
        name="话术库",
        category="资讯话术",
        description="股票/基金场景话术模板与智能匹配",
        method="GET",
        api="/api/scripts/list",
        keywords=("话术", "文案", "模板", "怎么说"),
    ),
    Skill(
        id="market_outlook",
        name="市场展望",
        category="资讯话术",
        description="结合消息面与情绪面的市场展望",
        method="GET",
        api="/api/agent/market_outlook",
        keywords=("展望", "后市", "明天", "市场观点"),
        auth=True,
    ),
)


def all_skills() -> list[Skill]:
    return list(SKILLS)


def get_skill(skill_id: str) -> Skill | None:
    return next((s for s in SKILLS if s.id == skill_id), None)


def list_skills(category: str = "", keyword: str = "") -> list[dict]:
    result = list(SKILLS)
    if category:
        result = [s for s in result if s.category == category]
    if keyword:
        needle = keyword.strip().lower()
        result = [
            s for s in result
            if needle in s.name.lower()
            or needle in s.description.lower()
            or any(needle in kw.lower() for kw in s.keywords)
        ]
    return [s.to_dict() for s in result]


def category_summary() -> list[dict]:
    summary = []
    for category in CATEGORY_ORDER:
        items = [s for s in SKILLS if s.category == category]
        summary.append({
            "category": category,
            "count": len(items),
            "read_only_count": sum(1 for s in items if s.read_only),
            "skills": [s.id for s in items],
        })
    return summary


def registry_stats() -> dict:
    return {
        "total": len(SKILLS),
        "categories": len(CATEGORY_ORDER),
        "read_only": sum(1 for s in SKILLS if s.read_only),
        "write": sum(1 for s in SKILLS if not s.read_only),
    }
