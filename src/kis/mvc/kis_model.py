"""
모델 — 데이터 출처별로 나눈다. 화면별이 아니다.

나누는 기준은 "누가 쓰느냐"다. 쓰는 스레드가 하나면 락이 필요 없고,
둘 이상이면 필요하다. 하나로 뭉쳐 두면 필요 없는 곳까지 락이 걸리거나
(느려짐), 필요한 곳에 안 걸린다(버그). 기존 MarketState가 후자였다.

    TickState          소켓 → 렌더 스레드 하나만 씀      → 락 없음
    SubscriptionState  이벤트 루프가 쓰고 렌더가 읽음    → 락 있음
    OrderState         조회 스레드가 쓰고 렌더가 읽음    → 락 있음
"""

from __future__ import annotations

import datetime
import threading
from collections import deque
from dataclasses import dataclass, field
from kis import kis_websocket

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
        # 파서가 내려주는 row는 dict다. 파서 교체 시 가장 자주 깨지는 지점.
        code = row.get("stock_code")
        price = row.get(price_key) if price_key else None

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


# ── ② 구독 상태 ────────────────────────────────────────────────
@dataclass(slots=True)
class SubRow:
    tr_id: str
    tr_key: str
    status: str                      # "대기 중" / "구독 완료" / "실패: ..."

    @property
    def label(self) -> str:
        return tr_label(self.tr_id)


class SubscriptionState:
    """
    이벤트 루프 스레드가 쓰고 렌더 스레드가 읽는다 → 락 필요.

    ⚠️ 기존 코드는 asyncio.Future를 그대로 담아 뷰가 .done()/.result()를
       호출했다. 다른 스레드에서 Future를 만지는 것이라 안전하지 않고,
       뷰가 asyncio를 알아야 하는 것도 이상하다. 여기서는 루프 쪽이
       평범한 문자열로 바꿔서 넣어준다.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._rows: dict[tuple[str, str], SubRow] = {}

    def mark(self, tr_id: str, tr_key: str, status: str) -> None:
        with self._lock:
            self._rows[(tr_id, tr_key)] = SubRow(tr_id, tr_key, status)

    def reset(self) -> None:
        """재연결 시 호출. 이전 세션 구독은 무효."""
        with self._lock:
            self._rows.clear()

    def snapshot(self) -> list[SubRow]:
        with self._lock:
            return list(self._rows.values())


# ── ③ 주문 내역 ────────────────────────────────────────────────
@dataclass(slots=True)
class OrderSnapshot:
    rows: list
    loading: bool
    error: str | None
    fetched_at: str | None


class OrderState:
    """
    조회 스레드가 쓰고 렌더 스레드가 읽는다 → 락 필요.

    여기서 API를 직접 부르지 않는다. 모델이 네트워크를 알면 테스트할 때마다
    통신이 필요해지고, 장 마감 후에는 돌릴 수가 없다. 부르는 쪽에서
    결과만 넣어준다.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._rows: list = []
        self._loading = False
        self._error: str | None = None
        self._fetched_at: datetime.datetime | None = None

    def begin_loading(self) -> None:
        with self._lock:
            self._loading = True
            self._error = None

    def set_rows(self, rows: list) -> None:
        with self._lock:
            self._rows = list(rows)
            self._loading = False
            self._error = None
            self._fetched_at = datetime.datetime.now()

    def set_error(self, message: str) -> None:
        with self._lock:
            self._loading = False
            self._error = message

    def snapshot(self) -> OrderSnapshot:
        with self._lock:
            return OrderSnapshot(
                rows=list(self._rows),
                loading=self._loading,
                error=self._error,
                fetched_at=(f"{self._fetched_at:%H:%M:%S}"
                            if self._fetched_at else None),
            )


# ── 묶음 ───────────────────────────────────────────────────────
@dataclass
class AppContext:
    """화면에 통째로 넘긴다. 각 화면은 자기가 쓸 것만 snapshot()한다."""
    ticks: TickState
    feed: kis_websocket.KisFeed
    orders: OrderState = field(default_factory=OrderState)