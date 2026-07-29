from loguru import logger

try:
    from src.agents.base import AgentRole, AgentOpinion, BaseAgent, DebateResult, TradingDecision
    from src.agents.fundamental_agent import FundamentalAgent
    from src.agents.technical_agent import TechnicalAgent
    from src.agents.sentiment_agent import SentimentAgent
    from src.agents.news_agent import NewsAgent
    from src.agents.research_team import BullResearcher, BearResearcher, ResearchTeam
    from src.agents.trading_manager import TradingManager, TraderAgent, RiskManagerAgent, PortfolioManagerAgent
except ImportError as e:
    logger.warning(f"import src.agents submodules failed: {e}")
