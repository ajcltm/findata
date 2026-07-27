import threading, datetime
from collections import deque
from kis import kis_api

class MarketState:
    def __init__(self, event_q, subscriptions):
        self.event_q = event_q
        self._lock = threading.Lock()
        self.total_msgs = 0
        self.last_time = None
        self.last_tr_id = None
        self.last_code = None
        self.last_kind = None
        self.last_price = None
        self.recent_lines = deque(maxlen=10)
        self.start_time = datetime.datetime.now()   # 엔진 시작 시간
        self.subscriptions = subscriptions if subscriptions is not None else {}
        self.orders = None

    def on_event(self, tr_id, parsed_data):
        now = datetime.datetime.now()
        kind = "price" if tr_id == "H0STCNT0" else ("orderbook" if tr_id == "H0STASP0" else tr_id)
        price_key = "current_price" if tr_id == "H0STCNT0" else ("est_exec_price" if tr_id == "H0STASP0" else tr_id)

        code = price = None
        if parsed_data and parsed_data:
            row0 = parsed_data[0]
            code = row0.get("stock_code")
            price = row0.get(price_key)

        with self._lock:
            self.total_msgs += 1
            self.last_time = now
            self.last_tr_id = tr_id
            self.last_kind = kind
            self.last_code = code
            self.last_price = price
            self.recent_lines.append(f"{now.strftime('%H:%M:%S')} {kind} {code} price={price}")

    def snapshot(self):
        with self._lock:
            elapsed = datetime.datetime.now() - self.start_time  # 경과시간 계산
            return {
                "total_msgs": self.total_msgs,
                "last_time": self.last_time.strftime("%H:%M:%S") if self.last_time else "-",
                "last_tr_id": self.last_tr_id,
                "last_kind": self.last_kind,
                "last_code": self.last_code,
                "last_price": self.last_price,
                "recent_lines": list(self.recent_lines),
                "event_qsize": self.event_q.qsize(),
                "start_time":     self.start_time.strftime("%H:%M:%S"),
                "elapsed":        str(elapsed).split(".")[0],   # "0:03:22" 형식
                "subscriptions": dict(self.subscriptions),  # ← 렌더 시점에 복사해서 읽음
                "orders" : self.orders
            }

    def refresh_orders(self):
        result = kis_api.domestic_stock_inquire_daily_ccld(
            ord_gno_brno="",   # 빈 문자열 = 전체 영업점 조회
            ODNO=""            # 빈 문자열 = 전체 주문번호 조회
        )
        self.orders = result.get("output1", [])