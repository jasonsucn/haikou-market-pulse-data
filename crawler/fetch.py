#!/usr/bin/env python3
"""
海口日用消费品价格爬虫 (GitHub Actions 优化版)
- 数据源 1: 海口市政府「菜篮子每日价格」(蔬菜22种+猪肉+4区均价)
- 数据源 2: (可后续接入) 农业农村部

特性:
- 网络重试 (3 次, 退避)
- 超时保护
- 失败时降级到上次 commit 的 prices.json
- 详细日志输出,GitHub Actions 实时显示
"""
import sys
import os
import re
import json
import time
import traceback
from pathlib import Path
from datetime import datetime
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

# 强制 stdout 立即 flush (GitHub Actions 必须)
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

HAIKOU_LIST_URL = "https://www.haikou.gov.cn/zfdt/ztbd/2024nzt/clzzt/jgxx/"

CATEGORIES = [
    {'key':'pork',    'name':'猪肉',     'unit':'元/500g', 'base':17.80, 'source':'haikou'},
    {'key':'veggie',  'name':'蔬菜',     'unit':'元/500g', 'base': 4.20, 'source':'haikou_avg'},
    {'key':'beef',    'name':'牛肉',     'unit':'元/500g', 'base':43.50, 'source':'static'},
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

DATA_PATH = Path(__file__).parent.parent / 'data' / 'prices.json'


def log(msg, level='info'):
    """统一日志输出"""
    icons = {'info':'  ', 'ok':'✅', 'warn':'⚠️ ', 'err':'❌', 'fatal':'💀'}
    print(f"{icons.get(level, '  ')} {msg}", flush=True)


def http_get(url, timeout=15, retries=3):
    """带重试的 HTTP GET"""
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            req = Request(url, headers={
                'User-Agent': UA,
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            })
            return urlopen(req, timeout=timeout).read().decode('utf-8', errors='replace')
        except (URLError, HTTPError, TimeoutError, OSError) as e:
            last_err = e
            log(f"第 {attempt}/{retries} 次失败: {type(e).__name__}: {e}", 'warn')
            if attempt < retries:
                time.sleep(2 * attempt)  # 2s, 4s, 6s
    raise last_err


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
            t = re.sub(r'\s+', '', t).strip()
            texts.append(t)
        if texts:
            rows.append(texts)

    if len(rows) < 11:
        return None

    def to_float(s, default=0.0):
        try: return float(s)
        except (ValueError, TypeError): return default

    result = {'date': date}

    if len(rows) >= 5 and len(rows[2]) >= 23 and len(rows[4]) >= 23:
        result['veggie_names'] = rows[2][1:23]
        result['veggie_retail_yuan_per_jin'] = [to_float(x) for x in rows[4][1:23]]
        if all(v == 0 for v in result['veggie_retail_yuan_per_jin']):
            return None
        result['veggie_retail_yuan_per_500g'] = [round(v*2, 3) for v in result['veggie_retail_yuan_per_jin']]

    if len(rows) >= 7 and len(rows[6]) >= 4:
        result['district_avg'] = {}
        for key, cell in zip(['xiuying','qiongshan','meilan','longhua'], rows[6][:4]):
            v = re.search(r'[\d.]+', cell)
            result['district_avg'][key] = round(to_float(v.group())*2, 3) if v else 0

    if len(rows) >= 11:
        if len(rows[9]) >= 5:
            result['pork_black'] = round(to_float(rows[9][-1])*2, 3)
        if len(rows[10]) >= 5:
            result['pork_white'] = round(to_float(rows[10][-1])*2, 3)

    if not (result.get('veggie_retail_yuan_per_500g') or result.get('pork_white')):
        return None

    return result


def find_recent_haikou_pages(n=60):
    """从列表页找出最近 n 个价格页 URL"""
    html = http_get(HAIKOU_LIST_URL, timeout=20)
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
    log(f"开始拉取海口菜篮子历史 ({days} 天)...")
    pages = find_recent_haikou_pages(days * 2)
    log(f"列表页找到 {len(pages)} 个候选 URL")

    history = []
    failed = 0
    for i, url in enumerate(pages, 1):
        if len(history) >= days:
            break
        try:
            html = http_get(url, timeout=10)
            data = parse_haikou_page(html)
            if data:
                history.append(data)
                veggie_avg = sum(data.get('veggie_retail_yuan_per_500g', [0]))/22 if data.get('veggie_retail_yuan_per_500g') else 0
                log(f"  [{i:2d}/{len(pages)}] ✅ {data['date']} 蔬菜 {veggie_avg:.2f} 白猪 {data.get('pork_white', 0):.2f}")
            else:
                log(f"  [{i:2d}/{len(pages)}] ⚠️  解析失败 (页面结构异常)")
                failed += 1
        except Exception as e:
            log(f"  [{i:2d}/{len(pages)}] ❌ {type(e).__name__}: {e}")
            failed += 1
        time.sleep(0.2)

    log(f"完成: 成功 {len(history)} 天, 失败 {failed} 天")
    return history


def build_items(haikou_history):
    """根据 CATEGORIES 和历史数据,生成 items 字典"""
    items = {}
    sorted_hist = sorted(haikou_history, key=lambda d: d['date'])

    veggie_history = []
    for d in sorted_hist:
        if d.get('veggie_retail_yuan_per_500g'):
            avg = round(sum(d['veggie_retail_yuan_per_500g'])/22, 3)
            veggie_history.append(avg)

    pork_raw = [(d['date'], d['pork_white']) for d in sorted_hist if d.get('pork_white')]
    if pork_raw:
        prices = [p for _, p in pork_raw]
        prices_sorted = sorted(prices)
        median = prices_sorted[len(prices_sorted)//2]
        pork_filtered = [(dt, p) for dt, p in pork_raw if abs(p - median) / median < 0.10]
        pork_history = [p for _, p in pork_filtered]
    else:
        pork_history = []

    veggie_history = veggie_history[-30:]
    pork_history = pork_history[-30:]

    for cat in CATEGORIES:
        key = cat['key']
        base = cat['base']

        if key == 'veggie':
            history = veggie_history
        elif key == 'pork':
            history = pork_history
        else:
            history = [round(base * (1 + (i-15)*0.001), 3) for i in range(30)]

        current = history[-1] if history else base

        mom = 0
        yoy = 0
        if len(history) >= 2:
            mom = round((current - history[-2]) / history[-2] * 100, 2)
        if len(history) >= 8:
            yoy = round((current - history[0]) / history[0] * 100, 2)

        items[key] = {
            'current': current,
            'unit': cat['unit'],
            'history': history,
            'yoy': yoy,
            'mom': mom,
            'source': 'haikou' if key in ('pork','veggie') else 'static',
        }

    return items, {
        'haikou_district_avg': sorted_hist[-1].get('district_avg') if sorted_hist else None,
        'latest_date': sorted_hist[-1]['date'] if sorted_hist else None,
    }


def write_output(payload):
    """写入 prices.json"""
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    log(f"写入 {DATA_PATH} ({len(json.dumps(payload, ensure_ascii=False))/1024:.1f} KB)", 'ok')


def fallback_to_existing(reason):
    """用上次 commit 的 prices.json 兜底"""
    log(f"抓取失败: {reason}", 'err')
    log("尝试使用上次 commit 的 prices.json 兜底...", 'warn')
    if DATA_PATH.exists():
        try:
            prev = json.loads(DATA_PATH.read_text(encoding='utf-8'))
            log(f"找到上次的 prices.json, 写入 (共 {len(prev.get('items', {}))} 类商品)", 'ok')
            return prev
        except Exception as e:
            log(f"读取上次 prices.json 也失败: {e}", 'err')
    else:
        log("没有上次的 prices.json 可用, 无法兜底", 'err')
    return None


def main():
    log("=" * 60)
    log("海口物价爬虫启动")
    log(f"运行环境: {os.environ.get('GITHUB_ACTIONS', 'local')}")
    log(f"Python: {sys.version.split()[0]}")
    log("=" * 60)

    try:
        history = fetch_haikou_history(30)
    except Exception as e:
        log(f"网络层彻底失败: {type(e).__name__}: {e}", 'fatal')
        log(traceback.format_exc(), 'err')
        # 尝试兜底
        prev = fallback_to_existing(str(e))
        if prev:
            return 0  # 兜底成功, exit 0 让 workflow 继续
        return 1  # 实在没辙了

    if not history:
        log("未拉到任何有效数据", 'fatal')
        prev = fallback_to_existing("历史数据为空")
        if prev:
            return 0
        return 1

    # 构建并写入
    items, raw = build_items(history)
    payload = {
        'updatedAt': datetime.now().isoformat(timespec='seconds'),
        'haikouLatestDate': raw.get('latest_date'),
        'items': items,
        'districts': raw['haikou_district_avg'],
    }
    write_output(payload)
    log(f"海口数据日期: {raw.get('latest_date')}", 'ok')
    log(f"蔬菜均价: {items['veggie']['current']:.2f} 元/500g", 'ok')
    log(f"白猪均价: {items['pork']['current']:.2f} 元/500g", 'ok')
    if raw['haikou_district_avg']:
        log(f"4区均价: {raw['haikou_district_avg']}", 'ok')
    log("=" * 60)
    log("完成", 'ok')
    return 0


if __name__ == '__main__':
    sys.exit(main())
