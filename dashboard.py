"""
財務帳單管理介面
執行方式：streamlit run dashboard.py
"""

import streamlit as st
import gspread
import datetime
import json
import os
import base64
import pandas as pd
from google.oauth2.service_account import Credentials

# ── 設定 ────────────────────────────────────────────────────────────
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "1vn-2RQqOxAXydbptP2B88vyKiWhKFeNjAUi3Wdn-etQ")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "填入你的_GEMINI_API_KEY")

st.set_page_config(
    page_title="財務帳單管理",
    page_icon="💰",
    layout="wide"
)

# ── Google Sheet 連線 ────────────────────────────────────────────────
@st.cache_resource
def get_sheet():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    if "GOOGLE_CREDENTIALS" in st.secrets:
        creds_info = json.loads(st.secrets["GOOGLE_CREDENTIALS"])
        creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
    else:
        creds = Credentials.from_service_account_file("credentials.json", scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SPREADSHEET_ID)
    try:
        return sh.worksheet("帳單記錄")
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet("帳單記錄", rows=1000, cols=10)
        ws.append_row(["ID","服務名稱","金額","幣別","截止日期","狀態","來源","建立日期","備註"])
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

# ── 用 Gemini 辨識圖片帳單 ───────────────────────────────────────────
def analyze_image_bill(image_bytes, mime_type):
    import urllib.request
    import urllib.parse

    key = st.secrets.get("GEMINI_API_KEY", GEMINI_API_KEY)
    image_b64 = base64.b64encode(image_bytes).decode()

    payload = json.dumps({
        "contents": [{
            "parts": [
                {
                    "inline_data": {
                        "mime_type": mime_type,
                        "data": image_b64
                    }
                },
                {
                    "text": """請從這張帳單圖片中擷取資訊，用 JSON 格式回傳：
{
  "service_name": "服務或公司名稱",
  "amount": 金額數字,
  "currency": "幣別（TWD/USD等）",
  "due_date": "截止日期 YYYY-MM-DD 格式，找不到填 null",
  "note": "備註"
}
只回傳 JSON，不要其他文字。"""
                }
            ]
        }],
        "generationConfig": {"temperature": 0}
    }).encode()

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent?key={key}"
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read())
            text = result["candidates"][0]["content"]["parts"][0]["text"].strip()
            text = text.replace("```json", "").replace("```", "").strip()
            return json.loads(text)
    except Exception as e:
        return None

# ── 標題 ─────────────────────────────────────────────────────────────
st.title("💰 財務帳單管理")
st.caption("資料同步自 Google Sheet，每 60 秒自動更新")

# ── Tab 介面 ─────────────────────────────────────────────────────────
tab1, tab2, tab3 = st.tabs(["📋 帳單列表", "📷 拍照新增", "➕ 手動新增"])

# ── Tab 1：帳單列表 ──────────────────────────────────────────────────
with tab1:
    df = load_data()

    if df.empty:
        st.info("還沒有帳單記錄！")
    else:
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
        col4.metric("逾期", len(overdue))
        col5.metric("即將到期", len(soon))

        if len(overdue) > 0:
            st.error(f"⚠️ 有 {len(overdue)} 筆帳單已逾期！")
        if len(soon) > 0:
            st.warning(f"⏰ 有 {len(soon)} 筆帳單 7 天內到期！")

        st.divider()

        col_search, col_filter = st.columns([2, 1])
        with col_search:
            search = st.text_input("🔍 搜尋服務名稱", placeholder="輸入關鍵字…")
        with col_filter:
            status_filter = st.selectbox("篩選狀態", ["全部", "待繳", "已繳", "逾期", "即將到期"])

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

        st.subheader(f"帳單列表（{len(filtered)} 筆）")

        for _, row in filtered.iterrows():
            days = days_until(row['截止日期'])
            is_paid = row['狀態'] == '已繳'
            is_overdue = not is_paid and days is not None and days < 0
            is_soon = not is_paid and days is not None and 0 <= days <= 7

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

    if st.button("🔄 重新整理資料"):
        st.cache_data.clear()
        st.rerun()

# ── Tab 2：拍照新增 ──────────────────────────────────────────────────
with tab2:
    st.subheader("📷 拍照辨識紙本帳單")
    st.caption("拍照或上傳帳單圖片，AI 自動辨識金額和日期")

    uploaded = st.file_uploader(
        "上傳帳單照片",
        type=["jpg", "jpeg", "png"],
        help="支援 JPG、PNG 格式"
    )

    if uploaded:
        st.image(uploaded, caption="上傳的帳單", use_column_width=True)

        if st.button("🤖 AI 自動辨識", type="primary"):
            with st.spinner("AI 辨識中，請稍候…"):
                image_bytes = uploaded.read()
                mime_type = uploaded.type
                result = analyze_image_bill(image_bytes, mime_type)

            if result:
                st.success("✅ 辨識成功！請確認以下資料：")

                with st.form("confirm_image_bill"):
                    name = st.text_input("服務名稱", value=result.get("service_name", ""))
                    col1, col2 = st.columns(2)
                    with col1:
                        amount = st.number_input("金額", value=float(result.get("amount") or 0), min_value=0.0, step=1.0)
                    with col2:
                        currency = st.selectbox("幣別", ["TWD", "USD", "JPY", "TRY", "EUR"],
                            index=["TWD", "USD", "JPY", "TRY", "EUR"].index(result.get("currency", "TWD"))
                            if result.get("currency") in ["TWD", "USD", "JPY", "TRY", "EUR"] else 0
                        )

                    due_val = None
                    if result.get("due_date"):
                        try:
                            due_val = datetime.date.fromisoformat(result["due_date"])
                        except:
                            due_val = None
                    due_date = st.date_input("截止日期", value=due_val)
                    note = st.text_input("備註", value=result.get("note", ""))

                    if st.form_submit_button("✅ 確認儲存", type="primary"):
                        ws = get_sheet()
                        today = datetime.date.today().isoformat()
                        due_str = due_date.isoformat() if due_date else ""
                        ws.append_row([
                            f"photo_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}",
                            name, amount, currency, due_str, "待繳", "拍照新增", today, note
                        ])
                        st.success(f"✅ 已新增：{name}")
                        st.cache_data.clear()
            else:
                st.error("❌ 辨識失敗，請確認圖片清晰，或手動輸入")

# ── Tab 3：手動新增 ──────────────────────────────────────────────────
with tab3:
    st.subheader("➕ 手動新增帳單")
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
