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

from alpha.engine.engine import Engine
from alpha.events.events import Bar
from alpha.broker.base import Broker, round_to_tick
from alpha.core.orders import Fill, Order, OrderStatus, OrderType, Position, Side
from alpha.strategy.base import Strategy
from alpha.trader.trader import Trader

log = logging.getLogger(__name__)


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


# _Pump 와 run_backtest 는 alpha/engine/backtest.py 로 옮겼다.