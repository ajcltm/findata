"""
═══════════════════════════════════════════════════════════════════
 base.py — 모든 데이터 소스 백엔드가 지키는 최소 계약
═══════════════════════════════════════════════════════════════════

■ 이 파일이 하는 일
    local(SQLite 기록 파일)/fdr(FinanceDataReader)/kis(KIS REST API)처럼
    "데이터를 어디서 가져오는가"가 서로 다른 백엔드들이 공통으로 따라야
    할 뼈대 하나(BaseStore)를 정의한다. 패키지 __init__.py 의 DataStore()
    파사드가 api= 값에 따라 이 뼈대를 상속한 클래스 중 하나를 골라 쓴다.

■ 왜 load() 하나만 강제하는가
    "이 소스가 주는 데이터를 원본 모양 그대로 꺼내온다"는 모든 백엔드가
    당연히 할 수 있는 일이지만, "이 소스의 시계열 데이터를 Tdata로 감싸
    돌려준다"(ticks()/quotes()/bars()/ohlcv()/indicators() 같은 것들)는
    그 소스에 그런 개념이 있을 때만 의미가 있다 — fdr에는 "체결통보"가
    없고, local에는 fdr만의 "상장 종목 목록" 같은 게 없다. 이런 메서드를
    전부 @abstractmethod로 강제하면, 없는 개념에도 억지로 빈 구현을
    만들게 된다. 그래서 load()만 강제하고, 나머지는 "있으면 같은 이름을
    쓰고 없으면 그 백엔드만의 새 이름을 쓴다"는 관례로 둔다(패키지
    __init__.py 모듈 docstring 참고).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

from ..tdata import Tdata
# ↑ 점 두 개(..)는 "한 단계 더 위 패키지"를 가리킨다 — 이 파일은
#   analysist/datastore/base.py 이므로, ..tdata 는 analysist/tdata.py다
#   (analysist.datastore.base 기준으로 한 단계 올라가면 analysist).


class BaseStore(ABC):
    """데이터 소스 백엔드의 공통 베이스. 단독으로 쓰지 않는다 — 항상
    LocalStore/FdrStore 같은 구체적인 백엔드를 통해서만 쓰인다."""

    @abstractmethod
    def load(self, *args, **kwargs) -> pd.DataFrame:
        """이 소스가 주는 데이터를, 그 소스가 원래 돌려주는 모양 그대로
        DataFrame으로 반환한다 — 여기서 컬럼명을 바꾸거나 표준화하지
        않는다("load는 원본 그대로"가 모든 백엔드의 공통 약속이다).
        인자 모양(symbol/start/end 등)은 백엔드마다 다를 수 있다."""

    # ── Tdata 포장 — 모든 백엔드가 공유하는 도우미 ──────────────────
    def _tdata(self, name: str, df: pd.DataFrame, label=None,
              keys: "tuple[str, ...]" = ()) -> Tdata:
        """df를 Tdata(Leaf)로 감싼다. name(@label)을 Tdata 이름으로,
        keys 중 실제 df에 있는 컬럼만 골라 쓴다.

        원래 local(LocalStore) 전용이던 헬퍼를 끌어올린 것이다 — "DataFrame
        한 장 + 이름 + 식별 키"를 Tdata로 감싸는 절차는 어느 백엔드든
        똑같아서, 백엔드마다 다시 쓸 이유가 없다.

        label : 그 값 하나로 이미 고정된 필터(예: indicators(label=...),
               bars(seconds=...))가 있으면 넘긴다 — Tdata 이름이
               "name@label"로 접히고, keys에서는 그 뜻으로 안 넣는다
               (호출하는 쪽이 keys 목록 자체에서 그 컬럼을 빼고 넘겨야
               한다 — 이 메서드는 label을 keys 제외 사유로 자동 추론하지
               않는다, 호출부의 명시적 결정을 그대로 따른다)."""
        full_name = f"{name}@{label}" if label is not None else name
        keys = tuple(k for k in keys if k in df.columns)
        return Tdata.from_df(full_name, df, keys=keys)
