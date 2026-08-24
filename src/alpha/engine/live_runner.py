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
    신규:  MarketEvent(KiSEngine.market_event_queue 가 이미 파싱+정규화) → engine.feed(ev)

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

from kis.kis_websocket import SENTINEL

from alpha.engine.engine import Engine
from alpha.trader.killswitch import KillSwitch
from alpha.events.events import MarketEvent, Notice
from alpha.trader.trading import Fill, Order

log = logging.getLogger("alpha.live_runner")

TIMER_INTERVAL = 1.0        # feed_timer 호출 주기(초)
BALANCE_INTERVAL = 5.0      # 잔고 폴링 주기(초)


class LiveRunner:
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

    def __init__(self, engine, trading_q: Queue, view_q: Queue, broker=None,
                 test_mode: bool = True,
                 dry_run: bool = True,
                 business_date: Optional[date] = None,
                 kill_file: str = "./STOP_TRADING"):
        self.trading_q = trading_q
        self.view_q = view_q
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.test_mode = test_mode
        self.engine = engine               # 이름은 기존과 맞추되 의미는 '팩토리'
        self.on_date = business_date        # 재생 시 필요. None 이면 오늘

        # ── 브로커 ──
        # SimBroker(모의) 또는 KISBroker(실전). Broker ABC 의 .now 를
        # _periodic()/_handle_notice() 의 시각 기준으로 쓴다.
        # (봉 기반 백테스트는 backtrader(_Pump)가 따로 담당한다)
        self.broker = broker
        self.dry_run = dry_run
        self.kill_file = kill_file
        self.kill: Optional[KillSwitch] = None
        self._last_timer = 0.0
        self._last_balance = 0.0

    def start(self):
        if self.engine is None:
            log.warning("전략 팩토리가 없습니다. trading() 종료.")
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self.roop, daemon=True,
                                        name="recorder")
        self._thread.start()
        log.info("LiveRunner 스레드 기동")
    # ═══════════════════════════════════════════════════════
    # KisTrader.trading() 자리
    # ═══════════════════════════════════════════════════════
    def roop(self):

        # 체결 알림은 별도 배선이 필요 없다.
        # 체결통보(H0STCNI0)가 trading_q 로 들어와 _handle_notice 가 처리한다.
        self.engine.start(history=self._load_history())

        # 킬스위치 — armed 는 '파일이 없을 때 돌아갈 상태'다.
        # 기동 시 dry_run=False 였다면 파일을 지웠을 때 거래가 재개된다.
        self.kill = KillSwitch(self.engine, path=self.kill_file,
                               armed=not self.dry_run)

        log.info("EngineTrader 시작 — 전략 %d개, 실주문 %s",
                 len(self.engine.slots), self.engine.trading_status())

        consumed = 0
        while True:
            try:
                raw = self.trading_q.get(timeout=0.2)
                # Tick/Quote와 거기서 파생되는 Bar는 engine.feed() 가 직접
                # view_q 로 흘린다 — 여기서 또 넣으면 화면에 같은 이벤트가
                # 두 번 뜬다. Notice는 MarketEvent이긴 하지만 engine.feed()
                # 를 거치지 않고 _handle_notice→feed_execution()으로 가므로
                # (체결통보는 심볼 방송이 아니라 주문 소유자에게만 가야 해서
                # 라우팅 방식 자체가 다르다) SENTINEL과 함께 여기서 예외로 넣는다.
                if not isinstance(raw, MarketEvent) or self._is_notice(raw):
                    self.view_q.put(raw)
                if raw:
                    consumed += 1
                    if consumed == 1 or consumed % 20 == 0:
                        log.info("trading_q 누적 소비 %d건 (최근 타입=%s)",
                                consumed, type(raw).__name__)
                    self._consume(raw)
            except Empty:
                pass                        # 틱이 없어도 아래 주기 작업은 돈다
            except Exception:
                log.exception("틱 처리 실패 — 루프는 계속")

            # 정지 요청이 와도 trading_q가 빌 때까지는 계속 돈다 — 종료
            # 순간 이미 큐에 들어와 있던 실시간 이벤트/체결통보를 전략에
            # 못 넘기고 버리는 일이 없도록 한다.
            if self._stop.is_set() and self.trading_q.empty():
                break

            self._periodic()

        self.engine.stop()                  # 상태 저장
        log.info("EngineTrader 종료 — 총 %d건 소비", consumed)

    # ═══════════════════════════════════════════════════════
    # 종료 — trading_q를 마저 비운 뒤에 스레드를 세운다
    # ═══════════════════════════════════════════════════════
    def stop(self, timeout: float = 5.0):
        """정상 종료 요청. roop()가 trading_q를 다 비울 때까지 스스로 돌다가
        멈추므로, 이미 들어와 있던 이벤트는 유실 없이 engine.feed까지 간다.

        이미 멈춘 뒤 다시 불러도 안전하다(멱등)."""
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            log.warning("LiveRunner 정지 시간 초과(%.1fs) — trading_q 잔여 %d건은 "
                        "다음 종료 시도까지 유실될 수 있음", timeout, self.trading_q.qsize())
        else:
            self._thread = None
            log.info("LiveRunner 정상 종료 완료")

    # ═══════════════════════════════════════════════════════
    # 큐에서 꺼낸 원본 하나를 처리
    # ═══════════════════════════════════════════════════════
    def _consume(self, ev):
        """trading_q 에는 이제 KiSEngine.market_event_queue 가 이미 파싱하고
        MarketEvent(Tick/Quote/Notice)로 정규화해 넣는다. 여기서는 더 이상
        원본을 파싱하지 않고 그대로 Engine 에 먹인다.

        ■ 체결통보(Notice)는 예외다
            같은 MarketEvent 지만 engine.feed() 의 심볼 기준 브로드캐스트가
            아니라 '이 주문을 낸 전략에게만' 가야 하므로, engine.feed_execution
            을 통해 주문 소유자로 라우팅한다(KiSEngine.실전/SimBroker.모의
            둘 다 events.Notice 로 정규화해서 넣으므로 필드 이름을 몰라도 된다).

        ■ SENTINEL(kis_websocket)도 여기로 들어온다
            KiSEngine이 웹소켓 종료 시 trading_q(=market_event_queue) 끝에
            흘려보내는 종료 표식이다 — 처리할 이벤트가 아니므로 조용히 지나간다."""
        if ev is SENTINEL:
            log.debug("trading_q 종료 표식(SENTINEL) 수신")
            return
        if self._is_notice(ev):
            log.debug("체결통보 수신 → _handle_notice")
            self._handle_notice(ev)
            return
        if isinstance(ev, MarketEvent):
            log.debug("MarketEvent 수신 kind=%s symbol=%s → engine.feed", ev.kind, ev.symbol)
            # 브로커의 시계·현재가 갱신은 Engine.feed 가 한다.
            # (여기서 따로 챙기면 _Pump 쪽과 어긋난다)
            self.engine.feed(ev)
        else:
            log.warning("알 수 없는 trading_q 항목 무시: %r", ev)

    def _is_notice(self, obj) -> bool:
        """체결통보인가. KiSEngine(실전)과 SimBroker(모의) 가 각각
        events.from_notice() / 직접 생성으로 이미 events.Notice 하나로
        정규화해서 넣으므로, 더 이상 필드 이름(order_no/ODER_NO 등)으로
        판별할 필요가 없다."""
        return isinstance(obj, Notice)

    def _handle_notice(self, n: Notice):
        """체결통보를 Engine 에 넘긴다.

        이 메서드의 책임은 'Notice → 표준 인자' 변환뿐이다.
        브로커 갱신과 전략 라우팅은 Engine.feed_execution 이 한다.
        (호출자가 브로커를 직접 만지면 백테스트 경로와 어긋난다)

        recorder 는 여기서 직접 챙긴다 — engine.feed() 를 거치지 않아서
        Engine.feed() 의 자동 recorder/view_q 전달을 못 받기 때문이다
        (view_q 는 roop() 이 이미 넣었다)."""
        if self.engine.recorder is not None:
            self.engine.recorder.put(n)

        log.info("체결통보 처리: 주문 %s %s %s주 @%s", n.order_no,
                "거부" if n.rejected else "체결", n.filled_qty, n.price)
        self.engine.feed_execution(
            broker_id=n.order_no,
            status=("reject" if n.rejected else "fill"),
            filled_qty=n.filled_qty,
            price=n.price,
            dt=n.dt,
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