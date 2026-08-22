import streamlit as st
import pandas as pd
import os
from datetime import datetime, timezone, timedelta
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

# --- 核心设置 ---
BJ_TZ = timezone(timedelta(hours=8))
DEFAULT_WARNING_THRESHOLD = 20.0
MAX_REMEMBERED_LOGINS = 5

# --- 数据库连接优化 ---
@st.cache_resource
def get_engine():
    # 优先使用 Streamlit 官方云的 secrets 配置
    if "DATABASE_URL" in st.secrets:
        DB_URL = st.secrets["DATABASE_URL"]
    else:
        DB_URL = os.getenv("DATABASE_URL", "sqlite:///local_dorm_data.db")
    
    # 修正前缀兼容性（SQLAlchemy 需要 postgresql://）
    if DB_URL.startswith("postgres://"):
        DB_URL = DB_URL.replace("postgres://", "postgresql://", 1)
        
    return create_engine(DB_URL, poolclass=NullPool)

# 全局调用缓存好的引擎
engine = get_engine()

# --- 数据库初始化与平滑升级 (修复了事务死锁问题) ---
def init_db():
    # 第一步：正常建表（独立事务）
    with engine.connect() as conn:
        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS users (
                dorm_id VARCHAR(50) PRIMARY KEY,
                password VARCHAR(50),
                warning_threshold FLOAT DEFAULT 20.0
            )
        '''))
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
        conn.commit()
        
    # 第二步：兼容旧数据字段升级（捕获错误且不影响主事务）
    try:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE records ADD COLUMN dorm_id VARCHAR(50)"))
            conn.execute(text("UPDATE records SET dorm_id = '默认老寝室' WHERE dorm_id IS NULL"))
            conn.commit()
    except Exception:
        pass # 如果字段已存在会报错，在这里安全忽略

    # 为已有寝室补充低电量预警阈值。
    try:
        with engine.connect() as conn:
            conn.execute(text(
                "ALTER TABLE users ADD COLUMN warning_threshold FLOAT DEFAULT 20.0"
            ))
            conn.commit()
    except Exception:
        pass # 字段已存在时安全忽略

init_db()

# --- 数据库操作函数 ---
def load_data(dorm_id):
    try:
        # 传入 engine.connect() 避免直接传入 engine 导致警告
        with engine.connect() as conn:
            df = pd.read_sql(
                text("SELECT * FROM records WHERE dorm_id = :dorm_id ORDER BY 记录时间 ASC"), 
                conn, 
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
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM records WHERE 记录时间 = :time AND dorm_id = :dorm_id"), 
                     {"time": record_time, "dorm_id": dorm_id})
        conn.commit()

def update_record_db(old_time, new_val, change, type_str, remark, dorm_id):
    with engine.connect() as conn:
        conn.execute(text("""
            UPDATE records 
            SET 当前剩余电量 = :new_val, 电量变化 = :change, 类型 = :type_str, 备注 = :remark 
            WHERE 记录时间 = :time AND dorm_id = :dorm_id
        """), {"new_val": new_val, "change": change, "type_str": type_str, "remark": remark, "time": old_time, "dorm_id": dorm_id})
        conn.commit()

def get_warning_threshold(dorm_id):
    with engine.connect() as conn:
        threshold = conn.execute(text("""
            SELECT warning_threshold FROM users WHERE dorm_id = :dorm_id
        """), {"dorm_id": dorm_id}).scalar()
    return DEFAULT_WARNING_THRESHOLD if threshold is None else float(threshold)

def update_warning_threshold(dorm_id, threshold):
    with engine.connect() as conn:
        conn.execute(text("""
            UPDATE users SET warning_threshold = :threshold WHERE dorm_id = :dorm_id
        """), {"threshold": threshold, "dorm_id": dorm_id})
        conn.commit()

def estimate_threshold_time(current_elec, threshold, daily_avg, now):
    """按当前日均耗电量，返回电量降至阈值的预计时间。"""
    if daily_avg is None or daily_avg <= 0 or current_elec <= threshold:
        return None
    return now + timedelta(days=(current_elec - threshold) / daily_avg)

def remember_login(dorm_id, password):
    """在当前浏览器会话中保存最近成功登录的账号。"""
    remembered_logins = st.session_state.get('remembered_logins', [])
    remembered_logins = [
        login for login in remembered_logins if login['dorm_id'] != dorm_id
    ]
    st.session_state['remembered_logins'] = (
        [{'dorm_id': dorm_id, 'password': password}] + remembered_logins
    )[:MAX_REMEMBERED_LOGINS]

def remove_selected_login():
    """删除下拉框中选中的已保存账号。"""
    selected_dorm = st.session_state.get('saved_login_selector')
    st.session_state['remembered_logins'] = [
        login for login in st.session_state.get('remembered_logins', [])
        if login['dorm_id'] != selected_dorm
    ]
    st.session_state.pop('saved_login_selector', None)
    st.session_state.pop('login_dorm_input', None)
    st.session_state.pop('login_pwd_input', None)

# --- 页面基础设置 ---
st.set_page_config(page_title="寝室电量管家", page_icon="⚡")

# --- 登录模块 ---
if 'logged_in' not in st.session_state:
    st.session_state['logged_in'] = False
    st.session_state['dorm_id'] = None
if 'remembered_logins' not in st.session_state:
    st.session_state['remembered_logins'] = []

if not st.session_state['logged_in']:
    st.title("⚡ 寝室电量管家 - 登录")
    st.info("首次登录的寝室号和密码将自动注册为初始账号。")

    remembered_logins = st.session_state['remembered_logins']
    if remembered_logins:
        login_options = [login['dorm_id'] for login in remembered_logins]
        selected_dorm = st.selectbox(
            "📋 已保存的登录账号",
            options=login_options,
            key='saved_login_selector'
        )
        selected_login = next(
            login for login in remembered_logins
            if login['dorm_id'] == selected_dorm
        )
        st.session_state['login_dorm_input'] = selected_login['dorm_id']
        st.session_state['login_pwd_input'] = selected_login['password']

        st.button(
            "🗑️ 删除已选账号",
            use_container_width=True,
            on_click=remove_selected_login
        )
    with st.form("login_form"):
        dorm_input = st.text_input("🏠 你的寝室号 (例如: 301)", key='login_dorm_input')
        pwd_input = st.text_input("🔑 密码", type="password", key='login_pwd_input')
        submit_btn = st.form_submit_button("进入管家")
        
        if submit_btn:
            if dorm_input and pwd_input:
                with engine.connect() as conn:
                    result = conn.execute(text("SELECT password FROM users WHERE dorm_id = :dorm_id"), {"dorm_id": dorm_input}).fetchone()
                    
                    if result:
                        if result[0] == pwd_input:
                            remember_login(dorm_input, pwd_input)
                            st.session_state['logged_in'] = True
                            st.session_state['dorm_id'] = dorm_input
                            st.rerun()
                        else:
                            st.error("密码错误，请重试！")
                    else:
                        conn.execute(text("INSERT INTO users (dorm_id, password) VALUES (:dorm_id, :password)"), 
                                     {"dorm_id": dorm_input, "password": pwd_input})
                        conn.commit()
                        remember_login(dorm_input, pwd_input)
                        st.session_state['logged_in'] = True
                        st.session_state['dorm_id'] = dorm_input
                        st.success("新寝室注册成功！")
                        st.rerun()
            else:
                st.warning("⚠️ 寝室号和密码都不能为空！")
    st.stop() 

# --- 主程序界面 (已登录) ---
current_dorm = st.session_state['dorm_id']
warning_threshold = get_warning_threshold(current_dorm)

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

col_m1, col_m2, col_m3 = st.columns(3)
col_m1.metric(label="🔋 电表当前剩余 (度)", value=f"{current_elec:.2f}")
if daily_avg is not None:
    col_m2.metric(label="📉 近期日均耗电 (度/天)", value=f"{daily_avg:.2f}")
else:
    col_m2.metric(label="📉 近期日均耗电 (度/天)", value="暂无数据")

estimated_time = estimate_threshold_time(
    current_elec, warning_threshold, daily_avg, datetime.now(BJ_TZ)
)
if df.empty:
    col_m3.metric(label=f"⏳ 预计低于 {warning_threshold:.2f} 度", value="等待首条记录")
elif current_elec <= warning_threshold:
    col_m3.metric(label=f"⚠️ 低于 {warning_threshold:.2f} 度", value="已低于阈值")
elif estimated_time is not None:
    col_m3.metric(
        label=f"⏳ 预计低于 {warning_threshold:.2f} 度",
        value=estimated_time.strftime("%m-%d %H:%M")
    )
else:
    col_m3.metric(label=f"⏳ 预计低于 {warning_threshold:.2f} 度", value="等待耗电数据")

if not df.empty and current_elec <= warning_threshold:
    st.error(f"当前剩余电量已低于你设置的 {warning_threshold:.2f} 度阈值，请及时充值。")
elif estimated_time is not None:
    st.info(
        f"若保持近期 {daily_avg:.2f} 度/天的耗电速度，预计将在 "
        f"{estimated_time.strftime('%Y-%m-%d %H:%M')} 低于 {warning_threshold:.2f} 度。"
    )

with st.expander("⚙️ 低电量预警设置"):
    with st.form("warning_threshold_form"):
        threshold_input = st.number_input(
            "预警阈值（度）",
            min_value=0.0,
            value=warning_threshold,
            step=1.0,
            help="剩余电量预计低于此数值时显示预警。"
        )
        if st.form_submit_button("保存阈值"):
            update_warning_threshold(current_dorm, threshold_input)
            st.success("低电量预警阈值已保存。")
            st.rerun()

st.divider()

# --- 极简交互输入区 ---
st.subheader("📝 记录最新电表")

col1, col2 = st.columns([1, 2])
with col1:
    action_type = st.radio("本次操作是：", ["日常打卡 (正常消耗)", "刚充了电费 (增加)"])

with col2:
    new_val = st.number_input("👉 请输入电表上目前显示的度数", min_value=0.0, value=current_elec, step=1.0)
    remark = st.text_input("备注", "日常记录" if "日常" in action_type else "交电费")
    
    if st.button("💾 确认保存", type="primary", use_container_width=True):
        change = new_val - current_elec
        current_bj_time = datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")
        
        # 🐛 修复后的智能判断逻辑
        if action_type == "刚充了电费 (增加)":
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
    
    display_df = df.copy().sort_values('记录时间', ascending=False)
    display_df['记录时间'] = display_df['记录时间'].dt.strftime('%Y-%m-%d %H:%M:%S')
    display_df = display_df.drop(columns=['dorm_id'])
    
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
