import threading, time
from queue import Empty
from src.kis.mvc import kis_model
from src.kis.mvc import kis_view
from kis import kis_parser

class ScreenManager:
    def __init__(self, event_q, parser, subscriptions=None, start="home", interval=1):
        self.state =  kis_model.MarketState(event_q=event_q, subscriptions= subscriptions)
        self.screens = {s.name: s for s in [kis_view.HomeScreen(), kis_view.RealDataScreen(), kis_view.OrderScreen()]}
        self.current = self.screens[start]
        self.interval = interval
        self._stop = threading.Event()
        self.parser = parser
        self.subscriptions = subscriptions

    def start(self):
        threading.Thread(target=self._render_loop, daemon=True).start()
        self._input_loop()  # 메인 스레드 input 유지(깔끔)

    def _drain_events(self, max_items=5000):
        """엔진 event_q를 최대 max_items개까지 비우면서 state 갱신"""
        q = self.state.event_q
        for _ in range(max_items):
            try:
                data = q.get_nowait()
                tr_id, parsed = self.parser.parse(data)
            except Empty:
                break
            self.state.on_event(tr_id, parsed)
            q.task_done()

    def _render_loop(self):
        while not self._stop.is_set():
            # ✅ 여기서 이벤트 소비 → state 업데이트
            self._drain_events()

            # ✅ 한 장면 렌더
            self.current.render(self.state)

            time.sleep(self.interval)

    def _input_loop(self):
        while not self._stop.is_set():
            k = input().strip().lower()
            if k == "h":
                self.current = self.screens["home"]
            elif k == "o":
                self.state.refresh_orders()
                self.current = self.screens["orders"]
            elif k == "r":
                self.current = self.screens["realdata"]
            elif k == "q":
                self._stop.set()
                self.state.engine.stop()
                break