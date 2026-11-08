"""敏感操作的静态 Token 鉴权。

设计:
- 通过环境变量 ADMIN_TOKEN 配置管理员令牌
- 客户端在请求头携带 Authorization: Bearer <token>
- 若 ADMIN_TOKEN 未配置:
    * 生产环境(app_env=production): 拒绝服务 503, 避免裸奔
    * 开发环境(app_env=dev): 放行但记录警告(开发便利)
- 若已配置但请求未携带/不匹配, 返回 401

应用范围:
- PUT /api/config/settings
- PUT /api/config/strategies
- POST /api/config/user_positions
- POST /api/trade/buy
- POST /api/trade/sell
- POST /api/trade/cancel
- POST /api/agent/* (LLM 高成本端点)
- POST /api/scripts/* (话术生成端点)
- POST /api/strategy/analyze (策略分析端点)
- POST /api/news/search (AI 检索端点)
"""
from __future__ import annotations

import os
import secrets

from fastapi import Header, HTTPException, status
from loguru import logger

from src.utils.config import settings


def _get_admin_tokens() -> list[str]:
    """返回所有被接受的管理员令牌（按优先级去重）。

    2026-11-08：新增 ``ADMIN_TOKENS``（逗号分隔）以支持"一个个人令牌在多个
    部署上通用"。浏览器 localStorage 按域名隔离，无法跨站点共享，所以"通用"
    只能在服务端实现：各部署保留自己的 ADMIN_TOKEN，同时把同一个个人令牌写进
    ADMIN_TOKENS，这样用户只需记住一个值，不必为每个站点记不同的。

    兼容性：ADMIN_TOKEN 仍然有效且优先；两者都没有时回退 settings 单例。
    """
    tokens: list[str] = []
    for raw in (os.getenv("ADMIN_TOKEN", ""), os.getenv("ADMIN_TOKENS", "")):
        if not raw:
            continue
        for part in raw.split(","):
            part = part.strip()
            if part and part not in tokens:
                tokens.append(part)
    if tokens:
        return tokens
    fallback = (settings.admin_token or "").strip()
    return [fallback] if fallback else []


def _get_admin_token() -> str:
    """兼容旧调用：返回首个令牌（仅用于日志/提示，鉴权请用 _get_admin_tokens）。"""
    tokens = _get_admin_tokens()
    return tokens[0] if tokens else ""


def _is_production() -> bool:
    """判断当前是否为生产环境。

    APP_ENV != "dev" 即视为生产环境(fail-closed 默认安全)。
    """
    return os.getenv("APP_ENV", "production").lower() != "dev"


def require_admin(authorization: str | None = Header(default=None)) -> None:
    """FastAPI 依赖: 校验管理员令牌。

    用法:
        @router.put("/settings", dependencies=[Depends(require_admin)])
        async def update_settings(...): ...

    安全策略:
    - 生产环境(APP_ENV != "dev")未配置 ADMIN_TOKEN → 抛 503(fail-closed,配置缺失属服务不可用)
    - 开发环境(APP_ENV == "dev")未配置 ADMIN_TOKEN → 放行但 warning
    - 已配置但请求未携带/不匹配 → 抛 401
    """
    expected = _get_admin_tokens()
    if not expected:
        if _is_production():
            # P1 修复(2026-07-29):生产环境 fail-closed,避免裸奔
            logger.error(
                "ADMIN_TOKEN 未配置, 生产环境拒绝敏感端点访问。"
                "请在 Render Dashboard 设置 ADMIN_TOKEN=<随机长字符串>"
                "（或用 ADMIN_TOKENS 配置多个，逗号分隔）"
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="服务端鉴权未配置, 联系管理员设置 ADMIN_TOKEN",
            )
        # 开发环境放行, 但记录警告
        logger.warning(
            "ADMIN_TOKEN 未配置, 敏感端点无鉴权保护(开发模式)。"
            "生产环境请在 .env 中设置 ADMIN_TOKEN=<随机长字符串>"
        )
        return

    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少 Authorization 请求头",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 支持 "Bearer <token>" 与裸 token 两种格式
    parts = authorization.split(" ", 1)
    token = parts[1].strip() if len(parts) == 2 and parts[0].lower() == "bearer" else authorization.strip()

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="令牌为空",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 使用 secrets.compare_digest 防止时序攻击；多令牌时逐个比对，任一匹配即通过
    if not any(secrets.compare_digest(token, exp) for exp in expected):
        # 安全修复(2026-10-26):原日志记录 `prefix={token[:4]}`，会把攻击者尝试的令牌
        # 前 4 位写进日志。日志常被转发到第三方平台、也常被截图贴到工单里，
        # 这等于给爆破提供了逐步逼近的反馈，且泄露了「用户可能输错的那个令牌」片段。
        # 只保留不可逆的元信息（长度），足以区分"空令牌/明显过短/长度正确但不匹配"。
        logger.warning(f"鉴权失败: 令牌不匹配 (len={len(token)})")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="令牌无效",
            headers={"WWW-Authenticate": "Bearer"},
        )


def generate_token() -> str:
    """生成一个安全的随机令牌(用于初始化配置)。"""
    return secrets.token_urlsafe(32)
