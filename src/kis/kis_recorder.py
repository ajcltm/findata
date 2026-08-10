from kis import kis_config
from kis import kis_parser
from kis.kis_websocket import SENTINEL

import sqlite3
import time
from queue import Empty, Queue, Full
import pandas as pd
import logging

class KisRecorder:

    def __init__(self, price_codes, orderbook_codes, save_q, stop_event, parser, test_mode=False):
        self.logger = logging.getLogger('kis')
        self.price_book = price_codes
        self.order_book = orderbook_codes
        self.test_mode = test_mode
        self._stop = stop_event
        self.save_q = save_q
        self.parser = parser

    def recording(self):

            file_path = kis_config.DATA_DIR / 'kis_data.db'
            conn = sqlite3.connect(file_path)
            
            price_book_batch = []
            order_book_batch = []
            order_batch = []
            last_flush = time.time()

            while not self._stop.is_set():
                try:
                    tick = self.save_q.get(timeout=0.2)
                    if tick is SENTINEL:
                        break
                    parsed_tick = self.parser.parse(tick)
                    if parsed_tick is None:
                        self.save_q.task_done()
                        continue
                    if parsed_tick.tr_id == "H0STCNT0":
                        file_name = "new_price_book"
                        if self.test_mode:
                            file_name = "simul_price_book"
                        price_book_batch.extend(parsed_tick.data)  # Extend batch with tick data (assuming tick is a list)
                        
                    if parsed_tick.tr_id == "H0STASP0":
                        file_name = "new_order_book"
                        if self.test_mode:
                            file_name = "simul_order_book"
                        order_book_batch.extend(parsed_tick.data)  # Extend batch with tick data (assuming tick is a list)

                    if parsed_tick.tr_id == "H0STCNI0":
                        file_name = "new_order"
                        if self.test_mode:
                            file_name = "simul_order"
                        order_batch.extend(parsed_tick.data)
                    self.save_q.task_done()
                except Empty:
                    pass

                now = time.time()

                # ① 500개 모이면 저장
                # ② 또는 1초 지났으면 저장
                if len(price_book_batch) >= 100 or (price_book_batch and now - last_flush > 50.0):
                    df = pd.DataFrame(price_book_batch)
                    df.to_sql(file_name, conn, if_exists="append", index=False)  # 매번 덮어쓰기 (최신 데이터만 유지)
                    price_book_batch.clear()
                    last_flush = now
                    self.logger.info(f"recording batch : price ")
                
                if len(order_book_batch) >= 100 or (order_book_batch and now - last_flush > 50.0):
                    df = pd.DataFrame(order_book_batch)
                    df.to_sql(file_name, conn, if_exists="append", index=False)  # 매번 덮어쓰기 (최신 데이터만 유지)
                    order_book_batch.clear()
                    last_flush = now
                    self.logger.info(f"recording batch : order book ")

                if len(order_batch) >= 1 or (order_batch and now - last_flush > 50.0):
                    df = pd.DataFrame(order_batch)
                    df.to_sql(file_name, conn, if_exists="append", index=False)  # 매번 덮어쓰기 (최신 데이터만 유지)
                    order_batch.clear()
                    last_flush = now
                    self.logger.info(f"recording batch : order ")