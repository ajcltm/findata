import threading
from collections import deque
from queue import Empty, Queue, Full

from kis import kis_parser

class KisTrader:
     
    def __init__(self, strategy, parser, trading_q, stop_event, test_mode):
           
           self.trading_q = trading_q
           self._stop = stop_event
           self.test_mode = test_mode
           self.parser = parser
           self.window = deque(maxlen=200)
           self.strategy = strategy
           if strategy:
               self.strategy = strategy(data =self.window, test_mode=self.test_mode)

    def trading(self):
            if self.strategy is None:
                print("전략이 없습니다. trading() 종료.")
                return
            
            while not self._stop.is_set():
                try:
                    data = self.trading_q.get(timeout=0.2)
                    if data:
                        parsed = self.parser.parse(data)
                        self.window.extend(parsed)   # 최근 200틱 유지
                    self.strategy.next()  # 전략의 next() 호출 (틱마다)
                except Empty:
                    pass