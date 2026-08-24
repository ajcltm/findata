"""
═══════════════════════════════════════════════════════════════════
 engine.py — 멀티 종목 · 멀티 전략 조립
═══════════════════════════════════════════════════════════════════

                    ┌──────────── Engine ────────────┐
   웹소켓/백테스트 → │  BarFactory   틱 → 봉 (공유)    │
                    │  EventRouter  구독자에게만 배달  │
                    │  PortfolioBroker  계좌 분할     │
                    └───┬────────────┬───────────────┘
                        │            │
                 Trader(A)      Trader(B)      ← 전략별 래퍼
                   │                │            (가드/워밍업/손익/상태/예외격리)
              StrategyBroker   StrategyBroker
                        └──── 실브로커 ────┘      KISBroker | BacktraderBroker

■ 각자 뭘 하나
    Engine          조립과 배달. 이벤트를 받아 알맞은 Trader 에 넘긴다
    BarFactory      틱을 봉으로. (symbol, seconds) 당 하나만 만들어 공유한다
    EventRouter     (종목, 종류) → 구독 Trader 목록
    Trader          전략 하나를 감싼다. 기존 그대로 재사용
    PortfolioBroker 전략별 가상 계좌. 체결을 주인에게 되돌려준다

■ 왜 Trader 를 그대로 두나
    Trader 가 하는 일(dry-run 가드, 워밍업, 손익 추적, 상태 저장, 예외 격리)은
    전부 '전략 하나당' 필요한 것이다. 멀티가 되어도 성격이 안 바뀐다.
    Engine 은 그 위에서 배달만 한다.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from alpha.events.events import Bar, EventRouter, MarketEvent, Quote, Subscription, Tick
from alpha.broker.portfolio_broker import PortfolioBroker, StrategyBroker
from alpha.trader.trading import Broker, Fill, Order, Strategy, Trader

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# 1. BarFactory — 틱을 봉으로. 여러 전략이 공유한다
# ═══════════════════════════════════════════════════════════════════

class _Agg:
    """(종목, 주기) 하나에 대한 봉 집계기.

    ■ 슬롯 방식
        slot = 타임스탬프 // 주기(초)
        09:00:00~09:00:59 는 같은 몫이 나온다. 슬롯이 바뀌면 봉을 닫는다.
        시각을 직접 비교하는 것보다 간단하고 경계가 정확하다."""

    def __init__(self, symbol: str, seconds: int):
        self.symbol, self.seconds = symbol, seconds
        self.cur: Optional[dict] = None

    def on_tick(self, dt: datetime, price: float, volume: float) -> Optional[Bar]:
        slot = int(dt.timestamp()) // self.seconds
        if self.cur is None:
            self.cur = self._new(slot, price, volume)
            return None
        if slot != self.cur["slot"]:
            done = self._close(self.cur)
            self.cur = self._new(slot, price, volume)
            return done
        c = self.cur
        c["high"] = max(c["high"], price)
        c["low"] = min(c["low"], price)
        c["close"] = price                  # 마지막 틱 가격이 종가
        c["volume"] += volume
        return None

    def flush(self, now: datetime) -> Optional[Bar]:
        """틱이 안 와도 시간이 지났으면 닫는다.

        틱 기반 집계는 '다음 틱'이 있어야 이전 봉이 닫힌다.
        거래 뜸한 종목은 그 틱이 안 와서 09:05 봉이 09:12에 닫히는 일이 생긴다."""
        if self.cur is None:
            return None
        if int(now.timestamp()) // self.seconds != self.cur["slot"]:
            done, self.cur = self._close(self.cur), None
            return done
        return None

    def _new(self, slot, price, volume):
        # 첫 틱 가격이 시가이자 고가이자 저가이자 종가
        return dict(slot=slot, open=price, high=price,
                    low=price, close=price, volume=volume)

    def _close(self, c) -> Bar:
        # dt 는 봉이 '끝난' 시각. 시작 시각을 쓰면 백테스트에서 미래를 보게 된다.
        end = datetime.fromtimestamp((c["slot"] + 1) * self.seconds)
        return Bar(kind="bar", symbol=self.symbol, dt=end,
                   open=c["open"], high=c["high"], low=c["low"],
                   close=c["close"], volume=c["volume"], seconds=self.seconds)


class BarFactory:
    """틱 → 봉 변환기 모음.

    ■ 왜 공유하나
        전략 10개가 005930 1분봉을 쓴다고 집계기를 10개 만들 이유가 없다.
        (종목, 주기) 조합당 하나만 만들어 결과를 라우터로 흘린다.

    ■ 여러 주기 동시 지원
        같은 틱이 1분봉 집계기와 5분봉 집계기에 동시에 들어간다.
        멀티 타임프레임이 자연스럽게 된다.
    """

    def __init__(self):
        self._aggs: dict[tuple[str, int], _Agg] = {}

    def ensure(self, symbol: str, seconds: int):
        """이 조합의 집계기를 준비한다. 전략이 봉을 구독할 때 부른다."""
        self._aggs.setdefault((symbol, seconds), _Agg(symbol, seconds))

    def on_tick(self, tick: Tick) -> list[Bar]:
        """틱 하나 → 이번에 닫힌 봉들 (여러 주기가 동시에 닫힐 수 있다)."""
        out = []
        for (sym, _), agg in self._aggs.items():
            if sym != tick.symbol:
                continue
            bar = agg.on_tick(tick.dt, tick.price, tick.volume)
            if bar is not None:
                out.append(bar)
        return out

    def flush(self, now: datetime) -> list[Bar]:
        """타이머가 부른다. 시간이 지난 봉들을 강제로 닫는다."""
        out = []
        for agg in self._aggs.values():
            bar = agg.flush(now)
            if bar is not None:
                out.append(bar)
        return out


# ═══════════════════════════════════════════════════════════════════
# 2. Engine — 전부를 묶는다
# ═══════════════════════════════════════════════════════════════════

@dataclass
class Slot:
    """등록된 전략 하나의 자리."""
    sid: str
    trader: Trader
    view: StrategyBroker
    subs: list[Subscription]
    history: list = field(default_factory=list)   # 이 전략 전용 워밍업 이벤트


class Engine:
    """이벤트를 받아 알맞은 전략에 배달하고, 체결을 주인에게 돌려준다.

    ■ 이 클래스도 루프를 돌지 않는다
        실전 : 웹소켓 스레드가 feed_*() 를 호출
        백테스트: _Pump 나 재생기가 feed_*() 를 호출
        양쪽 다 '밖에서 밀어넣는' 구조라 대칭이 맞는다.
    """

    def __init__(self, real_broker: Broker, dry_run: bool = True,
                 state_dir: str | None = None, recorder=None, view_q=None):
        self.portfolio = PortfolioBroker(real_broker)
        self.router = EventRouter()
        self.bars = BarFactory()
        self.slots: dict[str, Slot] = {}
        self.dry_run = dry_run
        self.state_dir = state_dir
        # 전략별 Trader 에 그대로 물려준다. IndicatorSnapshot 저장용
        # (Recorder|None) — None 이면 지표를 기록하지 않는다.
        self.recorder = recorder
        # 콘솔 뷰가 읽는 큐(Application.view_q). feed()/feed_timer() 가 만드는
        # 봉과, 여기로 들어오는 외부 이벤트(Tick/Quote)를 전부 여기로 흘린다
        # — '엔진을 거치는 것'의 저장·뷰가 이 한 곳(Engine)에서 일원화된다.
        # None 이면(백테스트 등) 아무 데도 안 흘린다.
        self.view_q = view_q

    # ───────── 등록 ─────────
    def add(self, strategy_id: str, strategy: Strategy,
            allocation: float,
            ticks: list[str] = (), quotes: list[str] = (),
            bars: list[tuple[str, int]] = (),
            warmup: int = 0,
            history: list = ()) -> Slot:
        """전략 하나를 붙인다.

        ■ history — 이 전략의 지표를 데울 과거 이벤트
            봉만이 아니라 Tick/Quote 도 섞을 수 있다. 지표가 등록 시
            선언한 (kind, variant) 로 걸러지므로, 1분봉은 1분봉 지표만
            데운다. 시간순으로 정렬해서 넣을 것.

                eng.add("추세", S(), allocation=..., 
                        bars=[("005930",60), ("005930",300)],
                        history=[*min1_bars, *min5_bars, *ticks])

            필요한 분량은 strategy.required_history() 로 알 수 있다:
                {("bar",60): 60, ("bar",300): 20}
            전략마다 다르므로 여기서 주입하는 게 맞다 —
            Engine.start 에 한꺼번에 넘기면 전략별로 나눌 수가 없다.

            eng.add("추세A", SmaCross(), allocation=5_000_000,
                    bars=[("005930", 60)], warmup=60)
            eng.add("호가B", Scalper(),  allocation=3_000_000,
                    ticks=["005930"], quotes=["005930"])

        ticks/quotes/bars 는 '무엇을 받을지' 선언이다.
        선언한 것만 배달되므로, 종목 200개를 받아도 전략은 자기 것만 본다."""

        view = self.portfolio.view(strategy_id, allocation)
        trader = Trader(broker=view, strategy=strategy, warmup=warmup,
                        dry_run=self.dry_run,
                        state_path=(f"{self.state_dir}/{strategy_id}.json"
                                    if self.state_dir else None),
                        strategy_id=strategy_id, recorder=self.recorder,
                        view_q=self.view_q)

        subs = [Subscription(s, "tick") for s in ticks]
        subs += [Subscription(s, "quote") for s in quotes]
        for sym, sec in bars:
            # ★ 주기(sec)를 구독에 넣는 게 중요하다 ★
            #   안 넣으면 1분봉 구독자에게 5분봉까지 배달되어
            #   지표가 다른 주기의 값으로 오염된다.
            subs.append(Subscription(sym, "bar", sec))
            self.bars.ensure(sym, sec)      # 집계기 준비 (이미 있으면 재사용)

        # 라우터에는 Trader 를 등록한다. 훅 확인은 Trader.handles 가 대신한다.
        # 전략이 아니라 Trader 를 등록한다 — 이유는 EventRouter 주석 참조.
        # Trader 가 handles()/dispatch() 를 갖고 있어 그대로 구독자가 된다.
        self.router.register(trader, subs)

        slot = Slot(sid=strategy_id, trader=trader, view=view, subs=subs,
                    history=list(history))
        self.slots[strategy_id] = slot

        # 요구량 대비 부족하면 경고. 200일선을 쓰면서 100봉만 주면
        # 장 시작 후 100봉을 더 기다려야 하는데, 조용히 그렇게 된다.
        self._check_history(strategy_id, strategy, slot.history)
        return slot

    def _check_history(self, sid: str, strategy: Strategy, history: list):
        need = strategy.required_history()
        if not need:
            return
        for key, n in need.items():
            sym, kind, variant = key
            # symbol 이 None 인 요구는 '아무 종목이나' 이므로 종목을 안 가린다
            got = sum(1 for ev in history
                      if ev.kind == kind
                      and (variant is None or ev.variant == variant)
                      and (sym is None or ev.symbol == sym))
            if got < n:
                log.warning("[%s] 워밍업 부족 — %s 가 %d개 필요한데 %d개 "
                            "(부족분은 실시간으로 채워질 때까지 대기)",
                            sid, key, n, got)

    def start(self, history: dict[str, list] | None = None):
        """전 전략 기동.

        각 전략은 add() 때 받은 history 로 지표를 데운다. 그동안
        GuardBroker 가 주문을 막으므로 과거 신호로 주문이 안 나간다.

        history 인자를 주면 그 전략의 것을 덮어쓴다 — 백테스트에서
        add() 는 그대로 두고 워밍업만 바꿔 끼울 때 쓴다."""
        override = history or {}
        for sid, slot in self.slots.items():
            slot.trader.start(history=override.get(sid, slot.history))
        log.info("엔진 기동 — 전략 %d개, dry_run=%s", len(self.slots), self.dry_run)

    def stop(self):
        for slot in self.slots.values():
            slot.trader.stop()

    def enable_trading(self, strategy_id: str | None = None) -> bool:
        """실주문 켜기. 전략 하나만 켤 수도 있다.

        멱등하다 — 이미 켜져 있으면 아무 일도 안 하고 False 를 반환한다.
        폴링 루프가 매초 불러도 안전하다."""
        changed = False
        for s in self._targets(strategy_id):
            changed |= s.trader.enable_trading()
        return changed

    def disable_trading(self, strategy_id: str | None = None,
                        reason: str = "") -> bool:
        """실주문 차단. 장중 비상정지.

        ★ 포지션은 건드리지 않는다 ★
          새 주문만 막는다. 보유 중인 포지션을 자동 청산하지 않는다 —
          비상 상황에서 시장가 청산이 더 큰 손실을 낼 수 있고,
          청산 여부는 사람이 판단할 일이다."""
        changed = False
        for s in self._targets(strategy_id):
            changed |= s.trader.disable_trading(reason)
        return changed

    def trading_status(self) -> dict[str, bool]:
        """전략별 실주문 허용 여부."""
        return {sid: s.trader.trading_enabled for sid, s in self.slots.items()}

    def _targets(self, strategy_id: str | None):
        if strategy_id is None:
            return list(self.slots.values())
        slot = self.slots.get(strategy_id)
        if slot is None:
            log.warning("모르는 전략 id: %s", strategy_id)
            return []
        return [slot]

    # ───────── 이벤트 투입 ─────────
    def feed(self, ev: MarketEvent):
        """시장 이벤트 하나. 이 엔진의 주 진입점이다.

        ★ 순서 ★
          ① view_q/recorder 로 흘린다 — 밖에서 들어온 이벤트든, 아래서 재귀로
             들어오는 봉이든 전부 여기를 한 번은 지난다. 콘솔 뷰가 보는 창구와
             원본 이벤트 기록 창구를 이 메서드 하나로 모아, LiveRunner가 따로
             챙기지 않게 한다. recorder에 해당 타입 구독이 없으면 Recorder가
             조용히 버린다(_accept의 미구독 경고만 남는다).
          ② 브로커가 본다 — 시계·현재가 갱신. 전략이 target_pct 를 부를 때
             이미 기준가가 있어야 한다.
          ③ 전략에 배달한다.

        틱이면 봉 집계도 함께 돌린다:
            틱 도착 → 틱 구독자에게 배달
                   → 봉이 닫혔으면 recorder 에 남기고(Bar는 여기서만 만들어져
                     kis.recorder 같은 원본 저장소가 없다) 같은 경로로 배달
        """
        if self.view_q is not None:
            self.view_q.put(ev)               # ①
        if self.recorder is not None:
            self.recorder.put(ev)             # ① 원본 Tick/Quote도 구독돼 있으면 기록

        self.portfolio.real.on_market(ev)    # ② 브로커 (구현 안 했으면 no-op)
        self.router.dispatch(ev)             # ③ 전략

        if ev.kind == "tick":
            for bar in self.bars.on_tick(ev):
                self.feed(bar)               # 재귀 아님 — bar 는 tick 이 아니다. view_q/recorder도 여기서 같이 탄다

    def feed_timer(self, now: datetime):
        """주기 호출(1초 등). 두 가지를 한다.
            ① 틱이 끊긴 봉 강제 마감 — feed()를 거치지 않으므로 recorder/view_q를
               직접 챙긴다(브로커의 on_market은 원래도 이 경로에서 안 불렀다)
            ② 전략의 on_timer — 종가청산, 미체결 정정 등 시각 기반 로직"""
        for bar in self.bars.flush(now):
            if self.recorder is not None:
                self.recorder.put(bar)
            if self.view_q is not None:
                self.view_q.put(bar)
            self.router.dispatch(bar)
        for slot in self.slots.values():
            slot.trader.feed_timer(now)

    def feed_fill(self, fill: Fill):
        """체결. 주인 전략을 찾아 넘기기만 한다.

        브로커 상태 갱신은 Trader.feed_fill 안에서 한다 — 브로커는 그
        Trader 의 소유물이므로 자기가 갱신하는 게 맞고, 그래야 갱신과
        on_trade 호출 순서가 한 곳에서 보장된다."""
        sid = self.portfolio.owner_of(fill.order_id)
        if sid is None:
            return                                   # 주인 없는 체결 (수동주문 등)
        self.slots[sid].trader.feed_fill(fill)
        if self.recorder is not None:
            self.recorder.put(fill)
        if self.view_q is not None:
            self.view_q.put(fill)

    def feed_order(self, order: Order):
        """주문 상태 변화. 그 주문을 낸 전략에만 알린다."""
        sid = self.portfolio.owner_of(order.id)
        if sid is None:
            return
        self.slots[sid].trader.feed_order(order)

    def feed_execution(self, broker_id: str, status: str,
                       filled_qty: float, price: float, dt: datetime):
        """증권사 체결통보를 브로커에 넣고 결과를 라우팅한다.

        ■ 왜 실브로커가 처리하나
            체결통보는 '증권사 주문번호'로 온다. 그걸 우리 Order 로 되돌리는
            매핑(_by_broker_id)은 주문을 실제로 전송한 실브로커에만 있다.
            PortfolioBroker 는 증권사 주문번호를 모른다.

        ■ 포지션이 두 곳에 기록되는 건 의도된 것이다
            실브로커._positions    실계좌 총 100주   ← 여기서 갱신
            StrategyBroker._pos    A 60 / B 40       ← feed_fill → apply_fill
            reconcile() 이 이 둘을 대조해 수동주문·통보유실을 잡아낸다.

        ■ _Pump.notify_order 와 같은 역할이다
            백테스트  : cerebro → _Pump.notify_order → on_bt_order → feed_fill/order
            실전      : 소켓 → EngineTrader → feed_execution → feed_fill/order
        """
        order, fill = self.portfolio.real.on_execution_report(
            broker_id=broker_id, status=status,
            filled_qty=filled_qty, price=price, dt=dt)

        if order is None:
            return                          # 수동주문이거나 재시작 이전 주문
        if fill is not None:
            self.feed_fill(fill)            # → 주인 전략의 TradeTracker
        self.feed_order(order)

    # ───────── 모니터링 ─────────
    def snapshot(self) -> dict:
        """전략별 현황 + 전체 합계."""
        snap = self.portfolio.snapshot()
        for sid, s in snap.items():
            t = self.slots[sid].trader.tracker
            s["trades"] = len(t.closed)
            s["realized_pnl"] = t.realized_pnl
        return snap

    def reconcile(self):
        """가상 합계와 실계좌 대조. 폴링 스레드가 주기적으로 부르면 좋다."""
        return self.portfolio.reconcile()

    # ───────── 사후 분석용 내보내기 ─────────
    def fill_records(self) -> list[dict]:
        """전 전략의 체결을 시간순으로 모은다. strategy_id 가 붙는다.

        컬럼: datetime, symbol, side, size, price, fillvalue,
              commission, order_id, strategy_id

        ■ strategy_id 를 여기서 붙이는 이유
            TradeTracker 는 자기가 어느 전략 것인지 모른다(알 필요도 없다).
            슬롯 id 를 아는 건 Engine 뿐이므로 여기서 태깅한다."""
        rows = []
        for sid, slot in self.slots.items():
            rows.extend(slot.trader.tracker.fill_records(sid))
        rows.sort(key=lambda r: r["datetime"])     # 전략 간 시간순 정렬
        return rows

    def trade_records(self) -> list[dict]:
        """전 전략의 완결된 라운드트립. 거래 단위 분석용."""
        rows = []
        for sid, slot in self.slots.items():
            rows.extend(slot.trader.tracker.trade_records(sid))
        rows.sort(key=lambda r: r["exit_dt"] or r["entry_dt"])
        return rows

    def fills_df(self):
        """체결 DataFrame. pandas 가 없으면 ImportError."""
        import pandas as pd
        return pd.DataFrame(self.fill_records())

    def trades_df(self):
        """거래 DataFrame."""
        import pandas as pd
        return pd.DataFrame(self.trade_records())

    def dump(self, prefix: str = "session"):
        """CSV 로 떨군다. 세션 종료 시 부르면 사후 분석 자료가 남는다.

        ★ 메모리에만 두면 프로세스가 죽을 때 통째로 잃는다 ★
          Engine.stop() 이나 종료 시그널 핸들러에서 부르는 것을 권장한다.
        """
        import pandas as pd
        out = []
        for name, rows in (("fills", self.fill_records()),
                           ("trades", self.trade_records())):
            if not rows:
                continue
            path = f"{prefix}_{name}.csv"
            pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")
            out.append(path)
            log.info("%s %d행 저장 → %s", name, len(rows), path)
        return out