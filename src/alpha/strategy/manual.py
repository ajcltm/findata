"""
manual.py — 콘솔 수동 주문 전용 전략. 지표도 훅도 없다.

■ 왜 필요한가
    콘솔에서 사람이 직접 낸 주문도 '주인 없는 수동주문(HTS)'으로 흘리지
    않고, 다른 자동 전략과 똑같이 포지션·체결·거래 내역이 기록·조회되게
    하려면 전용 StrategyBroker 가 필요하다. 그 그릇이 이 전략이다.

■ 왜 시세를 구독하지 않나
    ticks/quotes/bars 를 전부 비워 등록한다(AlphaTrader._ensure_manual_strategy
    참고) — 이 전략은 스스로 판단해서 주문을 내지 않는다. 콘솔이
    broker.buy()/sell() 을 직접 불러서 주문을 넣고, 그 결과(포지션·체결)만
    이 전략의 StrategyBroker 에 쌓인다.
"""

from __future__ import annotations

from alpha.trader.trading import Strategy

# 이 전략의 strategy_id. alphatrader.py(등록)와 view/controller.py(주문
# 화면이 engine.slots[...] 를 찾을 때) 양쪽이 이 상수를 그대로 참조한다.
# alpha.alphatrader.alphatrader 에서 값을 가져오면 app.py ↔ controller.py
# ↔ alphatrader.py 순환 임포트가 생기므로, 순환에서 자유로운 여기(leaf
# 모듈)에 둔다.
MANUAL_STRATEGY_ID = "수동"


class ManualStrategy(Strategy):
    """콘솔 수동 주문의 소유자. 지표 없음, 훅 없음 — 그릇 역할만 한다."""

    def setup(self):
        pass
