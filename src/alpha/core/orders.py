"""
═══════════════════════════════════════════════════════════════════
 orders.py — 공통 값 객체 (Order / Position / Fill)
═══════════════════════════════════════════════════════════════════

옛 위치: alpha/trader/trading.py 의 "1. 값 객체" 절.
이 파일은 표준 라이브러리만 import 한다.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

EPS = 1e-9          # 부동소수점 비교용. 0.0000001 같은 찌꺼기를 0으로 본다


# ═══════════════════════════════════════════════════════════════════
# 1. 값 객체 — backtrader 와 KIS 의 공통 분모만 남긴 것
# ═══════════════════════════════════════════════════════════════════

class Side(str, Enum):
    """매수/매도. str 을 상속해서 side.value 가 그대로 "buy" 문자열이 된다."""
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"       # 시장가 — 즉시 체결, 가격 모름
    LIMIT = "limit"         # 지정가 — 가격 지정, 체결 보장 없음


class OrderStatus(str, Enum):
    """주문의 일생. 위에서 아래로 흐르고 되돌아오지 않는다."""
    PENDING = "pending"         # 객체만 만들었고 아직 전송 전
    SUBMITTED = "submitted"     # 증권사로 전송함
    ACCEPTED = "accepted"       # 거래소가 접수함
    PARTIAL = "partial"         # 일부만 체결됨 (아직 살아있음)
    FILLED = "filled"           # 전량 체결 (끝)
    CANCELED = "canceled"       # 취소됨 (끝)
    REJECTED = "rejected"       # 거부됨 (끝)
    EXPIRED = "expired"         # 만료됨 (끝)


# '끝난' 상태 모음. is_alive 판정에 쓴다.
TERMINAL = {OrderStatus.FILLED, OrderStatus.CANCELED,
            OrderStatus.REJECTED, OrderStatus.EXPIRED}

# 주문 ID 발급용 카운터. o000001, o000002 ... 순으로 증가한다.
_seq = itertools.count(1)


@dataclass
class Order:
    """주문 한 건.

    ■ 주문 ID를 '우리가' 만드는 이유
        증권사가 주문번호를 주기 전에 이미 식별자가 필요하다.
        - 전송 전에 중복 주문인지 알 수 있다
        - 전송이 실패해도 그 주문을 가리킬 수 있다
        - 재시작 후에도 로그와 대조할 수 있다
    """
    # ---- 주문 낼 때 정하는 것 ----
    symbol: str
    side: Side
    size: float
    type: OrderType = OrderType.MARKET
    price: Optional[float] = None       # None 이면 시장가
    tag: str = ""                       # "entry" / "stop" 등 용도 표시

    # ---- 시스템이 채우는 것 ----
    id: str = field(default_factory=lambda: f"o{next(_seq):06d}")
    broker_id: Optional[str] = None     # 증권사가 준 주문번호
    status: OrderStatus = OrderStatus.PENDING
    filled_size: float = 0.0            # 지금까지 체결된 누적 수량
    avg_fill_price: float = 0.0         # 체결분의 가중평균 단가
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    reject_reason: str = ""

    # ★ 기록/조회용 — Order 자신은 어느 전략 것인지 모른다(브로커가 안다).
    #   StrategyBroker.submit() 이 소유권이 정해지는 시점(자기 _orders에
    #   올릴 때)에 한 번만 채운다. Trade.strategy_id 와 같은 이유·같은 자리다.
    strategy_id: str = ""

    @property
    def remaining(self) -> float:
        """아직 안 채워진 수량. 취소·정정할 때 이 값을 쓴다."""
        return self.size - self.filled_size

    @property
    def is_alive(self) -> bool:
        """아직 체결을 기다리는 중인가."""
        return self.status not in TERMINAL

    @property
    def is_done(self) -> bool:
        return not self.is_alive

    def apply_fill(self, size: float, price: float, dt: datetime):
        """체결 한 건을 반영한다. 부분체결이면 여러 번 불린다.

        평균단가 계산:
            기존 3주를 100원에 받았고, 이번에 2주를 110원에 받았다면
                (100×3 + 110×2) / 5 = 106
            즉 (기존평균 × 기존수량 + 이번가격 × 이번수량) ÷ 총수량
        """
        total = self.filled_size + size
        if total > 0:
            self.avg_fill_price = (
                self.avg_fill_price * self.filled_size + price * size) / total
        self.filled_size = total
        # 목표 수량을 채웠으면 FILLED, 아니면 아직 PARTIAL.
        # EPS 를 빼는 건 부동소수점 오차로 5.0 >= 5.0 이 False 가 되는 걸 막기 위함.
        self.status = (OrderStatus.FILLED if total >= self.size - EPS
                       else OrderStatus.PARTIAL)
        self.updated_at = dt


@dataclass
class Position:
    """특정 종목의 보유 현황 스냅샷.

    ■ 스냅샷이다
        broker.position() 은 부를 때마다 새 객체를 만든다.
        필드에 저장해두면 다음 봉에는 낡은 값이 된다.

    ■ '없음'을 None 이 아니라 size=0 으로 표현한다 (Null Object 패턴)
        전략이 `if pos is None or pos.size == 0` 대신 `if pos.is_flat` 만
        쓰면 되게 하려는 것.
    """
    symbol: str
    size: float = 0.0           # + 롱, - 숏, 0 보유없음
    avg_price: float = 0.0      # 매입 평균단가 (원가)
    last_price: float = 0.0     # 현재가. 수량 계산과 평가손익에 쓴다

    # ★ 기록/조회용 — Position 자신은 어느 전략 것인지 모른다(StrategyBroker
    #   가 안다). Engine.feed_fill() 이 view_q 에 흘리기 직전에 그 전략의
    #   sid 를 채워 넣는다. Trade/Order 의 strategy_id 와 같은 이유다.
    strategy_id: str = ""

    @property
    def is_flat(self) -> bool:
        return abs(self.size) < EPS

    @property
    def is_long(self) -> bool:
        return self.size > 0

    @property
    def market_value(self) -> float:
        """현재 평가금액. 현재가가 없으면 원가로 대체한다."""
        return self.size * (self.last_price or self.avg_price)

    @property
    def unrealized_pnl(self) -> float:
        """평가손익(원). 숏이면 size 가 음수라 부호가 자동으로 뒤집힌다.
        예: 숏 -3주, 평단 100, 현재가 90 → (90-100) × (-3) = +30 이익"""
        if self.is_flat:
            return 0.0
        return (self.last_price - self.avg_price) * self.size

    @property
    def unrealized_pct(self) -> float:
        """평가수익률. 롱이면 그대로, 숏이면 부호를 뒤집는다."""
        if self.is_flat or not self.avg_price:
            return 0.0
        r = self.last_price / self.avg_price - 1.0
        return r if self.is_long else -r


@dataclass(frozen=True)
class Fill:
    """체결 한 건. 시스템의 '단일 진실 공급원'이다.

    백테스트든 실전이든 모든 체결이 이 형태로 변환되어
    같은 TradeTracker 를 통과한다. 그래서 손익 계산식이 하나뿐이다."""
    dt: datetime
    symbol: str
    side: Side
    size: float                 # 항상 양수. 방향은 side 가 갖는다
    price: float
    order_id: str = ""
    commission: float = 0.0     # 이 체결에서 발생한 비용(수수료+세금)
