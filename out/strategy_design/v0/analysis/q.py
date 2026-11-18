# -*- coding: utf-8 -*-
# ---------------------------------------------------------------------------
# q.py — DB 조회 도구 (v0 칼만 OFI 성능 분석, UC-003 M1, 사이클 v1)
# 용도: SQL 문을 실행하고 결과와 걸린 시간을 출력한다.
# 입력: argv[1] = DB 경로, argv[2:] = SQL 문 (여러 개 가능)
# 원본 DB: D:/KIM/020_Project/260207.010_금융데이터/data/mock_data.db
#   - 읽기 전용(mode=ro)으로 연다. DB에 쓰지 않는다.
# 분석 실행 순서는 extract.py → analyze.py다. 이 도구는 선택으로 쓴다.
# 실행 예:
#   python q.py <DB> "select max(dt) from indicator where label='OFI_KF'"
# 이 머리 주석만 더했다. 로직은 매니저 원본과 같다.
# ---------------------------------------------------------------------------
import sqlite3,sys,time
c=sqlite3.connect(f"file:{sys.argv[1]}?mode=ro",uri=True)
for q in sys.argv[2:]:
    t=time.time()
    for r in c.execute(q): print(r)
    print('--',round(time.time()-t,1),'s')
