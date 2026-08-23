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
        lines.append(f" » {flash}")
    return CLEAR + "\n".join(lines)


def table(header: list[str], rows: list[list[str]], width: int,
          maxw: int = 22) -> list[str]:
    """헤더 + 행을 폭에 맞춰 정렬한다. 컬럼 폭은 내용에서 계산한다.

    첫 컬럼은 이름(종목코드·타입명)이라 왼쪽 정렬, 나머지는 숫자라
    오른쪽 정렬한다. 자릿수가 맞아야 눈으로 크기 비교가 된다.

    문자열 정렬은 뷰의 일이다. 모델(집계기)은 값만 내놓는다."""
    if not header:
        return [" 데이터 없음"]
    w = [min(maxw, max([len(header[i])] + [len(r[i]) for r in rows]))
         for i in range(len(header))]

    def line(cells):
        out = []
        for i, c in enumerate(cells):
            c = c[:w[i]]
            out.append(c.ljust(w[i]) if i == 0 else c.rjust(w[i]))
        return " " + "  ".join(out)

    head = line(header)
    return [head, " " + "-" * min(width - 1, len(head) - 1)] + \
           [line(r)[:width] for r in rows]


def home(m, width: int) -> list[str]:
    """대시보드 — 지금 무엇이 얼마나 들어오고 있나.

    '잘 돌고 있나' 를 한 화면에서 판단할 수 있어야 한다:
      종류별 유입량 / 마지막 수신 경과 / 큐 적체 / 최근 흐름"""
    ib = m.inbox
    out = [
        f" 가동 {ib.elapsed:,.0f}초   총 {ib.total:,}건   "
        f"평균 {ib.total / ib.elapsed:.1f}건/초   "
        f"큐 {ib.qsize:,}   유실 {ib.dropped:,}   구독종목 {m.subscribed}",
        "",
    ]

    rows = m.rows()
    if rows:
        out += table(m.header(), rows, width)
    else:
        out += [" 아직 들어온 데이터가 없습니다."]

    recent = ib.recent_lines()
    if recent:
        out += ["", " 최근 수신"] + [" " + r for r in recent[:6]]

    panels = m.panels()
    if panels:
        out += ["", " 구독 화면  [v] 로 이동"] + panels
    else:
        out += ["", " 구독된 화면이 없습니다. ctx.feed.subscribe(...) 로 등록하세요."]

    out += ["", " [r] 수신로그   [o] 주문내역   [v] 구독화면   [s] 종목구독 (s 005930)"]
    return out


def realdata(m, width: int) -> list[str]:
    """수신 로그. 큐에 들어온 것을 시간순으로."""
    rows = m.rows()
    if not m.total:
        return ["", " 수신 대기 중..."]
    out = [" " + r for r in rows]
    out += ["", f" {m.page_label}" + (f"   필터:{m.only}" if m.only else "")]
    return out


def detail(m, width: int) -> list[str]:
    """한 종목만. 구독 중인 Board 패널에서 그 종목 행만 뽑는다."""
    if not m.code:
        return [" 종목이 지정되지 않았습니다.  사용법: d 005930"]
    rows = m.rows()
    if not rows:
        return ["", f" {m.code} 데이터가 없습니다.",
                " (Board 로 집계하는 구독이 있어야 보입니다)"]
    return table(m.header(), rows, width) + ["", f" {m.page_label}"]


def orders(m, width: int) -> list[str]:
    rows = m.rows()
    if m.loading and not m.total:
        return ["", " 조회 중..."]
    if m.error and not m.total:
        return ["", f" 조회 실패: {m.error}", " [u] 재시도"]

    head = (f" {'주문번호':<12}{'종목':<8}{'구분':<6}"
            f"{'수량':>8}{'단가':>10}{'상태':>10}")
    out = [head, " " + "-" * (len(head) - 2)]
    for o in rows:
        out.append(f" {o.order_no:<12}{o.code:<8}{o.side:<6}"
                   f"{o.qty:>8,}{o.price:>10,}{o.status:>10}")
    if not m.total:
        out.append(" 주문이 없습니다.")
    out += ["", _status(m)]
    return out


def feed(m, width: int) -> list[str]:
    """구독 화면. 상단은 숫자키 메뉴, 본문은 선택된 집계기의 표.

    집계기가 header()/rows() 만 내놓고 표 그리기는 여기서 한다 —
    데이터 종류가 늘어도 이 함수는 그대로다."""
    if not m.hub.panels:
        return [
            " 구독된 화면이 없습니다.",
            "",
            " ctx.feed.subscribe(Tick, kis_model.Board(cols=['price']), name='시세판')",
            " 처럼 등록하면 여기에 숫자키로 나타납니다.",
        ]
    body = table(m.header(), m.rows(), width)
    if not m.total:
        body = [" 수신 대기 중..."]
    return [" " + m.menu()[:width - 1], ""] + body + ["", " " + m.status()[:width - 1]]


def _status(m) -> str:
    if m.loading:
        return " 조회 중..."
    if m.error:
        return f" 조회 실패: {m.error}  [u] 재시도"
    if m.loaded_at:
        return f" 기준 {m.loaded_at:%H:%M:%S}"
    return ""