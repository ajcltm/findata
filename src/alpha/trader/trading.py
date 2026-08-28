"""
═══════════════════════════════════════════════════════════════════
 trading.py — 공통 계층. Strategy 가 보는 세상은 여기까지다.
═══════════════════════════════════════════════════════════════════

이 파일에는 import backtrader 도, KIS API 호출도 없다. 표준 라이브러리뿐이다.

    Trader ── Strategy              전략 (사용자가 작성)
       └───── Broker                BacktraderBroker | KISBroker
                 └── Order / Position / Trade / Fill

■ 왜 이렇게 나눴나
    Strategy 는 self.broker 만 본다. 그 broker 가 시뮬레이션인지 실계좌인지
    모른다. 그래서 같은 전략 코드가 백테스트와 실전에서 그대로 돈다.
    바꿔 끼우는 건 런타임 인자 하나:   Trader(broker, strategy)

■ 설계 원칙 하나
    Broker 추상 클래스에 로직을 최대한 몰아넣는다.
    구현체가 채우는 건 7개뿐이고(cash/equity/now/position/open_orders/
    submit/cancel), buy·sell·close·target_pct 는 전부 베이스가 만든다.
    → 수량 계산 공식이 한 곳에만 있으니 백테스트와 실전이 다른 수량을
      살 수 없다.
"""

from __future__ import annotations

import itertools
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Optional

# Bar/Tick/Quote 는 이벤트 계층에 있다. events.py 는 trading.py 를
# import 하지 않으므로 순환이 생기지 않는다.
from alpha.events.events import Bar, MarketEvent, Quote, Tick

log = logging.getLogger(__name__)

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


@dataclass
class Trade:
    """포지션이 0에서 시작해 0으로 돌아올 때까지 = 거래 한 건 (라운드트립).

    ■ bt.Trade 와 같은 단위다
        분할 매수·분할 청산을 해도 한 건이다. 그래야 승률·손익비·평균
        보유기간이 직관적으로 나온다. 청산 이벤트마다 쪼개면 절반 익절만
        해도 거래 건수가 두 배가 되고 승률이 부풀려진다.

    ■ 왜 손익을 저장하나 (계산하지 않고)
        분할 청산은 청산가가 여러 개다. (exit_price - entry_price) × size
        같은 식으로는 복원할 수 없어서, 청산할 때마다 실현분을 누적한다.
        entry_price / exit_price 는 '표시용 가중평균'일 뿐이다.
    """
    symbol: str
    size: float                 # 보유했던 최대 수량. + 롱, - 숏
    entry_dt: datetime          # 최초 진입 시각
    entry_price: float          # 진입 가중평균가
    exit_dt: Optional[datetime] = None
    exit_price: float = 0.0     # 청산 가중평균가

    gross_pnl: float = 0.0      # 비용 전 실현손익 (분할 청산분 누적)
    commission: float = 0.0     # 왕복 비용 전체 (진입분 + 청산분)
    entry_fills: int = 0        # 몇 번에 나눠 샀나
    exit_fills: int = 0         # 몇 번에 나눠 팔았나
    fills: list = field(default_factory=list)   # 이 거래를 구성한 체결 전부

    # ★ 기록/조회용 — TradeTracker/이 dataclass는 자기가 어느 전략 것인지
    #   모른다(심볼·체결만 본다). Trader.feed_fill() 이 tracker.on_fill() 이
    #   돌려준 직후 채워 넣는다. Recorder.subscribe(..., extra=...)로 채우지
    #   않는 이유: extra는 구독 채널 하나에 고정되는 값이라, 거래를 내는
    #   전략이 둘 이상이면 같은 Recorder에 Trade를 구독하는 모든 채널에
    #   레코드가 전부 복사돼 strategy_id가 뒤섞인다(IndicatorSnapshot의
    #   strategy_id 필드와 같은 이유로 여기도 필드로 둔다).
    strategy_id: str = ""

    @property
    def is_closed(self) -> bool:
        return self.exit_dt is not None

    @property
    def pnl(self) -> float:
        """순손익. 왕복 비용을 뺀 값."""
        return self.gross_pnl - self.commission

    @property
    def notional(self) -> float:
        """진입 명목금액. 수익률의 분모."""
        return abs(self.size) * self.entry_price

    @property
    def pnl_pct(self) -> float:
        return self.pnl / self.notional if self.notional else 0.0

    @property
    def gross_pct(self) -> float:
        """비용 전 수익률. 비용이 성과를 얼마나 먹는지 볼 때."""
        return self.gross_pnl / self.notional if self.notional else 0.0

    @property
    def is_long(self) -> bool:
        return self.size > 0

    @property
    def is_win(self) -> bool:
        return self.pnl > 0

    @property
    def duration(self):
        """보유 기간. 미청산이면 None."""
        return self.exit_dt - self.entry_dt if self.is_closed else None


@dataclass
class _OpenTrade:
    """진행 중인 라운드트립. 완결되면 Trade 로 변환된다.

    Trade 를 재사용하지 않는 이유: 필드의 의미가 다르다.
    여기서 size 는 '지금 보유량'이고 Trade 에서는 '보유했던 최대량'이다.
    commission 도 여기서는 계속 쌓이는 값이다.
    """
    symbol: str
    size: float = 0.0           # 현재 보유량 (부호 포함)
    peak_size: float = 0.0      # 이 라운드트립에서 가장 크게 들었던 양
    entry_price: float = 0.0    # 진입 가중평균
    entry_dt: Optional[datetime] = None

    exit_value: float = 0.0     # Σ(청산가 × 청산수량) — 평균 청산가 계산용
    exit_qty: float = 0.0

    gross_pnl: float = 0.0      # 분할 청산으로 확정된 손익 누적
    commission: float = 0.0     # 진입·청산 비용 누적
    entry_fills: int = 0
    exit_fills: int = 0
    fills: list = field(default_factory=list)   # 이 라운드트립의 체결 전부

    def to_trade(self, exit_dt: datetime) -> Trade:
        return Trade(
            symbol=self.symbol,
            size=self.peak_size,
            entry_dt=self.entry_dt, entry_price=self.entry_price,
            exit_dt=exit_dt,
            exit_price=(self.exit_value / self.exit_qty) if self.exit_qty else 0.0,
            gross_pnl=self.gross_pnl, commission=self.commission,
            entry_fills=self.entry_fills, exit_fills=self.exit_fills,
            fills=list(self.fills),     # 거래 단위 분석용 (어떤 체결로 구성됐나)
        )


# ═══════════════════════════════════════════════════════════════════
# 2. TradeTracker — 체결 스트림에서 완결된 거래를 조립한다
# ═══════════════════════════════════════════════════════════════════

class TradeTracker:
    """Fill 스트림에서 라운드트립 거래를 조립한다. 평단(netting) 방식.

    ■ 거래의 단위
        포지션 0 → 진입 → (분할 매수/분할 청산) → 포지션 0
        이 전체가 Trade 한 건이다. 중간 청산에서는 아무것도 반환하지 않고
        손익만 누적한다. bt.Trade 와 같은 규칙이다.

    ■ 반환이 리스트인 이유
        보통 0건 또는 1건이지만, 포지션 반전(롱 → 숏)은 한 체결로
        한 거래가 닫히고 다음 거래가 열린다. 그때 1건이 나온다.
    """

    def __init__(self):
        self._open: dict[str, _OpenTrade] = {}
        self.closed: list[Trade] = []
        self.realized_pnl: float = 0.0      # 순손익 누적 (비용 차감 후)
        self.total_cost: float = 0.0        # 지불한 비용 누적

        # ── 체결 원장 ──
        # 시간순 append-only. 거래(Trade)로 묶기 전의 원자료다.
        # Trade 에도 fills 를 넣지만 그건 '이 거래를 구성한 체결'이고,
        # 이쪽은 미청산 포지션의 체결까지 포함한 전체 기록이다.
        self.fills: list[Fill] = []

    def on_fill(self, fill: Fill) -> Optional[Trade]:
        """체결 하나 투입. 라운드트립이 완결됐으면 Trade, 아니면 None."""
        signed = fill.size if fill.side is Side.BUY else -fill.size
        self.fills.append(fill)             # ★ 원장에는 무조건 남긴다
        op = self._open.get(fill.symbol)

        # ── ① 신규 진입 ──
        if op is None:
            self._open[fill.symbol] = _OpenTrade(
                symbol=fill.symbol, size=signed, peak_size=signed,
                entry_price=fill.price, entry_dt=fill.dt,
                commission=fill.commission, entry_fills=1,
                fills=[fill])
            return None

        # ── ② 같은 방향 = 불타기/물타기 ──
        if (op.size > 0) == (signed > 0):
            total = op.size + signed
            # 진입 평단: (기존평균×기존수량 + 이번가격×이번수량) ÷ 총수량
            op.entry_price = (op.entry_price * op.size
                              + fill.price * signed) / total
            op.size = total
            if abs(total) > abs(op.peak_size):
                op.peak_size = total
            op.commission += fill.commission
            op.entry_fills += 1
            op.fills.append(fill)
            return None

        # ── ③ 반대 방향 = 청산 / 부분청산 / 반전 ──
        return self._close(op, fill, signed)

    def _close(self, op: _OpenTrade, fill: Fill,
               signed: float) -> Optional[Trade]:
        """반대 방향 체결. 완전 청산·반전이면 Trade 를 발행한다.

        예시: 롱 10주 보유 → 매도 4주
            closing = 4
            실현손익 += (매도가 - 진입평단) × 4
            아직 6주 남았으므로 Trade 는 발행하지 않는다.
        """
        closing = min(abs(signed), abs(op.size))
        direction = 1.0 if op.size > 0 else -1.0

        # 이번 체결 중 청산에 쓰인 비율. 반전이면 1보다 작다.
        #   (나머지는 새 포지션을 여는 데 쓰였으므로 다음 거래의 비용이다)
        exit_ratio = closing / abs(signed)

        # 실현손익 누적 — 숏이면 direction 이 -1 이라 부호가 뒤집힌다
        op.gross_pnl += (fill.price - op.entry_price) * closing * direction
        op.exit_value += fill.price * closing
        op.exit_qty += closing
        op.commission += fill.commission * exit_ratio
        op.exit_fills += 1
        op.fills.append(fill)

        remaining = op.size + signed

        # ── 부분 청산: 아직 안 끝났다. 손익만 쌓고 반환하지 않는다 ──
        if abs(remaining) > EPS and (remaining > 0) == (op.size > 0):
            op.size = remaining
            return None

        # ── 완전 청산 또는 반전: 라운드트립 종료 ──
        trade = op.to_trade(fill.dt)
        self.closed.append(trade)
        self.realized_pnl += trade.pnl
        self.total_cost += trade.commission

        if abs(remaining) < EPS:
            self._open.pop(fill.symbol, None)
        else:
            # 반전 — 남은 수량은 '이번 체결가로 새로 잡은' 포지션이다.
            # 옛 진입가를 물려주면 다음 거래 손익이 통째로 틀어진다.
            # 이번 체결 수수료 중 청산에 안 쓰인 몫이 새 거래의 진입 비용.
            self._open[fill.symbol] = _OpenTrade(
                symbol=fill.symbol, size=remaining, peak_size=remaining,
                entry_price=fill.price, entry_dt=fill.dt,
                commission=fill.commission * (1.0 - exit_ratio),
                entry_fills=1, fills=[fill])   # 반전 체결은 양쪽에 다 속한다
        return trade

    # ───────── 조회 ─────────
    def open_trade(self, symbol: str) -> Optional[_OpenTrade]:
        """진행 중인 라운드트립 하나. 없으면 None.

        단일 종목 조회가 훨씬 흔해서 편의 메서드로 둔다."""
        return self._open.get(symbol)

    def open_trades(self, symbol: str | None = None) -> list[_OpenTrade]:
        """진행 중인 라운드트립 목록. symbol=None 이면 전 종목.

        ■ 항상 리스트를 반환한다
            Broker.open_orders 와 같은 규약이다. 인자에 따라 반환 타입이
            바뀌면(단건이면 객체, 전체면 dict) 호출부가 매번 분기해야 하고
            None 검사를 dict 이 통과해버리는 사고가 난다.

        ■ 내부 dict 을 그대로 주지 않는다
            리스트로 만들면 자연히 복사본이 되어 호출자가 _open 을
            직접 수정할 수 없다."""
        return [o for s, o in self._open.items()
                if symbol is None or s == symbol]

    # ───────── 사후 분석용 내보내기 ─────────
    def fill_records(self, strategy_id: str = "") -> list[dict]:
        """체결 원장을 dict 리스트로. DataFrame 재료다.

        fillvalue = size × price (체결 대금). 수수료는 별도 컬럼으로 두어
        세전/세후를 모두 볼 수 있게 한다."""
        return [{
            "datetime": f.dt,
            "symbol": f.symbol,
            "side": f.side.value,               # "buy" / "sell"
            "size": f.size,                     # 항상 양수. 방향은 side 가 갖는다
            "price": f.price,
            "fillvalue": f.size * f.price,
            "commission": f.commission,
            "order_id": f.order_id,
            "strategy_id": strategy_id,
        } for f in self.fills]

    def trade_records(self, strategy_id: str = "") -> list[dict]:
        """완결된 라운드트립을 dict 리스트로. 거래 단위 분석용."""
        return [{
            "entry_dt": t.entry_dt,
            "exit_dt": t.exit_dt,
            "symbol": t.symbol,
            "direction": "long" if t.is_long else "short",
            "size": abs(t.size),
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "gross_pnl": t.gross_pnl,
            "commission": t.commission,
            "pnl": t.pnl,
            "pnl_pct": t.pnl_pct,
            "entry_fills": t.entry_fills,
            "exit_fills": t.exit_fills,
            "duration": t.duration,
            "strategy_id": strategy_id,
        } for t in self.closed]

    def stats(self) -> dict:
        """전략 성과 요약. 라운드트립 단위라 승률이 직관적으로 나온다."""
        n = len(self.closed)
        if not n:
            return {"trades": 0}
        wins = [t for t in self.closed if t.is_win]
        losses = [t for t in self.closed if not t.is_win]
        gross_win = sum(t.pnl for t in wins)
        gross_loss = -sum(t.pnl for t in losses)
        return {
            "trades": n,
            "win_rate": len(wins) / n,
            "realized_pnl": self.realized_pnl,
            "total_cost": self.total_cost,
            "avg_win": gross_win / len(wins) if wins else 0.0,
            "avg_loss": gross_loss / len(losses) if losses else 0.0,
            "profit_factor": (gross_win / gross_loss if gross_loss
                              else float("inf")),
        }

    # ───────── 재시작 대비 ─────────
    def to_state(self) -> dict:
        """진행 중인 라운드트립은 봉으로 복원할 수 없다.
        진입가·진입시각·누적손익·누적비용 전부 저장해야 한다."""
        return {
            "open": {s: {"size": o.size, "peak_size": o.peak_size,
                         "entry_price": o.entry_price,
                         "entry_dt": o.entry_dt.isoformat() if o.entry_dt else None,
                         "exit_value": o.exit_value, "exit_qty": o.exit_qty,
                         "gross_pnl": o.gross_pnl, "commission": o.commission,
                         "entry_fills": o.entry_fills, "exit_fills": o.exit_fills}
                     for s, o in self._open.items()},
            "realized_pnl": self.realized_pnl,
            "total_cost": self.total_cost,
        }

    def from_state(self, state: dict):
        self._open = {}
        for s, d in state.get("open", {}).items():
            dt = d.get("entry_dt")
            self._open[s] = _OpenTrade(
                symbol=s, size=d["size"], peak_size=d.get("peak_size", d["size"]),
                entry_price=d["entry_price"],
                entry_dt=datetime.fromisoformat(dt) if dt else None,
                exit_value=d.get("exit_value", 0.0),
                exit_qty=d.get("exit_qty", 0.0),
                gross_pnl=d.get("gross_pnl", 0.0),
                commission=d.get("commission", 0.0),
                entry_fills=d.get("entry_fills", 0),
                exit_fills=d.get("exit_fills", 0))
        self.realized_pnl = state.get("realized_pnl", 0.0)
        self.total_cost = state.get("total_cost", 0.0)


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


# ═══════════════════════════════════════════════════════════════════
# 4. Strategy — 사용자가 상속하는 클래스
# ═══════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class IndicatorSnapshot:
    """지표 값 하나. 기록용 long 포맷.

    ■ 이것만 별도 타입인 이유
        Tick/Quote/Bar/Fill/Order/Trade 는 이미 객체가 있으니 그대로
        기록하면 된다. 지표 값은 대응하는 객체가 없어서 여기서 만든다.
        (저장용 클래스를 데이터 종류마다 만드는 건 순수 중복이다)

    ■ 왜 long 인가 (지표마다 컬럼을 만들지 않고)
        전략마다 지표가 다르고, 전략을 고치면 지표가 바뀐다.
        wide 로 하면 스키마가 계속 변해 과거 데이터와 안 맞는다.
        long 이면 스키마가 고정이고, 분석할 때 pivot 하면 된다.

    ■ line
        MACD(macd/signal/hist)나 볼린저(top/mid/bot)처럼 값이 여럿인
        지표는 라인마다 한 행이 된다. 단일 값이면 "".

    ■ trigger
        "order" 주문 낼 때 — 사후 분석의 대부분이 여기서 나온다 (아직 미구현)
        "bar"/"tick"/"quote"  그 이벤트가 지표를 갱신할 때마다 — ev.kind 그대로.
                              봉이면 봉 마감마다, 틱 지표면 체결마다.
    """
    dt: datetime
    strategy_id: str
    symbol: str
    label: str                  # "SMA(20)@005930@60s"
    line: str                   # "" | "top" | "signal" ...
    value: Optional[float]
    trigger: str = ""
    order_id: str = ""          # trigger="order" 일 때 체결 원장과 조인


@dataclass(frozen=True)
class AccountSnapshot:
    """전략(StrategyBroker) 하나의 현금·평가액 스냅샷.

    ■ 왜 IndicatorSnapshot과 같은 이유로 필요한가
        cash/equity 는 Broker.@property 라 스스로 이벤트를 만들지 않는다.
        누군가 '지금' 읽어서 찍어줘야 기록·뷰에 남는다.

    ■ 왜 feed_timer 에서 찍나 (특정 심볼 이벤트가 아니라)
        equity 는 그 전략이 들고 있는 모든 종목의 평가액 합이다. 봉 마감
        같은 특정 심볼 이벤트에 묶으면, 그 전략이 구독 안 한 종목의 가격이
        움직여 equity 가 바뀌어도 못 찍는다. feed_timer(보통 1초 간격)는
        심볼과 무관하게 전 전략을 고르게 훑으므로 이 문제가 없다."""
    dt: datetime
    strategy_id: str
    cash: float
    equity: float


@dataclass(frozen=True)
class _IndSlot:
    """지표 하나 + 그 지표를 무엇으로 갱신할지.

    ■ 왜 지표를 감싸나
        '어떤 피드로 갱신하는가'는 지표의 속성이 아니다. 같은 SMA(20)을
        봉에도 틱에도 쓸 수 있어야 하고, 그 선택은 전략이 등록할 때 한다.
        지표 클래스에 kind/variant 를 넣으면 지표가 이벤트 체계를 알게 되어
        재사용이 막힌다.

        그래서 지표는 update/value/ready/warmup 만 알고, 매칭은 이 슬롯이 한다.
        matches() 가 지표가 아니라 여기 있는 이유다."""
    ind: object             # 지표 본체 (SMA, ATR, ...)
    kind: str = "bar"       # "bar" | "tick" | "quote" | 앞으로 생길 kind
    variant: object = None  # 봉이면 주기(초). None 이면 그 kind 아무거나
    symbol: Optional[str] = None    # None 이면 구독 중인 전 종목
    override: Optional[str] = None  # 이름을 직접 지정하고 싶을 때

    def matches(self, ev: MarketEvent) -> bool:
        """★ symbol 을 빼먹으면 안 된다 ★
        두 종목을 구독한 전략에서 symbol 을 안 보면 7만원짜리와 20만원짜리
        가격이 한 SMA 에 섞여 들어간다. 값이 조용히 무의미해진다."""
        return (self.kind == ev.kind
                and (self.variant is None or self.variant == ev.variant)
                and (self.symbol is None or self.symbol == ev.symbol))

    @property
    def key(self) -> tuple:
        return (self.symbol, self.kind, self.variant)

    @property
    def label(self) -> str:
        """기록·로그용 식별자. 지표 이름 + 맥락(종목·주기).

            SMA20                    단일 종목, 봉 주기 하나
            SMA20@005930             여러 종목을 구독할 때
            SMA20@005930@60s         멀티 타임프레임일 때
            SMA20_price@005930@tick  틱 지표

        지표 본체는 자기 이름만 알고, 종목·피드는 여기서 붙인다.
        override 가 있으면 그것을 쓴다 — 'fast'/'slow' 처럼 의미로
        부르고 싶을 때가 있다."""
        if self.override:
            return self.override
        parts = [self.ind.name]
        if self.symbol:
            parts.append(self.symbol)
        if self.kind != "bar":
            parts.append(self.kind)
        elif self.variant:
            parts.append(f"{self.variant}s")
        return "@".join(parts)


class Strategy:
    """전략 베이스.

    ■ 이 클래스는 실행 모드를 모른다
        self.broker 가 시뮬레이션인지 실계좌인지 알 방법이 없고,
        알 필요도 없다. 그게 이 설계의 목적이다.

    ■ broker 는 언제 생기나
        Trader.__init__ 이 밖에서 꽂아준다. 그래서 setup() 안에서는
        아직 없다. 브로커가 필요한 초기화는 on_start() 에 쓸 것.
    """
    defaults: dict = {}         # 파라미터 기본값. 하위 클래스가 덮어쓴다

    # Trader 가 주입한다 (타입 힌트일 뿐, 여기서 대입하지 않음)
    broker: Broker
    trader: "Trader"

    def __init__(self, **params):
        # defaults 위에 사용자 인자를 덮어써서 self.p 를 만든다.
        # SimpleNamespace 라서 self.p.fast 처럼 점으로 접근된다.
        self.p = SimpleNamespace(**{**self.defaults, **params})
        self._indicators = []
        # 프레임워크 배선이 끝난 뒤 사용자 코드를 부른다.
        # 이 순서 덕분에 사용자는 super().__init__() 을 기억할 필요가 없다.
        self.setup()

    def ind(self, indicator, on: str = "bar", variant=None, symbol=None,
            name: Optional[str] = None):
        """지표 등록. 선언한 피드가 올 때마다 자동으로 update 된다.

        ■ on / variant — 어떤 이벤트로 갱신할지
            ind(SMA(20))                        1분봉이든 5분봉이든 오는 봉으로
            ind(SMA(20), on="bar", variant=60)  1분봉으로만
            ind(SMA(20), on="bar", variant=300) 5분봉으로만
            ind(SMA(20), on="tick")             체결 틱마다
            ind(Imbalance(), on="quote")        호가 갱신마다

        ■ symbol — 여러 종목을 구독할 때 필수
            ind(SMA(20), on="tick")                     구독 중인 전 종목이 섞인다
            ind(SMA(20), on="tick", symbol="005930")    이 종목 틱만

            ★ 두 종목 이상 구독하면 반드시 지정할 것 ★
              안 하면 7만원짜리와 20만원짜리 가격이 한 지표에 섞여
              값이 조용히 무의미해진다. 종목마다 세트가 필요하면
              per_symbol() 을 쓰는 게 편하다.

          지표를 봉으로만 돌린다는 가정을 두지 않는다. 체결강도 이동평균,
          호가 불균형 평활 같은 건 봉이 아니라 틱·호가로 굴러야 한다.

          ★ 멀티 타임프레임에서 variant 를 반드시 지정할 것 ★
            1분봉과 5분봉을 둘 다 구독하는데 variant 를 비워두면
            같은 지표가 두 주기로 번갈아 갱신되어 값이 뒤섞인다.

        ■ 지표가 읽는 필드
            SMA(period, src="close") 처럼 src 를 필드명으로 받는다.
            봉이면 "close", 틱이면 "price" 를 넘기면 그대로 동작한다.
                ind(SMA(20, src="price"), on="tick")

        ★ 등록 순서 = 계산 순서 ★
          CrossOver 처럼 다른 지표를 입력으로 받는 것은 반드시 뒤에 등록.
          먼저 등록하면 갱신 안 된 값으로 계산해서 '조용히' 틀린다.

        ■ 넣는 것과 돌려주는 것이 다르다
            내부 목록에는 _IndSlot(지표 + kind + variant)이 들어가고,
            반환은 지표 본체다. 전략에서 self.fast.value 로 쓰려면
            본체여야 하고, 매칭 정보는 전략이 알 필요가 없기 때문이다."""
        slot = _IndSlot(indicator, on, variant, symbol, name)
        # 같은 label 이 둘이면 기록에서 구별이 안 된다. 등록 시점에 잡는다.
        if any(s.label == slot.label for s in self._indicators):
            log.warning("지표 이름 충돌: %s — name= 로 구분해 주세요", slot.label)
        self._indicators.append(slot)
        return indicator

    def per_symbol(self, factory, symbols, on: str = "bar", variant=None):
        """종목마다 지표를 하나씩 만들어 등록한다. {종목: 지표} 를 돌려준다.

            self.sma = self.per_symbol(lambda: SMA(20), SYMBOLS, on="tick")
            ...
            self.sma[t.symbol].value

        factory 가 함수인 이유: 종목마다 '별개의' 인스턴스가 필요하다.
        하나를 만들어 돌려쓰면 모든 종목이 같은 버퍼를 공유해 섞인다."""
        return {sym: self.ind(factory(), on=on, variant=variant, symbol=sym)
                for sym in symbols}

    def indicator_snapshot(self, ev: Optional[MarketEvent] = None) -> dict[str, Optional[float]]:
        """지금 지표들의 값. {label: value}

        기록·로그용. Trader 가 이벤트 처리 직후(훅 호출 직전)에 찍으면
        '전략이 판단할 때 본 값'이 남는다.

        ev 를 주면 그 이벤트에 매칭되는(= 방금 갱신된) 지표만 돌려준다.
        안 주면 등록된 지표 전부의 현재 상태를 돌려준다.

        ■ 왜 필요한가
            한 전략이 여러 봉 주기(예: 60초+300초)를 같이 구독하면,
            필터 없이 전부 돌려줄 경우 60초봉이 마감될 때마다 아직
            안 바뀐 300초봉 지표까지 매번 같이 기록돼 중복이 쌓인다.
            _record_indicators 가 이걸로 '방금 이 이벤트가 실제로
            갱신한 지표'만 골라 기록한다."""
        slots = (self._indicators if ev is None
                else [s for s in self._indicators if s.matches(ev)])
        return {slot.label: slot.ind.value for slot in slots}

    @property
    def ready(self) -> bool:
        """등록한 지표가 전부 값을 낼 수 있나. SMA(60)은 60봉이 쌓여야 True.

        보수적으로 '전부'를 본다. 봉 지표와 틱 지표를 섞어 쓰면 둘 다
        데워질 때까지 훅이 안 불린다 — 어중간한 상태로 주문하는 것보다 낫다."""
        return all(slot.ind.ready for slot in self._indicators)

    def required_history(self) -> dict[tuple, int]:
        """지표들이 요구하는 워밍업 분량. {(symbol, kind, variant): 개수}

            {("005930","bar",60): 60, (None,"tick",None): 50}
            symbol 이 None 이면 '구독 중인 아무 종목이나'라는 뜻이다.

        같은 피드를 여러 지표가 쓰면 가장 큰 값을 취한다.
        warmup 을 모르는 지표(None)는 세지 않으므로, 그런 지표가 있으면
        실시간으로 데워질 때까지 기다리게 된다.

        Engine.add 에 넘길 history 를 준비할 때 이걸 보고 REST 조회량을
        정하면 된다."""
        need: dict[tuple[str, object], int] = {}
        for slot in self._indicators:
            n = getattr(slot.ind, "warmup", None)
            if not n:
                continue
            need[slot.key] = max(need.get(slot.key, 0), int(n))
        return need

    def _update_indicators(self, ev: MarketEvent):
        """Trader 가 모든 이벤트에 대해 부른다. 필터는 여기서 한다.

        등록 순서대로 돌되, 선언한 피드와 맞는 것만 갱신한다."""
        for slot in self._indicators:
            if slot.matches(ev):
                slot.ind.update(ev)

    # ───────── 사용자가 구현하는 훅 ─────────
    def setup(self):
        """지표 생성, 초기 변수. broker 는 아직 없다."""

    def on_start(self):
        """워밍업·상태복원 완료 후 1회. 여기서는 broker 를 쓸 수 있다."""

    def on_bar(self, bar: Bar):
        """봉이 확정될 때마다.

        베이스의 훅들은 전부 '아무것도 안 함' 기본 구현이다.
        raise 하지 않는 이유: 구독했는데 훅을 안 만들었으면
        EventRouter.register 가 등록 시점에 경고한다. 실행 중에
        매 봉 예외를 던지는 것보다 그쪽이 훨씬 빨리 눈에 띈다."""

    def on_order(self, order: Order):
        """주문 상태 변화(접수/체결/거부/취소)."""

    def on_trade(self, trade: Trade):
        """거래가 완결되어 손익이 확정됐을 때."""

    def on_timer(self, now: datetime):
        """주기적 호출. 봉과 무관한 시각 기반 로직(종가청산 등)."""

    def on_stop(self):
        """종료 시 정리."""

    # ───────── 재시작 대비 ─────────
    def to_state(self) -> dict:
        """봉으로 복원할 수 없는 것만 저장한다.

        지표는 과거 봉을 다시 흘리면 복원되지만,
        '진입 후 최고가 기준 트레일링 스탑' 같은 건 봉에 없는 정보라
        저장하지 않으면 영영 잃는다."""
        return {}

    def from_state(self, state: dict): ...


# ═══════════════════════════════════════════════════════════════════
# 5. Trader — 조립하고, 이벤트를 전략에 흘린다
# ═══════════════════════════════════════════════════════════════════

class Trader:
    """수동(passive) 이벤트 수신자. 루프를 소유하지 않는다.

    ■ 왜 수동인가
        실전   : 소켓 → trading_q → EngineTrader → Engine → 여기
        백테스트: cerebro → _Pump → Engine → 여기

        어느 쪽이든 Engine 이 dispatch() 를 불러준다. Trader 는 루프도
        모르고 자기가 어느 모드에 있는지도 모른다.

        루프 주인이 반대인데, Trader 가 밀어넣기만 받으면 그 비대칭이
        Trader 입장에서는 보이지 않는다.

    ■ Trader 가 없으면 흩어지는 것들
        GuardBroker 주입 / 워밍업 카운터 / TradeTracker 배선 /
        상태 저장·복원 / 전략 예외 격리
        — 전부 실전에 필요한 안전장치라 없앨 수 없고,
          없애면 전략마다 복붙될 뿐 총량은 안 준다.
    """

    def __init__(self, broker: Broker, strategy: Strategy,
                 warmup: int = 0, dry_run: bool = True,
                 state_path: str | None = None,
                 strategy_id: str = "", recorder=None, view_q=None):
        self.raw_broker = broker            # 어댑터가 내부 상태에 접근할 때 씀
        # 전략에게는 감싼 것을 준다. 킬스위치가 우회되지 않도록.
        self.broker = GuardBroker(broker, enabled=False, dry_run=dry_run)

        self.strategy = strategy
        strategy.broker = self.broker       # ★ 주입은 여기 두 줄이 전부
        strategy.trader = self

        self.strategy_id = strategy_id
        # IndicatorSnapshot 저장/화면표시용. 둘 다 None 이면 아무 일도 안 한다
        # (백테스트에서 매번 새로 만들 필요가 없도록 옵션으로 둔다).
        # Engine 이 자기 recorder/view_q 를 그대로 물려준다 — 다른 파생
        # 데이터(Bar)와 같은 두 큐를 공유해야 뷰·저장이 한 경로로 모인다.
        self.recorder = recorder
        self.view_q = view_q

        self.tracker = TradeTracker()
        self.warmup = warmup                # 이 봉 수까지는 on_bar 를 안 부른다
        self.state_path = Path(state_path) if state_path else None
        self._bars = 0
        self._started = False

    # ───────── 기동 ─────────
    def start(self, history: "list[MarketEvent]" = ()):
        """순서가 중요하다.
            ① 과거 이벤트로 지표 워밍업 (주문은 GuardBroker 가 막음)
            ② 저장된 상태 복원
            ③ 거래 허용 → on_start()

        ■ history 는 봉만이 아니다
            Bar(60초) / Bar(300초) / Tick / Quote 를 섞어 넣을 수 있다.
            _update_indicators 가 (kind, variant) 로 걸러주므로
            1분봉은 1분봉 지표만, 틱은 틱 지표만 데운다.
            시간순으로 정렬해서 넣을 것.

        ■ 봉 카운터는 봉만 센다
            warmup 파라미터는 봉 기준이라, 틱/호가는 세지 않는다.
        """
        for ev in history:
            self.strategy._update_indicators(ev)
            if getattr(ev, "kind", "bar") == "bar":
                self._bars += 1
            # ★ 훅을 부르지 않는다. 과거 신호로 주문이 나가면 안 되니까.

        if self.state_path and self.state_path.exists():
            state = json.loads(self.state_path.read_text())
            # ★ 브로커를 먼저 복원한다 ★
            #   on_start() 에서 self.broker.position() 을 조회할 수 있어야 하고,
            #   이게 빠지면 전략이 flat 인 줄 알고 중복 진입한다.
            self.broker.from_state(state.get("broker", {}))
            self.strategy.from_state(state.get("strategy", {}))
            self.tracker.from_state(state.get("tracker", {}))

        self.broker.enabled = True          # 이제부터 주문 허용
        self._started = True
        self._safe(self.strategy.on_start)
        log.info("기동 — 지표 ready=%s, dry_run=%s",
                 self.strategy.ready, self.broker.dry_run)

    def stop(self):
        self._safe(self.strategy.on_stop)
        self._save()

    def enable_trading(self):
        """실주문 허용. 이미 켜져 있으면 아무 일도 안 한다(멱등).

        반환값으로 '상태가 바뀌었는지'를 알려준다 — 폴링 루프가 매초
        불러도 로그가 도배되지 않게 하기 위함."""
        if not self.broker.dry_run:
            return False
        self.broker.dry_run = False
        log.warning("[%s] 실주문 활성화", type(self.strategy).__name__)
        return True

    def disable_trading(self, reason: str = ""):
        """실주문 차단. 장중 비상정지용. 멱등."""
        if self.broker.dry_run:
            return False
        self.broker.dry_run = True
        log.warning("[%s] 실주문 차단 %s",
                    type(self.strategy).__name__,
                    f"— {reason}" if reason else "")
        return True

    @property
    def trading_enabled(self) -> bool:
        return not self.broker.dry_run

    # ───────── 밖에서 밀어넣는 이벤트 ─────────
    def dispatch(self, ev: MarketEvent):
        """시장 이벤트 하나를 전략에 전달한다.

        ■ 훅을 이름으로 찾는다
              on_tick / on_quote / on_bar / on_{새로운kind}
          새 피드가 생겨도 이 메서드는 안 고친다.

        ■ 게이트 순서
          ① 시작 전이면 무시
          ② 지표 갱신 + 기록 — 이벤트 종류와 무관하게 매번 시도한다
             (매칭되는 지표만 update/record 된다. 봉으로 국한하면
              틱·호가 기반 지표는 영영 안 데워지고 기록도 안 남는다)
          ③ 워밍업 카운터는 봉 기준으로 센다
          ④ 지표가 준비 안 됐으면 on_bar 만 막는다
             — on_tick/on_quote 는 지표와 무관할 수 있으므로 통과시킨다
        """
        if not self._started:
            return

        # ① 지표 갱신 — 모든 이벤트에 대해 시도한다.
        #    어떤 지표가 이 이벤트를 쓸지는 Strategy._update_indicators 가
        #    등록 시 선언한 (kind, variant, symbol) 로 걸러낸다.
        self.strategy._update_indicators(ev)

        # 지표 기록 — 이 이벤트에 매칭되는(=방금 갱신된) 지표만 기록한다.
        # 봉으로 국한하지 않는다 — 틱/호가 기반 지표가 생기면 그 이벤트마다
        # 기록·뷰에 남아야 봉 사이의 값을 확인할 수 있다.
        self._record_indicators(ev)

        # ② 워밍업 카운터는 봉 기준이다.
        #    틱은 수만 개가 들어오므로 개수로 세는 게 의미가 없다.
        #    틱 전략의 워밍업은 지표의 ready 가 대신 막아준다.
        if ev.kind == "bar":
            self._bars += 1
            if self._bars <= self.warmup:
                return

        # ③ 지표가 준비 안 됐으면 어떤 훅도 부르지 않는다.
        #    (지표가 없는 전략은 all([]) == True 라 그냥 통과한다)
        if not self.strategy.ready:
            return

        hook = getattr(self.strategy, f"on_{ev.kind}", None)
        if hook is not None:
            self._safe(hook, ev)

    def __repr__(self):
        """라우터 경고 등에 쓰인다. 어느 전략인지 보여야 쓸모가 있다."""
        return f"Trader({type(self.strategy).__name__})"

    def handles(self, kind: str) -> bool:
        """이 전략이 해당 종류의 이벤트를 '실제로' 처리하나.

        ★ hasattr 로는 판정할 수 없다 ★
          Strategy 베이스가 on_bar/on_order/on_trade/on_timer 를 빈 메서드로
          이미 갖고 있어서, hasattr 는 어떤 전략에든 True 를 돌려준다.
          그러면 on_bar 오타를 잡아내지 못한다.

        그래서 '클래스가 베이스 것을 덮어썼는가'를 본다.
        on_tick 처럼 베이스에 아예 없는 훅은 존재 여부만 보면 된다."""
        name = f"on_{kind}"
        mine = getattr(type(self.strategy), name, None)
        if mine is None:
            return False
        base = getattr(Strategy, name, None)
        return base is None or mine is not base

    def feed_order(self, order: Order):
        """주문 상태 변화."""
        self._safe(self.strategy.on_order, order)
        if order.is_done:
            self._save()

    def feed_fill(self, fill: Fill):
        """체결 하나를 처리한다. 순서가 중요하다.

            ① 브로커 상태 갱신 — 내 가상 포지션 반영
            ② TradeTracker — 진입/청산 짝을 맞춰 Trade 조립
            ③ on_trade 훅 — 전략이 포지션을 조회할 수 있어야 하므로 ① 뒤

        ①을 Engine 이 아니라 여기서 하는 이유: 브로커는 이 Trader 의
        소유물이다. 자기 것을 자기가 갱신해야 순서가 어긋날 여지가 없다.

        ★ 백테스트와 실전이 같은 TradeTracker 를 통과하는 지점 ★"""
        self.broker.apply_fill(fill)                # ①
        trade = self.tracker.on_fill(fill)          # ②
        if trade is not None:
            trade.strategy_id = self.strategy_id    # TradeTracker는 이걸 모른다
            self._safe(self.strategy.on_trade, trade)
            # 라운드트립 완결 — recorder/view_q 에도 흘린다. TradeTracker
            # 안(완전청산 분기)에서 하지 않는 이유: 그 분기는 '끝난 뒤
            # _open 을 지울지 반전으로 새로 열지'를 가르는 것일 뿐이고,
            # trade 는 반전이든 완전청산이든 이미 위에서 완성돼 있다 —
            # 거기서 if 분기 안에만 넣으면 반전으로 끝난 거래를 놓친다.
            # IndicatorSnapshot 과 같은 자리(Trader, self.recorder/view_q)에서
            # 같은 패턴으로 처리한다.
            if self.recorder is not None:
                self.recorder.put(trade)
            if self.view_q is not None:
                self.view_q.put(trade)
            self._save()

    def feed_timer(self, now: datetime):
        if self._started:
            self._view_account(now)
            self._safe(self.strategy.on_timer, now)

    # ───────── 내부 ─────────
    def _view_account(self, now: datetime):
        """cash/equity 스냅샷을 recorder/view_q 에 흘린다.

        _record_indicators 와 같은 이유(property는 스스로 이벤트를 못 만듦)
        지만, 트리거는 특정 심볼 이벤트가 아니라 feed_timer 다 —
        AccountSnapshot 클래스 docstring 참고."""
        if self.recorder is None and self.view_q is None:
            return
        snap = AccountSnapshot(dt=now, strategy_id=self.strategy_id,
                               cash=self.broker.cash, equity=self.broker.equity)
        if self.view_q is not None:
            self.view_q.put(snap)

    def _record_indicators(self, ev: MarketEvent):
        """이 이벤트로 갱신된 지표 값을 IndicatorSnapshot(long 포맷)으로
        recorder 에 남기고 view_q 에도 흘려 콘솔 뷰(Pivot 등 구독 화면)에서
        바로 보이게 한다.

        봉으로 국한하지 않는다 — 틱/호가 기반 지표가 등록돼 있으면 그
        이벤트가 올 때마다 기록된다(indicator_snapshot(ev) 가 이 이벤트에
        매칭되는 지표만 걸러준다).

        지표는 Engine.feed() 가 아니라 여기(Trader) 안에서만 만들어지는
        데이터라, Engine 이 자기 view_q/recorder 를 물려준 것을 그대로 쓴다
        — 그래야 Bar/외부이벤트(Engine.feed() 담당)와 같은 두 큐로 합쳐진다.

        ev.dt 를 쓰는 이유: 기록 시각이 아니라 '그 이벤트 시각'이어야
        재생·조인이 맞는다. 둘 다 None 이면(기록·뷰를 안 켰거나 백테스트)
        아무 일도 하지 않는다."""
        if self.recorder is None and self.view_q is None:
            return
        for label, value in self.strategy.indicator_snapshot(ev).items():
            snap = IndicatorSnapshot(
                dt=ev.dt, strategy_id=self.strategy_id, symbol=ev.symbol,
                label=label, line="", value=value, trigger=ev.kind,
            )
            if self.recorder is not None:
                self.recorder.put(snap)
            if self.view_q is not None:
                self.view_q.put(snap)

    def _safe(self, fn, *args):
        """전략이 터져도 엔진은 살아있어야 한다.

        백테스트라면 예외로 죽는 게 낫지만, 실전에서는 봉 하나 처리에
        실패했다고 프로세스가 죽으면 포지션을 든 채 방치된다."""
        try:
            fn(*args)
        except Exception:
            log.exception("전략 예외: %s", getattr(fn, "__name__", fn))

    def _save(self):
        """이 전략에 딸린 상태 전부를 한 파일에 저장한다.

            broker   전략별 가상 포지션·현금·미체결 주문 (StrategyBroker)
            strategy 트레일링 스탑처럼 봉으로 재계산 못 하는 값
            tracker  미청산 거래의 진입가·진입시각·누적수수료

        셋 다 '이 전략의 것'이라 한 파일에 묶는다. 전략을 하나 빼거나
        추가해도 다른 전략 파일은 건드릴 일이 없다."""
        if not self.state_path:
            return
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps({
                "broker": self.broker.to_state(),
                "strategy": self.strategy.to_state(),
                "tracker": self.tracker.to_state(),
            }, ensure_ascii=False))
        except Exception:
            log.exception("상태 저장 실패")