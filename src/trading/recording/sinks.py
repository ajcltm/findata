"""
═══════════════════════════════════════════════════════════════════
 sinks.py — 레코드를 실제로 쓰는 곳
═══════════════════════════════════════════════════════════════════

■ sink 라는 이름
    데이터 흐름을 source → sink 라 부른다. 물이 수원지에서 나와
    배수구로 빠지듯, 데이터가 흘러 들어가 끝나는 지점이다.

    Broker 와 같은 패턴이다:
        Broker → KISBroker / BacktraderBroker    주문을 어디로
        Sink   → SqliteSink / ParquetSink        행을 어디로

■ 기존 객체를 그대로 받는다
    Tick, Fill, Order, Execution... 무엇이든 받는다.
    저장을 위해 별도 Record 클래스를 만들지 않는다 — 데이터 종류가
    늘 때마다 짝이 되는 클래스를 만드는 건 순수 중복이다.

    dataclass 이기만 하면 된다. 필드 이름은 타입마다 고정이므로
    한 번 구해 캐시하고 getattr 로 읽는다.

■ 저장 대상 이름(name)은 구독할 때 정한다
    sqlite 면 테이블, jsonl 이면 파일, parquet 이면 디렉터리 이름이다.
    객체가 자기 저장 위치를 알 필요가 없고, 같은 Tick 을 sqlite 와
    parquet 에 동시에 넣을 수도 있어야 한다.

■ 실패해도 던지지 않는다
    sink 하나가 터져서 recorder 스레드가 죽으면 다른 채널까지 멈춘다.
    예외는 Recorder 가 잡아 로그로 남기고 연속 실패를 센다.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from abc import ABC, abstractmethod
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("sink")


# ═══════════════════════════════════════════════════════════════════
# 공통 유틸
# ═══════════════════════════════════════════════════════════════════

def _resolve(path: str, when: Optional[date] = None) -> Path:
    """경로의 {date} 를 날짜로 치환한다. 일자별 파티션용.

        "data/{date}/ticks.jsonl" → "data/2024-01-15/ticks.jsonl"

    ■ {date} 가 없으면 그대로 둔다 — 롤링이 없다는 뜻이다
        "kis.db" → "kis.db"    항상 같은 경로

        SqliteSink 는 그게 정상이다(파일 하나가 커지는 것). JsonlSink 도
        append 라 안전하다. ParquetSink 만 주의가 필요하다 — 아래 참조.
    """
    d = (when or date.today()).isoformat()
    p = Path(path.replace("{date}", d))
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


_FIELDS: dict[type, tuple[str, ...]] = {}       # 타입별 필드 이름 캐시


def _to_row(obj) -> dict:
    """dataclass → dict. 이 시스템의 데이터는 전부 dataclass 다.

    ■ asdict() 를 안 쓰는 이유
        재귀적으로 deepcopy 를 해서 느리다. 5만 건 기준 150ms vs 39ms.
        필드 이름만 알면 getattr 로 충분하고, 이름은 타입마다 고정이라
        한 번 구해 캐시한다.

    ■ dataclass 가 아니면 TypeError
        __dict__ / __slots__ 를 뒤지는 폴백은 두지 않는다. 없는 경우를
        대비한 코드는 검증되지도 않고 실수를 조용히 넘긴다.
        여기서 터지면 '이 타입은 dataclass 로 만들라'는 뜻이다.
    """
    if isinstance(obj, dict):
        return obj                          # extra 를 붙인 결과가 dict 이다
    t = type(obj)
    names = _FIELDS.get(t)
    if names is None:
        names = _FIELDS[t] = tuple(f.name for f in fields(obj))
    return {n: getattr(obj, n) for n in names}


def _json_default(o):
    """JSON 이 모르는 타입 처리."""
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if is_dataclass(o):
        return {f.name: getattr(o, f.name) for f in fields(o)}
    if hasattr(o, "value"):         # Enum (Side, OrderStatus 등)
        return o.value
    return str(o)


def _flat(v):
    """저장 가능한 스칼라로. datetime → ISO, Enum → value, 나머지 → JSON."""
    if v is None or isinstance(v, (int, float, str, bytes)):
        return v
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if hasattr(v, "value") and not isinstance(v, (list, tuple, dict)):
        return v.value              # Enum
    return json.dumps(v, ensure_ascii=False, default=_json_default)


class Sink(ABC):
    """레코드 배치를 저장한다.

    ■ name 은 '저장 대상 이름'이다 (테이블명이 아니다)
        sink 마다 다르게 해석한다:
            SqliteSink   테이블 이름
            JsonlSink    파일 이름
            ParquetSink  디렉터리 이름
            MemorySink   키
        저장 위치는 구독 설정이지 데이터의 속성이 아니므로 sink 가 받는다."""

    @abstractmethod
    def write(self, records: list, name: str) -> None: ...

    def flush(self) -> None:
        """내부 버퍼를 비운다. 버퍼가 없으면 no-op."""

    def close(self) -> None:
        """파일·커넥션 정리. 종료 시 반드시 불린다.

        ★ close 후 write 는 에러다 ★
          닫혔는데 조용히 다시 열리면 순서 버그를 숨긴다.
          하나의 sink 를 여러 채널이 공유하므로(kis.db 하나에 테이블 셋),
          flush 와 close 순서가 섞이면 데이터가 유실된다.
          그 사고를 다음번에도 조용히 넘기지 않으려면 여기서 터져야 한다."""


class SinkClosedError(RuntimeError):
    """이미 닫힌 sink 에 쓰려고 했다. Recorder 의 flush/close 순서 문제다."""


# ═══════════════════════════════════════════════════════════════════
# SQLite — 기존 방식(pandas to_sql) 그대로
# ═══════════════════════════════════════════════════════════════════

class SqliteSink(Sink):
    """pandas 로 DataFrame 을 만들어 to_sql 로 저장한다.

    ■ 기존 코드와 같은 방식
            df = pd.DataFrame(batch)
            df.to_sql(table, conn, if_exists="append", index=False)
        테이블이 없으면 pandas 가 알아서 만들고 타입도 추론한다.

    ■ 속도
        executemany 보다 4배쯤 느리지만(5만 건 362ms vs 84ms) 초당
        14만 건 수준이라 코스피 전 종목 틱(초당 수천)의 20배 여유다.
        recorder 는 별도 스레드라 매매에도 영향이 없다.
        정말 밀리면 이 클래스만 갈아끼우면 된다.

    ■ 스레드
        sqlite3 커넥션은 만든 스레드에서만 쓸 수 있다.
        recorder 스레드가 처음 write 할 때 열고 닫는 것도 그 스레드가 한다.
    """

    def __init__(self, path: str, journal_mode: str = "WAL"):
        self.path = path
        self.journal_mode = journal_mode
        self._conn: Optional[sqlite3.Connection] = None
        self._closed = False

    def _connect(self):
        # ★ 재접속을 허용하지 않는다 ★
        #   전에는 close() 가 _conn=None 으로만 두고 여기서 다시 열었다.
        #   그래서 '닫은 뒤에 쓰는' 순서 버그가 조용히 통과했다.
        if self._closed:
            raise SinkClosedError(f"닫힌 SqliteSink 에 쓰기 시도: {self.path}")
        if self._conn is None:
            self._conn = sqlite3.connect(_resolve(self.path))
            # WAL: 읽기와 쓰기가 서로 안 막는다. 장중에 다른 프로세스가
            # 조회해도 기록이 멈추지 않는다.
            self._conn.execute(f"PRAGMA journal_mode={self.journal_mode}")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        return self._conn

    def write(self, records: list, name: str) -> None:
        """name 은 테이블 이름으로 쓴다."""
        import pandas as pd

        df = pd.DataFrame([_to_row(r) for r in records])

        # sqlite 가 모르는 타입(Enum, dataclass, 리스트 등)만 변환한다.
        # 숫자 컬럼은 dtype 이 object 가 아니므로 건드리지 않는다.
        for col in df.columns:
            if df[col].dtype == "object":
                df[col] = df[col].map(_flat)

        df.to_sql(name, self._connect(), if_exists="append", index=False)
        self._conn.commit()

    def close(self) -> None:
        self._closed = True
        if self._conn:
            self._conn.commit()
            self._conn.close()
            self._conn = None


class LegacySink(Sink):
    """기존 KisRecorder 를 그대로 감싼다.

    DB 파일도 테이블 구조도 저장 로직도 안 바뀐다.
    Recorder 는 '배치가 찼으니 써라'만 부른다 — 마이그레이션 없이
    새 구조로 옮겨갈 수 있다."""

    def __init__(self, legacy, method: str = "save"):
        self.legacy = legacy
        self.method = method

    def write(self, records: list, name: str) -> None:
        getattr(self.legacy, self.method)(records)

    def close(self) -> None:
        for name in ("close", "stop", "flush"):
            if hasattr(self.legacy, name):
                getattr(self.legacy, name)()
                return


# ═══════════════════════════════════════════════════════════════════
# JSONL — 가장 단순. 디버깅용으로 먼저 붙이기 좋다
# ═══════════════════════════════════════════════════════════════════

class JsonlSink(Sink):
    """한 줄에 JSON 하나. 사람이 읽을 수 있고 스키마 제약이 없다.

    용량이 크고 읽기가 느리므로 개발 중에 쓰고 나중에 교체한다.
    name 은 파일명으로 쓴다 — 경로의 {name} 이 치환된다."""

    def __init__(self, path: str):
        self.path = path            # "data/{date}/{name}.jsonl"
        self._files: dict[str, Any] = {}
        self._day: Optional[date] = None
        self._lock = threading.Lock()
        self._closed = False

    def _file(self, name: str):
        """날짜가 바뀌면 파일을 새로 연다(일자별 롤링).

        단, 닫힌 뒤에는 열지 않는다 — 일자 롤링과 종료를 구별해야 한다."""
        if self._closed:
            raise SinkClosedError(f"닫힌 JsonlSink 에 쓰기 시도: {self.path}")
        today = date.today()
        if self._day != today:
            for f in self._files.values():
                f.close()
            self._files.clear()
            self._day = today
        f = self._files.get(name)
        if f is None:
            p = _resolve(self.path.replace("{name}", name), today)
            f = self._files[name] = open(p, "a", encoding="utf-8")
        return f

    def write(self, records: list, name: str) -> None:
        with self._lock:
            f = self._file(name)
            for r in records:
                f.write(json.dumps(_to_row(r), ensure_ascii=False,
                                   default=_json_default) + "\n")

    def flush(self) -> None:
        with self._lock:
            for f in self._files.values():
                f.flush()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            for f in self._files.values():
                f.close()
            self._files.clear()


# ═══════════════════════════════════════════════════════════════════
# Parquet — 용량과 읽기 속도. 사후 분석의 기본 포맷
# ═══════════════════════════════════════════════════════════════════

class ParquetSink(Sink):
    """컬럼 저장. 압축률이 좋고 pandas 로 읽기가 빠르다.

    ■ 왜 버퍼가 필요한가
        파케이는 append 가 안 된다. 행 그룹 단위로 써야 하는데
        너무 작게 쓰면 파일이 조각나고 압축률이 떨어진다.
        Recorder 의 배치보다 크게 모았다가 한 번에 쓴다.

    ■ 파일이 여러 개가 된다
        쓸 때마다 part-00001.parquet ... 을 만든다.
        읽을 때는 디렉터리를 통째로 읽으면 된다:
            pd.read_parquet("data/2024-01-15/ticks/")

    ■ ★ part 번호는 디렉터리를 보고 이어붙인다 ★
        메모리 카운터만 쓰면 프로세스를 재시작할 때 0부터 다시 세어
        part-00001.parquet 을 덮어쓴다. 이전 데이터가 통째로 사라진다.
        {date} 를 안 쓰면 항상 그렇고, 써도 같은 날 재시작하면 그렇다.
        (JsonlSink 는 append 라 이 문제가 없다)
    """

    def __init__(self, path: str, row_group: int = 50_000):
        self.path = path            # "data/{date}/{name}"  디렉터리
        self.row_group = row_group
        self._buf: dict[str, list] = {}
        self._part: dict[str, int] = {}
        self._closed = False

    def write(self, records: list, name: str) -> None:
        # 파케이는 close 시점에 버퍼를 파일로 비운다. 그 뒤에 들어온
        # 데이터는 다시 비워질 기회가 없어 영영 유실되므로 막는다.
        if self._closed:
            raise SinkClosedError(f"닫힌 ParquetSink 에 쓰기 시도: {self.path}")
        buf = self._buf.setdefault(name, [])
        buf.extend(records)
        if len(buf) >= self.row_group:
            self._write_part(name)

    def _write_part(self, name: str):
        buf = self._buf.get(name)
        if not buf:
            return
        import pandas as pd
        df = pd.DataFrame([_to_row(r) for r in buf])
        for col in df.columns:
            if df[col].dtype == "object":
                df[col] = df[col].map(
                    lambda v: v if isinstance(v, (str, type(None), datetime))
                    else _flat(v))
        d = _resolve(self.path.replace("{name}", name))
        d.mkdir(parents=True, exist_ok=True)

        i = self._part.get(name)
        if i is None:
            # 이 디렉터리에 이미 있는 part 번호 중 최대값부터 이어간다.
            # 프로세스 재시작 시 기존 파일을 덮어쓰지 않기 위함.
            existing = [int(p.stem.split("-")[-1])
                        for p in d.glob("part-*.parquet")
                        if p.stem.split("-")[-1].isdigit()]
            i = max(existing, default=0)
        i = self._part[name] = i + 1

        df.to_parquet(d / f"part-{i:05d}.parquet", index=False)
        buf.clear()

    def flush(self) -> None:
        for name in list(self._buf):
            self._write_part(name)

    def close(self) -> None:
        self.flush()
        self._closed = True


class MemorySink(Sink):
    """메모리에 쌓는다. 테스트와 백테스트용.

    백테스트에서 이걸 쓰면 실전과 '같은 경로'로 기록이 돌아
    분석 코드를 공유할 수 있다."""

    def __init__(self):
        self.data: dict[str, list] = {}
        self._closed = False

    def write(self, records: list, name: str) -> None:
        if self._closed:
            raise SinkClosedError("닫힌 MemorySink 에 쓰기 시도")
        self.data.setdefault(name, []).extend(records)

    def close(self) -> None:
        self._closed = True

    def df(self, name: str):
        """닫힌 뒤에도 읽을 수 있다 — 백테스트가 결과를 꺼내야 하므로."""
        import pandas as pd
        return pd.DataFrame([_to_row(r) for r in self.data.get(name, [])])