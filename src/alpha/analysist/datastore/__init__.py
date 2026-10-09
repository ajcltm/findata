"""
═══════════════════════════════════════════════════════════════════
 datastore 패키지 — 데이터 소스 파사드(facade)
═══════════════════════════════════════════════════════════════════

■ 이 패키지가 하는 일
    DataStore(api=...) 를 부르면 그 api에 맞는 백엔드 인스턴스를 만들어
    돌려준다. 겉보기엔 클래스를 생성하는 것 같지만 사실은 공장(factory)
    함수다 — api마다 실제로는 서로 다른 클래스(LocalStore/FdrStore/...)
    를 쓰기 때문이다. 이렇게 하는 이유는 pathlib.Path()가 OS에 따라
    PosixPath/WindowsPath 중 하나를 돌려주는 것과 같다 — "생성자처럼
    보이는 함수"는 파이썬에서 드물지 않은 패턴이다.

        from alpha.analysist import DataStore

        store = DataStore()                      # == DataStore(api="local") (기본값)
        store = DataStore(live=False)             # 지금까지 쓰던 그대로 — 그냥 local로 간다
        store = DataStore(api="fdr")              # FinanceDataReader 백엔드
        store = DataStore(api="kis", ...)         # (앞으로 추가될) KIS REST 백엔드

■ 왜 이렇게 나눴나 — 확장 가능해야 한다
    local(SQLite 기록 파일 읽기)/fdr(FinanceDataReader로 외부 시세
    받아오기)/kis(KIS REST API) 처럼, 데이터를 가져오는 "소스"가 계속
    늘어날 수 있다는 전제 위에서 설계했다. 새 소스가 생기면:
        1. base.py의 BaseStore를 상속한 새 클래스를 하나 만든다
           (어디서든 — 이 패키지 안에 파일을 추가하든, 완전히 다른
           곳에서 만들든 상관없다).
        2. @register("이름") 데코레이터를 그 클래스에 붙인다.
    이 두 가지만 하면 되고, 이 파일(파사드) 자체는 안 건드린다.

■ 모든 백엔드가 지키는 단 하나의 계약 — load()
    load()는 그 라이브러리/소스가 원래 주는 모양 그대로 돌려준다(가공·
    표준화하지 않는다) — 이게 BaseStore가 강제하는 유일한 계약이다
    (base.py 참고).

■ 시계열을 Tdata로 감싸는 메서드들 — 있으면 이름을 맞추고, 없으면 안 만든다
    ticks()/quotes()/bars()/ohlcv()/indicators() 처럼 local에 이미 있는
    개념과 같은 걸 다른 소스도 제공하면, 같은 이름의 메서드로 만든다
    (예: FdrStore.ohlcv() — local의 ohlcv()와 "OHLCV 봉"이라는 개념이
    같다). 그 소스만의 고유한 개념이면 그 소스만의 새 이름을 쓴다
    (예: FdrStore.listing() — 상장 종목 목록은 local/kis에 없는 개념).
    이 메서드들은 BaseStore가 강제하지 않는다 — 있을 때만 만든다.

■ live/simul 같은 세부 사정은 백엔드 안에 갇혀있다
    live=/simul_mode= 는 LocalStore(= 지금까지의 DataStore)만의 사정
    (SQLite 파일 3개 중 뭘 열지)이다. 파사드는 **kwargs를 받은 api의
    생성자에 그대로 흘려보내기만 하고, 그 인자들이 뭔지 전혀 몰라도
    된다 — 그래서 DataStore(live=False) 같은 기존 호출이 그대로 동작
    한다(api 기본값이 "local"이라서). kis 백엔드가 나중에 "실전/모의
    투자" 구분이 필요해져도, 그건 KisStore 자신의 생성자 인자일 뿐이고
    local의 live/simul_mode와 의미까지 같다고 가정하지 않는다.
"""

from __future__ import annotations

from .base import BaseStore
from .local import LocalStore, DATA_DIR
from .fdr import FdrStore

# api 이름 -> 백엔드 클래스. DataStore()가 참조하는 단 하나의 표다.
_REGISTRY: "dict[str, type[BaseStore]]" = {
    "local": LocalStore,
    "fdr": FdrStore,
}


def register(name: str):
    """새 백엔드를 추가할 때 쓰는 데코레이터 — 이 파일(파사드)을 안
    건드리고 확장하는 지점이다.

        @register("kis")
        class KisStore(BaseStore):
            def load(self, ...): ...

    같은 이름으로 또 등록하면 덮어쓴다(마지막에 등록된 것이 이긴다) —
    개발 중 같은 모듈을 다시 import하는 경우를 막지 않으려는 것이다."""
    def deco(cls: "type[BaseStore]") -> "type[BaseStore]":
        _REGISTRY[name] = cls
        return cls
    return deco


def DataStore(api: str = "local", **kwargs) -> BaseStore:
    """api에 맞는 백엔드 인스턴스를 만들어 돌려준다(사실은 함수다 —
    모듈 docstring의 "겉보기엔 클래스 생성자" 설명 참고).

    api : "local"(기본, SQLite 기록 파일) / "fdr"(FinanceDataReader) /
          그 외 register() 로 등록된 이름. 모르는 이름이면 바로 에러를
          내고 지금 등록된 이름 목록을 같이 보여준다.
    **kwargs : 그 api의 생성자에 그대로 전달한다 — 파사드는 이 값들이
          뭔지 몰라도 된다(예: api="local"이면 live=/simul_mode=/path=
          가 LocalStore.__init__으로 그대로 간다)."""
    cls = _REGISTRY.get(api)
    if cls is None:
        raise ValueError(f"모르는 api: {api!r} (등록된 것: {sorted(_REGISTRY)})")
    return cls(**kwargs)


__all__ = ["DataStore", "DATA_DIR", "BaseStore", "LocalStore", "FdrStore", "register"]
