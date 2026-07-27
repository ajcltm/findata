from numpy import random
import websocket                 # pip install websocket-client
import json
import requests
import datetime
import pandas as pd
from kis import kis_api
from kis import kis_config
import threading
from queue import Empty, Queue, Full
import time
from collections import deque
import pandas as pd
import sqlite3
import random
from strategy import test_st
import os
from src.kis.mvc import kis_controller


# ──────────────────────────────────────
# 수신 데이터 파싱
# ──────────────────────────────────────
def parse_execution(data_str):
    """체결가 데이터 파싱"""
    f = data_str.split("^")
    now = datetime.datetime.now().isoformat(timespec="seconds")
    data = [{'datetime' : now, 'stock_code':f[0], 'price':f[2], 'volume': f[12], 'total_tr_value':f[13], 'change':f[4], 'pct_change':f[5]}]
    return data

def parse_orderbook(data_str):
    """호가 데이터 파싱"""
    f = data_str.split("^")
    now = datetime.datetime.now().isoformat(timespec="seconds")
    code = f[0]
    data = []
    for i in range(10):
        data.append({'datetime' : now, 'stock_code':code, 'level': f'ask{i+1}', 'price' : f[3 + i], 'qty' : f[23 + i]}) # 매도호가 매도잔량
        data.append({'datetime' : now, 'stock_code':code, 'level': f'bid{i+1}', 'price' : f[13 + i], 'qty' : f[33 + i]}) # 매수호가 매수잔량
    return data

# ──────────────────────────────────────
# WebSocket 콜백 함수들
# ──────────────────────────────────────

class KisEngine:

    def __init__(self, price_book=None, order_book=None):
        self.price_book = price_book # 체결가
        self.order_book = order_book # 호가
        self.APPROVAL_KEY = self.get_approval_key()

        self.save_q = Queue(maxsize=100_000)

        self._latest_lock = threading.Lock()
        self._latest_tick = None

        self._stop = threading.Event()
        self.tick_event = threading.Event()
        self.strategy = None
        self.window = deque(maxlen=200)

        # --- SHOW(모니터링) ---
        self.show_enabled = threading.Event()   # 켜짐/꺼짐 토글
        self.show_stop = threading.Event()
        self.show_interval = 0.5  # 화면 갱신 주기(초)

        self._stats_lock = threading.Lock()
        self._stats = {
            "total_msgs": 0,
            "last_msg_time": None,
            "last_tr_id": None,
            "last_code": None,
            "last_price": None,
            "last_kind": None,  # "tick" / "orderbook"
        }


        self.event_q = Queue(maxsize=200_000)
        

    def on_message(self, ws, message):
        """서버에서 메시지가 올 때마다 호출되는 함수"""
        
        # ① JSON 형태인지 확인 (구독응답, PINGPONG 등)
        if message[0] in ('{', '['):
            data = json.loads(message)
            
            # PINGPONG 메시지면 그대로 돌려보냄 (연결 유지용)
            if data.get("header", {}).get("tr_id") == "PINGPONG":
                ws.send(message)                # 서버에게 PONG 응답
                print("[PINGPONG] 연결 유지 신호 전송")
                if not self.test_mode:
                    return
            
            # 구독 응답 메시지
            print(f"[응답] {data.get('header', {}).get('tr_id', '')} - "
                f"{data.get('body', {}).get('msg1', '')}")
            if not self.test_mode:
                    return

        if self.test_mode:
            parsed_data = self.export_test_data()  # 테스트용 더미 데이터 생성
        else:
            # ② 문자열 형태 → 실시간 데이터
            # 형식: "암호화여부|tr_id|건수|데이터"
            parts = message.split("|")              # "|"로 분리
            encrypt_flag = parts[0]                 # 0: 평문, 1: 암호화
            tr_id = parts[1]                        # 거래 ID
            data_count = parts[2]                   # 데이터 건수
            raw_data = parts[3]                     # 실제 데이터 ("^"로 구분)

            if tr_id == "H0STCNT0":                # 체결가 데이터
                parsed_data = parse_execution(raw_data)
                #print(parsed_data)
            elif tr_id == "H0STASP0":              # 호가 데이터
                parsed_data = parse_orderbook(raw_data)
                #print(pd.DataFrame(parsed_data).sort_values(by="price", ascending=False))

        try:
            self.save_q.put(parsed_data)
        except Full:
            # 유실 절대 금지라면 여기서 전략을 바꿔야 함:
            # 1) put(tick)으로 블록(수신 지연 감수)
            # 2) 파일/메모리 버퍼 확대
            # 3) 저장 워커 확장/배치쓰기
            # 지금은 경고만:
            print("save_q FULL! (유실 금지면 설계 조정 필요)")
            # self.save_q.put(tick)  # <- 유실 금지 최우선이면 이쪽

        # 2) 최신 tick은 덮어쓰기(항상 최신만)
        with self._latest_lock:
            self._latest_tick = parsed_data

        self.tick_event.set()  # tick 이벤트 신호 (전략이 대기 중이라면 깨어남)

                # --- SHOW용 통계 업데이트 ---
        with self._stats_lock:
            self._stats["total_msgs"] += 1
            self._stats["last_msg_time"] = datetime.datetime.now()
            self._stats["last_tr_id"] = tr_id if not self.test_mode else "TEST"
            self._stats["last_kind"] = "orderbook" if (not self.test_mode and tr_id == "H0STASP0") else "tick"
            # parsed_data는 list[dict] 형태니까 첫 원소 기준으로 요약
            if parsed_data and isinstance(parsed_data, list):
                row0 = parsed_data[0]
                self._stats["last_code"] = row0.get("stock_code")
                self._stats["last_price"] = row0.get("price")

        self.event_q.put((tr_id, parsed_data))   # 유실 싫으면 put_nowait 말고 put(블록)

    def on_open(self, ws):
        """WebSocket 연결이 열렸을 때 호출되는 함수"""
        print("=" * 50)
        print("WebSocket 연결 성공!")
        print("=" * 50)
        if self.price_book:
            for stock in self.price_book:
                # 실시간 체결가 구독
                ws.send(self.build_message("H0STCNT0", stock))
                print(f"[구독요청] {stock} 실시간 체결가 (H0STCNT0)")

        if self.order_book:
            for stock in self.order_book:
                #  실시간 호가 구독 (하나의 연결에서 여러 개 구독 가능!)
                ws.send(self.build_message("H0STASP0", stock))
                print(f"[구독요청] {stock} 실시간 호가 (H0STASP0)")

    def on_error(self, ws, error):
        """에러 발생 시 호출되는 함수"""
        print(f"[에러] {error}")

    def on_close(self, ws, status_code, msg):
        """WebSocket 연결이 끊겼을 때 호출되는 함수"""
        print(f"[연결종료] 상태코드: {status_code}, 메시지: {msg}")

    def get_approval_key(self):
        url = f"{kis_config.domain}/oauth2/Approval"
        headers = {"content-type": "application/json"}
        body = {
            "grant_type": "client_credentials",
            "appkey": kis_config.APPKEY,
            "secretkey": kis_config.APPSECRET
        }
        res = requests.post(url, headers=headers, data=json.dumps(body))
        return res.json()["approval_key"]
    
    # ──────────────────────────────────────
    # 구독 메시지 생성
    # ──────────────────────────────────────
    def build_message(self, tr_id, tr_key, tr_type="1"):
        return json.dumps({
            "header": {
                "approval_key": self.APPROVAL_KEY,
                "custtype": "P",
                "tr_type": tr_type,              # "1":구독, "2":해제
                "content-type": "utf-8"
            },
            "body": {
                "input": {
                    "tr_id": tr_id,              # H0STCNT0 :체결가 or H0STASP0 : 호가
                    "tr_key": tr_key             # 종목코드
                }
            }
        })
    
    def recording(self):

        file_path = kis_config.DATA_DIR / 'kis_data.db'
        conn = sqlite3.connect(file_path)
        if self.price_book:
            file_name = "price_book"
            if self.test_mode:
                file_name = "test_price_book"
        elif self.order_book:
            file_name = "order_book"
            if self.test_mode:
                file_name = "test_order_book"
        
        batch = []
        last_flush = time.time()

        while not self._stop.is_set():
            try:
                tick = self.save_q.get(timeout=0.2)
                batch.extend(tick)  # Extend batch with tick data (assuming tick is a list)
                self.save_q.task_done()
            except Empty:
                pass

            now = time.time()

            # ① 500개 모이면 저장
            # ② 또는 1초 지났으면 저장
            if len(batch) >= 2 or (batch and now - last_flush > 50.0):
                df = pd.DataFrame(batch)
                df.to_sql(file_name, conn, if_exists="append", index=False)  # 매번 덮어쓰기 (최신 데이터만 유지)
                batch.clear()
                last_flush = now

    def trading(self):
        if self.strategy is None:
            print("전략이 없습니다. trading() 종료.")
            return
        while not self._stop.is_set():
            self.tick_event.wait()   # 틱이 올 때까지 잠
            self.tick_event.clear()  # 깨어났으니 다시 잠들 준비
            # 최신 tick 스냅샷만 잠깐 복사 (락 짧게)

            with self._latest_lock:
                tick = self._latest_tick

            if tick:
                self.window.extend(tick)   # 최근 200틱 유지
            self.strategy.next()  # 전략의 next() 호출 (틱마다)
    
    def add_strategy(self, strategy):
        self.strategy = strategy(data=self.window)

    def stop(self):
        self._stop.set()

    def export_test_data(self):
        # now = datetime.datetime.now().strftime("%H:%M:%S")
        # if self.price_book:
        #     test_data = {'datetime' : now, 'stock_code':"test_code", 'price':f"{random.randint(1000, 2000)}", 'volume': "test_volume", 'total_tr_value':"test_total_tr_value", 'change':"test_change", 'pct_change':"test_pct_change"}
        #     parsed_data = [test_data for _ in range(5)]
        # elif self.order_book:
        #     test_data = {'datetime' : now, 'stock_code':'test_code', 'level': 'test_level', 'price' : f"{random.randint(1000, 2000)}", 'qty' : 'test_qty'}
        #     parsed_data = [test_data for _ in range(5)]
        # return parsed_data

        price_book_message = "0|H0STASP0|001|088350^131404^0^6140^6150^6160^6170^6180^6190^6200^6210^6220^6230^6130^6120^6110^6100^6090^6080^6070^6060^6050^6040^81678^42182^16711^9388^9914^18570^95925^19565^13797^25943^1095^21584^13696^11160^4400^13144^4930^11426^30268^8388^333673^120091^0^0^0^0^48076^-6600^5^-100.00^70028035^100^888^0^0^0^6135^0^0"
        order_book_message = "" 
    
    def run(self, trading=False, recording=False, test_mode=True):
        ui = kis_controller.ScreenManager(event_q=self.event_q)
        threading.Thread(target=ui.start, daemon=True).start()
        self.test_mode = test_mode
        if trading:
            threading.Thread(target=self.trading, daemon=True).start()

        if recording:
            threading.Thread(target=self.recording, daemon=True).start()

        if self.price_book or self.order_book:         
            # ② WebSocket 앱 생성 (콜백 함수 연결)
            ws = websocket.WebSocketApp(
                kis_config.WS_URL,                              # WebSocket URL
                on_open=self.on_open,                     # 연결 시 → 구독 요청
                on_message=self.on_message,               # 데이터 수신 시 → 파싱
                on_error=self.on_error,                   # 에러 시
                on_close=self.on_close                    # 종료 시
            )
                # ③ 무한 루프로 실행 (Ctrl+C로 종료)
            print("실시간 데이터 수신 시작... (Ctrl+C로 종료)")
            ws.run_forever(ping_interval=20, ping_timeout=10)                         # 연결 유지하며 데이터 수신
        

# ──────────────────────────────────────
# 실행
# ──────────────────────────────────────
if __name__ == "__main__":
    e = KisEngine(order_book=['001290', '015260'])
    e.add_strategy(test_st.KisOvernightMomentumStrategy)
    e.run(trading=True, recording=True, test_mode=False)