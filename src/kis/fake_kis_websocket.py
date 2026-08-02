"""
KIS 웹소켓 모의 서버 — 장 마감 후·주말에 테스트하기 위한 대역(代役).

사용법 (kis_feed.py는 한 줄도 안 고침):

    import websockets, fake_kis
    websockets.connect = fake_kis.connect          # 교체
    fake_kis.SCENARIO = fake_kis.Scenario(tps=50)  # 설정

    asyncio.run(main(...))

진짜 서버와 같은 것:
  · 구독 요청을 받으면 SUBSCRIBE SUCCESS ACK를 돌려준다
  · PINGPONG을 주기적으로 보낸다
  · 0|TR_ID|건수|본문 형식의 실시간 프레임을 흘린다
  · 끊길 때 ConnectionClosed를 던진다

진짜 서버와 다른 것 (일부러):
  · 시나리오로 고장을 주입할 수 있다 (이상 프레임, 강제 절단, ACK 누락)
  · 시간을 압축할 수 있다 (tps를 올리면 장중 몇 분을 몇 초에)
  · seed를 주면 매번 같은 데이터가 나온다 (재현 가능한 테스트)
"""

from __future__ import annotations

import asyncio
import json
import random
from dataclasses import dataclass, field

# kis_feed가 잡는 예외와 동일해야 재연결 경로가 검증된다
from websockets.exceptions import ConnectionClosed


# ── 시나리오 ────────────────────────────────────────────────────
@dataclass
class Scenario:
    """무엇을 얼마나 보낼지, 어디를 고장낼지."""

    codes: list[str] = field(default_factory=lambda: ["005930", "000660"])
    tps: float = 20.0                # 초당 틱 수 (전체 합계)
    seed: int | None = 42            # None이면 매번 다름

    # ── 정상 동작 ──
    ack_delay: float = 0.02          # 구독 응답까지 걸리는 시간
    pingpong_interval: float = 5.0   # PINGPONG 주기

    # ── 고장 주입 (0.0 = 안 함) ──
    bad_frame_rate: float = 0.0      # 이상 프레임 비율 (0.02 = 2%)
    bytes_frame_rate: float = 0.0    # bytes로 보낼 비율
    burst_bad_at: float | None = None  # N초 시점에 이상 프레임 30건 연속
    disconnect_after: float | None = None  # N초 뒤 강제 절단
    ack_skip_codes: list[str] = field(default_factory=list)  # ACK 안 주는 종목
    notice_after: float | None = None  # N초 뒤 체결통보 1건

    tr_price: str = "H0STCNT0"
    tr_orderbook: str = "H0STASP0"
    tr_notice: str = "H0STCNI0"


SCENARIO = Scenario()          # connect()가 참조하는 전역 설정
_connect_count = 0             # 재연결 횟수 추적용


def connection_count() -> int:
    """지금까지 연결이 몇 번 수립됐는지. 재연결 검증에 쓴다."""
    return _connect_count


# ── 이상 프레임 표본 ────────────────────────────────────────────
BAD_FRAMES: list = [
    "깨진문자열",
    "",
    "0|H0STCNT0",                     # 필드 부족
    "0|H0STCNT0|xx|005930^1",         # 건수가 숫자가 아님
    "0|UNKNOWN99|001|a^b",            # 미구독 tr_id
    "0|H0STCNT0|001|",                # 본문 없음
    b"\xff\xfe\x00",                  # 디코딩 불가 바이트
    "<html>502 Bad Gateway</html>",   # 프록시가 끼어든 경우
    "null",
]


class FakeKisWebSocket:
    """websockets 객체를 흉내낸다. recv/send/pong/close만 있으면 충분하다."""

    def __init__(self, scenario: Scenario):
        self.sc = scenario
        self.rng = random.Random(scenario.seed)
        self._out: asyncio.Queue = asyncio.Queue()   # 클라이언트가 받을 프레임
        self._closed = False
        self._t0 = asyncio.get_running_loop().time()
        self._burst_done = False
        self._notice_done = False
        self._prices = {c: 70000 + self.rng.randint(-3000, 3000)
                        for c in scenario.codes}
        self._seq = 0
        self._tasks = [
            asyncio.create_task(self._tick_producer()),
            asyncio.create_task(self._pingpong_producer()),
        ]
        if scenario.disconnect_after is not None:
            self._tasks.append(asyncio.create_task(self._killer()))

    # ── 클라이언트가 부르는 메서드 ──────────────────────────────
    async def recv(self):
        if self._closed:
            raise ConnectionClosed(None, None)
        frame = await self._out.get()
        if frame is _CLOSE:
            self._closed = True
            raise ConnectionClosed(None, None)
        return frame

    async def send(self, message: str) -> None:
        """구독 요청을 받아 ACK를 돌려준다."""
        if self._closed:
            raise ConnectionClosed(None, None)
        try:
            msg = json.loads(message)
            inp = msg["body"]["input"]
            tr_id, tr_key = inp["tr_id"], inp["tr_key"]
        except Exception:
            return                                   # 알 수 없는 요청은 무시

        if tr_key in self.sc.ack_skip_codes:
            return                                   # 일부러 응답 안 함 → 타임아웃 검증

        await asyncio.sleep(self.sc.ack_delay)
        await self._out.put(json.dumps({
            "header": {"tr_id": tr_id, "tr_key": tr_key},
            "body": {"rt_cd": "0", "msg_cd": "OPSP0000",
                     "msg1": "SUBSCRIBE SUCCESS",
                     # 실제 서버는 iv/key를 항상 실어 보낸다 (encrypt=N이어도)
                     "output": {"iv": "f4101ab55c403cd8",
                                "key": "oktpbgrsmxvzlbcrhfykfkcqvzeeupjk"}},
        }))

    async def pong(self, data=None) -> None:
        pass                                          # 서버는 받기만 한다

    async def close(self) -> None:
        self._shutdown()

    # ── 내부 생산자 ────────────────────────────────────────────
    async def _tick_producer(self):
        try:
            interval = 1.0 / max(self.sc.tps, 0.001)
            while not self._closed:
                await asyncio.sleep(interval)
                await self._emit_burst_if_due()
                await self._emit_notice_if_due()
                await self._out.put(self._next_frame())
        except asyncio.CancelledError:
            pass

    async def _pingpong_producer(self):
        try:
            while not self._closed:
                await asyncio.sleep(self.sc.pingpong_interval)
                await self._out.put(json.dumps({
                    "header": {"tr_id": "PINGPONG", "datetime": "20260731120000"},
                    "body": None,
                }))
        except asyncio.CancelledError:
            pass

    async def _killer(self):
        try:
            await asyncio.sleep(self.sc.disconnect_after)
            await self._out.put(_CLOSE)
        except asyncio.CancelledError:
            pass

    def _elapsed(self) -> float:
        return asyncio.get_running_loop().time() - self._t0

    async def _emit_burst_if_due(self):
        if self.sc.burst_bad_at is None or self._burst_done:
            return
        if self._elapsed() >= self.sc.burst_bad_at:
            self._burst_done = True
            for _ in range(30):                       # 연속 폐기 한도 시험
                await self._out.put(self.rng.choice(BAD_FRAMES))

    async def _emit_notice_if_due(self):
        if self.sc.notice_after is None or self._notice_done:
            return
        if self._elapsed() >= self.sc.notice_after:
            self._notice_done = True
            # 체결통보는 암호화되어 온다 (flag=1). 복호화 경로 시험용.
            await self._out.put(f"1|{self.sc.tr_notice}|001|QkFTRTY0RU5DUllQVEVE")

    # ── 프레임 생성 ────────────────────────────────────────────
    def _next_frame(self):
        if self.rng.random() < self.sc.bad_frame_rate:
            return self.rng.choice(BAD_FRAMES)

        code = self.rng.choice(self.sc.codes)
        frame = (self._make_orderbook(code) if self.rng.random() < 0.3
                 else self._make_trade(code))

        if self.rng.random() < self.sc.bytes_frame_rate:
            return frame.encode("utf-8")
        return frame

    def _walk(self, code: str) -> int:
        """호가 단위를 지킨 랜덤워크."""
        p = self._prices[code]
        step = 100 if p >= 50000 else 50
        p = max(step, p + self.rng.choice([-2, -1, 0, 0, 1, 2]) * step)
        self._prices[code] = p
        return p

    def _make_trade(self, code: str) -> str:
        """H0STCNT0 실시간 체결가. 앞 몇 개 필드만 실제와 맞춘다."""
        self._seq += 1
        px = self._walk(code)
        f = [
            code,                                   # 종목코드
            f"{9 + self._seq // 3600 % 6:02d}"
            f"{self._seq // 60 % 60:02d}{self._seq % 60:02d}",  # 체결시간
            str(px),                                # 현재가
            self.rng.choice(["2", "5"]),            # 등락구분
            str(self.rng.randint(-500, 500)),       # 전일대비
            f"{self.rng.uniform(-2, 2):.2f}",       # 등락률
            str(px),                                # 가중평균가
            str(px + 200), str(px + 300), str(px - 300),        # 시/고/저
            str(px), str(px - 100),                 # 매도호가1 매수호가1
            str(self.rng.randint(1, 500)),          # 체결거래량
            str(self.rng.randint(10**5, 10**7)),    # 누적거래량
        ]
        f += ["0"] * (46 - len(f))                  # 나머지 필드 채움
        return f"0|{self.sc.tr_price}|001|{'^'.join(f)}"

    def _make_orderbook(self, code: str) -> str:
        """H0STASP0 실시간 호가. 10단계 매도/매수."""
        px = self._prices[code]
        step = 100 if px >= 50000 else 50
        asks = [str(px + step * i) for i in range(1, 11)]
        bids = [str(px - step * i) for i in range(1, 11)]
        vols = [str(self.rng.randint(1, 9999)) for _ in range(20)]
        f = [code, "123000", "0"] + asks + bids + vols
        f += ["0"] * (59 - len(f))
        return f"0|{self.sc.tr_orderbook}|001|{'^'.join(f)}"

    def _shutdown(self):
        self._closed = True
        for t in self._tasks:
            t.cancel()


_CLOSE = object()          # 절단 신호


class _ConnectCM:
    """async with websockets.connect(...) 를 흉내낸다."""

    def __init__(self, *args, **kwargs):
        self.ws: FakeKisWebSocket | None = None

    async def __aenter__(self) -> FakeKisWebSocket:
        global _connect_count
        _connect_count += 1
        await asyncio.sleep(0.01)              # 핸드셰이크 흉내
        self.ws = FakeKisWebSocket(SCENARIO)
        return self.ws

    async def __aexit__(self, *exc):
        if self.ws:
            self.ws._shutdown()
        return False


def connect(*args, **kwargs) -> _ConnectCM:
    """websockets.connect 자리에 그대로 끼운다."""
    return _ConnectCM(*args, **kwargs)