"""L3 访问边界：部署级 Bearer 校验（HTTP 与 MCP 共用）。

DEMO_API_TOKEN 缺省时不启用鉴权（本地开发便利）；设置后所有 /search、
/evidence、/invoke、/stream 与 MCP 端点必须携带 Authorization: Bearer <token>。
"""
import os

from fastapi import Header, HTTPException

EXPECTED_TOKEN = os.environ.get("DEMO_API_TOKEN", "")


def enforce_bearer(authorization: str | None = Header(default=None)) -> None:
    if not EXPECTED_TOKEN:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={"code": "not_authenticated", "message": "缺少 Bearer 凭据"})
    if authorization[7:] != EXPECTED_TOKEN:
        raise HTTPException(status_code=401, detail={"code": "not_authenticated", "message": "Bearer 凭据无效"})
