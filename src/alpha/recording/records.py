"""
═══════════════════════════════════════════════════════════════════
 records.py — 기록용 행 (IndicatorSnapshot / AccountSnapshot / StrategySpec)
═══════════════════════════════════════════════════════════════════

옛 위치: alpha/trader/trading.py 의 "4. Strategy" 절 앞부분.
이 파일은 표준 라이브러리만 import 한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class IndicatorSnapshot:
    """지표 값 하나. 기록용 long 포맷.

    ■ 이것만 별도 타입인 이유
        Tick/Quote/Bar/Fill/Order/Trade 는 이미 객체가 있으니 그대로
        기록하면 된다. 지표 값은 대응하는 객체가 없어서 여기서 만든다.
        (저장용 클래스를 데이터 종류마다 만드는 건 순수 중복이다)

    ■ 왜 long 인가 (지표마다 컬럼을 만들지 않고)
        전략마다 지표가 다르고, 전략을 고치면 지표가 바뀐다.
        wide 로 하면 스키마가 계속 변해 과거 데이터와 안 맞는다.
        long 이면 스키마가 고정이고, 분석할 때 pivot 하면 된다.

    ■ line
        MACD(macd/signal/hist)나 볼린저(top/mid/bot)처럼 값이 여럿인
        지표는 라인마다 한 행이 된다. 단일 값이면 "".

    ■ trigger
        "order" 주문 낼 때 — 사후 분석의 대부분이 여기서 나온다 (아직 미구현)
        "bar"/"tick"/"quote"  그 이벤트가 지표를 갱신할 때마다 — ev.kind 그대로.
                              봉이면 봉 마감마다, 틱 지표면 체결마다.
    """
    dt: datetime
    strategy_id: str
    symbol: str
    label: str                  # "SMA(20)@005930@60s"
    line: str                   # "" | "top" | "signal" ...
    value: Optional[float]
    trigger: str = ""
    order_id: str = ""          # trigger="order" 일 때 체결 원장과 조인


@dataclass(frozen=True)
class AccountSnapshot:
    """전략(StrategyBroker) 하나의 현금·평가액 스냅샷.

    ■ 왜 IndicatorSnapshot과 같은 이유로 필요한가
        cash/equity 는 Broker.@property 라 스스로 이벤트를 만들지 않는다.
        누군가 '지금' 읽어서 찍어줘야 기록·뷰에 남는다.

    ■ 왜 feed_timer 에서 찍나 (특정 심볼 이벤트가 아니라)
        equity 는 그 전략이 들고 있는 모든 종목의 평가액 합이다. 봉 마감
        같은 특정 심볼 이벤트에 묶으면, 그 전략이 구독 안 한 종목의 가격이
        움직여 equity 가 바뀌어도 못 찍는다. feed_timer(보통 1초 간격)는
        심볼과 무관하게 전 전략을 고르게 훑으므로 이 문제가 없다."""
    dt: datetime
    strategy_id: str
    cash: float
    equity: float


@dataclass(frozen=True)
class StrategySpec:
    """전략 하나가 이번 실행에서 어떤 데이터를 구독했는지 남겨두는 기록.

    ■ 왜 필요한가
        저장된 Trade/Fill 을 나중에 분석·플롯할 때, 그 전략이 어떤 데이터
        규격(60초봉인지 틱인지, 어느 종목인지)을 보고 있었는지 알아야
        같은 조건으로 차트를 그리거나 재현할 수 있다. Trade 자신은 체결
        결과일 뿐 그걸 모른다 — AlphaTrader.add_strategy() 인자에만 있다.

    ■ 한 번만 기록하는데도 append-only 로 쌓는 이유
        strategy_id 는 재실행해도 이름이 같다("추세" 등). 그런데 코드가
        바뀌면 같은 strategy_id 라도 이번 실행의 bars/ticks/quotes 가
        지난 실행과 달라질 수 있다. '지금 설정'만 한 행에 덮어써 두면
        예전 Trade 를 분석할 때 "그때는 어떤 설정이었는지"를 잃어버린다.
        실행마다 dt 를 찍어 한 행씩 쌓아두면, 분석할 때
        "Trade.entry_dt 이전 중 가장 최근 행"을 찾아 그 시점에 실제로
        활성이었던 설정을 정확히 복원할 수 있다.

    ■ IndicatorSnapshot/AccountSnapshot 과 같은 자리인 이유
        add_strategy() 인자는 AlphaTrader 안에서만 존재하는 등록 정보라
        자체 이벤트가 없다 — 그래서 여기서 기록용 타입을 새로 둔다.
    """
    dt: datetime                 # 이 실행에서 등록된 시각
    strategy_id: str
    strategy_class: str          # 클래스 이름 (SmaCrossATR 등)
    allocation: float
    ticks: list = field(default_factory=list)
    quotes: list = field(default_factory=list)
    bars: list = field(default_factory=list)     # [(symbol, seconds), ...]
    warmup: int = 0
