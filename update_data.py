# -*- coding: utf-8 -*-
"""
한국은행 ECOS API 및 금융 데이터 자동 수집기 v3.0
- 뉴스심리지수 (523Y001 / A001) 및 3개월(90일) 이동평균
- 코스피 과거 일봉/월봉 OHLCV (시가, 고가, 저가, 종가, 거래량)
- 코스피 20일, 60일, 120일 이동평균선
- 코스피 월봉 5월, 20월, 60월 이동평균선
- 외국인 국내주식 일별/월별 보유 비중(%) 및 순매수 대금 추이 (ECOS 802Y001)
- 수급, 금리차, VIX 보조지표
"""

import os
import json
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

API_KEY = 'TWSDJSZHSK3FJKI0KDUP'
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, 'data.json')

def fetch_json(url, timeout=15):
    headers = {'User-Agent': 'Mozilla/5.0'}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode('utf-8'))
    except Exception as e:
        print(f"Fetch error for {url}: {e}")
        return None

def fetch_news_sentiment(api_key):
    """한국은행 ECOS 뉴스심리지수 수집"""
    today = datetime.now().strftime('%Y%m%d')
    start_date = '20240101'
    
    url = f"https://ecos.bok.or.kr/api/StatisticSearch/{api_key}/json/kr/1/1000/523Y001/D/{start_date}/{today}/A001"
    res = fetch_json(url)
    if not res or 'StatisticSearch' not in res:
        print("Failed to fetch page 1 of news sentiment")
        return []
    
    total = int(res['StatisticSearch'].get('list_total_count', 0))
    rows = res['StatisticSearch'].get('row', [])
    
    if total > 1000:
        url2 = f"https://ecos.bok.or.kr/api/StatisticSearch/{api_key}/json/kr/1001/{total}/523Y001/D/{start_date}/{today}/A001"
        res2 = fetch_json(url2)
        if res2 and 'StatisticSearch' in res2:
            rows.extend(res2['StatisticSearch'].get('row', []))
            
    data = []
    for r in rows:
        t = r.get('TIME', '')
        v = r.get('DATA_VALUE', '')
        if t and v:
            try:
                date_str = f"{t[:4]}-{t[4:6]}-{t[6:8]}"
                data.append({
                    "date": date_str,
                    "rawDate": t,
                    "value": round(float(v), 2)
                })
            except ValueError:
                continue
    return data

def calculate_ma_and_zones(data, period=90):
    """3개월(90일) 이동평균 및 4국면 판정"""
    if not data:
        return [], None, None, None
        
    ma_data = []
    for i in range(len(data)):
        if i < period - 1:
            ma_data.append(None)
        else:
            window = [data[j]['value'] for j in range(i - period + 1, i + 1)]
            ma_data.append(round(sum(window) / period, 2))
            
    valid_ma = [v for v in ma_data if v is not None]
    if len(valid_ma) >= 15:
        ma_diff = valid_ma[-1] - valid_ma[-15]
        trend = "up" if ma_diff > 0.8 else "down" if ma_diff < -0.8 else "flat"
    else:
        trend = "flat"
        
    latest_val = data[-1]['value']
    latest_ma = valid_ma[-1] if valid_ma else latest_val
    
    if latest_val >= 110 and trend in ['up', 'flat']:
        zone = "overheated"
    elif (latest_val >= 105 and trend == 'down') or (latest_ma >= 108 and trend == 'down'):
        zone = "adjustment"
    elif latest_val <= 90 and trend in ['down', 'flat']:
        zone = "undervalued"
    elif (latest_val <= 95 and trend == 'up') or (latest_ma <= 93 and trend == 'up'):
        zone = "recovery"
    elif latest_val > 105 and trend == 'up':
        zone = "overheated"
    elif latest_val < 95 and trend == 'down':
        zone = "undervalued"
    elif trend == 'up':
        zone = "recovery"
    else:
        zone = "adjustment"
        
    return ma_data, trend, zone, latest_ma

def fetch_kospi_candles(timeframe='day', count=750):
    """네이버 금융 FChart에서 코스피 캔들스틱 OHLCV 수집"""
    url = f"https://fchart.stock.naver.com/sise.nhn?symbol=KOSPI&timeframe={timeframe}&count={count}&requestType=0"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        content = urllib.request.urlopen(req, timeout=12).read().decode('euc-kr', errors='ignore')
        root = ET.fromstring(content)
        candles = []
        for item in root.findall('.//item'):
            parts = item.attrib['data'].split('|')
            t = parts[0]
            date_str = f"{t[:4]}-{t[4:6]}-{t[6:8]}" if len(t) == 8 else t
            candles.append({
                "date": date_str,
                "open": round(float(parts[1]), 2),
                "high": round(float(parts[2]), 2),
                "low": round(float(parts[3]), 2),
                "close": round(float(parts[4]), 2),
                "volume": int(parts[5])
            })
        return candles
    except Exception as e:
        print(f"Error fetching KOSPI candles ({timeframe}): {e}")
        return []

def calc_series_ma(candles, period):
    """캔들 종가 기준 이동평균선 계산"""
    ma = []
    for i in range(len(candles)):
        if i < period - 1:
            ma.append(None)
        else:
            window = [candles[j]['close'] for j in range(i - period + 1, i + 1)]
            ma.append(round(sum(window) / period, 2))
    return ma

def fetch_foreign_holdings(api_key):
    """한국은행 ECOS 802Y001에서 외국인 일별/월별 순매수 및 시총 비중(%) 산출"""
    start_date = '20240101'
    today = datetime.now().strftime('%Y%m%d')
    
    # 1. 외국인 순매수(0030000, 억원)
    url_f = f"https://ecos.bok.or.kr/api/StatisticSearch/{api_key}/json/kr/1/1000/802Y001/D/{start_date}/{today}/0030000"
    res_f = fetch_json(url_f)
    rows_f = res_f.get('StatisticSearch', {}).get('row', []) if res_f else []
    
    # 2. 시가총액(0183000, 십억원)
    url_m = f"https://ecos.bok.or.kr/api/StatisticSearch/{api_key}/json/kr/1/1000/802Y001/D/{start_date}/{today}/0183000"
    res_m = fetch_json(url_m)
    rows_m = res_m.get('StatisticSearch', {}).get('row', []) if res_m else []
    
    if not rows_f or not rows_m:
        print("[오류] 외국인 순매수/시총 데이터 조회 실패")
        return [], []
        
    mcap_dict = {r['TIME']: float(r['DATA_VALUE']) * 10 for r in rows_m} # 십억원 -> 억원
    
    # 기준 시점(2024-01-02) 외국인 보유 비중 약 33.6% (약 721조원)
    base_time = rows_f[0]['TIME']
    base_mcap = mcap_dict.get(base_time, 214722300)
    curr_foreign_val = base_mcap * 0.336
    
    daily_list = []
    month_groups = {}
    
    for i, rf in enumerate(rows_f):
        t = rf['TIME']
        net_buy = float(rf['DATA_VALUE'])
        mcap = mcap_dict.get(t, base_mcap)
        
        if i > 0:
            prev_t = rows_f[i-1]['TIME']
            prev_mcap = mcap_dict.get(prev_t, mcap)
            price_ratio = mcap / prev_mcap if prev_mcap > 0 else 1.0
            curr_foreign_val = (curr_foreign_val * price_ratio) + net_buy
            
        ratio = round((curr_foreign_val / mcap) * 100, 2)
        date_str = f"{t[:4]}-{t[4:6]}-{t[6:8]}"
        
        item = {
            "date": date_str,
            "rawDate": t,
            "netBuy": round(net_buy, 0),
            "ratio": ratio,
            "foreignHoldingVal": round(curr_foreign_val / 10000, 1) # 조원
        }
        daily_list.append(item)
        
        # 월별 그룹화 (YYYY-MM)
        month_key = t[:6]
        if month_key not in month_groups:
            month_groups[month_key] = []
        month_groups[month_key].append(item)
        
    # 월봉 데이터 집계
    monthly_list = []
    for m_key, items in month_groups.items():
        total_net_buy = sum(x['netBuy'] for x in items)
        last_item = items[-1]
        monthly_list.append({
            "date": last_item['date'],
            "month": f"{m_key[:4]}-{m_key[4:6]}",
            "netBuy": round(total_net_buy, 0),
            "ratio": last_item['ratio'],
            "foreignHoldingVal": last_item['foreignHoldingVal']
        })
        
    return daily_list, monthly_list

def fetch_market_indicators(api_key):
    """보조 지표 수집"""
    ind = {
        "kospi": 5290.53,
        "kospi_change": -0.98,
        "per": 11.2,
        "pbr": 0.91,
        "vkospi": 18.5,
        "foreign_flow": 0,
        "institution_flow": 0,
        "spread": 0.18,
        "treasury_3y": 3.799,
        "treasury_1y": 3.623
    }
    
    k_data = fetch_json("https://m.stock.naver.com/api/index/KOSPI/basic")
    if k_data:
        try:
            ind["kospi"] = float(k_data.get("closePrice", "5290").replace(",", ""))
            ind["kospi_change"] = float(k_data.get("fluctuationsRatio", "0"))
        except:
            pass
            
    t_data = fetch_json("https://m.stock.naver.com/api/index/KOSPI/trend")
    if t_data:
        try:
            f_str = t_data.get("foreignValue", "0").replace(",", "").replace("+", "")
            i_str = t_data.get("institutionalValue", "0").replace(",", "").replace("+", "")
            ind["foreign_flow"] = int(f_str)
            ind["institution_flow"] = int(i_str)
        except:
            pass
            
    today = datetime.now().strftime('%Y%m%d')
    start_search = (datetime.now() - timedelta(days=30)).strftime('%Y%m%d')
    
    url_3y = f"https://ecos.bok.or.kr/api/StatisticSearch/{api_key}/json/kr/1/10/817Y002/D/{start_search}/{today}/010200000"
    res_3y = fetch_json(url_3y)
    if res_3y and 'StatisticSearch' in res_3y:
        rows = res_3y['StatisticSearch'].get('row', [])
        if rows:
            ind["treasury_3y"] = float(rows[-1]['DATA_VALUE'])
            
    url_1y = f"https://ecos.bok.or.kr/api/StatisticSearch/{api_key}/json/kr/1/10/817Y002/D/{start_search}/{today}/010190000"
    res_1y = fetch_json(url_1y)
    if res_1y and 'StatisticSearch' in res_1y:
        rows = res_1y['StatisticSearch'].get('row', [])
        if rows:
            ind["treasury_1y"] = float(rows[-1]['DATA_VALUE'])
            
    ind["spread"] = round(ind["treasury_3y"] - ind["treasury_1y"], 2)
    
    vix_data = fetch_json("https://query1.finance.yahoo.com/v8/finance/chart/^VIX")
    if vix_data and 'chart' in vix_data and vix_data['chart']['result']:
        try:
            meta = vix_data['chart']['result'][0]['meta']
            ind["vkospi"] = round(float(meta.get('regularMarketPrice', 19.3)), 1)
        except:
            pass
            
    # 원/달러 환율 (KRW/USD) 수집
    ind["usdkrw"] = 1338.30
    ind["usdkrw_change"] = 1.80
    ind["usdkrw_ratio"] = 0.13
    try:
        fx_data = fetch_json("https://m.stock.naver.com/front-api/marketIndex/productDetail?category=exchange&reutersCode=FX_USDKRW")
        if fx_data and fx_data.get('result'):
            res_fx = fx_data['result']
            price = float(res_fx.get('closePrice', '1338').replace(',', ''))
            change = float(str(res_fx.get('fluctuations', '0')).replace(',', ''))
            ratio = float(str(res_fx.get('fluctuationsRatio', '0')).replace(',', ''))
            f_type = res_fx.get('fluctuationsType', {}).get('name', 'RISING')
            if f_type in ('FALLING', 'DROP', '3', '5') and change > 0:
                change = -change
                ratio = -ratio
            ind["usdkrw"] = price
            ind["usdkrw_change"] = change
            ind["usdkrw_ratio"] = ratio
    except Exception as e:
        print("[경고] 네이버 환율 수집 예외:", e)

    # 달러 인덱스 (DXY) 수집
    ind["dxy"] = 103.69
    ind["dxy_change"] = 0.52
    ind["dxy_ratio"] = 0.50
    try:
        dxy_json = fetch_json("https://query1.finance.yahoo.com/v8/finance/chart/DX-Y.NYB?interval=1d&range=5d")
        if dxy_json and 'chart' in dxy_json and dxy_json['chart']['result']:
            meta = dxy_json['chart']['result'][0]['meta']
            curr = float(meta.get('regularMarketPrice', 103.69))
            prev = float(meta.get('chartPreviousClose', curr))
            diff = curr - prev
            pct = (diff / prev) * 100 if prev > 0 else 0.0
            ind["dxy"] = round(curr, 2)
            ind["dxy_change"] = round(diff, 2)
            ind["dxy_ratio"] = round(pct, 2)
    except Exception as e:
        print("[경고] DXY 수집 예외:", e)
            
    return ind

def generate_daily_commentary(kospi, kospi_change, foreign_flow, usdkrw, usdkrw_change, dxy, vkospi, foreign_ratio):
    """대시보드 핵심 지표 종합 3줄 실전 논평 자동 생성"""
    # 1줄: 시장 진단 & 국면
    if kospi_change <= -2.0:
        line1 = f"🌧️ [시장 진단] 코스피가 {kospi:,.2f}P({kospi_change:+.2f}%)로 급락하며 5,000선 등 주요 지지선이 무너진 본격 투매 및 하락 조정 국면입니다."
    elif kospi_change < 0:
        line1 = f"☁️ [시장 진단] 코스피가 {kospi:,.2f}P({kospi_change:+.2f}%)로 약세를 보이며 하방 압력을 받는 숨고르기 국면입니다."
    elif kospi_change < 1.5:
        line1 = f"⛅ [시장 진단] 코스피가 {kospi:,.2f}P({kospi_change:+.2f}%)로 반등하며 단기 하방 경직성을 시험하는 구간입니다."
    else:
        line1 = f"☀️ [시장 진단] 코스피가 {kospi:,.2f}P({kospi_change:+.2f}%)로 강하게 상승하며 지수 반등 탄력이 살아나는 국면입니다."

    # 2줄: 수급 & 환율/달러 원인 분석
    f_sign = "순매도" if foreign_flow < 0 else "순매수"
    f_amt = abs(round(foreign_flow))
    fx_status = "원화 약세" if (usdkrw >= 1340 or usdkrw_change > 0) else "환율 안정"
    
    if foreign_flow <= -10000:
        line2 = f"🔍 [수급/환율] 원/달러 환율({usdkrw:,.1f}원) {fx_status}와 달러 인덱스({dxy:.2f}P) 속에서 외국인이 -{f_amt:,}억 원의 대량 매도 폭탄을 쏟아내며 지분율({foreign_ratio:.2f}%) 하락을 주도했습니다."
    elif foreign_flow < 0:
        line2 = f"🔍 [수급/환율] 환율({usdkrw:,.1f}원) 부담 속에 외국인이 {foreign_flow:+,}억 원 {f_sign}를 지속하며 수급 주도권이 여전히 보수적인 흐름입니다."
    else:
        line2 = f"🔍 [수급/환율] 외국인이 +{f_amt:,}억 원 순매수로 유입되며 환율({usdkrw:,.1f}원) 안정세와 함께 수급 개선 신호를 보였습니다."

    # 3줄: 실전 매매 및 대응 전략
    if foreign_flow < -5000 and kospi_change < -1.0:
        line3 = "🎯 [실전 대응] 성급한 저가 매수나 물타기를 멈추고, 외국인 매도세 진정 및 환율 반락이 확인될 때까지 현금을 방어하며 관망하는 전략이 필수적입니다."
    elif foreign_flow < 0:
        line3 = "🎯 [실전 대응] 반등 시 현금 비중을 유지하고, 외국인 수급 전환 여부를 체크하며 분할 매수 시점을 신중히 저울질할 시기입니다."
    else:
        line3 = "🎯 [실전 대응] 우량주 및 지수 추종 ETF를 중심으로 분할 매수 접근이 유효하며, 환율 추세를 지켜보며 비중을 확대할 수 있는 구간입니다."

    return [line1, line2, line3]

def main():
    print("=" * 68)
    print("한국은행 ECOS + 코스피 캔들 + 외국인 보유현황(%) 데이터 수집 시작...")
    print("=" * 68)
    
    # 1. 뉴스심리지수
    raw_data = fetch_news_sentiment(API_KEY)
    if not raw_data:
        print("[오류] 뉴스심리지수 수집 실패")
        return
    ma_data, trend, zone, latest_ma = calculate_ma_and_zones(raw_data, period=90)
    print(f"✓ 뉴스심리지수: {len(raw_data)}건 수집 완료 (최신: {raw_data[-1]['value']})")
    
    # 2. 코스피 일봉/월봉
    kospi_daily = fetch_kospi_candles('day', 750)
    ma20_daily = calc_series_ma(kospi_daily, 20)
    ma60_daily = calc_series_ma(kospi_daily, 60)
    ma120_daily = calc_series_ma(kospi_daily, 120)
    
    kospi_monthly = fetch_kospi_candles('month', 60)
    ma5_monthly = calc_series_ma(kospi_monthly, 5)
    ma20_monthly = calc_series_ma(kospi_monthly, 20)
    ma60_monthly = calc_series_ma(kospi_monthly, 60)
    print(f"✓ 코스피 캔들: 일봉 {len(kospi_daily)}건 / 월봉 {len(kospi_monthly)}건 수집 완료")
    
    # 3. 외국인 보유현황 (일별 / 월별)
    foreign_daily, foreign_monthly = fetch_foreign_holdings(API_KEY)
    print(f"✓ 외국인 보유현황: 일별 {len(foreign_daily)}건 / 월별 {len(foreign_monthly)}건 산출 완료")
    if foreign_daily:
        print(f"  - 최신 외국인 보유비중: {foreign_daily[-1]['ratio']}% | 순매수: {foreign_daily[-1]['netBuy']}억원")
        
    # 4. 보조 지표
    indicators = fetch_market_indicators(API_KEY)

    # 4-1. 당일(오늘) 외국인 지분율 및 순매수 실시간 추정치 보간
    # 한국은행 공식 통계가 T+1일 지연되더라도 코스피 당일 캔들과 네이버 실시간 수급을 조합하여 오늘치 데이터를 즉시 실시간 연결
    if foreign_daily and kospi_daily:
        latest_f_date = foreign_daily[-1]['date']
        latest_k_candle = kospi_daily[-1]
        today_date = latest_k_candle['date']
        
        if latest_f_date < today_date:
            prev_candle = None
            for c in reversed(kospi_daily):
                if c['date'] == latest_f_date:
                    prev_candle = c
                    break
            if not prev_candle and len(kospi_daily) >= 2:
                prev_candle = kospi_daily[-2]
                
            prev_close = prev_candle['close'] if prev_candle else latest_k_candle['close']
            today_close = latest_k_candle['close']
            price_ratio = today_close / prev_close if prev_close > 0 else 1.0
            
            last_item = foreign_daily[-1]
            last_foreign_val = last_item['foreignHoldingVal'] * 10000 # 억원
            prev_mcap = last_foreign_val / (last_item['ratio'] / 100) if last_item['ratio'] > 0 else 1
            today_mcap = prev_mcap * price_ratio
            
            today_flow = float(indicators.get('foreign_flow', 0))
            curr_foreign_val = (last_foreign_val * price_ratio) + today_flow
            est_ratio = round((curr_foreign_val / today_mcap) * 100, 2)
            
            est_item = {
                "date": today_date,
                "rawDate": today_date.replace('-', ''),
                "netBuy": round(today_flow, 0),
                "ratio": est_ratio,
                "foreignHoldingVal": round(curr_foreign_val / 10000, 1),
                "isEstimate": True
            }
            foreign_daily.append(est_item)
            print(f"✓ 당일({today_date}) 외국인 지분율 실시간 추정치 추가: {est_ratio}% | 순매수: {today_flow}억원 (잠정)")
            
            # 월봉 리스트에도 당일 잠정치 반영
            m_key = today_date[:7]
            if foreign_monthly and foreign_monthly[-1]['month'] == m_key:
                foreign_monthly[-1]['netBuy'] = round(foreign_monthly[-1]['netBuy'] + today_flow, 0)
                foreign_monthly[-1]['ratio'] = est_ratio
                foreign_monthly[-1]['foreignHoldingVal'] = est_item['foreignHoldingVal']
                foreign_monthly[-1]['date'] = today_date
                foreign_monthly[-1]['isEstimate'] = True
    
    # 3줄 실전 논평 자동 생성
    f_ratio = foreign_daily[-1]['ratio'] if foreign_daily else 33.17
    commentary = generate_daily_commentary(
        indicators.get('kospi', 4968.48),
        indicators.get('kospi_change', 0.0),
        indicators.get('foreign_flow', 0),
        indicators.get('usdkrw', 1338.10),
        indicators.get('usdkrw_change', 0.0),
        indicators.get('dxy', 103.69),
        indicators.get('vkospi', 23.1),
        f_ratio
    )
    print("✓ 오늘자 3줄 실전 논평 생성 완료:")
    for c in commentary:
        print(f"  {c}")

    payload = {
        "updatedAt": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "source": "한국은행 ECOS (통계표 523Y001, 802Y001) 및 네이버 금융 공식 데이터",
        "trend": trend,
        "zone": zone,
        "latestValue": raw_data[-1]['value'],
        "latestMA": latest_ma,
        "latestDate": raw_data[-1]['date'],
        "indicators": indicators,
        "dailyCommentary": commentary,
        
        # 뉴스심리지수 시계열
        "timeSeries": raw_data,
        "maSeries": ma_data,
        
        # 코스피 일봉 & 이평선
        "kospiDaily": {
            "candles": kospi_daily,
            "ma20": ma20_daily,
            "ma60": ma60_daily,
            "ma120": ma120_daily
        },
        
        # 코스피 월봉 & 이평선
        "kospiMonthly": {
            "candles": kospi_monthly,
            "ma5": ma5_monthly,
            "ma20": ma20_monthly,
            "ma60": ma60_monthly
        },
        
        # 🌟 외국인 보유현황 & 순매수 추이 (일별 & 월별)
        "foreignDaily": foreign_daily,
        "foreignMonthly": foreign_monthly,
        "latestForeign": foreign_daily[-1] if foreign_daily else None
    }
    
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        
    print(f"[성공] {DATA_FILE} 저장 완료!")

if __name__ == '__main__':
    main()
