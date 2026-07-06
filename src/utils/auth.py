"""敏感操作的静态 Token 鉴权。

设计:
- 通过环境变量 ADMIN_TOKEN 配置管理员令牌
- 客户端在请求头携带 Authorization: Bearer <token>
- 若 ADMIN_TOKEN 未配置, 则放行但记录警告(开发模式便利)
- 若已配置但请求未携带/不匹配, 返回 401

应用范围:
- PUT /api/config/settings
- PUT /api/config/strategies
- POST /api/config/user_positions
- POST /api/trade/buy
- POST /api/trade/sell
- POST /api/trade/cancel
"""
from __future__ import annotations

import os
import secrets

from fastapi import Header, HTTPException, status
from loguru import logger


def _get_admin_token() -> str:
    return os.getenv("ADMIN_TOKEN", "").strip()


def require_admin(authorization: str | None = Header(default=None)) -> None:
    """FastAPI 依赖: 校验管理员令牌。

    用法:
        @router.put("/settings", dependencies=[Depends(require_admin)])
        async def update_settings(...): ...
    """
    expected = _get_admin_token()
    if not expected:
        # 未配置 ADMIN_TOKEN 时放行, 但记录警告(开发模式)
        logger.warning(
            "ADMIN_TOKEN 未配置, 敏感端点无鉴权保护。"
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

    # 使用 secrets.compare_digest 防止时序攻击
    if not secrets.compare_digest(token, expected):
        logger.warning(f"鉴权失败: 令牌不匹配 (prefix={token[:4]}...)")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="令牌无效",
            headers={"WWW-Authenticate": "Bearer"},
        )


def generate_token() -> str:
    """生成一个安全的随机令牌(用于初始化配置)。"""
    return secrets.token_urlsafe(32)
