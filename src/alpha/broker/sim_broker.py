"""
═══════════════════════════════════════════════════════════════════
 sim_broker.py — 모의투자 브로커
═══════════════════════════════════════════════════════════════════

■ 목적: 실시간 소켓 데이터로 모의투자
    실제 틱·호가를 받으면서 주문만 가짜로 낸다. 성과가 괜찮으면
    KISBroker 로 갈아끼운다 — 교체는 한 줄이다.

        broker = SimBroker(cash=10_000_000)          # 모의
        broker = KISBroker(api, account="...")       # 실전

■ ★ 낙관적으로 체결시키면 안 된다 ★
    모의투자의 목적은 '실전에서 어떻게 될지' 아는 것이다.
    체결을 너그럽게 해주면 성과가 좋게 나오고, 그 믿음으로 실전에 나가면
    깨진다. 그러면 모의투자를 한 의미가 없다.

    그래서 세 가지를 보수적으로 잡았다:

    ① 같은 이벤트에서 체결하지 않는다
        전략이 이 틱을 보고 주문했는데 그 틱 가격에 체결되면 미래를 본 것이다.
        주문은 '다음' 이벤트부터 체결 대상이 된다.

    ② 지연을 넣는다 (latency_ms)
        실제 주문은 REST 왕복에 100~300ms 걸린다. 그 사이 가격이 움직인다.
        빠른 전략일수록 이 차이가 성과를 지배한다.

    ③ 지정가는 '통과'해야 체결된다 (fill_policy="through")
        내 지정가를 스치는(touch) 것만으로는 체결되지 않는다.
        최우선호가에 주문을 넣으면 이미 그 가격에 줄 서 있는 물량 뒤에
        붙기 때문이다. 모의투자가 거짓말하는 가장 흔한 경로가 이것이다.
        낙관적으로 보고 싶으면 fill_policy="touch" 로 바꿀 수 있다.

■ 체결 경로가 실전과 같다
    실계좌:  거래소 체결 → 소켓 → trading_q → events.Notice → on_execution_report
    모의:    _match 판정 → trading_q → events.Notice → on_execution_report

    둘 다 같은 events.Notice(정규화된 MarketEvent)를 큐에 넣는다 —
    필드 이름이 다른 원본(SimNotice)을 따로 만들지 않으므로 LiveRunner가
    실전/모의를 구분할 필요가 없다.

    _match 는 '체결됐다'는 통보만 큐에 넣는다. 포지션·현금 갱신은
    on_execution_report 가 한다 — 실브로커와 완전히 같은 자리다.
    그래서 EngineTrader 는 둘을 구별하지 않는다.

■ 장시간 돌아간다
    백테스트와 달리 며칠~몇 주 켜 둔다. 그래서 KISBroker 에 없는 것이 필요하다:
        to_state/from_state   재시작 시 포지션·현금 복원
                              (실계좌는 증권사에 물어보면 되지만 모의는 없다)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from alpha.events.events import MarketEvent, Notice
from alpha.trader.trading import (EPS, Broker, Fill, Order, OrderStatus, OrderType,
                     Position, Side)

log = logging.getLogger("sim")


def round_to_tick(price: float) -> float:
    """한국 주식 호가단위. ★ 실전(kis_broker)과 같은 함수여야 한다 ★
    모의만 자유로운 가격에 체결시키면 성과가 좋게 나온다."""
    p = float(price)
    for limit, tick in ((2000, 1), (5000, 5), (20000, 10),
                        (50000, 50), (200000, 100), (500000, 500)):
        if p < limit:
            return round(p / tick) * tick
    return round(p / 1000) * 1000


class SimBroker(Broker):
    """모의 계좌. Broker ABC 구현체이므로 Engine 에 그대로 꽂힌다.

    ■ KISBroker 와의 차이
        submit   REST 전송 대신 대기 목록에 등록
        체결     웹소켓 통보 대신 on_market 에서 판정
        잔고     폴링 대신 자기가 계산
        상태     증권사에 물어볼 수 없으므로 파일로 저장

    ■ 하지 않는 것
        부분체결   호가 잔량으로 흉내 낼 수 있지만 실제 체결 순서를 모른다.
                  그럴듯하기만 하고 정확도가 없어서 전량 체결로 둔다.
        시장충격   대량 주문이 호가를 밀어올리는 것. 검증할 방법이 없다.
    """

    def __init__(self, fill_q, cash: float = 10_000_000,
                 commission: float = 0.00015,   # 매수·매도 공통 0.015%
                 tax: float = 0.0018,           # 매도세(거래세+농특세) 0.18%
                 slippage: float = 0.0005,      # 호가 없을 때 시장가 불리하게
                 latency_ms: int = 200,         # 주문 전송 왕복 지연
                 fill_policy: str = "through"): # "through" | "touch"
        # ★ 체결을 큐에 넣는다 — 실전과 같은 경로 ★
        #   실계좌에서는 체결통보가 소켓 → trading_q 로 들어온다.
        #   모의도 같은 큐에 Fill 을 넣으면 특별 취급이 필요 없다.
        #   Broker 인터페이스에 콜백 속성을 더할 이유도 없다.
        self.fill_q = fill_q
        self._cash = cash
        self._start_cash = cash
        self.commission = commission
        self.tax = tax
        self.slippage = slippage
        self.latency = timedelta(milliseconds=latency_ms)
        self.fill_policy = fill_policy

        self._pos: dict[str, Position] = {}
        self._orders: dict[str, Order] = {}     # 살아있는 것 + 끝난 것
        self._eligible: dict[str, datetime] = {}  # 주문 id -> 체결 가능 시각
        self._sent: set[str] = set()             # 체결통보를 이미 큐에 넣은 주문
        self._quote: dict[str, tuple[float, float]] = {}  # symbol -> (bid, ask)
        self._now = datetime.fromtimestamp(0)

    # ═══════════════════════════════════════════════════════
    # Broker 필수 구현
    # ═══════════════════════════════════════════════════════
    @property
    def cash(self) -> float:
        """가용 현금 = 잔고 - 미체결 매수에 묶인 금액.

        KISBroker._reserved 와 같은 개념이다. 이게 없으면 같은 돈으로
        여러 종목을 사려 드는 게 모의에서만 성공한다."""
        reserved = sum(
            o.remaining * (o.price or self._last(o.symbol))
            for o in self._orders.values()
            if o.is_alive and o.side is Side.BUY)
        return self._cash - reserved

    @property
    def equity(self) -> float:
        return self._cash + sum(p.market_value for p in self._pos.values())

    @property
    def now(self) -> datetime:
        return self._now

    def position(self, symbol: str) -> Position:
        return self._pos.get(symbol, Position(symbol))

    def open_orders(self, symbol=None) -> list[Order]:
        return [o for o in self._orders.values()
                if o.is_alive and (symbol is None or o.symbol == symbol)]

    def submit(self, order: Order) -> Order:
        """접수만 한다. 체결은 다음 이벤트에서, 그것도 지연 이후에."""
        if order.side is Side.BUY:
            ref = order.price or self._last(order.symbol)
            need = order.size * (ref or 0)
            if need > self.cash + EPS:
                order.status = OrderStatus.REJECTED
                order.reject_reason = (f"예수금 부족 (필요 {need:,.0f} > "
                                       f"가용 {self.cash:,.0f})")
                log.info("[SIM] %s", order.reject_reason)
                return order        # 거부는 submit 반환으로 전달된다

        order.status = OrderStatus.ACCEPTED
        order.created_at = self._now
        self._orders[order.id] = order
        # ★ 지연 ★ 이 시각 이후의 이벤트에서만 체결을 판정한다.
        #   같은 이벤트에서 체결하면 미래를 보는 것이고, 지연이 없으면
        #   실전보다 유리한 가격에 잡힌다.
        self._eligible[order.id] = self._now + self.latency
        log.info("[SIM] 주문 %s %s %d주 @%s", order.symbol, order.side.value,
                 int(order.size), f"{order.price:,.0f}" if order.price else "시장가")
        return order

    def cancel(self, order: Order) -> None:
        if order.is_alive:
            order.status = OrderStatus.CANCELED
            order.updated_at = self._now
            log.info("[SIM] 취소 %s", order.id)

    def round_price(self, p): return round_to_tick(p)
    def round_size(self, s): return float(int(s))
    def min_size(self): return 1.0

    # ═══════════════════════════════════════════════════════
    # 시세 수신 → 체결 판정
    # ═══════════════════════════════════════════════════════
    def on_market(self, ev: MarketEvent) -> None:
        """Engine.feed 가 모든 이벤트에 대해 부른다.
        시계·시세를 갱신하고 대기 주문을 체결시킨다.

        ★ 이벤트 종류를 묻지 않는다 ★
          if ev.kind == "tick" ... elif "bar" ... 로 분기하면 새 피드가
          생길 때마다 브로커를 고쳐야 한다. 이벤트가 자기 가격을 답하므로
          여기서는 그 답만 쓴다.
              ref_price    평가 기준가
              trade_price  실제 거래가 (호가 갱신은 None)
              quote        (매수1호가, 매도1호가)

        ★ 시계는 절대 뒤로 안 간다 ★
          ev.dt 는 틱에 실린 체결시간을 그대로 쓴다. 그 값이 어떤
          이유로든(피드 쪽 시간 역전, 순서가 어긋난 패킷 등) 이전보다
          이르게 들어오면 self._now 가 뒤로 가면서, 그 직전에 잡아둔
          주문의 _eligible 시각을 영원히 다시 못 넘어 그 주문만 하염없이
          미체결로 멈춘다 — 한 번 벌어지면 스스로 못 고친다. max() 로
          단조증가를 강제해서 이 클래스를 막는다."""
        self._now = ev.dt

        if ev.quote:
            self._quote[ev.symbol] = ev.quote
        if ev.ref_price:
            self._touch(ev.symbol).last_price = ev.ref_price

        self._match(ev)

    def _match(self, ev):
        """대기 주문 중 체결 조건을 만족한 것을 찾아 '통보'를 큐에 넣는다.

        ★ 여기서 상태를 바꾸지 않는다 ★
          포지션·현금 갱신은 on_execution_report 가 한다. 실계좌와 같은
          자리에서 같은 방식으로 처리되어야 모의와 실전이 갈리지 않는다."""
        for order in list(self._orders.values()):
            if not order.is_alive or order.symbol != ev.symbol:
                continue
            # ★ 지연 확인 ★ 아직 거래소에 도달하지 않은 주문이다
            if self._now < self._eligible.get(order.id, self._now):
                continue
            # 통보를 이미 보낸 주문은 건너뛴다. 큐에서 꺼내 처리되기까지
            # 다음 이벤트가 몇 개 지나갈 수 있는데, 그 사이 또 체결시키면
            # 이중 체결이 된다.
            if order.id in self._sent:
                continue

            px = self._fill_price(order, ev)
            if px is None:
                continue

            self._sent.add(order.id)
            # 실전(events.from_notice)과 같은 정규화 타입을 직접 만든다.
            # SimBroker는 자기 시계(self._now)를 이미 갖고 있으므로,
            # KiSEngine처럼 별도 정규화 함수를 거칠 필요 없이 여기서
            # 바로 events.Notice를 완성한다.
            notice = Notice(kind="notice", symbol=order.symbol, dt=self._now,
                            order_no=order.id, rejected=False,
                            filled_qty=order.remaining, price=px)
            try:
                self.fill_q.put_nowait(notice)
            except Exception:
                self._sent.discard(order.id)
                log.exception("[SIM] 체결통보 큐 투입 실패 — 재시도합니다")

    def _fill_price(self, order: Order, ev) -> Optional[float]:
        """체결가. 체결 안 되면 None.

        ■ 시장가 — 반대편 호가를 친다
            사려면 남의 매도호가를 쳐야 한다. 호가를 모르면(봉 데이터)
            기준가에 슬리피지를 얹는다.

        ■ 지정가 — fill_policy 가 성과를 좌우한다
            "through"  시장가가 내 가격을 통과해야 한다 (기본, 보수적)
                       최우선호가에 넣으면 이미 줄 선 물량 뒤이므로
                       스치는 것만으로는 내 차례가 안 온다.
            "touch"    닿기만 하면 체결. 낙관적이다.

            probe 는 이벤트가 알려준다 — 틱이면 체결가, 봉이면 저가/고가.
            (봉 종가만 보면 봉 안에서 지나간 가격을 놓친다)
        """
        buy = order.side is Side.BUY
        bid, ask = self._quote.get(order.symbol, (None, None))

        if order.type is OrderType.MARKET:
            fallback = ev.ref_price or 0
            px = (ask or fallback * (1 + self.slippage)) if buy else \
                 (bid or fallback * (1 - self.slippage))
            return round_to_tick(px) if px else None

        probe = ev.low_price if buy else ev.high_price
        if probe is None:                   # 호가 갱신 등 — 거래가 아니다
            return None
        if self.fill_policy == "touch":
            hit = probe <= order.price if buy else probe >= order.price
        else:
            hit = probe < order.price if buy else probe > order.price
        return order.price if hit else None

    # ═══════════════════════════════════════════════════════
    # 체결 반영 — KISBroker.on_execution_report 와 같은 시그니처
    # ═══════════════════════════════════════════════════════
    def on_execution_report(self, broker_id: str, status: str,
                            filled_qty: float, price: float,
                            dt: datetime) -> tuple[Optional[Order],
                                                   Optional[Fill]]:
        """체결통보를 반영한다. EngineTrader → Engine.feed_execution 이 부른다.

        ★ 실브로커와 같은 시그니처·같은 반환 ★
          그래서 EngineTrader._handle_notice 가 모의/실전을 구별하지 않는다.
          모의에서만 다른 건 '통보를 누가 만드는가'뿐이다 —
          실전은 거래소가, 모의는 _match 가 만든다.
        """
        order = self._orders.get(str(broker_id))
        if order is None:
            log.warning("[SIM] 모르는 주문번호 %s", broker_id)
            return None, None

        self._sent.discard(order.id)
        self._eligible.pop(order.id, None)

        if status == "reject":
            order.status = OrderStatus.REJECTED
            order.updated_at = dt
            return order, None

        size = min(filled_qty, order.remaining)
        if size <= 0:
            return order, None

        notional = size * price
        # 비용: 수수료는 양방향, 세금은 매도만
        cost = notional * self.commission
        if order.side is Side.SELL:
            cost += notional * self.tax

        # ── 포지션 갱신 (StrategyBroker.apply_fill 과 같은 규칙) ──
        pos = self._touch(order.symbol)
        signed = size if order.side is Side.BUY else -size
        total = pos.size + signed
        if abs(total) < EPS:
            pos.size = 0.0
            pos.avg_price = 0.0
        else:
            if pos.size == 0:
                pos.avg_price = price               # 신규 진입
            elif (pos.size > 0) == (signed > 0):
                pos.avg_price = (pos.avg_price * pos.size
                                 + price * signed) / total
            # 감소(부분청산)면 평단 유지
            pos.size = total

        # ── 현금 ──
        self._cash += (-notional if order.side is Side.BUY else notional) - cost

        order.apply_fill(size, price, dt)
        fill = Fill(dt=dt, symbol=order.symbol, side=order.side,
                    size=size, price=price, order_id=order.id, commission=cost)
        log.info("[SIM] 체결 %s %s %d주 @%s (비용 %s)", order.symbol,
                 order.side.value, int(size), f"{price:,.0f}", f"{cost:,.0f}")
        return order, fill

    # ═══════════════════════════════════════════════════════
    # 장시간 운용 대비
    # ═══════════════════════════════════════════════════════
    def to_state(self) -> dict:
        """★ 모의는 이걸 저장해야 한다 ★
        실계좌는 재시작 후 증권사에 물어보면 되지만 모의는 물어볼 곳이 없다.
        저장하지 않으면 재기동 때마다 현금이 초기값으로 돌아간다.

        미체결 주문은 저장하지 않는다 — 재기동 시 취소된 것으로 본다.
        (실전에서도 장 마감이면 미체결은 취소되므로 그쪽이 현실적이다)"""
        return {
            "cash": self._cash,
            "positions": {s: {"size": p.size, "avg_price": p.avg_price}
                          for s, p in self._pos.items() if not p.is_flat},
        }

    def from_state(self, state: dict) -> None:
        if not state:
            return
        self._cash = state.get("cash", self._start_cash)
        self._pos = {s: Position(symbol=s, size=d["size"],
                                 avg_price=d["avg_price"])
                     for s, d in state.get("positions", {}).items()}
        log.info("[SIM] 상태 복원 — 현금 %s, 보유 %s", f"{self._cash:,.0f}",
                 {s: p.size for s, p in self._pos.items()})

    # ═══════════════════════════════════════════════════════
    def _touch(self, symbol: str) -> Position:
        return self._pos.setdefault(symbol, Position(symbol))

    def _last(self, symbol: str) -> float:
        return self._pos.get(symbol, Position(symbol)).last_price or 0.0

    def summary(self) -> dict:
        return {
            "start_cash": self._start_cash,
            "cash": self._cash,
            "equity": self.equity,
            "return_pct": self.equity / self._start_cash - 1,
            "positions": {s: p.size for s, p in self._pos.items()
                          if not p.is_flat},
            "open_orders": len(self.open_orders()),
        }