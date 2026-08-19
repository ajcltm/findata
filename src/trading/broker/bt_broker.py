"""
═══════════════════════════════════════════════════════════════════
 bt_broker.py — 백테스트. backtrader 의존은 이 파일뿐.
═══════════════════════════════════════════════════════════════════

■ kis_broker.py 와 대칭이다
        BacktraderBroker   ↔   KISBroker        (Broker 구현체)
        _Pump              ↔   EngineTrader     (이벤트를 Engine 으로 밀어넣는 쪽)
        on_bt_order()      ↔   on_execution_report()   (체결 → Order/Fill 변환)

    이름은 달라도 역할과 반환 계약이 같다. 그래야 한쪽만 고치는 일이 없다.

■ 루프 소유권
        실전   : 소켓 스레드가 trading_q 에 넣고 EngineTrader 가 꺼내 Engine 에 먹인다
        백테스트: cerebro 가 봉을 돌리고 _Pump 가 Engine 에 먹인다

    cerebro.run() 을 부르면 제어권이 backtrader 로 넘어가지만, 그 안에서
    _Pump 가 아래로 밀어주기만 하면 Engine 은 똑같이 동작한다.
    Engine 이 '수동 이벤트 수신자'라서 누가 밀든 상관없다.

■ backtrader 에서 쓰는 것
    ✓ 체결 시뮬레이션, 수수료·슬리피지 모델, 애널라이저, 플로팅
    ✗ bt.Strategy(전략 로직), bt.indicators, bt.Trade — 전부 우리 것을 쓴다

■ 한계
    데이터피드가 OHLCV 만 실어 나른다. 호가(Quote) 이벤트는 재생할 수 없으므로
    on_quote 만 구현한 전략은 백테스트되지 않는다(등록은 되되 호출이 안 온다).
"""

from __future__ import annotations

import logging
from datetime import datetime

import backtrader as bt

from engine import Engine
from events import Bar
from trading import (Broker, Fill, Order, OrderStatus, OrderType,
                     Position, Side, Strategy, Trader)

log = logging.getLogger(__name__)


def round_to_tick(price: float) -> float:
    """한국 주식 호가단위 반올림.

    ★ 실전과 같은 함수를 쓰는 게 핵심 ★
      백테스트만 30,123원 같은 자유 가격에 체결시키면
      실전보다 성과가 좋게 나와서 백테스트가 거짓말을 한다.
    """
    p = float(price)
    for limit, tick in ((2000, 1), (5000, 5), (20000, 10),
                        (50000, 50), (200000, 100), (500000, 500)):
        if p < limit:
            return round(p / tick) * tick       # tick 으로 나눠 반올림 후 복원
    return round(p / 1000) * 1000


# ═══════════════════════════════════════════════════════════════════
# BacktraderBroker — cerebro 내부 상태를 공통 인터페이스로 감싼다
# ═══════════════════════════════════════════════════════════════════

class BacktraderBroker(Broker):
    """Broker ABC 의 백테스트 구현체.

    ■ bind() 를 받기 전에는 아무것도 못 한다
        cerebro 가 _Pump 를 만들어주기 전까지 self._bt 가 None 이다.
        객체 생성 → cerebro 조립 → run() → _Pump.__init__ → bind() 순서.
    """

    def __init__(self):
        self._bt: bt.Strategy | None = None     # bind() 가 채운다

        # ── 우리 Order 와 bt.Order 를 잇는 매핑 ──
        # 체결 통보는 bt.Order.ref(정수)로 오므로 역방향 조회가 필요하다.
        self._by_ref: dict[int, Order] = {}      # bt ref   -> 우리 Order
        self._bt_orders: dict[str, object] = {}  # 우리 id  -> bt order (취소용)

        # ── 누적 체결량 기록 ──
        # backtrader 의 order.executed.size 는 '누적값'이다.
        # 그대로 Fill 로 만들면 부분체결 시 이중 계산된다. 증분만 뽑기 위한 장부.
        self._reported: dict[str, float] = {}

    def bind(self, bt_strategy: bt.Strategy):
        """_Pump 가 자기 자신을 넘겨준다. 이때부터 cerebro 에 접근 가능."""
        self._bt = bt_strategy

    # ───────── 필수 구현 7개 ─────────
    @property
    def cash(self) -> float:
        """★ 주의: backtrader 는 '체결 시점'에 예수금을 차감한다.
        주문을 내도 다음 봉에 체결될 때까지 getcash() 는 그대로다.
        즉 KIS 와 마찬가지로 submit~체결 사이에는 낡은 값이다.

        지금 이걸 막고 있는 건 Broker._order() 의 has_pending 가드다.
        (미체결이 있으면 새 주문 자체를 안 낸다)
        다만 그 가드는 종목 단위라, 여러 종목을 동시에 다루기 시작하면
        양쪽 브로커 모두 _reserved 같은 장치가 필요해진다."""
        return self._bt.broker.getcash()

    @property
    def equity(self) -> float:
        return self._bt.broker.getvalue()       # 예수금 + 보유평가액

    @property
    def now(self) -> datetime:
        """현재 봉의 시각. datetime.now() 를 쓰면 백테스트가 미래를 본다."""
        return self._bt.data.datetime.datetime(0)

    def position(self, symbol: str) -> Position:
        """bt.Position 을 우리 Position 으로 변환한 스냅샷.

        last_price 를 봉 종가로 채우는 게 포인트다. 실전에서는 웹소켓이
        밀어주지만(push) 여기서는 그냥 읽으면 된다(pull). 소스가 다를 뿐
        결과적으로 양쪽 다 last_price 가 채워진다."""
        d = self._data(symbol)
        p = self._bt.getposition(d)
        return Position(symbol=symbol, size=p.size,
                        avg_price=p.price, last_price=d.close[0])

    def open_orders(self, symbol=None) -> list[Order]:
        return [o for o in self._by_ref.values()
                if o.is_alive and (symbol is None or o.symbol == symbol)]

    def submit(self, order: Order) -> Order:
        """전송만 한다. 중복 검사·미체결 차단은 Broker._order() 가 이미 했다.

        계약: 실패해도 예외를 던지지 않고 REJECTED 로 표시한다.
             (KISBroker.submit 과 같은 규칙 — 그래야 전략이 같은 코드로 대응)"""
        d = self._data(order.symbol)
        exectype = (bt.Order.Market if order.type is OrderType.MARKET
                    else bt.Order.Limit)
        fn = self._bt.buy if order.side is Side.BUY else self._bt.sell

        try:
            bto = fn(data=d, size=order.size, price=order.price,
                     exectype=exectype)
        except Exception as e:
            order.status = OrderStatus.REJECTED
            order.reject_reason = str(e)
            log.exception("주문 생성 실패 %s", order.id)
            return order

        order.broker_id = str(bto.ref)
        order.status = OrderStatus.SUBMITTED
        order.created_at = self.now

        # 양방향 매핑 등록 — 이 줄들이 빠지면 체결이 와도 못 찾는다
        self._by_ref[bto.ref] = order
        self._bt_orders[order.id] = bto
        self._reported[order.id] = 0.0          # 누적 체결 보고량 0에서 시작
        return order

    def cancel(self, order: Order) -> None:
        bto = self._bt_orders.get(order.id)
        if bto is not None:
            self._bt.cancel(bto)                # 상태 변경은 notify_order 로 온다

    # ───────── 시장 규칙 (실전과 동일해야 함) ─────────
    def round_price(self, price): return round_to_tick(price)
    def round_size(self, size): return float(int(size))
    def min_size(self): return 1.0

    # ───────── 체결 변환 (KIS 의 on_execution_report 와 같은 역할) ─────────
    def on_bt_order(self, bto, dt: datetime) -> tuple[Order | None, Fill | None]:
        """bt.Order 상태 변화를 우리 Order/Fill 로 변환한다.

        반환 계약이 KISBroker.on_execution_report 와 같다:
            (Order|None, Fill|None)
          — 브로커는 상태만 바꾸고, 라우팅은 _Pump/EngineTrader 가 한다.
            브로커가 Trader 를 직접 부르면 순환 의존이 생긴다.
        """
        ours = self._by_ref.get(bto.ref)
        if ours is None:
            return None, None                   # 우리가 낸 주문이 아님

        ours.status = _BT_STATUS.get(bto.status, ours.status)
        ours.updated_at = dt

        fill = None
        if bto.status in (bt.Order.Partial, bt.Order.Completed):
            # ★ 함정: executed.size 는 '이번 회차'가 아니라 '누적' 체결량이다.
            #   부분체결이 3주 → 5주 순으로 오면 3, 5 가 온다(3, 2 가 아니라).
            #   그대로 Fill 로 만들면 총 8주가 되어 포지션이 틀어진다.
            #   그래서 직전에 보고한 양을 빼서 증분만 뽑는다.
            done = abs(bto.executed.size)                   # 누적
            prev = self._reported.get(ours.id, 0.0)         # 직전까지 보고분
            delta = done - prev                             # 이번 증분
            if delta > 0:
                self._reported[ours.id] = done
                ours.apply_fill(delta, bto.executed.price, dt)
                fill = Fill(dt=dt, symbol=ours.symbol, side=ours.side,
                            size=delta, price=bto.executed.price,
                            order_id=ours.id,
                            commission=abs(bto.executed.comm))

        if bto.status in (bt.Order.Rejected, bt.Order.Margin):
            # Margin = 예수금 부족. 우리 모델에서는 둘 다 REJECTED 로 본다.
            ours.reject_reason = bto.getstatusname()

        return ours, fill

    # ───────── 내부 ─────────
    def _data(self, symbol: str):
        """종목코드로 backtrader 데이터피드를 찾는다. 없으면 첫 번째."""
        for d in self._bt.datas:
            if d._name == symbol:
                return d
        return self._bt.data


# backtrader 주문상태 → 우리 OrderStatus 매핑표
_BT_STATUS = {
    bt.Order.Submitted: OrderStatus.SUBMITTED,
    bt.Order.Accepted: OrderStatus.ACCEPTED,
    bt.Order.Partial: OrderStatus.PARTIAL,
    bt.Order.Completed: OrderStatus.FILLED,
    bt.Order.Canceled: OrderStatus.CANCELED,
    bt.Order.Rejected: OrderStatus.REJECTED,
    bt.Order.Margin: OrderStatus.REJECTED,      # 증거금 부족도 거부로 취급
    bt.Order.Expired: OrderStatus.EXPIRED,
}


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