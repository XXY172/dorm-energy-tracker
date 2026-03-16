import streamlit as st
import pandas as pd
import os
from datetime import datetime, timezone, timedelta
from sqlalchemy import create_engine, text

# --- 核心设置 ---
BJ_TZ = timezone(timedelta(hours=8))
DB_URL = os.getenv("DATABASE_URL", "sqlite:///local_dorm_data.db")

if DB_URL.startswith("postgresql://"):
    DB_URL = DB_URL.replace("postgresql://", "postgresql+pg8000://", 1)
elif DB_URL.startswith("postgres://"):
    DB_URL = DB_URL.replace("postgres://", "postgresql+pg8000://", 1)

engine = create_engine(DB_URL)

# --- 数据库初始化与平滑升级 ---
def init_db():
    with engine.begin() as conn:
        # 1. 创建用户表
        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS users (
                dorm_id VARCHAR(50) PRIMARY KEY,
                password VARCHAR(50)
            )
        '''))
        # 2. 创建记录表（新增了 dorm_id 字段）
        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS records (
                记录时间 TIMESTAMP,
                当前剩余电量 FLOAT,
                电量变化 FLOAT,
                类型 VARCHAR(50),
                备注 VARCHAR(255),
                dorm_id VARCHAR(50)
            )
        '''))
        # 3. 兼容旧数据的热更新：尝试给已存在的旧表加上 dorm_id 字段
        try:
            conn.execute(text("ALTER TABLE records ADD COLUMN dorm_id VARCHAR(50)"))
            conn.execute(text("UPDATE records SET dorm_id = '默认老寝室' WHERE dorm_id IS NULL"))
        except Exception:
            pass # 如果字段已存在会报错，直接忽略即可

init_db()

# --- 数据库操作函数 (加入 dorm_id 隔离) ---
def load_data(dorm_id):
    try:
        # 只读取当前登录寝室的数据
        df = pd.read_sql(
            text("SELECT * FROM records WHERE dorm_id = :dorm_id ORDER BY 记录时间 ASC"), 
            engine, 
            params={"dorm_id": dorm_id}
        )
        if not df.empty:
            df['记录时间'] = pd.to_datetime(df['记录时间'])
        return df
    except Exception as e:
        return pd.DataFrame(columns=['记录时间', '当前剩余电量', '电量变化', '类型', '备注', 'dorm_id'])

def save_record(now_str, new_val, change, type_str, remark, dorm_id):
    df = pd.DataFrame([{
        '记录时间': datetime.strptime(now_str, "%Y-%m-%d %H:%M:%S"), 
        '当前剩余电量': new_val, 
        '电量变化': change, 
        '类型': type_str, 
        '备注': remark,
        'dorm_id': dorm_id
    }])
    df.to_sql('records', engine, if_exists='append', index=False)

def delete_record_db(record_time, dorm_id):
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM records WHERE 记录时间 = :time AND dorm_id = :dorm_id"), 
                     {"time": record_time, "dorm_id": dorm_id})

def update_record_db(old_time, new_val, change, type_str, remark, dorm_id):
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE records 
            SET 当前剩余电量 = :new_val, 电量变化 = :change, 类型 = :type_str, 备注 = :remark 
            WHERE 记录时间 = :time AND dorm_id = :dorm_id
        """), {"new_val": new_val, "change": change, "type_str": type_str, "remark": remark, "time": old_time, "dorm_id": dorm_id})

# --- 页面基础设置 ---
st.set_page_config(page_title="寝室电量管家", page_icon="⚡")

# --- 登录模块 ---
if 'logged_in' not in st.session_state:
    st.session_state['logged_in'] = False
    st.session_state['dorm_id'] = None

if not st.session_state['logged_in']:
    st.title("⚡ 寝室电量管家 - 登录")
    st.info("首次登录的寝室号和密码将自动注册为初始账号。")
    
    with st.form("login_form"):
        dorm_input = st.text_input("🏠 你的寝室号 (例如: 301)")
        pwd_input = st.text_input("🔑 密码", type="password")
        submit_btn = st.form_submit_button("进入管家")
        
        if submit_btn:
            if dorm_input and pwd_input:
                with engine.begin() as conn:
                    result = conn.execute(text("SELECT password FROM users WHERE dorm_id = :dorm_id"), {"dorm_id": dorm_input}).fetchone()
                    
                    if result:
                        # 寝室已存在，校验密码
                        if result[0] == pwd_input:
                            st.session_state['logged_in'] = True
                            st.session_state['dorm_id'] = dorm_input
                            st.rerun()
                        else:
                            st.error("密码错误，请重试！")
                    else:
                        # 寝室不存在，自动注册
                        conn.execute(text("INSERT INTO users (dorm_id, password) VALUES (:dorm_id, :password)"), 
                                     {"dorm_id": dorm_input, "password": pwd_input})
                        st.session_state['logged_in'] = True
                        st.session_state['dorm_id'] = dorm_input
                        st.success("新寝室注册成功！")
                        st.rerun()
            else:
                st.warning("寝室号和密码不能为空！")
    st.stop() # 阻止未登录时渲染后续页面

# --- 主程序界面 (已登录) ---
current_dorm = st.session_state['dorm_id']

# 顶部导航栏
col_top1, col_top2 = st.columns([3, 1])
with col_top1:
    st.title(f"⚡ {current_dorm} 寝室电量")
with col_top2:
    if st.button("🚪 退出登录"):
        st.session_state['logged_in'] = False
        st.session_state['dorm_id'] = None
        st.rerun()

df = load_data(current_dorm)

current_elec = 0.0
daily_avg = None

if not df.empty:
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
col_m1.metric(label="🔋 电表当前剩余 (度)", value=f"{current_elec:.2f}")
if daily_avg is not None:
    col_m2.metric(label="📉 近期日均耗电 (度/天)", value=f"{daily_avg:.2f}")
else:
    col_m2.metric(label="📉 近期日均耗电 (度/天)", value="暂无数据")

st.divider()

# --- 极简交互输入区 ---
st.subheader("📝 记录最新电表")

col1, col2 = st.columns([1, 2])
with col1:
    action_type = st.radio("本次操作是：", ["日常打卡 (正常消耗)", "刚充了电费 (增加)"])

with col2:
    # 不管是日常还是充值，只要求填电表上的当前数字
    new_val = st.number_input("👉 请输入电表上目前显示的度数", min_value=0.0, value=current_elec, step=1.0)
    remark = st.text_input("备注", "日常记录" if "日常" in action_type else "交电费")
    
    if st.button("💾 确认保存", type="primary", use_container_width=True):
        change = new_val - current_elec
        current_bj_time = datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")
        
        # 智能判断类型
        if "充值" in action_type:
            record_type = "充值"
        else:
            record_type = "日常消耗" if change <= 0 else "异常增加"
            
        save_record(current_bj_time, new_val, change, record_type, remark, current_dorm)
        st.success(f"记录成功！电量变化：{change:+.2f} 度")
        st.rerun()

st.divider()

# --- 数据展示区 ---
st.subheader("📊 账单明细与趋势")

if not df.empty:
    chart_data = df.set_index('记录时间')['当前剩余电量']
    st.line_chart(chart_data)
    
    # 格式化展示数据
    display_df = df.copy().sort_values('记录时间', ascending=False)
    display_df['记录时间'] = display_df['记录时间'].dt.strftime('%Y-%m-%d %H:%M:%S')
    # 隐藏 dorm_id 列，不展示给前端
    display_df = display_df.drop(columns=['dorm_id'])
    
    # 使用带颜色的 dataframe 展示
    st.dataframe(display_df, use_container_width=True)
else:
    st.info("这个寝室还没有记录哦，在上面填入第一笔数据吧！")

st.divider()

# --- 数据管理区 ---
with st.expander("🛠️ 高级：修改或删除历史记录"):
    if not df.empty:
        time_list = df.sort_values('记录时间', ascending=False)['记录时间'].dt.strftime('%Y-%m-%d %H:%M:%S').tolist()
        selected_time_str = st.selectbox("请选择要操作的记录", time_list)
        
        if selected_time_str:
            row_idx = df['记录时间'].dt.strftime('%Y-%m-%d %H:%M:%S') == selected_time_str
            row = df[row_idx].iloc[0]
            
            col_e1, col_e2 = st.columns(2)
            with col_e1:
                edit_val = st.number_input("修改：剩余电量", value=float(row['当前剩余电量']), key="e_val")
                edit_change = st.number_input("修改：电量变化 (增减)", value=float(row['电量变化']), key="e_change")
            with col_e2:
                type_options = ["日常消耗", "异常增加", "充值"]
                current_type = row['类型'] if row['类型'] in type_options else "日常消耗"
                edit_type = st.selectbox("修改：记录类型", type_options, index=type_options.index(current_type), key="e_type")
                edit_remark = st.text_input("修改：备注", value=str(row['备注']), key="e_remark")
                
            col_btn1, col_btn2 = st.columns(2)
            target_time_obj = datetime.strptime(selected_time_str, "%Y-%m-%d %H:%M:%S")
            
            with col_btn1:
                if st.button("💾 保存修改"):
                    update_record_db(target_time_obj, edit_val, edit_change, edit_type, edit_remark, current_dorm)
                    st.success("修改成功！")
                    st.rerun()
            with col_btn2:
                if st.button("🗑️ 删除该记录", type="primary"):
                    delete_record_db(target_time_obj, current_dorm)
                    st.warning("记录已删除！")
                    st.rerun()
    else:
        st.write("暂无数据可管理。")
