import requests
from kis import kis_config
from datetime import datetime, timedelta

def create_hashkey(json_body=None):
    """
    [Hashkey] POST 주문/정정/취소 등에서 Request Body 변조 방지용 hashkey(HASH) 생성
    - METHOD/URL: POST /uapi/hashkey
    - 핵심: data(JsonBody)는 "해쉬를 만들고 싶은 원래 POST 요청 body" 그대로 넣어야 함
    """

    # JsonBody (required): 해쉬 생성 대상이 되는 "원래 POST API의 Body"
    if json_body:
        json_body["CANO"] = kis_config.CANO
        json_body["ACNT_PRDT_CD"] = kis_config.ACNT_PRDT_CD

    url = f"{kis_config.domain}/uapi/hashkey"

    headers = {
        # content-type은 시트에서 Required=N 이지만, 일반적으로 넣는 것을 권장
        "content-type": "application/json; charset=utf-8",  # 옵션: "application/json"
        "appkey": kis_config.APPKEY,
        "appsecret": kis_config.APPSECRET,
    }
    params = {}

    data = {
        "JsonBody": json_body,
    }

    r = requests.post(url, headers=headers, params=params, json=data, timeout=10)
    return r.json()

def websocket_approval_key():
    """
    [실시간 (웹소켓) 접속키 발급] WebSocket 구독을 위한 approval_key 발급
    - METHOD/URL: POST /oauth2/Approval
    """
    # ====== required (시트: Request Body) ======
    grant_type = "client_credentials"  # 옵션: 고정값(문서상 client_credentials)

    url = f"{kis_config.domain}/oauth2/Approval"

    headers = {
        # content-type은 시트에서 Required=N 이지만, 일반적으로 넣는 것을 권장
        "content-type": "application/json; utf-8",  # 옵션: "application/json"
    }
    params = {}

    data = {
        "grant_type": grant_type,
        "appkey": kis_config.APPKEY,
        "secretkey": kis_config.APPSECRET,
    }

    r = requests.post(url, headers=headers, params=params, json=data, timeout=10)
    return r.json()

def domestic_stock_inquire_price():
    """
    [주식현재가 시세]
    - METHOD: GET
    - URL: /uapi/domestic-stock/v1/quotations/inquire-price
    - TR_ID(실전): FHKST01010100
    - API ID: v1_국내주식-008
    """

    url = f"{kis_config.domain}/uapi/domestic-stock/v1/quotations/inquire-price"

    headers = {
        "content-type": 'application/json; charset=utf-8',  # application/json; charset=utf-8
        "authorization": f"Bearer {kis_config.ACCESS_TOKEN}",  # OAuth 토큰이 필요한 API 경우 발급한 Access token   일반고객(Access token 유효기간 1일, OAuth 2.0의 Client Crede...
        "appkey": kis_config.APPKEY,  # 한국투자증권 홈페이지에서 발급받은 appkey (절대 노출되지 않도록 주의해주세요.)
        "appsecret": kis_config.APPSECRET,  # 한국투자증권 홈페이지에서 발급받은 appkey (절대 노출되지 않도록 주의해주세요.)
        "tr_id": 'FHKST01010100',  # FHKST01010100
        "custtype": 'P',  # B : 법인   P : 개인
    }

    params = {
        "FID_COND_MRKT_DIV_CODE": 'J',  # J:KRX, NX:NXT, UN:통합
        "FID_INPUT_ISCD": '005930',  # 종목코드 (ex 005930 삼성전자)  // ETN은 종목코드 6자리 앞에 Q 입력 필수
    }

    data = {
        # (required body 없음)
    }

    r = requests.get(url, headers=headers, params=params, timeout=10)
    return r.json()


def domestic_stock_inquire_price_2():
    """
    [주식현재가 시세2]
    - METHOD: GET
    - URL: /uapi/domestic-stock/v1/quotations/inquire-price-2
    - TR_ID(실전): FHPST01010000
    - API ID: v1_국내주식-054
    """

    url = f"{kis_config.domain}/uapi/domestic-stock/v1/quotations/inquire-price-2"

    headers = {
        "content-type": 'application/json; charset=utf-8',  # application/json; charset=utf-8
        "authorization": f"Bearer {kis_config.ACCESS_TOKEN}",  # OAuth 토큰이 필요한 API 경우 발급한 Access token   일반고객(Access token 유효기간 1일, OAuth 2.0의 Client Crede...
        "appkey": kis_config.APPKEY,  # 한국투자증권 홈페이지에서 발급받은 appkey (절대 노출되지 않도록 주의해주세요.)
        "appsecret": kis_config.APPSECRET,  # 한국투자증권 홈페이지에서 발급받은 appkey (절대 노출되지 않도록 주의해주세요.)
        "tr_id": 'FHPST01010000',  # FHPST01010000
        "custtype": 'P',  # B : 법인   P : 개인
    }

    params = {
        "FID_COND_MRKT_DIV_CODE": 'J',  # J:KRX, NX:NXT, UN:통합
        "FID_INPUT_ISCD": '000660',  # 000660
    }

    data = {
        # (required body 없음)
    }

    r = requests.get(url, headers=headers, params=params, timeout=10)
    return r.json()

def domestic_stock_inquire_asking_price_exp_ccn():
    """
    [주식현재가 호가_예상체결]
    - METHOD: GET
    - URL: /uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn
    - TR_ID(실전): FHKST01010200
    - API ID: v1_국내주식-011
    """

    url = f"{kis_config.domain}/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn"

    headers = {
        "content-type": 'application/json; charset=utf-8',  # application/json; charset=utf-8
        "authorization": f"Bearer {kis_config.ACCESS_TOKEN}",  # OAuth 토큰이 필요한 API 경우 발급한 Access token   일반고객(Access token 유효기간 1일, OAuth 2.0의 Client Crede...
        "appkey": kis_config.APPKEY,  # 한국투자증권 홈페이지에서 발급받은 appkey (절대 노출되지 않도록 주의해주세요.)
        "appsecret": kis_config.APPSECRET,  # 한국투자증권 홈페이지에서 발급받은 appkey (절대 노출되지 않도록 주의해주세요.)
        "tr_id": 'FHKST01010200',  # FHKST01010200
        "custtype": 'P',  # B : 법인   P : 개인
    }

    params = {
        "FID_COND_MRKT_DIV_CODE": 'J',  # J:KRX, NX:NXT, UN:통합
        "FID_INPUT_ISCD": '005930',  # 종목코드 (ex 005930 삼성전자)
    }

    data = {
        # (required body 없음)
    }

    r = requests.get(url, headers=headers, params=params, timeout=10)
    return r.json()

def domestic_stock_inquire_daily_itemchartprice():
    """
    [국내주식기간별시세(일_주_월_년)]
    - METHOD: GET
    - URL: /uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice
    - TR_ID(실전): FHKST03010100
    - API ID: v1_국내주식-014
    """

    url = f"{kis_config.domain}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"

    headers = {
        "content-type": 'application/json; charset=utf-8',  # application/json; charset=utf-8
        "authorization": f"Bearer {kis_config.ACCESS_TOKEN}",  # OAuth 토큰이 필요한 API 경우 발급한 Access token   일반고객(Access token 유효기간 1일, OAuth 2.0의 Client Crede...
        "appkey": kis_config.APPKEY,  # 한국투자증권 홈페이지에서 발급받은 appkey (절대 노출되지 않도록 주의해주세요.)
        "appsecret": kis_config.APPSECRET,  # 한국투자증권 홈페이지에서 발급받은 appkey (절대 노출되지 않도록 주의해주세요.)
        "tr_id": 'FHKST03010100',  # FHKST03010100
        "custtype": 'P',  # B : 법인   P : 개인
    }

    params = {
        "FID_COND_MRKT_DIV_CODE": 'J',  # J:KRX, NX:NXT, UN:통합
        "FID_INPUT_ISCD": '005930',  # 종목코드(ex 005930 삼성전자)
        "FID_INPUT_DATE_1": '20240101',  # 조회 시작일자 YYYYMMDD
        "FID_INPUT_DATE_2": '20240201',  # 조회 종료일자 YYYYMMDD
        "FID_PERIOD_DIV_CODE": 'D',  # 기간분류코드 D:일 W:주 M:월 Y:년
        "FID_ORG_ADJ_PRC": '0',  # 수정주가반영여부 0:반영X 1:반영
    }

    data = {
        # (required body 없음)
    }

    r = requests.get(url, headers=headers, params=params, timeout=10)
    return r.json()

def domestic_stock_inquire_time_itemchartprice():
    """
    [주식당일분봉조회]
    - METHOD: GET
    - URL: /uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice
    - TR_ID(실전): FHKST03010200
    - API ID: v1_국내주식-015
    """

    url = f"{kis_config.domain}/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice"

    headers = {
        "content-type": 'application/json; charset=utf-8',  # application/json; charset=utf-8
        "authorization": f"Bearer {kis_config.ACCESS_TOKEN}",  # OAuth 토큰이 필요한 API 경우 발급한 Access token   일반고객(Access token 유효기간 1일, OAuth 2.0의 Client Crede...
        "appkey": kis_config.APPKEY,  # 한국투자증권 홈페이지에서 발급받은 appkey (절대 노출되지 않도록 주의해주세요.)
        "appsecret": kis_config.APPSECRET,  # 한국투자증권 홈페이지에서 발급받은 appkey (절대 노출되지 않도록 주의해주세요.)
        "tr_id": 'FHKST03010200',  # FHKST03010200
        "custtype": 'P',  # B : 법인   P : 개인
    }

    params = {
        "FID_COND_MRKT_DIV_CODE": 'J',  # 시장분류코드 J:KRX, NX:NXT, UN:통합
        "FID_INPUT_ISCD": '005930',  # 종목코드(ex 005930 삼성전자)
        "FID_INPUT_HOUR_1": '090000',  # 조회시각(HHMMSS) ex) 090000
        "FID_PW_DATA_INCU_YN": 'Y',  # 과거데이터포함여부 Y/N
        "FID_ETC_CLS_CODE" : ''
    }

    data = {
        # (required body 없음)
    }

    r = requests.get(url, headers=headers, params=params, timeout=10)
    return r.json()

def domestic_stock_inquire_time_dailychartprice(code, date, time):
    """
    [주식일별분봉조회]
    - METHOD: GET
    - URL: /uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice
    - TR_ID(실전): FHKST03010300
    - API ID: v1_국내주식-017
    """

    url = f"{kis_config.domain}/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice"

    headers = {
        "content-type": 'application/json; charset=utf-8',  # application/json; charset=utf-8
        "authorization": f"Bearer {kis_config.ACCESS_TOKEN}",  # OAuth 토큰이 필요한 API 경우 발급한 Access token   일반고객(Access token 유효기간 1일, OAuth 2.0의 Client Crede...
        "appkey": kis_config.APPKEY,  # 한국투자증권 홈페이지에서 발급받은 appkey (절대 노출되지 않도록 주의해주세요.)
        "appsecret": kis_config.APPSECRET,  # 한국투자증권 홈페이지에서 발급받은 appkey (절대 노출되지 않도록 주의해주세요.)
        "tr_id": 'FHKST03010230',  # FHKST03010230
        "custtype": 'P',  # B : 법인   P : 개인
    }

    params = {
        "FID_COND_MRKT_DIV_CODE": 'J',  # 시장분류코드 J:KRX, NX:NXT, UN:통합
        "FID_INPUT_ISCD": code,  # 종목코드(ex 005930 삼성전자)
        "FID_INPUT_DATE_1": date,  # 조회시작일자 YYYYMMDD
        "FID_INPUT_HOUR_1": time,  # 조회시각(HHMMSS)
        "FID_PW_DATA_INCU_YN": 'N',  # 과거데이터포함여부 Y/N
        "FID_FAKE_TICK_INCU_YN" : ''
    }

    data = {
        # (required body 없음)
    }

    r = requests.get(url, headers=headers, params=params, timeout=10)
    return r.json()

def domestic_stock_inquire_time_dailyccnl():
    """
    [주식현재가 당일시간대별체결]
    - METHOD: GET
    - URL: /uapi/domestic-stock/v1/quotations/inquire-time-dailyccnl
    - TR_ID(실전): FHKST01010500
    - API ID: v1_국내주식-020
    """

    url = f"{kis_config.domain}/uapi/domestic-stock/v1/quotations/inquire-time-itemconclusion"

    headers = {
        "content-type": 'application/json; charset=utf-8',  # application/json; charset=utf-8
        "authorization": f"Bearer {kis_config.ACCESS_TOKEN}",  # OAuth 토큰이 필요한 API 경우 발급한 Access token   일반고객(Access token 유효기간 1일, OAuth 2.0의 Client Crede...
        "appkey": kis_config.APPKEY,  # 한국투자증권 홈페이지에서 발급받은 appkey (절대 노출되지 않도록 주의해주세요.)
        "appsecret": kis_config.APPSECRET,  # 한국투자증권 홈페이지에서 발급받은 appkey (절대 노출되지 않도록 주의해주세요.)
        "tr_id": 'FHPST01060000',  # FHKST01010500
        "custtype": 'P',  # B : 법인   P : 개인
    }

    params = {
        "FID_COND_MRKT_DIV_CODE": 'J',  # 조건시장분류코드 J:KRX, NX:NXT, UN:통합
        "FID_INPUT_ISCD": '005930',  # 종목코드(ex 005930 삼성전자)
        "FID_INPUT_HOUR_1": '090000',  # 조회시각(HHMMSS)
    }

    data = {
        # (required body 없음)
    }

    r = requests.get(url, headers=headers, params=params, timeout=10)
    return r.json()

def domestic_stock_inquire_overtime_daily_price():
    """
    [주식현재가 시간외일자별주가]
    - METHOD: GET
    - URL: /uapi/domestic-stock/v1/quotations/inquire-overtime-daily-price
    - TR_ID(실전): FHKST01010700
    - API ID: v1_국내주식-021
    """

    url = f"{kis_config.domain}/uapi/domestic-stock/v1/quotations/inquire-overtime-daily-price"

    headers = {
        "content-type": 'application/json; charset=utf-8',  # application/json; charset=utf-8
        "authorization": f"Bearer {kis_config.ACCESS_TOKEN}",  # OAuth 토큰이 필요한 API 경우 발급한 Access token   일반고객(Access token 유효기간 1일, OAuth 2.0의 Client Crede...
        "appkey": kis_config.APPKEY,  # 한국투자증권 홈페이지에서 발급받은 appkey (절대 노출되지 않도록 주의해주세요.)
        "appsecret": kis_config.APPSECRET,  # 한국투자증권 홈페이지에서 발급받은 appkey (절대 노출되지 않도록 주의해주세요.)
        "tr_id": 'FHPST02320000',  # FHKST01010700
        "custtype": 'P',  # B : 법인   P : 개인
    }

    params = {
        "FID_COND_MRKT_DIV_CODE": 'J',  # 조건시장분류코드 J:KRX, NX:NXT, UN:통합
        "FID_INPUT_ISCD": '005930',  # 종목코드(ex 005930 삼성전자)
    }

    data = {
        # (required body 없음)
    }

    r = requests.get(url, headers=headers, params=params, timeout=10)
    return r

def query_av_buy(code=None):

    url = f"{kis_config.domain}/uapi/domestic-stock/v1/trading/inquire-psbl-order"

    headers = {
        "content-type": 'application/json; charset=utf-8',  # application/json; charset=utf-8
        "authorization": f"Bearer {kis_config.ACCESS_TOKEN}",  # OAuth 토큰이 필요한 API 경우 발급한 Access token   일반고객(Access token 유효기간 1일, OAuth 2.0의 Client Crede...
        "appkey": kis_config.APPKEY,  # 한국투자증권 홈페이지에서 발급받은 appkey (절대 노출되지 않도록 주의해주세요.)
        "appsecret": kis_config.APPSECRET,  # 한국투자증권 홈페이지에서 발급받은 appkey (절대 노출되지 않도록 주의해주세요.)
        "tr_id": 'TTTC8908R ', 
    }

    params = {
        "CANO": kis_config.CANO,
        "ACNT_PRDT_CD": kis_config.ACNT_PRDT_CD,
        "PDNO": code if code else "",  # 종목번호(6자리) * PDNO, ORD_UNPR 공란 입력 시, 매수수량 없이 매수금액만 조회됨
        "ORD_UNPR": '',  # 1주당 가격, * 시장가(ORD_DVSN:01)로 조회 시, 공란으로 입력, * PDNO, ORD_UNPR 공란 입력 시, 매수수량 없이 매수금액만 조회됨
        "ORD_DVSN": '00',  # 00 : 지정가 / 01 : 시장가
        "CMA_EVLU_AMT_ICLD_YN": 'Y',  # CMA평가금액포함여부 
        "OVRS_ICLD_YN": "Y", # 해외포함여부
    }

    data = {
        # (required body 없음)
    }

    r = requests.get(url, headers=headers, params=params, timeout=10)
    return r.json()

def query_av_sell(code):

    url = f"{kis_config.domain}/uapi/domestic-stock/v1/trading/inquire-psbl-sell"

    headers = {
        "content-type": 'application/json; charset=utf-8',  # application/json; charset=utf-8
        "authorization": f"Bearer {kis_config.ACCESS_TOKEN}",  # OAuth 토큰이 필요한 API 경우 발급한 Access token   일반고객(Access token 유효기간 1일, OAuth 2.0의 Client Crede...
        "appkey": kis_config.APPKEY,  # 한국투자증권 홈페이지에서 발급받은 appkey (절대 노출되지 않도록 주의해주세요.)
        "appsecret": kis_config.APPSECRET,  # 한국투자증권 홈페이지에서 발급받은 appkey (절대 노출되지 않도록 주의해주세요.)
        "tr_id": 'TTTC8408R', 
    }

    params = {
        "CANO": kis_config.CANO,
        "ACNT_PRDT_CD": kis_config.ACNT_PRDT_CD,
        "PDNO": f'{code}',  # 보유종목 코드 ex)000660
    }

    data = {
        # (required body 없음)
    }

    r = requests.get(url, headers=headers, params=params, timeout=10)
    return r.json()

def buy(code, qty, price='0'):
    """
    [주식주문(현금)]
    - METHOD: POST
    - URL: /uapi/domestic-stock/v1/trading/order-cash
    - TR_ID(실전): (매도) TTTC0011U (매수) TTTC0012U
    - TR_ID(모의): (매도) VTTC0011U (매수) VTTC0012U
    - API ID: v1_국내주식-001
    """

    url = f"{kis_config.domain}/uapi/domestic-stock/v1/trading/order-cash"

    headers = {
        "content-type": 'application/json; charset=utf-8',
        "authorization": f"Bearer {kis_config.ACCESS_TOKEN}",
        "appkey": kis_config.APPKEY,
        "appsecret": kis_config.APPSECRET,
        "tr_id": 'TTTC0012U',  # 매수:TTTC0012U, 매도:TTTC0011U,
        "custtype": 'P'  # B:법인, P:개인,
    }

    params = {}

    data = {
        "CANO": kis_config.CANO,
        "ACNT_PRDT_CD": kis_config.ACNT_PRDT_CD,
        "PDNO": code,  # 종목코드(6자리) , ETN의 경우 7자리 입력,
        "ORD_DVSN": '01' if price == "0" else "00",  # [KRX] 00 : 지정가 / 01 : 시장가 
        "ORD_QTY": qty,  # 주문수량,
        "ORD_UNPR": price,  # 주문단가 (시장가 등 주문시 "0"),
    }

    r = requests.post(url, headers=headers, params=params, json=data, timeout=10)
    return r.json()

def sell(code, qty, price='0'):
    """
    [주식주문(현금)]
    - METHOD: POST
    - URL: /uapi/domestic-stock/v1/trading/order-cash
    - TR_ID(실전): (매도) TTTC0011U (매수) TTTC0012U
    - TR_ID(모의): (매도) VTTC0011U (매수) VTTC0012U
    - API ID: v1_국내주식-001
    """

    url = f"{kis_config.domain}/uapi/domestic-stock/v1/trading/order-cash"

    headers = {
        "content-type": 'application/json; charset=utf-8',
        "authorization": f"Bearer {kis_config.ACCESS_TOKEN}",
        "appkey": kis_config.APPKEY,
        "appsecret": kis_config.APPSECRET,
        "tr_id": 'TTTC0011U',  # 매수:TTTC0012U, 매도:TTTC0011U,
        "custtype": 'P'  # B:법인, P:개인,
    }

    params = {}

    data = {
        "CANO": kis_config.CANO,
        "ACNT_PRDT_CD": kis_config.ACNT_PRDT_CD,
        "PDNO": code,  # 종목코드(6자리) , ETN의 경우 7자리 입력,
        "ORD_DVSN": '01' if price == "0" else "00",  # [KRX] 00 : 지정가 / 01 : 시장가 
        "ORD_QTY": qty,  # 주문수량,
        "ORD_UNPR": price,  # 주문단가 (시장가 등 주문시 "0"),
    }

    r = requests.post(url, headers=headers, params=params, json=data, timeout=10)
    return r.json()

def domestic_stock_order_modify(ORGN_ODNO, qty, price):
    """
    [주식주문(정정취소)]
    - METHOD: POST
    - URL: /uapi/domestic-stock/v1/trading/order-rvsecncl
    - TR_ID(실전): (정정) TTTC0013U (취소) TTTC0014U
    - TR_ID(모의): (정정) VTTC0013U (취소) VTTC0014U
    - API ID: v1_국내주식-003
    """

    url = f"{kis_config.domain}/uapi/domestic-stock/v1/trading/order-rvsecncl"

    headers = {
        "content-type": 'application/json; charset=utf-8',
        "authorization": f"Bearer {kis_config.ACCESS_TOKEN}",
        "appkey": kis_config.APPKEY,
        "appsecret": kis_config.APPSECRET,
        "tr_id": 'TTTC0013U',  
        "custtype": 'P'  # B:법인, P:개인,
    }

    params = {}

    data = {
        "CANO": kis_config.CANO,
        "ACNT_PRDT_CD": kis_config.ACNT_PRDT_CD,
        "KRX_FWDG_ORD_ORGNO": ORGN_ODNO,  # 거래소전송주문조직번호,
        "ORGN_ODNO": '',  # 원주문번호,
        "ORD_DVSN": '',  # 주문구분,
        "RVSE_CNCL_DVSN_CD": '01',  # 정정/취소구분코드, 01: 정정, 02: 취소
        "ORD_QTY": qty,  # 정정수량(또는 취소수량),
        "ORD_UNPR": price,  # 정정단가(시장가 등은 0),
        "QTY_ALL_ORD_YN" : 'N',  # 전량주문여부 Y/N
    }

    r = requests.post(url, headers=headers, params=params, json=data, timeout=10)
    return r.json()

def domestic_stock_order_cancel(ORGN_ODNO):
    """
    [주식주문(정정취소)]
    - METHOD: POST
    - URL: /uapi/domestic-stock/v1/trading/order-rvsecncl
    - TR_ID(실전): (정정) TTTC0013U (취소) TTTC0014U
    - TR_ID(모의): (정정) VTTC0013U (취소) VTTC0014U
    - API ID: v1_국내주식-003
    """

    url = f"{kis_config.domain}/uapi/domestic-stock/v1/trading/order-rvsecncl"

    headers = {
        "content-type": 'application/json; charset=utf-8',
        "authorization": f"Bearer {kis_config.ACCESS_TOKEN}",
        "appkey": kis_config.APPKEY,
        "appsecret": kis_config.APPSECRET,
        "tr_id": 'TTTC0013U',  
        "custtype": 'P'  # B:법인, P:개인,
    }

    params = {}

    data = {
        "CANO": kis_config.CANO,
        "ACNT_PRDT_CD": kis_config.ACNT_PRDT_CD,
        "KRX_FWDG_ORD_ORGNO": '06010',   # 한국거래소전송주문조직번호(=주문점)
        "ORGN_ODNO": ORGN_ODNO,  # 원주문번호,
        "ORD_DVSN": '00',  # 주문구분 00 : 지정가 / 01 : 시장가
        "RVSE_CNCL_DVSN_CD": '02',  # 정정/취소구분코드, 01: 정정, 02: 취소
        "ORD_QTY": '1',  # 정정수량(또는 취소수량),
        "ORD_UNPR": '0',  # 정정단가(시장가 등은 0),
        "QTY_ALL_ORD_YN" : 'N',  # 전량주문여부 Y/N
    }

    r = requests.post(url, headers=headers, params=params, json=data, timeout=10)
    return r.json()

def domestic_stock_inquire_psbl_rvsecncl():
    """
    [주식정정취소가능주문조회]
    - METHOD: GET
    - URL: /uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl
    - TR_ID(실전): TTTC0084R
    - TR_ID(모의): VTTC0801U
    - API ID: v1_국내주식-004
    """

    url = f"{kis_config.domain}/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl"

    headers = {
        "content-type": 'application/json; charset=utf-8',
        "authorization": f"Bearer {kis_config.ACCESS_TOKEN}",
        "appkey": kis_config.APPKEY,
        "appsecret": kis_config.APPSECRET,
        "tr_id": 'TTTC0084R',
        "custtype": 'P'  # B:법인, P:개인,
    }

    params = {
        "CANO": kis_config.CANO,
        "ACNT_PRDT_CD": kis_config.ACNT_PRDT_CD,
        "CTX_AREA_FK100": '',  # 연속조회검색조건(100),
        "CTX_AREA_NK100": '',  # 연속조회키(100),
        "INQR_DVSN_1": '0',  # 조회구분1,  '0 주문 / 1 종목'
        "INQR_DVSN_2": '0',  # 조회구분2, '0 전체 / 1 매도 / 2 매수'
    }

    data = {}

    r = requests.get(url, headers=headers, params=params, timeout=10)
    return r.json()

def domestic_stock_inquire_daily_ccld(ord_gno_brno, ODNO):
    """
    [주식일별주문체결조회]
    - METHOD: GET
    - URL: /uapi/domestic-stock/v1/trading/inquire-daily-ccld
    - TR_ID(실전): TTTC0081R
    - TR_ID(모의): VTTC0081R
    - API ID: v1_국내주식-005
    """

    url = f"{kis_config.domain}/uapi/domestic-stock/v1/trading/inquire-daily-ccld"

    headers = {
        "content-type": 'application/json; charset=utf-8',
        "authorization": f"Bearer {kis_config.ACCESS_TOKEN}",
        "appkey": kis_config.APPKEY,
        "appsecret": kis_config.APPSECRET,
        "tr_id": 'TTTC0081R',
        "custtype": 'P'  # B:법인, P:개인,
    }

    now = datetime.now()
    days_90_ago = now - timedelta(days=1)
    print(days_90_ago.strftime("%Y%m%d"))
    print(now.strftime("%Y%m%d"))

    params = {
        'CANO':	kis_config.CANO,  #계좌번호 체계(8-2)의 앞 8자리
        'ACNT_PRDT_CD':  kis_config.ACNT_PRDT_CD, #계좌번호 체계(8-2)의 뒤 2자리
        'INQR_STRT_DT': days_90_ago.strftime("%Y%m%d"),  # 조회시작일자,
        'INQR_END_DT': 	now.strftime("%Y%m%d"),  # 조회종료일자,
        'SLL_BUY_DVSN_CD': "00",	#00 : 전체 / 01 : 매도 / 02 : 매수
        'ORD_GNO_BRNO': ord_gno_brno, 	#	주문시 한국투자증권 시스템에서 지정된 영업점코드"06010"
        'ODNO' : ODNO, #주문번호
        'CCLD_DVSN': "00",	#	'00 전체        01 체결        02 미체결'
        'INQR_DVSN': "00",	#'00 역순 / 01 정순'
        'INQR_DVSN_1': "",	#'없음: 전체 / 1: ELW / 2: 프리보드'
        'INQR_DVSN_3': "", #'00 전체/01 현금/02 신용/03 담보/04 대주/05 대여/06 자기융자신규/상환/07 유통융자신규/상환'
        'EXCG_ID_DVSN_CD': "ALL",	#한국거래소 : KRX / 대체거래소 (NXT) : NXT/ SOR (Smart Order Routing) : SOR/ ALL : 전체
        'CTX_AREA_FK100': "",
        'CTX_AREA_NK100': "",
    }

    data = {}

    r = requests.get(url, headers=headers, params=params, timeout=10)
    return r.json()

def domestic_stock_inquire_balance():
    """
    [주식잔고조회]
    - METHOD: GET
    - URL: /uapi/domestic-stock/v1/trading/inquire-balance
    - TR_ID(실전): TTTC8434R
    - TR_ID(모의): VTTC8434R
    - API ID: v1_국내주식-006
    """

    url = f"{kis_config.domain}/uapi/domestic-stock/v1/trading/inquire-balance"

    headers = {
        "content-type": 'application/json; charset=utf-8',
        "authorization": f"Bearer {kis_config.ACCESS_TOKEN}",
        "appkey": kis_config.APPKEY,
        "appsecret": kis_config.APPSECRET,
        "tr_id": 'TTTC8434R',
        "custtype": 'P'  # B:법인, P:개인,
    }

    params = {
        "CANO": kis_config.CANO,
        "ACNT_PRDT_CD": kis_config.ACNT_PRDT_CD,
        "AFHR_FLPR_YN": '',  # 시간외단일가여부,
        "OFL_YN": '',  # 오프라인여부,
        "INQR_DVSN": '',  # 조회구분,
        "UNPR_DVSN": '',  # 단가구분,
        "FUND_STTL_ICLD_YN": '',  # 펀드결제분포함여부,
        "FNCG_AMT_AUTO_RDPT_YN": '',  # 융자금액자동상환여부,
        "PRCS_DVSN": '',  # 처리구분,
        "CTX_AREA_FK100": '',
        "CTX_AREA_NK100": '',
    }

    data = {}

    r = requests.get(url, headers=headers, params=params, timeout=10)
    return r.json()

def domestic_stock_order_reserve():
    """
    [주식예약주문]
    - METHOD: POST
    - URL: /uapi/domestic-stock/v1/trading/order-resv
    - TR_ID(실전): (매도) TTTC0019U (매수) TTTC0020U
    - TR_ID(모의): (매도) VTTC0019U (매수) VTTC0020U
    - API ID: v1_국내주식-010
    """

    url = f"{kis_config.domain}/uapi/domestic-stock/v1/trading/order-resv"

    headers = {
        "content-type": 'application/json; charset=utf-8',
        "authorization": f"Bearer {kis_config.ACCESS_TOKEN}",
        "appkey": kis_config.APPKEY,
        "appsecret": kis_config.APPSECRET,
        "tr_id": 'CTSC0008U',  # 매수:TTTC0020U, 매도:TTTC0019U,
        "custtype": 'P',  # B:법인, P:개인,
    }

    params = {}

    data = {
        "CANO": kis_config.CANO,
        "ACNT_PRDT_CD": kis_config.ACNT_PRDT_CD,
        "PDNO": '047040',
        "ORD_QTY": '1',
        "ORD_UNPR": '8000',
        "SLL_BUY_DVSN_CD": '01',  # 매도 01/매수 02
        "ORD_DVSN_CD": '00', # 00 : 지정가 / 01 : 시장가 / 02 : 조건부지정가 / 05 : 장전 시간외
        "ORD_OBJT_CBLC_DVSN_CD" : '10', # 10 : 현금  
        "RSVN_ORD_END_DT": '20260219',  # 예약주문종료일자,
        "ORD_TMD": '090000',  # 예약주문시간,
    }

    r = requests.post(url, headers=headers, params=params, json=data, timeout=10)
    return r.json()

def domestic_stock_inquire_reserve_order():
    """
    [주식예약주문조회]
    - METHOD: GET
    - URL: /uapi/domestic-stock/v1/trading/inquire-resv
    - TR_ID(실전): TTTC0083R
    - TR_ID(모의): VTTC0083R
    - API ID: v1_국내주식-012
    """

    url = f"{kis_config.domain}//uapi/domestic-stock/v1/trading/order-resv-ccnl"

    headers = {
        "content-type": 'application/json; charset=utf-8',
        "authorization": f"Bearer {kis_config.ACCESS_TOKEN}",
        "appkey": kis_config.APPKEY,
        "appsecret": kis_config.APPSECRET,
        "tr_id": 'CTSC0004R',
        "custtype": 'P'  # B:법인, P:개인,
    }

    params = {
        "CANO": kis_config.CANO,
        "ACNT_PRDT_CD": kis_config.ACNT_PRDT_CD,
        "RSVN_ORD_ORD_DT": '20260101',
        "RSVN_ORD_END_DT": '20260219',
        "RSVN_ORD_SEQ": '',
        "TMNL_MDIA_KIND_CD": '00',
        "PRCS_DVSN_CD": '0',
        "CNCL_YN": 'Y',
        "PDNO": '',
        "SLL_BUY_DVSN_CD": '',
        "CTX_AREA_FK200": '',
        "CTX_AREA_NK200": '',
    }

    data = {}

    r = requests.get(url, headers=headers, params=params, timeout=10)
    return r.json()

def domestic_stock_order_reserve_modify(RSVN_ORD_SEQ, code, qty, price):
    """
    [주식예약주문정정취소]
    - METHOD: POST
    - URL: /uapi/domestic-stock/v1/trading/order-resv-rvsecncl
    - API ID: v1_국내주식-011
    """

    url = f"{kis_config.domain}/uapi/domestic-stock/v1/trading/order-resv-rvsecncl"

    headers = {
        "content-type": 'application/json; charset=utf-8',
        "authorization": f"Bearer {kis_config.ACCESS_TOKEN}",
        "appkey": kis_config.APPKEY,
        "appsecret": kis_config.APPSECRET,
        "tr_id": 'CTSC0013U',  # CTSC0009U : 국내주식예약취소주문 / CTSC0013U : 국내주식예약정정주문
        "custtype": 'P'  # B:법인, P:개인,
    }

    params = {}

    data = {
        "CANO": kis_config.CANO,
        "ACNT_PRDT_CD": kis_config.ACNT_PRDT_CD,
        "PDNO" : code,  # 종목코드(6자리) , ETN의 경우 7자리 입력,
        "ORD_QTY": qty,
        "ORD_UNPR": price,
        "SLL_BUY_DVSN_CD": '01',  # 매도 01/매수 02,
        "ORD_DVSN_CD" : '00', # 00 : 지정가 / 01 : 시장가 / 02 : 조건부지정가 / 05 : 장전 시간외,
        "ORD_OBJT_CBLC_DVSN_CD" : '10', # 10 : 현금
        "RSVN_ORD_SEQ": RSVN_ORD_SEQ,  # 예약주문순번,
    }

    r = requests.post(url, headers=headers, params=params, json=data, timeout=10)
    return r.json()

def domestic_stock_order_reserve_cancel(RSVN_ORD_SEQ, code):
    """
    [주식예약주문정정취소]
    - METHOD: POST
    - URL: /uapi/domestic-stock/v1/trading/order-resv-rvsecncl
    - API ID: v1_국내주식-011
    """


    url = f"{kis_config.domain}/uapi/domestic-stock/v1/trading/order-resv-rvsecncl"

    headers = {
        "content-type": 'application/json; charset=utf-8',
        "authorization": f"Bearer {kis_config.ACCESS_TOKEN}",
        "appkey": kis_config.APPKEY,
        "appsecret": kis_config.APPSECRET,
        "tr_id": 'CTSC0009U',  # CTSC0009U : 국내주식예약취소주문 / CTSC0013U : 국내주식예약정정주문
        "custtype": 'P'  # B:법인, P:개인,
    }

    params = {}

    data = {
        "CANO": kis_config.CANO,
        "ACNT_PRDT_CD": kis_config.ACNT_PRDT_CD,
        "PDNO" : code,  # 종목코드(6자리) , ETN의 경우 7자리 입력,
        "ORD_QTY": "",
        "ORD_UNPR": "",
        "SLL_BUY_DVSN_CD": '01',  # 매도 01/매수 02,
        "ORD_DVSN_CD" : '', # 00 : 지정가 / 01 : 시장가 / 02 : 조건부지정가 / 05 : 장전 시간외,
        "ORD_OBJT_CBLC_DVSN_CD" : '10', # 10 : 현금
        "RSVN_ORD_SEQ": RSVN_ORD_SEQ,  # 예약주문순번,
    }

    r = requests.post(url, headers=headers, params=params, json=data, timeout=10)
    return r.json()
