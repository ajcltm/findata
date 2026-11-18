# -*- coding: utf-8 -*-
# ---------------------------------------------------------------------------
# labels.py — DB 조회 도구 (v0 칼만 OFI 성능 분석, UC-003 M1, 사이클 v1)
# 용도: 한 종목의 indicator 라벨 목록과 라벨별 dt 최대·최소를 출력한다.
#       OFI_KF 데이터가 있는 마지막 날(2026-10-23)을 찾을 때 썼다.
# 입력: argv[1] = DB 경로, argv[2] = 종목 코드
# 원본 DB: D:/KIM/020_Project/260207.010_금융데이터/data/mock_data.db
#   - 읽기 전용(mode=ro)으로 연다. DB에 쓰지 않는다.
# 분석 실행 순서는 extract.py → analyze.py다. 이 도구는 선택으로 쓴다.
# 실행 예: python labels.py <DB> 005930
# 이 머리 주석만 더했다. 로직은 매니저 원본과 같다.
# ---------------------------------------------------------------------------
import sqlite3,sys
c=sqlite3.connect(f"file:{sys.argv[1]}?mode=ro",uri=True)
sym=sys.argv[2]; last=''
while True:
    r=c.execute("select label from indicator where symbol=? and label>? order by label limit 1",(sym,last)).fetchone()
    if not r: break
    last=r[0]
    mx=c.execute("select max(dt),min(dt) from indicator where symbol=? and label=?",(sym,last)).fetchone()
    print(last,mx)
