"""
═══════════════════════════════════════════════════════════════════
 base.py — 브로커 기반 (Broker / GuardBroker / round_to_tick)
═══════════════════════════════════════════════════════════════════

옛 위치:
    Broker, GuardBroker  alpha/trader/trading.py 의 "3. Broker" 절
    round_to_tick        bt_broker.py, kis_broker.py, sim_broker.py 에
                         똑같은 함수가 3개 있었다. 여기 하나로 합쳤다.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from alpha.core.orders import Fill, Order, OrderStatus, OrderType, Position, Side
from alpha.events.events import MarketEvent

log = logging.getLogger(__name__)


def round_to_tick(price: float) -> float:
    """한국 주식 호가단위로 가격을 반올림한다.

    거래소는 아무 가격이나 받지 않는다. 예를 들어 3만원대 종목은 50원 단위로만
    주문할 수 있어서 30,123원 같은 지정가는 거부된다.

    ★ 실전·모의·백테스트가 같은 함수를 쓰는 게 핵심 ★
      백테스트만 30,123원 같은 자유 가격에 체결시키면
      실전보다 성과가 좋게 나와서 백테스트가 거짓말을 한다.
    """
    p = float(price)
    # (가격 상한, 그 구간의 호가단위) 쌍을 낮은 가격부터 훑는다.
    for limit, tick in ((2000, 1), (5000, 5), (20000, 10),
                        (50000, 50), (200000, 100), (500000, 500)):
        if p < limit:
            # tick 으로 나눠 반올림한 뒤 다시 곱하면 tick 배수가 된다.
            return round(p / tick) * tick
    return round(p / 1000) * 1000        # 50만원 이상은 1,000원 단위


# ═══════════════════════════════════════════════════════════════════
# 3. Broker — 추상 인터페이스
# ═══════════════════════════════════════════════════════════════════

class Broker(ABC):
    """구현체가 채우는 건 아래 @abstractmethod 7개뿐.

    ■ 왜 편의 메서드를 베이스에 두나
        buy/sell/close/target_pct 의 수량 계산이 여기 한 곳에만 있으면
        백테스트와 실전이 '같은 공식'으로 수량을 뽑는다.
        구현체에 각각 두면 언젠가 반드시 갈린다.

    ■ 공통 규칙도 여기서 강제한다
        미체결 차단, 최소수량 필터, 로깅 — 전부 _order() 한 곳에서.
        구현체의 submit() 은 '전송만' 한다.
    """

    # ───────── 구현체가 반드시 채울 것 ─────────
    @property
    @abstractmethod
    def cash(self) -> float:
        """지금 쓸 수 있는 현금. 미체결 매수에 묶인 금액은 제외한 값이어야 한다."""

    @property
    @abstractmethod
    def equity(self) -> float:
        """총평가금액 = 예수금 + 보유종목 평가액. target_pct 의 분모."""

    @property
    @abstractmethod
    def now(self) -> datetime:
        """현재 시각. datetime.now() 를 직접 쓰면 안 된다.
        백테스트는 봉 시각, 실전은 틱 시각. 같은 창구로 얻어야
        전략이 백테스트에서 미래를 보지 않는다."""

    @abstractmethod
    def position(self, symbol: str) -> Position:
        """보유 현황. 없어도 None 이 아니라 size=0 인 Position 을 반환할 것."""

    @abstractmethod
    def open_orders(self, symbol: str | None = None) -> list[Order]:
        """아직 살아있는 주문 목록. symbol=None 이면 전 종목."""

    @abstractmethod
    def submit(self, order: Order) -> Order:
        """주문 전송. 계약을 반드시 지킬 것:

            성공 → order.status = SUBMITTED, order.broker_id 설정
            실패 → order.status = REJECTED, order.reject_reason 설정
                   ★ 예외를 밖으로 던지지 말 것 ★
                     전략은 '거부됐다'만 알면 되고 엔진은 살아있어야 한다.

        중복 검사·미체결 차단은 _order() 가 이미 했다. 여기선 전송만 한다."""

    @abstractmethod
    def cancel(self, order: Order) -> None:
        """미체결 주문 취소. 상태 변경은 체결통보/알림에서 처리한다.
        이미 끝난 주문이면 조용히 무시할 것."""

    # ───────── 상태 저장/복원 (선택적 오버라이드) ─────────
    def to_state(self) -> dict:
        """재시작 대비 직렬화. 기본은 저장할 게 없다.

        실브로커는 증권사에 물어보면 되므로(sync_balance) 저장하지 않는다.
        StrategyBroker 만 오버라이드한다 — '실계좌 100주 중 A가 60주'라는
        건 증권사가 모르는 정보라, 저장하지 않으면 재시작 후 영영 잃는다."""
        return {}

    def from_state(self, state: dict) -> None:
        """to_state 의 역."""

    # ───────── 시세 반영 (선택적 오버라이드) ─────────
    def on_market(self, ev: MarketEvent) -> None:
        """시장 이벤트로 브로커의 시세·시계를 갱신한다.

        기본은 아무것도 안 한다. BacktraderBroker 는 cerebro 에서 직접
        읽으므로(d.close[0]) 밀어줄 필요가 없다.
        KISBroker 는 오버라이드한다 — 웹소켓이 밀어주지 않으면
        last_price 가 0으로 남아 target_pct 가 조용히 실패한다.

        ★ Engine.feed 가 모든 이벤트에 대해 부른다 ★
          호출자(EngineTrader / _Pump)가 각자 챙기면 반드시 한쪽이 빠진다."""

    # ───────── 체결 반영 (선택적 오버라이드) ─────────
    def apply_fill(self, fill: Fill) -> None:
        """체결을 이 브로커의 내부 상태에 반영한다.

        기본은 아무것도 안 한다. 실브로커(KISBroker/BacktraderBroker)는
        자기 체결 경로에서 이미 포지션을 갱신했기 때문이다.

        StrategyBroker 만 오버라이드한다 — 실계좌 100주 중 '내 60주'를
        따로 세야 하므로 체결을 한 번 더 자기 장부에 기록해야 한다.

        no-op 기본 구현을 두는 이유: Trader 가 hasattr 로 확인하지 않고
        그냥 부를 수 있다. 덕타이핑 분기는 나중에 조용히 어긋난다."""

    # ───────── 시장 규칙 (구현체가 오버라이드) ─────────
    # ★ 백테스트도 실전과 같은 규칙을 써야 결과가 맞는다.
    #   백테스트만 30,123원에 체결시키면 실전보다 성과가 좋게 나온다.
    def round_price(self, price: float) -> float:
        """호가단위로 반올림."""
        return price

    def round_size(self, size: float) -> float:
        """주문 가능 수량으로 조정. 주식은 정수."""
        return float(int(size))

    def min_size(self) -> float:
        """최소 주문 수량. 이보다 작으면 주문을 내지 않는다."""
        return 1.0

    # ───────── 전략이 부르는 주문 API ─────────
    def buy(self, symbol, size, price=None, tag="") -> Optional[Order]:
        """매수. 수량 전용이다.

        비중으로 사고 싶으면 target_pct 를 써라. 여기에 pct 를 두면
        '증분'과 '목표'라는 다른 개념이 같은 이름으로 섞인다."""
        return self._order(Side.BUY, symbol, size, price, tag)

    def sell(self, symbol, size, price=None, tag="") -> Optional[Order]:
        return self._order(Side.SELL, symbol, size, price, tag)

    def close(self, symbol, price=None, tag="close") -> Optional[Order]:
        """전량 청산. 보유 방향의 반대로 보유 수량만큼 낸다."""
        pos = self.position(symbol)
        if pos.is_flat:
            return None                              # 청산할 게 없음
        side = Side.SELL if pos.is_long else Side.BUY
        return self._order(side, symbol, abs(pos.size), price, tag)

    def target_pct(self, symbol, pct, price=None, tag="") -> Optional[Order]:
        """포트폴리오 비중을 pct 로 '맞춘다'. (증분이 아니라 목표다)

        ■ 왜 cash 가 아니라 equity 로 계산하나
            pct 는 '총자산 중 이 종목의 비중'이다. 분모는 총자산이어야 한다.

            cash 로 하면 멱등하지 않다:
                총 1000만(전액 현금)에서 target_pct(0.5) → 500만 매수 ✓
                이제 현금 500만. 다시 target_pct(0.5) → 250만 더 사라?  ✗
            equity 로 하면:
                목표 = 1000만 × 0.5 = 500만, 이미 500만 보유 → delta 0 ✓

        ■ 계산
            목표수량 = 총평가금액 × 비중 ÷ 현재가
            주문수량 = 목표수량 - 현재보유          (차이만 주문)
        """
        pos = self.position(symbol)
        ref = price or pos.last_price               # 지정가가 있으면 그 가격 기준
        if not ref:
            log.warning("[%s] 기준가 없음 — target_pct 무시", symbol)
            return None

        target = self.round_size(self.equity * pct / ref)
        delta = target - pos.size                   # + 사야 함, - 팔아야 함

        if abs(delta) < self.min_size():
            return None                             # 이미 목표에 도달
        side = Side.BUY if delta > 0 else Side.SELL
        return self._order(side, symbol, abs(delta), price, tag)

    def modify(self, order: Order, price=None, size=None) -> Optional[Order]:
        """정정. 기본 구현은 취소 후 재주문.

        ★ 구현체가 이걸 NotImplementedError 로 막으면 안 된다.
          백테스트에서 되던 게 실전에서 터지면 이 구조의 의미가 없다.
          더 나은 방법(KIS 정정 API)이 있으면 오버라이드해서 개선할 뿐이다."""
        self.cancel(order)
        return self._order(order.side, order.symbol,
                           size or order.remaining,
                           price or order.price, order.tag)

    def cancel_all(self, symbol: str | None = None):
        for o in self.open_orders(symbol):
            self.cancel(o)

    def has_pending(self, symbol: str | None = None) -> bool:
        """미체결 주문이 있나."""
        return bool(self.open_orders(symbol))

    # ───────── 모든 주문이 지나가는 단일 통로 ─────────
    def _order(self, side, symbol, size, price, tag) -> Optional[Order]:
        """buy/sell/close/target_pct/modify 가 전부 여기로 모인다.
        그래서 공통 가드를 여기 한 번만 걸면 된다."""

        # ── 가드 ①: 미체결 주문이 있으면 새 주문을 내지 않는다 ──
        # 실전은 체결이 늦어서, 확인 안 하면 같은 신호로 두 번 산다.
        # (지금은 종목 단위 통짜 차단. 지정가 관리가 필요해지면
        #  tag 단위로 좁힐 것: 진입 주문만 막고 손절 주문은 통과)
        if self.has_pending(symbol):
            log.debug("[%s] 미체결 존재 → 신규 주문 차단 (%s)", symbol, tag)
            return None

        # ── 가드 ②: 최소 수량 미만이면 주문하지 않는다 ──
        size = self.round_size(size or 0)
        if size < self.min_size():
            return None

        # ── 주문 객체 생성 후 구현체로 전달 ──
        return self.submit(Order(
            symbol=symbol, side=side, size=size,
            # 가격이 있으면 지정가, 없으면 시장가
            type=OrderType.LIMIT if price else OrderType.MARKET,
            price=self.round_price(price) if price else None,
            tag=tag, created_at=self.now))


class GuardBroker(Broker):
    """실제 브로커를 감싸는 안전장치. 데코레이터 패턴.

    ■ 왜 Broker 레벨에 있어야 하나
        Strategy 가 self.broker 를 '직접' 부른다.
        Trader 안에 dry_run 플래그를 두면 그냥 우회된다.
        전략이 쥐고 있는 객체 자체가 막아야 확실하다.

    조회(cash/position/...)는 전부 통과시키고 submit 만 막는다."""

    def __init__(self, inner: Broker, enabled: bool = False, dry_run: bool = True):
        self.inner = inner
        self.enabled = enabled      # False = 워밍업 중. 과거 봉 신호를 막는다
        self.dry_run = dry_run      # True = 로그만 찍고 실제 전송 안 함

    # ── 조회는 전부 그대로 위임 ──
    @property
    def cash(self): return self.inner.cash
    @property
    def equity(self): return self.inner.equity
    @property
    def now(self): return self.inner.now
    def position(self, symbol): return self.inner.position(symbol)
    def open_orders(self, symbol=None): return self.inner.open_orders(symbol)
    def cancel(self, order): return self.inner.cancel(order)
    def apply_fill(self, fill): return self.inner.apply_fill(fill)
    def on_market(self, ev): return self.inner.on_market(ev)
    def to_state(self): return self.inner.to_state()
    def from_state(self, st): return self.inner.from_state(st)
    def round_price(self, p): return self.inner.round_price(p)
    def round_size(self, s): return self.inner.round_size(s)
    def min_size(self): return self.inner.min_size()

    # ── 주문만 가로챈다 ──
    def submit(self, order: Order) -> Order:
        if not self.enabled or self.dry_run:
            why = "warmup" if not self.enabled else "dry_run"
            log.info("[BLOCKED:%s] %s %s %s주 @ %s (%s)",
                     why, order.symbol, order.side.value, order.size,
                     order.price or "시장가", order.tag)
            order.status = OrderStatus.REJECTED
            order.reject_reason = why
            return order
        return self.inner.submit(order)
