import threading
import time
from typing import Dict, Optional
from uuid7 import uuid7

uid = uuid7()
print(uid)

from order.order import Order, OrderStatus


class Broker:
    def __init__(self, kis_api, poll_interval: float = 1.0):
        self.kis_api = kis_api
        self.poll_interval = poll_interval

        self._orders: Dict[str, Order] = {}     # 전체 주문(원하면 보관)
        self._pending: Dict[str, Order] = {}    # 미완료 주문만
        self._lock = threading.Lock()

        self._stop = threading.Event()
        self._poller: Optional[threading.Thread] = None

    # ---------- lifecycle ----------
    def start(self):
        if self._poller and self._poller.is_alive():  # 이미 실행 중이면 시작 안함
            return
        self._stop.clear() # stop 플래그 초기화
        self._poller = threading.Thread(target=self._polling, daemon=True)
        self._poller.start()

    def stop(self):
        self._stop.set()

    # ---------- trading ----------
    def buy(self, symbol: str, qty: float, price: Optional[float] = None, strategy: Optional[str] = None) -> Order:
        order_type = "market" if price is None else "limit"
        order = Order(
            order_id= str(uuid7()),
            api_value=None,   # 조회키가 따로 있으면 여기로 넣는 게 베스트(예: odno)
            symbol=symbol,
            status=None,
            order_type=order_type,
            qty=float(qty),
            price=price,
            strategy=strategy,
        )

        order.update_state(OrderStatus.SUBMITTED)
        
        resp = self.kis_api.buy(symbol=symbol, qty=qty, price=price, order_type=order_type)
        if resp.get("rt_cd") != "0":
            order.update_state(OrderStatus.REJECTED)
            return order
        
        api_key = resp['output'][0].get("KRX_FWDG_ORD_ORGNO") +"_"+ resp['output'][0].get("ODNO") +"_"+ resp['output'][0].get("ORD_TMD")
        order.update_state(OrderStatus.ACCEPTED, api_value=api_key)
        self._add_pending(order)
        return order

    def sell(self, symbol: str, qty: float, price: Optional[float] = None, strategy: Optional[str] = None) -> Order:
        order_type = "market" if price is None else "limit"
        order = Order(
            order_id= str(uuid7()),
            api_value=None,   # 조회키가 따로 있으면 여기로 넣는 게 베스트(예: odno)
            symbol=symbol,
            status=None,
            order_type=order_type,
            qty=float(qty),
            price=price,
            strategy=strategy,
        )

        order.update_state(OrderStatus.SUBMITTED)
        
        resp = self.kis_api.sell(symbol=symbol, qty=qty, price=price, order_type=order_type)
        if resp.get("rt_cd") != "0":
            order.update_state(OrderStatus.REJECTED)
            return order
        
        api_key = resp['output'][0].get("KRX_FWDG_ORD_ORGNO") +"_"+ resp['output'][0].get("ODNO") +"_"+ resp['output'][0].get("ORD_TMD")
        order.update_state(OrderStatus.ACCEPTED, api_value=api_key)
        self._add_pending(order)
        return order

    def _add_pending(self, order: "Order"):
        with self._lock:
            self._orders[order.order_id] = order
            self._pending[order.order_id] = order
        self.start()

    # ---------- polling ----------
    def _polling(self):
        """
        미완료 주문이 존재하는 동안 계속 조회.
        - pending이 비어있으면 루프는 계속 돌되, sleep만 하며 대기
          (원하면 pending 비면 스레드 종료하게 바꿀 수도 있음)
        """
        while not self._stop.is_set():
            # 스냅샷 떠서 락 점유 최소화
            with self._lock:
                pending_items = list(self._pending.items())

            if not pending_items:
                time.sleep(self.poll_interval)
                continue
            r = self.kis_api.domestic_stock_inquire_daily_ccld()
            for order_id, order in pending_items:
                if self._stop.is_set():
                    break

                try:
                    r.get('output1').get()
                    self.update_order_status(order)
                except Exception as e:
                    # 폴링 에러도 로그 남기고 계속
                    order.update_state(order.status, api_value=f"{order.api_value} | poll_error={e}")

                # 완료면 pending에서 제거
                if self._is_terminal(order.status):
                    with self._lock:
                        self._pending.pop(order_id, None)

            time.sleep(self.poll_interval)

    def update_order_status(self, order: "Order"):
        """
        - order.api_value(또는 order_id)로 KIS 상태 조회
        - 결과를 order.update_state()로 반영 (자동 log)
        """
        res = self.kis_api.inquire_order(order.api_value)
        new_status = self._map_status(res)
        order.update_state(new_status, api_value=str(res))

    # ---------- helpers ----------
    @staticmethod
    def _is_terminal(status: str) -> bool:
        return status in {"filled", "canceled", "rejected", "expired"}

    @staticmethod
    def _map_status(res: dict) -> str:
        # KIS 응답 키에 맞춰 수정 필요(예시)
        s = (res.get("status") or res.get("ord_stat") or "").upper()
        mapping = {
            "SUBMIT": "submit",
            "ACPT": "accepted",
            "ACCEPTED": "accepted",
            "PART": "partial_filled",
            "PARTIAL": "partial_filled",
            "FILL": "filled",
            "FILLED": "filled",
            "CANC": "canceled",
            "CANCELED": "canceled",
            "CANCELLED": "canceled",
            "RJCT": "rejected",
            "REJECTED": "rejected",
            "EXPR": "expired",
            "EXPIRED": "expired",
        }
        return mapping.get(s, "unknown")