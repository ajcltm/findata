# -*- coding: utf-8 -*-
# ---------------------------------------------------------------------------
# tables.py — DB 조회 도구 (v0 칼만 OFI 성능 분석, UC-003 M1, 사이클 v1)
# 용도: DB의 테이블별 행 수와 열 이름(앞 15개)을 출력한다.
#       count(*)를 쓰므로 큰 테이블에서 오래 걸린다.
# 입력: argv[1] = DB 경로
# 원본 DB: D:/KIM/020_Project/260207.010_금융데이터/data/mock_data.db
#   - 읽기 전용(mode=ro)으로 연다. DB에 쓰지 않는다.
# 분석 실행 순서는 extract.py → analyze.py다. 이 도구는 선택으로 쓴다.
# 실행 예: python tables.py <DB>
# 이 머리 주석만 더했다. 로직은 매니저 원본과 같다.
# ---------------------------------------------------------------------------
import sqlite3,sys
p=sys.argv[1]
c=sqlite3.connect(f"file:{p}?mode=ro",uri=True)
for (n,) in c.execute("select name from sqlite_master where type='table'"):
    cols=[r[1] for r in c.execute(f'pragma table_info("{n}")')]
    cnt=c.execute(f'select count(*) from "{n}"').fetchone()[0]
    print(n,cnt,cols[:15])
