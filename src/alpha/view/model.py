"""모델 — 데이터 전부. 화면 하나당 모델 하나, 그리고 공유 ctx.

화면 모델은 ctx를 품는다. 그래서 컨트롤러가 뷰에 넘기는 건 언제나 이거
하나뿐이고, 뷰는 ctx를 따로 받을 필요가 없다.

■ 특정 데이터 타입을 모른다
    TickState 처럼 'KIS 시세' 를 전제한 모델을 두지 않는다. 큐에 무엇이
    들어올지 미리 알 수 없기 때문이다 — 소켓 틱, 지표 스냅샷, 체결,
    앞으로 만들 무엇이든.

    그래서 두 가지만 둔다:
        Inbox    타입별로 몇 건 들어왔나 (홈 대시보드)
        FeedHub  타입별로 구독한 집계기에 배달 (구독 화면)
    새 데이터를 붙여도 이 파일은 안 고친다.

■ 스레드
    Inbox 와 FeedHub 는 렌더 스레드 하나만 쓴다 → 락 없음.
    다른 스레드가 손대기 시작하면 그때 락이 필요하다.
    조회 결과는 app.submit 이 렌더 스레드로 넘겨주므로 여기서도 락이 없다.
"""

from __future__ import annotations

import datetime
import threading
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field, fields, is_dataclass
from typing import Any, Callable, Optional, Type


# ══════════════════════════════════════════════════════════════════
# ① Inbox — 큐로 무엇이 얼마나 들어오는지
# ══════════════════════════════════════════════════════════════════

class Inbox:
    """큐에 들어온 객체의 종류별 통계. 홈 화면의 재료다.

    ■ 왜 타입 이름으로 세나
        무엇이 들어올지 미리 알 수 없다. 타입 이름을 키로 세기만 하면
        새 데이터를 붙여도 이 클래스는 그대로다.

    ■ 무엇을 보여주나
        · 지금 무엇이 들어오고 있나 (종류·누적·초당)
        · 마지막으로 들어온 게 언제인가 (끊겼는지 판별)
        · 큐가 밀리고 있나 (qsize)
        이 셋이면 "잘 돌고 있나" 를 판단할 수 있다.
    """

    def __init__(self, view_q=None, recent: int = 12):
        self.view_q = view_q
        self.total = 0
        self.dropped = 0
        self.counts: Counter = Counter()            # 타입 이름 -> 누적 건수
        self.first_at: dict[str, float] = {}
        self.last_at: dict[str, float] = {}
        self.recent: deque = deque(maxlen=recent)   # (시각, 타입, 요약)
        self.start = time.time()

    def on_object(self, obj) -> None:
        """렌더 스레드에서만 부른다."""
        name = type(obj).__name__
        now = time.time()
        self.total += 1
        self.counts[name] += 1
        self.first_at.setdefault(name, now)
        self.last_at[name] = now
        self.recent.append((now, name, self._summary(obj)))

    def on_dropped(self, n: int = 1) -> None:
        """큐가 차서 버린 건수. 넣는 쪽이 알려주면 화면에 뜬다."""
        self.dropped += n

    @staticmethod
    def _summary(obj) -> str:
        """한 줄 요약. 필드 이름을 추측하되 없으면 그냥 넘어간다.

        새 타입이 와도 최소한 타입 이름은 보이므로 화면이 비지 않는다."""
        r = to_row(obj)
        key = next((r[k] for k in ("symbol", "stock_code", "code", "label")
                    if r.get(k) not in (None, "")), "")
        val = next((r[k] for k in ("price", "current_price", "close",
                                   "value", "executed_price", "size")
                    if r.get(k) is not None), "")
        return f"{key} {cell(val)}".strip()

    @property
    def elapsed(self) -> float:
        return max(time.time() - self.start, 1e-9)

    @property
    def qsize(self) -> int:
        try:
            return self.view_q.qsize() if self.view_q else 0
        except Exception:
            return 0

    def rows(self) -> list[list[str]]:
        """타입별 대시보드 행. 표 그리기는 뷰가 한다."""
        now = time.time()
        out = []
        for name, n in self.counts.most_common():
            span = max(now - self.first_at.get(name, now), 1e-9)
            age = now - self.last_at.get(name, now)
            out.append([name, f"{n:,}", f"{n / span:.1f}", f"{age:.0f}s"])
        return out

    def recent_lines(self) -> list[str]:
        """최근에 들어온 것들. 무엇이 흐르는지 눈으로 확인."""
        return [f"{datetime.datetime.fromtimestamp(t):%H:%M:%S}  "
                f"{name:<22}{summary}"
                for t, name, summary in reversed(self.recent)]


# ══════════════════════════════════════════════════════════════════
# ② 구독형 집계 — 모델을 안 만들어도 화면이 나오게 하는 부분
# ══════════════════════════════════════════════════════════════════
#
# ■ 전체 흐름 (이것만 알면 아래 코드가 다 읽힌다)
#
#     show_q.put(Tick(...))            ← 아무 데서나 큐에 넣는다
#            ↓
#     Runtime._drain()                 ← 렌더 스레드가 꺼낸다
#            ↓
#     FeedHub.on_object(tick)          ← 타입을 보고 나눠준다
#            ↓
#     Panel.offer(tick)                ← 필터를 통과하면
#            ↓
#     Aggregator.add(tick)             ← 집계기가 모은다
#            ↓
#     Aggregator.header() / rows()     ← 화면 그릴 때 꺼낸다
#            ↓
#     kis_view.table(...)              ← 표로 그린다
#
# ■ 왜 이렇게 나눴나
#     화면 하나를 늘리려면 원래 모델·뷰·컨트롤러 셋을 만들어야 했다.
#     그런데 모델이 하는 일은 사실 두 가지가 섞여 있다.
#
#         (a) 데이터를 어떻게 모을지    ← 데이터 종류마다 다르다
#         (b) 스크롤·선택 같은 화면 상태 ← 어떤 화면이든 똑같다
#
#     (a)만 Aggregator 로 떼어내면 (b)는 Feed 모델 하나로 돌려쓸 수 있다.
#     그래서 화면 추가가 subscribe() 한 줄이 된다.


# ── 아무 객체나 dict 으로 바꾸는 도구 ──────────────────────────

# 타입마다 필드 이름은 고정이므로 한 번 구해서 여기 저장해 둔다.
#   {Tick: ("dt", "symbol", "price", "volume", "side"), ...}
_FIELDS: dict[type, tuple[str, ...]] = {}


def to_row(obj) -> dict:
    """dataclass 객체를 dict 으로 바꾼다.

    예)
        obj  = Tick(dt=..., symbol="005930", price=70000.0, volume=10.0)
        결과 = {"dt": ..., "symbol": "005930", "price": 70000.0, "volume": 10.0}

    ■ 이게 왜 중요한가
        집계기가 Tick 이 뭔지 몰라도 표를 그릴 수 있게 해주는 장치다.
        필드 이름이 곧 표의 컬럼 이름이 되므로, 새 데이터 타입을 만들어도
        집계기 코드를 안 고친다.

    ■ dataclasses.asdict() 를 왜 안 쓰나
        asdict 은 안쪽 객체까지 재귀적으로 복사(deepcopy)해서 느리다.
        5만 건 기준 150ms vs 39ms. 화면은 초당 수천 건을 받으므로
        필드 이름만 알고 getattr 로 읽는 쪽이 낫다.
    """
    # 이미 dict 이면 그대로 쓴다
    if isinstance(obj, dict):
        return obj

    obj_type = type(obj)
    field_names = _FIELDS.get(obj_type)

    # 이 타입을 처음 본다면 필드 이름을 구해서 저장해 둔다
    if field_names is None:
        if not is_dataclass(obj):
            # dataclass 가 아니면 표로 만들 수 없다.
            # 화면이 죽는 것보다는 뭐라도 보여주는 게 낫다.
            return {"repr": repr(obj)}
        field_names = tuple(f.name for f in fields(obj))
        _FIELDS[obj_type] = field_names

    return {name: getattr(obj, name, None) for name in field_names}


def cell(value, max_len: int = 100) -> str:
    """값 하나를 화면에 쓸 문자열로 바꾼다.

    예)
        datetime(2024,1,15,9,30,15) → "09:30:15"
        70000.0                     → "70,000"
        12.5                        → "12.50"
        None                        → "-"
        "아주긴문자열입니다다다다다" → "아주긴문자열입니다다다…"
    """
    if value is None:
        return "-"
    if isinstance(value, datetime.datetime):
        return value.strftime("%H:%M:%S")
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, float):
        # 작은 수는 소수점까지, 큰 수는 천단위 콤마만
        return f"{value:,.2f}" if abs(value) < 1000 else f"{value:,.0f}"
    if isinstance(value, int):
        return f"{value:,}"

    text = str(value)
    if len(text) <= max_len:
        return text
    return text[:max_len - 1] + "…"


# ── 집계기 ────────────────────────────────────────────────────
#
# 집계기는 "객체를 어떻게 모을지" 만 안다.
# 화면에 어떻게 그릴지(폭, 정렬)는 뷰가 하고, 어떤 타입인지는 알 필요가 없다.
#
# 만들어야 할 메서드는 셋이다:
#     add(obj)     객체 하나를 받아서 모은다
#     header()     표의 컬럼 이름 목록      예) ["symbol", "price"]
#     rows()       표의 내용 (문자열 2차원) 예) [["005930", "70,000"], ...]


class Aggregator:
    """모든 집계기의 부모. 직접 쓰지 않는다."""

    label = "집계"           # 화면 하단에 표시되는 이름

    def add(self, obj) -> None:
        """객체 하나를 모은다. 렌더 스레드에서만 불린다."""
        raise NotImplementedError

    def header(self) -> list[str]:
        """표의 컬럼 이름들."""
        raise NotImplementedError

    def rows(self) -> list[list[str]]:
        """표의 내용. 이미 문자열로 바뀐 상태여야 한다."""
        raise NotImplementedError

    def footer(self) -> str:
        """화면 하단에 덧붙일 한 줄. 없으면 빈 문자열."""
        return ""


class Board(Aggregator):
    """시세판 — 키 하나당 한 줄, 줄 위치가 고정된다.

    예) Board(by="symbol", cols=["price", "volume"])

        symbol   price  volume   n  age
        000660  70,509   12.00  20   0s
        005930  70,492   11.00  20   3s     ← 이 줄은 항상 여기 있다

    ■ 종목이 여럿이면 이걸 쓴다
        Recent 는 시간순이라 종목이 섞이면 새 틱마다 전체가 한 줄씩
        밀린다. 눈으로 못 따라간다.
        Board 는 종목별로 자리를 잡아두고 그 줄의 숫자만 바꾼다.

    ■ n 과 age
        n   그 종목이 지금까지 몇 건 왔나
        age 마지막으로 온 지 몇 초 됐나
        age 가 계속 늘어나면 그 종목이 끊긴 것이다. 이게 제일 유용하다.

    ■ sort 를 주면
        그 필드로 정렬한다. 값이 바뀔 때마다 줄이 움직이므로
        "지금 뭐가 제일 비싼가" 같은 걸 볼 때만 쓴다.
    """

    def __init__(self, by: str = "symbol", cols=None,
                 sort: str | None = None, desc: bool = True,
                 show_age: bool = True):
        self.by = by                    # 줄을 나누는 기준 필드
        self.cols = cols                # 보여줄 컬럼. None 이면 전부
        self.sort = sort                # 정렬 기준 필드. None 이면 by 순
        self.desc = desc                # 정렬 시 내림차순인가
        self.show_age = show_age        # n, age 컬럼을 붙일까

        # 종목코드 -> 그 종목의 마지막 행
        #   {"005930": {"dt":..., "symbol":"005930", "price":70000.0}, ...}
        self.last_row: dict[Any, dict] = {}
        # 종목코드 -> 지금까지 온 건수
        self.hits: Counter = Counter()
        # 종목코드 -> 마지막으로 온 시각 (time.time() 값)
        self.last_time: dict[Any, float] = {}

        self.label = f"{by} 시세판"

    def add(self, obj) -> None:
        row = to_row(obj)               # Tick → dict
        key = row.get(self.by, "-")     # "005930"
        self.last_row[key] = row        # 그 종목 줄을 덮어쓴다
        self.hits[key] += 1
        self.last_time[key] = time.time()

    def _value_columns(self) -> list[str]:
        """보여줄 컬럼 이름들. cols 를 안 줬으면 필드에서 자동으로 뽑는다."""
        if self.cols:
            return list(self.cols)
        if not self.last_row:
            return []
        # 아무 행이나 하나 꺼내서 그 필드 이름을 쓴다.
        # 단, 기준 필드(symbol)는 맨 앞에 따로 넣으므로 여기서 뺀다.
        any_row = next(iter(self.last_row.values()))
        return [name for name in any_row if name != self.by]

    def header(self) -> list[str]:
        columns = [self.by] + self._value_columns()
        if self.show_age:
            columns = columns + ["n", "age"]
        return columns

    def _sorted_items(self) -> list[tuple]:
        """줄 순서를 정한다. [(종목코드, 행dict), ...]"""
        if not self.sort:
            # 기본: 종목코드 오름차순. 줄이 절대 안 움직인다.
            return sorted(self.last_row.items())

        # sort 필드로 정렬. 값이 없는(None) 행은 뒤로 보낸다.
        def sort_key(item):
            _key, row = item
            value = row.get(self.sort)
            is_empty = value is None
            return (is_empty, value)

        return sorted(self.last_row.items(), key=sort_key, reverse=self.desc)

    def rows(self) -> list[list[str]]:
        value_columns = self._value_columns()
        now = time.time()
        result = []

        for key, row in self._sorted_items():
            line = [str(key)]
            for column in value_columns:
                line.append(cell(row.get(column)))
            if self.show_age:
                seconds_ago = now - self.last_time.get(key, now)
                line.append(f"{self.hits[key]:,}")
                line.append(f"{seconds_ago:.0f}s")
            result.append(line)
        return result


class Recent(Aggregator):
    """최근 N건을 시간순으로. 새 것이 위에 온다.

    예) Recent(20)

              dt  symbol   price
        09:30:15  005930  70,000    ← 가장 최근
        09:30:14  005930  69,990

    ■ 종목이 여럿이면 쓰지 말 것
        새 틱마다 전체가 한 줄씩 밀려서 읽을 수가 없다. Board 를 쓴다.

    ■ Recent 가 맞는 곳
        · 한 종목만 볼 때 (where 로 걸러서)
        · 빈도가 낮은 것 — 체결, 주문, 봉 마감 지표
    """

    def __init__(self, n: int = 20, cols=None):
        # maxlen 을 주면 N개를 넘는 순간 앞에서 자동으로 빠진다.
        # 직접 지울 필요가 없다.
        self.buffer: deque = deque(maxlen=n)
        self.cols = cols
        self.label = f"최근 {n}건"

    def add(self, obj) -> None:
        self.buffer.append(to_row(obj))

    def header(self) -> list[str]:
        if self.cols:
            return list(self.cols)
        if not self.buffer:
            return []
        # 마지막에 들어온 행의 필드 이름을 컬럼으로 쓴다
        return list(self.buffer[-1].keys())

    def rows(self) -> list[list[str]]:
        columns = self.header()
        result = []
        # [::-1] 은 순서를 뒤집는다는 뜻. 최신이 위로 오게.
        for row in reversed(self.buffer):
            result.append([cell(row.get(c)) for c in columns])
        return result


class Latest(Aggregator):
    """마지막 1건만. 필드가 많은 객체(호가 40필드)를 세로로 본다.

    예)
        필드          값
        symbol       005930
        ask_price_1  70,100
        bid_price_1  70,000
        ...
    """

    label = "최신 1건"

    def __init__(self, cols=None):
        self.row: dict | None = None
        self.cols = cols
        self.received_at: float | None = None

    def add(self, obj) -> None:
        self.row = to_row(obj)
        self.received_at = time.time()

    def header(self) -> list[str]:
        return ["필드", "값"]

    def rows(self) -> list[list[str]]:
        if not self.row:
            return []
        field_names = self.cols or list(self.row)
        return [[name, cell(self.row.get(name), 1000)] for name in field_names]

    def footer(self) -> str:
        if not self.received_at:
            return ""
        return f"수신 {time.time() - self.received_at:.1f}초 전"


class Count(Aggregator):
    """키별 건수와 초당 유입량. "뭐가 얼마나 들어오나" 를 본다.

    예) Count(by="symbol")

        symbol  건수   건/초
        005930  1,204   12.4
        000660    980   10.1
    """

    def __init__(self, by: str = "symbol"):
        self.by = by
        self.counts: Counter = Counter()    # {"005930": 1204, ...}
        self.start_time = time.time()
        self.label = f"{by}별 건수"

    def add(self, obj) -> None:
        key = to_row(obj).get(self.by, "-")
        self.counts[key] += 1

    def header(self) -> list[str]:
        return [self.by, "건수", "건/초"]

    def rows(self) -> list[list[str]]:
        # 0으로 나누는 것을 막으려고 아주 작은 값을 하한으로 둔다
        elapsed = max(time.time() - self.start_time, 1e-9)
        result = []
        for key, count in self.counts.most_common():    # 많은 순
            result.append([str(key), f"{count:,}", f"{count / elapsed:.1f}"])
        return result

    def footer(self) -> str:
        elapsed = max(time.time() - self.start_time, 1e-9)
        return f"합계 {sum(self.counts.values()):,}건  경과 {elapsed:.0f}초"


@dataclass
class _StatBox:
    """Stat 이 키 하나마다 들고 있는 상자.

    리스트에 [최신, 최소, 최대, 합계, 개수] 를 넣고 a[0], a[1] 로 쓰면
    나중에 아무도 못 읽는다. 이름을 붙여 둔다."""
    latest: float = 0.0
    lowest: float = float("inf")     # 처음엔 무한대. 어떤 값이 와도 갱신된다
    highest: float = float("-inf")   # 처음엔 마이너스 무한대
    total: float = 0.0               # 평균을 구하려고 다 더해 둔다
    count: int = 0

    def put(self, value: float) -> None:
        self.latest = value
        self.lowest = min(self.lowest, value)
        self.highest = max(self.highest, value)
        self.total += value
        self.count += 1

    @property
    def average(self) -> float:
        return self.total / self.count if self.count else 0.0


class Stat(Aggregator):
    """키별 수치 요약 — 최신/최저/최고/평균.

    예) Stat(field="price", by="symbol")

        symbol     최신     최저     최고     평균  건수
        005930  70,492  70,000  70,748  70,326    20
    """

    def __init__(self, field: str = "price", by: str = "symbol"):
        self.field = field      # 어떤 숫자를 볼지  예) "price"
        self.by = by            # 어떻게 나눌지     예) "symbol"
        self.boxes: dict[Any, _StatBox] = {}
        self.label = f"{by}별 {field}"

    def add(self, obj) -> None:
        row = to_row(obj)
        value = row.get(self.field)

        # 숫자가 아니면 통계에 못 넣는다. 조용히 넘어간다.
        if not isinstance(value, (int, float)):
            return

        key = row.get(self.by, "-")
        if key not in self.boxes:
            self.boxes[key] = _StatBox()
        self.boxes[key].put(value)

    def header(self) -> list[str]:
        return [self.by, "최신", "최저", "최고", "평균", "건수"]

    def rows(self) -> list[list[str]]:
        result = []
        for key, box in sorted(self.boxes.items()):
            result.append([
                str(key),
                cell(box.latest),
                cell(box.lowest),
                cell(box.highest),
                cell(box.average),
                f"{box.count:,}",
            ])
        return result


class Pivot(Aggregator):
    """세로로 긴 데이터를 표로 편다.

    지표 스냅샷은 한 행에 값이 하나뿐이라 그대로 보면 읽기 어렵다.

        받는 것 (여러 건)
            IndicatorSnapshot(symbol="005930", label="SMA(20)", value=70100)
            IndicatorSnapshot(symbol="005930", label="RSI(14)", value=62.3)
            IndicatorSnapshot(symbol="000660", label="SMA(20)", value=201000)

        보여주는 것
            symbol  SMA(20)  RSI(14)
            005930   70,100    62.30
            000660  201,000        -
    """

    def __init__(self, index: str = "symbol", columns: str = "label",
                 value: str = "value"):
        self.index = index        # 세로축이 될 필드   예) "symbol"
        self.columns = columns    # 가로축이 될 필드   예) "label"
        self.value = value        # 칸에 채울 필드     예) "value"

        # {"005930": {"SMA(20)": 70100, "RSI(14)": 62.3}, ...}
        self.table: dict[Any, dict] = {}
        # 가로축에 등장한 이름들. 나온 순서를 지키려고 리스트로 둔다.
        self.column_names: list = []

        self.label = f"{columns} 피벗"

    def add(self, obj) -> None:
        row = to_row(obj)
        row_key = row.get(self.index, "-")       # "005930"
        col_key = row.get(self.columns, "-")     # "SMA(20)"

        # 처음 보는 가로축 이름이면 목록에 추가
        if col_key not in self.column_names:
            self.column_names.append(col_key)

        if row_key not in self.table:
            self.table[row_key] = {}
        self.table[row_key][col_key] = row.get(self.value)

    def header(self) -> list[str]:
        return [self.index] + [str(c) for c in self.column_names]

    def rows(self) -> list[list[str]]:
        result = []
        for row_key, values in sorted(self.table.items()):
            line = [str(row_key)]
            for col_key in self.column_names:
                # 그 조합의 값이 아직 없으면 cell(None) 이 "-" 를 준다
                line.append(cell(values.get(col_key)))
            result.append(line)
        return result


# ── 구독 ──────────────────────────────────────────────────────

class Panel:
    """구독 하나 = 화면 하나 = 숫자키 하나.

    "어떤 타입을(dtype) 어떤 방식으로(agg) 모을지, 이름은 뭔지(name),
     걸러낼 조건이 있는지(where)" 를 묶어둔 것이다.

    예)
        Panel(Tick, Board(cols=["price"]), "시세판")
            → 모든 Tick 을 시세판으로

        Panel(Tick, Recent(20), "삼성", where={"symbol": "005930"})
            → 005930 의 Tick 만 최근 20건으로
    """

    def __init__(self, dtype: Type, agg: Aggregator, name: str, where=None,
                 render_interval: float | None = None):
        self.dtype = dtype      # 이 타입의 객체만 받는다
        self.agg = agg          # 집계 방식
        self.name = name        # 화면 이름
        self.where = where      # 필터 조건 (아래 _passes 참조)
        self.seen = 0           # 지금까지 받은 건수
        # None 이면 Runtime 기본 주기(보통 1초)를 그대로 쓴다. 타이핑
        # 도중 화면이 자꾸 지워지면 안 되는 패널(예: 주문 취소를 'oc'로
        # 타이핑하는 주문 패널)만 구독 시점에 값을 준다 —
        # FeedController.render_interval 이 '지금 보는 패널'의 이 값을
        # 그대로 돌려준다(controller.py 참고).
        self.render_interval = render_interval

    def _passes(self, obj) -> bool:
        """이 객체를 받을지 말지 판단한다.

        where 가 세 가지 형태를 지원한다:

            None                          전부 받는다
            {"symbol": "005930"}          그 필드가 그 값인 것만
            함수                          함수가 True 를 주는 것만
                                          예) lambda t: t.price > 70000
        """
        # ① 조건이 없으면 전부 통과
        if self.where is None:
            return True

        # ② 함수를 준 경우 — 그 함수에 물어본다
        if callable(self.where):
            return bool(self.where(obj))

        # ③ dict 를 준 경우 — 모든 항목이 일치해야 통과
        #    where = {"symbol": "005930", "side": "buy"} 라면
        #    obj.symbol == "005930" 이고 obj.side == "buy" 여야 한다
        for field_name, wanted in self.where.items():
            actual = getattr(obj, field_name, None)
            if actual != wanted:
                return False
        return True

    def offer(self, obj) -> None:
        """객체를 건넨다. 조건에 맞으면 집계기로 들어간다."""
        if self._passes(obj):
            self.agg.add(obj)
            self.seen += 1

    @property
    def title(self) -> str:
        """화면 하단에 쓰는 설명.  예) "삼성 (Tick·최근 20건)" """
        return f"{self.name} ({self.dtype.__name__}·{self.agg.label})"


class FeedHub:
    """큐에서 나온 객체를 타입 보고 알맞은 패널에 나눠준다.

    예)
        hub.subscribe(Tick, Board(cols=["price"]), name="시세판")
        hub.subscribe(Tick, Recent(20), name="삼성", where={"symbol":"005930"})
        hub.subscribe(Quote, Latest(), name="호가")

        hub.on_object(어떤_Tick)
            → 시세판 패널과 삼성 패널 둘 다에 건넨다
              (삼성 패널은 005930 이 아니면 자기가 알아서 버린다)

        hub.on_object(어떤_Fill)
            → 구독한 패널이 없으므로 unknown 에만 세어 두고 버린다

    ■ 구독 안 한 타입을 큐에 넣어도 된다
        그냥 흘러간다. 다만 홈 화면 하단에 "미구독: Fill×12" 로 뜬다.
        큐에 넣었는데 화면에 안 보이면 구독을 빼먹은 것이다.

    ■ 락이 없는 이유
        on_object 를 부르는 곳이 렌더 스레드 하나뿐이다.
        다른 스레드가 손대기 시작하면 그때 락이 필요해진다.
    """

    def __init__(self):
        # 타입 -> 그 타입을 구독한 패널들
        #   {Tick: [시세판패널, 삼성패널], Quote: [호가패널]}
        # 리스트인 이유: 같은 타입을 여러 번 구독할 수 있다
        self.routes: dict[Type, list[Panel]] = {}

        # 등록한 순서대로 담는다. 이 순서가 곧 숫자키 1, 2, 3...
        self.panels: list[Panel] = []

        # 구독하지 않은 타입이 몇 건 왔는지
        #   {"Fill": 12, "Order": 3}
        self.unknown: Counter = Counter()

    def subscribe(self, dtype: Type, agg: Aggregator,
                  name: str | None = None, where=None,
                  render_interval: float | None = None) -> Panel:
        """화면 하나를 등록한다. 등록 순서가 곧 숫자키 번호다."""
        panel = Panel(dtype, agg, name or dtype.__name__, where, render_interval)

        # routes 에 이 타입 칸이 없으면 빈 리스트를 만들고 거기 넣는다
        if dtype not in self.routes:
            self.routes[dtype] = []
        self.routes[dtype].append(panel)

        self.panels.append(panel)
        return panel

    def on_object(self, obj) -> None:
        """객체 하나를 받아 알맞은 패널들에 나눠준다.
        렌더 스레드에서만 부른다."""
        panels = self.routes.get(type(obj))

        # 구독한 패널이 없는 타입 — 세어 두고 버린다
        if not panels:
            self.unknown[type(obj).__name__] += 1
            return

        for panel in panels:
            panel.offer(obj)


# ── 공유 ───────────────────────────────────────────────────────

class AppCtx:
    """전 화면이 공유하는 데이터.

    ws/engine 은 없어도 된다 — 그 기능(종목 구독/수동 주문)을 안 쓰면
    None 이면 그만이다."""

    def __init__(self, ws=None, view_q=None, feed: "FeedHub | None" = None,
                 engine=None):
        self.ws = ws                        # 웹소켓 엔진 (종목 구독용)
        self.inbox = Inbox(view_q)          # 큐 통계 → 홈 대시보드
        self.feed = feed or FeedHub()       # 구독형 화면
        # Engine. 콘솔 수동 주문 화면이 engine.slots[...] 로 StrategyBroker를
        # 찾아 실제 주문을 낸다. Application 생성 시점엔 아직 없을 수 있어
        # (view_q를 먼저 줘야 Engine을 만들 수 있으므로) 나중에
        # app.ctx.engine = eng 로 채워 넣는 것도 허용한다.
        self.engine = engine
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
    """대시보드 — 지금 무엇이 얼마나 들어오고 있나."""

    @property
    def inbox(self) -> Inbox:
        return self.ctx.inbox

    @property
    def subscribed(self) -> str:
        """웹소켓 구독 현황 한 줄 요약. ws 가 없으면 '-'.

        ws.subscription_status() 는 (tr_id, tr_key, 상태) 를 구독 하나당
        한 줄씩 돌려준다 — 종목 하나가 가격(H0STCNT0)·호가(H0STASP0)로
        각각 잡히니 종목 수의 배로 늘어나고, 그걸 str()로 그대로 찍으면
        홈 화면 첫 줄이 종목이 늘어날수록 한없이 길어진다(체결통보
        tr_key인 계좌번호까지 섞여서 더 알아보기 어려웠다). 여기서
        건수로 요약한다 — 상세가 필요하면 ws.subscription_status() 를
        직접 부를 것."""
        ws = self.ctx.ws
        if ws is None or not hasattr(ws, "subscription_status"):
            return "-"
        try:
            rows = ws.subscription_status()
        except Exception:
            return "?"
        if not rows:
            return "0건"

        ok = sum(1 for _, _, status in rows if "SUCCESS" in str(status).upper())
        # 종목(가격·호가) 구독만 센다 — 체결통보는 tr_key가 계좌번호라 "종목"이 아니다.
        feed_tr_ids = {getattr(ws, "tr_price", None), getattr(ws, "tr_orderbook", None)}
        symbols = {key for tr, key, _ in rows if tr in feed_tr_ids}

        text = f"{len(symbols)}종목 · 구독 {len(rows)}건(성공 {ok}"
        if ok < len(rows):
            text += f", 대기/실패 {len(rows) - ok}"
        return text + ")"

    def header(self) -> list[str]:
        return ["종류", "누적", "건/초", "최근수신"]

    def rows(self) -> list[list[str]]:
        return self.inbox.rows()

    def panels(self) -> list[str]:
        """구독된 화면 목록. 숫자키에 뭐가 있는지 홈에서 바로 보인다."""
        return [f" [{i + 1}] {p.name}  ({p.dtype.__name__} · {p.agg.label})"
                for i, p in enumerate(self.ctx.feed.panels)]


class RealData(Paged):
    """수신 로그 — 큐에 들어온 것을 시간순으로 훑는다.

    종목별 시세판이 필요하면 구독 화면(Board)을 쓴다.
    이쪽은 '무엇이 흐르고 있나' 를 날것으로 보는 용도다."""

    def __init__(self, ctx):
        super().__init__(ctx)
        self.only: str | None = None        # 문자열 포함 필터

    def rows(self) -> list:
        items = self.ctx.inbox.recent_lines()
        if self.only:
            items = [t for t in items if self.only in t]
        return self.page(items)


class Detail(Paged):
    """한 종목만 걸러서 본다.

    별도 저장소를 두지 않는다 — 구독 패널이 이미 데이터를 갖고 있다.
    화면 하나를 위해 같은 데이터를 두 벌 쌓을 이유가 없다."""

    def __init__(self, ctx):
        super().__init__(ctx)
        self.code: str | None = None

    def _board(self) -> "Panel | None":
        """Board 로 집계 중인 첫 패널. 없으면 None."""
        return next((p for p in self.ctx.feed.panels
                     if isinstance(p.agg, Board)), None)

    def header(self) -> list[str]:
        p = self._board()
        return p.agg.header() if p else []

    def rows(self) -> list:
        p = self._board()
        if not (p and self.code):
            return self.page([])
        return self.page([r for r in p.agg.rows() if r and self.code in r[0]])


class OrderEntry(ScreenModel):
    """수동 주문 화면 — 구독 종목 번호 목록을 보여주고, 콘솔 명령
    ("o -s 번호|종목코드 -q 수량 -d buy|sell [-p 가격]")으로 실제 주문을
    낸다. 명령 해석과 주문 전송은 OrderEntryController 가 한다.

    ■ 주문 내역을 여기서 쌓지 않는다
        Position/Order 는 이미 Engine.feed_fill/feed_order 가 view_q 로
        흘려서 구독 화면('v')의 Board 패널에 실시간으로 보인다. 이 화면은
        '주문을 넣는' 용도에 집중하고, 넣은 결과는 그쪽에서 확인한다."""

    def __init__(self, ctx):
        super().__init__(ctx)
        self.last_result: str = ""      # 마지막 주문 시도 결과 한 줄

    def symbols(self) -> list[str]:
        """번호 목록에 쓸 구독 종목 코드. ws 가 없으면 빈 리스트.

        price_codes/orderbook_codes 를 합치되 순서를 지키고 중복을 없앤다
        (이 앱에서는 보통 둘이 같은 목록이다). 번호가 가리키는 건 이
        리스트의 인덱스이므로, 화면에 찍는 순서와 여기 순서가 같아야 한다."""
        ws = self.ctx.ws
        if ws is None:
            return []
        seen: list[str] = []
        for code in (list(getattr(ws, "price_codes", ()))
                     + list(getattr(ws, "orderbook_codes", ()))):
            if code not in seen:
                seen.append(code)
        return seen


class Feed(Paged):
    """구독 화면의 모델.

    ■ 이 모델은 데이터를 안 들고 있다
        데이터는 집계기(Aggregator)가 갖고 있다.
        여기는 "몇 번 패널을 보고 있나(sel)" 와 "몇 줄 내렸나(scroll)" 뿐이다.
        그래서 데이터 종류가 늘어도 이 클래스는 안 고친다.

    ■ 화면이 그려지는 순서
        1. menu()    상단 메뉴 문자열   "[1]시세판*  [2]삼성"
        2. header()  선택된 집계기의 컬럼 이름
        3. rows()    선택된 집계기의 내용 (페이지만큼 잘라서)
        4. status()  하단 설명 줄
        kis_view.feed() 가 이 넷을 순서대로 부른다.
    """

    def __init__(self, ctx):
        super().__init__(ctx)
        self.sel = 0            # 지금 보고 있는 패널 번호 (0부터)

    @property
    def hub(self) -> FeedHub:
        return self.ctx.feed

    @property
    def panel(self) -> Panel | None:
        """지금 보고 있는 패널. 구독이 하나도 없으면 None."""
        panels = self.hub.panels
        if 0 <= self.sel < len(panels):
            return panels[self.sel]
        return None

    def select(self, index: int) -> bool:
        """패널을 바꾼다. 성공하면 True, 그 번호가 없으면 False.

        컨트롤러가 숫자키를 눌렀을 때 부른다."""
        if 0 <= index < len(self.hub.panels):
            self.sel = index
            self.top()          # 화면을 맨 위로 (Paged 의 메서드)
            return True
        return False

    def header(self) -> list[str]:
        """표의 컬럼 이름들. 집계기에게 물어본다."""
        panel = self.panel
        if panel is None:
            return []
        return panel.agg.header()

    def rows(self) -> list[list[str]]:
        """표의 내용. 집계기에게 받아서 화면 크기만큼 잘라 준다.

        page() 는 Paged 가 주는 메서드다. scroll 위치부터
        page_size(15) 줄만 돌려주고, total 도 같이 갱신한다."""
        panel = self.panel
        if panel is None:
            return []
        return self.page(panel.agg.rows())

    def menu(self) -> str:
        """상단 메뉴 줄. 지금 보고 있는 것에 * 를 붙인다.

        결과 예)  "[1]시세판*  [2]삼성  [3]지표"
        """
        parts = []
        for i, panel in enumerate(self.hub.panels):
            number = i + 1                      # 숫자키는 1부터
            if i == self.sel:
                parts.append(f"[{number}]{panel.name}*")
            else:
                parts.append(f"[{number}]{panel.name}")
        return "  ".join(parts)

    def status(self) -> str:
        """하단 설명 줄.

        결과 예)
            "삼성 (Tick·최근 20건)  수신 1,204건  1-15 / 20  |  미구독: Fill×3"
        """
        panel = self.panel
        if panel is None:
            return ""

        text = f"{panel.title}  수신 {panel.seen:,}건  {self.page_label}"

        # 집계기가 덧붙일 말이 있으면 (예: Count 의 합계, Latest 의 수신 시각)
        extra = panel.agg.footer()
        if extra:
            text += f"  |  {extra}"

        # 큐에 넣었는데 구독을 안 한 타입이 있으면 알려준다.
        # "화면에 안 보이는데?" 의 원인이 대개 이것이다.
        if self.hub.unknown:
            missing = ", ".join(f"{name}×{count:,}"
                                for name, count in self.hub.unknown.most_common(3))
            text += f"  |  미구독: {missing}"

        return text