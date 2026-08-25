"""
indicator_watcher.py — 지표만 계산·기록하는 관찰용 전략. 주문은 내지 않는다.

■ 왜 필요한가
    Trader._record_indicators() 는 지표값을 '그 봉을 보낸 종목'
    (ev.symbol) 이름표로 찍는다. 한 전략 인스턴스가 여러 종목의 봉을
    받으면, 그 인스턴스에 등록된 지표가 전부 '방금 온 봉의 종목'
    이름표를 뒤집어써서 기록·뷰가 뒤섞인다.

    그래서 종목별로 독립된 지표를 보려면 종목마다 이 전략을 하나씩
    등록해야 한다 — SpreadWatcher(호가 관찰)와 같은 패턴이다.
"""

from __future__ import annotations

from alpha.indicators.indicators import ATR, SMA, CrossOver
from alpha.trader.trading import Strategy


class IndicatorWatcher(Strategy):
    """SMA/CrossOver/ATR 만 갱신·기록한다. 주문은 내지 않는다."""

    defaults = dict(symbol="005930", fast=5, slow=20, atr_period=10)

    def setup(self):
        # symbol= 은 matches() 필터링에만 쓰고, name= 으로 라벨은 심볼
        # 접미사 없이 고정한다 — SmaCrossATR.setup() 과 같은 이유.
        sym = self.p.symbol
        fast, slow = SMA(self.p.fast), SMA(self.p.slow)
        cross, atr = CrossOver(fast, slow), ATR(self.p.atr_period)
        self.fast = self.ind(fast, symbol=sym, name=fast.name)
        self.slow = self.ind(slow, symbol=sym, name=slow.name)
        self.cross = self.ind(cross, symbol=sym, name=cross.name)
        self.atr = self.ind(atr, symbol=sym, name=atr.name)
