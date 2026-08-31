"""
microstructure_watcher.py — 호가·체결 미시구조 지표만 계산·기록하는
관찰용 전략. 주문은 내지 않는다.

■ 왜 종목마다 별도 인스턴스인가
    IndicatorWatcher/DobiWatcher와 같은 이유(그 두 파일 참고) —
    Trader._record_indicators() 는 지표값을 '그 이벤트를 보낸 종목'
    (ev.symbol) 이름표로 찍는다. 한 전략 인스턴스가 여러 종목을 받으면
    지표가 전부 '방금 온 이벤트의 종목' 이름표를 뒤집어써서 기록·뷰가
    뒤섞인다.

■ 무엇을 등록하나 (등록 순서 = 계산 순서, indicators.py 규칙)
    flow(BestQuoteFlow)                호가 순유출 — 라인 5개
      ├ ofi_ema  = TimeEMA(flow.ofi_norm)      단일 라인
      ├ ofi_kf   = KalmanTrend(flow.ofi_norm)  라인 5개(level/slope/...)
      └ dep      = DepletionRate(flow)         라인 3개(ask/bid/net)
    imb(QueueImbalance)                 호가 잔량 불균형 — 단일 라인
      └ imb_ema  = TimeEMA(imb)                단일 라인
    tfi(TradeFlowImbalance, on="tick")  체결 방향 불균형 — 단일 라인

    flow/ofi_kf/dep 는 라인이 여럿이라 label을 고정한다(BestQuoteFlow/
    OFI_KF/DepletionRate) — __main__.py의 전용 Pivot 뷰가 이 문자열을
    그대로 쓴다(MACD/DOBI와 같은 패턴).

■ 왜 quotes= 와 ticks= 를 둘 다 구독해야 하나
    tfi 만 체결(tick)로 갱신되고 나머지는 전부 호가(quote)로 갱신된다
    — __main__.py에서 이 전략을 등록할 때 quotes=[symbol], ticks=[symbol]
    둘 다 줘야 한다(하나만 주면 그쪽 지표만 갱신된다).
"""

from __future__ import annotations

from alpha.indicators.microstructure import (BestQuoteFlow, DepletionRate,
                                             KalmanTrend, QueueImbalance,
                                             TimeEMA, TradeFlowImbalance)
from alpha.trader.trading import Strategy


class MicrostructureWatcher(Strategy):
    """BestQuoteFlow/QueueImbalance/TradeFlowImbalance 및 그 파생(TimeEMA/
    KalmanTrend/DepletionRate)만 갱신·기록한다. 주문은 내지 않는다."""

    defaults = dict(symbol="005930",
                    ema_halflife=30.0,      # TimeEMA 반감기(초)
                    kf_q=1e-4,              # KalmanTrend 추세반응 비율
                    imb_levels=3,           # QueueImbalance 깊이
                    depletion_window=60.0,  # DepletionRate 창(초)
                    tfi_window=30.0)        # TradeFlowImbalance 창(초)

    def setup(self):
        # symbol= 은 matches() 필터링(값 오염 방지)에만 쓰고, name= 으로
        # 라벨은 심볼 접미사 없이 고정한다 — IndicatorWatcher/DobiWatcher와
        # 같은 이유(종목마다 별도 인스턴스라 이름이 겹칠 일이 없고,
        # 접미사가 붙으면 Pivot 뷰에서 지표×종목 조합마다 칼럼이 따로
        # 생겨버린다).
        sym = self.p.symbol

        # ── 호가 순유출(속도 축) — 라인 5개, label 고정 ──
        flow = BestQuoteFlow()
        self.flow = self.ind(flow, on="quote", symbol=sym, name="BestQuoteFlow")

        # flow.ofi_norm 을 시간 기반으로 평활 — 라인 하나("value")라
        # 일반 지표판(where={"line":"value"})에 자동으로 잡힌다.
        ofi_ema = TimeEMA(self.p.ema_halflife, flow, "ofi_norm")
        self.ofi_ema = self.ind(ofi_ema, on="quote", symbol=sym,
                                name=f"OFI_EMA({self.p.ema_halflife:g}s)")

        # flow.ofi_norm 을 2상태 칼만으로 — 라인 5개(level/slope/lead/
        # obs_var/nis), label 고정. 반드시 flow 뒤에 등록.
        ofi_kf = KalmanTrend(q=self.p.kf_q, source=flow, line="ofi_norm")
        self.ofi_kf = self.ind(ofi_kf, on="quote", symbol=sym, name="OFI_KF")

        # flow.ask_flow/bid_flow 의 상대 소진 속도 — 라인 3개(ask/bid/net),
        # label 고정. 반드시 flow 뒤에 등록.
        dep = DepletionRate(flow, self.p.depletion_window)
        self.dep = self.ind(dep, on="quote", symbol=sym, name="DepletionRate")

        # ── 호가 잔량 불균형(강도 축) — 단일 라인 ──
        imb = QueueImbalance(levels=self.p.imb_levels)
        self.imb = self.ind(imb, on="quote", symbol=sym, name=imb.name)

        imb_ema = TimeEMA(self.p.ema_halflife, imb)
        self.imb_ema = self.ind(imb_ema, on="quote", symbol=sym,
                                name=f"Imbalance_EMA({self.p.ema_halflife:g}s)")

        # ── 체결 방향 불균형 — 단일 라인, 틱마다 갱신 ──
        tfi = TradeFlowImbalance(self.p.tfi_window)
        self.tfi = self.ind(tfi, on="tick", symbol=sym, name=tfi.name)
