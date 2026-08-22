"""
spread_watcher.py — 호가만 보는 관찰용 전략. 주문은 내지 않는다.

봉을 안 받는 전략도 AlphaTrader 에 공존한다는 예시. backtrader 경로에는
호가(Quote) 이벤트가 없어서 못 올라간다 — 실전/모의 전용.
"""

from __future__ import annotations

import logging

from alpha.trader.trading import Strategy

log = logging.getLogger("main")


class SpreadWatcher(Strategy):
    """호가만 보는 전략. 주문은 내지 않는다."""

    defaults = dict(threshold=0.3)

    def on_quote(self, q):
        if abs(q.imbalance) >= self.p.threshold:
            log.info("  [호가] 불균형 %s  스프레드 %s원",
                     f"{q.imbalance:+.2f}", f"{q.spread:,.0f}")
