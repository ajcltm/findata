"""
dobi_watcher.py — DOBI(1호가 잔량 기반 Order Book Imbalance)만 계산·기록하는
관찰용 전략. 주문은 내지 않는다.

■ 왜 종목마다 별도 인스턴스인가
    IndicatorWatcher와 같은 이유(indicator_watcher.py 참고) — Trader.
    _record_indicators() 는 지표값을 '그 이벤트를 보낸 종목'(ev.symbol)
    이름표로 찍는다. 한 전략 인스턴스가 여러 종목의 호가를 받으면, 그
    인스턴스에 등록된 지표가 전부 '방금 온 호가의 종목' 이름표를
    뒤집어써서 기록·뷰가 뒤섞인다. 그래서 종목별로 독립된 지표를 보려면
    종목마다 이 전략을 하나씩 등록해야 한다.

■ 왜 봉이 아니라 호가(quote)인가
    DOBI는 1호가 매수/매도 잔량의 변화를 보는 지표라, 호가창이 갱신될
    때마다(봉 마감을 기다리지 않고) 계산해야 의미가 있다.
"""

from __future__ import annotations

from alpha.indicators.indicators import DOBI
from alpha.trader.trading import Strategy


class DobiWatcher(Strategy):
    """DOBI 만 갱신·기록한다. 주문은 내지 않는다."""

    defaults = dict(symbol="005930", var_err=7_000_000, var_sig=100)

    def setup(self):
        # symbol= 은 matches() 필터링(값 오염 방지)에만 쓰고, name= 으로
        # 라벨은 심볼 접미사 없이 고정한다 — SmaCrossATR/IndicatorWatcher와
        # 같은 이유(종목마다 별도 인스턴스라 이름이 겹칠 일이 없고,
        # 접미사가 붙으면 Pivot 뷰에서 지표×종목 조합마다 칼럼이 따로
        # 생겨버린다).
        sym = self.p.symbol
        dobi = DOBI(self.p.var_err, self.p.var_sig)
        self.dobi = self.ind(dobi, on="quote", symbol=sym, name="DOBI")
