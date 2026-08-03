"""
KIS 실시간 시세 수신기 (개선판)

구조:
    websocket ──> _recv_loop ──> asyncio.Queue ──> _pump ──> queue.Queue (recording)
                  (얇게 유지)     (백프레셔)                 queue.Queue (trading)

핵심 개선:
  1. 세션 단위 재연결 + 자동 재구독 (지수 백오프)
  2. PINGPONG 애플리케이션 레벨 처리 (+ ping_interval=None)
  3. sleep 대신 구독 ACK 대기 (Future, 타임아웃 시 경고 후 진행)
  4. recv 루프는 수신만 담당, 파싱/분배는 분리
  5. 큐 백프레셔 + 드롭 정책
  6. simul_mode를 별도 프로듀서로 분리
  7. sentinel 기반 스레드 정상 종료
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import random
import time
from dataclasses import dataclass
from typing import Sequence
import requests
import websockets
from websockets.exceptions import ConnectionClosed

from kis import kis_config

# 스레드 컨슈머 종료 신호. recording/trading 루프에서 이 값을 받으면 break.
SENTINEL = object()


@dataclass(frozen=True)
class Subscription:
    """구독 단위. tr_id + tr_key 조합이 ACK 매칭 키가 된다."""
    tr_id: str
    tr_key: str

    @property
    def ack_key(self) -> tuple[str, str]:
        return (self.tr_id, self.tr_key)


@dataclass(frozen=True)
class Tick:
    """다운스트림으로 넘기는 최소 단위. 상세 파싱은 컨슈머에게 위임."""
    tr_id: str          # H0STCNT0 / H0STASP0 / H0STCNI0 ...
    count: int          # 데이터 건수
    payload: str        # ^ 구분 원본 바디
    encrypted: bool
    # 암호 프레임(flag=1)일 때만 채운다. 세션마다 값이 바뀌므로
    # 공유 dict를 스레드가 읽게 하면 재연결 순간 옛 틱에 새 키를 쓰게 된다.
    # 틱에 실어 보내면 그 짝이 절대 어긋나지 않는다.
    iv: str | None = None
    key: str | None = None


class KisFeed:
    # ── 튜닝 파라미터 ────────────────────────────────────────────
    ACK_TIMEOUT = 3.0        # 구독 응답 대기 한도(초)
    SUBSCRIBE_GAP = 0.05     # ACK를 기다리므로 rate limit 여유분만
    BASE_BACKOFF = 1.0
    MAX_BACKOFF = 30.0
    MAX_SUBSCRIPTIONS = 40   # ⚠️ 계정 등급별로 다름. 반드시 본인 계정 기준으로 확인.
    MAX_CONSECUTIVE_BAD = 20 # 연속 폐기 한도. 넘으면 프로토콜 이상으로 보고 재연결
    MAX_FRAME_BYTES = 1 << 20
    BAD_LOG_INTERVAL = 1.0   # 같은 종류 오류는 초당 1건만 로깅 (디스크 보호)

    def __init__(
        self,
        price_codes: Sequence[str],
        orderbook_codes: Sequence[str],
        consumer_queues: dict[str, queue.Queue],
        simul_mode: bool = False,
        max_pending: int = 10_000,
        logger: logging.Logger | None = None,
    ) -> None:
        self.ws_url = kis_config.WS_URL
        self.hts_id = kis_config.HTS_ID
        self.approval_key = "fake" if simul_mode else self.get_approval_key()
        self.price_codes = list(price_codes)
        self.orderbook_codes = list(orderbook_codes)
        self.tr_price = "H0STCNT0"  # 실시간 주식 체결가 (시세)
        self.tr_orderbook = "H0STASP0"  # 실시간 주식 호가 (시세)
        self.tr_notice = "H0STCNI0"  # 모의 : "H0STCNI9"  # 체결 통보
        self.simul_mode = simul_mode

        # 접속 함수를 여기서 한 번만 고른다. _session은 이게 진짜인지 모른다.
        # fake_kis는 테스트 전용이므로 필요할 때만 import (실전 배포에 불필요).
        if self.simul_mode:
            from kis import fake_kis_websocket as fake_kis
            self._connect = fake_kis.connect
        else:
            self._connect = websockets.connect
        self.log = logger or logging.getLogger(__name__)

        # 컨슈머별 독립 큐. 하나를 공유하면 데이터를 나눠 먹는다.
        self.consumer_queues = consumer_queues

        self._raw_q: asyncio.Queue[Tick] = asyncio.Queue(maxsize=max_pending)
        self._ack: dict[tuple[str, str], asyncio.Future] = {}
        self._dropped = 0
        self._stopping = asyncio.Event()

        # 프레임 폐기 통계 (연속 실패가 한도를 넘으면 세션을 재수립)
        self._known_tr_ids = {self.tr_price, self.tr_orderbook, self.tr_notice}
        self._consecutive_bad = 0
        self._total_bad = 0

        # tr_id -> (iv, key). 세션 한정. 재연결 시 비운다.
        self._crypto: dict[str, tuple[str, str]] = {}
        self._last_bad_log: dict[str, float] = {}

    def get_approval_key(self):
        url = f"{kis_config.domain}/oauth2/Approval"
        headers = {"content-type": "application/json"}
        body = {
            "grant_type": "client_credentials",
            "appkey": kis_config.APPKEY,
            "secretkey": kis_config.APPSECRET
        }
        res = requests.post(url, headers=headers, data=json.dumps(body))
        return res.json()["approval_key"]

    # ── 구독 목록 ────────────────────────────────────────────────
    def subscriptions(self) -> list[Subscription]:
        subs = [Subscription(self.tr_price, c) for c in self.price_codes]
        subs += [Subscription(self.tr_orderbook, c) for c in self.orderbook_codes]
        subs.append(Subscription(self.tr_notice, self.hts_id))

        if len(subs) > self.MAX_SUBSCRIPTIONS:
            raise ValueError(
                f"구독 {len(subs)}건 > 한도 {self.MAX_SUBSCRIPTIONS}건. "
                "세션을 분리하거나 종목을 줄이세요."
            )
        return subs

    def make_subscribe_msg(self, tr_type: str, sub: Subscription) -> str:
        return json.dumps({
            "header": {
                "approval_key": self.approval_key,
                "custtype": "P",
                "tr_type": tr_type,          # "1" 등록 / "2" 해지
                "content-type": "utf-8",
            },
            "body": {"input": {"tr_id": sub.tr_id, "tr_key": sub.tr_key}},
        })

    # ── 최상위 실행 루프 (재연결 담당) ───────────────────────────
    async def run(self) -> None:
        backoff = self.BASE_BACKOFF
        try:
            while not self._stopping.is_set():
                try:
                    await self._session()
                    backoff = self.BASE_BACKOFF          # 정상 세션 후 리셋
                except asyncio.CancelledError:
                    raise
                except (ConnectionClosed, OSError) as e:
                    self.log.warning("연결 끊김: %r → %.1fs 후 재연결", e, backoff)
                except Exception:
                    self.log.exception("세션 예외 → %.1fs 후 재연결", backoff)

                if self._stopping.is_set():
                    break

                # 지터를 섞어 동시 재접속 폭주를 방지
                await asyncio.sleep(backoff + random.uniform(0, backoff * 0.3))
                backoff = min(backoff * 2, self.MAX_BACKOFF)
        finally:
            self._shutdown_consumers()

    async def _session(self) -> None:
        # ping_interval=None: KIS는 표준 ping에 응답하지 않고
        # 애플리케이션 레벨 PINGPONG을 쓴다. 켜두면 라이브러리가
        # 'no close frame received'로 연결을 끊는다.
        async with self._connect(
            self.ws_url, ping_interval=None, close_timeout=5,
        ) as ws:
            self.log.info("WebSocket 연결됨: %s", self.ws_url)

            recv_task = asyncio.create_task(self._recv_loop(ws), name="recv")
            pump_task = asyncio.create_task(self._pump(), name="pump")
            try:
                # 구독 ACK는 recv 루프가 처리하므로 먼저 띄운 뒤 구독
                await self._subscribe_all(ws)
                done, pending = await asyncio.wait(
                    {recv_task, pump_task},
                    return_when=asyncio.FIRST_EXCEPTION,
                )
                for t in done:
                    t.result()                        # 예외를 밖으로 전파
            finally:
                for t in (recv_task, pump_task):
                    t.cancel()
                await asyncio.gather(recv_task, pump_task, return_exceptions=True)
                self._fail_pending_acks()

    # ── 구독 (ACK 대기) ──────────────────────────────────────────
    async def _subscribe_all(self, ws) -> None:
        for sub in self.subscriptions():
            ok = await self._subscribe_one(ws, sub)
            if not ok:
                self.log.warning("구독 ACK 미확인: %s %s (계속 진행)",
                                 sub.tr_id, sub.tr_key)
            await asyncio.sleep(self.SUBSCRIBE_GAP)

    async def _subscribe_one(self, ws, sub: Subscription) -> bool:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._ack[sub.ack_key] = fut
        try:
            await ws.send(self.make_subscribe_msg("1", sub))
            await asyncio.wait_for(fut, timeout=self.ACK_TIMEOUT)
            self.log.info("구독 완료: %s %s", sub.tr_id, sub.tr_key)
            return True
        except asyncio.TimeoutError:
            return False
        finally:
            self._ack.pop(sub.ack_key, None)

    def _fail_pending_acks(self) -> None:
        for fut in self._ack.values():
            if not fut.done():
                fut.cancel()
        self._ack.clear()

    # ── 수신 루프 (얇게 유지) ────────────────────────────────────
    async def _recv_loop(self, ws) -> None:
        """
        오류를 3등급으로 나눈다.
          · 연결 오류   → try 밖. 예외를 올려보내 재연결로 간다.
          · 프레임 1건  → 폐기하고 계속. 루프는 죽지 않는다.
          · 연속 폐기   → 프로토콜 이상. 예외를 올려 세션을 재수립한다.
        """
        self._consecutive_bad = 0
        while True:
            frame = await ws.recv()          # ConnectionClosed는 여기서 발생

            try:
                raw = self._decode(frame)
                kind, value = self._classify(raw)
                if kind == "tick":
                    self._enqueue(value)
                else:
                    await self._handle_control(ws, value)
                self._consecutive_bad = 0    # 정상 1건이면 연속 카운터 리셋
            except Exception as e:
                self._on_bad_frame(frame, e)  # 한도 초과 시 여기서 예외가 올라감

    # ── 프레임 검증: 봉투만 보고 내용물은 열지 않는다 ────────────
    def _decode(self, frame) -> str:
        if isinstance(frame, (bytes, bytearray)):
            # 서버가 바이너리 프레임으로 보낼 수 있다. int 인덱싱 오류 방지.
            frame = frame.decode("utf-8", errors="replace")
        if not isinstance(frame, str):
            raise TypeError(f"예상 밖 타입 {type(frame).__name__}")
        if not frame:
            raise ValueError("빈 프레임")
        if len(frame) > self.MAX_FRAME_BYTES:
            raise ValueError(f"과대 프레임 {len(frame)}바이트")
        return frame

    def _classify(self, raw: str) -> tuple[str, object]:
        head = raw[0]
        if head in "01":                     # 실시간: 0=평문 1=암호문
            return "tick", self._parse_envelope(raw)
        if head == "{":
            return "control", raw
        # 정체불명은 조용히 삼키지 않는다. else로 흘리면 사라진다.
        raise ValueError(f"분류 불가 (첫 글자 {head!r})")

    def _parse_envelope(self, raw: str) -> Tick:
        parts = raw.split("|", 3)
        if len(parts) != 4:
            raise ValueError(f"필드 {len(parts)}개 (4개 필요)")
        flag, tr_id, count, payload = parts
        if tr_id not in self._known_tr_ids:
            raise ValueError(f"미구독 tr_id {tr_id!r}")
        if not count.isdigit():
            raise ValueError(f"건수 비정상 {count!r}")
        if not payload:
            raise ValueError("본문 없음")
        # payload는 문자열 그대로 넘긴다. ^ 분해와 값 검증은 컨슈머 책임.
        encrypted = (flag == "1")
        iv = key = None
        if encrypted:
            pair = self._crypto.get(tr_id)
            if not pair:
                raise ValueError(f"{tr_id} 암호 프레임인데 키 없음")
            iv, key = pair
        return Tick(tr_id=tr_id, count=int(count), payload=payload,
                    encrypted=encrypted, iv=iv, key=key)

    def _on_bad_frame(self, frame, exc: Exception) -> None:
        self._consecutive_bad += 1
        self._total_bad += 1

        # 이상 프레임이 초당 수천 건 오면 로그가 디스크를 채운다 → 종류별 스로틀
        key = type(exc).__name__
        now = time.monotonic()
        if now - self._last_bad_log.get(key, 0.0) > self.BAD_LOG_INTERVAL:
            self._last_bad_log[key] = now
            self.log.warning("프레임 폐기(누적 %d건): %s | %.80r",
                             self._total_bad, exc, frame)

        if self._consecutive_bad >= self.MAX_CONSECUTIVE_BAD:
            # 한 건 깨지는 건 노이즈지만, 계속 깨지면 프로토콜이 바뀐 것이다.
            raise RuntimeError(
                f"연속 {self._consecutive_bad}건 폐기 → 세션 재수립"
            )

    async def _handle_control(self, ws, raw: str) -> None:
        # 여기서 나는 예외는 _recv_loop의 가드가 받아 폐기·집계한다.
        # 조용히 return하면 이상 프레임이 통계에 안 잡힌다.
        msg = json.loads(raw)

        header = msg.get("header", {})
        tr_id = header.get("tr_id")

        # PINGPONG은 반드시 되돌려줘야 서버가 연결을 유지한다
        if tr_id == "PINGPONG":
            await ws.pong(raw)
            return

        body = msg.get("body") or {}
        rt_cd = body.get("rt_cd")
        tr_key = header.get("tr_key")

        if rt_cd == "0":
            # 구독 응답에 복호화 재료가 실려 온다. 세션 한정이므로 메모리에만
            # 둔다. 파일로 남기면 재연결 후 옛 키를 쓰게 되고, 체결통보에는
            # 계좌·주문 정보가 들어 있어 평문 저장 자체가 위험하다.
            out = body.get("output") or {}
            iv, key = out.get("iv"), out.get("key")
            if iv and key:
                self._crypto[tr_id] = (iv, key)
                self.log.info("복호화 키 수신: %s", tr_id)
 
            fut = self._ack.get((tr_id, tr_key))
            if fut and not fut.done():
                fut.set_result(body)
            else:
                self.log.debug("매칭 안 된 성공 응답: %s", raw[:200])
        else:
            self.log.error("구독 실패 응답: %s", raw[:300])

    # ── asyncio.Queue → queue.Queue 브리지 ─────────────────────
    def _enqueue(self, tick: Tick) -> None:
        """루프를 절대 블로킹하지 않는다. 넘치면 버린다."""
        try:
            self._raw_q.put_nowait(tick)
        except asyncio.QueueFull:
            self._dropped += 1
            if self._dropped % 100 == 1:
                self.log.warning("수신 큐 포화, 누적 드롭 %d건", self._dropped)

    async def _pump(self) -> None:
        while True:
            tick = await self._raw_q.get()
            for name, q in self.consumer_queues.items():
                try:
                    q.put_nowait(tick)
                except queue.Full:
                    self.log.warning("[%s] 큐 포화, 틱 드롭", name)

    # ── 종료 ────────────────────────────────────────────────────
    def stop(self) -> None:
        self._stopping.set()

    def _shutdown_consumers(self) -> None:
        """스레드가 get()에서 영원히 대기하지 않도록 sentinel 투입."""
        for q in self.consumer_queues.values():
            try:
                q.put_nowait(SENTINEL)
            except queue.Full:
                pass


# ── 스레드 컨슈머 예시 ──────────────────────────────────────────
def consumer_worker(name: str, q: queue.Queue, handle) -> None:
    log = logging.getLogger(name)
    while True:
        item = q.get()
        if item is SENTINEL:
            log.info("[%s] 종료 신호 수신", name)
            break
        try:
            handle(item)          # DB 쓰기, 주문 등 블로킹 작업 OK
        except Exception:
            log.exception("[%s] 처리 실패", name)
        finally:
            q.task_done()


# ── 실행부: asyncio 루프 + 스레드 조립 ──────────────────────────
async def main(kis_config, record_handler, trade_handler) -> None:
    import signal
    import threading

    # 컨슈머별 독립 큐. maxsize를 주면 포화 시 드롭 정책이 작동한다.
    qs = {
        "recording": queue.Queue(maxsize=50_000),   # 유실 최소화 → 크게
        "trading": queue.Queue(maxsize=1_000),      # 최신성 우선 → 작게
    }

    feed = KisFeed(
        ws_url=kis_config.WS_URL,
        approval_key=kis_config.APPROVAL_KEY,
        hts_id=kis_config.HTS_ID,
        price_codes=["005930", "000660"],
        orderbook_codes=["005930"],
        consumer_queues=qs,
        tr_price="H0STCNT0",
        tr_orderbook="H0STASP0",
        tr_notice="H0STCNI0",
    )

    threads = [
        threading.Thread(target=consumer_worker, args=("recording", qs["recording"], record_handler), daemon=True),
        threading.Thread(target=consumer_worker, args=("trading", qs["trading"], trade_handler), daemon=True),
    ]
    for t in threads:
        t.start()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, feed.stop)     # Ctrl+C → 정상 종료

    try:
        await feed.run()
    finally:
        for t in threads:                          # sentinel 소진 대기
            t.join(timeout=10)