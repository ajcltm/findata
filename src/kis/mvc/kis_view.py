import os, datetime
import subprocess

class HomeScreen:
    name = "home"

    def render(self, state, current_input=""):      # ← current_input 파라미터 추가
        subprocess.run("cls" if os.name == "nt" else "clear", shell=True)
        s = state.snapshot()
        now = datetime.datetime.now().strftime("%H:%M:%S")

        print("=" * 70)
        print(f"  HOME  now={now}")
        print("=" * 70)

        # 엔진 시작 / 경과 시간
        print(f"  엔진 시작: {s['start_time']}   경과: {s['elapsed']}")
        print("-" * 70)

        # 구독 정보 (성공/실패 포함)
        print(f"  {'구분':<12} {'종목코드':<10} {'상태'}")
        print("  " + "-" * 35)
        for key, info in s["subscriptions"].items():
            code = key.replace("_ob", "")
            print(f"  {info['type']:<12} {code:<10} {info['status']}")

        print("=" * 70)
        print(f"  [h]home  [r]realdata  [o]orders  [q]quit")

class RealDataScreen:
    name = "realdata"
    def render(self, state, current_input=""):
        # os.system("cls")
        subprocess.run("cls" if os.name == "nt" else "clear", shell=True)
        s = state.snapshot()
        now = datetime.datetime.now().strftime("%H:%M:%S")

        print("="*70)
        print(f"HOME now={now}")
        print("="*70)
        print(f"msgs={s['total_msgs']}  last={s['last_time']}  {s['last_kind']}  {s['last_code']}  price={s['last_price']}")
        print(f"event_q={s['event_qsize']}")
        print("-"*70)
        for line in s["recent_lines"]:
            print(" ", line)
        print("-"*70)
        print(f"  [h]home  [r]realdata  [o]orders  [q]quit")

class OrderScreen:
    name = "orders"                        # ← controller가 이 이름으로 찾음

    def render(self, state, current_input=""):
        subprocess.run("cls" if os.name == "nt" else "clear", shell=True)
        s = state.snapshot()
        now = datetime.datetime.now().strftime("%H:%M:%S")

        print("=" * 70)
        print(f"ORDERS  now={now}")
        print("=" * 70)
        print(f"{'종목코드':<10} {'구분':<6} {'주문수량':<8} {'체결수량':<8} {'주문단가':<10} {'상태'}")
        print("-" * 70)

        orders = s.get("orders", [])       # snapshot에서 주문 목록 가져옴

        if not orders:
            print("  주문 내역이 없습니다.")
        else:
            for o in orders:
                # API 응답 필드명: KIS 문서 기준
                code   = o.get("pdno", "-")         # 종목코드
                dvsn   = o.get("sll_buy_dvsn_cd_name", "-")  # 매수/매도
                qty    = o.get("ord_qty", "-")       # 주문수량
                ccld   = o.get("tot_ccld_qty", "-")  # 체결수량
                price  = o.get("ord_unpr", "-")      # 주문단가
                status = o.get("ord_tmd", "-")       # 주문시간

                print(f"  {code:<10} {dvsn:<6} {qty:<8} {ccld:<8} {price:<10} {status}")

        print("-" * 70)
        print(f"  [h]home  [r]realdata  [o]order update  [q]quit")