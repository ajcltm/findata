"""T-001: SimBroker 체결 로그와 시세 DB를 대조해 이상 체결을 찾는다.

- 대상 코드를 실행하지 않는다. 로그와 DB만 읽는다.
- DB는 읽기 전용(mode=ro)으로 연다.
- 사용: py -3.12 find_outlier_fills.py <프로젝트 루트> [출력 csv]
"""
import csv
import glob
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta

ROOT = sys.argv[1] if len(sys.argv) > 1 else "."
OUT = sys.argv[2] if len(sys.argv) > 2 else None
LOGS = sorted(glob.glob(os.path.join(ROOT, "logs", "alpha_sim_*.log")))
DBS = [os.path.join(ROOT, "data", n) for n in
       ("kis_data_260823.db", "kis_data_260831.db", "kis_data.db")]

RE_TS = r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),(\d{3})"
RE_ORDER = re.compile(RE_TS + r".*\[SIM\] 주문 (\S+) (buy|sell) (\d+)주 @(\S+)")
RE_FILL = re.compile(RE_TS + r".*\[SIM\] 체결 (\S+) (buy|sell) (\d+)주 @([\d,]+)")
RE_MODE = re.compile(r"simul_mode=(True|False)")


def ts(d, ms):
    return datetime.strptime(d, "%Y-%m-%d %H:%M:%S") + timedelta(milliseconds=int(ms))


def parse_logs():
    rows = []
    for path in LOGS:
        mode = None
        pending = {}                     # (symbol, side) -> 주문 정보 (FIFO 1건)
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if mode is None:
                    m = RE_MODE.search(line)
                    if m:
                        mode = m.group(1)
                if "[SIM]" not in line:
                    continue
                m = RE_ORDER.match(line)
                if m:
                    d, ms, sym, side, qty, px = m.groups()
                    pending[(sym, side)] = dict(order_dt=ts(d, ms),
                                                order_px=px.replace(",", ""))
                    continue
                m = RE_FILL.match(line)
                if m:
                    d, ms, sym, side, qty, px = m.groups()
                    o = pending.pop((sym, side), {})
                    rows.append(dict(log=os.path.basename(path), simul=mode,
                                     fill_dt=ts(d, ms), symbol=sym, side=side,
                                     qty=int(qty), fill_px=int(px.replace(",", "")),
                                     order_dt=o.get("order_dt"),
                                     order_px=o.get("order_px", "?")))
    return rows


_conns = {}


def conn(db):
    if db not in _conns:
        _conns[db] = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    return _conns[db]


def market_at(sym, when):
    """when(로그 벽시계) 직전 체결과 1호가. 120초 창에서 찾는다."""
    bd = when.strftime("%Y%m%d")
    lo = (when - timedelta(seconds=120)).strftime("%H%M%S")
    hi = (when + timedelta(seconds=2)).strftime("%H%M%S")
    best = None
    for db in DBS:
        if not os.path.exists(db):
            continue
        q = ("select execution_time, current_price, ask_price_1, bid_price_1, "
             "datetime from execution where stock_code=? and execution_time "
             "between ? and ? and business_date=? order by execution_time")
        rs = conn(db).execute(q, (sym, lo, hi, bd)).fetchall()
        rs = [r for r in rs if r[4] and r[4] <= when.isoformat()]
        if rs:
            prices = [float(r[1]) for r in rs]
            last = rs[-1]
            best = dict(db=os.path.basename(db), mkt_time=last[0],
                        last_px=float(last[1]), ask1=float(last[2] or 0),
                        bid1=float(last[3] or 0),
                        win_min=min(prices), win_max=max(prices), n=len(rs))
    return best


def main():
    rows = parse_logs()
    out = []
    for r in rows:
        mk = market_at(r["symbol"], r["fill_dt"]) if r["simul"] == "False" else None
        dev = None
        if mk:
            dev = (r["fill_px"] - mk["last_px"]) / mk["last_px"] * 100
        out.append({**r, **(mk or {}), "dev_pct": None if dev is None else round(dev, 3)})

    cols = ["log", "simul", "order_dt", "order_px", "fill_dt", "symbol", "side",
            "qty", "fill_px", "db", "mkt_time", "last_px", "ask1", "bid1",
            "win_min", "win_max", "n", "dev_pct"]
    if OUT:
        with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(out)

    print(f"체결 {len(out)}건, 로그 {len(LOGS)}개")
    nreal = [o for o in out if o["simul"] == "False"]
    matched = [o for o in nreal if o["dev_pct"] is not None]
    print(f"실시세 모의 {len(nreal)}건, DB 대조 {len(matched)}건")
    print("|dev| 상위 15건:")
    for o in sorted(matched, key=lambda o: -abs(o["dev_pct"]))[:15]:
        print(o["fill_dt"], o["symbol"], o["side"], o["order_px"], o["fill_px"],
              o["last_px"], o["ask1"], o["bid1"], o["dev_pct"])
    print("DB 대조 실패:")
    for o in nreal:
        if o["dev_pct"] is None:
            print(" ", o["log"], o["fill_dt"], o["symbol"], o["fill_px"])
    print("지정가 체결 (주문가가 시장가가 아님):")
    for o in out:
        if o["order_px"] not in ("시장가", "?"):
            print(" ", o["log"], o["simul"], o["order_dt"], o["symbol"], o["side"],
                  o["order_px"], "->", o["fill_px"], "last", o.get("last_px"))


if __name__ == "__main__":
    main()
