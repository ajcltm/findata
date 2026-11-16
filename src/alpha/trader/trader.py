"""
═══════════════════════════════════════════════════════════════════
 trader.py — Trader. 조립하고, 이벤트를 전략에 흘린다
═══════════════════════════════════════════════════════════════════

옛 위치: alpha/trader/trading.py 의 "5. Trader" 절.

    Trader ── Strategy              전략 (사용자가 작성)   alpha/strategy/base.py
       └───── Broker                BacktraderBroker | KISBroker   alpha/broker/base.py
                 └── Order / Position / Trade / Fill       alpha/core/

■ 왜 이렇게 나눴나
    Strategy 는 self.broker 만 본다. 그 broker 가 시뮬레이션인지 실계좌인지
    모른다. 그래서 같은 전략 코드가 백테스트와 실전에서 그대로 돈다.
    바꿔 끼우는 건 런타임 인자 하나:   Trader(broker, strategy)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from alpha.broker.base import Broker, GuardBroker
from alpha.core.orders import Fill, Order
from alpha.core.trades import TradeTracker
from alpha.events.events import MarketEvent
from alpha.recording.records import AccountSnapshot, IndicatorSnapshot
from alpha.strategy.base import Strategy

log = logging.getLogger(__name__)


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
        for label, line, value in self.strategy.indicator_snapshot(ev):
            snap = IndicatorSnapshot(
                dt=ev.dt, strategy_id=self.strategy_id, symbol=ev.symbol,
                label=label, line=line, value=value, trigger=ev.kind,
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
