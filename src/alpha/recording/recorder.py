"""
═══════════════════════════════════════════════════════════════════
 recorder.py — 구독 기반 기록
═══════════════════════════════════════════════════════════════════

    소켓      ──subscribe(TickRecord, SqliteSink(...))──┐
    트레이더  ──subscribe(IndicatorSnapshot, Parquet...)─┼→ Recorder → Sink
    브로커    ──subscribe(FillRecord, ...)──────────────┘   (큐 1개, 스레드 1개)

■ 기존 객체를 그대로 밀어 넣는다
    저장용 Record 클래스를 따로 만들지 않는다. 데이터 종류가 늘 때마다
    짝이 되는 클래스를 만드는 건 순수 중복이다.

        rec.subscribe(Tick, SqliteSink("kis.db"), name="ticks")
        rec.put(tick)               # 기존 Tick 객체 그대로

    라우팅 키는 type(obj) 다. 저장 위치(name)와 방식(sink)은 구독 설정이지
    데이터의 속성이 아니므로 subscribe 에서 정한다.

■ 큐가 하나인 이유
    구독마다 큐를 만들면 드레인 루프가 여러 개 되거나 select 가 필요하다.
    타입으로 라우팅하면 큐 하나로 충분하고 채널 간 시간 순서도 보존된다.

■ 백프레셔 — 이 파일에서 제일 중요한 결정
    기록은 매매의 크리티컬 패스가 아니다.
    큐가 차면 '버린다'. 막지 않는다.

        trading_q  절대 안 버림 — 틱을 놓치면 매매가 틀어진다
        recording  버려도 됨   — 분석 자료가 조금 비는 것뿐

    put 이 블록되면 소켓 스레드가 멈춰서 진짜 틱을 놓친다.
    유실 건수는 세서 주기적으로 경고한다.

■ flush 조건은 둘
    batch(건수) 또는 max_age(초) 중 먼저 오는 쪽.
    건수만 쓰면 거래 뜸한 채널은 영영 안 써지고 프로세스가 죽을 때
    통째로 잃는다.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from queue import Empty, Full, Queue
from typing import Callable, Optional, Type

log = logging.getLogger("recorder")

DEFAULT_QUEUE_SIZE = 100_000
DROP_LOG_INTERVAL = 30.0        # 유실 경고 간격(초)


@dataclass
class Channel:
    """구독 하나. (타입 + 저장 위치 + 저장 방식)"""
    dtype: Type                     # 라우팅 키가 되는 데이터 타입
    sink: object                    # Sink
    name: str                       # 저장 대상 이름 (테이블/파일/디렉터리)
    batch: int = 500                # 이만큼 모이면 쓴다
    max_age: float = 5.0            # 또는 이만큼 지나면 쓴다(초)
    extra: dict = field(default_factory=dict)   # 객체에 없는 공통 컬럼

    buf: list = field(default_factory=list)
    last_flush: float = field(default_factory=time.monotonic)
    written: int = 0
    dropped: int = 0
    errors: int = 0
    disabled: bool = False          # 연속 실패하면 끈다

    def due(self, now: float) -> bool:
        if not self.buf:
            return False
        return (len(self.buf) >= self.batch
                or now - self.last_flush >= self.max_age)

    @property
    def desc(self) -> str:
        """로그용. "Tick→ticks" 형태."""
        return f"{self.dtype.__name__}→{self.name}"


class Recorder:
    """큐를 드레인해 채널별로 모았다가 sink 로 넘긴다.

    구독자는 put 함수만 받는다 — Recorder 객체도, 채널 이름도,
    저장 방식도 몰라도 된다."""

    MAX_ERRORS = 5      # 이만큼 연속 실패하면 그 채널을 끈다

    def __init__(self, queue_size: int = DEFAULT_QUEUE_SIZE):
        self.q: Queue = Queue(maxsize=queue_size)
        # 한 타입을 여러 곳에 저장할 수 있다 (sqlite + parquet 동시 등)
        self.channels: dict[Type, list[Channel]] = {}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._dropped_total = 0
        self._last_drop_log = 0.0

    # ───────── 구독 ─────────
    def subscribe(self, dtype: Type, sink, name: Optional[str] = None,
                  batch: int = 500, max_age: float = 5.0,
                  extra: Optional[dict] = None) -> Callable[[object], bool]:
        """데이터 타입 하나를 저장하도록 등록하고 put 함수를 돌려준다.

            put = rec.subscribe(Tick, SqliteSink("kis.db"), name="ticks")
            put(tick)                       # 기존 Tick 객체 그대로

        ■ 저장용 클래스를 만들지 않는다
            Tick/Fill/Order/Trade/Execution 등 이미 있는 객체를 그대로
            넣는다. sink 가 dataclass·__dict__·__slots__ 를 알아서 훑는다.

        ■ name — 저장 대상 이름. sink 가 알아서 해석한다
            SqliteSink 테이블 / JsonlSink 파일 / ParquetSink 디렉터리
            생략하면 타입 이름을 소문자로 쓴다 (Tick → "tick").

        ■ extra — 객체에 없는 공통 컬럼
            Fill 에는 strategy_id 가 없다. 전략마다 따로 구독하면서
            extra={"strategy_id": sid} 를 주면 저장할 때 붙는다.

        ■ 같은 타입을 여러 번 구독할 수 있다
            rec.subscribe(Tick, SqliteSink(...),  name="ticks")
            rec.subscribe(Tick, ParquetSink(...), name="ticks")
            → 두 곳에 다 들어간다.
        """
        ch = Channel(dtype=dtype, sink=sink,
                     name=name or dtype.__name__.lower(),
                     batch=batch, max_age=max_age, extra=dict(extra or {}))
        self.channels.setdefault(dtype, []).append(ch)
        log.info("구독 등록 %s → %s (batch=%d, max_age=%.1fs)",
                 ch.desc, type(sink).__name__, batch, max_age)
        return self.put

    def put(self, record) -> bool:
        """레코드 투입. 큐가 차면 버리고 False 를 돌려준다.

        ★ 절대 블록하지 않는다 ★
          여기서 막히면 소켓 스레드가 멈춰 진짜 틱을 놓친다."""
        try:
            self.q.put_nowait(record)
            return True
        except Full:
            self._dropped_total += 1
            for ch in self.channels.get(type(record), ()):
                ch.dropped += 1
            self._maybe_log_drop()
            return False

    def _maybe_log_drop(self):
        """유실이 나도 매 건 로그를 찍으면 그게 더 큰 부하다."""
        now = time.monotonic()
        if now - self._last_drop_log >= DROP_LOG_INTERVAL:
            self._last_drop_log = now
            log.warning("기록 유실 누적 %d건 — 큐 포화. "
                        "batch 를 키우거나 sink 를 빠른 것으로 바꾸세요",
                        self._dropped_total)

    # ───────── 스레드 ─────────
    def start(self):
        if self._thread:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="recorder")
        self._thread.start()
        log.info("Recorder 시작 — 채널 %d개", len(self.channels))

    def stop(self, timeout: float = 10.0):
        """★ 반드시 불러야 한다 ★
        안 부르면 마지막 배치를 항상 잃는다. 종료 시그널 핸들러에도 걸 것.

        마무리(잔여 드레인 → flush → sink.close)는 recorder 스레드가
        직접 한다. 여기서는 신호만 주고 기다린다 — 이유는 _finalize 참조."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
            self._thread = None
        else:
            self._finalize()            # 스레드 없이 쓴 경우
        log.info("Recorder 종료 — %s", self.stats())

    def _run(self):
        try:
            while not self._stop.is_set():
                try:
                    rec = self.q.get(timeout=0.2)
                    self._accept(rec)
                except Empty:
                    pass
                except Exception:
                    log.exception("레코드 처리 실패 — 루프는 계속")
                self._tick()
        finally:
            # ★ 마무리를 이 스레드에서 하는 이유 ★
            #   sqlite3 커넥션은 만든 스레드에서만 쓸 수 있다.
            #   sink 를 처음 연 게 이 스레드이므로 닫는 것도 여기서 해야 한다.
            #   호출자 스레드에서 close 하면 ProgrammingError 가 난다.
            self._finalize()

    def _finalize(self):
        """잔여 레코드를 모두 저장하고 sink 를 닫는다.

        ★ 두 단계로 나눠야 한다 ★
          flush 와 close 를 한 루프에서 하면, 첫 채널이 닫은 sink 에
          두 번째 채널이 쓰려고 한다. 하나의 sink 를 여러 채널이
          공유하기 때문이다(kis.db 하나에 테이블 셋).

          SqliteSink 는 close 후 _connect 가 다시 열어서 우연히 살지만,
          ParquetSink 는 close 시점에 자기 버퍼를 비우므로 그 뒤에 들어온
          데이터가 버퍼에 갇힌 채 영영 유실된다.

          ① 모든 채널을 sink 로 밀어낸다
          ② 그 다음 sink 를 닫는다 (같은 sink 는 한 번만)
        """
        self._drain_remaining()

        for ch in self._all():                      # ①
            self._flush(ch, final=True)

        closed = set()
        for ch in self._all():                      # ②
            if id(ch.sink) in closed:
                continue
            closed.add(id(ch.sink))
            try:
                ch.sink.close()
            except Exception:
                log.exception("sink close 실패: %s", ch.desc)

    def _drain_remaining(self):
        """종료 시 큐에 남은 것을 마저 처리한다."""
        while True:
            try:
                self._accept(self.q.get_nowait())
            except Empty:
                return
            except Exception:
                log.exception("종료 드레인 중 실패")
                return

    def _accept(self, rec):
        chans = self.channels.get(type(rec))
        if not chans:
            # 구독하지 않은 타입. 버리되 조용히 버리지는 않는다.
            log.warning("구독되지 않은 타입: %s", type(rec).__name__)
            return
        for ch in chans:
            if ch.disabled:
                ch.dropped += 1
                continue
            ch.buf.append(rec)

    def _all(self):
        for chans in self.channels.values():
            yield from chans

    def _tick(self):
        now = time.monotonic()
        for ch in self._all():
            if ch.due(now):
                self._flush(ch)

    def _flush(self, ch: Channel, final: bool = False):
        if not ch.buf or ch.disabled:
            return
        batch, ch.buf = ch.buf, []
        ch.last_flush = time.monotonic()
        try:
            if ch.extra:
                # 객체에 없는 공통 컬럼(strategy_id 등)을 붙인다.
                # extra 가 없으면 객체 리스트를 그대로 넘겨 변환을 아낀다.
                from alpha.recording.sinks import _to_row
                batch = [{**_to_row(r), **ch.extra} for r in batch]
            ch.sink.write(batch, ch.name)
            ch.written += len(batch)
            ch.errors = 0
        except Exception:
            ch.errors += 1
            ch.dropped += len(batch)
            log.exception("%s 저장 실패 (%d회 연속)", ch.desc, ch.errors)
            # ── 실패 격리 ──
            # 한 sink 가 계속 터져도 다른 채널과 스레드는 살아야 한다.
            if ch.errors >= self.MAX_ERRORS and not final:
                ch.disabled = True
                log.error("%s 채널 비활성화 — 연속 %d회 실패",
                          ch.desc, ch.errors)

    def flush_all(self):
        """즉시 전량 저장. 장중에 주기적으로 부르면 크래시 손실이 준다.

        _finalize 와 같은 이유로 두 단계다 — 채널을 모두 밀어낸 뒤에
        sink 를 비운다. 순서를 섞으면 뒤 채널의 데이터가 sink 버퍼에 남는다."""
        for ch in self._all():
            self._flush(ch)

        flushed = set()
        for ch in self._all():
            if id(ch.sink) in flushed:
                continue
            flushed.add(id(ch.sink))
            try:
                ch.sink.flush()
            except Exception:
                log.exception("sink flush 실패: %s", ch.desc)

    # ───────── 모니터링 ─────────
    def stats(self) -> dict:
        return {
            "queued": self.q.qsize(),
            "dropped_total": self._dropped_total,
            "channels": {
                ch.desc: {
                    "written": ch.written, "buffered": len(ch.buf),
                    "dropped": ch.dropped, "disabled": ch.disabled,
                } for ch in self._all()
            },
        }