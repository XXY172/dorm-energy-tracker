import streamlit as st
import pandas as pd
import os
from datetime import datetime

# 定义本地保存的 CSV 文件名
CSV_FILE = 'dorm_electricity_log.csv'

# 初始化/读取数据的函数
def load_data():
    if os.path.exists(CSV_FILE):
        df = pd.read_csv(CSV_FILE, encoding='utf-8-sig')
        # 将字符串时间转换成 Pandas 的时间对象，方便后续计算时间差
        df['记录时间'] = pd.to_datetime(df['记录时间'])
        return df
    else:
        return pd.DataFrame(columns=['记录时间', '当前剩余电量', '电量变化', '类型', '备注'])

def save_data(df):
    df.to_csv(CSV_FILE, index=False, encoding='utf-8-sig')

# --- 页面主体 ---
st.set_page_config(page_title="寝室电量管家", page_icon="⚡")
st.title("⚡ 寝室电量管家")

# 加载数据
df = load_data()

# --- 核心计算：获取当前电量与日均耗电量 ---
current_elec = 0.0
daily_avg = None

if not df.empty:
    # 按照时间排序，确保最后一条是最新的
    df = df.sort_values('记录时间').reset_index(drop=True)
    current_elec = float(df['当前剩余电量'].iloc[-1])
    
    # 计算近期日均耗电量（需要至少两条数据）
    if len(df) >= 2:
        last_record = df.iloc[-1]
        prev_record = df.iloc[-2]
        
        # 计算两次记录之间相差的天数
        time_diff_days = (last_record['记录时间'] - prev_record['记录时间']).total_seconds() / (24 * 3600)
        
        # 排除掉间隔太短（比如连续点了两次保存）或者最新一次是充值的情况
        if time_diff_days > 0.01 and last_record['类型'] != '充值':
            consumed = prev_record['当前剩余电量'] - last_record['当前剩余电量']
            if consumed > 0:
                daily_avg = consumed / time_diff_days

# --- 顶部数据看板 ---
col_m1, col_m2 = st.columns(2)
col_m1.metric(label="🔋 当前剩余电量 (度)", value=f"{current_elec:.2f}")

if daily_avg is not None:
    col_m2.metric(label="📉 近期日均耗电量 (度/天)", value=f"{daily_avg:.2f}", help="基于最近两次打卡记录的差值和时间计算得出")
else:
    col_m2.metric(label="📉 近期日均耗电量 (度/天)", value="暂无足够数据", help="需要至少两次不同时间的打卡记录才能计算")

st.divider() # 分割线

# --- 交互输入区 ---
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
            
            new_record = pd.DataFrame([{
                '记录时间': now_str, 
                '当前剩余电量': new_val, 
                '电量变化': change, 
                '类型': '日常消耗' if change <= 0 else '异常增加', 
                '备注': remark
            }])
            
            df = pd.concat([df, new_record], ignore_index=True)
            save_data(df)
            st.rerun() # 刷新页面更新数据

    else:
        recharge_val = st.number_input("输入充值的度数", min_value=0.0, value=50.0, step=10.0)
        remark = st.text_input("备注 (例如：张三充值50元)", "充值")
        
        if st.button("💰 保存充值记录", type="primary"):
            new_val = current_elec + recharge_val
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            new_record = pd.DataFrame([{
                '记录时间': now_str, 
                '当前剩余电量': new_val, 
                '电量变化': recharge_val, 
                '类型': '充值', 
                '备注': remark
            }])
            
            df = pd.concat([df, new_record], ignore_index=True)
            save_data(df)
            st.rerun()

st.divider()

# --- 数据展示区 ---
st.subheader("📊 历史趋势与明细")

if not df.empty:
    # 绘制折线图
    chart_data = df.set_index('记录时间')['当前剩余电量']
    st.line_chart(chart_data)
    
    # 格式化时间列以美化表格展示，同时反转顺序让最新的数据显示在最上面
    display_df = df.copy().sort_values('记录时间', ascending=False)
    display_df['记录时间'] = display_df['记录时间'].dt.strftime('%Y-%m-%d %H:%M')
    st.dataframe(display_df, use_container_width=True)
else:
    st.info("暂无数据，请在上方添加你的第一条记录吧！")