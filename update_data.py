# -*- coding: utf-8 -*-
"""
한국은행 ECOS API 및 금융 데이터 자동 수집기
- 뉴스심리지수 (523Y001 / A001) 및 3개월(90일) 이동평균 산출
- 코스피 지수, 외국인/기관 수급, 장단기 금리차, 변동성(VIX) 수집
- data.json 생성
"""

import os
import json
import urllib.request
import re
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
    """한국은행 ECOS 뉴스심리지수 수집 (2024년부터 최신까지)"""
    today = datetime.now().strftime('%Y%m%d')
    start_date = '20240101'
    
    url = f"https://ecos.bok.or.kr/api/StatisticSearch/{api_key}/json/kr/1/1000/523Y001/D/{start_date}/{today}/A001"
    res = fetch_json(url)
    if not res or 'StatisticSearch' not in res:
        print("Failed to fetch page 1 of news sentiment")
        return []
    
    total = int(res['StatisticSearch'].get('list_total_count', 0))
    rows = res['StatisticSearch'].get('row', [])
    
    # 1000건 초과 시 다음 페이지 조회
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
            
    # 최근 추세 판정 (최근 15영업일 MA 기울기)
    valid_ma = [v for v in ma_data if v is not None]
    if len(valid_ma) >= 15:
        ma_diff = valid_ma[-1] - valid_ma[-15]
        if ma_diff > 0.8:
            trend = "up"
        elif ma_diff < -0.8:
            trend = "down"
        else:
            trend = "flat"
    else:
        trend = "flat"
        
    latest_val = data[-1]['value']
    latest_ma = valid_ma[-1] if valid_ma else latest_val
    
    # 4단계 국면 판정
    # 1. 110 이상 & 상승세: 과열/고평가
    # 2. 고점 통과 후 하락 반전: 조정 국면
    # 3. 90 이하 & 하락세: 침체/저평가
    # 4. 저점 통과 후 상승 반전: 회복/상승 국면
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

def fetch_market_indicators(api_key):
    """코스피 지수, 수급, 금리차, VIX 수집"""
    ind = {
        "kospi": 2580.0,
        "kospi_change": 0.0,
        "per": 11.2,
        "pbr": 0.91,
        "vkospi": 18.5,
        "foreign_flow": 0,
        "institution_flow": 0,
        "spread": 0.15,
        "treasury_3y": 3.75,
        "treasury_1y": 3.60
    }
    
    # 1. 네이버 금융 KOSPI 정보
    k_data = fetch_json("https://m.stock.naver.com/api/index/KOSPI/basic")
    if k_data:
        try:
            ind["kospi"] = float(k_data.get("closePrice", "2580").replace(",", ""))
            ind["kospi_change"] = float(k_data.get("fluctuationsRatio", "0"))
        except:
            pass
            
    # 2. 외국인/기관 수급
    t_data = fetch_json("https://m.stock.naver.com/api/index/KOSPI/trend")
    if t_data:
        try:
            f_str = t_data.get("foreignValue", "0").replace(",", "").replace("+", "")
            i_str = t_data.get("institutionalValue", "0").replace(",", "").replace("+", "")
            ind["foreign_flow"] = int(f_str)
            ind["institution_flow"] = int(i_str)
        except:
            pass
            
    # 3. 국고채 3년, 1년 금리 및 스프레드 (한국은행 ECOS)
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
    
    # 4. VIX 지수 (Yahoo Finance)
    vix_data = fetch_json("https://query1.finance.yahoo.com/v8/finance/chart/^VIX")
    if vix_data and 'chart' in vix_data and vix_data['chart']['result']:
        try:
            meta = vix_data['chart']['result'][0]['meta']
            ind["vkospi"] = round(float(meta.get('regularMarketPrice', 18.5)), 1)
        except:
            pass
            
    return ind

def main():
    print("=" * 60)
    print("한국은행 ECOS 및 금융 데이터 자동 수집 시작...")
    print(f"인증키: {API_KEY}")
    print("=" * 60)
    
    # 뉴스심리지수 수집
    raw_data = fetch_news_sentiment(API_KEY)
    if not raw_data:
        print("[오류] 뉴스심리지수 수집 실패")
        return
        
    print(f"총 {len(raw_data)}건의 일별 뉴스심리지수 수집 완료")
    
    # 이동평균 및 국면 판정
    ma_data, trend, zone, latest_ma = calculate_ma_and_zones(raw_data, period=90)
    
    # 보조 지표 수집
    indicators = fetch_market_indicators(API_KEY)
    
    payload = {
        "updatedAt": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "source": "한국은행 경제통계시스템 (ECOS) 공식 API",
        "trend": trend,
        "zone": zone,
        "latestValue": raw_data[-1]['value'],
        "latestMA": latest_ma,
        "latestDate": raw_data[-1]['date'],
        "indicators": indicators,
        "timeSeries": raw_data,
        "maSeries": ma_data
    }
    
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        
    print(f"[성공] {DATA_FILE} 저장 완료!")
    print(f"- 최신 뉴스심리지수: {payload['latestValue']} ({payload['latestDate']})")
    print(f"- 90일(3개월) 이동평균: {payload['latestMA']}")
    print(f"- 추세: {trend} | 판정 국면: {zone}")
    print(f"- 코스피: {indicators['kospi']} | 장단기 금리차: {indicators['spread']}%p")

if __name__ == '__main__':
    main()
