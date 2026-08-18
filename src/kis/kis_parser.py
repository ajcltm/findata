"""
KIS 실시간 데이터 파서 — dict 대신 dataclass.

설계 원칙
  · slots=True   : dict를 안 만들어 3.5배 빠르고 메모리 1/4. 오타 대입도 막힌다.
  · frozen 안 씀 : 46필드에서 frozen은 3배 느려진다(필드마다 __setattr__ 우회).
  · 필드 순서 = 전문 순서. 그래서 Cls(*fields)로 한 번에 만들 수 있고,
                 개수가 안 맞으면 그 자리에서 TypeError가 난다.
                 (dict 방식은 한 칸씩 밀린 채 조용히 통과했다)
  · 숫자 변환은 선택 : to_typed()를 부를 때만. recording은 문자열 그대로 저장해도
                 되고, trading만 숫자가 필요한 경우가 많다.
"""

from __future__ import annotations

import datetime
import logging
from base64 import b64decode
from dataclasses import dataclass, fields as dc_fields
from typing import ClassVar

from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad


# ── 실시간 체결가 H0STCNT0 (46필드) ─────────────────────────────
@dataclass(slots=True)
class Execution:
    stock_code: str                  # MKSC_SHRN_ISCD 종목코드
    execution_time: str              # STCK_CNTG_HOUR 체결시간
    current_price: str               # STCK_PRPR 현재가
    price_diff_sign: str             # PRDY_VRSS_SIGN 전일대비 부호
    price_diff: str                  # PRDY_VRSS 전일대비
    price_change_rate: str           # PRDY_CTRT 전일대비율
    vwap: str                        # WGHN_AVRG_STCK_PRC 가중평균가
    open_price: str                  # STCK_OPRC 시가
    high_price: str                  # STCK_HGPR 고가
    low_price: str                   # STCK_LWPR 저가
    ask_price_1: str                 # ASKP1 매도호가1
    bid_price_1: str                 # BIDP1 매수호가1
    tick_volume: str                 # CNTG_VOL 체결거래량
    acc_volume: str                  # ACML_VOL 누적거래량
    acc_trade_value: str             # ACML_TR_PBMN 누적거래대금
    sell_execution_cnt: str          # SELN_CNTG_CSNU 매도체결건수
    buy_execution_cnt: str           # SHNU_CNTG_CSNU 매수체결건수
    net_buy_cnt: str                 # NTBY_CNTG_CSNU 순매수체결건수
    volume_power: str                # CTTR 체결강도
    total_sell_qty: str              # SELN_CNTG_SMTN 총매도수량
    total_buy_qty: str               # SHNU_CNTG_SMTN 총매수수량
    exec_division: str               # CCLD_DVSN 체결구분
    buy_rate: str                    # SHNU_RATE 매수비율
    vol_diff_rate: str               # PRDY_VOL_VRSS_ACML_VOL_RATE 거래량 등락율
    open_time: str                   # OPRC_HOUR 시가시간
    open_diff_sign: str              # OPRC_VRSS_PRPR_SIGN 시가대비구분
    open_diff: str                   # OPRC_VRSS_PRPR 시가대비
    high_time: str                   # HGPR_HOUR 최고가시간
    high_diff_sign: str              # HGPR_VRSS_PRPR_SIGN 고가대비구분
    high_diff: str                   # HGPR_VRSS_PRPR 고가대비
    low_time: str                    # LWPR_HOUR 최저가시간
    low_diff_sign: str               # LWPR_VRSS_PRPR_SIGN 저가대비구분
    low_diff: str                    # LWPR_VRSS_PRPR 저가대비
    business_date: str               # BSOP_DATE 영업일자
    market_op_code: str              # NEW_MKOP_CLS_CODE 장운영구분코드
    is_suspended: str                # TRHT_YN 거래정지여부
    ask_rsvp_1: str                  # ASKP_RSQN1 매도호가잔량1
    bid_rsvp_1: str                  # BIDP_RSQN1 매수호가잔량1
    total_ask_rsvp: str              # TOTAL_ASKP_RSQN 총매도호가잔량
    total_bid_rsvp: str              # TOTAL_BIDP_RSQN 총매수호가잔량
    vol_turnover_rate: str           # VOL_TNRT 거래량회전율
    prev_same_time_vol: str          # PRDY_SMNS_HOUR_ACML_VOL 전일동시간누적거래량
    prev_same_time_vol_rate: str     # PRDY_SMNS_HOUR_ACML_VOL_RATE 그 비율
    hour_cls_code: str               # HOUR_CLS_CODE 시간구분코드
    market_term_code: str            # MRKT_TRTM_CLS_CODE 임의종료구분코드
    vi_standard_price: str           # VI_STND_PRC 정적VI발동기준가
    datetime: str = ""                # 수신 시각 (전문에 없음, 파서가 채움)

    N_FIELDS: ClassVar[int] = 46
    INT_FIELDS: ClassVar[tuple[str, ...]] = (
        "current_price", "open_price", "high_price", "low_price",
        "ask_price_1", "bid_price_1", "tick_volume", "acc_volume",
    )
    FLOAT_FIELDS: ClassVar[tuple[str, ...]] = ("price_change_rate", "volume_power")


# ── 실시간 호가 H0STASP0 (59필드) ───────────────────────────────
@dataclass(slots=True)
class OrderBook:
    stock_code: str
    business_hour: str               # BSOP_HOUR 영업시간
    hour_cls_code: str               # HOUR_CLS_CODE 시간구분코드
    # 매도호가 1~10
    ask_price_1: str; ask_price_2: str; ask_price_3: str; ask_price_4: str
    ask_price_5: str; ask_price_6: str; ask_price_7: str; ask_price_8: str
    ask_price_9: str; ask_price_10: str
    # 매수호가 1~10
    bid_price_1: str; bid_price_2: str; bid_price_3: str; bid_price_4: str
    bid_price_5: str; bid_price_6: str; bid_price_7: str; bid_price_8: str
    bid_price_9: str; bid_price_10: str
    # 매도호가 잔량 1~10
    ask_rsvp_1: str; ask_rsvp_2: str; ask_rsvp_3: str; ask_rsvp_4: str
    ask_rsvp_5: str; ask_rsvp_6: str; ask_rsvp_7: str; ask_rsvp_8: str
    ask_rsvp_9: str; ask_rsvp_10: str
    # 매수호가 잔량 1~10
    bid_rsvp_1: str; bid_rsvp_2: str; bid_rsvp_3: str; bid_rsvp_4: str
    bid_rsvp_5: str; bid_rsvp_6: str; bid_rsvp_7: str; bid_rsvp_8: str
    bid_rsvp_9: str; bid_rsvp_10: str
    total_ask_rsvp: str              # TOTAL_ASKP_RSQN 총매도호가잔량
    total_bid_rsvp: str              # TOTAL_BIDP_RSQN 총매수호가잔량
    ovtm_total_ask_rsvp: str         # OVTM_TOTAL_ASKP_RSQN 시간외 총매도잔량
    ovtm_total_bid_rsvp: str         # OVTM_TOTAL_BIDP_RSQN 시간외 총매수잔량
    est_exec_price: str              # ANTC_CNPR 예상체결가
    est_exec_qty: str                # ANTC_CNQN 예상체결량
    est_vol: str                     # ANTC_VOL 예상거래량
    est_price_diff: str              # ANTC_CNTG_VRSS 예상체결대비
    est_price_sign: str              # ANTC_CNTG_VRSS_SIGN 예상체결대비부호
    est_price_rate: str              # ANTC_CNTG_PRDY_CTRT 예상체결 전일대비율
    acc_vol: str                     # ACML_VOL 누적거래량
    total_ask_rsvp_icdc: str         # TOTAL_ASKP_RSQN_ICDC 총매도잔량증감
    total_bid_rsvp_icdc: str         # TOTAL_BIDP_RSQN_ICDC 총매수잔량증감
    ovtm_total_ask_icdc: str         # OVTM_TOTAL_ASKP_ICDC 시간외 총매도증감
    ovtm_total_bid_icdc: str         # OVTM_TOTAL_BIDP_ICDC 시간외 총매수증감
    trade_cls_code: str              # STCK_DEAL_CLS_CODE 매매구분코드
    datetime: str = ""

    N_FIELDS: ClassVar[int] = 59
    INT_FIELDS: ClassVar[tuple[str, ...]] = (
        "ask_price_1", "bid_price_1", "ask_rsvp_1", "bid_rsvp_1",
        "total_ask_rsvp", "total_bid_rsvp",
    )
    FLOAT_FIELDS: ClassVar[tuple[str, ...]] = ()


# ── 체결 통보 H0STCNI0 ──────────────────────────────────────────
@dataclass(slots=True)
class Notice:
    CUST_ID: str    #고객 ID
    ACNT_NO: str    #계좌번호
    ODER_NO: str    #주문번호
    OODER_NO: str    #원주문번호
    SELN_BYOV_CLS: str    #매도매수구분
    RCTF_CLS: str    #정정구분
    ODER_KIND: str    #주문종류
    ODER_COND: str    #주문조건
    STCK_SHRN_ISCD: str    #주식 단축 종목코드
    CNTG_QTY: str    #체결 수량
    CNTG_UNPR: str    #체결단가
    STCK_CNTG_HOUR: str    #주식 체결 시간
    RFUS_YN: str    #거부여부
    CNTG_YN: str    #체결여부
    ACPT_YN: str    #접수여부
    BRNC_NO: str    #지점번호
    ODER_QTY: str    #주문수량
    ACNT_NAME: str    #계좌명
    ORD_COND_PRC: str    #호가조건가격
    ORD_EXG_GB: str    #주문거래소 구분
    POPUP_YN: str    #실시간체결창 표시여부
    FILLER: str    #필러
    CRDT_CLS: str    #신용구분
    CRDT_LOAN_DATE: str    #신용대출일자
    CNTG_ISNM40: str    #체결종목명
    ODER_PRC: str    #주문가격
    datetime: str = ""

    # ⚠️ 체결통보는 계정 유형(실전/모의)에 따라 필드 수가 다르다는 보고가 있다.
    #    None이면 개수 검증을 건너뛰고 앞에서부터 채운다. 실제 전문을 한 번
    #    찍어보고 확정한 뒤 숫자를 넣는 것을 권장.
    N_FIELDS: ClassVar[int] = 26
    INT_FIELDS: ClassVar[tuple[str, ...]] = ("executed_qty", "executed_price",
                                             "order_qty", "order_price")
    FLOAT_FIELDS: ClassVar[tuple[str, ...]] = ()


@dataclass(slots=True)
class ParsedTick:
    tr_id: str
    data: list                       # Execution | OrderBook | Notice 리스트


class ParseError(ValueError):
    """전문 형식이 예상과 다름. 상위에서 폐기·집계 대상."""


class KISParser:
    """tr_id -> dataclass 매핑. 새 TR을 추가하려면 이 표에만 등록하면 된다."""

    REGISTRY = {
        "H0STCNT0": Execution,
        "H0STASP0": OrderBook,
        "H0STCNI0": Notice,
    }

    def __init__(self, logger: logging.Logger | None = None):
        self.logger = logger or logging.getLogger("kis.parser")

    # ── 진입점 ─────────────────────────────────────────────────
    def parse(self, tick) -> ParsedTick | None:
        cls = self.REGISTRY.get(tick.tr_id)
        if cls is None:
            self.logger.warning("알 수 없는 TR ID: %s", tick.tr_id)
            return None
        
        try:
            body = tick.payload
            if tick.encrypted:
                body = self.decrypt(key=tick.key, iv=tick.iv, cipher_text_b64=body)
            records = [cls(*chunk[:cls.N_FIELDS], datetime=self.now())
                       for chunk in self.chunks(body, tick.count, cls)]
            return ParsedTick(tr_id=tick.tr_id, data=records)

        except Exception as e:
            # payload 전체를 로그에 남기면 체결통보의 계좌정보가 평문으로 남는다.
            self.logger.error("파싱 실패 tr_id=%s count=%s: %s | %s",
                              tick.tr_id, tick.count, e, tick)
            return None

    # ── 레코드 분할 ────────────────────────────────────────────
    def chunks(self, raw: str, count, cls) -> list[list[str]]:
        n = int(count)
        fields = raw.split("^")

        if n <= 1:
            self._check_size(len(fields), cls)
            return [fields]

        if len(fields) % n:
            raise ParseError(f"필드 {len(fields)}개가 건수 {n}으로 안 나뉨")

        size = len(fields) // n
        self._check_size(size, cls)
        return [fields[i * size:(i + 1) * size] for i in range(n)]

    # @staticmethod
    # def _check_size(size: int, cls) -> None:
    #     """
    #     여기서 막지 않으면 필드가 한 칸씩 밀린 채 통과한다.
    #     가격 자리에 거래량이 들어가도 프로그램은 멀쩡히 돈다 — 최악의 경우다.
    #     """
    #     expected = cls.N_FIELDS
    #     if expected is not None and size < expected:
    #         raise ParseError(f"{cls.__name__} 필드 {size}개 (최소 {expected}개 필요)")

    #     # 전문 필드 + recv_at(파서가 채움) 이므로 -1
    #     capacity = len(dc_fields(cls)) - 1
    #     if size > capacity:
    #         raise ParseError(f"{cls.__name__} 정의 {capacity}칸에 {size}개 못 담음")

    @staticmethod
    def _check_size(size: int, cls) -> None:
        expected = cls.N_FIELDS
        if expected is not None:
            if size < expected:
                raise ParseError(f"{cls.__name__} 필드 {size}개 (최소 {expected}개 필요)")
            return

        capacity = len(dc_fields(cls)) - 1
        if size > capacity:
            raise ParseError(f"{cls.__name__} 정의 {capacity}칸에 {size}개 못 담음")

    # ── 복호화 ─────────────────────────────────────────────────
    @staticmethod
    def decrypt(key: str, iv: str, cipher_text_b64: str) -> str:
        if not key or not iv:
            raise ParseError("복호화 키 없음")
        cipher = AES.new(key.encode("utf-8"), AES.MODE_CBC, iv.encode("utf-8"))
        return unpad(cipher.decrypt(b64decode(cipher_text_b64)),
                     AES.block_size).decode("utf-8")

    @staticmethod
    def now() -> str:
        return datetime.datetime.now().isoformat(timespec="seconds")


# ── 선택적 타입 변환 ────────────────────────────────────────────
def to_typed(obj):
    """
    필요한 필드만 숫자로 바꾼다. 제자리 수정이므로 반환값을 안 써도 된다.
    recording은 문자열 그대로 저장해도 되니, trading 스레드에서만 부르면 된다.
    빈 문자열은 0으로 (장 시작 전 전문에 빈 칸이 온다).
    """
    for name in obj.INT_FIELDS:
        v = getattr(obj, name)
        if isinstance(v, str):
            setattr(obj, name, int(v) if v.strip() else 0)
    for name in obj.FLOAT_FIELDS:
        v = getattr(obj, name)
        if isinstance(v, str):
            setattr(obj, name, float(v) if v.strip() else 0.0)
    return obj


def to_dict(obj) -> dict:
    """DB·pandas로 넘길 때. slots라서 asdict()보다 이쪽이 빠르다."""
    return {f.name: getattr(obj, f.name) for f in dc_fields(obj)}