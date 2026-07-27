"""
[테스트 목적]
domestic_stock_inquire_daily_ccld() 실제 API 호출 결과를 눈으로 확인하기 위한 테스트
- API 응답 구조 (output1, output2, rt_cd 등) 파악
- OrderScreen에서 사용할 필드명 확인

[실행 방법]
프로젝트 루트에서 실행:
    python -m pytest test/test_inquire_daily_ccld.py -v -s
    또는
    python test/test_inquire_daily_ccld.py
"""

import sys
import os
import json
import unittest

# ── 경로 설정: src 폴더를 import 경로에 추가 ──────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
sys.path.insert(0, SRC_DIR)  # src/kis/kis_api.py 등을 import 가능하게 함

# ── 실제 모듈 import ───────────────────────────────────────────────────────
from kis import kis_api  # 실제 KIS API 함수


class TestInquireDailyCcld(unittest.TestCase):
    """주식일별주문체결조회 API 응답 내용 확인 테스트"""

    def test_print_raw_response(self):
        """
        [실행 흐름]
        1. domestic_stock_inquire_daily_ccld("", "") 호출
        2. API 서버로 GET 요청 전송
        3. JSON 응답 수신
        4. 전체 응답을 그대로 출력 → 필드명 파악
        """
        print("\n" + "=" * 70)
        print("  [API 호출] domestic_stock_inquire_daily_ccld")
        print("=" * 70)

        # ── 실제 API 호출 ──────────────────────────────────────────────────
        result = kis_api.domestic_stock_inquire_daily_ccld(
            ord_gno_brno="",   # 빈 문자열 = 전체 영업점 조회
            ODNO=""            # 빈 문자열 = 전체 주문번호 조회
        )

        # ── 응답 전체 출력 (JSON 포맷, 보기 좋게) ──────────────────────────
        print("\n[전체 응답 (raw)]")
        print(json.dumps(result, ensure_ascii=False, indent=2))

        # ── 응답 코드 확인 ─────────────────────────────────────────────────
        rt_cd = result.get("rt_cd")          # "0" = 성공, 그 외 = 실패
        msg   = result.get("msg1", "")       # 응답 메시지
        print(f"\n[응답코드] rt_cd={rt_cd}  msg={msg}")

        # ── output1: 주문 리스트 출력 ──────────────────────────────────────
        output1 = result.get("output1", [])
        print(f"\n[output1] 주문 건수: {len(output1)}건")

        if output1:
            # 첫 번째 주문 항목의 키 목록 출력 → 어떤 필드가 있는지 확인
            print("\n  ▶ 첫 번째 주문 항목의 필드 목록:")
            first_order = output1[0]
            for key, value in first_order.items():
                print(f"    {key:<30} = {value}")

            # 모든 주문 항목 요약 출력
            print(f"\n  ▶ 전체 주문 목록 ({len(output1)}건):")
            print(f"  {'종목코드':<10} {'매수/매도':<8} {'주문수량':<8} {'체결수량':<8} {'주문단가':<10}")
            print("  " + "-" * 50)
            for o in output1:
                code  = o.get("pdno", "-")               # 종목코드
                dvsn  = o.get("sll_buy_dvsn_cd_name", "-")  # 매수/매도 구분명
                qty   = o.get("ord_qty", "-")            # 주문수량
                ccld  = o.get("tot_ccld_qty", "-")       # 체결수량
                price = o.get("ord_unpr", "-")           # 주문단가
                print(f"  {code:<10} {dvsn:<8} {qty:<8} {ccld:<8} {price:<10}")
        else:
            print("  → 주문 내역 없음 (output1 비어있음)")

        # ── output2: 합계 정보 출력 ────────────────────────────────────────
        output2 = result.get("output2", {})
        print(f"\n[output2] 합계 정보:")
        if output2:
            for key, value in output2.items():
                print(f"  {key:<30} = {value}")
        else:
            print("  → output2 없음")

        print("\n" + "=" * 70)

        # ── 테스트 통과 조건: API 호출 자체가 성공했는지 ──────────────────
        self.assertIn("rt_cd", result, "API 응답에 rt_cd 필드가 없습니다")
        self.assertEqual(rt_cd, "0", f"API 호출 실패: rt_cd={rt_cd}, msg={msg}")


if __name__ == "__main__":
    # python test/test_inquire_daily_ccld.py 로 직접 실행할 때
    unittest.main(verbosity=2)
