"""
財務帳單 Agent
- 自動讀取 Gmail 電子帳單
- 用 Groq AI 辨識帳單內容
- 記錄到 Google Sheet
- 到期前發送 Telegram 和 Email 提醒
"""

import os
import json
import base64
import datetime
import re
from email.mime.text import MIMEText

from groq import Groq
from google.oauth2.service_account import Credentials
from google.oauth2.credentials import Credentials as OAuthCredentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import gspread

# ── 設定區 ───────────────────────────────────────────────────────────
GROQ_API_KEY     = os.environ.get("GROQ_API_KEY", "")
SPREADSHEET_ID   = os.environ.get("SPREADSHEET_ID", "1vn-2RQqOxAXydbptP2B88vyKiWhKFeNjAUi3Wdn-etQ")
CREDENTIALS_FILE = "credentials.json"
OAUTH_FILE       = "oauth.json"
OAUTH_TOKEN_FILE = "token.json"
YOUR_EMAIL       = os.environ.get("YOUR_EMAIL", "kcfengsanity@gmail.com")
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "8654866495:AAFJ5yzb2t1xvYRBUiZ8l3rQkA0JnS9Ji-E")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "8169349551")
DAYS_BEFORE_DUE  = [7, 3, 1]

# ── 初始化 Groq ──────────────────────────────────────────────────────
groq_client = Groq(api_key=GROQ_API_KEY)

# ── Gmail 授權範圍 ───────────────────────────────────────────────────
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

# ── OAuth 登入 Gmail ─────────────────────────────────────────────────
def get_gmail_creds():
    gmail_token_json = os.environ.get("GMAIL_TOKEN")
    if gmail_token_json:
        creds = OAuthCredentials.from_authorized_user_info(
            json.loads(gmail_token_json), GMAIL_SCOPES
        )
        if creds and creds.valid:
            return creds
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            return creds

    creds = None
    if os.path.exists(OAUTH_TOKEN_FILE):
        creds = OAuthCredentials.from_authorized_user_file(OAUTH_TOKEN_FILE, GMAIL_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(OAUTH_FILE, GMAIL_SCOPES)
            creds = flow.run_local_server(port=0)
        with open(OAUTH_TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return creds

# ── 初始化 Google Sheets ─────────────────────────────────────────────
def get_sheet():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]

    google_creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if google_creds_json:
        creds = Credentials.from_service_account_info(
            json.loads(google_creds_json), scopes=scopes
        )
    else:
        creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)

    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SPREADSHEET_ID)

    try:
        return sh.worksheet("帳單記錄")
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet("帳單記錄", rows=1000, cols=10)
        ws.append_row(["ID", "服務名稱", "金額", "幣別", "截止日期", "狀態", "來源", "建立日期", "備註"])
        return ws

# ── 讀取 Gmail 帳單 ──────────────────────────────────────────────────
def fetch_bill_emails(gmail_creds):
    service = build("gmail", "v1", credentials=gmail_creds)
    query = "subject:(帳單 OR invoice OR 繳費 OR 扣款 OR 通知 OR bill OR payment OR receipt OR 收據 OR 訂閱 OR subscription OR 費用 OR charge) newer_than:90d"
    results = service.users().messages().list(userId="me", q=query, maxResults=20).execute()
    messages = results.get("messages", [])

    emails = []
    for msg in messages:
        detail = service.users().messages().get(userId="me", id=msg["id"], format="full").execute()
        headers = {h["name"]: h["value"] for h in detail["payload"]["headers"]}
        subject = headers.get("Subject", "")
        sender  = headers.get("From", "")
        date    = headers.get("Date", "")

        body = ""
        if "parts" in detail["payload"]:
            for part in detail["payload"]["parts"]:
                if part["mimeType"] == "text/plain":
                    data = part["body"].get("data", "")
                    body = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
                    break
        else:
            data = detail["payload"]["body"].get("data", "")
            if data:
                body = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")

        emails.append({
            "id": msg["id"],
            "subject": subject,
            "sender": sender,
            "date": date,
            "body": body[:3000]
        })

    return emails

# ── 用 Groq AI 分析帳單 ──────────────────────────────────────────────
def analyze_bill(email):
    prompt = f"""
你是一個帳單分析助手。請從以下電子郵件中擷取帳單資訊。

郵件主旨：{email['subject']}
寄件者：{email['sender']}
內容：{email['body']}

請用 JSON 格式回傳以下欄位（如果找不到就填 null）：
{{
  "is_bill": true/false,
  "service_name": "服務或公司名稱",
  "amount": 金額數字,
  "currency": "幣別（TWD/USD等）",
  "due_date": "截止日期 YYYY-MM-DD 格式",
  "note": "備註"
}}

只回傳 JSON，不要其他文字。
"""
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )
    text = response.choices[0].message.content.strip()
    text = re.sub(r"```json|```", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"is_bill": False}

# ── 存入 Google Sheet ────────────────────────────────────────────────
def save_to_sheet(ws, email_id, bill_data):
    existing = ws.col_values(1)
    if email_id in existing:
        return False

    today = datetime.date.today().isoformat()
    ws.append_row([
        email_id,
        bill_data.get("service_name", ""),
        bill_data.get("amount", ""),
        bill_data.get("currency", "TWD"),
        bill_data.get("due_date", ""),
        "待繳",
        "Gmail",
        today,
        bill_data.get("note", ""),
    ])
    return True

# ── 檢查即將到期的帳單 ───────────────────────────────────────────────
def check_due_bills(ws):
    records = ws.get_all_records()
    today = datetime.date.today()
    due_soon = []

    for row in records:
        if row.get("狀態") == "已繳" or not row.get("截止日期"):
            continue
        try:
            due = datetime.date.fromisoformat(str(row["截止日期"]))
            days_left = (due - today).days
            if days_left in DAYS_BEFORE_DUE:
                due_soon.append({**row, "days_left": days_left})
        except ValueError:
            continue

    return due_soon

# ── 發送 Email 提醒 ──────────────────────────────────────────────────
def send_email_reminder(gmail_creds, bills):
    if not bills:
        return

    service = build("gmail", "v1", credentials=gmail_creds)
    body = "以下帳單即將到期，請記得繳款：\n\n"
    for b in bills:
        body += f"• {b['服務名稱']}  金額：{b['幣別']} {b['金額']}  截止：{b['截止日期']}（還有 {b['days_left']} 天）\n"
    body += "\n此郵件由財務 Agent 自動發送。"

    msg = MIMEText(body, "plain", "utf-8")
    msg["To"]      = YOUR_EMAIL
    msg["From"]    = YOUR_EMAIL
    msg["Subject"] = f"⚠️ 帳單繳款提醒：{len(bills)} 筆即將到期"

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()
    print(f"✅ Email 提醒已發送（{len(bills)} 筆帳單）")

# ── 發送 Telegram 提醒 ───────────────────────────────────────────────
def send_telegram_reminder(bills):
    if not bills or not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return

    import urllib.request
    import urllib.parse

    msg = "⚠️ *帳單繳款提醒*\n\n"
    for b in bills:
        msg += f"• *{b['服務名稱']}*\n"
        msg += f"  金額：{b['幣別']} {b['金額']}\n"
        msg += f"  截止：{b['截止日期']}（還有 {b['days_left']} 天）\n\n"
    msg += "請記得繳款！"

    encoded = urllib.parse.quote(msg)
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage?chat_id={TELEGRAM_CHAT_ID}&text={encoded}&parse_mode=Markdown"
    try:
        urllib.request.urlopen(url)
        print(f"✅ Telegram 提醒已發送（{len(bills)} 筆帳單）")
    except Exception as e:
        print(f"❌ Telegram 發送失敗：{e}")

# ── 主程式 ───────────────────────────────────────────────────────────
def main():
    print("🤖 財務 Agent 啟動中…")

    ws = get_sheet()
    print("✅ Google Sheet 連線成功")

    print("\n🔐 登入 Gmail…")
    gmail_creds = get_gmail_creds()
    print("✅ Gmail 登入成功")

    print("\n📧 讀取 Gmail 中…")
    emails = fetch_bill_emails(gmail_creds)
    print(f"   找到 {len(emails)} 封可能的帳單郵件")

    new_count = 0
    for email in emails:
        print(f"   分析：{email['subject'][:40]}…")
        bill = analyze_bill(email)
        if bill.get("is_bill"):
            saved = save_to_sheet(ws, email["id"], bill)
            if saved:
                new_count += 1
                print(f"   ✅ 新增：{bill.get('service_name')} {bill.get('amount')} {bill.get('due_date')}")

    print(f"\n📊 新增 {new_count} 筆帳單記錄")

    print("\n⏰ 檢查即將到期的帳單…")
    due_bills = check_due_bills(ws)

    if due_bills:
        print(f"   找到 {len(due_bills)} 筆即將到期")
        send_email_reminder(gmail_creds, due_bills)
        send_telegram_reminder(due_bills)
    else:
        print("   目前沒有即將到期的帳單")

    print("\n✅ 完成！")


if __name__ == "__main__":
    main()
