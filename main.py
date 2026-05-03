# -*- coding: utf-8 -*-
import os, json, sys, socket, time, urllib.request, urllib.error, numpy as np, pandas as pd
from pathlib import Path
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from mootdx.quotes import Quotes
import warnings
warnings.filterwarnings('ignore')

_mootdx_dir = os.path.join(str(Path.home()), '.mootdx')
_config_file = os.path.join(_mootdx_dir, 'config.json')
if not os.path.exists(_config_file):
    os.makedirs(_mootdx_dir, exist_ok=True)
    with open(_config_file, 'w', encoding='utf-8') as f:
        json.dump({"HQ": [{"name": "上海双线", "ip": "124.71.187.122", "port": 7709}], "EX": []}, f)

socket.setdefaulttimeout(10)

P1, P2, BIAS_THRESH = 8.0, 9.0, 6.0
EXCEL_LIST = 'stock_list.xlsx'
DAILY_CANDIDATES_FILE = 'left_daily_candidates.json'
LEFT_HISTORY_FILE = 'left_history.json'
HTML_OUTPUT = 'left_dashboard.html'
RATINGS_FILE = 'ratings.json'
PENDING_FILE = 'pending_analysis.json'

MX_APIKEY = os.environ.get("MX_APIKEY", "")
FEISHU_WEBHOOK_URL = os.environ.get("FEISHU_WEBHOOK_URL", "")

def convert_numpy(obj):
    if isinstance(obj, dict): return {k: convert_numpy(v) for k, v in obj.items()}
    elif isinstance(obj, list): return [convert_numpy(v) for v in obj]
    elif isinstance(obj, tuple): return tuple(convert_numpy(v) for v in obj)
    elif isinstance(obj, (np.integer, np.floating)): return obj.item()
    elif isinstance(obj, np.bool_): return bool(obj)
    elif isinstance(obj, np.ndarray): return obj.tolist()
    else: return obj

def load_json(path):
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def save_json(data, path):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(convert_numpy(data), f, ensure_ascii=False, indent=4)

def load_ratings():
    if os.path.exists(RATINGS_FILE):
        with open(RATINGS_FILE) as f:
            return json.load(f)
    return {'ratings': []}

def save_ratings(data):
    with open(RATINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def query_mx_data(code, name):
    if not MX_APIKEY:
        print("⚠️ MX_APIKEY 未设置")
        return None
    url = "https://mkapi2.dfcfs.com/finskillshub/api/claw/query"
    headers = {"Content-Type": "application/json", "apikey": MX_APIKEY}
    data = json.dumps({"toolQuery": f"{name} {code} 市盈率PE(TTM) 市净率PB 净资产收益率ROE 收盘价 涨跌幅"}).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"⚠️ 妙想查询失败({code}): {e}")
        return None

def extract_latest(data, indicator_name):
    try:
        for table in data["data"]["data"]["searchDataResultDTO"]["dataTableDTOList"]:
            for key, name in table.get("nameMap", {}).items():
                if indicator_name in name:
                    values = table.get("rawTable", {}).get(key, [])
                    if values:
                        try: return float(values[-1])
                        except: return values[-1]
    except: pass
    return None

def rule_based_rating(pe, pb, roe):
    if isinstance(pe, (int, float)) and pe < 0:
        return "D", 35, "公司亏损，市盈率为负"
    score = 50
    if isinstance(roe, (int, float)):
        if roe > 20: score += 20
        elif roe > 15: score += 15
        elif roe > 10: score += 10
        elif roe > 5: score += 5
        else: score -= 10
    if isinstance(pe, (int, float)):
        if 0 < pe <= 15: score += 15
        elif pe <= 25: score += 10
        elif pe <= 40: score += 5
        else: score -= 5
    if isinstance(pb, (int, float)):
        if pb <= 1.5: score += 10
        elif pb <= 3: score += 5
        elif pb > 6: score -= 10
    score = max(10, min(100, score))
    if score >= 80: rating = "A"
    elif score >= 65: rating = "B+"
    elif score >= 50: rating = "B"
    elif score >= 40: rating = "C"
    else: rating = "D"
    risks = []
    if isinstance(pe, (int, float)) and pe > 40: risks.append("市盈率偏高")
    if isinstance(pb, (int, float)) and pb > 5: risks.append("市净率较高")
    if isinstance(roe, (int, float)) and roe < 10: risks.append("净资产收益率偏低")
    return rating, score, "; ".join(risks) if risks else "财务指标正常范围内"

def send_feishu(ratings):
    if not FEISHU_WEBHOOK_URL: return
    today_str = datetime.now().strftime("%Y-%m-%d")
    lines = [f"📈 每日抄底 AI评级 ({today_str})"]
    for r in ratings:
        lines.append(f"{r.get('code','')} {r.get('name','')} | 综合评级{r.get('rating','?')} | PE {r.get('pe','N/A')} PB {r.get('pb','N/A')} ROE {r.get('roe','N/A')} | 风险: {r.get('risk','')}")
    req = urllib.request.Request(FEISHU_WEBHOOK_URL, data=json.dumps({"msg_type":"text","content":{"text":"\n".join(lines)}}).encode(), headers={"Content-Type":"application/json"})
    try: urllib.request.urlopen(req)
    except Exception as e: print(f"飞书推送失败: {e}")

def analyze_left_buy(stock_info, client):
    symbol = stock_info['code']
    try:
        time.sleep(0.05)
        df = client.bars(symbol=symbol, frequency=9, offset=100)
        if df is None or len(df) < 60: return None
        df.rename(columns={'datetime':'日期','open':'开盘','close':'收盘','high':'最高','low':'最低','vol':'成交量'}, inplace=True)
        for col in ['开盘','收盘','最高','最低','成交量']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df['VAR1'] = (df['收盘']+df['最高']+df['开盘']+df['最低'])/4
        df['MID'] = df['VAR1'].ewm(span=32, adjust=False).mean()
        df['LOWER'] = df['MID']*(1-P2/100.0)
        df['MA20'] = df['收盘'].rolling(20).mean()
        df['BIAS_VAL'] = (df['收盘']-df['MA20'])/df['MA20']*100
        curr, prev = df.iloc[-1], df.iloc[-2] if len(df)>1 else df.iloc[-1]
        limit_pct = 0.20 if symbol.startswith(('688','30')) else 0.10
        if curr['收盘'] >= (round(prev['收盘']*(1+limit_pct),2)-0.015): return None
        if not (curr['BIAS_VAL'] < -BIAS_THRESH and (curr['最低']<=curr['LOWER']) and (curr['收盘']>curr['开盘']) and ((curr['收盘']-curr['最低'])>(curr['最高']-curr['收盘']))): return None
        eps = 0.0
        try:
            fin_df = client.finance(symbol=symbol)
            if fin_df is not None and not fin_df.empty and 'jinglirun' in fin_df.columns and 'zongguben' in fin_df.columns:
                jinglirun, zongguben = float(fin_df['jinglirun'].iloc[0]), float(fin_df['zongguben'].iloc[0])
                if zongguben>0: eps = round((jinglirun/10)/zongguben,3)
        except: pass
        return {'code': symbol, 'name': stock_info['name'], 'price': float(curr['收盘']), 'low': float(curr['最低']), 'bias_val': float(curr['BIAS_VAL']), 'date': datetime.now().strftime('%Y-%m-%d'), 'eps': eps}
    except: return None

def analyze_left_sell(code, client):
    try:
        df = client.bars(symbol=code, frequency=9, offset=100)
        if df is None or len(df)<60: return False,None,None,None
        df.rename(columns={'datetime':'日期','open':'开盘','close':'收盘','high':'最高','low':'最低','vol':'成交量'}, inplace=True)
        for col in ['开盘','收盘','最高','最低','成交量']: df[col] = pd.to_numeric(df[col], errors='coerce')
        df['VAR1'] = (df['收盘']+df['最高']+df['开盘']+df['最低'])/4
        df['MID'] = df['VAR1'].ewm(span=32, adjust=False).mean()
        df['UPPER'] = df['MID']*(1+P1/100.0)
        df['DIF'] = df['收盘'].ewm(span=12, adjust=False).mean()-df['收盘'].ewm(span=26, adjust=False).mean()
        df['DEA'] = df['DIF'].ewm(span=9, adjust=False).mean()
        df['UP_TREND'] = (df['DIF']>0)&(df['DEA']>0)&(df['DIF']>df['DEA'])
        curr, prev = df.iloc[-1], df.iloc[-2] if len(df)>1 else df.iloc[-1]
        s_cond1 = curr['最高']>=curr['UPPER']
        body = abs(curr['收盘']-curr['开盘'])
        upper_shadow = curr['最高']-max(curr['收盘'], curr['开盘'])
        s_cond2 = (curr['收盘']<curr['开盘']) or (upper_shadow>body*1.5)
        vol_shrink = curr['成交量']<prev['成交量']
        sell_signal = s_cond1 and s_cond2 and vol_shrink and (not curr['UP_TREND'])
        return sell_signal, float(curr['收盘']), float(curr['最低']), float(curr['UPPER'])
    except: return False,None,None,None

def update_history_with_sell(history, client, today_date):
    today_dt = datetime.strptime(today_date, '%Y-%m-%d')
    updated = False
    for rec in history:
        if rec.get('sell_date'): continue
        sell_signal, price, low, upper = analyze_left_sell(rec['code'], client)
        if price is None: continue
        rec['latest_price'], rec['latest_update'] = price, today_date
        updated = True
        buy_dt = datetime.strptime(rec['buy_date'], '%Y-%m-%d')
        days_held = (today_dt - buy_dt).days
        buy_day_low = rec.get('buy_day_low', rec['buy_price'])
        if days_held <= 15 and price < buy_day_low:
            rec['sell_date'], rec['sell_reason'], rec['sell_price'] = today_date, f"破位止损 (跌破买入日最低价 {buy_day_low:.2f})", price
            continue
        if sell_signal:
            rec['sell_date'], rec['sell_reason'], rec['sell_price'] = today_date, f"S_落袋 (触碰上轨 {upper:.2f})", price
    return updated

def generate_dashboard(today_date, now_time):
    daily = load_json(DAILY_CANDIDATES_FILE) if os.path.exists(DAILY_CANDIDATES_FILE) and os.path.getsize(DAILY_CANDIDATES_FILE)>0 else {'left': []}
    history = load_json(LEFT_HISTORY_FILE)
    ratings_data = load_ratings()
    rating_map = {f"{r.get('code','')}_{r.get('date','')}": r for r in ratings_data.get('ratings', [])}
    history_sorted = sorted(history, key=lambda x: x.get('buy_date',''), reverse=True)

    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><title>左侧抄底看板</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"><style>body{{background:#f8f9fa;padding:20px;}}.positive{{color:#dc3545;}}.negative{{color:#198754;}}.rating-A{{background-color:#28a745;color:white;padding:2px 8px;border-radius:4px;font-weight:bold;}}.rating-B\\+{{background-color:#17a2b8;color:white;padding:2px 8px;border-radius:4px;font-weight:bold;}}.rating-B{{background-color:#0d6efd;color:white;padding:2px 8px;border-radius:4px;font-weight:bold;}}.rating-C{{background-color:#ffc107;color:black;padding:2px 8px;border-radius:4px;font-weight:bold;}}.rating-D{{background-color:#dc3545;color:white;padding:2px 8px;border-radius:4px;font-weight:bold;}}.rating-unknown{{background-color:#6c757d;color:white;padding:2px 8px;border-radius:4px;font-weight:bold;}}[title]{{cursor:help;position:relative;}}[title]:hover::after{{content:attr(title);position:absolute;left:50%;bottom:calc(100%+6px);transform:translateX(-50%);background:#333;color:#fff;padding:4px 8px;border-radius:4px;white-space:pre-wrap;max-width:300px;font-size:0.85rem;z-index:999;pointer-events:none;}}</style></head><body><h2>左侧抄底系统 <small>{now_time}</small></h2><div class="alert alert-info">参数: 下轨{P2}%, 上轨{P1}%, 乖离率&lt;-{BIAS_THRESH}%</div><div class="card mb-4"><div class="card-header bg-primary text-white">今日抄底候选</div><div class="card-body"><table class="table"><thead><tr><th>代码</th><th>名称</th><th>最新价</th><th>乖离率%</th><th>当日最低价</th><th>每股收益</th><th>AI评级</th><th>风险</th><th>摘要</th><th>风险提示</th></tr></thead><tbody>"""
    for s in daily.get('left', []):
        code = s.get('code',''); key = f"{code}_{today_date}"; r = rating_map.get(key, {})
        rating = r.get('rating','-'); score = r.get('score','-')
        if isinstance(score, int):
            if score <= 35: risk_display = f"💣 {score}"
            elif score <= 50: risk_display = f"⚠️ {score}"
            else: risk_display = f"🟢 {score}"
        else: risk_display = score
        summary = r.get('summary','-'); risk = r.get('risk','-')
        rating_class = f"rating-{rating}" if rating in ['A','B+','B','C','D'] else "rating-unknown"
        html += f"<tr><td>{code}</td><td>{s.get('name','')}</td><td>{s.get('price',0):.2f}</td><td>{s.get('bias_val',0):.2f}</td><td>{s.get('low',0):.2f}</td><td>{s.get('eps',0):.3f}</td><td><span class=\"{rating_class}\">{rating}</span></td><td>{risk_display}</td><td>{summary}</td><td>{risk}</td></tr>"
    if not daily.get('left'): html += "<tr><td colspan='10'>暂无抄底信号</td></tr>"
    html += "</tbody></table></div></div><div class=\"card\"><div class=\"card-header bg-secondary text-white\">历史追溯池</div><div class=\"card-body\"><table class=\"table\"><thead><tr><th>买入日期</th><th>代码</th><th>名称</th><th>买入价</th><th>生命线</th><th>最新价</th><th>涨跌%</th><th>每股收益</th><th>AI评级</th><th>风险</th><th>状态</th><th>卖出原因</th></tr></thead><tbody>"
    for rec in history_sorted:
        code = rec.get('code',''); buy_date = rec.get('buy_date',''); key = f"{code}_{buy_date}"; r = rating_map.get(key, {})
        rating = r.get('rating','-'); score = r.get('score','-')
        if isinstance(score, int):
            if score <= 35: risk_display = f"💣 {score}"
            elif score <= 50: risk_display = f"⚠️ {score}"
            else: risk_display = f"🟢 {score}"
        else: risk_display = score
        summary = r.get('summary',''); risk = r.get('risk','')
        tooltip = "暂无信息" if rating == '-' else (("\n".join(filter(None, [f"📊 {summary}" if summary else None, f"⚠️ {risk}" if risk else None]))) or "")
        rating_class = f"rating-{rating}" if rating in ['A','B+','B','C','D'] else "rating-unknown"
        buy_price = rec.get('buy_price',0); latest_price = rec.get('latest_price', buy_price)
        pct = (latest_price/buy_price-1)*100 if buy_price else 0
        color = 'positive' if pct>0 else 'negative' if pct<0 else ''
        status = f"已卖出({rec['sell_date']})" if rec.get('sell_date') else "持有中"
        reason = rec.get('sell_reason','-')
        html += f"<tr><td>{buy_date}</td><td>{code}</td><td>{rec.get('name','')}</td><td>{buy_price:.2f}</td><td>{rec.get('buy_day_low', buy_price):.2f}</td><td>{latest_price:.2f}</td><td class='{color}'>{pct:+.2f}%</td><td>{rec.get('eps',0):.3f}</td><td><span class=\"{rating_class}\" title=\"{tooltip}\">{rating}</span></td><td>{risk_display}</td><td>{status}</td><td>{reason}</td></tr>"
    html += "</tbody></table></div></div></body></html>"
    with open(HTML_OUTPUT, 'w', encoding='utf-8') as f: f.write(html)
    print("看板已生成")

# 主流程
if __name__ == '__main__':
    beijing_now = datetime.now(timezone.utc) + timedelta(hours=8)
    today = beijing_now.strftime('%Y-%m-%d')
    now_time = beijing_now.strftime('%Y-%m-%d %H:%M:%S')
    mode = 'auto' if len(sys.argv) == 1 else sys.argv[1]
    if mode == 'auto':
        mode = 'history' if beijing_now.hour >= 15 or (beijing_now.hour == 14 and beijing_now.minute >= 50) else 'candidates'
    print(f"运行模式: {mode}")

    tdx_servers = [('124.71.187.122', 7709), ('115.238.90.165', 7709)]
    client = None
    for ip, port in tdx_servers:
        try:
            temp_client = Quotes.factory(market='std', server=(ip, port), multithread=True, heartbeat=True)
            if temp_client.bars(symbol='600000', frequency=9, offset=1) is not None:
                client = temp_client; break
        except: pass
    if client is None: print("无法连接服务器"); sys.exit(1)

    meta_df = pd.read_excel(EXCEL_LIST, usecols=[0,1])
    meta_df.columns = ['code','name']
    meta_df['code'] = meta_df['code'].astype(str).str.replace(r'\.0$','', regex=True).str.zfill(6)
    stock_list = meta_df.to_dict('records')

    if mode == 'candidates':
        if os.path.exists(DAILY_CANDIDATES_FILE):
            with open(DAILY_CANDIDATES_FILE) as f: yest = json.load(f)
            if yest.get('date') and yest['date'] != today:
                history = load_json(LEFT_HISTORY_FILE)
                for c in yest.get('left', []):
                    if not any(r['code']==c['code'] and r['buy_date']==yest['date'] for r in history):
                        history.append({'code': c['code'], 'name': c['name'], 'buy_price': c['price'], 'buy_date': yest['date'], 'buy_day_low': c['low'], 'latest_price': c['price'], 'eps': c.get('eps',0.0)})
                if history:
                    save_json(history, LEFT_HISTORY_FILE)
                    ratings = load_ratings(); existing_keys = {f"{x['code']}_{x['date']}" for x in ratings['ratings']}; added = 0
                    for c in yest.get('left', []):
                        for r in ratings['ratings']:
                            if r['code']==c['code'] and r['date']==yest['date']:
                                new_key = f"{r['code']}_{yest['date']}"
                                if new_key not in existing_keys:
                                    record = dict(r); record['date'] = yest['date']; ratings['ratings'].append(record); existing_keys.add(new_key); added+=1
                                break
                    if added: save_ratings(ratings); print(f"✅ 同步 {added} 条历史评级")
        history = load_json(LEFT_HISTORY_FILE)
        if history:
            if update_history_with_sell(history, client, today): save_json(history, LEFT_HISTORY_FILE)
        total = len(stock_list); buy_candidates = []; completed = 0
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {executor.submit(analyze_left_buy, s, client): s['code'] for s in stock_list}
            for future in as_completed(futures):
                completed+=1
                if completed%100==0 or completed==total: print(f"进度: {completed}/{total}")
                res = future.result()
                if res: buy_candidates.append(res)
        buy_candidates.sort(key=lambda x: x['bias_val']); top5 = buy_candidates[:5]
        with open(DAILY_CANDIDATES_FILE, 'w') as f:
            json.dump({'date': today, 'left': top5}, f, ensure_ascii=False, indent=4)
        print(f"保存{len(top5)}只候选")
        # AI评级
        new_ratings = []
        for stock in top5:
            code, name = stock['code'], stock['name']
            raw = query_mx_data(code, name)
            if raw:
                pe, pb, roe = extract_latest(raw, "市盈率PE"), extract_latest(raw, "市净率PB"), extract_latest(raw, "净资产收益率ROE")
                rating, score, risk_str = rule_based_rating(pe, pb, roe)
                pe_str = f"{pe:.2f}" if isinstance(pe, float) else "N/A"
                pb_str = f"{pb:.2f}" if isinstance(pb, float) else "N/A"
                roe_str = f"{roe:.2f}%" if isinstance(roe, float) else "N/A"
                new_ratings.append({"code": code, "name": name, "date": today, "rating": rating, "score": score, "summary": f"PE {pe_str}，PB {pb_str}，ROE {roe_str}", "risk": risk_str, "pe": pe_str, "pb": pb_str, "roe": roe_str})
            else:
                new_ratings.append({"code": code, "name": name, "date": today, "rating": "C", "score": 50, "summary": "财务数据获取失败", "risk": "数据源暂时不可用", "pe": "N/A", "pb": "N/A", "roe": "N/A"})
        all_ratings = load_ratings(); existing_keys = {f"{x['code']}_{x['date']}" for x in all_ratings['ratings']}
        for r in new_ratings:
            if f"{r['code']}_{r['date']}" not in existing_keys: all_ratings['ratings'].append(r); existing_keys.add(f"{r['code']}_{r['date']}")
        save_ratings(all_ratings)
        send_feishu(new_ratings)
        with open(PENDING_FILE, 'w', encoding='utf-8') as f:
            json.dump({'date': today, 'stocks': [{'code': s['code'], 'name': s['name']} for s in top5]}, f, ensure_ascii=False, indent=2)
        print("待分析列表已生成")
    elif mode == 'history':
        history = load_json(LEFT_HISTORY_FILE)
        if history and update_history_with_sell(history, client, today): save_json(history, LEFT_HISTORY_FILE)

    generate_dashboard(today, now_time)
    os._exit(0)
