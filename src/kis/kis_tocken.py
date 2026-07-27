import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, Callable
from kis import kis_config
import requests
import logging

logger = logging.getLogger("kis")

def oauth_token_p():
    """
    [접근토큰발급(P)] OAuth 인증으로 접근토큰(access_token) 발급
    - METHOD/URL: POST /oauth2/tokenP
    """
    # ====== required (시트: Request Body) ======
    grant_type = "client_credentials"  # 옵션: 고정값(문서상 client_credentials)
          # 옵션: 한국투자증권에서 발급받은 APP SECRET
    appkey = kis_config.APPKEY
    appsecret = kis_config.APPSECRET

    url = f"{kis_config.domain}/oauth2/tokenP"

    headers = {
        "content-type": "application/json",
        # 옵션(필요 시): "charset": "UTF-8"
    }
    params = {}  # 보통 QueryString 없이 Body로만 보냄

    data = {
        "grant_type": grant_type,
        "appkey": appkey,
        "appsecret": appsecret,
    }

    r = requests.post(url, headers=headers, params=params, json=data, timeout=10)
    return r.json()

def oauth_revoke_p():
    """
    [접근토큰폐기(P)] 발급받은 접근토큰(access_token) 폐기
    - METHOD/URL: POST /oauth2/revokeP
    """
    url = f"{kis_config.domain}/oauth2/revokeP"

    headers = {
        "content-type": "application/json",
    }
    params = {}

    data = {
        "appkey": kis_config.APPKEY,
        "appsecret": kis_config.APPSECRET,
        "token": kis_config.ACCESS_TOKEN,
    }

    r = requests.post(url, headers=headers, params=params, json=data, timeout=10)
    return r.json()

# -----------------------------
# 저장 경로 (프로젝트 루트 고정)
# -----------------------------
TOKEN_PATH = Path(__file__).resolve().parent / "token.json"


# -----------------------------
# Token 객체
# -----------------------------
class TokenInfo:
    def __init__(self, access_token: str, expired_at: datetime, token_type: str = "Bearer"):
        self.access_token = access_token
        self.expired_at = expired_at
        self.token_type = token_type

    def is_valid(self) -> bool:
        return datetime.now() < self.expired_at


# -----------------------------
# 토큰 로드
# -----------------------------
def load_token() -> Optional[TokenInfo]:
    if not TOKEN_PATH.exists():
        return None

    try:
        data = json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
        return TokenInfo(
            access_token=data["access_token"],
            expired_at=datetime.fromisoformat(data["expired_at"]),
            token_type=data.get("token_type", "Bearer")
        )
    except Exception:
        return None


# -----------------------------
# 토큰 저장
# -----------------------------
def save_token(token: TokenInfo) -> None:
    payload = {
        "access_token": token.access_token,
        "token_type": token.token_type,
        "issued_at": datetime.now().isoformat(),
        "expired_at": token.expired_at.isoformat(),
    }

    TOKEN_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


# -----------------------------
# 핵심 함수
# -----------------------------

def refresh_fn():
    
    return {
        "access_token": oauth_token_p().get("access_token"),
        "expires_in": 24
    }

def get_or_refresh_token() -> str:

    token = load_token()

    # 유효하면 그대로 사용
    if token and token.is_valid():
        return token.access_token

    # 만료 or 없음 → 재발급
    new_data = refresh_fn()
    

    access_token = new_data["access_token"]

    logger.info(f"new token published : {access_token}")

    expired_at = datetime.now() + timedelta(hours=new_data["expires_in"])

    new_token = TokenInfo(access_token, expired_at)
    save_token(new_token)

    return new_token.access_token