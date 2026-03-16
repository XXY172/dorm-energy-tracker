import streamlit as st
import pandas as pd
import os
from datetime import datetime, timezone, timedelta
from sqlalchemy import create_engine, text

# --- 核心更新 1：强制设定北京时间 ---
BJ_TZ = timezone(timedelta(hours=8))

# --- 数据库配置 ---
DB_URL = os.getenv("DATABASE_URL", "sqlite:///local_dorm_data.db")

# 严谨地替换数据库驱动前缀，明确告诉 SQLAlchemy 使用 pg8000
if DB_URL.startswith("postgresql://"):
    DB_URL = DB_URL.replace("postgresql://", "postgresql+pg8000://", 1)
elif DB_URL.startswith("postgres://"):
    DB_URL = DB_URL.replace("postgres://", "postgresql+pg8000://", 1)

engine = create_engine(DB_URL)

def init_db():
    with engine.connect() as conn:
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

def load_data():
    try:
        df = pd.read_sql_table('records', engine)
        if not df.empty:
            df['记录时间'] = pd.to_datetime(df['记录时间'])
        return df
    except Exception as e:
        return pd.DataFrame(columns=['记录时间', '当前剩余电量', '电量变化', '类型', '备注'])

def save_record(now_str, new_val, change, type_str, remark):
    df = pd.DataFrame([{
        '记录时间': datetime.strptime(now_str, "%Y-%m-%d %H:%M:%S"), 
        '当前剩余电量': new_val, 
        '电量变化': change, 
        '类型': type_str, 
        '备注': remark
    }])
    df.to_sql('records', engine, if_exists='append', index=False)

# --- 核心更新 2：新增修改与删除的数据库交互函数 ---
def delete_record_db(record_time):
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM records WHERE 记录时间 = :time"), {"time": record_time})

def update_record_db(old_time, new_val, change, type_str, remark):
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE records 
            SET 当前剩余电量 = :new_val, 电量变化 = :change, 类型 = :type_str, 备注 = :remark 
            WHERE 记录时间 = :time
        """), {"new_val": new_val, "change": change, "type_str": type_str, "remark": remark, "time": old_time})

init_db()

# --- 页面主体 ---
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
    col_m2.metric(label="📉 近期日均耗电量 (度/天)", value="暂无足够数据")

st.divider()

# --- 交互输入区 ---
st.subheader("📝 记录电量")

col1, col2 = st.columns(2)
with col1:
    action_type = st.radio("你想做什么？", ["日常打卡 (更新剩余电量)", "充值电费 (增加电量)"])

with col2:
    current_bj_time = datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")

    if action_type == "日常打卡 (更新剩余电量)":
        new_val = st.number_input("输入电表显示的最新度数", min_value=0.0, value=current_elec, step=1.0)
        remark = st.text_input("备注", "日常记录")
        
        if st.button("💾 保存记录", type="primary"):
            change = new_val - current_elec
            save_record(current_bj_time, new_val, change, '日常消耗' if change <= 0 else '异常增加', remark)
            st.rerun()

    else:
        recharge_val = st.number_input("输入充值的度数", min_value=0.0, value=50.0, step=10.0)
        remark = st.text_input("备注 (例如：张三充值50元)", "充值")
        
        if st.button("💰 保存充值记录", type="primary"):
            new_val = current_elec + recharge_val
            save_record(current_bj_time, new_val, recharge_val, '充值', remark)
            st.rerun()

st.divider()

# --- 数据管理区 ---
st.subheader("🛠️ 数据管理")

if not df.empty:
    time_list = df.sort_values('记录时间', ascending=False)['记录时间'].dt.strftime('%Y-%m-%d %H:%M:%S').tolist()
    selected_time_str = st.selectbox("请选择要修改或删除的记录 (按时间)", time_list)
    
    if selected_time_str:
        row_idx = df['记录时间'].dt.strftime('%Y-%m-%d %H:%M:%S') == selected_time_str
        row = df[row_idx].iloc[0]
        
        with st.expander("展开编辑面板", expanded=False):
            col_e1, col_e2 = st.columns(2)
            with col_e1:
                edit_val = st.number_input("剩余电量", value=float(row['当前剩余电量']), key="e_val")
                edit_change = st.number_input("电量变化", value=float(row['电量变化']), key="e_change")
            with col_e2:
                type_options = ["日常消耗", "异常增加", "充值"]
                current_type = row['类型'] if row['类型'] in type_options else "日常消耗"
                edit_type = st.selectbox("记录类型", type_options, index=type_options.index(current_type), key="e_type")
                edit_remark = st.text_input("备注", value=str(row['备注']), key="e_remark")
                
            col_btn1, col_btn2 = st.columns(2)
            target_time_obj = datetime.strptime(selected_time_str, "%Y-%m-%d %H:%M:%S")
            
            with col_btn1:
                if st.button("💾 保存修改", use_container_width=True):
                    update_record_db(target_time_obj, edit_val, edit_change, edit_type, edit_remark)
                    st.success("修改成功！")
                    st.rerun()
            with col_btn2:
                if st.button("🗑️ 删除该记录", type="primary", use_container_width=True):
                    delete_record_db(target_time_obj)
                    st.warning("记录已删除！")
                    st.rerun()
else:
    st.info("暂无数据可管理。")

st.divider()

# --- 数据展示区 ---
st.subheader("📊 历史趋势与明细")

if not df.empty:
    chart_data = df.set_index('记录时间')['当前剩余电量']
    st.line_chart(chart_data)
    
    display_df = df.copy().sort_values('记录时间', ascending=False)
    display_df['记录时间'] = display_df['记录时间'].dt.strftime('%Y-%m-%d %H:%M:%S')
    st.dataframe(display_df, use_container_width=True)
else:
    st.info("暂无数据，请在上方添加你的第一条记录吧！")
