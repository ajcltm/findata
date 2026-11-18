# -*- coding: utf-8 -*-
# ---------------------------------------------------------------------------
# extract.py — v0 칼만 OFI 성능 분석 (UC-003 M1, 사이클 v1)
# 용도: DB에서 18종목의 2026-10-23 indicator 행(OFI_KF, BestQuoteFlow,
#       OFI_EMA(30s))과 tick 행을 뽑는다. 종목별 pkl 2개로 저장한다.
# 입력: argv[1] = DB 경로
#       argv[2] = pkl 출력 폴더 (미리 만든다. 18종목 약 400MB)
#       argv[3] = 종목 코드 쉼표 목록
# 원본 DB: D:/KIM/020_Project/260207.010_금융데이터/data/mock_data.db
#   - 읽기 전용(mode=ro)으로 연다. DB에 쓰지 않는다.
# 실행 순서: 1) extract.py → 2) analyze.py
# 실행 예:
#   python extract.py D:/KIM/020_Project/260207.010_금융데이터/data/mock_data.db <pkl폴더> 000270,000660,003550,005380,005490,005930,006400,012330,034730,035420,035720,051910,055550,066570,068270,086790,105560,316140
# 분석서: kalman_ofi_prev_analysis.md (같은 폴더)
# 이 머리 주석만 더했다. 계산 로직은 매니저 원본과 같다.
# ---------------------------------------------------------------------------
import sqlite3,sys,pandas as pd,time
db,out=sys.argv[1],sys.argv[2]; syms=sys.argv[3].split(',')
D0,D1='2026-10-23 00:00','2026-10-24 00:00'
c=sqlite3.connect(f"file:{db}?mode=ro",uri=True)
for s in syms:
    t=time.time()
    ind=pd.read_sql("select dt,label,line,value from indicator where symbol=? and label in ('OFI_KF','BestQuoteFlow','OFI_EMA(30s)') and dt>=? and dt<?",c,params=(s,D0,D1))
    tk=pd.read_sql("select kind,dt,price,volume,side,bid,ask,bid_size,ask_size from tick where symbol=? and dt>=? and dt<?",c,params=(s,D0,D1))
    ind.to_pickle(f"{out}/{s}_ind.pkl"); tk.to_pickle(f"{out}/{s}_tick.pkl")
    print(s,len(ind),len(tk),round(time.time()-t,1),flush=True)
