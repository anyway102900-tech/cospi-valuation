# -*- coding: utf-8 -*-
"""
한국은행 ECOS API 및 글로벌·수급·밸류에이션 전략 분석 수집기 v4.0
- 뉴스심리지수 (523Y001 / A001) 및 3개월(90일) 이동평균
- 코스피 일봉/월봉 OHLCV + 20/60/120일 이평선 + 거래량 20일선 + RSI(14) + 이격도
- 외국인 국내주식 보유 비중(%) 및 일별·5일누적·20일누적 순매수 추이 (ECOS 802Y001 + 실시간 보간)
- 실시간 코스피 공식 PER·배당수익률(ECOS 901Y014 연동) 및 대표주/시총 연동 실시간 PBR 밴드·일드갭
- 미국 나스닥(^IXIC)·필라델피아 반도체(^SOX)·미 10년물 금리(^TNX)·VIX·원달러 환율 5일 평균선
- 실전 매수 타점 5대 체크리스트 & 종합 스코어(0~100점) & 권장 주식/현금 비중 산출 엔진
"""

import os
import re
import json
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

API_KEY = 'TWSDJSZHSK3FJKI0KDUP'
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, 'data.json')


def fetch_json(url, timeout=12, retries=3):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode('utf-8'))
        except Exception as e:
            if attempt < retries:
                time.sleep(1.2)
            else:
                print(f"Fetch error for {url} (시도 {retries}회 최종 실패): {e}")
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
    """3개월(90일) 이동평균 및 심리 4국면 판정"""
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


def calc_series_ma(candles, period, field='close'):
    """캔들 특정 필드 기준 단순 이동평균선 계산"""
    ma = []
    for i in range(len(candles)):
        if i < period - 1:
            ma.append(None)
        else:
            window = [candles[j][field] for j in range(i - period + 1, i + 1)]
            ma.append(round(sum(window) / period, 2))
    return ma


def calc_rsi_series(candles, period=14):
    """Wilder's Smoothing 기준 RSI(14) 시계열 계산"""
    if not candles or len(candles) <= period:
        return [None] * len(candles)

    rsi = [None] * len(candles)
    gains = []
    losses = []

    for i in range(1, period + 1):
        diff = candles[i]['close'] - candles[i - 1]['close']
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        rsi[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        rsi[period] = round(100.0 - (100.0 / (1.0 + rs)), 2)

    for i in range(period + 1, len(candles)):
        diff = candles[i]['close'] - candles[i - 1]['close']
        gain = max(diff, 0.0)
        loss = max(-diff, 0.0)
        avg_gain = ((avg_gain * (period - 1)) + gain) / period
        avg_loss = ((avg_loss * (period - 1)) + loss) / period

        if avg_loss == 0:
            rsi[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi[i] = round(100.0 - (100.0 / (1.0 + rs)), 2)

    return rsi


def enrich_foreign_cumulative(daily_list, monthly_list):
    """외국인 일별 5일/20일 누적 순매수 및 월별 3개월 누적 순매수 계산"""
    for i in range(len(daily_list)):
        w5 = [daily_list[j]['netBuy'] for j in range(max(0, i - 4), i + 1)]
        w20 = [daily_list[j]['netBuy'] for j in range(max(0, i - 19), i + 1)]
        daily_list[i]['cum5'] = round(sum(w5), 0)
        daily_list[i]['cum20'] = round(sum(w20), 0)

    for i in range(len(monthly_list)):
        w3 = [monthly_list[j]['netBuy'] for j in range(max(0, i - 2), i + 1)]
        monthly_list[i]['cum3'] = round(sum(w3), 0)


def fetch_foreign_holdings(api_key):
    """한국은행 ECOS 802Y001에서 외국인 일별/월별 순매수 및 시총 비중(%) 산출"""
    start_date = '20240101'
    today = datetime.now().strftime('%Y%m%d')

    url_f = f"https://ecos.bok.or.kr/api/StatisticSearch/{api_key}/json/kr/1/1000/802Y001/D/{start_date}/{today}/0030000"
    res_f = fetch_json(url_f)
    rows_f = res_f.get('StatisticSearch', {}).get('row', []) if res_f else []

    url_m = f"https://ecos.bok.or.kr/api/StatisticSearch/{api_key}/json/kr/1/1000/802Y001/D/{start_date}/{today}/0183000"
    res_m = fetch_json(url_m)
    rows_m = res_m.get('StatisticSearch', {}).get('row', []) if res_m else []

    if not rows_f or not rows_m:
        print("[오류] 외국인 순매수/시총 데이터 조회 실패")
        return [], []

    mcap_dict = {r['TIME']: float(r['DATA_VALUE']) * 10 for r in rows_m}

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
            prev_t = rows_f[i - 1]['TIME']
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
            "foreignHoldingVal": round(curr_foreign_val / 10000, 1)
        }
        daily_list.append(item)

        month_key = t[:6]
        if month_key not in month_groups:
            month_groups[month_key] = []
        month_groups[month_key].append(item)

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

    enrich_foreign_cumulative(daily_list, monthly_list)
    return daily_list, monthly_list


def parse_korean_mcap_trillion(mc_str):
    """'1,002조 6,368억' 형태의 문자열을 조원 단위 float로 변환"""
    if not mc_str:
        return 0.0
    s = mc_str.replace(',', '').strip()
    tril = 0.0
    m_tril = re.search(r'([0-9]+(?:\.[0-9]+)?)\s*조', s)
    m_eok = re.search(r'([0-9]+(?:\.[0-9]+)?)\s*억', s)
    if m_tril:
        tril += float(m_tril.group(1))
    if m_eok:
        tril += float(m_eok.group(1)) / 10000.0
    return tril


def fetch_yahoo_quote(symbol):
    """야후 파이낸스 차트 API에서 최신가, 전일 대비 등락률, 5일 추세 수집"""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=10d"
    data = fetch_json(url)
    if not data or 'chart' not in data or not data['chart'].get('result'):
        return None
    try:
        res = data['chart']['result'][0]
        meta = res.get('meta', {})
        quotes = res.get('indicators', {}).get('quote', [{}])[0]
        closes = [c for c in (quotes.get('close') or []) if c is not None]
        highs = [h for h in (quotes.get('high') or []) if h is not None]

        curr = float(meta.get('regularMarketPrice', closes[-1] if closes else 0.0))
        if len(closes) >= 2:
            prev = float(closes[-2])
        else:
            prev = float(meta.get('chartPreviousClose', curr))

        diff = curr - prev
        pct = (diff / prev) * 100.0 if prev != 0 else 0.0
        ma5 = sum(closes[-5:]) / len(closes[-5:]) if len(closes) >= 5 else curr
        recent_high = max(highs[-5:]) if len(highs) >= 2 else curr

        return {
            "price": round(curr, 2),
            "change": round(diff, 2),
            "ratio": round(pct, 2),
            "ma5": round(ma5, 2),
            "recentHigh": round(recent_high, 2)
        }
    except Exception as e:
        print(f"[경고] Yahoo quote 파싱 예외 ({symbol}): {e}")
        return None


def fetch_kospi_valuation_and_breadth(api_key, curr_kospi, treasury_3y, cached_val=None):
    """
    한국은행 ECOS(901Y014 공식 월간 PER·배당수익률) + 당일 코스피 비례 환산
    및 대표 시총 상위주 실측 자본총계 기반 실시간 코스피 PBR 밴드 산출
    """
    cached_val = cached_val or {}
    val = {
        "per": cached_val.get("per", 8.2),
        "pbr": cached_val.get("pbr", 0.98),
        "divYield": cached_val.get("divYield", 1.32),
        "earningsYield": cached_val.get("earningsYield", 12.2),
        "yieldGap": cached_val.get("yieldGap", 8.5),
        "baseMonth": cached_val.get("baseMonth", "2026-10"),
        "high52w": cached_val.get("high52w", 9385.59),
        "low52w": cached_val.get("low52w", 3892.08),
        "drawdownPct": cached_val.get("drawdownPct", -49.8),
        "riseCount": cached_val.get("riseCount", 420),
        "fallCount": cached_val.get("fallCount", 440),
        "programNetBuy": cached_val.get("programNetBuy", 0)
    }

    # 1. 한국은행 ECOS 901Y014 공식 코스피 PER / 배당수익률 / 기준 종가 조회
    today_ym = datetime.now().strftime('%Y%m')
    start_ym = (datetime.now() - timedelta(days=365)).strftime('%Y%m')
    try:
        res_cls = fetch_json(f"https://ecos.bok.or.kr/api/StatisticSearch/{api_key}/json/kr/1/15/901Y014/M/{start_ym}/{today_ym}/1070000")
        res_per = fetch_json(f"https://ecos.bok.or.kr/api/StatisticSearch/{api_key}/json/kr/1/15/901Y014/M/{start_ym}/{today_ym}/1110000")
        res_div = fetch_json(f"https://ecos.bok.or.kr/api/StatisticSearch/{api_key}/json/kr/1/15/901Y014/M/{start_ym}/{today_ym}/1100000")

        rows_cls = res_cls.get('StatisticSearch', {}).get('row', []) if res_cls else []
        rows_per = res_per.get('StatisticSearch', {}).get('row', []) if res_per else []
        rows_div = res_div.get('StatisticSearch', {}).get('row', []) if res_div else []

        if rows_cls and rows_per:
            base_ym = rows_per[-1]['TIME']
            base_per = float(rows_per[-1]['DATA_VALUE'])
            cls_map = {r['TIME']: float(r['DATA_VALUE']) for r in rows_cls}
            div_map = {r['TIME']: float(r['DATA_VALUE']) for r in rows_div}

            base_kospi = cls_map.get(base_ym, float(rows_cls[-1]['DATA_VALUE']))
            base_div = div_map.get(base_ym, 1.1)

            if base_kospi > 0 and curr_kospi > 0:
                scale = curr_kospi / base_kospi
                live_per = round(base_per * scale, 2)
                live_div = round(base_div / scale, 2)
                ey = round(100.0 / live_per, 2) if live_per > 0 else 0.0
                yg = round(ey - treasury_3y, 2)

                val["per"] = live_per
                val["divYield"] = live_div
                val["earningsYield"] = ey
                val["yieldGap"] = yg
                val["baseMonth"] = f"{base_ym[:4]}-{base_ym[4:6]}"
    except Exception as e:
        print("[경고] ECOS 공식 밸류에이션 환산 예외:", e)

    # 2. 네이버 KOSPI integration (52주 고저, 상승/하락 종목수, 프로그램 수급)
    try:
        ig = fetch_json("https://m.stock.naver.com/api/index/KOSPI/integration")
        if ig:
            t_infos = {x.get('code'): x.get('value') for x in ig.get('totalInfos', [])}
            if t_infos.get('highPriceOf52Weeks'):
                val["high52w"] = float(str(t_infos['highPriceOf52Weeks']).replace(',', ''))
            if t_infos.get('lowPriceOf52Weeks'):
                val["low52w"] = float(str(t_infos['lowPriceOf52Weeks']).replace(',', ''))
            if val["high52w"] > 0 and curr_kospi > 0:
                val["drawdownPct"] = round(((curr_kospi - val["high52w"]) / val["high52w"]) * 100.0, 2)

            ud = ig.get('upDownStockInfo') or {}
            if ud:
                val["riseCount"] = int(ud.get('riseCount', 0)) + int(ud.get('upperCount', 0))
                val["fallCount"] = int(ud.get('fallCount', 0)) + int(ud.get('lowerCount', 0))

            prog = ig.get('programTrendInfo') or {}
            if prog.get('indexTotalReal'):
                p_str = str(prog['indexTotalReal']).replace(',', '').replace('+', '')
                val["programNetBuy"] = int(float(p_str))
    except Exception as e:
        print("[경고] KOSPI integration 수집 예외:", e)

    # 3. 코스피 대표 시총 상위 핵심주 PBR 샘플링 + 지수 연동 실시간 PBR 산출
    try:
        top_codes = ['005930', '000660', '005380', '105560', '000270', '055550', '005490', '068270']
        sample_mcap = 0.0
        sample_book = 0.0
        for code in top_codes:
            st = fetch_json(f"https://m.stock.naver.com/api/stock/{code}/integration", timeout=6, retries=2)
            if not st:
                continue
            infos = {x.get('code'): x.get('value') for x in st.get('totalInfos', [])}
            pbr_raw = str(infos.get('pbr') or '').replace('배', '').replace(',', '').strip()
            mc_tril = parse_korean_mcap_trillion(infos.get('marketValue', ''))
            if pbr_raw and mc_tril > 0:
                pbr_v = float(pbr_raw)
                if pbr_v > 0:
                    sample_mcap += mc_tril
                    sample_book += (mc_tril / pbr_v)

        # 대형 수출·금융주 실측 자본 대비 비샘플 전통주(평균 PBR ~0.62배 비중 보정) 결합
        if sample_mcap > 0 and sample_book > 0:
            top_pbr = sample_mcap / sample_book
            # 코스피 전체 PBR: 대형주(52% 가중)와 일반 상장사 평균(48% 가중, 기준지수 비례) 조화 산출
            broad_pbr = 0.65 * (curr_kospi / 4800.0)
            est_kospi_pbr = round(1.0 / ((0.52 / top_pbr) + (0.48 / max(0.35, broad_pbr))), 2)
            val["pbr"] = est_kospi_pbr
    except Exception as e:
        print("[경고] 코스피 실시간 PBR 산출 예외:", e)

    return val


def fetch_market_indicators(api_key, cached_ind=None):
    """핵심 보조 지표 + 글로벌 선행 지표(나스닥·필라델피아 반도체·미10년물) + 환율 5일 평균선 수집"""
    cached_ind = cached_ind or {}
    ind = {
        "kospi": cached_ind.get("kospi", 4711.21),
        "kospi_change": cached_ind.get("kospi_change", 0.0),
        "per": cached_ind.get("per", 8.0),
        "pbr": cached_ind.get("pbr", 0.98),
        "div_yield": cached_ind.get("div_yield", 1.32),
        "yield_gap": cached_ind.get("yield_gap", 8.5),
        "vkospi": cached_ind.get("vkospi", 25.0),
        "vkospi_change": cached_ind.get("vkospi_change", 0.0),
        "vkospi_peaked": cached_ind.get("vkospi_peaked", False),
        "foreign_flow": cached_ind.get("foreign_flow", 0),
        "institution_flow": cached_ind.get("institution_flow", 0),
        "personal_flow": cached_ind.get("personal_flow", 0),
        "program_flow": cached_ind.get("program_flow", 0),
        "spread": cached_ind.get("spread", 0.07),
        "treasury_3y": cached_ind.get("treasury_3y", 3.639),
        "treasury_1y": cached_ind.get("treasury_1y", 3.568),
        "usdkrw": cached_ind.get("usdkrw", 1342.8),
        "usdkrw_change": cached_ind.get("usdkrw_change", 0.0),
        "usdkrw_ratio": cached_ind.get("usdkrw_ratio", 0.0),
        "usdkrw_ma5": cached_ind.get("usdkrw_ma5", 1343.0),
        "dxy": cached_ind.get("dxy", 103.71),
        "dxy_change": cached_ind.get("dxy_change", 0.0),
        "dxy_ratio": cached_ind.get("dxy_ratio", 0.0),
        "nasdaq": cached_ind.get("nasdaq", 23585.8),
        "nasdaq_change": cached_ind.get("nasdaq_change", 0.0),
        "nasdaq_ratio": cached_ind.get("nasdaq_ratio", 0.0),
        "sox": cached_ind.get("sox", 8774.9),
        "sox_change": cached_ind.get("sox_change", 0.0),
        "sox_ratio": cached_ind.get("sox_ratio", 0.0),
        "us10y": cached_ind.get("us10y", 4.85),
        "us10y_change": cached_ind.get("us10y_change", 0.0)
    }

    # 1. 코스피 기본 시세
    k_data = fetch_json("https://m.stock.naver.com/api/index/KOSPI/basic")
    if k_data:
        try:
            ind["kospi"] = float(str(k_data.get("closePrice", ind["kospi"])).replace(",", ""))
            ind["kospi_change"] = float(str(k_data.get("fluctuationsRatio", "0")).replace(",", ""))
        except Exception:
            pass

    # 2. 투자자별 매매동향
    t_data = fetch_json("https://m.stock.naver.com/api/index/KOSPI/trend")
    if t_data:
        try:
            f_str = str(t_data.get("foreignValue", "0")).replace(",", "").replace("+", "")
            i_str = str(t_data.get("institutionalValue", "0")).replace(",", "").replace("+", "")
            p_str = str(t_data.get("personalValue", "0")).replace(",", "").replace("+", "")
            ind["foreign_flow"] = int(float(f_str))
            ind["institution_flow"] = int(float(i_str))
            ind["personal_flow"] = int(float(p_str))
        except Exception:
            pass

    # 3. 국고채 금리 (3년 / 1년)
    today = datetime.now().strftime('%Y%m%d')
    start_search = (datetime.now() - timedelta(days=30)).strftime('%Y%m%d')

    res_3y = fetch_json(f"https://ecos.bok.or.kr/api/StatisticSearch/{api_key}/json/kr/1/10/817Y002/D/{start_search}/{today}/010200000")
    if res_3y and 'StatisticSearch' in res_3y:
        rows = res_3y['StatisticSearch'].get('row', [])
        if rows:
            ind["treasury_3y"] = float(rows[-1]['DATA_VALUE'])

    res_1y = fetch_json(f"https://ecos.bok.or.kr/api/StatisticSearch/{api_key}/json/kr/1/10/817Y002/D/{start_search}/{today}/010190000")
    if res_1y and 'StatisticSearch' in res_1y:
        rows = res_1y['StatisticSearch'].get('row', [])
        if rows:
            ind["treasury_1y"] = float(rows[-1]['DATA_VALUE'])

    ind["spread"] = round(ind["treasury_3y"] - ind["treasury_1y"], 2)

    # 4. 원/달러 환율 현재치 + 최근 5일 이동평균
    try:
        fx_data = fetch_json("https://m.stock.naver.com/front-api/marketIndex/productDetail?category=exchange&reutersCode=FX_USDKRW")
        if fx_data and fx_data.get('result'):
            res_fx = fx_data['result']
            price = float(str(res_fx.get('closePrice', ind["usdkrw"])).replace(',', ''))
            change = float(str(res_fx.get('fluctuations', '0')).replace(',', ''))
            ratio = float(str(res_fx.get('fluctuationsRatio', '0')).replace(',', ''))
            f_type = res_fx.get('fluctuationsType', {}).get('name', 'RISING')
            if f_type in ('FALLING', 'DROP', '3', '5') and change > 0:
                change = -change
                ratio = -ratio
            ind["usdkrw"] = price
            ind["usdkrw_change"] = change
            ind["usdkrw_ratio"] = ratio

        fx_hist = fetch_json("https://m.stock.naver.com/front-api/chart/pricesByPeriod?reutersCode=FX_USDKRW&category=exchange&chartInfoType=marketindex&scriptChartType=areaMonth")
        if fx_hist and fx_hist.get('result', {}).get('priceInfos'):
            p_infos = fx_hist['result']['priceInfos']
            fx_closes = [float(x['closePrice']) for x in p_infos[-5:] if x.get('closePrice')]
            if fx_closes:
                ind["usdkrw_ma5"] = round(sum(fx_closes) / len(fx_closes), 2)
    except Exception as e:
        print("[경고] 네이버 환율 수집 예외:", e)

    # 5. 글로벌 지표 (VIX, DXY, 나스닥 ^IXIC, 필라델피아 반도체 ^SOX, 미 10년물 ^TNX)
    vix_q = fetch_yahoo_quote("^VIX")
    if vix_q:
        ind["vkospi"] = round(vix_q["price"], 1)
        ind["vkospi_change"] = round(vix_q["change"], 2)
        ind["vkospi_peaked"] = bool(vix_q["price"] < vix_q["recentHigh"] * 0.96 or vix_q["change"] < 0)

    dxy_q = fetch_yahoo_quote("DX-Y.NYB")
    if dxy_q:
        ind["dxy"] = dxy_q["price"]
        ind["dxy_change"] = dxy_q["change"]
        ind["dxy_ratio"] = dxy_q["ratio"]

    ixic_q = fetch_yahoo_quote("^IXIC")
    if ixic_q:
        ind["nasdaq"] = ixic_q["price"]
        ind["nasdaq_change"] = ixic_q["change"]
        ind["nasdaq_ratio"] = ixic_q["ratio"]

    sox_q = fetch_yahoo_quote("^SOX")
    if sox_q:
        ind["sox"] = sox_q["price"]
        ind["sox_change"] = sox_q["change"]
        ind["sox_ratio"] = sox_q["ratio"]

    tnx_q = fetch_yahoo_quote("^TNX")
    if tnx_q:
        ind["us10y"] = tnx_q["price"]
        ind["us10y_change"] = tnx_q["change"]

    return ind


# ==========================================================
# 📉 다우존스 / 코스피 고점 대비 등락률 (52주 고점 · 역대 고점)
# ==========================================================
def _ts_to_date(ts):
    try:
        return datetime.fromtimestamp(int(ts), timezone.utc).strftime('%Y-%m-%d')
    except Exception:
        return ''


def fetch_dow_drawdown(cached=None):
    """야후 파이낸스 ^DJI: 현재가 · 전일비 · 52주 고점 · 역대 고점 대비 등락률"""
    out = dict(cached or {})
    try:
        d1 = fetch_json("https://query1.finance.yahoo.com/v8/finance/chart/%5EDJI?interval=1d&range=1y")
        if d1 and d1.get('chart', {}).get('result'):
            r = d1['chart']['result'][0]
            ts = r.get('timestamp') or []
            q = r.get('indicators', {}).get('quote', [{}])[0]
            highs = q.get('high') or []
            closes = [c for c in (q.get('close') or []) if c is not None]
            price = float(r['meta'].get('regularMarketPrice', closes[-1] if closes else 0))
            prev = float(closes[-2]) if len(closes) >= 2 else price
            hi_idx = max((i for i in range(len(highs)) if highs[i] is not None), key=lambda i: highs[i])
            high52 = float(highs[hi_idx])
            out.update({
                "price": round(price, 2),
                "change": round(price - prev, 2),
                "ratio": round((price - prev) / prev * 100.0, 2) if prev else 0.0,
                "high52": round(high52, 2),
                "high52Date": _ts_to_date(ts[hi_idx]) if hi_idx < len(ts) else '',
                "dd52": round((price - high52) / high52 * 100.0, 2) if high52 else 0.0,
            })

        dm = fetch_json("https://query1.finance.yahoo.com/v8/finance/chart/%5EDJI?interval=1mo&range=max")
        if dm and dm.get('chart', {}).get('result'):
            r = dm['chart']['result'][0]
            ts = r.get('timestamp') or []
            highs = r.get('indicators', {}).get('quote', [{}])[0].get('high') or []
            valid = [i for i in range(len(highs)) if highs[i] is not None]
            if valid:
                ai = max(valid, key=lambda i: highs[i])
                ath = max(float(highs[ai]), float(out.get('high52', 0)))
                ath_date = _ts_to_date(ts[ai])[:7] if float(highs[ai]) >= float(out.get('high52', 0)) else out.get('high52Date', '')
                price = float(out.get('price', 0))
                out.update({
                    "ath": round(ath, 2),
                    "athDate": ath_date,
                    "ddAth": round((price - ath) / ath * 100.0, 2) if ath else 0.0,
                })
    except Exception as e:
        print("[경고] 다우존스 고점대비 산출 예외:", e)
    return out


def calc_kospi_drawdown(kospi_daily, kospi_monthly, ind):
    """코스피 일봉(최근 250거래일)·월봉(최근 5년) 기반 52주/역대(5년) 고점 대비 등락률"""
    out = {}
    if not kospi_daily:
        return out
    price = float(kospi_daily[-1]['close'])
    last250 = kospi_daily[-250:]
    h52 = max(last250, key=lambda c: c['high'])
    out.update({
        "price": round(price, 2),
        "ratio": float(ind.get('kospi_change', 0.0)),
        "high52": h52['high'],
        "high52Date": h52['date'],
        "dd52": round((price - h52['high']) / h52['high'] * 100.0, 2),
        "low52": min(c['low'] for c in last250),
    })
    out["upFromLow52"] = round((price - out["low52"]) / out["low52"] * 100.0, 2) if out["low52"] else 0.0
    pool = list(kospi_monthly or []) + list(kospi_daily)
    hath = max(pool, key=lambda c: c['high'])
    out.update({
        "ath": hath['high'],
        "athDate": str(hath['date'])[:7],
        "ddAth": round((price - hath['high']) / hath['high'] * 100.0, 2),
    })
    return out


# ==========================================================
# 📰 외부(글로벌) · 내부(국내) 요인 뉴스 헤드라인 심층 분석
# ==========================================================
NEWS_POS = {
    '폭등': 3, '급등': 3, '호실적': 3, '서프라이즈': 2, '반등': 2, '순매수': 2, '사자': 2, '회복': 2,
    '호조': 2, '인하': 2, '완화': 2, '부양': 2, '강세': 2, '랠리': 2, '훈풍': 2, '안도': 2, '타결': 2,
    '기대': 1, '대책': 1, '최고': 1, '돌파': 1, '개선': 1, '상승': 1, '유입': 1, '저가매수': 2, '귀환': 2
}
NEWS_NEG = {
    '폭락': 3, '급락': 3, '반토막': 3, '패닉': 3, '투매': 3, '디폴트': 3, '파산': 3, '반대매매': 3,
    '순매도': 2, '팔자': 2, '공포': 2, '증발': 2, '고금리': 2, '위기': 2, '약세': 2, '충격': 2, '쇼크': 2,
    '침체': 2, '전쟁': 2, '불안': 2, '긴축': 2, '이탈': 2, '하락': 1, '매도': 1, '우려': 1, '긴장': 1,
    '인상': 1, '부채': 1, '관세': 1, '경고': 1, '둔화': 1, '손실': 1, '최저': 1, '역주행': 1, '굴욕': 1
}

EXT_THEMES = {
    '미 연준·금리': (['연준', 'Fed', 'FOMC', '파월', '금리', '국채', '긴축'],
                 '금리 부담 완화로 성장주·신흥국 자금 유입 여건 개선', '고금리 장기화 우려로 글로벌 위험자산 할인율 상승 압력'),
    '미 물가·경기': (['물가', 'CPI', 'PCE', '인플레', '성장률', 'GDP', '고용', '실업', '소비'],
                 '연착륙 기대가 위험선호를 지지', '경기·물가 불확실성이 변동성을 키우는 구간'),
    'AI·반도체 빅테크': (['AI', '반도체', '엔비디아', '빅테크', '기술주', '나스닥', '마이크론'],
                   '반도체 업황 기대가 국내 대형 반도체주에 직접 호재', 'AI·반도체 밸류 부담이 국내 반도체 대형주 수급에 직격탄'),
    '달러·환율·원자재': (['달러', '환율', '엔화', '위안', '유가', '원유', '금값', '원자재'],
                   '달러 약세·원자재 안정으로 신흥국 증시 우호적', '강달러·원자재 변동이 외국인 환차손 우려를 자극'),
    '지정학·무역': (['관세', '무역', '전쟁', '중동', '러시아', '우크라', '중국', '트럼프', '제재'],
               '지정학·무역 긴장 완화로 수출주 심리 개선', '지정학·무역 리스크가 수출 중심 국내 증시에 부담'),
}
INT_THEMES = {
    '외국인·기관 수급': (['외국인', '기관', '순매수', '순매도', '사자', '팔자', '수급', '개인'],
                   '외국인 귀환이 지수 하단을 지지 — 연속성(5일 누적) 확인이 관건', '외국인 이탈이 지속되며 지수 하방 압력 확대'),
    '정부 정책·증시대책': (['정부', '대책', '금융위', '밸류업', '공매도', '세제', '부양', '증시안정', '정책'],
                    '증시 안정 정책 기대가 투자심리를 떠받치는 국면', '정책 불확실성이 시장 신뢰 회복을 지연'),
    '반도체·대형주 실적': (['삼성전자', '하이닉스', '반도체', 'HBM', '실적', '영업이익', '대형주'],
                    '대형 반도체주 반등이 지수 회복을 견인할 가능성', '대형 반도체주 약세가 지수 전체를 끌어내리는 구간'),
    '한은 금리·환율': (['한은', '한국은행', '기준금리', '금통위', '원화', '환율'],
                 '금리·환율 안정이 외국인 수급 환경을 개선', '원화 약세·금리 부담이 외국인 매도 요인으로 작용'),
    '신용·반대매매 리스크': (['반대매매', '신용', '빚투', '마진콜', '레버리지', '청산'],
                     '신용 물량 정리로 악성 매물 부담 완화', '반대매매·신용 청산 매물이 추가 하락을 증폭'),
}

EXT_QUERIES = ['뉴욕증시', '미국 연준 금리', '글로벌 증시', '미국 반도체 엔비디아']
INT_QUERIES = ['코스피 마감', '코스피 외국인', '증시 대책 정부', '삼성전자 SK하이닉스 주가']


def _score_title(title):
    text = title
    pos = neg = 0
    for w in sorted(NEWS_POS.keys() | NEWS_NEG.keys(), key=len, reverse=True):
        if w in text:
            if w in NEWS_POS:
                pos += NEWS_POS[w]
            else:
                neg += NEWS_NEG[w]
            text = text.replace(w, ' ')
    return pos, neg


def _fetch_google_news(queries, per_query=15):
    import urllib.parse
    seen = set()
    items = []
    for q in queries:
        url = 'https://news.google.com/rss/search?q=' + urllib.parse.quote(q + ' when:1d') + '&hl=ko&gl=KR&ceid=KR:ko'
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            root = ET.fromstring(urllib.request.urlopen(req, timeout=12).read())
        except Exception as e:
            print(f"[경고] 뉴스 RSS 수집 실패 ({q}): {e}")
            continue
        for it in root.findall('.//item')[:per_query]:
            raw = (it.findtext('title') or '').strip()
            if ' - ' in raw:
                title, source = raw.rsplit(' - ', 1)
            else:
                title, source = raw, ''
            key = re.sub(r'[^0-9A-Za-z가-힣]', '', title)[:28]
            if not key or key in seen:
                continue
            seen.add(key)
            items.append({"title": title.strip(), "source": source.strip(),
                          "link": it.findtext('link') or '', "pubDate": it.findtext('pubDate') or ''})
    return items


def analyze_news_factor(items, themes, label):
    """헤드라인 호재/악재 점수 + 핵심 테마 판별 → 한줄 평가 생성"""
    if not items:
        return None
    theme_stat = {t: {"cnt": 0, "pos": 0, "neg": 0, "best": None} for t in themes}
    tot_pos = tot_neg = 0
    scored = []
    for it in items:
        p, n = _score_title(it['title'])
        it = dict(it, pos=p, neg=n, score=p - n)
        scored.append(it)
        tot_pos += p
        tot_neg += n
        for t, (kws, _, _) in themes.items():
            if any(k.lower() in it['title'].lower() for k in kws):
                st = theme_stat[t]
                st["cnt"] += 1
                st["pos"] += p
                st["neg"] += n
                if st["best"] is None or abs(it['score']) > abs(st["best"]['score']):
                    st["best"] = it

    top_theme = max(theme_stat, key=lambda t: (theme_stat[t]["cnt"], theme_stat[t]["pos"] + theme_stat[t]["neg"]))
    st = theme_stat[top_theme]
    denom = tot_pos + tot_neg
    tone_score = round((tot_pos - tot_neg) / denom * 100) if denom else 0
    if tone_score >= 20:
        tone, tone_cls, icon = "호재 우위", "pos", "🟢"
    elif tone_score <= -20:
        tone, tone_cls, icon = "악재 우위", "neg", "🔴"
    else:
        tone, tone_cls, icon = "호재·악재 혼조", "mix", "🟡"

    pos_impl, neg_impl = themes[top_theme][1], themes[top_theme][2]
    theme_tone = st["pos"] - st["neg"]
    if theme_tone > 0:
        impl = pos_impl
    elif theme_tone < 0:
        impl = neg_impl
    else:
        impl = pos_impl if tone_score > 0 else neg_impl if tone_score < 0 else "방향성 탐색 구간 — 지표 확인 후 대응"

    rep = st["best"] or max(scored, key=lambda x: abs(x['score']))
    rep_title = rep['title'] if len(rep['title']) <= 48 else rep['title'][:47] + '…'
    line = f"{icon} [{label}] '{top_theme}' 이슈가 핵심 — \"{rep_title}\" ➔ {impl}."

    heads = sorted(scored, key=lambda x: abs(x['score']), reverse=True)[:5]
    return {
        "line": line,
        "tone": tone,
        "toneClass": tone_cls,
        "toneScore": tone_score,
        "theme": top_theme,
        "themeCount": st["cnt"],
        "posCount": sum(1 for x in scored if x['score'] > 0),
        "negCount": sum(1 for x in scored if x['score'] < 0),
        "total": len(scored),
        "headlines": [{"title": h['title'], "source": h['source'], "link": h['link'], "score": h['score']} for h in heads],
    }


def fetch_news_factors(cached=None):
    cached = cached or {}
    ext_items = _fetch_google_news(EXT_QUERIES)
    int_items = _fetch_google_news(INT_QUERIES)
    ext = analyze_news_factor(ext_items, EXT_THEMES, "외부 요인") or cached.get('external')
    inn = analyze_news_factor(int_items, INT_THEMES, "내부 요인") or cached.get('internal')
    return {
        "updatedAt": datetime.now().strftime('%Y-%m-%d %H:%M'),
        "external": ext,
        "internal": inn,
    }


def evaluate_buy_strategy(raw_nsi, kospi_candles, ma20_daily, rsi_daily, foreign_daily, ind, valuation):
    """
    🎯 실전 매수 타점 5대 체크리스트 & 종합 스코어(0~100점) 및 권장 현금/주식 비중 산출
    각 항목 최대 20점 (pass=20, partial=10~12, fail=0~4)
    """
    curr_kospi = kospi_candles[-1]['close'] if kospi_candles else ind.get('kospi', 4700)
    prev_kospi = kospi_candles[-2]['close'] if len(kospi_candles) >= 2 else curr_kospi
    is_bull_candle = kospi_candles[-1]['close'] >= kospi_candles[-1]['open'] if kospi_candles else False

    curr_ma20 = ma20_daily[-1] if (ma20_daily and ma20_daily[-1]) else curr_kospi
    disparity20 = round((curr_kospi / curr_ma20) * 100.0, 2) if curr_ma20 > 0 else 100.0

    valid_rsi = [r for r in rsi_daily if r is not None]
    curr_rsi = valid_rsi[-1] if valid_rsi else 50.0
    prev_rsi = valid_rsi[-2] if len(valid_rsi) >= 2 else curr_rsi
    min_rsi_5d = min(valid_rsi[-5:]) if len(valid_rsi) >= 5 else curr_rsi

    pbr = valuation.get('pbr', 1.0)
    per = valuation.get('per', 10.0)
    drawdown = valuation.get('drawdownPct', 0.0)

    checklist = []

    # ① 가격·밸류 바닥 (PBR / PER / 20일 이격도)
    if (pbr <= 1.00 or per <= 9.0) and (disparity20 <= 94.0 or drawdown <= -25.0):
        c1_status, c1_score = "pass", 20
        c1_badge = "✅ 조건 충족"
        c1_desc = f"PBR {pbr:.2f}배 · PER {per:.1f}배 저평가 영역 및 20일 이격도 {disparity20:.1f}% 과매도 메리트 확보"
    elif pbr <= 1.12 or per <= 10.5 or disparity20 <= 97.0:
        c1_status, c1_score = "partial", 12
        c1_badge = "⏳ 저점 접근"
        c1_desc = f"PBR {pbr:.2f}배 · PER {per:.1f}배 (20일 이격도 {disparity20:.1f}%) — 중립 이하 분할 관심권"
    else:
        c1_status, c1_score = "fail", 2
        c1_badge = "❌ 밸류 부담"
        c1_desc = f"PBR {pbr:.2f}배 · PER {per:.1f}배 (20일 이격도 {disparity20:.1f}%) — 가격 메리트 부족"

    checklist.append({
        "id": "valuation",
        "title": "① 가격·밸류 바닥 (PBR · 이격도)",
        "target": "PBR ≤ 1.0배(PER ≤ 9배) & 이격도 ≤ 94%",
        "current": f"PBR {pbr:.2f}배 / 이격도 {disparity20:.1f}%",
        "status": c1_status,
        "badge": c1_badge,
        "score": c1_score,
        "desc": c1_desc
    })

    # ② 기술적 바닥 (일봉 RSI 14 과매도 후 확실한 양봉/반등 전환)
    price_chg_pct = ((curr_kospi - prev_kospi) / prev_kospi * 100.0) if prev_kospi > 0 else 0.0
    rsi_turning_up = (curr_rsi >= prev_rsi + 1.2) or (curr_rsi > prev_rsi and is_bull_candle and price_chg_pct >= 0.8)
    if min_rsi_5d <= 35.0 and rsi_turning_up:
        c2_status, c2_score = "pass", 20
        c2_badge = "✅ 반등 포착"
        c2_desc = f"일봉 RSI({curr_rsi:.1f})가 과매도권({min_rsi_5d:.1f}) 통과 후 양봉 반등 전환 성공"
    elif curr_rsi <= 37.0 or min_rsi_5d <= 35.0:
        c2_status, c2_score = "partial", 12
        c2_badge = "⏳ 과매도 대기"
        c2_desc = f"일봉 RSI {curr_rsi:.1f}(5일 저점 {min_rsi_5d:.1f}) 과매도권 진입 — 확실한 양봉 반등 대기"
    elif 37.0 < curr_rsi <= 55.0 and curr_kospi >= curr_ma20:
        c2_status, c2_score = "pass", 18
        c2_badge = "✅ 추세 회복"
        c2_desc = f"일봉 RSI {curr_rsi:.1f} 안정권 및 20일 이동평균선({curr_ma20:,.0f}P) 안착"
    else:
        c2_status, c2_score = "fail", 4
        c2_badge = "❌ 하락 진행"
        c2_desc = f"일봉 RSI {curr_rsi:.1f} (20일선 {curr_ma20:,.0f}P 하회) — 바닥 다지기 미완성"

    checklist.append({
        "id": "technical",
        "title": "② 기술적 바닥 (RSI 과매도 반등)",
        "target": "일봉 RSI ≤ 35 찍고 상승 전환 (또는 20일선 회복)",
        "current": f"RSI {curr_rsi:.1f} (5일 저점 {min_rsi_5d:.1f})",
        "status": c2_status,
        "badge": c2_badge,
        "score": c2_score,
        "desc": c2_desc
    })

    # ③ 심리 바닥 (뉴스심리지수 침체 통과 후 3일 연속 우상향)
    nsi_vals = [x['value'] for x in raw_nsi[-5:]] if len(raw_nsi) >= 5 else [95.0]
    curr_nsi = nsi_vals[-1]
    min_nsi_10d = min([x['value'] for x in raw_nsi[-10:]]) if len(raw_nsi) >= 10 else curr_nsi
    nsi_3d_up = len(nsi_vals) >= 4 and (nsi_vals[-1] > nsi_vals[-2] > nsi_vals[-3])

    if min_nsi_10d <= 93.0 and nsi_3d_up:
        c3_status, c3_score = "pass", 20
        c3_badge = "✅ 심리 턴어라운드"
        c3_desc = f"심리지수({curr_nsi:.1f})가 침체 저점({min_nsi_10d:.1f}) 통과 후 3일 연속 회복"
    elif curr_nsi <= 93.0:
        c3_status, c3_score = "partial", 10
        c3_badge = "⏳ 공포 극대화"
        c3_desc = f"심리지수 {curr_nsi:.1f} 침체권 진입 — 하락 멈춤 및 3일 연속 반등 대기"
    elif 93.0 < curr_nsi <= 105.0 and len(nsi_vals) >= 2 and nsi_vals[-1] >= nsi_vals[-2]:
        c3_status, c3_score = "partial", 12
        c3_badge = "⏳ 심리 보합"
        c3_desc = f"심리지수 {curr_nsi:.1f} 중립 회복 흐름 유지"
    else:
        c3_status, c3_score = "fail", 2
        c3_badge = "❌ 심리 냉각중"
        c3_desc = f"심리지수 {curr_nsi:.1f} — 투자심리 위축 진행 중 (바닥 미확인)"

    checklist.append({
        "id": "sentiment",
        "title": "③ 심리 바닥 (NSI 공포 통과 후 반등)",
        "target": "NSI ≤ 92 통과 후 3일 연속 우상향 전환",
        "current": f"NSI {curr_nsi:.2f} ({'반등중' if (len(nsi_vals)>=2 and nsi_vals[-1]>nsi_vals[-2]) else '하락중'})",
        "status": c3_status,
        "badge": c3_badge,
        "score": c3_score,
        "desc": c3_desc
    })

    # ④ 수급 바닥 (외국인 5거래일 누적 순매수 전환) - 하루짜리 가짜 반등 제거 핵심!
    cum5 = foreign_daily[-1].get('cum5', 0) if foreign_daily else 0
    cum20 = foreign_daily[-1].get('cum20', 0) if foreign_daily else 0
    last3_f = [x['netBuy'] for x in foreign_daily[-3:]] if len(foreign_daily) >= 3 else [0]
    f_3d_buy = all(v > 0 for v in last3_f)

    if cum5 > 0 and (cum20 > -10000 or f_3d_buy):
        c4_status, c4_score = "pass", 20
        c4_badge = "✅ 수급 귀환"
        c4_desc = f"외국인 5일 누적 +{int(cum5):,}억 순매수 전환 완료 — 진정한 추세 수급 유입"
    elif cum5 > 0 or (foreign_daily and foreign_daily[-1]['netBuy'] > 0 and len(last3_f) >= 2 and last3_f[-2] > 0):
        c4_status, c4_score = "partial", 12
        c4_badge = "⏳ 유입 초기"
        c4_desc = f"외국인 단기 매수 유입(5일 누적 {int(cum5):+,}억) — 연속성 확인 필요"
    else:
        c4_status, c4_score = "fail", 0
        c4_badge = "❌ 외국인 이탈중"
        c4_desc = f"외국인 5일 누적 {int(cum5):+,}억 / 20일 누적 {int(cum20):+,}억 — 추세 매도 지속 (속임수 반등 주의)"

    checklist.append({
        "id": "foreign",
        "title": "④ 수급 바닥 (외국인 5일 누적 순매수)",
        "target": "외국인 5거래일 누적 순매수(+) 전환",
        "current": f"5일 누적 {int(cum5):+,}억",
        "status": c4_status,
        "badge": c4_badge,
        "score": c4_score,
        "desc": c4_desc
    })

    # ⑤ 매크로·반도체 진정 (환율 5일선 안정 + VIX 꺾임 + 필라델피아 반도체 지지)
    usdkrw = ind.get('usdkrw', 1342.0)
    usdkrw_ma5 = ind.get('usdkrw_ma5', 1343.0)
    vix = ind.get('vkospi', 25.0)
    vix_peaked = ind.get('vkospi_peaked', False)
    sox_ratio = ind.get('sox_ratio', 0.0)

    fx_ok = (usdkrw <= usdkrw_ma5) and (usdkrw < 1360)
    vol_ok = (vix < 24.0) or (vix < 30.0 and vix_peaked)
    semi_ok = (sox_ratio >= -0.5)

    if fx_ok and vol_ok and semi_ok:
        c5_status, c5_score = "pass", 20
        c5_badge = "✅ 대외 안정"
        c5_desc = f"환율({usdkrw:,.1f}원 ≤ 5일선 {usdkrw_ma5:,.1f}원) 하향 안정 및 미 반도체({sox_ratio:+.2f}%)·변동성 진정"
    elif (fx_ok and vol_ok) or (fx_ok and semi_ok):
        c5_status, c5_score = "partial", 12
        c5_badge = "⏳ 진정 진행중"
        c5_desc = f"환율({usdkrw:,.1f}원) 진정세이나 VIX({vix:.1f})·미 반도체({sox_ratio:+.2f}%) 추가 안정 확인 필요"
    else:
        c5_status, c5_score = "fail", 4
        c5_badge = "❌ 대외 경계"
        c5_desc = f"환율 {usdkrw:,.1f}원 · VIX {vix:.1f} · 미 반도체 {sox_ratio:+.2f}% — 글로벌 변동성 경계 구간"

    checklist.append({
        "id": "macro",
        "title": "⑤ 환율·공포·미 반도체 진정",
        "target": "환율 5일선 하향 안정 & VIX 진정 & SOX 지지",
        "current": f"환율 {usdkrw:,.1f}원 / SOX {sox_ratio:+.2f}%",
        "status": c5_status,
        "badge": c5_badge,
        "score": c5_score,
        "desc": c5_desc
    })

    total_score = sum(item['score'] for item in checklist)
    pass_count = sum(1 for item in checklist if item['status'] == 'pass')
    partial_count = sum(1 for item in checklist if item['status'] == 'partial')

    # 과열장 예외 처리 (RSI >= 70 & 이격도 >= 105%)
    if curr_rsi >= 70.0 and disparity20 >= 105.0:
        verdict = "🔴 단기 과열 · 분할 차익실현 구간"
        verdict_color = "red"
        stock_weight = 30
        cash_weight = 70
        action_summary = "단기 과열권 진입으로 추격 매수를 금하고, 수익 실현을 통해 현금 비중을 확보하세요."
    elif total_score >= 76 and pass_count >= 4:
        verdict = "🟢 적극 매수 타점 (진바닥 시그널 점등)"
        verdict_color = "green"
        stock_weight = 75
        cash_weight = 25
        action_summary = "가격·기술·수급 핵심 바닥 조건 4개 이상이 충족되었습니다. 주도주(반도체·대형주) 및 지수 ETF 비중을 적극 확대하세요."
    elif total_score >= 60 and pass_count >= 3:
        verdict = "🔵 분할 매수 구간 (1~2차 저점 매집)"
        verdict_color = "blue"
        stock_weight = 50
        cash_weight = 50
        action_summary = "5대 조건 중 3개 이상이 충족되었습니다. 현금 절반을 남겨둔 채 2~3회 나누어 본격 분할 매수로 접근하세요."
    elif total_score >= 40:
        verdict = "🟡 관망 · 소액 정찰병 구간 (진바닥 탐색 중)"
        verdict_color = "orange"
        stock_weight = 25
        cash_weight = 75
        action_summary = "가격·밸류 매력은 높으나 외국인 누적 수급 전환과 확실한 반등 캔들이 아직 대기 중입니다. 현금 75%를 지키며 정찰병(20~25%)만 유지하세요."
    else:
        verdict = "🛑 매수 보류 · 현금 방어 구간 (떨어지는 칼날 주의)"
        verdict_color = "red"
        stock_weight = 10
        cash_weight = 90
        action_summary = "수급 이탈과 하락 추세가 강하게 진행 중입니다. 섣부른 물타기를 멈추고 외국인 5일 누적 순매수 전환까지 현금을 방어하세요."

    return {
        "totalScore": total_score,
        "passCount": pass_count,
        "partialCount": partial_count,
        "verdict": verdict,
        "verdictColor": verdict_color,
        "stockWeight": stock_weight,
        "cashWeight": cash_weight,
        "actionSummary": action_summary,
        "disparity20": disparity20,
        "rsi14": curr_rsi,
        "foreignCum5": cum5,
        "foreignCum20": cum20,
        "checklist": checklist
    }


def generate_daily_commentary(kospi, kospi_change, ma20, ind, valuation, buy_strat):
    """하루치 등락에 흔들리지 않는 중기 추세·누적수급·매수점수 통합 3줄 실전 논평"""
    rsi = buy_strat.get('rsi14', 50.0)
    disp = buy_strat.get('disparity20', 100.0)
    cum5 = int(buy_strat.get('foreignCum5', 0))
    f_today = int(ind.get('foreign_flow', 0))
    usdkrw = ind.get('usdkrw', 1342.0)
    sox_r = ind.get('sox_ratio', 0.0)
    ixic_r = ind.get('nasdaq_ratio', 0.0)
    pbr = valuation.get('pbr', 1.0)
    per = valuation.get('per', 8.0)
    score = buy_strat.get('totalScore', 50)
    p_cnt = buy_strat.get('passCount', 0)
    st_w = buy_strat.get('stockWeight', 20)
    ca_w = buy_strat.get('cashWeight', 80)

    # 1줄: 가격·추세·밸류에이션 입체 진단
    pos_str = f"20일선({ma20:,.0f}P) 아래 역배열(이격도 {disp:.1f}%)" if kospi < ma20 else f"20일선({ma20:,.0f}P) 상회 안착(이격도 {disp:.1f}%)"
    if kospi < ma20 and kospi_change >= 1.5:
        line1 = f"⛅ [추세/밸류 진단] 코스피가 {kospi:,.2f}P({kospi_change:+.2f}%)로 반등했으나 여전히 {pos_str} 구간이며, PBR {pbr:.2f}배·PER {per:.1f}배·RSI {rsi:.1f}에서 진바닥을 시험 중입니다."
    elif kospi < ma20:
        line1 = f"🌧️ [추세/밸류 진단] 코스피 {kospi:,.2f}P({kospi_change:+.2f}%)로 {pos_str} 조정 국면이나, 밸류에이션(PBR {pbr:.2f}배·PER {per:.1f}배)과 RSI({rsi:.1f})는 저평가 과매도 권역에 진입해 있습니다."
    else:
        line1 = f"☀️ [추세/밸류 진단] 코스피가 {kospi:,.2f}P({kospi_change:+.2f}%)로 {pos_str} 흐름을 회복하며(PBR {pbr:.2f}배·RSI {rsi:.1f}), 단기 추세 전환 탄력을 확보하고 있습니다."

    # 2줄: 5일 누적 외국인 수급 + 환율 + 미국 반도체(SOX)/나스닥 연동 진단
    cum5_dir = "순매수 유입" if cum5 > 0 else "순매도 이탈 지속"
    line2 = (
        f"🔍 [누적수급/글로벌] 외국인 당일 {f_today:+,}억(5일 누적 {cum5:+,}억 {cum5_dir}) · "
        f"환율 {usdkrw:,.1f}원 · 미 나스닥({ixic_r:+.2f}%) 및 필라델피아 반도체({sox_r:+.2f}%) 흐름이 국내 수급 방향성을 좌우하고 있습니다."
    )

    # 3줄: 매수 타점 점수 & 명확한 권장 비중 제시
    next_trigger = "외국인 5일 누적 순매수(+) 전환 및 20일선 탈환" if cum5 <= 0 else "환율 하향 안정 및 거래량 실린 지지선 안착"
    line3 = (
        f"🎯 [실전 매수 전략] 매수 타점 점수 {score}점(5대 핵심조건 중 {p_cnt}개 충족) ➔ "
        f"현재 권장 비중 【주식 {st_w}% : 현금 {ca_w}%】 유지 (본격 비중 확대 방아쇠: {next_trigger} 확인 시)."
    )

    return [line1, line2, line3]


def main():
    print("=" * 72)
    print("한국은행 ECOS + 코스피 HTS(RSI/거래량) + 밸류에이션 + 매수타점 스코어 수집 시작...")
    print("=" * 72)

    cached_data = {}
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                cached_data = json.load(f)
        except Exception as e:
            print("[경고] 기존 data.json 캐시 로드 실패:", e)

    # 1. 뉴스심리지수
    raw_data = fetch_news_sentiment(API_KEY)
    if raw_data:
        ma_data, trend, zone, latest_ma = calculate_ma_and_zones(raw_data, period=90)
        print(f"✓ 뉴스심리지수: {len(raw_data)}건 수집 완료 (최신: {raw_data[-1]['value']})")
    else:
        print("[안전망 가동] 한국은행 뉴스심리지수 수집 실패 - 기존 캐시 데이터를 재사용합니다.")
        raw_data = cached_data.get('timeSeries', [])
        ma_data = cached_data.get('maSeries', [])
        trend = cached_data.get('trend', 'down')
        zone = cached_data.get('zone', 'undervalued')
        latest_ma = cached_data.get('latestMA', 98.0)

    # 2. 코스피 일봉/월봉 + 이평선 + 거래량 이평선 + RSI(14)
    kospi_daily = fetch_kospi_candles('day', 750)
    if not kospi_daily:
        kospi_daily = cached_data.get('kospiDaily', {}).get('candles', [])
    ma20_daily = calc_series_ma(kospi_daily, 20, 'close')
    ma60_daily = calc_series_ma(kospi_daily, 60, 'close')
    ma120_daily = calc_series_ma(kospi_daily, 120, 'close')
    vol_ma20_daily = calc_series_ma(kospi_daily, 20, 'volume')
    rsi14_daily = calc_rsi_series(kospi_daily, 14)

    kospi_monthly = fetch_kospi_candles('month', 60)
    if not kospi_monthly:
        kospi_monthly = cached_data.get('kospiMonthly', {}).get('candles', [])
    ma5_monthly = calc_series_ma(kospi_monthly, 5, 'close')
    ma20_monthly = calc_series_ma(kospi_monthly, 20, 'close')
    ma60_monthly = calc_series_ma(kospi_monthly, 60, 'close')
    vol_ma5_monthly = calc_series_ma(kospi_monthly, 5, 'volume')
    rsi14_monthly = calc_rsi_series(kospi_monthly, 14)
    print(f"✓ 코스피 캔들+RSI+거래량: 일봉 {len(kospi_daily)}건 / 월봉 {len(kospi_monthly)}건 산출 완료")

    # 3. 외국인 보유현황 & 누적 순매수
    foreign_daily, foreign_monthly = fetch_foreign_holdings(API_KEY)
    if not foreign_daily:
        print("[안전망 가동] 한국은행 외국인 지분율 수집 실패 - 기존 캐시 데이터를 재사용합니다.")
        foreign_daily = cached_data.get('foreignDaily', [])
        foreign_monthly = cached_data.get('foreignMonthly', [])

    # 4. 보조 지표 & 글로벌 선행 지표 (나스닥, SOX, 미10년물, 환율5일선, VIX)
    indicators = fetch_market_indicators(API_KEY, cached_data.get('indicators'))

    # 4-1. 당일 외국인 지분율 및 순매수 실시간 보간 후 5일/20일 누적 재계산
    if foreign_daily and kospi_daily:
        latest_f_date = foreign_daily[-1]['date']
        latest_k_candle = kospi_daily[-1]
        today_date = latest_k_candle['date']

        # 이미 추정치로 들어간 동일 날짜가 있으면 최신 수급으로 갱신
        if latest_f_date == today_date and foreign_daily[-1].get('isEstimate'):
            foreign_daily.pop()
            latest_f_date = foreign_daily[-1]['date'] if foreign_daily else ''

        if latest_f_date and latest_f_date < today_date:
            prev_candle = next((c for c in reversed(kospi_daily) if c['date'] == latest_f_date), None)
            if not prev_candle and len(kospi_daily) >= 2:
                prev_candle = kospi_daily[-2]

            prev_close = prev_candle['close'] if prev_candle else latest_k_candle['close']
            today_close = latest_k_candle['close']
            price_ratio = today_close / prev_close if prev_close > 0 else 1.0

            last_item = foreign_daily[-1]
            last_foreign_val = last_item['foreignHoldingVal'] * 10000
            prev_mcap = last_foreign_val / (last_item['ratio'] / 100.0) if last_item['ratio'] > 0 else 1.0
            today_mcap = prev_mcap * price_ratio

            today_flow = float(indicators.get('foreign_flow', 0))
            curr_foreign_val = (last_foreign_val * price_ratio) + today_flow
            est_ratio = round((curr_foreign_val / today_mcap) * 100.0, 2)

            est_item = {
                "date": today_date,
                "rawDate": today_date.replace('-', ''),
                "netBuy": round(today_flow, 0),
                "ratio": est_ratio,
                "foreignHoldingVal": round(curr_foreign_val / 10000.0, 1),
                "isEstimate": True
            }
            foreign_daily.append(est_item)

            m_key = today_date[:7]
            if foreign_monthly and foreign_monthly[-1]['month'] == m_key and not foreign_monthly[-1].get('isEstimate'):
                foreign_monthly[-1]['netBuy'] = round(foreign_monthly[-1]['netBuy'] + today_flow, 0)
                foreign_monthly[-1]['ratio'] = est_ratio
                foreign_monthly[-1]['foreignHoldingVal'] = est_item['foreignHoldingVal']
                foreign_monthly[-1]['date'] = today_date
                foreign_monthly[-1]['isEstimate'] = True

        enrich_foreign_cumulative(foreign_daily, foreign_monthly)
        print(
            f"✓ 외국인 수급: 최신 지분율 {foreign_daily[-1]['ratio']}% | "
            f"당일 {int(foreign_daily[-1]['netBuy']):+,}억 | "
            f"5일누적 {int(foreign_daily[-1]['cum5']):+,}억 | "
            f"20일누적 {int(foreign_daily[-1]['cum20']):+,}억"
        )

    # 5. 실시간 코스피 밸류에이션 (공식 PER·배당수익률 + 실시간 PBR 밴드·일드갭)
    curr_kospi = kospi_daily[-1]['close'] if kospi_daily else indicators.get('kospi', 4711.21)
    valuation = fetch_kospi_valuation_and_breadth(
        API_KEY, curr_kospi, indicators.get('treasury_3y', 3.64), cached_data.get('valuation')
    )
    indicators['per'] = valuation['per']
    indicators['pbr'] = valuation['pbr']
    indicators['div_yield'] = valuation['divYield']
    indicators['yield_gap'] = valuation['yieldGap']
    indicators['program_flow'] = valuation['programNetBuy']
    print(
        f"✓ 실시간 밸류에이션: PBR {valuation['pbr']}배 | PER {valuation['per']}배 | "
        f"배당 {valuation['divYield']}% | 일드갭 +{valuation['yieldGap']}%p"
    )

    # 6. 실전 매수 타점 5대 체크리스트 & 종합 스코어 산출
    buy_strategy = evaluate_buy_strategy(
        raw_data, kospi_daily, ma20_daily, rsi14_daily, foreign_daily, indicators, valuation
    )
    print(
        f"✓ 매수 타점 스코어: {buy_strategy['totalScore']}점 "
        f"(5개 중 {buy_strategy['passCount']}개 충족) -> {buy_strategy['verdict']} "
        f"[주식 {buy_strategy['stockWeight']}% : 현금 {buy_strategy['cashWeight']}%]"
    )

    # 7. 맥락형 3줄 실전 논평 생성
    curr_ma20 = ma20_daily[-1] if (ma20_daily and ma20_daily[-1]) else curr_kospi
    commentary = generate_daily_commentary(
        curr_kospi, indicators.get('kospi_change', 0.0), curr_ma20, indicators, valuation, buy_strategy
    )
    print("✓ 맥락형 3줄 실전 논평 생성 완료:")
    for c in commentary:
        print(f"  {c}")

    # 8. 다우존스 · 코스피 고점 대비 등락률
    drawdown = {
        "kospi": calc_kospi_drawdown(kospi_daily, kospi_monthly, indicators),
        "dow": fetch_dow_drawdown((cached_data.get('drawdown') or {}).get('dow')),
    }
    for k, nm in (("kospi", "코스피"), ("dow", "다우존스")):
        d = drawdown[k]
        if d:
            print(f"✓ {nm}: {d.get('price')} | 52주 고점대비 {d.get('dd52')}% | 역대 고점대비 {d.get('ddAth')}%")

    # 9. 외부·내부 요인 뉴스 한줄 평가
    news_factors = fetch_news_factors(cached_data.get('newsFactors'))
    for k in ('external', 'internal'):
        if news_factors.get(k):
            print(f"✓ {news_factors[k]['line']}")

    payload = {
        "updatedAt": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "source": "한국은행 ECOS (523Y001, 802Y001, 901Y014) · 네이버 금융 · 글로벌 지수 실시간 연동",
        "trend": trend,
        "zone": zone,
        "latestValue": raw_data[-1]['value'] if raw_data else 95.0,
        "latestMA": latest_ma,
        "latestDate": raw_data[-1]['date'] if raw_data else "",
        "indicators": indicators,
        "valuation": valuation,
        "buyStrategy": buy_strategy,
        "dailyCommentary": commentary,
        "drawdown": drawdown,
        "newsFactors": news_factors,

        "timeSeries": raw_data,
        "maSeries": ma_data,

        "kospiDaily": {
            "candles": kospi_daily,
            "ma20": ma20_daily,
            "ma60": ma60_daily,
            "ma120": ma120_daily,
            "volMa20": vol_ma20_daily,
            "rsi14": rsi14_daily
        },

        "kospiMonthly": {
            "candles": kospi_monthly,
            "ma5": ma5_monthly,
            "ma20": ma20_monthly,
            "ma60": ma60_monthly,
            "volMa5": vol_ma5_monthly,
            "rsi14": rsi14_monthly
        },

        "foreignDaily": foreign_daily,
        "foreignMonthly": foreign_monthly,
        "latestForeign": foreign_daily[-1] if foreign_daily else None
    }

    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"[성공] {DATA_FILE} 저장 완료!")


if __name__ == '__main__':
    main()
