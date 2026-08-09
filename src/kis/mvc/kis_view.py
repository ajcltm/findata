"""뷰 — (model, width) → 문자열 리스트.

규칙 둘.
    1. print 하지 않는다. 출력은 런타임이 한 번에 한다.
    2. 조회하지 않는다. 여기서 네트워크를 타면 렌더 스레드가 멈춘다.
"""

from __future__ import annotations

from datetime import datetime

CLEAR = "\033[2J\033[H"


def frame(title: str, body: list[str], hint: str,
          flash: str | None, width: int) -> str:
    """화면 공통 테두리. 모든 화면이 같은 골격을 쓴다."""
    lines = [
        f"[{title}]".ljust(max(0, width - 20)) + datetime.now().strftime("%H:%M:%S"),
        "─" * width,
        *body,
        "─" * width,
        hint,
    ]
    if flash:
        lines.append(f"  » {flash}")
    return CLEAR + "\n".join(lines)


def home(m, width: int) -> list[str]:
    return [
        f"  구독 종목   {m.subscribed}",
        "",
        "  [r] 실시간 시세   [o] 주문 내역   [s] 종목 구독 (s 005930)",
    ]


def realdata(m, width: int) -> list[str]:
    rows = m.rows()
    # head = f"  {'종목':<8}{'현재가':>10}{'등락률':>9}{'거래량':>12}{'시각':>10}"
    # out = [head, "  " + "-" * (len(head) - 2)]
    # for t in rows:
    #     sign = "▲" if t.chg_rate > 0 else ("▼" if t.chg_rate < 0 else " ")
    #     out.append(f"  {t.code:<8}{t.price:>10,}"
    #                f"{sign + f'{abs(t.chg_rate):.2f}%':>9}"
    #                f"{t.volume:>12,}{t.ts:%H:%M:%S:>10}")
    # if not m.total:
    #     out.append("  수신된 시세가 없습니다.")
    # out += ["", f"  {m.page_label}   정렬:{m.sort}"
    #             + (f"   필터:{m.only}" if m.only else "")]
    return rows


def detail(m, width: int) -> list[str]:
    if not m.code:
        return ["  종목이 지정되지 않았습니다."]
    out = []
    if m.asks or m.bids:
        for p, q in reversed(m.asks):
            out.append(f"  {'':>12}{p:>10,} │ {q:>8,}")
        out.append("  " + "-" * 40)
        for p, q in m.bids:
            out.append(f"  {q:>8,} │ {p:>10,}")
    else:
        out.append("  호가 없음")
    out += ["", "  최근 체결"]
    for t in m.trades:
        out.append(f"    {t.ts:%H:%M:%S}  {t.price:>10,}  {t.qty:>7,}")
    return out


def orders(m, width: int) -> list[str]:
    rows = m.rows()
    if m.loading and not m.total:
        return ["", "  조회 중..."]
    if m.error and not m.total:
        return ["", f"  조회 실패: {m.error}", "  [u] 재시도"]
    head = (f"  {'주문번호':<12}{'종목':<8}{'구분':<6}"
            f"{'수량':>8}{'단가':>10}{'상태':>10}")
    out = [head, "  " + "-" * (len(head) - 2)]
    for o in rows:
        out.append(f"  {o.order_no:<12}{o.code:<8}{o.side:<6}"
                   f"{o.qty:>8,}{o.price:>10,}{o.status:>10}")
    if not m.total:
        out.append("  주문이 없습니다.")
    out += ["", _status(m)]
    return out


def _status(m) -> str:
    if m.loading:
        return "  조회 중..."
    if m.error:
        return f"  조회 실패: {m.error}   [u] 재시도"
    if m.loaded_at:
        return f"  기준 {m.loaded_at:%H:%M:%S}"
    return ""