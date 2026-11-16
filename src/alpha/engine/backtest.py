"""
═══════════════════════════════════════════════════════════════════
 backtest.py — 백테스트 실행 루프 (_Pump / run_backtest)
═══════════════════════════════════════════════════════════════════

옛 위치: alpha/broker/bt_broker.py 의 "_Pump" 절과 "조립" 절.
브로커 구현체(BacktraderBroker)는 bt_broker.py 에 남는다.

■ 루프 소유권
        실전   : 소켓 스레드가 trading_q 에 넣고 EngineTrader 가 꺼내 Engine 에 먹인다
        백테스트: cerebro 가 봉을 돌리고 _Pump 가 Engine 에 먹인다
"""

from __future__ import annotations

import backtrader as bt

from alpha.broker.bt_broker import BacktraderBroker
from alpha.engine.engine import Engine
from alpha.events.events import Bar


# ═══════════════════════════════════════════════════════════════════
# _Pump — 전략 로직이 0인 껍데기. cerebro 이벤트를 Trader 로 넘긴다
# ═══════════════════════════════════════════════════════════════════

class _Pump(bt.Strategy):
    """전략 로직이 0인 껍데기. cerebro 이벤트를 Engine 으로 넘긴다.

    ■ 왜 필요한가
        cerebro 가 봉과 체결을 주는 창구는 bt.Strategy 메서드뿐이다.
        그걸 받으려면 bt.Strategy 를 하나는 만들어야 한다.

    ■ Engine 을 먹인다 (Trader 가 아니라)
        Engine 을 거쳐야 라우팅·전략별 가상계좌·다중 전략이 전부 동작한다.
        Trader 를 직접 부르면 단일 전략만 돌아간다.

    ■ params 주의
        cerebro.addstrategy(cls, **kw) 의 kw 는 params 에 '선언된 이름만'
        받는다. 선언 안 한 이름을 넘기면 __init__ 으로 흘러가 TypeError.

    ■ optstrategy 금지
        파라미터 최적화를 켜면 backtrader 가 멀티프로세싱으로 돌리며
        params 를 pickle 한다. Engine 은 pickle 되지 않는다.
        최적화가 필요하면 cerebro.run(maxcpus=1).
    """

    params = dict(engine=None, symbol="", seconds=60)

    def __init__(self):
        self.eng: Engine = self.p.engine
        # ★ self.broker 는 bt.Strategy 가 이미 쓰고 있다.
        #   덮어쓰면 order_target_percent 같은 게 깨지므로 다른 이름을 쓴다.
        self.our_broker: BacktraderBroker = self.eng.portfolio.real
        self.our_broker.bind(self)          # 이제 브로커가 cerebro 에 접근 가능

    def start(self):
        """cerebro 가 루프 시작 전 1회 호출.

        history 를 안 넘기는 이유: backtrader 가 과거 봉을 전부 흘려주므로
        따로 워밍업 데이터를 넣을 필요가 없다.
        대신 Trader.warmup 카운터가 초반 N봉의 주문을 막는다."""
        self.eng.start()

    def next(self):
        """봉마다 호출. bt 데이터를 우리 Bar 로 변환해 Engine 에 넣는다."""
        for d in self.datas:
            self.eng.feed(Bar(
                kind="bar", symbol=d._name,
                dt=d.datetime.datetime(0),  # [0] 은 '현재 봉'. [-1] 이 직전 봉
                open=d.open[0], high=d.high[0], low=d.low[0],
                close=d.close[0], volume=d.volume[0],
                seconds=self.p.seconds,     # ★ 구독 주기와 맞아야 배달된다
            ))

    def notify_order(self, bto):
        """주문 상태가 바뀔 때마다 호출.
        EngineTrader._handle_notice 와 같은 모양이다."""
        order, fill = self.our_broker.on_bt_order(
            bto, self.data.datetime.datetime(0))
        if order is None:
            return
        if fill is not None:
            self.eng.feed_fill(fill)    # → PortfolioBroker → 주인 전략
        self.eng.feed_order(order)

    def notify_timer(self, timer, when, *args, **kwargs):
        """봉과 무관한 시각 기반 로직(종가청산 등)을 위한 통로."""
        self.eng.feed_timer(when)

    # notify_trade 는 일부러 구현하지 않는다.
    # bt.Trade 를 쓰면 손익 계산이 두 벌이 되어(우리 TradeTracker + backtrader)
    # 어느 숫자를 믿어야 할지 모르게 된다. Fill 을 유일한 진실로 삼는다.

    def stop(self):
        self.eng.stop()


# ═══════════════════════════════════════════════════════════════════
# 조립
# ═══════════════════════════════════════════════════════════════════

def run_backtest(build_engine, df, symbol="005930", seconds=60,
                 cash=10_000_000, commission=0.00015, slippage=0.001,
                 plot=False, analyzers=True):
    """백테스트 실행.

        build_engine : Engine 을 만드는 팩토리. 실전과 '똑같은 함수'를 넘긴다.
                       build_engine(broker, dry_run) -> Engine
        df           : OHLCV 컬럼(소문자)을 가진 DatetimeIndex DataFrame
        seconds      : df 의 봉 주기. Engine 의 bars=[(sym, seconds)] 와 맞출 것

    ■ 매번 새로 만드는 이유
        cerebro/Engine/Broker 를 재사용하면 이전 실행의 주문 장부와
        TradeTracker 누적값이 남는다. 이 함수만 쓰면 그 사고가 안 난다.

    ■ dry_run=False 인 이유
        백테스트는 '가짜 돈'이므로 항상 실주문(시뮬레이션)을 낸다.
        dry_run 은 실전 전용 안전장치다.
    """
    broker = BacktraderBroker()
    eng = build_engine(broker, False)

    cerebro = bt.Cerebro(stdstats=False)        # 기본 관찰자 끔(우리가 추적)
    if isinstance(df, dict):                    # 멀티 종목: {종목코드: DataFrame}
        for sym, d in df.items():
            cerebro.adddata(bt.feeds.PandasData(dataname=d, name=sym))
    else:
        cerebro.adddata(bt.feeds.PandasData(dataname=df, name=symbol))

    cerebro.addstrategy(_Pump, engine=eng, symbol=symbol, seconds=seconds)

    cerebro.broker.setcash(cash)
    cerebro.broker.setcommission(commission=commission)     # 0.015%
    cerebro.broker.set_slippage_perc(slippage)              # 0.1% 불리하게 체결

    if analyzers:
        # 손익 자체는 TradeTracker 가 계산하지만, 샤프·MDD 같은 시계열 지표는
        # backtrader 것을 쓰는 게 편하다.
        cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe")
        cerebro.addanalyzer(bt.analyzers.DrawDown, _name="dd")
        cerebro.addanalyzer(bt.analyzers.TimeReturn, _name="ret")

    result = cerebro.run()
    if plot:
        cerebro.plot(style="candle")

    # eng.snapshot() 에 전략별 거래·손익이 들어있다.
    return result[0], eng
