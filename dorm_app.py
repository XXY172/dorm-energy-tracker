import streamlit as st
import pandas as pd
import os
from datetime import datetime
from sqlalchemy import create_engine, text

# --- 数据库配置 ---
# 尝试获取云端的数据库链接（Zeabur 会提供 DATABASE_URL）
# 如果没有获取到（比如在你本地电脑上运行），就默认在本地生成一个 sqlite 数据库文件
DB_URL = os.getenv("DATABASE_URL", "sqlite:///local_dorm_data.db")

# SQLAlchemy 稍微有点挑剔，如果链接是 postgres:// 开头，需要替换为 postgresql://
if DB_URL.startswith("postgres://"):
    DB_URL = DB_URL.replace("postgres://", "postgresql://", 1)

# 创建数据库引擎
engine = create_engine(DB_URL)

# 初始化数据库表（如果不存在的话）
def init_db():
    with engine.connect() as conn:
        # SQLite 和 PostgreSQL 的语法兼容
        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS records (
                记录时间 TIMESTAMP,
                当前剩余电量 FLOAT,
                电量变化 FLOAT,
                类型 VARCHAR(50),
                备注 VARCHAR(255)
            )
        '''))
        conn.commit()

# 从数据库加载数据
def load_data():
    try:
        # 使用 pandas 直接从数据库表读取数据
        df = pd.read_sql_table('records', engine)
        if not df.empty:
            df['记录时间'] = pd.to_datetime(df['记录时间'])
        return df
    except Exception as e:
        # 如果表不存在，返回空 DataFrame
        return pd.DataFrame(columns=['记录时间', '当前剩余电量', '电量变化', '类型', '备注'])

# 保存单条记录到数据库
def save_record(now_str, new_val, change, type_str, remark):
    df = pd.DataFrame([{
        '记录时间': datetime.strptime(now_str, "%Y-%m-%d %H:%M:%S"), 
        '当前剩余电量': new_val, 
        '电量变化': change, 
        '类型': type_str, 
        '备注': remark
    }])
    # 追加数据到 records 表
    df.to_sql('records', engine, if_exists='append', index=False)

# 初始化表结构
init_db()

# --- 以下是完全不变的页面主体 ---
st.set_page_config(page_title="寝室电量管家", page_icon="⚡")
st.title("⚡ 寝室电量管家")

df = load_data()

current_elec = 0.0
daily_avg = None

if not df.empty:
    df = df.sort_values('记录时间').reset_index(drop=True)
    current_elec = float(df['当前剩余电量'].iloc[-1])
    
    if len(df) >= 2:
        last_record = df.iloc[-1]
        prev_record = df.iloc[-2]
        
        time_diff_days = (last_record['记录时间'] - prev_record['记录时间']).total_seconds() / (24 * 3600)
        
        if time_diff_days > 0.01 and last_record['类型'] != '充值':
            consumed = prev_record['当前剩余电量'] - last_record['当前剩余电量']
            if consumed > 0:
                daily_avg = consumed / time_diff_days

col_m1, col_m2 = st.columns(2)
col_m1.metric(label="🔋 当前剩余电量 (度)", value=f"{current_elec:.2f}")

if daily_avg is not None:
    col_m2.metric(label="📉 近期日均耗电量 (度/天)", value=f"{daily_avg:.2f}", help="基于最近两次打卡记录的差值和时间计算得出")
else:
    col_m2.metric(label="📉 近期日均耗电量 (度/天)", value="暂无足够数据", help="需要至少两次不同时间的打卡记录才能计算")

st.divider()

st.subheader("📝 记录电量")

col1, col2 = st.columns(2)

with col1:
    action_type = st.radio("你想做什么？", ["日常打卡 (更新剩余电量)", "充值电费 (增加电量)"])

with col2:
    if action_type == "日常打卡 (更新剩余电量)":
        new_val = st.number_input("输入电表显示的最新度数", min_value=0.0, value=current_elec, step=1.0)
        remark = st.text_input("备注", "日常记录")
        
        if st.button("💾 保存记录", type="primary"):
            change = new_val - current_elec
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            # 调用新的保存函数
            save_record(now_str, new_val, change, '日常消耗' if change <= 0 else '异常增加', remark)
            st.rerun()

    else:
        recharge_val = st.number_input("输入充值的度数", min_value=0.0, value=50.0, step=10.0)
        remark = st.text_input("备注 (例如：张三充值50元)", "充值")
        
        if st.button("💰 保存充值记录", type="primary"):
            new_val = current_elec + recharge_val
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            # 调用新的保存函数
            save_record(now_str, new_val, recharge_val, '充值', remark)
            st.rerun()

st.divider()

st.subheader("📊 历史趋势与明细")

if not df.empty:
    chart_data = df.set_index('记录时间')['当前剩余电量']
    st.line_chart(chart_data)
    
    display_df = df.copy().sort_values('记录时间', ascending=False)
    display_df['记录时间'] = display_df['记录时间'].dt.strftime('%Y-%m-%d %H:%M')
    st.dataframe(display_df, use_container_width=True)
else:
    st.info("暂无数据，请在上方添加你的第一条记录吧！")