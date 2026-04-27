# -*- coding: utf-8 -*-
import json
import os
from datetime import datetime, timezone, timedelta

DAILY_CANDIDATES_FILE = 'left_daily_candidates.json'
LEFT_HISTORY_FILE = 'left_history.json'
RATINGS_FILE = 'ratings.json'
HTML_OUTPUT = 'left_dashboard.html'

P1 = 8.0
P2 = 9.0
BIAS_THRESH = 6.0

def load_json(path):
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None

def generate_dashboard_with_ratings():
    daily = load_json(DAILY_CANDIDATES_FILE) or {'left': []}
    history = load_json(LEFT_HISTORY_FILE) or []
    ratings_data = load_json(RATINGS_FILE) or {'ratings': []}

    rating_map = {}
    for r in ratings_data.get('ratings', []):
        key = f"{r.get('code', '')}_{r.get('date', '')}"
        rating_map[key] = r

    beijing_now = datetime.now(timezone.utc) + timedelta(hours=8)
    now_time = beijing_now.strftime('%Y-%m-%d %H:%M:%S')
    today_date = beijing_now.strftime('%Y-%m-%d')
    history_sorted = sorted(history, key=lambda x: x.get('buy_date', ''), reverse=True)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><title>左侧抄底看板</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
<style>
body{{background:#f8f9fa;padding:20px;}}
.positive{{color:#dc3545;}}
.negative{{color:#198754;}}
.rating-A{{background-color:#28a745;color:white;padding:2px 8px;border-radius:4px;font-weight:bold;}}
.rating-B\+{{background-color:#17a2b8;color:white;padding:2px 8px;border-radius:4px;font-weight:bold;}}
.rating-B{{background-color:#0d6efd;color:white;padding:2px 8px;border-radius:4px;font-weight:bold;}}
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
    print(f"含评级的看板已生成: {HTML_OUTPUT}")

if __name__ == '__main__':
    generate_dashboard_with_ratings()
