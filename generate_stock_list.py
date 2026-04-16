import pandas as pd

# 在这里修改你的股票池（代码，名称）
stocks = [
    ('000001', '平安银行'),
    ('600036', '招商银行'),
    ('000858', '五粮液'),
    ('002415', '海康威视'),
]

df = pd.DataFrame(stocks, columns=['code', 'name'])
df.to_excel('stock_list.xlsx', index=False)
print(f"生成股票池文件，共 {len(df)} 只股票")
