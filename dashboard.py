"""
財務帳單管理介面
執行方式：streamlit run dashboard.py
"""

import streamlit as st
import gspread
import datetime
import pandas as pd
from google.oauth2.service_account import Credentials

# ── 設定 ────────────────────────────────────────────────────────────
SPREADSHEET_ID   = "1vn-2RQqOxAXydbptP2B88vyKiWhKFeNjAUi3Wdn-etQ"
CREDENTIALS_FILE = "credentials.json"

st.set_page_config(
    page_title="財務帳單管理",
    page_icon="💰",
    layout="wide"
)

# ── 自訂樣式 ─────────────────────────────────────────────────────────
st.markdown("""
<style>
    .metric-card { background: #f8f9fa; border-radius: 10px; padding: 16px; text-align: center; }
    .stDataFrame { border-radius: 10px; }
    div[data-testid="stMetric"] { background: #f8f9fa; border-radius: 10px; padding: 12px; }
</style>
""", unsafe_allow_html=True)

# ── Google Sheet 連線 ────────────────────────────────────────────────
@st.cache_resource
def get_sheet():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SPREADSHEET_ID)
    try:
        return sh.worksheet("帳單記錄")
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet("帳單記錄", rows=1000, cols=10)
        ws.append_row(["ID", "服務名稱", "金額", "幣別", "截止日期", "狀態", "來源", "建立日期", "備註"])
        return ws

@st.cache_data(ttl=60)
def load_data():
    ws = get_sheet()
    records = ws.get_all_records()
    if not records:
        return pd.DataFrame(columns=["ID","服務名稱","金額","幣別","截止日期","狀態","來源","建立日期","備註"])
    df = pd.DataFrame(records)
    df['金額'] = pd.to_numeric(df['金額'], errors='coerce')
    return df

def days_until(date_str):
    if not date_str:
        return None
    try:
        due = datetime.date.fromisoformat(str(date_str))
        return (due - datetime.date.today()).days
    except:
        return None

# ── 標題 ─────────────────────────────────────────────────────────────
st.title("💰 財務帳單管理")
st.caption("資料同步自 Google Sheet，每 60 秒自動更新")

# ── 載入資料 ─────────────────────────────────────────────────────────
df = load_data()

if df.empty:
    st.info("還沒有帳單記錄，先跑 agent.py 掃描 Gmail！")
else:
    # ── 統計看板 ─────────────────────────────────────────────────────
    pending = df[df['狀態'] == '待繳']
    paid = df[df['狀態'] == '已繳']

    overdue = pending[pending['截止日期'].apply(
        lambda x: days_until(x) is not None and days_until(x) < 0
    )]
    soon = pending[pending['截止日期'].apply(
        lambda x: days_until(x) is not None and 0 <= days_until(x) <= 7
    )]

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("所有帳單", len(df))
    col2.metric("待繳", len(pending))
    col3.metric("已繳", len(paid))
    col4.metric("逾期", len(overdue), delta=f"-{len(overdue)}" if len(overdue) > 0 else None, delta_color="inverse")
    col5.metric("即將到期", len(soon))

    # ── 到期警示 ─────────────────────────────────────────────────────
    if len(overdue) > 0:
        st.error(f"⚠️ 有 {len(overdue)} 筆帳單已逾期！")
    if len(soon) > 0:
        st.warning(f"⏰ 有 {len(soon)} 筆帳單 7 天內到期！")

    st.divider()

    # ── 篩選和搜尋 ────────────────────────────────────────────────────
    col_search, col_filter = st.columns([2, 1])
    with col_search:
        search = st.text_input("🔍 搜尋服務名稱", placeholder="輸入關鍵字…")
    with col_filter:
        status_filter = st.selectbox("篩選狀態", ["全部", "待繳", "已繳", "逾期", "即將到期"])

    # 套用篩選
    filtered = df.copy()
    if search:
        filtered = filtered[filtered['服務名稱'].str.contains(search, case=False, na=False)]
    if status_filter == "待繳":
        filtered = filtered[filtered['狀態'] == '待繳']
    elif status_filter == "已繳":
        filtered = filtered[filtered['狀態'] == '已繳']
    elif status_filter == "逾期":
        filtered = filtered[filtered['截止日期'].apply(
            lambda x: days_until(x) is not None and days_until(x) < 0
        )]
    elif status_filter == "即將到期":
        filtered = filtered[filtered['截止日期'].apply(
            lambda x: days_until(x) is not None and 0 <= days_until(x) <= 7
        )]

    # ── 帳單列表 ─────────────────────────────────────────────────────
    st.subheader(f"帳單列表（{len(filtered)} 筆）")

    for _, row in filtered.iterrows():
        days = days_until(row['截止日期'])
        is_paid = row['狀態'] == '已繳'
        is_overdue = not is_paid and days is not None and days < 0
        is_soon = not is_paid and days is not None and 0 <= days <= 7

        # 顏色標示
        if is_overdue:
            border_color = "#ff4b4b"
        elif is_soon:
            border_color = "#ffa500"
        elif is_paid:
            border_color = "#00cc88"
        else:
            border_color = "#e0e0e0"

        with st.container():
            st.markdown(f"""
            <div style="border-left: 4px solid {border_color}; padding: 8px 16px; margin: 4px 0; background: #fafafa; border-radius: 0 8px 8px 0;">
            </div>
            """, unsafe_allow_html=True)

            col_name, col_amt, col_due, col_status, col_action = st.columns([3, 2, 2, 1, 2])

            with col_name:
                st.write(f"**{row['服務名稱']}**")
                if row['備註']:
                    st.caption(row['備註'])

            with col_amt:
                amt = row['金額']
                if pd.notna(amt):
                    st.write(f"{row['幣別']} {amt:,.2f}")
                else:
                    st.write("—")

            with col_due:
                if row['截止日期']:
                    if is_overdue:
                        st.write(f"🔴 {row['截止日期']}")
                        st.caption(f"逾期 {abs(days)} 天")
                    elif is_soon:
                        st.write(f"🟡 {row['截止日期']}")
                        st.caption(f"還有 {days} 天")
                    else:
                        st.write(row['截止日期'])
                else:
                    st.write("—")

            with col_status:
                if is_paid:
                    st.success("已繳")
                elif is_overdue:
                    st.error("逾期")
                elif is_soon:
                    st.warning("快到期")
                else:
                    st.info("待繳")

            with col_action:
                if not is_paid:
                    if st.button("✅ 標記已繳", key=f"paid_{row['ID']}"):
                        ws = get_sheet()
                        records = ws.get_all_records()
                        for i, r in enumerate(records):
                            if str(r.get('ID')) == str(row['ID']):
                                ws.update_cell(i + 2, 6, '已繳')
                                st.cache_data.clear()
                                st.rerun()
                else:
                    if st.button("↩️ 取消已繳", key=f"unpaid_{row['ID']}"):
                        ws = get_sheet()
                        records = ws.get_all_records()
                        for i, r in enumerate(records):
                            if str(r.get('ID')) == str(row['ID']):
                                ws.update_cell(i + 2, 6, '待繳')
                                st.cache_data.clear()
                                st.rerun()

        st.divider()

# ── 新增帳單 ─────────────────────────────────────────────────────────
st.subheader("➕ 新增帳單")
with st.form("add_bill"):
    col1, col2 = st.columns(2)
    with col1:
        name = st.text_input("服務名稱 *", placeholder="例：Netflix、台灣大哥大")
        amount = st.number_input("金額", min_value=0.0, step=1.0)
        currency = st.selectbox("幣別", ["TWD", "USD", "JPY", "TRY", "EUR"])
    with col2:
        due_date = st.date_input("截止日期", value=None)
        note = st.text_input("備註（選填）", placeholder="例：年費、家庭方案")

    submitted = st.form_submit_button("儲存帳單", type="primary")
    if submitted:
        if not name:
            st.error("請填入服務名稱！")
        else:
            ws = get_sheet()
            today = datetime.date.today().isoformat()
            due_str = due_date.isoformat() if due_date else ""
            ws.append_row([
                f"manual_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}",
                name, amount, currency, due_str, "待繳", "手動新增", today, note
            ])
            st.success(f"✅ 已新增：{name}")
            st.cache_data.clear()
            st.rerun()

# ── 重新整理按鈕 ─────────────────────────────────────────────────────
if st.button("🔄 重新整理資料"):
    st.cache_data.clear()
    st.rerun()
