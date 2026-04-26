# build_dashboard_with_ratings.py
import json
import os
from datetime import datetime, timezone, timedelta

DAILY_CANDIDATES_FILE = 'left_daily_candidates.json'
LEFT_HISTORY_FILE = 'left_history.json'
RATINGS_FILE = 'ratings.json'
HTML_OUTPUT = 'left_dashboard.html'

def load_json(path):
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None

def generate_dashboard_with_ratings():
    daily = load_json(DAILY_CANDIDATES_FILE) or {'left': []}
    history = load_json(LEFT_HISTORY_FILE) or []
    ratings_data = load_json(RATINGS_FILE) or {'ratings': []}
    
    # 构建 code+date → rating 的快速查找表
    rating_map = {}
    for r in ratings_data.get('ratings', []):
        key = f"{r['code']}_{r['date']}"
        rating_map[key] = r
    
    beijing_now = datetime.now(timezone.utc) + timedelta(hours=8)
    now_time = beijing_now.strftime('%Y-%m-%d %H:%M:%S')
    
    history_sorted = sorted(history, key=lambda x: x['buy_date'], reverse=True)
    
    # ... 生成 HTML，在表格中新增 AI评级、评分、摘要、风险提示 四列
    # 从 rating_map 中根据 code + date 匹配评级数据
    # 如果没有匹配到（流程B还没运行），对应列显示 "-"
    
    with open(HTML_OUTPUT, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"含评级的看板已生成: {HTML_OUTPUT}")

if __name__ == '__main__':
    generate_dashboard_with_ratings()
