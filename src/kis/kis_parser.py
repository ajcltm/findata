from dataclasses import dataclass
import datetime
import logging

from base64 import b64decode
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

@dataclass
class ParsedTick:
    tr_id: str
    data: list

class KISParser:

    def __init__(self):
        self.logger = logging.getLogger("kis")
       
    def parse(self, tick):
        try:
            
            if tick.tr_id == "H0STCNT0":  # 시세
                if not tick.encrypted:
                    return self.parse_execution(tick.payload, tick.count)
                decrypted = self.aes256_cbc_base64_decrypt(iv=tick.iv, key=tick.key, cipher_text_b64=tick.payload)
                return self.parse_execution(decrypted, tick.count)

            elif tick.tr_id == "H0STASP0":  # 호가
                if not tick.encrypted:
                    return self.parse_orderbook(tick.payload, tick.count)
                decrypted = self.aes256_cbc_base64_decrypt(iv=tick.iv, key=tick.key, cipher_text_b64=tick.payload)
                return self.parse_orderbook(decrypted, tick.count)

            elif tick.tr_id == "H0STCNI0":  # 체결 통보
                if not tick.encrypted:
                    return self.parse_notice(tick.payload, tick.count)

                decrypted = self.aes256_cbc_base64_decrypt(iv=tick.iv, key=tick.key, cipher_text_b64=tick.payload)
                return self.parse_notice(decrypted, tick.count)

            else:
                print(f"⚠️ 알 수 없는 TR ID: {tick.tr_id}")
                return None

        except Exception as e:
            self.logger.exception(
                "파싱 실패 tr_id=%s count=%s",
                getattr(tick, "tr_id", "?"), getattr(tick, "count", "?"))
            return None
    
    def parse_execution(self, data_str, data_count):
        """체결가 데이터 파싱"""
        chunk = self.get_chunk_data_str(data_str, data_count)
        parsed_data = []
        for f in chunk:
            # 실시간 시세/체결 데이터 f[0] ~ f[45] 매핑
            parsed = {
                'datetime' : self.now(),
                'stock_code': f[0],               # MKSC_SHRN_ISCD: 유가증권 단축 종목코드
                'execution_time': f[1],           # STCK_CNTG_HOUR: 주식 체결 시간
                'current_price': f[2],            # STCK_PRPR: 주식 현재가
                'price_diff_sign': f[3],          # PRDY_VRSS_SIGN: 전일 대비 부호
                'price_diff': f[4],               # PRDY_VRSS: 전일 대비
                'price_change_rate': f[5],        # PRDY_CTRT: 전일 대비율
                'vwap': f[6],                     # WGHN_AVRG_STCK_PRC: 가중 평균 주식 가격
                'open_price': f[7],               # STCK_OPRC: 주식 시가
                'high_price': f[8],               # STCK_HGPR: 주식 최고가
                'low_price': f[9],                # STCK_LWPR: 주식 최저가
                'ask_price_1': f[10],             # ASKP1: 매도호가1
                'bid_price_1': f[11],             # BIDP1: 매수호가1
                'tick_volume': f[12],             # CNTG_VOL: 체결 거래량
                'acc_volume': f[13],              # ACML_VOL: 누적 거래량
                'acc_trade_value': f[14],         # ACML_TR_PBMN: 누적 거래 대금
                'sell_execution_cnt': f[15],      # SELN_CNTG_CSNU: 매도 체결 건수
                'buy_execution_cnt': f[16],       # SHNU_CNTG_CSNU: 매수 체결 건수
                'net_buy_cnt': f[17],             # NTBY_CNTG_CSNU: 순매수 체결 건수
                'volume_power': f[18],            # CTTR: 체결강도
                'total_sell_qty': f[19],          # SELN_CNTG_SMTN: 총 매도 수량
                'total_buy_qty': f[20],           # SHNU_CNTG_SMTN: 총 매수 수량
                'exec_division': f[21],           # CCLD_DVSN: 체결구분
                'buy_rate': f[22],                # SHNU_RATE: 매수비율
                'vol_diff_rate': f[23],           # PRDY_VOL_VRSS_ACML_VOL_RATE: 전일 거래량 대비 등락율
                'open_time': f[24],               # OPRC_HOUR: 시가 시간
                'open_diff_sign': f[25],          # OPRC_VRSS_PRPR_SIGN: 시가대비구분
                'open_diff': f[26],               # OPRC_VRSS_PRPR: 시가대비
                'high_time': f[27],               # HGPR_HOUR: 최고가 시간
                'high_diff_sign': f[28],          # HGPR_VRSS_PRPR_SIGN: 고가대비구분
                'high_diff': f[29],               # HGPR_VRSS_PRPR: 고가대비
                'low_time': f[30],                # LWPR_HOUR: 최저가 시간
                'low_diff_sign': f[31],           # LWPR_VRSS_PRPR_SIGN: 저가대비구분
                'low_diff': f[32],                # LWPR_VRSS_PRPR: 저가대비
                'business_date': f[33],           # BSOP_DATE: 영업 일자
                'market_op_code': f[34],          # NEW_MKOP_CLS_CODE: 신 장운영 구분 코드
                'is_suspended': f[35],            # TRHT_YN: 거래정지 여부
                'ask_rsvp_1': f[36],              # ASKP_RSQN1: 매도호가 잔량1
                'bid_rsvp_1': f[37],              # BIDP_RSQN1: 매수호가 잔량1
                'total_ask_rsvp': f[38],          # TOTAL_ASKP_RSQN: 총 매도호가 잔량
                'total_bid_rsvp': f[39],          # TOTAL_BIDP_RSQN: 총 매수호가 잔량
                'vol_turnover_rate': f[40],       # VOL_TNRT: 거래량 회전율
                'prev_same_time_vol': f[41],      # PRDY_SMNS_HOUR_ACML_VOL: 전일 동시간 누적 거래량
                'prev_same_time_vol_rate': f[42], # PRDY_SMNS_HOUR_ACML_VOL_RATE: 전일 동시간 누적 거래량 비율
                'hour_cls_code': f[43],           # HOUR_CLS_CODE: 시간 구분 코드
                'market_term_code': f[44],        # MRKT_TRTM_CLS_CODE: 임의종료구분코드
                'vi_standard_price': f[45]        # VI_STND_PRC: 정적VI발동기준가
            }
            parsed_data.append(parsed)
        return ParsedTick(tr_id="H0STCNT0", data=parsed_data)
        
    def parse_orderbook(self, data_str, data_count):
        """호가 데이터 파싱"""
        chunk = self.get_chunk_data_str(data_str, data_count)
        parsed_data = []
        for f in chunk:
            prased = {
                'datetime' : self.now(),
                'stock_code': f[0],               # MKSC_SHRN_ISCD: 유가증권 단축 종목코드
                'business_hour': f[1],            # BSOP_HOUR: 영업 시간
                'hour_cls_code': f[2],            # HOUR_CLS_CODE: 시간 구분 코드
                
                # 매도호가 1~10 (Ask Prices)
                'ask_price_1': f[3], 'ask_price_2': f[4], 'ask_price_3': f[5], 'ask_price_4': f[6], 'ask_price_5': f[7],
                'ask_price_6': f[8], 'ask_price_7': f[9], 'ask_price_8': f[10], 'ask_price_9': f[11], 'ask_price_10': f[12],
                
                # 매수호가 1~10 (Bid Prices)
                'bid_price_1': f[13], 'bid_price_2': f[14], 'bid_price_3': f[15], 'bid_price_4': f[16], 'bid_price_5': f[17],
                'bid_price_6': f[18], 'bid_price_7': f[19], 'bid_price_8': f[20], 'bid_price_9': f[21], 'bid_price_10': f[22],
                
                # 매도호가 잔량 1~10 (Ask Remaining Quantities)
                'ask_rsvp_1': f[23], 'ask_rsvp_2': f[24], 'ask_rsvp_3': f[25], 'ask_rsvp_4': f[26], 'ask_rsvp_5': f[27],
                'ask_rsvp_6': f[28], 'ask_rsvp_7': f[29], 'ask_rsvp_8': f[30], 'ask_rsvp_9': f[31], 'ask_rsvp_10': f[32],
                
                # 매수호가 잔량 1~10 (Bid Remaining Quantities)
                'bid_rsvp_1': f[33], 'bid_rsvp_2': f[34], 'bid_rsvp_3': f[35], 'bid_rsvp_4': f[36], 'bid_rsvp_5': f[37],
                'bid_rsvp_6': f[38], 'bid_rsvp_7': f[39], 'bid_rsvp_8': f[40], 'bid_rsvp_9': f[41], 'bid_rsvp_10': f[42],
                
                # 총 잔량 및 시간외 잔량
                'total_ask_rsvp': f[43],          # TOTAL_ASKP_RSQN: 총 매도호가 잔량
                'total_bid_rsvp': f[44],          # TOTAL_BIDP_RSQN: 총 매수호가 잔량
                'ovtm_total_ask_rsvp': f[45],     # OVTM_TOTAL_ASKP_RSQN: 시간외 총 매도호가 잔량
                'ovtm_total_bid_rsvp': f[46],     # OVTM_TOTAL_BIDP_RSQN: 시간외 총 매수호가 잔량
                
                # 예상 체결 정보 (Anticipated/Estimated)
                'est_exec_price': f[47],          # ANTC_CNPR: 예상 체결가
                'est_exec_qty': f[48],            # ANTC_CNQN: 예상 체결량
                'est_vol': f[49],                 # ANTC_VOL: 예상 거래량
                'est_price_diff': f[50],          # ANTC_CNTG_VRSS: 예상 체결 대비
                'est_price_sign': f[51],          # ANTC_CNTG_VRSS_SIGN: 예상 체결 대비 부호
                'est_price_rate': f[52],          # ANTC_CNTG_PRDY_CTRT: 예상 체결 전일 대비율
                
                # 누적 및 증감 정보
                'acc_vol': f[53],                 # ACML_VOL: 누적 거래량
                'total_ask_rsvp_icdc': f[54],     # TOTAL_ASKP_RSQN_ICDC: 총 매도호가 잔량 증감
                'total_bid_rsvp_icdc': f[55],     # TOTAL_BIDP_RSQN_ICDC: 총 매수호가 잔량 증감
                'ovtm_total_ask_icdc': f[56],     # OVTM_TOTAL_ASKP_ICDC: 시간외 총 매도호가 증감
                'ovtm_total_bid_icdc': f[57],     # OVTM_TOTAL_BIDP_ICDC: 시간외 총 매수호가 증감
                'trade_cls_code': f[58]           # STCK_DEAL_CLS_CODE: 주식 매매 구분 코드
            }
            parsed_data.append(prased)
        return ParsedTick(tr_id="H0STASP0", data=parsed_data)
    
    def parse_notice(self, data_str, data_count):
        """체결 통보 데이터 파싱"""
        """체결가 데이터 파싱"""
        chunk = self.get_chunk_data_str(data_str, data_count)
        parsed_data = []
        for f in chunk:
            now = datetime.datetime.now().isoformat(timespec="seconds")
            parsed = {
                    'datetime' : self.now(),
                    'execution_time': f[11],        # STCK_CNTG_HOUR: 주식 체결 시간
                    'customer_id': f[0],             # CUST_ID: 고객 ID
                    'account_no': f[1],              # ACNT_NO: 계좌번호
                    'order_no': f[2],               # ODER_NO: 주문번호
                    'org_order_no': f[3],           # OODER_NO: 원주문번호
                    'side': f[4],                    # SELN_BYOV_CLS: 매도매수구분 (Buy/Sell)
                    'correction_type': f[5],        # RCTF_CLS: 정정구분
                    'order_type': f[6],             # ODER_KIND: 주문종류
                    'order_condition': f[7],        # ODER_COND: 주문조건
                    'stock_code': f[8],             # STCK_SHRN_ISCD: 주식 단축 종목코드
                    'stock_name': f[-2],            # CNTG_ISNM40: 체결종목명
                    'executed_qty': f[9],           # CNTG_QTY: 체결 수량
                    'executed_price': f[10],        # CNTG_UNPR: 체결단가
                    'order_price': f[-1],            # ODER_PRC: 주문가격
                    'is_rejected': f[12],           # RFUS_YN: 거부여부
                    'is_executed': f[13],           # CNTG_YN: 체결여부
                    'is_accepted': f[14],           # ACPT_YN: 접수여부
                    'branch_no': f[15],             # BRNC_NO: 지점번호
                    'order_qty': f[16],             # ODER_QTY: 주문수량
                    'account_name': f[17],          # ACNT_NAME: 계좌명
                    # 'ask_bid_price': f[18],         # ORD_COND_PRC: 호가조건가격
                    # 'exchange_code': f[19],         # ORD_EXG_GB: 주문거래소 구분
                    # 'show_popup': f[20],            # POPUP_YN: 실시간체결창 표시여부
                    # 'filler': f[21],                # FILLER: 필러
                    # 'credit_type': f[22],           # CRDT_CLS: 신용구분
                    # 'loan_date': f[23],             # CRDT_LOAN_DATE: 신용대출일자
                }
            parsed_data.append(parsed)
        return ParsedTick(tr_id="H0STCNI0", data=parsed_data)
    
    def now(self):
        return datetime.datetime.now().isoformat(timespec="seconds")
    
    def get_chunk_data_str(self, raw_data: str, data_count: int):
        n = int(data_count)
        fields = raw_data.split("^")

        # # 끝에 '^'가 있으면 split 결과 마지막이 ''로 들어올 수 있어서 제거
        # if fields and fields[-1] == "":
        #     fields = fields[:-1]

        if n == 0 :
            return [fields]

        if len(fields) % n != 0:
            raise ValueError(f"필드 수({len(fields)})가 건수({n})로 나누어 떨어지지 않음")

        record_size = len(fields) // n

        chunks = []
        for i in range(n):
            s = i * record_size
            e = s + record_size
            chunks.append(fields[s:e])

        return chunks
    
    def aes256_cbc_base64_decrypt(self, key: str, iv: str, cipher_text_b64: str) -> str:
        """
        key: 구독 성공 응답의 output.key (문자열)
        iv : 구독 성공 응답의 output.iv  (문자열)
        cipher_text_b64: 실시간 메시지의 마지막 필드(암호문, base64)
        """
        cipher = AES.new(key.encode("utf-8"), AES.MODE_CBC, iv.encode("utf-8"))
        plain_bytes = unpad(cipher.decrypt(b64decode(cipher_text_b64)), AES.block_size)
        return plain_bytes.decode("utf-8")