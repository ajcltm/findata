"""
═══════════════════════════════════════════════════════════════════
 fdr.py — FinanceDataReader → Tdata/DataFrame
═══════════════════════════════════════════════════════════════════

■ 이 파일이 하는 일
    FinanceDataReader(https://github.com/financedata-org/FinanceDataReader)
    라이브러리로 국내외 주식·지수·환율·암호화폐 등의 과거 시세를 그
    자리에서(네트워크로) 받아온다. local(LocalStore)과 달리 "이미
    기록된 것"이 아니라 "그 라이브러리가 지금 가져다주는 것"을 다룬다.

    DataStore(api="fdr") 를 부르면 이 FdrStore가 만들어진다 — 바깥에서
    이 파일 이름을 직접 알 필요는 없다(패키지 __init__.py의 DataStore()
    파사드 참고).

■ load() 는 FDR이 주는 모양 그대로
    fdr.DataReader()가 돌려주는 df를 컬럼명도 안 바꾸고 그대로 반환한다
    (Open/High/Low/Close/Volume/Change, 인덱스 이름 "Date") — local의
    load()가 SQLite 원본 그대로 돌려주는 것과 같은 원칙이다("load는
    그 소스가 주는 그대로"가 이 패키지 전체의 공통 약속).

■ ohlcv() 는 local과 이름을 맞춘 Tdata 버전
    FDR의 OHLCV 데이터는 local의 bars()/ohlcv()와 개념이 같아서(둘 다
    "시가/고가/저가/종가/거래량의 일정 주기 봉"), 이름도 그대로 ohlcv()
    를 쓴다. 여기서는 Tdata로 감싸기 위해 컬럼명을 local과 맞게
    소문자로 바꾸는 정도의 가공만 한다(load()에는 손 안 댄다 — 가공은
    ohlcv() 안에서만 한다).

■ FDR에 없는 개념(ticks/quotes/indicators/trades 등)은 안 만든다
    FDR은 과거 시세·종목 목록 조회 라이브러리라 체결/호가/지표/거래
    기록 같은 개념이 없다 — 억지로 빈 메서드를 만들지 않는다
    (BaseStore는 load()만 강제한다, base.py 모듈 docstring 참고).
    반대로 FDR만의 개념(상장 종목 목록)은 listing()이라는 새 이름으로
    만든다 — local/kis에 없는 개념이라 이름을 새로 지었다.

■ FinanceDataReader를 지연 임포트(lazy import)하는 이유
    FDR은 plotly/beautifulsoup4 등 꽤 무거운 의존성을 끌고 온다 — local
    백엔드만 쓰는 사람이 그걸 강제로 설치/로딩할 이유가 없다. 그래서
    FdrStore를 실제로 만들 때(__init__)만 import한다 — DataStore(api=
    "local")만 쓰는 스크립트는 이 무게를 전혀 안 짊어진다(Tdata.plot()
    이 matplotlib/seaborn을 지연 임포트하는 것과 같은 이유다).
"""

from __future__ import annotations

from typing import Optional, Sequence, Union

import pandas as pd

from .base import BaseStore
from ..tdata import Tdata

# local.py의 Symbols와 같은 뜻의 타입 별명이다 — 여기서도 symbol 인자가
# 문자열 하나/여러 개/None(이 파일에서는 "필수"라 None은 안 받지만,
# 리스트 형태를 함께 받는다는 뜻은 같다) 중 뭐든 받을 수 있다는 표시.
Symbols = Union[str, Sequence[str]]


class FdrStore(BaseStore):
    """FinanceDataReader를 그대로 감싼 백엔드. SQLite 파일이 없다 —
    매번 네트워크로 받아온다(인터넷 연결이 필요하다).

    사용 예:
        store = DataStore(api="fdr")
        df = store.load("005930", "2024-01-01", "2024-01-10")  # FDR 원본 그대로
        ohlcv = store.ohlcv("005930", start="2024-01-01")       # → Tdata
        ohlcv2 = store.ohlcv(["005930", "000660"])              # 여러 종목 → symbol 키로 구분
        listing = store.listing("KOSPI")                        # 상장 종목 목록(DataFrame)
    """

    def __init__(self):
        # 여기서만(= FdrStore를 실제로 만드는 시점에만) FinanceDataReader를
        # 불러온다 — 모듈 맨 위에서 import하면 FdrStore를 만들지도 않는
        # 사람까지 그 무게를 짊어진다(위 모듈 docstring 참고).
        import FinanceDataReader as fdr
        self._fdr = fdr

    def load(self, symbol: str, start=None, end=None, exchange=None) -> pd.DataFrame:
        """fdr.DataReader()가 돌려주는 df를 그대로 반환한다 — 컬럼명
        (Open/High/Low/Close/Volume/Change)도, 인덱스 이름("Date")도
        안 건드린다.

        symbol   : fdr.DataReader가 받는 코드/티커 그대로 — 국내 종목
                   코드("005930"), 지수("KS11"), 환율("USD/KRW"),
                   암호화폐("BTC/KRW") 등. 여러 개를 한 번에 조회하는
                   기능은 FDR 자체엔 없어서, 여러 종목이 필요하면
                   ohlcv()를 쓴다(종목마다 load()를 불러 모아준다).
        start/end : datetime 또는 문자열. fdr.DataReader에 그대로 전달.
        exchange : fdr.DataReader(symbol, exchange=...) 그대로 전달 —
                   예: 'NAVER'/'YAHOO' 등 다른 소스로 바꿀 때."""
        return self._fdr.DataReader(symbol, start, end, exchange=exchange)

    # ── 자주 쓰는 조합 ────────────────────────────────────────
    def ohlcv(self, symbol: Symbols, start=None, end=None, exchange=None) -> Tdata:
        """FDR의 OHLCV 시세를 Tdata로 감싼다 — local의 bars()/ohlcv()와
        개념이 같은 것이라 이름도 맞췄다.

        symbol이 문자열 하나면 그 종목 하나짜리(keys 없음)로, 리스트면
        종목마다 load()를 불러 모은 뒤 "symbol" 키로 묶는다(local의
        여러-종목 관례와 맞춤) — FDR 원본에는 symbol 컬럼이 없어서(한
        번에 한 종목만 조회하는 함수라서), 여러 개를 합칠 때만 여기서
        직접 붙여준다.

        컬럼명은 local과 맞춰 소문자로 바꾼다(open/high/low/close/
        volume/change) — load()는 원본 그대로지만, Tdata로 감싸는 이
        메서드 안에서는 가공해도 되는 영역이다(모듈 docstring 참고)."""
        symbols = [symbol] if isinstance(symbol, str) else list(symbol)
        many = len(symbols) > 1

        frames = []
        for sym in symbols:
            df = self.load(sym, start=start, end=end, exchange=exchange)
            df = df.rename(columns=str.lower)
            if many:
                df = df.copy()
                df["symbol"] = sym
            frames.append(df)
        combined = pd.concat(frames).sort_index() if len(frames) > 1 else frames[0]

        keys = ("symbol",) if many else ()
        return self._tdata("ohlcv", combined, keys=keys)

    def listing(self, market: str = "KRX") -> pd.DataFrame:
        """상장 종목 목록. fdr.StockListing()을 그대로 반환한다 — 시계열이
        아니라 "그 시점의 한 번짜리 표"라서 Tdata로 안 감싼다(local의
        indicator_pivot()처럼, Tdata 틀에 안 맞는 결과는 그냥 DataFrame
        으로 둔다).

        market : "KRX"/"KOSPI"/"KOSDAQ"/"KONEX"/"NASDAQ"/"NYSE"/"S&P500"/
                "ETF/KR" 등 — fdr.StockListing() 문서 참고."""
        return self._fdr.StockListing(market)
