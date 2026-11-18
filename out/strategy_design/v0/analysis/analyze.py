# -*- coding: utf-8 -*-
# ---------------------------------------------------------------------------
# analyze.py — v0 칼만 OFI 성능 분석 (UC-003 M1, 사이클 v1)
# 용도: extract.py가 만든 pkl을 읽는다. 순위 IC 표(ic.csv)와
#       잡음·진단 표(diag.csv)를 계산한다.
#       범위: 2026-10-23 09:05~15:20, 1초 격자, 보유 기간 10/30/60/300초.
# 입력: argv[1] = extract.py의 pkl 출력 폴더
#       결과 ic.csv, diag.csv를 같은 폴더에 쓴다.
# 원본 DB: D:/KIM/020_Project/260207.010_금융데이터/data/mock_data.db
#   - 이 스크립트는 DB를 열지 않는다. extract.py가 읽기 전용으로 연다.
# 실행 순서: 1) extract.py → 2) analyze.py
# 실행 예: python analyze.py <pkl폴더>
# 분석서: kalman_ofi_prev_analysis.md (같은 폴더)
# 이 머리 주석만 더했다. 계산 로직은 매니저 원본과 같다.
# ---------------------------------------------------------------------------
import sys, glob, os, numpy as np, pandas as pd
D = sys.argv[1]
T0, T1 = '2026-10-23 09:05:00', '2026-10-23 15:20:00'
HS = [10, 30, 60, 300]
rows, diag = [], []

def ic(x, y):
    m = x.notna() & y.notna()
    if m.sum() < 200: return np.nan
    return x[m].rank().corr(y[m].rank())

for f in sorted(glob.glob(f'{D}/*_ind.pkl')):
    s = os.path.basename(f)[:6]
    ind = pd.read_pickle(f); tk = pd.read_pickle(f'{D}/{s}_tick.pkl')
    ind['dt'] = pd.to_datetime(ind.dt); tk['dt'] = pd.to_datetime(tk.dt)
    ind = ind[(ind.dt >= T0) & (ind.dt <= T1)]; tk = tk[(tk.dt >= T0) & (tk.dt <= T1)]
    # raw event-level ofi_norm (order kept by rowid order)
    ev = ind[(ind.label == 'BestQuoteFlow') & (ind.line == 'ofi_norm')].value.astype(float).reset_index(drop=True)
    dup = ind[(ind.label == 'BestQuoteFlow') & (ind.line == 'is_dup')].value.astype(float)
    ac1 = ev.autocorr(1); ac5 = ev.autocorr(5)
    # 1s grid
    grid = pd.date_range(T0, T1, freq='1s')
    def last(label, line):
        x = ind[(ind.label == label) & (ind.line == line)].groupby('dt').value.last()
        return x.reindex(grid).ffill()
    sumofi = ind[(ind.label == 'BestQuoteFlow') & (ind.line == 'ofi_norm')].groupby('dt').value.sum().reindex(grid).fillna(0)
    mid = ((tk.bid + tk.ask) / 2).groupby(tk.dt).last().reindex(grid).ffill()
    lvl, slp, lead, ema = last('OFI_KF', 'level'), last('OFI_KF', 'slope'), last('OFI_KF', 'lead'), last('OFI_EMA(30s)', 'value')
    nis, qh, wf, rv = last('OFI_KF', 'nis'), last('OFI_KF', 'q_hat'), last('OFI_KF', 'world_frac'), last('OFI_KF', 'obs_var')
    lr = np.log(mid)
    cum60 = sumofi.rolling(60).sum(); cum300 = sumofi.rolling(300).sum()
    mom60 = lr - lr.shift(60)
    sig = {'kf_level': lvl, 'kf_slope': slp, 'kf_lead': lead, 'ofi_ema30': ema,
           'ofi_sum60': cum60, 'ofi_sum300': cum300, 'mom60': mom60}
    for h in HS:
        fut = lr.shift(-h) - lr
        for k, v in sig.items():
            rows.append((s, h, k, ic(v, fut)))
    past30 = lr - lr.shift(30)
    # noise: sign flips per minute, and smoothness
    def flips(x):
        sg = np.sign(x.dropna()); return (sg.diff().abs() > 0).sum() / (len(sg) / 60)
    diag.append(dict(sym=s, n_ev=len(ev), dup=dup.mean(), ac1=ac1, ac5=ac5,
        flips_lvl=flips(lvl), flips_slope=flips(slp), flips_ema=flips(ema),
        nis_med=nis.median(), nis_mean=nis.mean(), wf_med=wf.median(),
        snr_lvl=(lvl.std() / lvl.diff().std()), snr_ema=(ema.std() / ema.diff().std()),
        corr_lvl_ema=lvl.corr(ema), ic_lvl_past30=ic(lvl, past30),
        mid_chg_ticks=(mid.diff() != 0).mean()))

R = pd.DataFrame(rows, columns=['sym', 'h', 'sig', 'ic'])
pd.set_option('display.width', 200)
print('== mean rank IC over symbols (future log-mid return) ==')
print(R.groupby(['sig', 'h']).ic.mean().unstack().round(3))
print('== share of symbols with IC>0 ==')
print(R.assign(p=R.ic > 0).groupby(['sig', 'h']).p.mean().unstack().round(2))
Dg = pd.DataFrame(diag).set_index('sym')
print('== diagnostics per symbol ==')
print(Dg.round(3).to_string())
print('== medians ==')
print(Dg.median().round(3))
R.to_csv(f'{D}/ic.csv', index=False); Dg.to_csv(f'{D}/diag.csv')
