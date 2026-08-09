"""모델 — 데이터 전부. 화면 하나당 모델 하나, 그리고 공유 ctx.

화면 모델은 ctx를 품는다. 그래서 컨트롤러가 뷰에 넘기는 건 언제나 이거
하나뿐이고, 뷰는 ctx를 따로 받을 필요가 없다.
"""

from __future__ import annotations

import threading
import datetime

from collections import deque
from dataclasses import dataclass, field
"""
모델 — 데이터 출처별로 나눈다. 화면별이 아니다.
 
나누는 기준은 "누가 쓰느냐"다. 쓰는 스레드가 하나면 락이 필요 없고,
둘 이상이면 필요하다. 하나로 뭉쳐 두면 필요 없는 곳까지 락이 걸리거나
(느려짐), 필요한 곳에 안 걸린다(버그). 기존 MarketState가 후자였다.
 
    TickState          소켓 → 렌더 스레드 하나만 씀      → 락 없음
    SubscriptionState  이벤트 루프가 쓰고 렌더가 읽음    → 락 있음
    OrderState         조회 스레드가 쓰고 렌더가 읽음    → 락 있음
"""
 
# tr_id별 표시 이름과 "대표 가격" 필드. 새 TR은 여기만 추가하면 된다.
TR_META: dict[str, tuple[str, str | None]] = {
    "H0STCNT0": ("시세", "current_price"),
    "H0STASP0": ("호가", "ask_price_1"),
    "H0STCNI0": ("체결통보", "executed_price"),
}
 
 
def tr_label(tr_id: str) -> str:
    return TR_META.get(tr_id, (tr_id, None))[0]
 
 
# ── ① 틱 통계 ──────────────────────────────────────────────────
@dataclass(slots=True)
class TickSnapshot:
    total_msgs: int
    dropped: int
    last_time: str
    last_tr_id: str | None
    last_kind: str | None
    last_code: str | None
    last_price: str | None
    recent_lines: list[str]
    qsize: int
    start_time: str
    elapsed: str
 
 
class TickState:
    """
    큐는 흐름이고 화면은 순간이므로, 여기서 흐름을 순간으로 접는다.
 
    쓰는 스레드가 둘이라 락이 필요하다.
        render 스레드 : on_parsed()  — total_msgs, last_*, recent_lines
        parser 스레드 : on_dropped() — dropped
    처음에는 "렌더 혼자"로 보고 락을 뺐는데, 파서가 폐기 건수를 올리는
    순간 전제가 깨졌다. 스레드 하나가 더 손대면 그때부터 락이 필요하다.
 
    락은 짧게만 잡는다. 화면 그리기(느림)는 snapshot()이 복사본을 만들어
    반환한 뒤, 락 밖에서 한다.
    """
 
    def __init__(self, view_q):
        self._lock = threading.Lock()
        self.view_q = view_q
        self.total_msgs = 0
        self.dropped = 0                 # 파싱 실패 등으로 버린 건수
        self.last_time: datetime.datetime | None = None
        self.last_tr_id: str | None = None
        self.last_code: str | None = None
        self.last_price: str | None = None
        self.recent_lines: deque[str] = deque(maxlen=10)
        self.start_time = datetime.datetime.now()
 
    def on_parsed(self, parsed) -> None:
        """ParsedTick 1건 반영. 화면은 최신 1건만 보여주므로 마지막만 남긴다."""
        if not parsed.data:
            return
        row = parsed.data[-1]
        _, price_key = TR_META.get(parsed.tr_id, (parsed.tr_id, None))
 
        now = datetime.datetime.now()
        # dataclass는 .get()이 없다. 파서 교체 시 가장 자주 깨지는 지점.
        code = getattr(row, "stock_code", None)
        price = getattr(row, price_key, None) if price_key else None
 
        with self._lock:
            self._apply(now, parsed.tr_id, code, price)
 
    def _apply(self, now, tr_id, code, price) -> None:
        self.total_msgs += 1
        self.last_time = now
        self.last_tr_id = tr_id
        self.last_code = code
        self.last_price = price
        self.recent_lines.append(
            f"{now:%H:%M:%S} {tr_label(tr_id):<6} {code} price={price}")
 
    def on_dropped(self) -> None:
        """파서 스레드가 부른다. += 는 읽기+쓰기 2단계라 락 없이는 유실된다."""
        with self._lock:
            self.dropped += 1
 
    def snapshot(self) -> TickSnapshot:
        elapsed = datetime.datetime.now() - self.start_time
        with self._lock:
            return self._snapshot(elapsed)
 
    def _snapshot(self, elapsed) -> TickSnapshot:
        return TickSnapshot(
            total_msgs=self.total_msgs,
            dropped=self.dropped,
            last_time=f"{self.last_time:%H:%M:%S}" if self.last_time else "-",
            last_tr_id=self.last_tr_id,
            last_kind=tr_label(self.last_tr_id) if self.last_tr_id else None,
            last_code=self.last_code,
            last_price=self.last_price,
            recent_lines=list(self.recent_lines),
            qsize=self.view_q.qsize(),
            start_time=f"{self.start_time:%H:%M:%S}",
            elapsed=str(elapsed).split(".")[0],
        )


# ── 공유 ───────────────────────────────────────────────────────
class AppCtx:
    """전 화면이 공유하는 데이터."""

    def __init__(self, tickstate, ws):
        self.ticks = tickstate              # TickStore (기존 프로젝트 것)
        self.ws = ws            # 웹소켓 엔진 (구독 등)
        self._flash: str | None = None
        self._lock = threading.Lock()

    def flash(self, msg: str) -> None:
        """한 줄 알림. 다음 프레임에 한 번 표시되고 사라진다."""
        with self._lock:
            self._flash = msg

    def take_flash(self) -> str | None:
        with self._lock:
            msg, self._flash = self._flash, None
            return msg


# ── 화면 모델 ──────────────────────────────────────────────────
class ScreenModel:
    def __init__(self, ctx: AppCtx):
        self.ctx = ctx


class Paged(ScreenModel):
    """페이지 단위 스크롤. 화면 밖으로 벗어나지 않게 스스로 조인다."""

    page_size = 15

    def __init__(self, ctx):
        super().__init__(ctx)
        self.scroll = 0
        self.total = 0

    def down(self):
        self.scroll = min(self.scroll + self.page_size,
                          max(0, self.total - self.page_size))

    def up(self):
        self.scroll = max(0, self.scroll - self.page_size)

    def top(self):
        self.scroll = 0

    def page(self, rows: list) -> list:
        self.total = len(rows)
        self.scroll = min(self.scroll, max(0, self.total - self.page_size))
        return rows[self.scroll:self.scroll + self.page_size]

    @property
    def page_label(self) -> str:
        if not self.total:
            return "0 / 0"
        end = min(self.scroll + self.page_size, self.total)
        return f"{self.scroll + 1}-{end} / {self.total}"


class Home(ScreenModel):

    @property
    def subscribed(self) -> int:
        return self.ctx.ws.subscription_status()


class RealData(Paged):
    SORTS = ("code", "chg", "vol")

    def __init__(self, ctx):
        super().__init__(ctx)
        self.sort = "code"
        self.only: str | None = None       # 종목코드 prefix 필터

    def next_sort(self):
        i = self.SORTS.index(self.sort)
        self.sort = self.SORTS[(i + 1) % len(self.SORTS)]

    def rows(self) -> list:
        """필터·정렬·페이지까지 끝난 행. 뷰는 이걸 그대로 찍는다."""
        items = list(self.ctx.ticks.snapshot().recent_lines)
        return self.page(items)
        # if self.only:
        #     items = [t for t in items if t.last_code.startswith(self.only)]
        # key = {"code": lambda t: t.last_code,
        #        "chg": lambda t: -t.last_price,  # 음수로 해야 내림차순
        #        "vol": lambda t: -t.last_time}[self.sort]
        # return self.page(sorted(items, key=key))


class Detail(ScreenModel):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.code: str | None = None

    @property
    def asks(self) -> list:
        book = self.ctx.ticks.book(self.code) if self.code else None
        return list(book.asks[:5]) if book else []

    @property
    def bids(self) -> list:
        book = self.ctx.ticks.book(self.code) if self.code else None
        return list(book.bids[:5]) if book else []

    @property
    def trades(self) -> list:
        return list(self.ctx.ticks.recent(self.code, 10)) if self.code else []


class Orders(Paged):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.orders: list = []
        self.account: str | None = None
        self.loading = False
        self.error: str | None = None
        self.loaded_at: datetime | None = None

    def rows(self) -> list:
        return self.page(self.orders)

    # 조회 상태 -----------------------------------------------
    def begin(self):
        self.loading, self.error = True, None

    def done(self, orders: list):
        self.loading = False
        self.loaded_at = datetime.now()
        self.orders = orders
        self.top()

    def fail(self, msg: str):
        self.loading, self.error = False, msg