# -*- coding: utf-8 -*-
import os
import json
import sys
import socket
import time
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from mootdx.quotes import Quotes
import warnings
warnings.filterwarnings('ignore')

# 伪装配置
_mootdx_dir = os.path.join(str(Path.home()), '.mootdx')
_config_file = os.path.join(_mootdx_dir, 'config.json')
if not os.path.exists(_config_file):
    os.makedirs(_mootdx_dir, exist_ok=True)
    with open(_config_file, 'w', encoding='utf-8') as f:
        fake_config = {"HQ": [{"name": "上海双线", "ip": "124.71.187.122", "port": 7709}], "EX": []}
        json.dump(fake_config, f)

socket.setdefaulttimeout(10)

# ========== 策略参数 ==========
P1 = 8.0
P2 = 9.0
BIAS_THRESH = 6.0

EXCEL_LIST = 'stock_list.xlsx'
DAILY_CANDIDATES_FILE = 'left_daily_candidates.json'
LEFT_HISTORY_FILE = 'left_history.json'
HTML_OUTPUT = 'left_dashboard.html'
RATINGS_FILE = 'ratings.json'

# ========== 工具函数 ==========
def convert_numpy(obj):
    if isinstance(obj, dict):
        return {k: convert_numpy(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_numpy(v) for v in obj]
    elif isinstance(obj, tuple):
        return tuple(convert_numpy(v) for v in obj)
    elif isinstance(obj, (np.integer, np.floating)):
        return obj.item()
    elif isinstance(obj, np.bool_):
        return bool(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    else:
        return obj

def load_history(file_path):
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def save_history(data, file_path):
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(convert_numpy(data), f, ensure_ascii=False, indent=4)

def load_ratings():
    if os.path.exists(RATINGS_FILE):
        with open(RATINGS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {'ratings': []}

def save_ratings(data):
    with open(RATINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ========== 选股与卖出逻辑（保持不变） ==========
def analyze_left_buy(stock_info, client):
    symbol = stock_info['code']
    try:
        time.sleep(0.05)
        df = client.bars(symbol=symbol, frequency=9, offset=100)
        if df is None or len(df) < 60:
            return None
        df.rename(columns={'datetime':'日期','open':'开盘','close':'收盘','high':'最高','low':'最低','vol':'成交量'}, inplace=True)
        for col in ['开盘', '收盘', '最高', '最低', '成交量']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df['VAR1'] = (df['收盘'] + df['最高'] + df['开盘'] + df['最低']) / 4
        df['MID'] = df['VAR1'].ewm(span=32, adjust=False).mean()
        df['LOWER'] = df['MID'] * (1 - P2 / 100.0)
        df['MA20'] = df['收盘'].rolling(20).mean()
        df['BIAS_VAL'] = (df['收盘'] - df['MA20']) / df['MA20'] * 100
        curr = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else curr
        limit_pct = 0.20 if symbol.startswith(('688', '30')) else 0.10
        is_limit_up = curr['收盘'] >= (round(prev['收盘'] * (1 + limit_pct), 2) - 0.015)
        if is_limit_up:
            return None
        bias_ok = curr['BIAS_VAL'] < -BIAS_THRESH
        cond1 = (curr['最低'] <= curr['LOWER']) and bias_ok
        cond2 = (curr['收盘'] > curr['开盘']) and ((curr['收盘'] - curr['最低']) > (curr['最高'] - curr['收盘']))
        buy_signal = bias_ok and cond1 and cond2
        if not buy_signal:
            return None

        eps = 0.0
        try:
            fin_df = client.finance(symbol=symbol)
            if fin_df is not None and not fin_df.empty:
                if 'jinglirun' in fin_df.columns and 'zongguben' in fin_df.columns:
                    jinglirun = float(fin_df['jinglirun'].iloc[0])
                    zongguben = float(fin_df['zongguben'].iloc[0])
                    if zongguben > 0:
                        eps = round((jinglirun / 10) / zongguben, 3)
        except Exception:
            pass

        return {
            'code': symbol,
            'name': stock_info['name'],
            'price': float(curr['收盘']),
            'low': float(curr['最低']),
            'bias_val': float(curr['BIAS_VAL']),
            'date': datetime.now().strftime('%Y-%m-%d'),
            'eps': eps
        }
    except Exception:
        return None

def analyze_left_sell(code, client):
    try:
        df = client.bars(symbol=code, frequency=9, offset=100)
        if df is None or len(df) < 60:
            return False, None, None, None
        df.rename(columns={'datetime':'日期','open':'开盘','close':'收盘','high':'最高','low':'最低','vol':'成交量'}, inplace=True)
        for col in ['开盘', '收盘', '最高', '最低', '成交量']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df['VAR1'] = (df['收盘'] + df['最高'] + df['开盘'] + df['最低']) / 4
        df['MID'] = df['VAR1'].ewm(span=32, adjust=False).mean()
        df['UPPER'] = df['MID'] * (1 + P1 / 100.0)
        df['DIF'] = df['收盘'].ewm(span=12, adjust=False).mean() - df['收盘'].ewm(span=26, adjust=False).mean()
        df['DEA'] = df['DIF'].ewm(span=9, adjust=False).mean()
        df['UP_TREND'] = (df['DIF'] > 0) & (df['DEA'] > 0) & (df['DIF'] > df['DEA'])
        curr = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else curr
        s_cond1 = curr['最高'] >= curr['UPPER']
        body = abs(curr['收盘'] - curr['开盘'])
        upper_shadow = curr['最高'] - max(curr['收盘'], curr['开盘'])
        s_cond2 = (curr['收盘'] < curr['开盘']) or (upper_shadow > body * 1.5)
        vol_shrink = curr['成交量'] < prev['成交量']
        sell_signal = s_cond1 and s_cond2 and vol_shrink and (not curr['UP_TREND'])
        return (sell_signal,
                float(curr['收盘']),
                float(curr['最低']),
                float(curr['UPPER']))
    except Exception:
        return False, None, None, None

def update_history_with_sell(history, client, today_date):
    today_dt = datetime.strptime(today_date, '%Y-%m-%d')
    updated = False
    for rec in history:
        if rec.get('sell_date'):
            continue
        code = rec['code']
        sell_signal, curr_price, curr_low, upper = analyze_left_sell(code, client)
        if curr_price is None:
            continue
        rec['latest_price'] = curr_price
        rec['latest_update'] = today_date
        updated = True
        buy_dt = datetime.strptime(rec['buy_date'], '%Y-%m-%d')
        days_held = (today_dt - buy_dt).days
        buy_day_low = rec.get('buy_day_low', rec['buy_price'])
        if days_held <= 15 and curr_price < buy_day_low:
            rec['sell_date'] = today_date
            rec['sell_reason'] = f"破位止损 (跌破买入日最低价 {buy_day_low:.2f})"
            rec['sell_price'] = curr_price
            print(f"🩸 [{code}] {rec['name']} 触发破位止损")
            continue
        if sell_signal:
            rec['sell_date'] = today_date
            rec['sell_reason'] = f"S_落袋 (触碰上轨 {upper:.2f})"
            rec['sell_price'] = curr_price
            print(f"💰 [{code}] {rec['name']} 触发S_落袋")
            continue
    return updated

# ========== 看板生成（固定格式，含 AI 评级列） ==========
def generate_dashboard(today_date, now_time):
    daily = {}
    if os.path.exists(DAILY_CANDIDATES_FILE):
        with open(DAILY_CANDIDATES_FILE, 'r', encoding='utf-8') as f:
            daily = json.load(f)
    history = load_history(LEFT_HISTORY_FILE)
    ratings_data = load_ratings()
    rating_map = {}
    for r in ratings_data.get('ratings', []):
        key = f"{r.get('code','')}_{r.get('date','')}"
        rating_map[key] = r
    history_sorted = sorted(history, key=lambda x: x.get('buy_date',''), reverse=True)

    # === HTML 头部，加入悬停样式 ===
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><title>左侧抄底看板</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
<style>
body{{background:#f8f9fa;padding:20px;}}
.positive{{color:#dc3545;}}
.negative{{color:#198754;}}
.rating-A{{background-color:#28a745;color:white;padding:2px 8px;border-radius:4px;font-weight:bold;}}
.rating-B{{background-color:#17a2b8;color:white;padding:2px 8px;border-radius:4px;font-weight:bold;}}
.rating-C{{background-color:#ffc107;color:black;padding:2px 8px;border-radius:4px;font-weight:bold;}}
.rating-D{{background-color:#dc3545;color:white;padding:2px 8px;border-radius:4px;font-weight:bold;}}
.rating-unknown{{background-color:#6c757d;color:white;padding:2px 8px;border-radius:4px;font-weight:bold;}}
[title] {{ cursor: help; position: relative; }}
[title]:hover::after {{
  content: attr(title);
  position: absolute;
  left: 50%;
  bottom: calc(100% + 6px);
  transform: translateX(-50%);
  background: #333;
  color: #fff;
  padding: 4px 8px;
  border-radius: 4px;
  white-space: pre-wrap;
  max-width: 300px;
  font-size: 0.85rem;
  z-index: 999;
  pointer-events: none;
}}
</style>
</head>
<body>
<h2>左侧抄底系统 <small>{now_time}</small></h2>
<div class="alert alert-info">参数: 下轨{P2}%, 上轨{P1}%, 乖离率&lt;-{BIAS_THRESH}%</div>

<!-- 今日抄底候选 -->
<div class="card mb-4"><div class="card-header bg-primary text-white">今日抄底候选</div>
<div class="card-body"><table class="table"><thead><tr>
<th>代码</th><th>名称</th><th>最新价</th><th>乖离率%</th><th>当日最低价</th><th>每股收益</th>
<th>AI评级</th><th>评分</th><th>摘要</th><th>风险提示</th>
</tr></thead><tbody>"""

    for s in daily.get('left', []):
        code = s.get('code', '')
        key = f"{code}_{today_date}"
        r = rating_map.get(key, {})
        rating = r.get('rating', '-')
        score = r.get('score', '-')
        summary = r.get('summary', '-')
        risk = r.get('risk', '-')
        rating_class = f"rating-{rating}" if rating in ['A','B+','B','C','D'] else "rating-unknown"
        html += f"""<tr>
<td>{code}</td><td>{s.get('name','')}</td><td>{s.get('price',0):.2f}</td>
<td>{s.get('bias_val',0):.2f}</td><td>{s.get('low',0):.2f}</td><td>{s.get('eps',0):.3f}</td>
<td><span class="{rating_class}">{rating}</span></td><td>{score}</td><td>{summary}</td><td>{risk}</td>
</tr>"""
    if not daily.get('left'):
        html += "<tr><td colspan='10'>暂无抄底信号</td></tr>"
    html += "</tbody></table></div></div>"

    # 历史追溯池
    html += """<div class="card"><div class="card-header bg-secondary text-white">历史追溯池</div>
<div class="card-body"><table class="table"><thead><tr>
<th>买入日期</th><th>代码</th><th>名称</th><th>买入价</th><th>生命线</th><th>最新价</th><th>涨跌%</th><th>每股收益</th><th>AI评级</th><th>评分</th><th>状态</th><th>卖出原因</th>
</tr></thead><tbody>"""

    for rec in history_sorted:
        code = rec.get('code', '')
        buy_date = rec.get('buy_date', '')
        key = f"{code}_{buy_date}"
        r = rating_map.get(key, {})
        rating = r.get('rating', '-')
        score = r.get('score', '-')
        summary = r.get('summary', '')
        risk = r.get('risk', '')
        if rating == '-' or not r:
            tooltip = "暂无信息"
        else:
            parts = []
            if summary: parts.append(f"📊 {summary}")
            if risk: parts.append(f"⚠️ {risk}")
            tooltip = "\n".join(parts) if parts else ""
        rating_class = f"rating-{rating}" if rating in ['A','B+','B','C','D'] else "rating-unknown"
        buy_price = rec.get('buy_price', 0)
        latest_price = rec.get('latest_price', buy_price)
        pct = (latest_price / buy_price - 1) * 100 if buy_price else 0
        color = 'positive' if pct > 0 else 'negative' if pct < 0 else ''
        if rec.get('sell_date'):
            status = f"已卖出({rec['sell_date']})"
            reason = rec.get('sell_reason', '-')
        else:
            status = "持有中"
            reason = "-"
        html += f"""<tr>
<td>{buy_date}</td><td>{code}</td><td>{rec.get('name','')}</td>
<td>{buy_price:.2f}</td><td>{rec.get('buy_day_low', buy_price):.2f}</td><td>{latest_price:.2f}</td>
<td class='{color}'>{pct:+.2f}%</td><td>{rec.get('eps',0):.3f}</td>
<td><span class="{rating_class}" title="{tooltip}">{rating}</span></td><td>{score}</td>
<td>{status}</td><td>{reason}</td>
</tr>"""
    html += "</tbody></table></div></div></body></html>"

    with open(HTML_OUTPUT, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"看板已生成: {HTML_OUTPUT}")

# ========== 主流程 ==========
if __name__ == '__main__':
    beijing_now = datetime.now(timezone.utc) + timedelta(hours=8)
    today = beijing_now.strftime('%Y-%m-%d')
    now_time = beijing_now.strftime('%Y-%m-%d %H:%M:%S')
    mode = 'auto' if len(sys.argv) == 1 else sys.argv[1]
    if mode == 'auto':
        mode = 'history' if beijing_now.hour >= 15 else 'candidates'
    print(f"运行模式: {mode}")

    # 连接服务器
    tdx_servers = [('124.71.187.122', 7709), ('115.238.90.165', 7709)]
    client = None
    for ip, port in tdx_servers:
        try:
            temp_client = Quotes.factory(market='std', server=(ip, port), multithread=True, heartbeat=True)
            if temp_client.bars(symbol='600000', frequency=9, offset=1) is not None:
                client = temp_client
                break
        except:
            pass
    if client is None:
        print("无法连接服务器")
        sys.exit(1)

    # 读取股票池
    meta_df = pd.read_excel(EXCEL_LIST, usecols=[0, 1])
    meta_df.columns = ['code', 'name']
    meta_df['code'] = meta_df['code'].astype(str).str.replace(r'\.0$', '', regex=True).str.zfill(6)
    stock_list = meta_df.to_dict('records')

    if mode == 'candidates':
        # 1. 结转昨日候选到历史池
        if os.path.exists(DAILY_CANDIDATES_FILE):
            with open(DAILY_CANDIDATES_FILE, 'r', encoding='utf-8') as f:
                yest = json.load(f)
            if yest.get('date') and yest['date'] != today:
                print(f"📦 发现昨日 ({yest['date']}) 抄底候选，正在移入历史回溯池...")
                history = load_history(LEFT_HISTORY_FILE)
                for c in yest.get('left', []):
                    if not any(r['code'] == c['code'] and r['buy_date'] == yest['date'] for r in history):
                        history.append({
                            'code': c['code'],
                            'name': c['name'],
                            'buy_price': c['price'],
                            'buy_date': yest['date'],
                            'buy_day_low': c['low'],
                            'latest_price': c['price'],
                            'eps': c.get('eps', 0.0)
                        })
                if history:
                    save_history(history, LEFT_HISTORY_FILE)
                    print(f"✅ 已结转 {len(yest.get('left', []))} 只股票到历史池")

                # 同步 AI 评级到历史日期
                ratings = load_ratings()
                existing_keys = {f"{r['code']}_{r['date']}" for r in ratings['ratings']}
                added = 0
                for c in yest.get('left', []):
                    for r in ratings['ratings']:
                        if r['code'] == c['code'] and r['date'] == yest['date']:
                            new_key = f"{r['code']}_{yest['date']}"
                            if new_key not in existing_keys:
                                record = dict(r)
                                record['date'] = yest['date']
                                ratings['ratings'].append(record)
                                existing_keys.add(new_key)
                                added += 1
                            break
                if added > 0:
                    save_ratings(ratings)
                    print(f"✅ 已同步 {added} 条 AI 评级到历史池日期")

        # 2. 更新历史池（价格+卖出判断）
        history = load_history(LEFT_HISTORY_FILE)
        if history:
            print("正在更新历史池价格并判断卖出...")
            changed = update_history_with_sell(history, client, today)
            if changed:
                save_history(history, LEFT_HISTORY_FILE)
                print("历史池状态已更新")

        # 3. 扫描选股
        total = len(stock_list)
        print(f"扫描抄底信号... 共 {total} 只股票")
        buy_candidates = []
        completed = 0
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {executor.submit(analyze_left_buy, s, client): s['code'] for s in stock_list}
            for future in as_completed(futures):
                completed += 1
                if completed % 100 == 0 or completed == total:
                    print(f"进度: {completed}/{total}")
                    sys.stdout.flush()
                res = future.result()
                if res:
                    buy_candidates.append(res)
        print(f"扫描完成！发现 {len(buy_candidates)} 只股票满足条件，正在排序...")
        buy_candidates.sort(key=lambda x: x['bias_val'])
        top5 = buy_candidates[:5]
        print(f"选出前 {len(top5)} 只作为今日抄底候选")
        with open(DAILY_CANDIDATES_FILE, 'w', encoding='utf-8') as f:
            json.dump({'date': today, 'left': top5}, f, ensure_ascii=False, indent=4)
        print(f"保存{len(top5)}只候选")

    elif mode == 'history':
        history = load_history(LEFT_HISTORY_FILE)
        if history:
            print("正在更新历史池价格并判断卖出...")
            changed = update_history_with_sell(history, client, today)
            if changed:
                save_history(history, LEFT_HISTORY_FILE)
                print("历史池状态已更新")
        else:
            print("历史池为空，无需更新")

    generate_dashboard(today, now_time)
    os._exit(0)
