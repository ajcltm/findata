import asyncio
import websockets
import json
import requests
import random
import logging

from kis import kis_config

from kis import fake_kis_websocket as fake_kis

websockets.connect = fake_kis.connect 

class KISWebSocket:

    def __init__(self, price_codes=None, orderbook_codes=None, simul_mode=False):

        self.logger = logging.getLogger("kis")

        self.simul_mode = simul_mode

        # TR 코드
        self.TR_PRICE = "H0STCNT0"       # 실시간 주식 체결가 (시세)
        self.TR_ORDERBOOK = "H0STASP0"         # 실시간 주식 호가 (시세)
        self.TR_NOTICE = "H0STCNI0"      # 모의 : "H0STCNI9"  # 체결 통보
        self.price_codes = price_codes
        self.orderbook_codes = orderbook_codes
        self.approval_key = self.get_approval_key()
        self.crypto_info = {}
        self.RETRY_DELAY     = 5    # 재연결 대기 초
        self.MAX_RETRY_DELAY = 60   # 최대 대기 초 (지수 백오프 상한)
        self.recording_q = asyncio.Queue()
        self.trading_q = asyncio.Queue()
        self.show_q = asyncio.Queue()

        self.subscriptions = {}
        # 초기 상태: 아직 구독 안 됨
        for code in (price_codes or []):
            self.subscriptions[f"price_{code}"] = {"code": code, "type": "시세", "status": "대기중"}
        for code in (orderbook_codes or []):
            self.subscriptions[f"ob_{code}"] = {"code": code, "type": "호가", "status": "대기중"}


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

    def make_subscribe_msg(self, approval_key, tr_type, tr_id, tr_key):
        """
        tr_type: "1" = 등록, "2" = 해제
        tr_id  : TR 코드
        tr_key : 종목코드(시세) 또는 HTS ID(체결통보)
        """
        return json.dumps({
            "header": {
                "approval_key": approval_key,
                "custtype": "P",
                "tr_type": tr_type,
                "content-type": "utf-8"
            },
            "body": {
                "input": {
                    "tr_id": tr_id,
                    "tr_key": tr_key
                }
            }
        })
    
    def check_json(self, msg):
        if msg[0] in ('{', '['):
            return json.loads(msg)
        return False
    
    async def do_pingpong(self, ws, msg):
        """PINGPONG 메시지 처리 - 서버에서 PINGPONG이 오면 그대로 돌려보냄"""
        await ws.send(msg)  # 서버에게 PONG 응답
        self.logger.info("[PINGPONG] 연결 유지 신호 전송")

    def setting_crypto_info(self, data):
        # 일반 메시지 처리 (구독 응답 등)
        tr_id = data.get("header", {}).get("tr_id")
        iv = data.get('body', {}).get('output', {}).get('iv', '')
        key = data.get('body', {}).get('output', {}).get('key', '')
        msg1 = data.get('body', {}).get('msg1', '')

        self.logger.info(f"[구독 응답] tr_id: {tr_id} - msg1: {msg1} - iv: {iv} - key : {key}")
        self.crypto_info[tr_id] = {'iv' : iv, 'key' : key}

    def setting_subscribe_info(self, data):
        """책임: 구독 성공/실패 상태만 업데이트"""
        tr_id  = data.get("header", {}).get("tr_id")
        tr_key = data.get("header", {}).get("tr_key", "")
        msg1   = data.get('body', {}).get('msg1', '')

        status = msg1

        if tr_id == "H0STCNT0":
            key_name = f"price_{tr_key}"
        elif tr_id == "H0STASP0":
            key_name = f"ob_{tr_key}"
        else:
            return

        if key_name in self.subscriptions:
            self.subscriptions[key_name]["status"] = status

    # ============================================================
    # 5. 메인 WebSocket 핸들러
    # ============================================================
    async def run_session(self):

        """
        approval_key : ws 승인키
        hts_id       : HTS ID (체결통보 구독에 필요)
        stock_codes  : 시세 구독할 종목코드 리스트 ex) ["005930", "000660"]
        """
        async with websockets.connect(kis_config.WS_URL) as ws:
            print(f"✅ WebSocket 연결됨: {kis_config.WS_URL}")

            # --- 시세 구독 등록 ---
            for code in self.price_codes:
                await ws.send(self.make_subscribe_msg(self.approval_key, "1", self.TR_PRICE, code))
                print(f"  📡 시세 구독: {code}")
                await asyncio.sleep(0.1)                # 구독 응답 대기 후 다음 구독

            for code in self.orderbook_codes:
                await ws.send(self.make_subscribe_msg(self.approval_key, "1", self.TR_ORDERBOOK, code))
                print(f"  📡 호가 구독: {code}")
                
                # 구독 응답 대기 후 다음 구독
                await asyncio.sleep(0.3)  # 0.1 → 0.3

            # --- 체결 통보 구독 등록 ---
            await ws.send(self.make_subscribe_msg(self.approval_key, "1", self.TR_NOTICE, kis_config.HTS_ID))
            print(f"  📡 체결 통보 구독: {kis_config.HTS_ID}")

            num = 0
            # --- 수신 루프 ---
            while True:
                try:
                    msg = await ws.recv()
                    # self.logger.info(f"recv : {msg}")

                    if self.simul_mode:                            
                        msg = await self.produce_simul_msg(num)
                        self.logger.info(f"{msg}")
                        

                    data = self.check_json(msg)
                    
                    if data:
                        tr_id = data.get("header", {}).get("tr_id")
                        if tr_id == "PINGPONG":
                            await self.do_pingpong(ws, msg)
                        else :
                            self.setting_crypto_info(data)   # 암호화 키 저장
                            self.setting_subscribe_info(data)   # 구독 상태 업데이트
                        continue
                    
                    self.recording_q.put_nowait(msg)
                    self.trading_q.put_nowait(msg)
                    self.show_q.put_nowait(msg)
                    # self.logger.info("msg put in recording, trading q")

                    num = num + 1

                except websockets.exceptions.ConnectionClosed:
                    self.logger.error("❌ WebSocket 연결 종료 — 재연결 시도...")
                    break
                except Exception as e:
                    self.logger.error(f"⚠️ 오류: {e}")



    async def run(self):

        # WebSocket을 백그라운드로 실행
        ws_task = asyncio.create_task(
            self.run_session()
        )
        await ws_task

        delay = self.RETRY_DELAY

        session_num = 1
        while True:
            try:
                session_num =+ 1
                self.logger.info(f"session number : {session_num} started.")

                # 재연결마다 승인키 갱신 (만료 방지)
                self.approval_key = self.get_approval_key()
                self.logger.info(f"new approval key published.")
                await self.run_session()

            except (websockets.exceptions.ConnectionClosed,
                    websockets.exceptions.WebSocketException) as e:
                self.logger.error(f"⚠️connection error: {e}")

            except OSError as e:
                # 네트워크 자체 불가
                self.logger.error(f"❌ network error: {e}")

            except Exception as e:
                self.logger.error(f"❌ unexpected error: {e}")

            # 지수 백오프 대기
            self.logger.info(f"🔄 {delay}초 후 재연결...")
            await asyncio.sleep(delay)
            delay = min(delay * 2, self.MAX_RETRY_DELAY)  # 5 → 10 → 20 → 40 → 60 상한

    async def produce_simul_msg(self, num):

        if num == 0:
            print("crypto setted")
            self.crypto_info["H0STCNI0"] = {'iv' : '71430b89402b6e40', 'key' : 'ymmahjojvrgbvmgfnmiuqhvnavgygpzg'}

        # if self.check_json(msg):
        #     self.logger.info(f"{msg}")
        #     return msg

        await asyncio.sleep(1)
        order_book_msg = "0|H0STASP0|001|088350^131404^0^6140^6150^6160^6170^6180^6190^6200^6210^6220^6230^6130^6120^6110^6100^6090^6080^6070^6060^6050^6040^81678^42182^16711^9388^9914^18570^95925^19565^13797^25943^1095^21584^13696^11160^4400^13144^4930^11426^30268^8388^333673^120091^0^0^0^0^48076^-6600^5^-100.00^70028035^100^888^0^0^0^6135^0^0"
        price_msg = "0|H0STCNT0|003|000400^131509^2920^2^395^15.64^2856.31^3250^3280^2550^2925^2920^3418^27066179^77309405544^20814^22669^1855^72.43^14761609^10692460^5^0.40^230.98^090242^5^-330^090243^5^-360^094341^2^370^20260223^20^N^5210^4632^118651^86686^8.72^11265250^240.26^0^^2725^000400^131509^2925^2^400^15.84^2856.32^3250^3280^2550^2925^2920^1873^27068052^77314884069^20814^22670^1856^72.45^14761609^10694333^1^0.40^230.99^090242^5^-325^090243^5^-355^094341^2^375^20260223^20^N^5210^4632^118651^86686^8.72^11265250^240.28^0^^2725^000400^131509^2925^2^400^15.84^2856.32^3250^3280^2550^2925^2920^3^27068055^77314892844^20814^22671^1857^72.45^14761609^10694336^1^0.40^230.99^090242^5^-325^090243^5^-355^094341^2^375^20260223^20^N^3337^6214^116728^83780^8.72^11265250^240.28^0^^2725"
        ordr_msg = "1|H0STCNI0|001|E8UESjYyDtc4TjX+zhIArXr/zWxrkOBQQlhdK2IaawgkPEoBH3aXcZlbsrWFB7/9ez5Z6kpC7fAxB0EZ0qKz0f4tyFfbdUybAnXjgmiKeyuOTnwksqwyOAlKSet9adcKMqm49f2UR/SKHBumH5OeU06nuczWLfsTZ0wAASA1JaOL/rMUhUyjp5cUNJa2EMMz"
        msgs = []
        if self.price_codes:
            msgs.append(price_msg)
        if self.orderbook_codes:
            msgs.append(order_book_msg)
        msgs.append(ordr_msg)
        return random.choice(msgs)

if __name__ == "__main__":
    # 구독할 종목
    stock_codes = ["005930", "000660"]  # 삼성전자, SK하이닉스
    ws = KISWebSocket(price_codes=stock_codes, orderbook_codes=stock_codes)
    asyncio.run(ws.run())