"""
═══════════════════════════════════════════════════════════════════
 universe.py — 유니버스(종목 구성) 재계산 스케줄러
═══════════════════════════════════════════════════════════════════

■ 무엇이 바뀌었나 (이전 설계를 폐기한 이유)
    이전 버전은 "종목 하나당 전략 인스턴스 하나"를 프레임워크가 강제하고
    (strategy_factory 콜백), 배정자본도 프레임워크가 고정값으로 나눠줬다
    (allocation_per_symbol). 여러 종목을 전략 하나가 관리하는 구조는
    아예 표현할 수 없었고, 유니버스가 바뀔 때 자금을 어떻게 재분배할지도
    프레임워크 마음대로였다 — 프로그램 사용을 경직되게 만드는 설계였다.

    지금은 "종목 리스트가 주어지면 무엇을 어떻게 등록할지"를 전부
    build_trader(trader, symbols) 사용자 함수에 맡긴다(AlphaTrader.
    run_live/run_sim 의 build_trader= 인자). 이 파일은 다음 세 가지만
    한다.
        ① 언제 다시 계산할지 정하기(UniverseSchedule)
        ② 새로 들어온 종목만 build_trader(trader, 새_종목들) 로 다시 부르기
        ③ 완전히 빠진 종목의 실시간 시세 구독을 끊기
    전략을 몇 개 쓰든, 종목을 어떻게 나눠 담든(1종목:1전략이든 여러
    종목을 전략 하나가 다 보든) 이 세 가지는 그대로 안전하게 동작한다
    — Engine 슬롯 구조를 이 클래스가 몰라도 되기 때문이다.

■ "빠진 종목"을 얼마나 뜯어내나 — 딱 웹소켓 구독까지만
    포지션이 남아있는 종목의 시세를 끊으면, 그 포지션을 관리하던 전략이
    청산 판단(스탑/목표가 등)을 할 근거를 잃는다. 그래서 구독을 끊기
    전에 반드시 "이 종목에 지금 포지션을 든 전략이 하나라도 있는가"를
    확인한다(PortfolioBroker.has_position — 등록된 전 전략의 가상계좌를
    훑으므로 어느 전략이 이 종목을 보는지 몰라도 판단할 수 있다).
    포지션이 남아있으면 이번엔 그냥 두고, 다음 재계산 때 다시 확인한다.

    Engine.add()/remove() 같은 슬롯 단위 조작은 여기서 하지 않는다 —
    그 슬롯을 누가 어떻게 쓰고 있는지 이 클래스는 모르고, 알 필요도
    없다. 완전한 정리(전략 인스턴스 자체를 뗌)가 필요하면 사용자가
    engine.remove(strategy_id) 를 직접 쓸 수 있다(범용 메서드로 남겨둠).

■ 사용 예
        from alpha.engine.universe import UniverseSchedule

        def get_universe() -> list[str]:
            ...   # REST 랭킹 조회 등, 완전히 사용자 자유
            return ["005930", "000660", ...]

        def build_trader(trader, symbols: list[str]) -> None:
            # symbols 에 대해서만 등록한다 — 구조는 전부 사용자 자유.
            for sym in symbols:
                trader.add_strategy(f"미시구조_{sym}", MicrostructureWatcher(symbol=sym),
                                    allocation=0, quotes=[sym], ticks=[sym])

        runner = trader.run_sim(market_event_queue, ws=kis.ws,
                                build_trader=build_trader, universe=get_universe,
                                universe_schedule=UniverseSchedule(interval=7200))
"""

from __future__ import annotations

import logging
import queue
from datetime import date as date_type, datetime, timedelta
from typing import Callable, Iterable, Optional, Union

log = logging.getLogger("universe")

UniverseSpec = Union[Iterable[str], Callable[[], Iterable[str]]]


def resolve_universe(universe: UniverseSpec) -> list[str]:
    """정적 리스트든 get_universe() 콜백이든, 지금 시점의 종목 리스트로."""
    symbols = universe() if callable(universe) else universe
    return list(symbols)


class UniverseSchedule:
    """유니버스를 언제 다시 계산할지. 간격(초) 또는 특정 시각들 중
    하나 이상을 준다 — 둘 다 줘도 된다(둘 중 하나라도 되면 재계산).

        UniverseSchedule(interval=7200)            2시간마다
        UniverseSchedule(at=["12:00", "14:00"])    매일 그 시각에 한 번씩
        UniverseSchedule(interval=3600, at=["09:05"])  1시간마다 + 장 시작 직후 한 번 더

    ■ now 는 벽시계가 아니라 주입된 시각(브로커 시계)이다
        Engine.feed_timer(now) 가 그대로 넘겨준다 — 실전/모의 둘 다
        "시장이 실제로 흐른 시각" 기준으로 재계산 시점을 잰다."""

    def __init__(self, interval: Optional[float] = None,
                at: Optional[Iterable[str]] = None):
        if interval is None and not at:
            raise ValueError("interval 또는 at 중 하나는 있어야 한다")
        self.interval = timedelta(seconds=interval) if interval else None
        self.at = set(at or ())               # {"12:00", "14:00"}
        self._last: Optional[datetime] = None
        self._fired_today: set[str] = set()
        self._today: Optional[date_type] = None

    def due(self, now: datetime) -> bool:
        if self._today != now.date():
            self._today = now.date()
            self._fired_today.clear()

        if self.interval is not None and (self._last is None or now - self._last >= self.interval):
            return True

        hhmm = now.strftime("%H:%M")
        if hhmm in self.at and hhmm not in self._fired_today:
            return True

        return False

    def mark(self, now: datetime) -> None:
        self._last = now
        hhmm = now.strftime("%H:%M")
        if hhmm in self.at:
            self._fired_today.add(hhmm)


class UniverseManager:
    """build_trader(trader, symbols) 를 언제, 어떤 종목들로 다시 부를지
    결정한다. 전략 구조는 전혀 몰라도 된다(모듈 설명 참고).

    ■ 콘솔(다른 스레드)에서 종목을 넣고 빼려면
        request_add(sym)/request_remove(sym) 을 쓸 것 — 큐에 담아두기만
        하고 바로 반환하므로(스레드 안전) 콘솔 워커 스레드에서 불러도
        된다. 실제 반영(build_trader 호출/ws 구독)은 다음 maybe_rebalance()
        때(대략 1초 이내) Engine.feed_timer() 를 부르는 스레드(LiveRunner)
        위에서 일어난다 — Engine.add() 가 그 스레드 하나가 소유한다는
        전제로 락이 없는 것과 같은 이유다."""

    def __init__(self, trader, ws, get_universe: Callable[[], Iterable[str]],
                build_trader: Callable[[object, list[str]], None],
                schedule: UniverseSchedule,
                known: Optional[Iterable[str]] = None):
        self.trader = trader
        self.ws = ws
        self.get_universe = get_universe
        self.build_trader = build_trader
        self.schedule = schedule
        self.known: set[str] = set(known or ())

        self._manual_add: "queue.Queue[str]" = queue.Queue()
        self._manual_remove: "queue.Queue[str]" = queue.Queue()

        # 포지션이 남아 이번엔 못 뺀 종목들. 다음 정기 재계산까지 기다리면
        # (interval이 길거나 아예 자동 재계산을 안 쓰면) 청산이 끝나고도
        # 한참 뒤에야 구독이 풀린다 — 그래서 이 목록만은 매 틱(1초마다)
        # 다시 확인한다. 포지션 flat 여부 확인은 로컬 조회라 비용이 거의
        # 없다.
        self._pending_release: set[str] = set()

    # ───────── 다른 스레드에서 부르는 통로(콘솔 s/sc 등) ─────────
    def request_add(self, sym: str) -> None:
        self._manual_add.put(sym)

    def request_remove(self, sym: str) -> None:
        self._manual_remove.put(sym)

    # ───────── 트리거 ─────────
    def maybe_rebalance(self, now: datetime) -> None:
        """Engine.feed_timer 가 매번(보통 1초마다) 불러도 되게 자기 자신의
        일정을 여기서 관리한다 — 호출자는 언제 부를지 신경 쓸 필요 없다.

        수동 요청과 '포지션 때문에 미뤄둔 제거'는 일정과 무관하게
        매번(1초 이내) 처리한다."""
        if self.schedule.due(now):
            self.schedule.mark(now)
            try:
                self._rebalance()
            except Exception:
                log.exception("유니버스 재계산 실패 — 이번 주기는 건너뛰고 다음에 다시 시도")
        self._drain_manual()
        if self._pending_release:
            self._release(set(self._pending_release))

    def _rebalance(self) -> None:
        desired = set(resolve_universe(self.get_universe))
        to_add = desired - self.known
        to_check = self.known - desired
        log.info("유니버스 재계산 — 보유 %d종목, 목표 %d종목(신규 %d, 검토 %d)",
                 len(self.known), len(desired), len(to_add), len(to_check))
        self._release(to_check)
        self._acquire(to_add)

    def _drain_manual(self) -> None:
        adds = self._drain(self._manual_add)
        if adds:
            self._acquire(set(adds) - self.known)
        removes = self._drain(self._manual_remove)
        if removes:
            self._release(set(removes) & self.known)

    @staticmethod
    def _drain(q: "queue.Queue[str]") -> list[str]:
        out = []
        while True:
            try:
                out.append(q.get_nowait())
            except queue.Empty:
                break
        return out

    # ───────── 실제 반영 ─────────
    def _acquire(self, symbols) -> None:
        """새 종목들의 시세를 구독하고, build_trader 를 그 종목들만으로
        다시 불러 등록시킨다. 기존에 이미 등록된 종목은 절대 다시 부르지
        않는다 — build_trader 가 같은 종목에 뭘 또 등록해서 충돌 내는
        일이 없다."""
        symbols = sorted(symbols)
        if not symbols:
            return
        for sym in symbols:
            try:
                self.ws.subscribe_symbol(sym)
            except Exception:
                log.exception("[유니버스] %s 구독 실패 — build_trader 는 그래도 진행한다"
                             "(다음 재연결 때 시세가 들어오면 살아난다)", sym)
        try:
            self.build_trader(self.trader, symbols)
        except Exception:
            log.exception("[유니버스] build_trader(%s) 실패 — 이 종목들은 다음 재계산 때 재시도됨",
                          symbols)
            return
        self.known.update(symbols)
        log.info("[유니버스] 추가: %s", symbols)

    def _release(self, symbols) -> None:
        """포지션이 없는 종목만 시세 구독을 해지한다. Engine 쪽 전략/
        슬롯은 건드리지 않는다(모듈 설명 참고) — 데이터가 더 안 들어올
        뿐이다. 포지션이 남아 못 뺀 종목은 _pending_release 에 넣어두고
        maybe_rebalance() 가 매 틱 다시 시도한다(정기 재계산 주기까지
        안 기다린다)."""
        engine = getattr(self.trader, "_engine", None)
        for sym in list(symbols):
            if engine is not None and engine.portfolio.has_position(sym):
                if sym not in self._pending_release:
                    log.info("[유니버스] %s 포지션 보유 중 — 구독 유지, 청산될 때까지 매 틱 재확인", sym)
                self._pending_release.add(sym)
                continue
            try:
                self.ws.unsubscribe_symbol(sym)
            except Exception:
                log.exception("[유니버스] %s 구독 해지 실패", sym)
            self.known.discard(sym)
            self._pending_release.discard(sym)
            log.info("[유니버스] 제거(구독 해지): %s", sym)
