"""
sma_cross_atr.py — 이평 교차 진입 + ATR 트레일링 스탑. 봉 기반 추세추종.

AlphaTrader.add_strategy() 로 등록해서 쓴다:
    trader.add_strategy("추세", SmaCrossATR(symbol="005930"),
                        allocation=5_000_000, bars=[("005930", 60)], warmup=20)
"""

from __future__ import annotations

import logging

from alpha.indicators.indicators import ATR, SMA, CrossOver
from alpha.trader.trading import Strategy

log = logging.getLogger("main")


class SmaCrossATR(Strategy):
    """이평 교차 진입 + ATR 트레일링 스탑. 봉 기반 추세추종.

    진입: 단기선이 장기선을 상향 돌파
    청산: ① 하향 돌파(추세 끝)  ② 스탑 터치(급락)
    """

    defaults = dict(symbol="005930", fast=5, slow=20,
                    atr_period=10, size_pct=0.9, stop_atr=2.0)

    def setup(self):
        self.fast = self.ind(SMA(self.p.fast))
        self.slow = self.ind(SMA(self.p.slow))
        self.cross = self.ind(CrossOver(self.fast, self.slow))   # 입력 지표 뒤에
        self.atr = self.ind(ATR(self.p.atr_period))
        self.stop_price = None

    def on_bar(self, bar):
        b, sym = self.broker, self.p.symbol
        pos = b.position(sym)

        if pos.is_flat:
            if self.cross.value > 0:
                # 스탑 = 현재가 - ATR×2. 평소 움직임의 2배만큼 반대로 가면
                # 판단이 틀렸다고 본다. 종목마다 자동으로 폭이 맞춰진다.
                self.stop_price = bar.close - self.p.stop_atr * self.atr.value
                b.target_pct(sym, self.p.size_pct, tag="entry")
        else:
            hit = self.stop_price is not None and bar.close <= self.stop_price
            if self.cross.value < 0 or hit:
                b.close(sym, tag="stop" if hit else "cross")
                self.stop_price = None
            else:
                new_stop = bar.close - self.p.stop_atr * self.atr.value
                if self.stop_price is None or new_stop > self.stop_price:
                    self.stop_price = new_stop          # ★ 오를 때만 올린다

    def on_order(self, o):
        if o.status.value == "filled":
            log.info("  체결 %s %d주 @%s", o.side.value, int(o.filled_size),
                     f"{o.avg_fill_price:,.0f}")
        elif o.status.value == "rejected":
            log.warning("  주문거부 %s: %s", o.id, o.reject_reason)

    def on_trade(self, t):
        log.info("  ★ 거래완료 %s원 (%s)",
                 f"{t.pnl:+,.0f}", f"{t.pnl_pct:+.2%}")

    def to_state(self):
        return {"stop_price": self.stop_price}      # 봉으로 복원 불가

    def from_state(self, s):
        self.stop_price = s.get("stop_price")
