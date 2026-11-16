"""
═══════════════════════════════════════════════════════════════════
 trades.py — 거래(라운드트립) 조립 (Trade / TradeTracker)
═══════════════════════════════════════════════════════════════════

옛 위치: alpha/trader/trading.py 의 Trade, _OpenTrade 와 "2. TradeTracker" 절.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from alpha.core.orders import EPS, Fill, Side


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
