"""
═══════════════════════════════════════════════════════════════════
 portfolio.py — 실계좌 하나를 여러 전략이 나눠 쓰게 한다
═══════════════════════════════════════════════════════════════════

■ 풀려는 문제
    전략 A와 B가 둘 다 005930 을 들고 있다. 실계좌에는 100주가 있다.
    A가 60주, B가 40주 몫이다.

        A.close()  →  60주만 팔아야 한다. 100주가 아니라.

    지금 구조에서 broker.position("005930") 은 100주를 돌려준다.
    그대로 두면 A가 청산할 때 B의 포지션까지 날아간다.

■ 해결: 전략마다 '가상 계좌 뷰'를 준다

        Strategy A ── StrategyBroker(A) ┐
        Strategy B ── StrategyBroker(B) ├─ PortfolioBroker ── 실브로커(KIS/BT)
        Strategy C ── StrategyBroker(C) ┘

    StrategyBroker 는 Broker ABC 를 구현하되, position/equity/open_orders 를
    '그 전략 몫'으로만 답한다. 주문은 그대로 실브로커로 흘려보낸다.

■ 왜 코드가 적게 드나 — GuardBroker 와 같은 데코레이터 패턴이다
    Broker 베이스의 close/target_pct/has_pending 은 전부
    self.position() / self.equity / self.open_orders() 위에 만들어져 있다.
    그 세 개만 '내 몫'으로 바꾸면 나머지 로직은 손댈 필요가 없다.

        close()      → self.position() 이 60주라고 답하니 60주 매도
        target_pct() → self.equity 가 내 배정자본이니 그 비율로 계산
        has_pending()→ 내 미체결만 보므로 B의 주문이 A를 막지 않는다

■ 배정자본(allocation)이 없으면 벌어지는 일
    전략 셋이 각자 target_pct(0.95) 를 부르면 총 285% 를 사려 한다.
    전략별 equity 를 분리해야 각자 자기 몫 안에서만 움직인다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from trading import (EPS, Broker, Fill, Order, OrderStatus, Position, Side,
                     TradeTracker)

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# 1. StrategyBroker — 전략 하나가 보는 계좌
# ═══════════════════════════════════════════════════════════════════

class StrategyBroker(Broker):
    """전략별 가상 계좌.

    ■ 무엇이 '가상'인가
        포지션과 현금만 가상이다. 주문은 진짜로 나간다.
        체결이 돌아오면 그 주문을 낸 전략의 가상 포지션만 갱신한다.

    ■ 무엇을 실브로커에 그대로 위임하나
        now(시계), round_price/round_size/min_size(시장 규칙), cancel.
        시장 규칙은 전략마다 다를 이유가 없다.
    """

    def __init__(self, real: Broker, strategy_id: str, allocation: float):
        self.real = real
        self.sid = strategy_id
        self.allocation = allocation        # 이 전략에 배정된 자본(원)

        # ── 가상 상태 ──
        # 이 전략이 낸 주문의 체결로만 쌓인다. 다른 전략 것은 절대 안 섞인다.
        self._pos: dict[str, Position] = {}
        self._orders: dict[str, Order] = {}     # 내가 낸 주문만
        self._cash = allocation                 # 매수하면 줄고 매도하면 는다
        self._restored_ids: set[str] = set()    # 재시작 전에 낸 미체결 주문 id

    # ───────── 내 몫으로 답하는 것 (핵심 3개) ─────────
    def position(self, symbol: str) -> Position:
        """★ 실계좌 100주 중 '내 60주'만 답한다 ★

        last_price 는 내가 관리할 이유가 없으므로 실브로커에서 가져온다.
        (시세는 공용이고, 포지션만 전략별이다)"""
        p = self._pos.get(symbol)
        last = self.real.position(symbol).last_price
        if p is None:
            return Position(symbol=symbol, last_price=last)
        return Position(symbol=symbol, size=p.size,
                        avg_price=p.avg_price, last_price=last)

    @property
    def equity(self) -> float:
        """내 배정자본 기준 총평가액 = 내 현금 + 내 보유종목 평가액.

        target_pct(0.5) 가 '전체 계좌의 50%'가 아니라
        '내 배정분의 50%'가 되게 하는 지점이다."""
        holdings = sum(self.position(s).market_value
                       for s in self._pos)
        return self._cash + holdings

    def open_orders(self, symbol=None) -> list[Order]:
        """내가 낸 주문만. B의 미체결이 A의 신규 주문을 막지 않는다."""
        return [o for o in self._orders.values()
                if o.is_alive and (symbol is None or o.symbol == symbol)]

    @property
    def cash(self) -> float:
        """내 배정자본 중 남은 현금.

        실계좌 예수금이 아니다. 실계좌가 부족하면 어차피 증권사가 거부한다.
        여기서는 '내 몫을 넘지 않는지'만 본다."""
        return self._cash

    # ───────── 실브로커에 그대로 위임 ─────────
    @property
    def now(self) -> datetime:
        return self.real.now

    def cancel(self, order: Order) -> None:
        self.real.cancel(order)

    def round_price(self, p): return self.real.round_price(p)
    def round_size(self, s): return self.real.round_size(s)
    def min_size(self): return self.real.min_size()

    # ───────── 주문 ─────────
    def submit(self, order: Order) -> Order:
        """주문은 진짜로 나간다. 다만 '누가 냈는지'를 기록해둔다.

        ■ 예산 검사
          내 배정자본을 넘는 매수는 여기서 막는다. 실계좌에 돈이 있어도
          다른 전략 몫이므로 쓰면 안 된다."""
        if order.side is Side.BUY:
            ref = order.price or self.position(order.symbol).last_price
            need = order.size * (ref or 0)
            if need > self._cash + EPS:
                order.status = OrderStatus.REJECTED
                order.reject_reason = f"배정자본 초과 (필요 {need:,.0f} > 가용 {self._cash:,.0f})"
                log.warning("[%s] %s", self.sid, order.reject_reason)
                return order

        self._orders[order.id] = order      # 체결을 되돌려받기 위한 소유권 기록
        return self.real.submit(order)

    # ───────── 체결 반영 (PortfolioBroker 가 라우팅해서 부른다) ─────────
    def apply_fill(self, fill: Fill):
        """내 주문이 체결됐다. 가상 포지션과 현금을 갱신한다.

        계산은 KISBroker.on_execution_report 와 같은 방식이다:
            평단은 '같은 방향으로 늘어날 때만' 다시 계산한다.
            줄어들 때(청산)는 평단이 그대로여야 남은 수량의 원가가 유지된다."""
        pos = self._pos.setdefault(fill.symbol, Position(fill.symbol))
        signed = fill.size if fill.side is Side.BUY else -fill.size
        total = pos.size + signed

        if abs(total) < EPS:
            # 완전 청산 — 포지션 제거
            self._pos.pop(fill.symbol, None)
        else:
            # 평단 갱신은 세 경우로 갈린다.
            #   ① 신규 진입      → 체결가가 곧 평단
            #   ② 같은 방향 증가  → 가중평균 재계산
            #   ③ 감소(부분청산)  → 평단 유지. 남은 수량의 원가가 바뀌면 안 된다
            #
            # ★ pos.size >= 0 으로 쓰면 안 된다 ★
            #   신규 숏(pos.size=0, signed<0)일 때 (0>=0)=True vs (signed>0)=False
            #   라서 조건이 어긋나 평단이 0으로 남는다.
            if pos.size == 0:
                pos.avg_price = fill.price                          # ①
            elif (pos.size > 0) == (signed > 0):
                #   예) 100원 3주 + 110원 2주 → (300+220)/5 = 104원
                pos.avg_price = (pos.avg_price * pos.size
                                 + fill.price * signed) / total     # ②
            pos.size = total

        # 내 현금 이동. 매수하면 나가고 매도하면 들어온다.
        notional = fill.size * fill.price
        self._cash += (-notional if fill.side is Side.BUY else notional)
        self._cash -= fill.commission

    def owns(self, order_id: str) -> bool:
        """이 전략이 낸 주문인가. 재시작으로 복원된 주문번호도 인정한다."""
        return order_id in self._orders or order_id in self._restored_ids

    # ───────── 재시작 대비 ─────────
    def to_state(self) -> dict:
        """★ 이건 반드시 저장해야 한다 ★

        '실계좌 100주 중 A가 60주'라는 정보는 증권사가 모른다.
        저장하지 않으면 재시작 후 전략이 flat 인 줄 알고 중복 진입한다.

        live_orders 도 같이 저장한다 — 재시작 직후 들어온 체결통보를
        누구 것인지 알아보려면 주문번호 소유권이 살아 있어야 한다."""
        return {
            "cash": self._cash,
            "positions": {s: {"size": p.size, "avg_price": p.avg_price}
                          for s, p in self._pos.items()},
            "live_orders": [o.id for o in self._orders.values() if o.is_alive],
        }

    def from_state(self, state: dict) -> None:
        if not state:
            return
        self._cash = state.get("cash", self.allocation)
        self._pos = {s: Position(symbol=s, size=d["size"],
                                 avg_price=d["avg_price"])
                     for s, d in state.get("positions", {}).items()}
        self._restored_ids = set(state.get("live_orders", []))
        log.info("[%s] 상태 복원 — 현금 %s, 보유 %s", self.sid,
                 f"{self._cash:,.0f}",
                 {s: p.size for s, p in self._pos.items()})


# ═══════════════════════════════════════════════════════════════════
# 2. PortfolioBroker — 뷰를 나눠주고 체결을 라우팅한다
# ═══════════════════════════════════════════════════════════════════

class PortfolioBroker:
    """실브로커 하나를 여러 StrategyBroker 로 쪼갠다.

    Broker ABC 를 구현하지 않는다. 전략에 주입되는 건 StrategyBroker 이고,
    이 클래스는 그 위에서 조율만 한다."""

    def __init__(self, real: Broker):
        self.real = real
        self._views: dict[str, StrategyBroker] = {}
        self._owner: dict[str, str] = {}        # order_id -> strategy_id

    def view(self, strategy_id: str, allocation: float) -> StrategyBroker:
        """전략 하나에게 줄 가상 계좌를 만든다.

            pb = PortfolioBroker(kis_broker)
            trader_a = Trader(pb.view("A", 5_000_000), StratA())
            trader_b = Trader(pb.view("B", 3_000_000), StratB())
        """
        v = StrategyBroker(self.real, strategy_id, allocation)
        self._views[strategy_id] = v
        return v

    # ───────── 소유권 조회 ─────────
    def owner_of(self, order_id: str) -> Optional[str]:
        """이 주문을 낸 전략 id. 없으면 None.

        ■ order_id 로 찾는 이유
            주문을 낼 때 어느 StrategyBroker 를 통과했는지가
            유일하게 확실한 소유권 증거다. 종목으로는 못 가른다
            (A와 B가 같은 종목을 들고 있으니까).

        ■ 여기서 체결을 반영하지 않는다
            상태 갱신은 그 브로커를 소유한 Trader 가 한다.
            (Trader.feed_fill → self.broker.apply_fill)
            조회와 상태변경을 한 메서드에 섞으면 호출 순서에 의존하게 된다."""
        sid = self._owner.get(order_id)
        if sid is not None:
            return sid

        # 캐시에 없으면 각 뷰에 물어본다 (재시작 직후 등)
        for s, v in self._views.items():
            if v.owns(order_id):
                self._owner[order_id] = s
                return s

        # HTS 수동주문이거나 재시작 이전 주문.
        # 어느 전략도 자기 것으로 여기지 않으므로 가상 합계와 실계좌가 벌어진다.
        log.warning("주인 없는 주문 %s — reconcile 필요", order_id)
        return None

    # ───────── 정합성 확인 ─────────
    def reconcile(self, symbols: list[str] | None = None) -> dict[str, float]:
        """가상 포지션 합계 vs 실계좌 수량 대조.

        ■ 왜 어긋나나
            수동 주문, 체결통보 유실, 배당·분할, 프로그램 재시작.
            실전에서 반드시 벌어진다.

        ■ 여기서 자동으로 고치지 않는 이유
            차이를 어느 전략에 귀속시킬지 알 수 없다. 잘못 배분하면
            엉뚱한 전략이 남의 포지션을 청산한다.
            경고만 하고 판단은 사람에게 맡긴다.

        반환: {종목: 차이}  (양수 = 실계좌가 더 많음)
        """
        targets = symbols or sorted(
            {s for v in self._views.values() for s in v._pos})
        drift = {}
        for sym in targets:
            virtual = sum(v.position(sym).size for v in self._views.values())
            actual = self.real.position(sym).size
            if abs(virtual - actual) > EPS:
                drift[sym] = actual - virtual
                log.warning("[%s] 수량 불일치 — 가상합계 %g / 실계좌 %g (차이 %+g)",
                            sym, virtual, actual, actual - virtual)
        return drift

    # ───────── 조회 ─────────
    def snapshot(self) -> dict:
        """전략별 현황. 모니터링·로깅용."""
        return {
            sid: {
                "allocation": v.allocation,
                "cash": v.cash,
                "equity": v.equity,
                "pnl_pct": (v.equity / v.allocation - 1) if v.allocation else 0.0,
                "positions": {s: v.position(s).size for s in v._pos},
                "open_orders": len(v.open_orders()),
            }
            for sid, v in self._views.items()
        }

    # to_state / from_state 는 여기 두지 않는다.
    # 가상 포지션은 '그 전략의 것'이므로 Trader 가 자기 상태 파일에 함께 저장한다.
    #   Trader._save  → self.broker.to_state()   (= StrategyBroker.to_state)
    #   Trader.start  → self.broker.from_state()
    # 전략별로 파일이 나뉘어 있어야 전략을 빼거나 추가해도 서로 안 건드린다.