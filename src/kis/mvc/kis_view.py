"""
뷰 — 화면은 자기가 쓸 상태만 읽는다.

도메인 상태(틱·구독·주문)는 공유하고, 화면 상태(스크롤·필터)는 화면이
소유한다. OrderScreen이 틱 통계를 스냅샷 뜰 이유는 없다.
"""

from __future__ import annotations

import datetime
import os
import shutil
import subprocess

W = 74
FOOTER = "  [h]home  [r]realdata  [o]orders  [q]quit"


def clear() -> None:
    subprocess.run("cls" if os.name == "nt" else "clear", shell=True)


def header(title: str) -> None:
    now = f"{datetime.datetime.now():%H:%M:%S}"
    print("=" * W)
    print(f"  {title}{' ' * max(1, W - len(title) - len(now) - 6)}{now}")
    print("=" * W)


def cell(text, width: int) -> str:
    """한글은 두 칸을 차지한다. 그냥 :<10을 쓰면 표가 어긋난다."""
    text = "-" if text is None else str(text)
    w = sum(2 if ord(c) > 0x2E80 else 1 for c in text)
    if w > width:
        out = ""
        acc = 0
        for c in text:
            cw = 2 if ord(c) > 0x2E80 else 1
            if acc + cw > width - 1:
                return out + "…"
            out += c
            acc += cw
    return text + " " * (width - w)


# ── HOME ───────────────────────────────────────────────────────
class HomeScreen:
    name = "home"

    def render(self, ctx) -> None:
        subs = ctx.feed.subscription_status()          # 구독 + 틱만
        t = ctx.ticks.snapshot()

        clear()
        header("HOME")
        print(f"  엔진 시작: {t.start_time}   경과: {t.elapsed}   "
              f"수신: {t.total_msgs:,}건")
        if t.dropped:
            print(f"  ⚠️  폐기 {t.dropped}건 — 파싱 실패 로그 확인 필요")
        print("-" * W)
        print(f"  {cell('구분', 12)}{cell('종목코드', 12)}상태")
        print("  " + "-" * (W - 4))

        if not subs:
            print("  구독 정보 없음 (연결 대기 중)")
        for row in subs:
            print(f"  {cell(row[0], 12)}{cell(row[1], 12)}{row[2]}")

        print("=" * W)
        print(FOOTER)


# ── REALDATA ───────────────────────────────────────────────────
class RealDataScreen:
    name = "realdata"

    def render(self, ctx) -> None:
        s = ctx.ticks.snapshot()           # 틱만. 주문은 안 읽는다.

        clear()
        header("REALDATA")
        print(f"  수신 {s.total_msgs:,}건   폐기 {s.dropped}건   "
              f"대기열 {s.qsize}")
        print(f"  최근 {s.last_time}  {s.last_kind or '-'}  "
              f"{s.last_code or '-'}  price={s.last_price or '-'}")
        print("-" * W)
        if not s.recent_lines:
            print("  수신 대기 중...")
        for line in s.recent_lines:
            print("  " + line)
        print("-" * W)
        print(FOOTER)


# ── ORDERS ─────────────────────────────────────────────────────
class OrderScreen:
    name = "orders"

    # 화면 상태는 화면이 소유한다. 다른 화면과 나눌 이유가 없다.
    def __init__(self):
        self.scroll = 0
        self.page_size = max(5, (shutil.get_terminal_size().lines or 24) - 12)

    def render(self, ctx) -> None:
        s = ctx.orders.snapshot()          # 주문만

        clear()
        header("ORDERS")

        if s.loading:
            print("  조회 중...")          # 블로킹 없이 즉시 이 화면이 뜬다
        elif s.error:
            print(f"  ❌ 조회 실패: {s.error}")
            print("  [o]를 눌러 다시 시도하세요.")
        else:
            self._table(s)

        print("-" * W)
        print("  [h]home  [r]realdata  [o]새로고침  [q]quit")

    def _table(self, s) -> None:
        print(f"  {cell('종목코드', 10)}{cell('구분', 8)}{cell('주문수량', 10)}"
              f"{cell('체결수량', 10)}{cell('주문단가', 12)}시간")
        print("-" * W)

        if not s.rows:
            print("  주문 내역이 없습니다.")
            return

        total = len(s.rows)
        self.scroll = max(0, min(self.scroll, max(0, total - self.page_size)))
        window = s.rows[self.scroll:self.scroll + self.page_size]

        for o in window:
            print(f"  {cell(o.get('pdno'), 10)}"
                  f"{cell(o.get('sll_buy_dvsn_cd_name'), 8)}"
                  f"{cell(o.get('ord_qty'), 10)}"
                  f"{cell(o.get('tot_ccld_qty'), 10)}"
                  f"{cell(o.get('ord_unpr'), 12)}"
                  f"{o.get('ord_tmd', '-')}")

        shown = f"{self.scroll + 1}-{self.scroll + len(window)}/{total}"
        stamp = f"  조회 {s.fetched_at}" if s.fetched_at else ""
        print(f"\n  {shown}{stamp}")