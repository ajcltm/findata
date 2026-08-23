import asyncio
import threading
import queue

from kis import kis_websocket
from kis import kis_parser

from alpha.events import events
from alpha.recording.recorder import Recorder


import logging

class KiSEngine:
    def __init__(self, price_codes=None, orderbook_codes=None, simul_mode=False):

        self.logger = logging.getLogger("kis")
        self.simul_mode = simul_mode

        self.price_codes = price_codes
        self.orderbook_codes = orderbook_codes
        self.logger.info("KiSEngine 초기화: price_codes=%s orderbook_codes=%s simul_mode=%s",
                         self.price_codes, self.orderbook_codes, self.simul_mode)

        # KisFeed가 채우는 원본 Tick 큐. 파싱 파이프라인과 이벤트 파이프라인을
        # 서로 다른 큐로 나눠 받아야, 한쪽 소비자가 밀려도 다른 쪽은 영향받지 않는다.
        self.consumer_queue = {'parsing_q': queue.Queue(), 'market_event_q': queue.Queue()}

        self.ws = kis_websocket.KisFeed(price_codes=self.price_codes, orderbook_codes=self.orderbook_codes, consumer_queues=self.consumer_queue, simul_mode=self.simul_mode)
        self.parser = kis_parser.KISParser()

        # 이 엔진이 외부에 제공하는 결과 큐.
        #   parsing_queue      : kis_parser로 파싱된 객체(ParsedTick)
        #   market_event_queue : 그 파싱 객체를 events.MarketEvent로 변환한 결과
        self.parsing_queue = queue.Queue()
        self.market_event_queue = queue.Queue()

        # ── 레코딩 ──────────────────────────────────────────────────
        # _run_parsing 이 파싱한 원본(Execution/OrderBook/Notice)과
        # _run_market_event 가 만든 MarketEvent(Tick/Quote)를 이 레코더로
        # 보낸다. 기록은 매매의 크리티컬 패스가 아니므로 Recorder 는 큐가
        # 차면 조용히 버린다.
        # 무엇을 어디에 저장할지는 여기서 정하지 않는다 — run() 을 부르기
        # 전에 메인 파일이 self.recorder.subscribe(...) 로 등록한다.
        # 구독이 없는 타입은 Recorder 가 경고만 남기고 버린다.
        self.recorder = Recorder()

        self._stop = threading.Event()
        self._parsing_thread = None
        self._market_event_thread = None

    # ── consumer_queue['parsing_q'] → parsing_queue ─────────────────
    def _run_parsing(self):
        self.logger.info("parsing 워커 시작")
        raw_q = self.consumer_queue['parsing_q']
        count = 0
        while True:
            tick = raw_q.get()
            if tick is kis_websocket.SENTINEL:
                self.parsing_queue.put(kis_websocket.SENTINEL)
                self.logger.info("parsing 워커 종료 (누적 %d건)", count)
                break
            parsed = self.parser.parse(tick)
            if parsed is None:
                continue
            count += 1
            if count == 1 or count % 20 == 0:
                self.logger.info("parsing_queue 누적 %d건 (최근 tr_id=%s)",
                                 count, parsed.tr_id)
            for record in parsed.data:
                self.recorder.put(record)
            self.parsing_queue.put(parsed)

    # ── consumer_queue['market_event_q'] → market_event_queue ───────
    def _run_market_event(self):
        self.logger.info("market_event 워커 시작")
        raw_q = self.consumer_queue['market_event_q']
        count = 0
        while True:
            tick = raw_q.get()
            if tick is kis_websocket.SENTINEL:
                self.market_event_queue.put(kis_websocket.SENTINEL)
                self.logger.info("market_event 워커 종료 (누적 %d건)", count)
                break
            parsed = self.parser.parse(tick)
            if parsed is None:
                continue
            for record in parsed.data:
                ev = self._to_market_event(parsed.tr_id, record)
                count += 1
                if count == 1 or count % 20 == 0:
                    self.logger.info("market_event_queue 누적 %d건 (최근 tr_id=%s, kind=%s, symbol=%s)",
                                     count, parsed.tr_id, getattr(ev, "kind", type(ev).__name__),
                                     getattr(ev, "symbol", getattr(ev, "stock_code", "?")))
                self.recorder.put(ev)
                self.market_event_queue.put(ev)

    def _to_market_event(self, tr_id, record):
        if tr_id == self.ws.tr_price:
            return events.from_execution(record)
        if tr_id == self.ws.tr_orderbook:
            return events.from_orderbook(record)
        # 체결통보(H0STCNI0) 등 아직 MarketEvent로 정규화되지 않은 tr_id는
        # 누락시키지 않고 파싱된 원본 객체를 그대로 흘려보낸다.
        return record

    def start_parsing(self):
        self._parsing_thread = threading.Thread(target=self._run_parsing, daemon=True, name="parsing")
        self._parsing_thread.start()
        self.logger.info("parsing thread on.")

    def start_market_event(self):
        self._market_event_thread = threading.Thread(target=self._run_market_event, daemon=True, name="market_event")
        self._market_event_thread.start()
        self.logger.info("market event thread on.")

    # ── 종료 ─────────────────────────────────────────────────────
    def stop(self):
        """웹소켓 종료를 요청한다. run()의 asyncio 루프가 이 신호로 풀리고,
        그 finally에서 파싱/이벤트 워커와 recorder가 순서대로 드레인된다."""
        self.logger.info("KiSEngine.stop() 호출 — 웹소켓 종료 요청")
        self.ws.stop()

    def run(self, recording=True, trading=False, show=True):
        self.logger.info("websocket mode run. (recording=%s, trading=%s)", recording, trading)

        if recording:
            self.recorder.start()

        self.start_parsing()
        self.start_market_event()

        if trading :
            self.start_trading()

        self.logger.info("웹소켓 루프 시작 (블로킹)")
        try:
            asyncio.run(self.ws.run())
        finally:
            self.logger.info("웹소켓 루프 종료 — 파싱/이벤트 워커 드레인 대기")
            # ws.run()이 SENTINEL을 큐에 넣었다고 바로 recorder.stop()을 부르면,
            # 워커가 아직 그 SENTINEL 앞의 마지막 레코드를 recorder에 못 넣은
            # 채로 recorder가 먼저 드레인·flush될 수 있다 — 그 몇 건이 유실된다.
            # 워커 종료(=SENTINEL까지 다 처리)를 먼저 기다려야 유실이 없다.
            for t in (self._parsing_thread, self._market_event_thread):
                if t is not None:
                    t.join(timeout=5.0)
                    if t.is_alive():
                        self.logger.warning("%s 워커가 5초 안에 끝나지 않음 — 마지막 레코드가 유실될 수 있음", t.name)
            if recording:
                self.recorder.stop()
