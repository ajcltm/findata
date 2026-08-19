"""
═══════════════════════════════════════════════════════════════════
 kis_bridge.py — 기존 findata 큐 구조와 새 Engine 을 잇는다
═══════════════════════════════════════════════════════════════════

■ 기존 구조는 그대로 둔다
    KiSEngine
      ├ consumer_queue = {recording_q, trading_q, show_q}
      ├ KisFeed(웹소켓)  ─ 원본 틱을 큐에 put          ← 손대지 않음
      ├ KisRecorder      ─ recording_q 소비             ← 손대지 않음
      ├ KisTrader        ─ trading_q 소비 → strategy.next()
      └ Application      ─ show_q 소비                  ← 손대지 않음

    이 파일의 EngineTrader 가 KisTrader 자리에 그대로 들어간다.
    생성자 시그니처가 같아서 kis_engine.py 는 클래스 이름 한 줄만 바꾸면 된다.

        - trader = kis_trader.KisTrader(strategy=..., trading_q=..., ...)
        + trader = kis_bridge.EngineTrader(strategy=..., trading_q=..., ...)

■ 바뀌는 것은 소비 루프 안쪽뿐
    기존:  raw → parser.parse → deque(200) → strategy.next()
    신규:  raw → parser.parse → 이벤트 정규화 → engine.feed(ev)

    deque 윈도우가 사라지는 이유: 지표가 증분 계산이라 과거 틱을 들고 있을
    필요가 없다. 전략이 굳이 원시 윈도우를 원하면 자기 안에서 deque 를 두면 된다.

■ 타이머 스레드가 필요 없다
    trading_q.get(timeout=0.2) 이 이미 주기적으로 깨어난다.
    깨어난 김에 feed_timer 를 호출하면 봉 강제 마감과 on_timer 가 해결된다.
    (스레드를 하나 덜 만드는 게 디버깅에 유리하다)
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime
from queue import Empty, Queue
from typing import Callable, Optional

from engine import Engine
from killswitch import KillSwitch
from events import MarketEvent, from_execution, from_orderbook
from trading import Fill, Order

log = logging.getLogger("kis")

TIMER_INTERVAL = 1.0        # feed_timer 호출 주기(초)
BALANCE_INTERVAL = 5.0      # 잔고 폴링 주기(초)


class EngineTrader:
    """KisTrader 드롭인 교체. 큐를 소비해 Engine 에 흘린다.

    ■ 생성자 인자 (KisTrader 와 동일 + broker 하나 추가)
        strategy   : Engine 을 만드는 팩토리. build_engine(broker, dry_run) -> Engine
                     (기존에는 전략 클래스였지만, 이제 멀티 전략이라 팩토리를 받는다)
        parser     : 기존 KISParser. 그대로 쓴다
        trading_q  : 기존 trading_q. 그대로 쓴다
        stop_event : 기존 stop 이벤트. 그대로 쓴다
        test_mode  : True 면 dry-run(주문 로그만), False 면 주문 허용
        broker     : SimBroker(모의) 또는 KISBroker(실전)

    ■ 모의투자 → 실전 전환은 broker 교체 한 줄
            SimBroker(cash=10_000_000)      실시간 소켓 + 가짜 주문
            KISBroker(api, account="...")   실시간 소켓 + 실주문
        전략·지표·손익계산·기록은 전부 그대로다.
    """

    def __init__(self, strategy: Callable[..., Engine], parser, trading_q: Queue,
                 stop_event: threading.Event, test_mode: bool = True,
                 broker=None, dry_run: bool = True,
                 business_date: Optional[date] = None,
                 kill_file: str = "./STOP_TRADING"):
        self.trading_q = trading_q
        self.parser = parser
        self._stop = stop_event
        self.test_mode = test_mode
        self.build = strategy               # 이름은 기존과 맞추되 의미는 '팩토리'
        self.on_date = business_date        # 재생 시 필요. None 이면 오늘

        # ── 브로커 ──
        # SimBroker(모의) 또는 KISBroker(실전). 봉 기반 백테스트는
        # backtrader(_Pump)가 따로 담당한다.
        if broker is None:
            raise ValueError("SimBroker 또는 KISBroker 인스턴스를 넘겨야 한다")
        self.broker = broker

        self.engine: Optional[Engine] = None
        self.dry_run = dry_run
        self.kill_file = kill_file
        self.kill: Optional[KillSwitch] = None
        self._last_timer = 0.0
        self._last_balance = 0.0

    # ═══════════════════════════════════════════════════════
    # KisTrader.trading() 자리
    # ═══════════════════════════════════════════════════════
    def trading(self):
        if self.build is None:
            log.warning("전략 팩토리가 없습니다. trading() 종료.")
            return

        # 체결 알림은 별도 배선이 필요 없다.
        # 체결통보(H0STCNI0)가 trading_q 로 들어와 _handle_notice 가 처리한다.
        self.engine = self.build(self.broker, self.dry_run)
        self.engine.start(history=self._load_history())

        # 킬스위치 — armed 는 '파일이 없을 때 돌아갈 상태'다.
        # 기동 시 dry_run=False 였다면 파일을 지웠을 때 거래가 재개된다.
        self.kill = KillSwitch(self.engine, path=self.kill_file,
                               armed=not self.dry_run)

        log.info("EngineTrader 시작 — 전략 %d개, 실주문 %s",
                 len(self.engine.slots), self.engine.trading_status())

        while not self._stop.is_set():
            try:
                raw = self.trading_q.get(timeout=0.2)
                if raw:
                    self._consume(raw)
            except Empty:
                pass                        # 틱이 없어도 아래 주기 작업은 돈다
            except Exception:
                log.exception("틱 처리 실패 — 루프는 계속")

            self._periodic()

        self.engine.stop()                  # 상태 저장
        log.info("EngineTrader 종료")

    # ═══════════════════════════════════════════════════════
    # 큐에서 꺼낸 원본 하나를 처리
    # ═══════════════════════════════════════════════════════
    def _consume(self, raw):
        """parser.parse 는 리스트를 돌려준다(기존 코드가 extend 하고 있었다).
        단일 객체를 돌려주는 경우도 있어 양쪽 다 받는다."""
        # SimBroker 가 넣은 체결통보는 이미 객체다 — 파서를 태우면 안 된다.
        # (KISParser 는 소켓 전문 문자열을 기대한다)
        if self._is_notice(raw):
            self._handle_notice(raw)
            return
        parsed = self.parser.parse(raw)
        if parsed is None:
            return
        if not isinstance(parsed, (list, tuple)):
            parsed = [parsed]

        for obj in parsed:
            ev = self._to_event(obj)
            if ev is not None:
                # 브로커의 시계·현재가 갱신은 Engine.feed 가 한다.
                # (여기서 따로 챙기면 _Pump 쪽과 어긋난다)
                self.engine.feed(ev)
                continue
            if self._is_notice(obj):
                self._handle_notice(obj)

    def _to_event(self, obj) -> Optional[MarketEvent]:
        """원본 dataclass → 정규화 이벤트.

        ■ 타입 이름 대신 필드로 판별하는 이유
            parser 가 어떤 클래스를 돌려주는지 여기서 단정하지 않는다.
            필드 조합으로 구별하면 dataclass 를 리네임해도 안 깨진다.

            Execution : current_price 와 tick_volume 이 있다
            OrderBook : ask_price_3 이 있다 (Execution 은 1호가만 갖는다)
        """
        if hasattr(obj, "ask_price_3"):
            return from_orderbook(obj, on=self.on_date)
        if hasattr(obj, "current_price") and hasattr(obj, "tick_volume"):
            return from_execution(obj, on=self.on_date)
        return None

    def _is_notice(self, obj) -> bool:
        """체결통보인가. 주문번호와 체결수량을 갖는지로 판별한다.

        KIS 의 Notice(H0STCNI0)와 SimBroker 의 SimNotice 가 둘 다 통과한다 —
        필드로 판별하므로 클래스를 알 필요가 없다."""
        return hasattr(obj, "order_no") and hasattr(obj, "executed_qty")

    def _handle_notice(self, n):
        """체결통보 전문을 파싱해 Engine 에 넘긴다.

        이 메서드의 책임은 'KIS 전문 → 표준 인자' 변환뿐이다.
        브로커 갱신과 전략 라우팅은 Engine.feed_execution 이 한다.
        (호출자가 브로커를 직접 만지면 백테스트 경로와 어긋난다)

        ★ 모의와 실전이 같은 경로다 ★
          실계좌: 거래소 → 소켓 → trading_q → Notice
          모의:   SimBroker._match → trading_q → SimNotice
          여기서는 둘을 구별하지 않는다. 필드 이름만 맞으면 된다."""
        self.engine.feed_execution(
            broker_id=n.order_no,
            status=("reject" if getattr(n, "is_rejected", "N") == "Y" else "fill"),
            filled_qty=float(getattr(n, "executed_qty", 0) or 0),
            price=float(getattr(n, "executed_price", 0) or 0),
            dt=self.broker.now,
        )

    # ═══════════════════════════════════════════════════════
    # 주기 작업 — 별도 스레드 없이 루프 안에서 처리
    # ═══════════════════════════════════════════════════════
    def _periodic(self):
        now = time.monotonic()

        # ⓪ 킬스위치 — 제일 먼저 본다. 자체적으로 1초 간격을 지킨다.
        #    touch STOP_TRADING 하면 1초 안에 실주문이 차단된다.
        if self.kill is not None:
            self.kill.check()

        # ① 봉 강제 마감 + 전략의 on_timer
        #    거래가 뜸한 종목은 다음 틱이 안 와서 봉이 안 닫힌다.
        if now - self._last_timer >= TIMER_INTERVAL:
            self._last_timer = now
            try:
                self.engine.feed_timer(self.broker.now)
            except Exception:
                log.exception("feed_timer 실패")

        # ② 잔고 동기화 + 정합성 대조
        #    SimBroker 는 자기가 계산하므로 동기화할 대상이 없다.
        if now - self._last_balance >= BALANCE_INTERVAL:
            self._last_balance = now
            try:
                if hasattr(self.broker, "sync_balance"):
                    self.broker.sync_balance()
                self.engine.reconcile()
            except Exception:
                log.exception("잔고 동기화 실패")

    def _load_history(self) -> dict:
        """워밍업용 과거 봉. REST 분봉 조회를 연결하면 된다.
        비워두면 실시간 봉이 warmup 개수만큼 쌓일 때까지 주문이 안 나간다."""
        return {}

    # ═══════════════════════════════════════════════════════
    # 운영 편의
    # ═══════════════════════════════════════════════════════
    def enable_trading(self, strategy_id: str | None = None):
        """dry-run 해제. 며칠 관찰 후 손으로 켠다."""
        if self.engine:
            self.engine.enable_trading(strategy_id)

    def snapshot(self) -> dict:
        return self.engine.snapshot() if self.engine else {}