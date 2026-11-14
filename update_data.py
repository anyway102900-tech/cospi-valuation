# -*- coding: utf-8 -*-
"""
한국은행 ECOS API 및 금융 데이터 자동 수집기 v2.5
- 뉴스심리지수 (523Y001 / A001) 및 3개월(90일) 이동평균
- 코스피 과거 일봉/월봉 OHLCV (시가, 고가, 저가, 종가, 거래량)
- 코스피 20일, 60일, 120일 이동평균선
- 코스피 월봉 5월, 20월, 60월 이동평균선
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
            
    return ind

def main():
    print("=" * 65)
    print("한국은행 ECOS + 코스피 일봉/월봉 캔들 데이터 수집 시작...")
    print("=" * 65)
    
    # 1. 뉴스심리지수
    raw_data = fetch_news_sentiment(API_KEY)
    if not raw_data:
        print("[오류] 뉴스심리지수 수집 실패")
        return
    ma_data, trend, zone, latest_ma = calculate_ma_and_zones(raw_data, period=90)
    print(f"✓ 뉴스심리지수: {len(raw_data)}건 수집 완료 (최신: {raw_data[-1]['value']})")
    
    # 2. 코스피 일봉 OHLCV + 20/60/120선
    kospi_daily = fetch_kospi_candles('day', 750)
    ma20_daily = calc_series_ma(kospi_daily, 20)
    ma60_daily = calc_series_ma(kospi_daily, 60)
    ma120_daily = calc_series_ma(kospi_daily, 120)
    print(f"✓ 코스피 일봉: {len(kospi_daily)}건 수집 완료 (최신 종가: {kospi_daily[-1]['close']})")
    
    # 3. 코스피 월봉 OHLCV + 5/20/60선
    kospi_monthly = fetch_kospi_candles('month', 60)
    ma5_monthly = calc_series_ma(kospi_monthly, 5)
    ma20_monthly = calc_series_ma(kospi_monthly, 20)
    ma60_monthly = calc_series_ma(kospi_monthly, 60)
    print(f"✓ 코스피 월봉: {len(kospi_monthly)}건 수집 완료 (최신 월봉 종가: {kospi_monthly[-1]['close']})")
    
    # 4. 보조 지표
    indicators = fetch_market_indicators(API_KEY)
    
    payload = {
        "updatedAt": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "source": "한국은행 ECOS 및 네이버 금융 공식 데이터",
        "trend": trend,
        "zone": zone,
        "latestValue": raw_data[-1]['value'],
        "latestMA": latest_ma,
        "latestDate": raw_data[-1]['date'],
        "indicators": indicators,
        
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
        }
    }
    
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        
    print(f"[성공] {DATA_FILE} 저장 완료!")
    print(f"- 코스피 일봉 캔들: {len(kospi_daily)}개 | 월봉 캔들: {len(kospi_monthly)}개")

if __name__ == '__main__':
    main()
