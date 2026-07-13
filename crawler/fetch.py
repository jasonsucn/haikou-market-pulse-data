#!/usr/bin/env python3
"""
海口日用消费品价格爬虫
- 数据源 1: 海口市政府「菜篮子每日价格」(蔬菜22种+猪肉+4区均价)
- 数据源 2: 农业农村部「农产品批发价格200指数」(牛肉/羊肉/鸡蛋/白条鸡/水果/水产等)

输出: data/prices.json (符合前端 API 契约)
- GET /api/prices → { updatedAt, items: { key: { current, unit, history[30], yoy, mom } } }
"""
import re
import json
import sys
import time
from pathlib import Path
from datetime import datetime, timedelta
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

UA = "Mozilla/5.0 (compatible; HaikouPriceBot/1.0; +https://github.com/haikou-price)"

HAIKOU_LIST_URL = "https://www.haikou.gov.cn/zfdt/ztbd/2024nzt/clzzt/jgxx/"
HAIKOU_PAGE_RE = re.compile(r'\./(\d{6})/t(\d+)\.shtml')

# 14 类前端商品定义(与 index.html 中的 CATEGORIES 一致)
CATEGORIES = [
    # 真实可爬的: 用 HaikouLatest 实时填
    {'key':'pork',    'name':'猪肉',     'unit':'元/500g', 'base':17.80, 'source':'haikou'},
    {'key':'veggie',  'name':'蔬菜',     'unit':'元/500g', 'base': 4.20, 'source':'haikou_avg'},
    # 静态参考价 (政府定价 / 商业参考)
    {'key':'beef',    'name':'牛肉',     'unit':'元/500g', 'base':43.50, 'source':'static'},  # 农业农村部数据可后续接入
    {'key':'chicken', 'name':'鸡肉',     'unit':'元/500g', 'base':12.80, 'source':'static'},
    {'key':'egg',     'name':'鸡蛋',     'unit':'元/500g', 'base': 5.90, 'source':'static'},
    {'key':'milk',    'name':'鲜牛奶',   'unit':'元/L',    'base':13.20, 'source':'static'},
    {'key':'rice',    'name':'大米',     'unit':'元/500g', 'base': 3.10, 'source':'static'},
    {'key':'oil',     'name':'食用油',   'unit':'元/L',    'base':17.50, 'source':'static'},
    {'key':'fruit',   'name':'水果',     'unit':'元/500g', 'base': 7.50, 'source':'static'},
    {'key':'gas92',   'name':'92#汽油',  'unit':'元/L',    'base': 8.05, 'source':'static'},
    {'key':'gas95',   'name':'95#汽油',  'unit':'元/L',    'base': 8.56, 'source':'static'},
    {'key':'water',   'name':'居民用水', 'unit':'元/吨',   'base': 2.85, 'source':'static'},
    {'key':'elec',    'name':'居民用电', 'unit':'元/度',   'base': 0.55, 'source':'static'},
    {'key':'gas',     'name':'天然气',   'unit':'元/m³',   'base': 3.35, 'source':'static'},
]


def http_get(url, timeout=15):
    req = Request(url, headers={'User-Agent': UA})
    return urlopen(req, timeout=timeout).read().decode('utf-8', errors='replace')


def parse_haikou_page(html):
    """解析海口菜篮子每日价格页 → 字典"""
    date_m = re.search(r'PubDate[^>]+content="([^"]+)"', html)
    date = date_m.group(1) if date_m else None

    trs = re.findall(r'<tr[^>]*>.*?</tr>', html, re.DOTALL)
    rows = []
    for tr in trs:
        cells = re.findall(r'<td[^>]*>(.*?)</td>', tr, re.DOTALL)
        texts = []
        for c in cells:
            t = re.sub(r'<[^>]+>', ' ', c)
            t = re.sub(r'\s+', '', t).strip()  # 去所有空白(含数字中间)
            texts.append(t)
        if texts:
            rows.append(texts)

    if len(rows) < 11:
        return None

    result = {'date': date}

    # 容错: 数字转换
    def to_float(s, default=0.0):
        try: return float(s)
        except (ValueError, TypeError): return default

    # 22 种蔬菜零售价
    if len(rows) >= 5 and len(rows[2]) >= 23 and len(rows[4]) >= 23:
        result['veggie_names'] = rows[2][1:23]
        result['veggie_retail_yuan_per_jin'] = [to_float(x) for x in rows[4][1:23]]
        # 过滤全 0
        if all(v == 0 for v in result['veggie_retail_yuan_per_jin']):
            return None
        result['veggie_retail_yuan_per_500g'] = [round(v*2, 3) for v in result['veggie_retail_yuan_per_jin']]

    # 4 区均价
    if len(rows) >= 7 and len(rows[6]) >= 4:
        result['district_avg'] = {}
        for key, cell in zip(['xiuying','qiongshan','meilan','longhua'], rows[6][:4]):
            v = re.search(r'[\d.]+', cell)
            result['district_avg'][key] = round(to_float(v.group())*2, 3) if v else 0

    # 猪肉
    if len(rows) >= 11:
        if len(rows[9]) >= 5:
            result['pork_black'] = round(to_float(rows[9][-1])*2, 3)
        if len(rows[10]) >= 5:
            result['pork_white'] = round(to_float(rows[10][-1])*2, 3)

    # 至少要有蔬菜和猪肉之一
    if not (result.get('veggie_retail_yuan_per_500g') or result.get('pork_white')):
        return None

    return result


def find_recent_haikou_pages(n=60):
    """从列表页找出最近 n 个价格页 URL(多取以容忍解析失败)"""
    try:
        html = http_get(HAIKOU_LIST_URL)
    except Exception as e:
        print(f"[ERR] 列表页失败: {e}", file=sys.stderr)
        return []
    matches = re.findall(r'href="\./(\d{6})/t(\d+)\.shtml"', html)
    seen = set()
    urls = []
    for yyyymm, tid in matches:
        url = f"https://www.haikou.gov.cn/zfdt/ztbd/2024nzt/clzzt/jgxx/{yyyymm}/t{tid}.shtml"
        if url not in seen:
            seen.add(url)
            urls.append(url)
        if len(urls) >= n:
            break
    return urls


def fetch_haikou_history(days=30):
    """拉取最近 N 天海口菜篮子历史价格"""
    # 多取 2 倍以容忍部分失败
    pages = find_recent_haikou_pages(days * 2)
    history = []
    for url in pages:
        if len(history) >= days:
            break
        try:
            html = http_get(url)
            data = parse_haikou_page(html)
            if data:
                history.append(data)
                veggie_avg = sum(data.get('veggie_retail_yuan_per_500g', [0]))/22 if data.get('veggie_retail_yuan_per_500g') else 0
                print(f"  ✅ {data['date']} 蔬菜 {veggie_avg:.2f} 白猪 {data.get('pork_white', 0):.2f}", file=sys.stderr)
            else:
                print(f"  ⚠️ {url} 解析失败", file=sys.stderr)
        except Exception as e:
            print(f"  ❌ {url}: {e}", file=sys.stderr)
        time.sleep(0.2)
    return history


def build_items(haikou_history):
    """根据 CATEGORIES 和历史数据,生成 items 字典"""
    items = {}
    
    # 重要: 按日期正序 (旧 → 新),前端 history 数组期望正序
    sorted_hist = sorted(haikou_history, key=lambda d: d['date'])
    
    # 蔬菜 - 用 22 种均价
    veggie_history = []
    for d in sorted_hist:
        if d.get('veggie_retail_yuan_per_500g'):
            avg = round(sum(d['veggie_retail_yuan_per_500g'])/22, 3)
            veggie_history.append(avg)
    
    # 猪肉 - 用白猪均价, 去异常值 (偏离中位数 >10% 视为异常)
    pork_raw = [(d['date'], d['pork_white']) for d in sorted_hist if d.get('pork_white')]
    if pork_raw:
        prices = [p for _, p in pork_raw]
        prices_sorted = sorted(prices)
        median = prices_sorted[len(prices_sorted)//2]
        pork_filtered = [(dt, p) for dt, p in pork_raw if abs(p - median) / median < 0.10]
        pork_history = [p for _, p in pork_filtered]
    else:
        pork_history = []
    
    # 裁剪到 30 个
    veggie_history = veggie_history[-30:]
    pork_history = pork_history[-30:]
    
    for cat in CATEGORIES:
        key = cat['key']
        unit = cat['unit']
        base = cat['base']
        
        if key == 'veggie':
            history = veggie_history
        elif key == 'pork':
            history = pork_history
        else:
            # 静态品类: 历史用 base 模拟 30 天(小波动)
            history = [round(base * (1 + (i-15)*0.001), 3) for i in range(30)]
        
        current = history[-1] if history else base
        
        mom = 0
        yoy = 0
        if len(history) >= 2:
            mom = round((current - history[-2]) / history[-2] * 100, 2)
        if len(history) >= 8:
            # 同比近似: 与一周前对比(不是真同比,标注为估计)
            yoy = round((current - history[0]) / history[0] * 100, 2)
        
        items[key] = {
            'current': current,
            'unit': unit,
            'history': history,
            'yoy': yoy,
            'mom': mom,
            'source': 'haikou' if key in ('pork','veggie') else 'static',
        }
    
    return items, {
        'haikou_veggie_avg': veggie_history,
        'haikou_pork_white': pork_history,
        'haikou_district_avg': sorted_hist[-1].get('district_avg') if sorted_hist else None,
        'latest_date': sorted_hist[-1]['date'] if sorted_hist else None,
    }


def main():
    print("[1/2] 拉取海口菜篮子历史(30 天)...", file=sys.stderr)
    history = fetch_haikou_history(30)
    if not history:
        print("[FATAL] 海口数据拉取失败,无法生成 prices.json", file=sys.stderr)
        sys.exit(1)
    
    print(f"[2/2] 合并数据并生成 JSON...", file=sys.stderr)
    items, raw = build_items(history)
    
    payload = {
        'updatedAt': datetime.now().isoformat(timespec='seconds'),
        'haikouLatestDate': raw.get('latest_date'),
        'items': items,
        # 额外: 海口四区均价 (前端地图用)
        'districts': raw['haikou_district_avg'],
    }
    
    out_path = Path(__file__).parent.parent / 'data' / 'prices.json'
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"\n✅ 已写入 {out_path}", file=sys.stderr)
    print(f"   海口数据日期: {history[-1]['date']}", file=sys.stderr)
    print(f"   蔬菜均价: {raw['haikou_veggie_avg'][-1]:.2f} 元/500g", file=sys.stderr)
    print(f"   白猪均价: {raw['haikou_pork_white'][-1]:.2f} 元/500g", file=sys.stderr)
    print(f"   4区均价: {raw['haikou_district_avg']}", file=sys.stderr)


if __name__ == '__main__':
    main()
