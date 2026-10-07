import streamlit as st
import pandas as pd
from datetime import datetime, date, time, timedelta
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import secrets
import io
import os
import urllib.request
import threading
import time as pytime
import gspread
from google.oauth2.service_account import Credentials

# ไลบรารีสำหรับสร้างไฟล์ PDF
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ตัวล็อกระดับโปรเซส ป้องกันการแย่งกันเขียนฐานข้อมูลเมื่อกดย้ำ (Race Condition Lock)
BOOKING_LOCK = threading.Lock()

# ตัวแปรจำใน RAM ป้องกันการกดย้ำซ้ำในระดับเสี้ยววินาที (แก้ปัญหา Google Sheet Latency)
RECENT_BOOKING_SUBMISSIONS = {}

# ========== คอนฟิกหน้าเว็บ ==========
st.set_page_config(
    page_title="ระบบจองคิวทันตกรรม ศูนย์บริการสาธารณสุข 65 รักษาศุข บางบอน",
    page_icon="🦷",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ========== Custom CSS ==========
CUSTOM_CSS = """<style>
@import url('https://fonts.googleapis.com/css2?family=Sarabun:wght@300;400;500;600;700&display=swap');
html, body, [class*="css"] { font-family: 'Sarabun', sans-serif; }

.hero-banner {
    background: linear-gradient(135deg, #0ea5e9 0%, #0284c7 50%, #0369a1 100%);
    color: white;
    padding: 2rem;
    border-radius: 16px;
    margin-bottom: 2rem;
    box-shadow: 0 10px 25px -5px rgba(14, 165, 233, 0.25);
}
.hero-banner h1 { margin: 0; font-size: 1.8rem; font-weight: 700; color: white; }
.hero-banner p { margin-top: 0.5rem; margin-bottom: 0; font-size: 1.05rem; opacity: 0.95; }

[data-testid="stMetric"] {
    background: #ffffff;
    border: 1px solid #e2e8f0;
    padding: 1.25rem 1.5rem;
    border-radius: 12px;
    box-shadow: 0 2px 4px rgba(0,0,0,0.02);
}

button[kind="primary"] {
    background: linear-gradient(135deg, #0284c7 0%, #0369a1 100%) !important;
    border: none !important;
    border-radius: 10px !important;
    padding: 0.6rem 1.5rem !important;
    font-weight: 600 !important;
    box-shadow: 0 4px 12px rgba(2, 132, 199, 0.3) !important;
}

/* ป้องกันปุ่มรับคลิกซ้ำขณะประมวลผล */
button[kind="primary"]:active {
    pointer-events: none !important;
    opacity: 0.65 !important;
}

.day-card {
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 10px;
    padding: 12px 8px;
    min-height: 240px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
}
.day-header {
    text-align: center;
    font-weight: 700;
    font-size: 0.95rem;
    color: #0f172a;
    margin-bottom: 4px;
}
.day-sub {
    text-align: center;
    font-size: 0.8rem;
    color: #64748b;
    margin-bottom: 12px;
}
.slot-pill-box {
    display: flex;
    justify-content: space-between;
    align-items: center;
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 6px;
    padding: 5px 8px;
    margin-bottom: 6px;
    font-size: 0.82rem;
}
.pill-badge {
    background-color: #0284c7;
    color: white;
    border-radius: 12px;
    padding: 2px 8px;
    font-size: 0.75rem;
    font-weight: bold;
}
.day-footer {
    text-align: center;
    border-top: 1px solid #f1f5f9;
    padding-top: 8px;
    font-weight: 700;
    font-size: 1.05rem;
    color: #1e293b;
}

/* ===== แอนิเมชัน Loading Overlay & นาฬิกาทราย ===== */
@keyframes spin-hourglass {
    0% { transform: rotate(0deg); }
    40% { transform: rotate(180deg); }
    50% { transform: rotate(180deg); }
    90% { transform: rotate(360deg); }
    100% { transform: rotate(360deg); }
}

@keyframes spin-circle {
    0% { transform: rotate(0deg); }
    100% { transform: rotate(360deg); }
}

.booking-loading-overlay {
    position: fixed;
    top: 0;
    left: 0;
    width: 100vw;
    height: 100vh;
    background: rgba(15, 23, 42, 0.72);
    backdrop-filter: blur(5px);
    z-index: 999999;
    display: flex;
    justify-content: center;
    align-items: center;
    pointer-events: all;
}

.booking-loading-card {
    background: #ffffff;
    padding: 35px 25px;
    border-radius: 20px;
    text-align: center;
    box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.4);
    max-width: 400px;
    width: 88%;
    border: 2px solid #e2e8f0;
}

.hourglass-anim {
    font-size: 58px;
    display: inline-block;
    animation: spin-hourglass 2.4s ease-in-out infinite;
    margin-bottom: 12px;
}

.loading-spinner {
    border: 4px solid #f1f5f9;
    border-top: 4px solid #0284c7;
    border-radius: 50%;
    width: 30px;
    height: 30px;
    animation: spin-circle 0.8s linear infinite;
    margin: 14px auto 0 auto;
}
</style>"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

SERVICES = ["ถอนฟัน", "อุดฟัน", "ขูดหินปูน", "ตรวจสุขภาพช่องปาก"]

# บัญชีเจ้าหน้าที่ที่ได้รับอนุญาต (Username: Password)
DEFAULT_ADMIN_CREDENTIALS = {
    "arsm": "4969",
    "kn1": "kn1",
    "dental665": "dent665"
}

def verify_admin_login(username: str, password: str) -> bool:
    if not username or not password:
        return False
    u = username.strip().lower().replace(" ", "")
    p = password.strip()
    
    if "admin_users" in st.secrets:
        try:
            sec_users = {str(k).strip().lower().replace(" ", ""): str(v).strip() for k, v in st.secrets["admin_users"].items()}
            if u in sec_users and sec_users[u] == p:
                return True
        except Exception:
            pass
            
    if u in DEFAULT_ADMIN_CREDENTIALS and DEFAULT_ADMIN_CREDENTIALS[u] == p:
        return True
        
    return False

def get_booking_source_name(username: str) -> str:
    if not username:
        return "จองผ่านระบบออนไลน์"
    u = str(username).strip().lower().replace(" ", "")
    if u == "kn1":
        return "จองผ่านประชาสัมพันธ์"
    elif u in ["dental665", "dental"]:
        return "จองผ่านคลินิกทันตกรรม"
    elif u == "arsm":
        return "จองผ่าน UMSC"
    return f"จองผ่านเจ้าหน้าที่ ({username})"

def get_user_display_role(username: str) -> str:
    if not username:
        return ""
    u = str(username).strip().lower().replace(" ", "")
    if u == "kn1":
        return "ประชาสัมพันธ์ (พี่เคนนี่)"
    elif u in ["dental665", "dental"]:
        return "คลินิกทันตกรรม"
    elif u == "arsm":
        return "UMSC (ผู้ดูแลระบบ)"
    return "เจ้าหน้าที่"

TABLE_SCHEMAS = {
    "appointments": ["id", "patient_id", "full_name", "id_card", "phone", "email", "service_type", "appointment_date", "appointment_time", "status", "token", "reminder_sent", "notes", "created_at"],
    "patients": ["id", "full_name", "id_card", "phone", "email", "created_at"],
    "daily_schedule": ["id", "schedule_date", "is_open", "start_time", "end_time", "max_patients", "note"],
    "blacklist": ["id", "patient_id", "full_name", "id_card", "phone", "reason", "no_show_count", "blacklisted_until", "created_by", "created_at"],
    "no_show_records": ["id", "appointment_id", "patient_id", "appointment_date", "status", "reported_by", "notes", "created_at"],
    "system_settings": ["setting_key", "setting_value", "updated_at"]
}

# ========== ฟังก์ชันเวลาประเทศไทย (UTC+7) ==========
def get_bangkok_now():
    return datetime.utcnow() + timedelta(hours=7)

def get_bangkok_today():
    return get_bangkok_now().date()

def clean_sheet_val(v):
    if pd.isna(v) or v is None:
        return ""
    if hasattr(v, 'item'):
        val = v.item()
        if isinstance(val, (int, float, str, bool)):
            return val
        return str(val)
    if isinstance(v, (datetime, date)):
        return str(v)
    if isinstance(v, (int, float, str, bool)):
        return v
    return str(v)

def safe_append_row(ws, row_values):
    cleaned = [clean_sheet_val(x) for x in row_values]
    return ws.append_row(cleaned)

def safe_append_rows(ws, rows_values):
    cleaned = [[clean_sheet_val(x) for x in row] for row in rows_values]
    return ws.append_rows(cleaned)

def append_appointment_mapped(ws, data_dict):
    headers = [h.strip() for h in ws.row_values(1)]
    row = [clean_sheet_val(data_dict.get(h, "")) for h in headers]
    safe_append_row(ws, row)

def normalize_thai_name(name: str) -> str:
    """ตัดคำนำหน้าและช่องว่างออกเพื่อเทียบชื่อ-นามสกุลได้อย่างแม่นยำ"""
    if not name or pd.isna(name):
        return ""
    s = str(name).strip().lower().replace(" ", "")
    prefixes = [
        "เด็กชาย", "เด็กหญิง", "ด.ช.", "ด.ญ.", "ด.ช", "ด.ญ",
        "นาย", "นางสาว", "นาง", "น.ส.", "น.ส", "คุณ"
    ]
    for p in prefixes:
        if s.startswith(p):
            s = s[len(p):]
            break
    return s

def normalize_time_slot(t_str: str) -> str:
    if not t_str or pd.isna(t_str):
        return ""
    s = str(t_str).replace("น.", "").replace("น", "").strip()
    s = s.replace(".", ":")
    parts = s.split("-")
    clean_parts = []
    for p in parts:
        p = p.strip()
        sub = p.split(":")
        if len(sub) >= 2:
            try:
                h = str(int(sub[0].strip())).zfill(2)
                m = str(int(sub[1].strip())).zfill(2)
                clean_parts.append(f"{h}:{m}")
            except Exception:
                clean_parts.append(p)
        else:
            clean_parts.append(p)
    return " - ".join(clean_parts)

def normalize_date_str(d_val) -> str:
    if d_val is None or pd.isna(d_val):
        return ""
    if isinstance(d_val, (date, datetime)):
        return d_val.strftime('%Y-%m-%d')
    
    s = str(d_val).strip()
    if not s:
        return ""
    s = s.split(" ")[0].strip()
    
    if "-" in s:
        parts = s.split("-")
        if len(parts) == 3 and len(parts[0]) == 4:
            try:
                y = int(parts[0])
                m = int(parts[1])
                d = int(parts[2])
                if y > 2400:
                    y -= 543
                return f"{y:04d}-{m:02d}-{d:02d}"
            except Exception:
                pass
                
    if "/" in s:
        parts = s.split("/")
        if len(parts) == 3:
            try:
                if len(parts[0]) == 4:
                    y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
                else:
                    d, m, y = int(parts[0]), int(parts[1]), int(parts[2])
                if y > 2400:
                    y -= 543
                return f"{y:04d}-{m:02d}-{d:02d}"
            except Exception:
                pass
                
    return s

def check_duplicate_appointment(all_appts_list, id_card, phone, full_name, appt_date_str):
    clean_id = str(id_card).strip().replace("-", "").replace(" ", "")
    clean_phone = str(phone).strip().replace("-", "").replace(" ", "")
    clean_name = normalize_thai_name(full_name)
    clean_date = normalize_date_str(appt_date_str)
    
    for row in all_appts_list:
        r_id = str(row.get('id_card', '')).strip().replace("-", "").replace(" ", "")
        r_phone = str(row.get('phone', '')).strip().replace("-", "").replace(" ", "")
        r_name = normalize_thai_name(row.get('full_name', ''))
        r_status = str(row.get('status', '')).strip().lower()
        r_date = normalize_date_str(row.get('appointment_date', ''))
        
        if r_status not in ['pending', 'confirmed', 'reconfirmed']:
            continue
            
        # ตรวจสอบเลขบัตรประชาชน
        if clean_id and r_id:
            if clean_id == r_id or (clean_id.lstrip('0') == r_id.lstrip('0') and len(clean_id) > 6):
                return True, row
                
        # ตรวจสอบเบอร์โทรศัพท์
        if clean_phone and r_phone:
            if clean_phone == r_phone or (clean_phone.lstrip('0') == r_phone.lstrip('0') and len(clean_phone) > 6):
                return True, row
                
        # ตรวจสอบชื่อ-นามสกุล ในวันเดียวกัน
        if clean_name and r_name and (clean_name == r_name):
            if clean_date == r_date:
                return True, row
                
    return False, None

def get_system_status() -> bool:
    df_settings = get_table_df("system_settings")
    if df_settings.empty or "setting_key" not in df_settings.columns:
        return True
    row = df_settings[df_settings['setting_key'].astype(str) == "booking_system_status"]
    if row.empty:
        return True
    val = str(row.iloc[0].get('setting_value', 'open')).strip().lower()
    return val != "closed"

def set_system_status(is_open: bool):
    sh = get_spreadsheet()
    if not sh:
        return False
    try:
        ws = sh.worksheet("system_settings")
    except Exception:
        ws = sh.add_worksheet(title="system_settings", rows=100, cols=5)
        safe_append_row(ws, TABLE_SCHEMAS["system_settings"])

    df_settings = get_table_df("system_settings")
    now_str = get_bangkok_now().strftime('%Y-%m-%d %H:%M:%S')
    new_val = "open" if is_open else "closed"
    
    headers = [h.strip() for h in ws.row_values(1)]
    row_idx = None
    if not df_settings.empty and "setting_key" in df_settings.columns:
        records = ws.get_all_records()
        for idx, r in enumerate(records, start=2):
            if str(r.get("setting_key", "")).strip() == "booking_system_status":
                row_idx = idx
                break
    
    if row_idx:
        v_col = headers.index("setting_value") + 1 if "setting_value" in headers else 2
        t_col = headers.index("updated_at") + 1 if "updated_at" in headers else 3
        ws.update_cell(row_idx, v_col, new_val)
        ws.update_cell(row_idx, t_col, now_str)
    else:
        safe_append_row(ws, ["booking_system_status", new_val, now_str])
        
    st.cache_data.clear()
    return True

def get_arrival_time_str(slot_label: str) -> str:
    norm = normalize_time_slot(slot_label)
    if "16:00" in norm:
        return "15.00 - 15.45 น."
    elif "17:00" in norm:
        return "16.30 - 16.45 น."
    try:
        raw_start = norm.split("-")[0].strip()
        parts = raw_start.split(":")
        h, m = int(parts[0]), int(parts[1])
        start_dt = datetime(2000, 1, 1, h, m)
        t1 = (start_dt - timedelta(minutes=30)).strftime("%H.%M")
        t2 = (start_dt - timedelta(minutes=15)).strftime("%H.%M น.")
        return f"{t1} - {t2}"
    except Exception:
        return "ก่อนเวลานัดหมาย 30 นาที"

TERMS_AND_CONDITIONS_HTML = """
<div style="background-color: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 12px; padding: 1.25rem; margin-top: 1.5rem; margin-bottom: 1rem;">
    <h4 style="color: #166534; margin-top: 0; margin-bottom: 0.75rem; font-size: 1.05rem;">🏥 เงื่อนไขและข้อตกลงการเข้ารับบริการนัดหมายออนไลน์</h4>
    <div style="font-size: 0.88rem; color: #1e293b; line-height: 1.6;">
        <p style="margin-bottom: 4px;"><b>1. การเตรียมตัวก่อนมาถึง</b></p>
        <ul style="margin-top: 0; margin-bottom: 8px; padding-left: 20px; color: #334155;">
            <li><b>การยืนยันนัด:</b> ผู้รับบริการต้อง <b>ยืนยันนัดหมายใน E-mail ที่ส่งให้ท่าน ก่อนเข้ารับบริการ 1 วัน หรือโทรยืนยันนัดหมาย เบอร์ 02 453 0526 ต่อ 302</b></li>
            <li><b>การลงทะเบียน:</b> ผู้รับบริการต้องมาติดต่อที่ห้องเวชระเบียน เพื่อตรวจสอบสิทธิ์และทำประวัติ <b style="color: #dc2626;">รอบเวลา 16.00 - 17.00 น. ลงทะเบียนเวลา 15.00 น. / รอบเวลา 17.00 - 18.00 น. ลงทะเบียนเวลา 16.30 น.</b></li>
            <li><b>เอกสารที่ต้องเตรียม:</b> โปรดนำ <b>บัตรประจำตัวประชาชนตัวจริง</b> มาแสดงทุกครั้งที่เข้ารับบริการ</li>
            <li><b>ผู้รับบริการอายุต่ำกว่า 18 ปี:</b> <b style="color: #dc2626;">ผู้ที่มีอายุต่ำกว่า 18 ปี บริบูรณ์ ต้องมีผู้ปกครองมาด้วยทุกครั้ง</b></li>
            <li><b>ประวัติสุขภาพ:</b> หากมีโรคประจำตัว โปรดนำยาทั้งหมดมาด้วย หากแพ้ยา โปรดนำบัตรแพ้ยามาด้วย</li>
        </ul>
        <p style="margin-bottom: 4px;"><b>2. ข้อกำหนดเรื่องเวลาและการรักษาคิว</b></p>
        <ul style="margin-top: 0; margin-bottom: 8px; padding-left: 20px; color: #334155;">
            <li><b>การมาสาย:</b>
                <ul style="margin-top: 2px; margin-bottom: 4px; padding-left: 18px;">
                    <li><b style="color: #dc2626;">รอบเวลา 16.00 - 17.00 น. ท่านต้องมาติดต่อห้องเวชระเบียน 15.00 - 15.45 น.</b></li>
                    <li><b style="color: #dc2626;">รอบเวลา 17.00 - 18.00 น. ท่านต้องมาติดต่อห้องเวชระเบียน 16.30 - 16.45 น.</b></li>
                </ul>
                <b style="color: #dc2626;">หากเกินเวลาดังกล่าว ทางศูนย์ขอสงวนสิทธิ์ในการ ยกเลิกนัดหมาย ของท่านทันที เพื่อไม่ให้กระทบต่อคิวถัดไป</b>
            </li>
            <li><b>การลงทะเบียนนัดหมาย :</b> ระบบจำกัดสิทธิ์ <b>1 ชื่อ ต่อ 1 คิวนัดหมาย</b> เท่านั้น</li>
        </ul>
        <p style="margin-bottom: 4px;"><b>3. การยกเลิกหรือเลื่อนนัด</b></p>
        <ul style="margin-top: 0; margin-bottom: 0; padding-left: 20px; color: #334155;">
            <li><b>การแจ้งยกเลิก:</b> หากไม่สามารถมาตามนัดได้ ท่านสามารถกดยกเลิกผ่านอีเมลได้ตลอดเวลา หรือโทรแจ้งล่วงหน้า 1 วันทำการ โทร. <b>02 453 0526 ต่อ 302</b> <b style="color: #dc2626;">หากท่านไม่แจ้งยกเลิกนัดหมาย ท่านจะไม่สามารถจองผ่านระบบออนไลน์ได้ ในครั้งถัดไป</b></li>
        </ul>
    </div>
</div>
"""

@st.cache_resource
def init_pdf_fonts():
    font_reg = "Sarabun-Regular.ttf"
    font_bold = "Sarabun-Bold.ttf"
    if not os.path.exists(font_reg):
        try:
            urllib.request.urlretrieve("https://raw.githubusercontent.com/google/fonts/main/ofl/sarabun/Sarabun-Regular.ttf", font_reg)
        except Exception:
            pass
    if not os.path.exists(font_bold):
        try:
            urllib.request.urlretrieve("https://raw.githubusercontent.com/google/fonts/main/ofl/sarabun/Sarabun-Bold.ttf", font_bold)
        except Exception:
            pass

    has_font = False
    if os.path.exists(font_reg):
        try:
            pdfmetrics.registerFont(TTFont('Sarabun', font_reg))
            has_font = True
        except Exception:
            pass
    if os.path.exists(font_bold):
        try:
            pdfmetrics.registerFont(TTFont('Sarabun-Bold', font_bold))
            has_font = True
        except Exception:
            pass
    return has_font

def generate_daily_appointments_pdf(df_day: pd.DataFrame, target_date: date) -> bytes:
    has_font = init_pdf_fonts()
    font_name = 'Sarabun' if has_font else 'Helvetica'
    font_bold = 'Sarabun-Bold' if has_font else 'Helvetica-Bold'
    
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=30,
        leftMargin=30,
        topMargin=30,
        bottomMargin=30
    )
    
    elements = []
    style_title = ParagraphStyle('TitleStyle', fontName=font_bold, fontSize=15, leading=19, alignment=1, textColor=colors.HexColor('#0369a1'))
    style_sub = ParagraphStyle('SubTitleStyle', fontName=font_bold, fontSize=12, leading=16, alignment=1, textColor=colors.HexColor('#1e293b'))
    style_meta = ParagraphStyle('MetaStyle', fontName=font_name, fontSize=9, leading=12, alignment=2, textColor=colors.HexColor('#64748b'))
    style_th = ParagraphStyle('THStyle', fontName=font_bold, fontSize=9, leading=11, alignment=1, textColor=colors.white)
    style_td = ParagraphStyle('TDStyle', fontName=font_name, fontSize=8.5, leading=11, alignment=0, textColor=colors.HexColor('#1e293b'))
    style_td_center = ParagraphStyle('TDCenterStyle', fontName=font_name, fontSize=8.5, leading=11, alignment=1, textColor=colors.HexColor('#1e293b'))
    
    d_be = target_date.strftime('%d/%m/') + str(target_date.year + 543)
    now_be = get_bangkok_now().strftime('%d/%m/') + str(get_bangkok_now().year + 543) + get_bangkok_now().strftime(' %H:%M น.')
    
    elements.append(Paragraph("ศูนย์บริการสาธารณสุข 65 รักษาศุข บางบอน", style_title))
    elements.append(Spacer(1, 3))
    elements.append(Paragraph(f"ใบรายชื่อผู้เข้ารับบริการทันตกรรม ประจำวันที่ {d_be}", style_sub))
    elements.append(Spacer(1, 3))
    elements.append(Paragraph(f"พิมพ์รายงานเมื่อ: {now_be} | รวมทั้งหมด: {len(df_day)} ท่าน", style_meta))
    elements.append(Spacer(1, 8))
    
    headers = [
        Paragraph("<b>ลำดับ</b>", style_th),
        Paragraph("<b>เวลารักษา</b>", style_th),
        Paragraph("<b>เวลาเวชระเบียน</b>", style_th),
        Paragraph("<b>ชื่อ - นามสกุล</b>", style_th),
        Paragraph("<b>บริการ</b>", style_th),
        Paragraph("<b>เบอร์โทรศัพท์</b>", style_th),
        Paragraph("<b>สถานะการยืนยัน</b>", style_th),
        Paragraph("<b>หมายเหตุ / ลงชื่อรับบริการ</b>", style_th),
    ]
    
    table_data = [headers]
    status_map = {
        'reconfirmed': '🟢 ยืนยันแล้ว (มาแน่นอน)',
        'confirmed': '🟡 ลงทะเบียนสำเร็จ',
        'pending': '⚪ รอยืนยัน',
        'completed': '✅ รับบริการแล้ว',
        'no_show': '🔴 ไม่มาตามนัด',
        'cancelled': '❌ ยกเลิก'
    }
    
    for idx, (_, row) in enumerate(df_day.iterrows(), start=1):
        appt_time = str(row.get('appointment_time', ''))
        arrival_t = get_arrival_time_str(appt_time)
        full_name = str(row.get('full_name', ''))
        service = str(row.get('service_type', ''))
        phone = str(row.get('phone', ''))
        raw_stat = str(row.get('status', ''))
        stat_th = status_map.get(raw_stat, raw_stat)
        notes = str(row.get('notes', '')) if row.get('notes') and str(row.get('notes')).strip() != 'None' else ''
        
        table_data.append([
            Paragraph(str(idx), style_td_center),
            Paragraph(appt_time, style_td_center),
            Paragraph(arrival_t, style_td_center),
            Paragraph(full_name, style_td),
            Paragraph(service, style_td),
            Paragraph(phone, style_td_center),
            Paragraph(stat_th, style_td_center),
            Paragraph(notes, style_td),
        ])
        
    col_widths = [35, 75, 80, 160, 100, 85, 110, 135]
    table = Table(table_data, colWidths=col_widths, repeatRows=1)
    
    t_style = [
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0284c7')),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e1')),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]
    
    for r_idx in range(1, len(table_data)):
        if r_idx % 2 == 0:
            t_style.append(('BACKGROUND', (0, r_idx), (-1, r_idx), colors.HexColor('#f8fafc')))
        else:
            t_style.append(('BACKGROUND', (0, r_idx), (-1, r_idx), colors.white))
            
    table.setStyle(TableStyle(t_style))
    elements.append(table)
    
    doc.build(elements)
    buffer.seek(0)
    return buffer.getvalue()

@st.cache_resource
def get_gspread_client():
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    if "gcp_service_account" in st.secrets:
        creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scopes)
        return gspread.authorize(creds)
    return None

@st.cache_resource
def get_spreadsheet():
    gc = get_gspread_client()
    if not gc:
        return None
    sheet_url = st.secrets.get("sheets", {}).get("spreadsheet_url")
    if not sheet_url:
        return None
    try:
        sh = gc.open_by_url(sheet_url)
        return sh
    except Exception as e:
        st.error(f"❌ ไม่สามารถเปิด Google Sheet ได้: {e}")
        return None

@st.cache_resource
def ensure_worksheets_initialized(_sh):
    try:
        existing = [ws.title for ws in _sh.worksheets()]
        for title, headers in TABLE_SCHEMAS.items():
            if title not in existing:
                ws = _sh.add_worksheet(title=title, rows=1000, cols=len(headers) + 2)
                safe_append_row(ws, headers)
    except Exception:
        pass

@st.cache_data(ttl=6, show_spinner=False)
def get_table_df(table_name):
    sh = get_spreadsheet()
    if not sh:
        return pd.DataFrame(columns=TABLE_SCHEMAS.get(table_name, []))
    try:
        ws = sh.worksheet(table_name)
        all_vals = ws.get_all_values()
        if not all_vals or len(all_vals) <= 1:
            return pd.DataFrame(columns=TABLE_SCHEMAS.get(table_name, []))
        
        headers = [h.strip() for h in all_vals[0]]
        data = all_vals[1:]
        
        norm_data = []
        for row in data:
            if len(row) < len(headers):
                row = row + [""] * (len(headers) - len(row))
            elif len(row) > len(headers):
                row = row[:len(headers)]
            norm_data.append(row)
            
        df = pd.DataFrame(norm_data, columns=headers)
        df = df.loc[:, [c for c in df.columns if c != ""]]
        
        expected = TABLE_SCHEMAS.get(table_name, [])
        for col in expected:
            if col not in df.columns:
                df[col] = None
        
        if not df.empty:
            if 'id' in df.columns:
                df = df[df['id'].astype(str).str.strip() != ""]
            elif 'setting_key' in df.columns:
                df = df[df['setting_key'].astype(str).str.strip() != ""]
        return df
    except Exception as e:
        st.error(f"⚠️ เกิดข้อผิดพลาดในการโหลดตาราง {table_name}: {e}")
        return pd.DataFrame(columns=TABLE_SCHEMAS.get(table_name, []))

def send_email(to_email: str, subject: str, body: str, cc_email: str = "*********@*****.***") -> bool:
    if not to_email or not str(to_email).strip():
        return False
    try:
        sender_email = st.secrets["email"]["sender"]
        sender_password = st.secrets["email"]["password"]
        
        msg = MIMEMultipart()
        msg['From'] = f"คลินิกทันตกรรม ศบส.65 <{sender_email}>"
        msg['To'] = to_email.strip()
        msg['Cc'] = cc_email
        msg['Reply-To'] = "*********@*****.***"
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'html'))
        
        recipients = [to_email.strip()]
        if cc_email and cc_email != to_email.strip():
            recipients.append(cc_email)
        
        with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=10) as server:
            server.login(sender_email, sender_password)
            server.send_message(msg, to_addrs=recipients)
        return True
    except KeyError:
        st.warning("⚠️ ไม่พบคีย์การตั้งค่าอีเมลใน .streamlit/secrets.toml")
        return False
    except Exception as e:
        st.error(f"❌ ส่งอีเมลล้มเหลว: {str(e)}")
        return False

def send_booking_confirmation_email(to_email: str, full_name: str, service_name: str, appointment_date_str: str, norm_time_label: str, arrival_time_str: str, token: str) -> bool:
    if not to_email or not str(to_email).strip():
        return False
    base_url = "https://dental-booking-s7ybkcswqp4qkxg2am8dvl.streamlit.app"
    cancel_url = f"{base_url}/?cancel={token}"

    email_body = f"""
    <div style="font-family: Arial, sans-serif; line-height: 1.6; max-width: 620px; margin: auto; padding: 24px; border: 1px solid #e2e8f0; border-radius: 14px;">
        <div style="background: linear-gradient(135deg, #0284c7 0%, #0369a1 100%); padding: 20px; border-radius: 10px; text-align: center; color: white;">
            <h2 style="margin:0; font-size: 22px; font-weight: 800;">ท่านได้ลงทะเบียนนัดหมายบริการทันตกรรมสำเร็จ</h2>
            <p style="margin:6px 0 0 0; font-size: 15px; opacity: 0.95;">ศูนย์บริการสาธารณสุข 65 รักษาศุข บางบอน</p>
        </div>
        
        <p style="margin-top: 25px; font-size: 16px; color: #1e293b;">เรียนคุณ <b>{full_name}</b>,</p>
        <p style="font-size: 15px; color: #334155; margin-bottom: 20px;">ระบบได้รับการลงทะเบียนนัดหมายบริการทันตกรรมของท่านเรียบร้อยแล้ว โดยมีรายละเอียดการนัดหมายดังนี้:</p>
        
        <div style="background-color: #ffffff; border: 2.5px solid #0284c7; border-radius: 14px; padding: 22px; margin: 20px 0; box-shadow: 0 4px 12px rgba(2, 132, 199, 0.08);">
            <div style="border-bottom: 1.5px solid #e2e8f0; padding-bottom: 12px; margin-bottom: 12px;">
                <span style="font-size: 14px; color: #64748b; font-weight: bold; text-transform: uppercase;">บริการที่นัดหมาย</span><br>
                <span style="font-size: 24px; color: #0f172a; font-weight: 800;">🦷 {service_name}</span>
            </div>
            
            <div style="display: flex; justify-content: space-between; border-bottom: 1.5px solid #e2e8f0; padding-bottom: 12px; margin-bottom: 15px;">
                <div style="width: 50%;">
                    <span style="font-size: 14px; color: #64748b; font-weight: bold;">วันที่เข้ารับบริการ</span><br>
                    <span style="font-size: 22px; color: #0284c7; font-weight: 800;">📅 {appointment_date_str}</span>
                </div>
                <div style="width: 50%;">
                    <span style="font-size: 14px; color: #64748b; font-weight: bold;">ช่วงเวลาเข้ารับการรักษา</span><br>
                    <span style="font-size: 22px; color: #0f172a; font-weight: 800;">⏰ {norm_time_label} น.</span>
                </div>
            </div>

            <div style="background-color: #fef2f2; border: 2.5px dashed #ef4444; border-radius: 12px; padding: 18px 12px; text-align: center; margin-top: 10px;">
                <span style="font-size: 16px; color: #991b1b; font-weight: 800; letter-spacing: 0.5px;">🏥 เวลาที่ต้องมาติดต่อห้องเวชระเบียน</span><br>
                <span style="font-size: 38px; color: #dc2626; font-weight: 900; line-height: 1.3; display: block; margin: 4px 0;">{arrival_time_str}</span>
                <span style="font-size: 13px; color: #b91c1c; font-weight: 600;">(ต้องมาติดต่อในเวลาดังกล่าวเพื่อทำประวัติและตรวจสิทธิ์ หากเกินเวลาขอยกเลิกนัดทันที)</span>
            </div>
        </div>

        <div style="text-align: center; background-color: #fffbeb; border: 2px solid #fde68a; border-radius: 12px; padding: 16px 20px; margin: 22px 0;">
            <p style="margin: 0; color: #b45309; font-weight: 800; font-size: 16px;">
                ⏰ 1 วันก่อนถึงวันนัดหมาย ให้ท่านตรวจสอบ E-mail อีกครั้ง เพื่อกดยืนยันการเข้ารับบริการ
            </p>
        </div>

        <div style="text-align: center; margin: 30px 0 25px 0;">
            <p style="font-size: 14px; color: #64748b; margin-bottom: 12px;">หากท่านไม่สะดวกเข้ารับบริการตามวันเวลาดังกล่าว:</p>
            <a href="{cancel_url}" style="background-color: #dc2626; color: white !important; padding: 14px 34px; text-decoration: none; border-radius: 10px; font-weight: 800; font-size: 15px; display: inline-block; box-shadow: 0 4px 10px rgba(220, 38, 38, 0.3);">
                ❌ กดยกเลิกการนัดหมาย
            </a>
        </div>

        {TERMS_AND_CONDITIONS_HTML}

        <hr style="border: 0; border-top: 1px solid #e2e8f0; margin: 25px 0 12px 0;">
        <p style="font-size: 12px; color: #94a3b8; text-align: center; margin: 0;">ศูนย์บริการสาธารณสุข 65 รักษาศุข บางบอน | โทร. 02 453 0526 ต่อ 302</p>
    </div>
    """
    return send_email(to_email.strip(), "ท่านได้ลงทะเบียนนัดหมายบริการทันตกรรมสำเร็จ", email_body)

def check_blacklist(id_card=None, phone=None, patient_id=None):
    df_bl = get_table_df("blacklist")
    if df_bl.empty:
        return None
    today_str = get_bangkok_today().strftime('%Y-%m-%d')
    active_bl = df_bl[df_bl['blacklisted_until'].astype(str) >= today_str]
    
    if id_card:
        match = active_bl[active_bl['id_card'].astype(str) == str(id_card).strip()]
        if not match.empty:
            return match.iloc[0].to_dict()
    if phone:
        match = active_bl[active_bl['phone'].astype(str) == str(phone).strip()]
        if not match.empty:
            return match.iloc[0].to_dict()
    if patient_id:
        match = active_bl[active_bl['patient_id'].astype(str) == str(patient_id)]
        if not match.empty:
            return match.iloc[0].to_dict()
    return None

def add_to_blacklist(patient_id, reason, days_penalty=30, reported_by="system"):
    sh = get_spreadsheet()
    df_p = get_table_df("patients")
    match_p = df_p[df_p['id'].astype(str) == str(patient_id)]
    if match_p.empty:
        return False
    p_info = match_p.iloc[0]
    
    ws_bl = sh.worksheet("blacklist")
    df_bl = get_table_df("blacklist")
    existing = check_blacklist(patient_id=patient_id)
    
    until_d = (get_bangkok_today() + timedelta(days=days_penalty)).strftime('%Y-%m-%d')
    now_str = get_bangkok_now().strftime('%Y-%m-%d %H:%M:%S')
    
    if existing:
        new_cnt = int(existing.get('no_show_count', 1)) + 1
        all_rows = ws_bl.get_all_values()
        headers = [h.strip().lower() for h in all_rows[0]]
        p_id_col = headers.index("patient_id")
        for idx, row in enumerate(all_rows[1:], start=2):
            if row[p_id_col].strip() == str(patient_id).strip():
                ws_bl.update_cell(idx, headers.index('no_show_count') + 1, new_cnt)
                ws_bl.update_cell(idx, headers.index('blacklisted_until') + 1, until_d)
                ws_bl.update_cell(idx, headers.index('reason') + 1, f"{reason} (ครั้งที่ {new_cnt})")
                break
    else:
        next_id = int(pd.to_numeric(df_bl['id'], errors='coerce').fillna(0).max()) + 1 if not df_bl.empty else 1
        safe_append_row(ws_bl, [
            next_id, int(patient_id), str(p_info.get('full_name')), str(p_info.get('id_card')), 
            str(p_info.get('phone')), reason, 1, until_d, reported_by, now_str
        ])
    st.cache_data.clear()
    return True

def record_no_show(appointment_id, reported_by="system", notes=""):
    sh = get_spreadsheet()
    df_appts = get_table_df("appointments")
    match_a = df_appts[df_appts['id'].astype(str) == str(appointment_id)]
    if match_a.empty:
        return False
    
    a_info = match_a.iloc[0]
    patient_id = a_info.get('patient_id')
    appt_date = a_info.get('appointment_date')
    
    ws_a = sh.worksheet("appointments")
    all_rows = ws_a.get_all_values()
    headers = [h.strip().lower() for h in all_rows[0]]
    id_col = headers.index("id")
    status_col = headers.index("status") + 1
    
    for idx, row in enumerate(all_rows[1:], start=2):
        if row[id_col].strip() == str(appointment_id).strip():
            ws_a.update_cell(idx, status_col, 'no_show')
            break
            
    ws_ns = sh.worksheet("no_show_records")
    df_ns = get_table_df("no_show_records")
    next_id = int(pd.to_numeric(df_ns['id'], errors='coerce').fillna(0).max()) + 1 if not df_ns.empty else 1
    now_str = get_bangkok_now().strftime('%Y-%m-%d %H:%M:%S')
    safe_append_row(ws_ns, [next_id, int(appointment_id), int(patient_id), appt_date, 'no_show', reported_by, notes, now_str])
    st.cache_data.clear()
    
    cutoff_date = (get_bangkok_today() - timedelta(days=90)).strftime('%Y-%m-%d')
    df_ns_all = get_table_df("no_show_records")
    patient_no_shows = df_ns_all[
        (df_ns_all['patient_id'].astype(str) == str(patient_id)) &
        (df_ns_all['appointment_date'].astype(str) >= cutoff_date) &
        (df_ns_all['status'] == 'no_show')
    ]
    if len(patient_no_shows) >= 2:
        add_to_blacklist(
            patient_id, 
            f"ไม่มาตามนัด {len(patient_no_shows)} ครั้งในรอบ 90 วัน", 
            days_penalty=30 * len(patient_no_shows), 
            reported_by="auto_system"
        )
    return True

def batch_update_appointments_status(selected_ids, target_status, reported_by="admin"):
    sh = get_spreadsheet()
    if not sh or not selected_ids:
        return 0
    ws_a = sh.worksheet("appointments")
    all_rows = ws_a.get_all_values()
    headers = [h.strip().lower() for h in all_rows[0]]
    id_col = headers.index("id")
    status_col = headers.index("status") + 1
    
    count = 0
    for appt_id in selected_ids:
        if target_status == "no_show":
            record_no_show(appt_id, reported_by=reported_by, notes="เจ้าหน้าที่ระบุไม่มาตามนัด (แบบกลุ่ม)")
            count += 1
        else:
            for idx, row in enumerate(all_rows[1:], start=2):
                if row[id_col].strip() == str(appt_id).strip():
                    ws_a.update_cell(idx, status_col, target_status)
                    count += 1
                    break
    st.cache_data.clear()
    return count

def get_available_slots(appointment_date: date, allow_admin_override: bool = False):
    today_dt = get_bangkok_today()
    now_dt = get_bangkok_now()
    
    if not allow_admin_override and appointment_date == today_dt and now_dt.time() >= time(15, 30):
        return [], "หมดเวลาจองนัดหมายรับบริการแล้ว"

    date_str = appointment_date.strftime('%Y-%m-%d')
    df_sched = get_table_df("daily_schedule")
    if df_sched.empty:
        return [], "คลินิกยังไม่ได้เปิดรับจองในวันนี้"
        
    df_sched['norm_date'] = df_sched['schedule_date'].apply(normalize_date_str)
    day_sched = df_sched[df_sched['norm_date'] == date_str]
    if day_sched.empty:
        return [], "คลินิกยังไม่ได้เปิดรับจองในวันนี้"
        
    if (day_sched['is_open'].astype(int) == 0).any():
        close_row = day_sched[day_sched['is_open'].astype(int) == 0].iloc[0]
        note = close_row.get('note', 'ปิดทำการพิเศษ')
        return [], f"ปิดทำการ ({note})"
        
    df_appts = get_table_df("appointments")
    if not df_appts.empty:
        df_appts['norm_date'] = df_appts['appointment_date'].apply(normalize_date_str)
        df_appts['norm_time'] = df_appts['appointment_time'].apply(normalize_time_slot)
        df_appts['norm_status'] = df_appts['status'].astype(str).str.strip().str.lower()
        
        active_appts = df_appts[
            (df_appts['norm_date'] == date_str) & 
            (df_appts['norm_status'].isin(['pending', 'confirmed', 'reconfirmed', 'completed'])) &
            (df_appts['id_card'].astype(str).str.strip() != "")
        ]
    else:
        active_appts = pd.DataFrame(columns=['norm_time'])
    
    all_slots = []
    day_sched_sorted = day_sched.sort_values(by=['start_time'])
    for _, row in day_sched_sorted.iterrows():
        s_t = str(row.get('start_time', '')).strip()
        e_t = str(row.get('end_time', '')).strip()
        if not s_t or not e_t:
            continue
        raw_slot_label = f"{s_t} - {e_t}"
        norm_slot = normalize_time_slot(raw_slot_label)
        max_cap = int(row.get('max_patients', 4))
        
        booked_count = len(active_appts[active_appts['norm_time'] == norm_slot]) if not active_appts.empty else 0
        
        if booked_count < max_cap:
            all_slots.append({
                'label': norm_slot,
                'available': max(0, max_cap - booked_count),
                'total_slots': max_cap
            })
            
    if not all_slots:
        return [], "คิวนัดหมายเต็มแล้ว"
        
    return all_slots, "เปิดทำการ"

def show_booking_form():
    is_sys_open = get_system_status()
    is_admin_logged_in = bool(st.session_state.get("admin_user"))

    if not is_sys_open:
        if not is_admin_logged_in:
            st.markdown("""
            <div style="text-align: center; padding: 50px 25px; background: #fff7ed; border: 3px solid #fdba74; border-radius: 20px; margin: 30px auto; max-width: 720px; box-shadow: 0 15px 30px -5px rgba(234, 88, 12, 0.15);">
                <div style="font-size: 70px; margin-bottom: 15px;">🛠️</div>
                <h1 style="color: #9a3412; font-size: 28px; margin: 0 0 15px 0; font-weight: 800; line-height: 1.4;">
                    ระบบอยู่ระหว่างปรับปรุงการให้บริการ
                </h1>
                <p style="font-size: 16px; color: #475569; line-height: 1.8; margin: 0 0 25px 0;">
                    ศูนย์บริการสาธารณสุข 65 รักษาศุข บางบอน ขออภัยในความไม่สะดวก<br>
                    ขณะนี้ระบบจองคิวออนไลน์กำลังปิดปรับปรุงชั่วคราว เพื่อพัฒนาระบบการให้บริการ<br>
                    เจ้าหน้าที่จะเปิดให้ทำการนัดหมายออนไลน์อีกครั้งเมื่อปรับปรุงเสร็จสิ้น
                </p>
                <div style="background-color: #ffffff; border: 2px dashed #f97316; padding: 14px 26px; border-radius: 12px; display: inline-block;">
                    📞 ติดต่อสอบถาม / นัดหมายโดยตรง โทร. <b style="color: #c2410c; font-size: 17px;">02 453 0526 ต่อ 302</b>
                </div>
            </div>
            """, unsafe_allow_html=True)

            with st.expander("🔑 เข้าสู่ระบบสำหรับเจ้าหน้าที่ (เพื่อทดสอบระบบขณะปิดปรับปรุง)"):
                test_username = st.text_input("ชื่อผู้ใช้งาน (Username)", key="test_admin_user")
                test_pwd = st.text_input("รหัสผ่าน", type="password", key="test_admin_pwd")
                if st.button("🔓 เข้าสู่โหมดทดสอบการจอง", type="primary", use_container_width=True):
                    if verify_admin_login(test_username, test_pwd):
                        st.session_state.admin_user = test_username.strip().lower().replace(" ", "")
                        st.session_state.is_admin = True
                        st.success("เข้าสู่โหมดทดสอบสำเร็จ!")
                        st.rerun()
                    else:
                        st.error("❌ ชื่อผู้ใช้งานหรือรหัสผ่านไม่ถูกต้อง")
            return
        else:
            st.markdown(f"""
            <div style="background-color: #fef2f2; border: 2px solid #ef4444; border-radius: 12px; padding: 14px 20px; margin-bottom: 20px;">
                <span style="font-size: 1.1rem; color: #b91c1c; font-weight: bold;">
                    🛠️️ ขณะนี้ระบบปิดปรับปรุงอยู่ (บุคคลภายนอกไม่สามารถเข้าใช้งานได้)
                </span><br>
                <span style="font-size: 0.92rem; color: #7f1d1d;">
                    คุณเข้าสู่ระบบในฐานะเจ้าหน้าที่ <b>{st.session_state.admin_user}</b> สามารถทดสอบการลงทะเบียนจองและระบบอีเมลได้ตามปกติ
                </span>
            </div>
            """, unsafe_allow_html=True)

    if "just_booked_data" in st.session_state and st.session_state.just_booked_data:
        bk = st.session_state.just_booked_data
        st.markdown(f"""
        <div style="background: linear-gradient(135deg, #0284c7 0%, #0369a1 100%); color: white; padding: 35px 25px; border-radius: 18px; border: 3px solid #38bdf8; box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.2); margin: 20px 0; text-align: center;">
            <div style="font-size: 60px; margin-bottom: 10px;">🎉</div>
            <h1 style="color: #fef08a; font-size: 26px; margin: 0 0 15px 0; font-weight: 800; line-height: 1.4;">
                ระบบได้รับการลงทะเบียน<br>นัดหมายบริการทันตกรรมของท่านแล้ว
            </h1>
            <p style="font-size: 18px; margin: 10px 0; font-weight: 500;">
                หากท่านลงทะเบียนสำเร็จ ท่านจะได้รับ E-mail ยืนยันการลงทะเบียน
            </p>
            <div style="font-size: 20px; font-weight: 900; color: #ffffff; background: rgba(0,0,0,0.25); border: 2px dashed #fde047; padding: 14px 24px; border-radius: 12px; display: inline-block; margin: 15px 0;">
                📩 โปรดตรวจสอบ E-mail เพื่อตรวจสอบการจองของท่าน
            </div>
            <div style="background-color: #ffffff; color: #1e293b; border-radius: 12px; padding: 16px; margin: 20px auto; max-width: 500px; text-align: left;">
                <p style="margin: 4px 0;"><b>ชื่อผู้รับบริการ:</b> {bk['full_name']}</p>
                <p style="margin: 4px 0;"><b>บริการ:</b> {bk['service_type']}</p>
                <p style="margin: 4px 0;"><b>วันที่นัดหมาย:</b> {bk['appointment_date']}</p>
                <p style="margin: 4px 0;"><b>ช่วงเวลารักษา:</b> {bk['appointment_time']} น.</p>
                <p style="margin: 4px 0; color: #dc2626;"><b>เวลาติดต่อห้องเวชระเบียน:</b> <b>{bk['arrival_time']}</b></p>
            </div>
            <p style="font-size: 14px; opacity: 0.95; margin: 10px 0 0 0;">
                ระบบได้ส่งรายละเอียดไปยังอีเมล: <b>{bk['email']}</b> เรียบร้อยแล้ว (หากไม่พบโปรดดูในกล่องขยะ/Spam)
            </p>
        </div>
        """, unsafe_allow_html=True)
        
        if st.button("🏠 เสร็จสิ้น / จองรายการอื่นเพิ่มเติม", type="primary", use_container_width=True):
            st.session_state.just_booked_data = None
            st.rerun()
        return

    # -------------------------------------------------------------
    # ⏳ จัดการสถานะ Loading ระหว่างบันทึกข้อมูล
    # -------------------------------------------------------------
    if "is_submitting" not in st.session_state:
        st.session_state.is_submitting = False

    if st.session_state.is_submitting:
        st.markdown("""
        <div class="booking-loading-overlay">
            <div class="booking-loading-card">
                <div class="hourglass-anim">⏳</div>
                <h3 style="color: #0369a1; margin: 0 0 6px 0; font-size: 20px; font-weight: 800;">โปรดรอสักครู่</h3>
                <h4 style="color: #1e293b; margin: 0 0 10px 0; font-size: 16px; font-weight: 700;">ระบบกำลังบันทึกข้อมูลการนัดหมาย</h4>
                <p style="color: #64748b; font-size: 14px; margin: 0; line-height: 1.5;">
                    กำลังออกใบนัดและส่งข้อมูลยืนยัน<br>
                    <b style="color: #dc2626;">กรุณาอย่าปิดหน้าจอหรือกดย้อนกลับ</b>
                </p>
                <div class="loading-spinner"></div>
            </div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("""<div class="hero-banner">
        <h1>🦷 ระบบจองคิวทันตกรรม</h1>
        <p>ศูนย์บริการสาธารณสุข 65 รักษาศุข บางบอน<br>กรุณากรอกข้อมูลส่วนตัว เลือกบริการ และนัดหมายวันเวลาที่สะดวกเข้ารับบริการ</p>
    </div>""", unsafe_allow_html=True)
    
    with st.container(border=True):
        st.subheader("1. ข้อมูลผู้เข้ารับบริการ")
        col1, col2 = st.columns(2)
        with col1:
            full_name = st.text_input("ชื่อ - นามสกุล *", placeholder="ระบุชื่อและนามสกุลจริง", disabled=st.session_state.is_submitting)
            id_card = st.text_input("เลขประจำตัวประชาชน (13 หลัก) *", placeholder="xxxxxxxxxxxxx", max_chars=13, disabled=st.session_state.is_submitting)
        with col2:
            phone = st.text_input("เบอร์โทรศัพท์ติดต่อ *", placeholder="08xxxxxxxx", max_chars=10, disabled=st.session_state.is_submitting)
            email = st.text_input("อีเมลสำหรับรับการยืนยัน *", placeholder="****_*****@*******.***", disabled=st.session_state.is_submitting)
            
        st.markdown("<br>", unsafe_allow_html=True)
        st.subheader("2. เลือกบริการและวันเวลา")
        
        service_name = st.selectbox("บริการที่ต้องการรับการรักษา *", SERVICES, disabled=st.session_state.is_submitting)
        
        today_bkk = get_bangkok_today()
        now_bkk = get_bangkok_now()
        is_past_today_cutoff = now_bkk.time() >= time(15, 30)

        col_date, col_slot = st.columns(2)
        with col_date:
            min_date = today_bkk
            max_date = min_date + timedelta(days=120)
            default_date = today_bkk + timedelta(days=1) if is_past_today_cutoff else today_bkk
            appointment_date = st.date_input("เลือกวันที่ต้องการนัดหมาย *", min_value=min_date, max_value=max_date, value=default_date, disabled=st.session_state.is_submitting)
            
            if appointment_date == today_bkk and is_past_today_cutoff:
                st.warning("⛔ ปิดรับจองออนไลน์สำหรับวันนี้แล้วตั้งแต่เวลา 15.30 น. เพื่อสรุปยอดคิว โปรดจองวันรับบริการวันถัดไป")
            
        available_slots, status_msg = get_available_slots(appointment_date, allow_admin_override=False)
        
        with col_slot:
            if not available_slots:
                st.selectbox("ช่วงเวลา *", [f"⛔ {status_msg}"], disabled=True)
                selected_time_slot = None
            else:
                slot_options = [f"{slot['label']} น. (ว่าง {slot['available']}/{slot['total_slots']} คิว)" for slot in available_slots]
                selected_time_slot = st.selectbox("เลือกช่วงเวลานัดหมาย *", slot_options, disabled=st.session_state.is_submitting)

        notes = st.text_area("หมายเหตุเพิ่มเติม / อาการเบื้องต้น / โรคประจำตัว (ถ้ามี)", disabled=st.session_state.is_submitting)
        st.markdown(TERMS_AND_CONDITIONS_HTML, unsafe_allow_html=True)
        agree_terms = st.checkbox("ข้าพเจ้าได้อ่านและยอมรับเงื่อนไขและข้อตกลงการเข้ารับบริการนัดหมายออนไลน์ข้างต้น *", disabled=st.session_state.is_submitting)
        st.markdown("<br>", unsafe_allow_html=True)
        
        submitted = st.button("📅 ยืนยันข้อมูลและส่งคำขอจองคิว", type="primary", use_container_width=True, disabled=st.session_state.is_submitting)

    if submitted:
        # ตรวจสอบความถูกต้องของข้อมูล
        if not agree_terms:
            st.error("❌ กรุณาทำเครื่องหมายถูกเพื่อยอมรับเงื่อนไขและข้อตกลงก่อนส่งคำขอจองคิว")
            return

        if not available_slots or not selected_time_slot:
            st.error(f"❌ วันที่เลือกไม่สามารถจองได้: {status_msg}")
            return
            
        if not all([full_name.strip(), id_card.strip(), phone.strip(), email.strip()]):
            st.error("❌ กรุณากรอกข้อมูลที่มีเครื่องหมาย * ให้ครบทุกช่อง")
            return
            
        if len(id_card) != 13 or not id_card.isdigit():
            st.error("❌ เลขประจำตัวประชาชนต้องเป็นตัวเลข 13 หลักเท่านั้น")
            return

        if check_blacklist(id_card=id_card.strip()):
            st.error("⚠️ บัญชีนี้ถูกระงับสิทธิ์ชั่วคราวเนื่องจากไม่มาตามเวลานัดหมาย กรุณาติดต่อคลินิก")
            return
        if check_blacklist(phone=phone.strip()):
            st.error("⚠️ เบอร์โทรศัพท์นี้ถูกระงับสิทธิ์ชั่วคราว กรุณาติดต่อคลินิก")
            return

        # -------------------------------------------------------------
        # ⏳ ล็อกหน้าจอทันทีด้วยการสลับสถานะ submitting เป็น True แล้วสั่ง Rerun
        # -------------------------------------------------------------
        st.session_state.is_submitting = True
        st.rerun()

    # เมื่อหน้าจอถูกรีรันขึ้นมาพร้อมสถานะ submitting = True ให้ทำงานเบื้องหลังต่อทันที
    if st.session_state.is_submitting:
        try:
            clean_time_label = selected_time_slot.split(" น.")[0]
            norm_time_label = normalize_time_slot(clean_time_label)
            date_str = appointment_date.strftime('%Y-%m-%d')
            arrival_time_str = get_arrival_time_str(norm_time_label)

            booking_source_tag = "[จองผ่านระบบออนไลน์]"
            final_user_notes = f"{booking_source_tag} {notes}".strip() if notes else booking_source_tag

            with BOOKING_LOCK:
                # -------------------------------------------------------------
                # 🛡️ ดักการกดย้ำในระดับ RAM
                # -------------------------------------------------------------
                submission_key = f"{id_card.strip()}_{date_str}"
                now_timestamp = pytime.time()
                last_submit_time = RECENT_BOOKING_SUBMISSIONS.get(submission_key, 0)
                
                if now_timestamp - last_submit_time < 30:
                    st.warning("⚠️ ระบบได้รับข้อมูลของท่านแล้ว และกำลังดำเนินการ กรุณารอสักครู่...")
                    st.session_state.is_submitting = False
                    st.stop()
                
                RECENT_BOOKING_SUBMISSIONS[submission_key] = now_timestamp

                st.cache_data.clear()
                df_appts_fresh = get_table_df("appointments")
                all_appts_list = df_appts_fresh.to_dict('records') if not df_appts_fresh.empty else []
                
                is_dup, dup_row = check_duplicate_appointment(all_appts_list, id_card, phone, full_name, date_str)
                if is_dup:
                    st.cache_data.clear()
                    st.session_state.is_submitting = False
                    st.session_state.just_booked_data = {
                        "full_name": full_name.strip(),
                        "service_type": dup_row.get('service_type', service_name),
                        "appointment_date": dup_row.get('appointment_date', date_str),
                        "appointment_time": dup_row.get('appointment_time', norm_time_label),
                        "arrival_time": get_arrival_time_str(dup_row.get('appointment_time', norm_time_label)),
                        "email": email.strip()
                    }
                    st.rerun()

                sh = get_spreadsheet()
                ws_p = sh.worksheet("patients")
                df_p = get_table_df("patients")
                p_match = df_p[df_p['id_card'].astype(str) == id_card.strip()]
                now_str = get_bangkok_now().strftime('%Y-%m-%d %H:%M:%S')
                
                if not p_match.empty:
                    p_id = int(p_match.iloc[0]['id'])
                    all_p_rows = ws_p.get_all_values()
                    p_headers = [h.strip().lower() for h in all_p_rows[0]]
                    p_id_col = p_headers.index("id")
                    for idx, row in enumerate(all_p_rows[1:], start=2):
                        if row[p_id_col].strip() == str(p_id).strip():
                            ws_p.update_cell(idx, p_headers.index('full_name') + 1, full_name.strip())
                            ws_p.update_cell(idx, p_headers.index('phone') + 1, phone.strip())
                            ws_p.update_cell(idx, p_headers.index('email') + 1, email.strip())
                            break
                else:
                    p_id = int(pd.to_numeric(df_p['id'], errors='coerce').fillna(0).max()) + 1 if not df_p.empty else 1
                    safe_append_row(ws_p, [p_id, full_name.strip(), id_card.strip(), phone.strip(), email.strip(), now_str])

                ws_a = sh.worksheet("appointments")
                next_appt_id = int(pd.to_numeric(df_appts_fresh['id'], errors='coerce').fillna(0).max()) + 1 if not df_appts_fresh.empty else 1
                token = secrets.token_urlsafe(32)

                appt_dict = {
                    "id": next_appt_id,
                    "patient_id": p_id,
                    "full_name": full_name.strip(),
                    "id_card": id_card.strip(),
                    "phone": phone.strip(),
                    "email": email.strip(),
                    "service_type": service_name,
                    "appointment_date": date_str,
                    "appointment_time": norm_time_label,
                    "queue_number": "",
                    "status": "confirmed",
                    "token": token,
                    "reminder_sent": 0,
                    "notes": final_user_notes,
                    "created_at": now_str
                }
                append_appointment_mapped(ws_a, appt_dict)
                st.cache_data.clear()

            # ส่งอีเมลแบบ Background Thread
            threading.Thread(
                target=send_booking_confirmation_email,
                kwargs={
                    "to_email": email.strip(),
                    "full_name": full_name.strip(),
                    "service_name": service_name,
                    "appointment_date_str": appointment_date.strftime('%d/%m/%Y'),
                    "norm_time_label": norm_time_label,
                    "arrival_time_str": arrival_time_str,
                    "token": token
                },
                daemon=True
            ).start()
            
            # ปิดสถานะ submitting และเก็บข้อมูลโชว์หน้าจอสำเร็จ
            st.session_state.is_submitting = False
            st.session_state.just_booked_data = {
                "full_name": full_name.strip(),
                "service_type": service_name,
                "appointment_date": appointment_date.strftime('%d/%m/%Y'),
                "appointment_time": norm_time_label,
                "arrival_time": arrival_time_str,
                "email": email.strip()
            }
            st.rerun()
            
        except Exception as e:
            st.session_state.is_submitting = False
            st.error(f"❌ เกิดข้อผิดพลาดระหว่างบันทึกข้อมูล: {str(e)}")

# ========== หน้า Dashboard แอดมิน ==========
def send_reminder_batch(targets_df, day_type_label, date_formatted):
    if targets_df.empty:
        st.info(f"ไม่มีรายการนัดหมายที่ต้องส่งแจ้งเตือนสำหรับ{day_type_label}")
        return

    sh = get_spreadsheet()
    ws_a = sh.worksheet("appointments")
    all_a_rows = ws_a.get_all_values()
    headers = [h.strip().lower() for h in all_a_rows[0]]
    id_col = headers.index("id")
    remind_col = headers.index("reminder_sent") + 1
    base_url = "https://dental-booking-s7ybkcswqp4qkxg2am8dvl.streamlit.app"
    
    sent_count = 0
    with st.spinner(f"กำลังส่งอีเมลแจ้งเตือนสำหรับ{day_type_label}..."):
        for _, row in targets_df.iterrows():
            email_addr = row.get('email')
            name = row.get('full_name')
            appt_t = normalize_time_slot(str(row.get('appointment_time', '')))
            srv = row.get('service_type')
            appt_id = row.get('id')
            token = row.get('token')
            arrival_time = get_arrival_time_str(appt_t)
            final_confirm_url = f"{base_url}/?final_confirm={token}"
            cancel_url = f"{base_url}/?cancel={token}"
            
            body = f"""
            <div style="font-family: Arial, sans-serif; line-height: 1.6; max-width: 620px; margin: auto; padding: 24px; border: 1px solid #e2e8f0; border-radius: 14px;">
                <div style="background: linear-gradient(135deg, #0284c7 0%, #0369a1 100%); padding: 20px; border-radius: 10px; text-align: center; color: white;">
                    <h2 style="margin:0; font-size: 22px; font-weight: 800;">⏰ แจ้งเตือนนัดหมายทันตกรรม{day_type_label}</h2>
                    <p style="margin:6px 0 0 0; font-size: 15px; opacity: 0.95;">ศูนย์บริการสาธารณสุข 65 รักษาศุข บางบอน</p>
                </div>
                
                <p style="margin-top: 25px; font-size: 16px; color: #1e293b;">เรียนคุณ <b>{name}</b>,</p>
                <p style="font-size: 15px; color: #334155; margin-bottom: 20px;">ท่านมีนัดหมายบริการทันตกรรมใน{day_type_label} ({date_formatted}) โปรดตรวจสอบรายละเอียดและกดยืนยันการเข้ารับบริการ:</p>
                
                <div style="background-color: #ffffff; border: 2.5px solid #0284c7; border-radius: 14px; padding: 22px; margin: 20px 0; box-shadow: 0 4px 12px rgba(2, 132, 199, 0.08);">
                    <div style="border-bottom: 1.5px solid #e2e8f0; padding-bottom: 12px; margin-bottom: 12px;">
                        <span style="font-size: 14px; color: #64748b; font-weight: bold; text-transform: uppercase;">บริการที่นัดหมาย</span><br>
                        <span style="font-size: 24px; color: #0f172a; font-weight: 800;">🦷 {srv}</span>
                    </div>
                    
                    <div style="display: flex; justify-content: space-between; border-bottom: 1.5px solid #e2e8f0; padding-bottom: 12px; margin-bottom: 15px;">
                        <div style="width: 50%;">
                            <span style="font-size: 14px; color: #64748b; font-weight: bold;">วันที่เข้ารับบริการ</span><br>
                            <span style="font-size: 22px; color: #0284c7; font-weight: 800;">📅 {date_formatted}</span>
                        </div>
                        <div style="width: 50%;">
                            <span style="font-size: 14px; color: #64748b; font-weight: bold;">ช่วงเวลาเข้ารับการรักษา</span><br>
                            <span style="font-size: 22px; color: #0f172a; font-weight: 800;">⏰ {appt_t} น.</span>
                        </div>
                    </div>

                    <div style="background-color: #fef2f2; border: 2.5px dashed #ef4444; border-radius: 12px; padding: 18px 12px; text-align: center; margin-top: 10px;">
                        <span style="font-size: 16px; color: #991b1b; font-weight: 800; letter-spacing: 0.5px;">🏥 เวลาที่ต้องมาติดต่อห้องเวชระเบียน</span><br>
                        <span style="font-size: 38px; color: #dc2626; font-weight: 900; line-height: 1.3; display: block; margin: 4px 0;">{arrival_time}</span>
                        <span style="font-size: 13px; color: #b91c1c; font-weight: 600;">(ต้องมาติดต่อในเวลาดังกล่าวเพื่อทำประวัติและตรวจสิทธิ์ หากเกินเวลาขอยกเลิกนัดทันที)</span>
                    </div>
                </div>

                <div style="text-align: center; margin: 30px 0 15px 0;">
                    <a href="{final_confirm_url}" style="background-color: #16a34a; color: white !important; padding: 16px 36px; text-decoration: none; border-radius: 10px; font-weight: 800; display: inline-block; font-size: 17px; box-shadow: 0 4px 12px rgba(22, 163, 74, 0.35);">
                        ✅ ยืนยันเข้ารับบริการ{day_type_label} แน่นอน
                    </a>
                </div>

                <div style="background-color: #fff1f2; border: 2px solid #fecdd3; border-radius: 10px; padding: 14px 18px; margin: 20px 0; text-align: center;">
                    <p style="margin: 0; color: #e11d48; font-weight: 800; font-size: 15px;">
                        ⚠️ หากท่านยืนยันนัดหมายแล้วไม่มารับบริการตามนัด ระบบขอทำการระงับการจองครั้งถัดไป
                    </p>
                </div>

                <div style="text-align: center; margin: 20px 0 25px 0;">
                    <p style="font-size: 14px; color: #64748b; margin-bottom: 10px;">หากท่านไม่สะดวกเข้ารับบริการตามวันเวลาดังกล่าว:</p>
                    <a href="{cancel_url}" style="background-color: #dc2626; color: white !important; padding: 12px 28px; text-decoration: none; border-radius: 8px; font-weight: 800; font-size: 14px; display: inline-block;">
                        ❌ กดยกเลิกการนัดหมาย
                    </a>
                </div>

                {TERMS_AND_CONDITIONS_HTML}

                <hr style="border: 0; border-top: 1px solid #e2e8f0; margin: 25px 0 12px 0;">
                <p style="font-size: 12px; color: #94a3b8; text-align: center; margin: 0;">ศูนย์บริการสาธารณสุข 65 รักษาศุข บางบอน | โทร. 02 453 0526 ต่อ 302</p>
            </div>
            """
            if send_email(email_addr, f"เตือนนัดหมายทันตกรรม{day_type_label} (โปรดยืนยันรับบริการ)", body):
                for idx, r in enumerate(all_a_rows[1:], start=2):
                    if r[id_col].strip() == str(appt_id).strip():
                        ws_a.update_cell(idx, remind_col, 1)
                        break
                sent_count += 1
    st.cache_data.clear()
    st.success(f"ส่งการแจ้งเตือนสำหรับ{day_type_label} สำเร็จทั้งหมด {sent_count}/{len(targets_df)} รายการ")
    st.rerun()

def show_admin_dashboard():
    if "admin_user" not in st.session_state:
        st.session_state.admin_user = None

    if not st.session_state.admin_user:
        st.sidebar.subheader("🔒 เข้าสู่ระบบเจ้าหน้าที่")
        user_input = st.sidebar.text_input("ชื่อผู้ใช้งาน (Username)")
        pwd = st.sidebar.text_input("รหัสผ่าน", type="password")
        if st.sidebar.button("เข้าสู่ระบบ", type="primary", use_container_width=True):
            if verify_admin_login(user_input, pwd):
                st.session_state.admin_user = user_input.strip().lower().replace(" ", "")
                st.session_state.is_admin = True
                st.rerun()
            else:
                st.sidebar.error("❌ ชื่อผู้ใช้งานหรือรหัสผ่านไม่ถูกต้อง")
        st.info("กรุณากรอกชื่อผู้ใช้งานและรหัสผ่านทางแถบด้านซ้ายเพื่อเข้าจัดการระบบ")
        return

    user_role_display = get_user_display_role(st.session_state.admin_user)
    st.sidebar.success(f"👤 ผู้ใช้งาน: **{st.session_state.admin_user}**\n\n📌 แผนก: **{user_role_display}**")
    
    sys_open = get_system_status()
    st.sidebar.caption(f"สถานะระบบ: {'🟢 เปิดบริการ' if sys_open else '🔴 ปิดปรับปรุง'}")
    if st.sidebar.button("🚪 ออกจากระบบ", use_container_width=True):
        st.session_state.admin_user = None
        st.session_state.is_admin = False
        st.rerun()

    st.markdown("### ⚙️ ควบคุมสถานะระบบจองคิวออนไลน์")
    col_st1, col_st2, col_st3 = st.columns([2.5, 1.8, 1.7])
    
    if sys_open:
        col_st1.success("🟢 **สถานะระบบ:** เปิดให้บริการจองคิวตามปกติ")
        if col_st2.button("🔴 ปิดระบบการจอง (โหมดปรับปรุง)", type="primary", use_container_width=True):
            set_system_status(False)
            st.warning("⚠️ ปิดระบบการจองเรียบร้อยแล้ว")
            st.rerun()
    else:
        col_st1.error("🔴 **สถานะระบบ:** ปิดปรับปรุงชั่วคราว")
        if col_st2.button("🟢 เปิดระบบการจองทันที", type="primary", use_container_width=True):
            set_system_status(True)
            st.success("✅ เปิดระบบการจองเรียบร้อยแล้ว")
            st.rerun()
            
    if col_st3.button("🔄 ซิงค์ข้อมูลล่าสุดจาก Sheets", help="กดปุ่มนี้เพื่อล้างแคชและดึงข้อมูลสดจาก Google Sheet ทันที", use_container_width=True):
        st.cache_data.clear()
        st.success("ซิงค์ข้อมูลสดสำเร็จ!")
        st.rerun()

    st.markdown("---")

    menu = st.sidebar.radio(
        "เมนูจัดการระบบ",
        [
            "📊 ภาพรวมสถิติ", 
            "📞 รับโทรจอง / Walk-in", 
            "📅 จัดการคิวนัดหมาย", 
            "🗓️ จัดการ Slot และปฏิทิน", 
            "👥 ทะเบียนผู้ป่วย", 
            "📧 ระบบส่งแจ้งเตือน", 
            "🚫 จัดการ Blacklist", 
            "📋 ประวัติ No-Show"
        ]
    )

    df_appts = get_table_df("appointments")
    sh = get_spreadsheet()

    # 1. ภาพรวมสถิติ
    if menu == "📊 ภาพรวมสถิติ":
        today_dt = get_bangkok_today()
        today_str = today_dt.strftime('%Y-%m-%d')
        
        c_pick1, _ = st.columns([3, 7])
        view_date = c_pick1.date_input("📅 เลือกวันที่ต้องการดูตารางนัดหมาย", value=today_dt, key="dashboard_view_date")
        view_date_str = view_date.strftime('%Y-%m-%d')

        if not df_appts.empty:
            df_appts_st = df_appts.copy()
            df_appts_st['norm_date'] = df_appts_st['appointment_date'].apply(normalize_date_str)
            df_appts_st['norm_status'] = df_appts_st['status'].astype(str).str.strip().str.lower()
            
            future_cnt = len(df_appts_st[df_appts_st['norm_date'] >= today_str])
            selected_cnt = len(df_appts_st[df_appts_st['norm_date'] == view_date_str])
            reconf_cnt = len(df_appts_st[(df_appts_st['norm_date'] == view_date_str) & (df_appts_st['norm_status'] == 'reconfirmed')])
            completed_cnt = len(df_appts_st[(df_appts_st['norm_date'] == view_date_str) & (df_appts_st['norm_status'] == 'completed')])
        else:
            future_cnt = selected_cnt = reconf_cnt = completed_cnt = 0

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("นัดหมายล่วงหน้า (ทั้งหมด)", f"{future_cnt} ราย")
        col2.metric(f"นัดหมายวันที่ {view_date.strftime('%d/%m')}", f"{selected_cnt} ราย")
        col3.metric("🟢 ยืนยันแล้ว (วันที่เลือก)", f"{reconf_cnt} ราย")
        col4.metric("✅ ตรวจเสร็จสิ้น (วันที่เลือก)", f"{completed_cnt} ราย")
            
        st.markdown("<br>", unsafe_allow_html=True)
        st.subheader(f"📋 รายชื่อผู้ป่วยนัดหมายวันที่ {view_date.strftime('%d/%m/%Y')}")
        
        if not df_appts.empty:
            df_today = df_appts_st[df_appts_st['norm_date'] == view_date_str].copy()
            if not df_today.empty:
                df_today['เวลาเวชระเบียน'] = df_today['appointment_time'].apply(get_arrival_time_str)
                df_today['สถานะแสดงผล'] = df_today['status'].map({
                    'reconfirmed': '🟢 ยืนยันแล้ว (มาแน่นอน)',
                    'confirmed': '🟡 ลงทะเบียนสำเร็จ',
                    'pending': '⚪ รอยืนยัน',
                    'completed': '✅ รับบริการแล้ว (Completed)',
                    'no_show': '🔴 ไม่มาตามนัด',
                    'cancelled': '❌ ยกเลิก'
                }).fillna(df_today['status'])
                
                df_today = df_today.sort_values(by=['appointment_time'])

                col_sel1, _ = st.columns([3, 7])
                sel_all_today = col_sel1.checkbox("☑️ ติ๊กเลือกทั้งหมดในตาราง", key=f"sel_all_{view_date_str}")
                
                df_view = df_today.copy()
                df_view['id_num'] = pd.to_numeric(df_view['id'], errors='coerce').fillna(0).astype(int)
                df_view.insert(0, "เลือก", sel_all_today)
                
                display_cols = ['เลือก', 'id_num', 'appointment_time', 'เวลาเวชระเบียน', 'full_name', 'service_type', 'สถานะแสดงผล', 'phone', 'notes']
                
                edited_df = st.data_editor(
                    df_view[display_cols].rename(columns={
                        'id_num': 'ID',
                        'appointment_time': 'ช่วงเวลารักษา',
                        'full_name': 'ชื่อผู้ป่วย',
                        'service_type': 'บริการ',
                        'phone': 'เบอร์โทรศัพท์',
                        'notes': 'หมายเหตุ / ช่องทางจอง'
                    }),
                    column_config={
                        "เลือก": st.column_config.CheckboxColumn("เลือก", default=False),
                        "ID": st.column_config.NumberColumn("ID", disabled=True),
                    },
                    disabled=['ID', 'ช่วงเวลารักษา', 'เวลาเวชระเบียน', 'ชื่อผู้ป่วย', 'บริการ', 'สถานะแสดงผล', 'เบอร์โทรศัพท์', 'หมายเหตุ / ช่องทางจอง'],
                    hide_index=True,
                    use_container_width=True,
                    key=f"editor_today_{view_date_str}_{sel_all_today}"
                )

                selected_today_ids = edited_df[edited_df["เลือก"] == True]["ID"].tolist()
                
                st.markdown("---")
                st.markdown(f"##### ⚡ จัดการสถานะให้คนไข้ที่เลือก (เลือกอยู่: **{len(selected_today_ids)}** ท่าน)")
                
                c_act1, c_act2, c_act3 = st.columns([3, 2, 2])
                with c_act1:
                    new_bulk_status = st.selectbox("เลือกสถานะที่ต้องการเปลี่ยน:", [
                        ("completed", "✅ รับบริการเสร็จสิ้น (Completed)"),
                        ("reconfirmed", "🟢 ยืนยันเข้ารับบริการ (Reconfirmed)"),
                        ("confirmed", "🟡 ลงทะเบียนสำเร็จ (Confirmed)"),
                        ("cancelled", "❌ ยกเลิกนัด (Cancelled) - คืนคิวว่าง"),
                        ("no_show", "🔴 ไม่มาตามนัด (No-Show)")
                    ], format_func=lambda x: x[1], key="bulk_stat_today")
                
                with c_act2:
                    st.markdown("### ")
                    if st.button("💾 บันทึกเปลี่ยนสถานะ", type="primary", use_container_width=True):
                        if not selected_today_ids:
                            st.warning("⚠️ กรุณาทำเครื่องหมายติ๊กถูกที่หน้าชื่อคนไข้อย่างน้อย 1 ท่าน")
                        else:
                            with st.spinner("กำลังอัปเดตข้อมูล..."):
                                updated_cnt = batch_update_appointments_status(selected_today_ids, new_bulk_status[0], reported_by=st.session_state.admin_user)
                                st.success(f"อัปเดตสถานะสำเร็จทั้งหมด {updated_cnt} ท่าน เรียบร้อยแล้ว!")
                                st.rerun()

                with c_act3:
                    st.markdown("### ")
                    if st.button("🩺 ตรวจเสร็จสิ้นทันที", use_container_width=True, help="เปลี่ยนสถานะคนที่ติ๊กเป็น completed ทันที"):
                        if not selected_today_ids:
                            st.warning("⚠️ กรุณาติ๊กหน้าชื่อคนไข้ก่อนกดปุ่มนี้")
                        else:
                            with st.spinner("กำลังบันทึก..."):
                                updated_cnt = batch_update_appointments_status(selected_today_ids, "completed", reported_by=st.session_state.admin_user)
                                st.success(f"บันทึกตรวจเสร็จสิ้น {updated_cnt} ท่าน เรียบร้อยแล้ว!")
                                st.rerun()
            else:
                st.info(f"ไม่มีรายการนัดหมายในวันที่ {view_date.strftime('%d/%m/%Y')}")
        else:
            st.info("ไม่มีรายการนัดหมายในระบบ")

    # 2. รับโทรจอง / Walk-in
    elif menu == "📞 รับโทรจอง / Walk-in":
        booking_agency = get_booking_source_name(st.session_state.admin_user)
        st.subheader("📞 รับโทรจองคิว / ผู้ป่วย Walk-in ประจำศูนย์ฯ")
        st.info(f"👤 ผู้ทำรายการขณะนี้: **{st.session_state.admin_user}** ({user_role_display}) ➔ ระบบจะบันทึก: **{booking_agency}**")
        
        with st.container(border=True):
            channel = st.radio("ช่องทางการติดต่อ", ["📞 โทรศัพท์จอง", "🚶 ติดต่อด้วยตนเอง (Walk-in)"], horizontal=True)
            
            c_p1, c_p2 = st.columns(2)
            with c_p1:
                p_name = st.text_input("ชื่อ - นามสกุล ผู้รับบริการ *", placeholder="ระบุชื่อและนามสกุล")
                p_idcard = st.text_input("เลขประจำตัวประชาชน (13 หลัก) *", placeholder="xxxxxxxxxxxxx", max_chars=13)
            with c_p2:
                p_phone = st.text_input("เบอร์โทรศัพท์ติดต่อ *", placeholder="08xxxxxxxx", max_chars=10)
                p_email = st.text_input("อีเมล (ไม่บังคับ - เว้นว่างได้)", placeholder="หากไม่มี ให้เว้นว่างไว้")

            st.markdown("---")
            c_s1, c_s2, c_s3 = st.columns([2, 2, 1.5])
            with c_s1:
                p_service = st.selectbox("บริการที่ต้องการนัด *", SERVICES)
            with c_s2:
                p_date = st.date_input("เลือกวันที่นัดหมาย *", min_value=get_bangkok_today(), value=get_bangkok_today())
            
            avail_slots, s_msg = get_available_slots(p_date, allow_admin_override=True)
            with c_s3:
                if not avail_slots:
                    st.selectbox("ช่วงเวลา *", [f"⛔ {s_msg}"], disabled=True)
                    p_slot = None
                else:
                    slot_labels = [f"{s['label']} น. (ว่าง {s['available']}/{s['total_slots']} คิว)" for s in avail_slots]
                    p_slot = st.selectbox("ช่วงเวลานัดหมาย *", slot_labels)

            c_n1, c_n2 = st.columns([2, 1])
            with c_n1:
                p_note = st.text_area("หมายเหตุ / ข้อมูลเพิ่มเติม", placeholder="ระบุอาการเบื้องต้น หรือข้อความจากสายโทรศัพท์")
            with c_n2:
                p_status = st.selectbox("สถานะการนัด", ["confirmed (ลงทะเบียนสำเร็จทันที)", "pending (รอยืนยัน)"], index=0)
                send_mail_chk = st.checkbox("ส่งอีเมลแจ้งเตือน (หากระบุอีเมล)", value=True)

            st.markdown("<br>", unsafe_allow_html=True)
            submit_walkin = st.button("💾 บันทึกการนัดหมาย", type="primary", use_container_width=True)

        if submit_walkin:
            if not avail_slots or not p_slot:
                st.error(f"❌ ไม่สามารถจองวันที่เลือกได้: {s_msg}")
            elif not all([p_name.strip(), p_idcard.strip(), p_phone.strip()]):
                st.error("❌ กรุณากรอก ชื่อ-นามสกุล, เลขบัตรประชาชน และเบอร์โทรศัพท์ ให้ครบถ้วน")
            elif len(p_idcard.strip()) != 13 or not p_idcard.strip().isdigit():
                st.error("❌ เลขประจำตัวประชาชนต้องเป็นตัวเลข 13 หลักเท่านั้น")
            else:
                if check_blacklist(id_card=p_idcard.strip()):
                    st.error("⚠️ บัญชีนี้ถูกระงับสิทธิ์ชั่วคราวเนื่องจากไม่มาตามเวลานัดหมาย")
                    return

                p_slot_clean = p_slot.split(" น.")[0]
                norm_p_slot = normalize_time_slot(p_slot_clean)
                p_date_str = p_date.strftime('%Y-%m-%d')
                arrival_time_str = get_arrival_time_str(norm_p_slot)

                with st.spinner("กำลังบันทึกข้อมูล..."):
                    with BOOKING_LOCK:
                        st.cache_data.clear()
                        df_appts_cur = get_table_df("appointments")
                        all_cur_list = df_appts_cur.to_dict('records') if not df_appts_cur.empty else []
                        
                        is_dup, dup_row = check_duplicate_appointment(all_cur_list, p_idcard, p_phone, p_name, p_date_str)
                        if is_dup:
                            st.warning(f"⚠️ คนไข้รายนี้มีนัดอยู่แล้วในวันที่ {dup_row.get('appointment_date')} ช่วงเวลา {dup_row.get('appointment_time')} น.")
                            return

                        ws_p = sh.worksheet("patients")
                        df_p = get_table_df("patients")
                        p_match = df_p[df_p['id_card'].astype(str) == p_idcard.strip()]
                        now_str = get_bangkok_now().strftime('%Y-%m-%d %H:%M:%S')
                        email_save = p_email.strip() if p_email.strip() else ""

                        if not p_match.empty:
                            pt_id = int(p_match.iloc[0]['id'])
                        else:
                            pt_id = int(pd.to_numeric(df_p['id'], errors='coerce').fillna(0).max()) + 1 if not df_p.empty else 1
                            safe_append_row(ws_p, [pt_id, p_name.strip(), p_idcard.strip(), p_phone.strip(), email_save, now_str])

                        ws_a = sh.worksheet("appointments")
                        new_appt_id = int(pd.to_numeric(df_appts_cur['id'], errors='coerce').fillna(0).max()) + 1 if not df_appts_cur.empty else 1
                        token = secrets.token_urlsafe(32)
                        t_stat = 'confirmed' if "confirmed" in p_status else 'pending'
                        
                        source_tag = f"[{booking_agency} - {channel}]"
                        final_notes = f"{source_tag} {p_note}".strip() if p_note else source_tag

                        admin_appt_dict = {
                            "id": new_appt_id,
                            "patient_id": pt_id,
                            "full_name": p_name.strip(),
                            "id_card": p_idcard.strip(),
                            "phone": p_phone.strip(),
                            "email": email_save,
                            "service_type": p_service,
                            "appointment_date": p_date_str,
                            "appointment_time": norm_p_slot,
                            "queue_number": "",
                            "status": t_stat,
                            "token": token,
                            "reminder_sent": 0,
                            "notes": final_notes,
                            "created_at": now_str
                        }
                        append_appointment_mapped(ws_a, admin_appt_dict)
                        st.cache_data.clear()

                if send_mail_chk and email_save:
                    threading.Thread(
                        target=send_booking_confirmation_email,
                        kwargs={
                            "to_email": email_save.strip(),
                            "full_name": p_name.strip(),
                            "service_name": p_service,
                            "appointment_date_str": p_date.strftime('%d/%m/%Y'),
                            "norm_time_label": norm_p_slot,
                            "arrival_time_str": arrival_time_str,
                            "token": token
                        },
                        daemon=True
                    ).start()

                st.success(f"🎉 **บันทึกนัดหมายสำเร็จ!** ({booking_agency}) วันที่ {p_date.strftime('%d/%m/%Y')} ช่วงเวลา {norm_p_slot} น. (เวลาเวชระเบียน: {arrival_time_str})")

    # 3. จัดการคิวนัดหมาย
    elif menu == "📅 จัดการคิวนัดหมาย":
        tab_manage1, tab_manage2, tab_manage3 = st.tabs([
            "🖨️ พิมพ์ใบรายชื่อคนไข้ (PDF)", 
            "🔍 ค้นหาและเปลี่ยนสถานะนัดหมาย",
            "✉️ แก้ไขอีเมลคนไข้ & ส่งอีเมลใหม่"
        ])
        
        with tab_manage1:
            st.subheader("🖨️ พิมพ์ใบรายชื่อผู้เข้ารับบริการทันตกรรม (PDF)")
            st.caption("เลือกวันที่ต้องการ เพื่อพิมพ์ใบรายชื่อคนไข้สำหรับเจ้าหน้าที่และแพทย์ประจำคลินิก (รูปแบบกระดาษ A4 แนวนอน)")
            
            c_pdate1, c_pdate2 = st.columns([2, 3])
            print_date = c_pdate1.date_input("เลือกวันที่ต้องการพิมพ์ใบรายชื่อ", value=get_bangkok_today())
            print_date_str = print_date.strftime('%Y-%m-%d')
            
            if not df_appts.empty:
                df_appts_copy = df_appts.copy()
                df_appts_copy['norm_date'] = df_appts_copy['appointment_date'].apply(normalize_date_str)
                df_day_print = df_appts_copy[df_appts_copy['norm_date'] == print_date_str].copy()
                
                if not df_day_print.empty:
                    df_day_print = df_day_print.sort_values(by=['appointment_time'])
                    df_day_print['เวลาเวชระเบียน'] = df_day_print['appointment_time'].apply(get_arrival_time_str)
                    df_day_print['สถานะแสดงผล'] = df_day_print['status'].map({
                        'reconfirmed': '🟢 ยืนยันแล้ว (มาแน่นอน)',
                        'confirmed': '🟡 ลงทะเบียนสำเร็จ',
                        'pending': '⚪ รอยืนยัน',
                        'completed': '✅ รับบริการแล้ว (Completed)',
                        'no_show': '🔴 ไม่มาตามนัด',
                        'cancelled': '❌ ยกเลิก'
                    }).fillna(df_day_print['status'])

                    c_s1, c_s2, c_s3 = st.columns(3)
                    c_s1.metric("จำนวนคนไข้นัดทั้งหมด", f"{len(df_day_print)} ราย")
                    c_s2.metric("🟢 ยืนยันแล้ว (มาแน่นอน)", f"{len(df_day_print[df_day_print['status'] == 'reconfirmed'])} ราย")
                    c_s3.metric("🟡 ลงทะเบียนสำเร็จ", f"{len(df_day_print[df_day_print['status'] == 'confirmed'])} ราย")

                    pdf_bytes = generate_daily_appointments_pdf(df_day_print, print_date)
                    d_be_filename = print_date.strftime('%Y%m%d')
                    
                    st.download_button(
                        label=f"📥 ดาวน์โหลดไฟล์ PDF ประจำวันที่ {print_date.strftime('%d/%m/%Y')} (สำหรับสั่งพิมพ์)",
                        data=pdf_bytes,
                        file_name=f"รายชื่อคนไข้ทันตกรรม_{d_be_filename}.pdf",
                        mime="application/pdf",
                        type="primary"
                    )

                    st.markdown("##### 📋 ตัวอย่างตารางข้อมูลที่จะพิมพ์ออกมา:")
                    disp_view = df_day_print[['appointment_time', 'เวลาเวชระเบียน', 'full_name', 'service_type', 'phone', 'สถานะแสดงผล', 'notes']].rename(columns={
                        'appointment_time': 'ช่วงเวลารักษา',
                        'เวลาเวชระเบียน': 'เวลาเวชระเบียน',
                        'full_name': 'ชื่อ-นามสกุล',
                        'service_type': 'บริการ',
                        'phone': 'เบอร์โทรศัพท์',
                        'สถานะแสดงผล': 'สถานะ',
                        'notes': 'หมายเหตุ / ช่องทางจอง'
                    })
                    st.dataframe(disp_view, use_container_width=True)
                else:
                    st.info(f"ℹ️ ไม่มีรายการนัดหมายในวันที่ {print_date.strftime('%d/%m/%Y')}")
            else:
                st.info("ยังไม่มีข้อมูลนัดหมายในระบบ")

        with tab_manage2:
            st.subheader("🔍 ค้นหาและเปลี่ยนสถานะนัดหมาย (ติ๊กหน้าชื่อเพื่อเปลี่ยนสถานะ)")
            col1, col2 = st.columns(2)
            start_d = col1.date_input("ตั้งแต่วันที่", value=get_bangkok_today())
            end_d = col2.date_input("ถึงวันที่", value=get_bangkok_today() + timedelta(days=7))
            
            if not df_appts.empty:
                df_appts_f = df_appts.copy()
                df_appts_f['norm_date'] = df_appts_f['appointment_date'].apply(normalize_date_str)
                mask = (df_appts_f['norm_date'] >= start_d.strftime('%Y-%m-%d')) & (df_appts_f['norm_date'] <= end_d.strftime('%Y-%m-%d'))
                df_filtered = df_appts_f[mask].copy()
                
                if not df_filtered.empty:
                    df_filtered['เวลาเวชระเบียน'] = df_filtered['appointment_time'].apply(get_arrival_time_str)
                    df_filtered['สถานะแสดงผล'] = df_filtered['status'].map({
                        'reconfirmed': '🟢 ยืนยันแล้ว (มาแน่นอน)',
                        'confirmed': '🟡 ลงทะเบียนสำเร็จ',
                        'pending': '⚪ รอยืนยัน',
                        'completed': '✅ รับบริการแล้ว (Completed)',
                        'no_show': '🔴 ไม่มาตามนัด',
                        'cancelled': '❌ ยกเลิก'
                    }).fillna(df_filtered['status'])
                    df_filtered = df_filtered.sort_values(by=['appointment_date', 'appointment_time'])

                    col_m_sel1, _ = st.columns([3, 7])
                    sel_all_filtered = col_m_sel1.checkbox("☑️ ติ๊กเลือกทั้งหมดในช่วงเวลานี้", key="sel_all_filtered_chk")
                    
                    df_filtered_view = df_filtered.copy()
                    df_filtered_view['id_num'] = pd.to_numeric(df_filtered_view['id'], errors='coerce').fillna(0).astype(int)
                    df_filtered_view.insert(0, "เลือก", sel_all_filtered)
                    
                    cols_to_show = ['เลือก', 'id_num', 'appointment_date', 'appointment_time', 'เวลาเวชระเบียน', 'full_name', 'service_type', 'สถานะแสดงผล', 'phone', 'notes']
                    
                    edited_manage_df = st.data_editor(
                        df_filtered_view[cols_to_show].rename(columns={
                            'id_num': 'ID',
                            'appointment_date': 'วันที่นัดหมาย',
                            'appointment_time': 'ช่วงเวลารักษา',
                            'full_name': 'ชื่อผู้ป่วย',
                            'service_type': 'บริการ',
                            'phone': 'เบอร์โทรศัพท์',
                            'notes': 'หมายเหตุ / ช่องทางจอง'
                        }),
                        column_config={
                            "เลือก": st.column_config.CheckboxColumn("เลือก", default=False),
                            "ID": st.column_config.NumberColumn("ID", disabled=True),
                        },
                        disabled=['ID', 'วันที่นัดหมาย', 'ช่วงเวลารักษา', 'เวลาเวชระเบียน', 'ชื่อผู้ป่วย', 'บริการ', 'สถานะแสดงผล', 'เบอร์โทรศัพท์', 'หมายเหตุ / ช่องทางจอง'],
                        hide_index=True,
                        use_container_width=True,
                        key=f"editor_filtered_{sel_all_filtered}"
                    )

                    selected_filter_ids = edited_manage_df[edited_manage_df["เลือก"] == True]["ID"].tolist()

                    st.markdown("---")
                    st.markdown(f"##### 💾 เปลี่ยนสถานะให้รายการที่เลือก (เลือกอยู่: **{len(selected_filter_ids)}** รายการ)")
                    c_act_m1, c_act_m2 = st.columns([3, 2])
                    
                    with c_act_m1:
                        target_manage_status = st.selectbox("เลือกสถานะใหม่ที่ต้องการ:", [
                            ("completed", "✅ รับบริการเสร็จสิ้นแล้ว (Completed)"),
                            ("cancelled", "❌ ยกเลิกนัด (Cancelled) - คืนคิวว่างให้ระบบทันที"),
                            ("reconfirmed", "🟢 ยืนยันมาแน่นอน (Reconfirmed)"),
                            ("confirmed", "🟡 ลงทะเบียนสำเร็จ (Confirmed)"),
                            ("pending", "⚪ รอยืนยัน (Pending)"),
                            ("no_show", "🔴 ไม่มาตามนัด (No-Show)")
                        ], format_func=lambda x: x[1], key="filter_bulk_status")
                        
                    with c_act_m2:
                        st.markdown("### ")
                        if st.button("💾 บันทึกเปลี่ยนสถานะที่เลือกทั้งหมด", type="primary", use_container_width=True):
                            if not selected_filter_ids:
                                st.warning("⚠️ กรุณาติ๊กถูกที่หน้าชื่อในตารางอย่างน้อย 1 รายการ")
                            else:
                                with st.spinner("กำลังบันทึกข้อมูล..."):
                                    up_cnt = batch_update_appointments_status(selected_filter_ids, target_manage_status[0], reported_by=st.session_state.admin_user)
                                    st.success(f"อัปเดตสถานะสำเร็จทั้งหมด {up_cnt} รายการ!")
                                    st.rerun()
                else:
                    st.info("ไม่มีข้อมูลนัดหมายในช่วงเวลานี้")
            else:
                st.info("ไม่มีข้อมูลนัดหมายในช่วงเวลานี้")

        with tab_manage3:
            st.subheader("✉️ แก้ไขอีเมลคนไข้ & ส่งอีเมลยืนยันนัดใหม่")
            st.caption("ใช้สำหรับแก้ไขที่อยู่อีเมลในกรณีที่คนไข้พิมพ์ผิด เพื่อให้คนไข้ได้รับอีเมลยืนยันตามปกติ")
            
            if not df_appts.empty:
                c_srch1, _ = st.columns([3, 2])
                search_kw = c_srch1.text_input("🔍 ค้นหาคนไข้ (ชื่อ, เบอร์โทร, เลขบัตร ปชช., ID)", placeholder="พิมพ์คำค้นหา...")
                
                df_search = df_appts.copy()
                if search_kw.strip():
                    kw = search_kw.strip().lower()
                    mask = (
                        df_search['full_name'].astype(str).str.lower().str.contains(kw) |
                        df_search['phone'].astype(str).str.contains(kw) |
                        df_search['id_card'].astype(str).str.contains(kw) |
                        df_search['id'].astype(str).str.contains(kw)
                    )
                    df_search = df_search[mask]
                else:
                    df_search = df_search.sort_values(by=['id'], ascending=False).head(25)
                    
                if not df_search.empty:
                    appt_choices = {
                        str(row['id']): f"ID {row['id']} | {row['full_name']} | นัดวันที่ {row['appointment_date']} ({row['appointment_time']} น.) | อีเมลเดิม: {row.get('email', '-')}"
                        for _, row in df_search.iterrows()
                    }
                    selected_edit_id = st.selectbox("เลือกรายการนัดหมายที่ต้องการแก้ไขอีเมล:", options=list(appt_choices.keys()), format_func=lambda x: appt_choices[x])
                    
                    target_row = df_appts[df_appts['id'].astype(str) == str(selected_edit_id)].iloc[0]
                    curr_email = str(target_row.get('email', '')).strip()
                    pt_id = target_row.get('patient_id')
                    p_name = target_row.get('full_name')
                    p_serv = target_row.get('service_type')
                    p_date = target_row.get('appointment_date')
                    p_time = normalize_time_slot(str(target_row.get('appointment_time', '')))
                    p_token = target_row.get('token')
                    arrival_time = get_arrival_time_str(p_time)
                    
                    with st.container(border=True):
                        st.markdown(f"""
                        **ข้อมูลคนไข้ปัจจุบัน:**
                        * **ชื่อ - นามสกุล:** `{p_name}` | **เบอร์โทร:** `{target_row.get('phone')}`
                        * **บริการ:** `{p_serv}` | **วันเวลานัด:** `{p_date}` ({p_time} น.)
                        * **อีเมลเดิมในระบบ:** <span style="color:#dc2626; font-weight:bold;">{curr_email or '(ไม่มีอีเมล)'}</span>
                        """, unsafe_allow_html=True)
                        
                        new_email_input = st.text_input("ระบุที่อยู่อีเมลที่ถูกต้องใหม่ *", value=curr_email, placeholder="เช่น *******@*****.***")
                        col_btn1, col_btn2, col_btn3 = st.columns(3)
                        
                        if col_btn1.button("✉️ บันทึก & ส่งอีเมลยืนยันนัดใหม่", type="primary", use_container_width=True):
                            clean_mail = new_email_input.strip()
                            if not clean_mail or "@" not in clean_mail or "." not in clean_mail:
                                st.error("❌ กรุณาระบุรูปแบบอีเมลที่ถูกต้อง")
                            else:
                                with st.spinner("กำลังบันทึกและส่งอีเมลยืนยันใหม่..."):
                                    ws_a = sh.worksheet("appointments")
                                    a_rows = ws_a.get_all_values()
                                    a_headers = [h.strip().lower() for h in a_rows[0]]
                                    id_col = a_headers.index("id")
                                    mail_col = a_headers.index("email") + 1
                                    for idx, r in enumerate(a_rows[1:], start=2):
                                        if str(r[id_col]).strip() == str(selected_edit_id).strip():
                                            ws_a.update_cell(idx, mail_col, clean_mail)
                                            break
                                            
                                    if pt_id:
                                        try:
                                            ws_p = sh.worksheet("patients")
                                            p_rows = ws_p.get_all_values()
                                            p_headers = [h.strip().lower() for h in p_rows[0]]
                                            p_id_col = p_headers.index("id")
                                            p_mail_col = p_headers.index("email") + 1
                                            for idx, r in enumerate(p_rows[1:], start=2):
                                                if str(r[p_id_col]).strip() == str(pt_id).strip():
                                                    ws_p.update_cell(idx, p_mail_col, clean_mail)
                                                    break
                                        except Exception:
                                            pass
                                            
                                    st.cache_data.clear()
                                    
                                    sent = send_booking_confirmation_email(
                                        to_email=clean_mail,
                                        full_name=p_name,
                                        service_name=p_serv,
                                        appointment_date_str=p_date,
                                        norm_time_label=p_time,
                                        arrival_time_str=arrival_time,
                                        token=p_token
                                    )
                                    if sent:
                                        st.success(f"🎉 บันทึกอีเมลใหม่เป็น `{clean_mail}` และส่งอีเมลเรียบร้อยแล้ว!")
                                    else:
                                        st.warning("บันทึกสำเร็จ แต่ส่งอีเมลไม่ผ่าน กรุณาตรวจสอบ SMTP")
                                    st.rerun()

                        if col_btn2.button("⏰ บันทึก & ส่งอีเมลแจ้งเตือนใหม่", use_container_width=True):
                            clean_mail = new_email_input.strip()
                            if not clean_mail or "@" not in clean_mail or "." not in clean_mail:
                                st.error("❌ กรุณาระบุรูปแบบอีเมลที่ถูกต้อง")
                            else:
                                with st.spinner("กำลังบันทึกและส่งอีเมลแจ้งเตือนใหม่..."):
                                    ws_a = sh.worksheet("appointments")
                                    a_rows = ws_a.get_all_values()
                                    a_headers = [h.strip().lower() for h in a_rows[0]]
                                    id_col = a_headers.index("id")
                                    mail_col = a_headers.index("email") + 1
                                    for idx, r in enumerate(a_rows[1:], start=2):
                                        if str(r[id_col]).strip() == str(selected_edit_id).strip():
                                            ws_a.update_cell(idx, mail_col, clean_mail)
                                            break
                                            
                                    if pt_id:
                                        try:
                                            ws_p = sh.worksheet("patients")
                                            p_rows = ws_p.get_all_values()
                                            p_headers = [h.strip().lower() for h in p_rows[0]]
                                            p_id_col = p_headers.index("id")
                                            p_mail_col = p_headers.index("email") + 1
                                            for idx, r in enumerate(p_rows[1:], start=2):
                                                if str(r[p_id_col]).strip() == str(pt_id).strip():
                                                    ws_p.update_cell(idx, p_mail_col, clean_mail)
                                                    break
                                        except Exception:
                                            pass
                                            
                                    st.cache_data.clear()
                                    
                                    base_url = "https://dental-booking-s7ybkcswqp4qkxg2am8dvl.streamlit.app"
                                    final_confirm_url = f"{base_url}/?final_confirm={p_token}"
                                    cancel_url = f"{base_url}/?cancel={p_token}"
                                    
                                    remind_body = f"""
                                    <div style="font-family: Arial, sans-serif; line-height: 1.6; max-width: 620px; margin: auto; padding: 24px; border: 1px solid #e2e8f0; border-radius: 14px;">
                                        <div style="background: linear-gradient(135deg, #0284c7 0%, #0369a1 100%); padding: 20px; border-radius: 10px; text-align: center; color: white;">
                                            <h2 style="margin:0; font-size: 22px; font-weight: 800;">⏰ แจ้งเตือนนัดหมายทันตกรรม</h2>
                                            <p style="margin:6px 0 0 0; font-size: 15px; opacity: 0.95;">ศูนย์บริการสาธารณสุข 65 รักษาศุข บางบอน</p>
                                        </div>
                                        <p style="margin-top: 25px; font-size: 16px;">เรียนคุณ <b>{p_name}</b>,</p>
                                        <p style="font-size: 15px;">ท่านมีนัดหมายบริการทันตกรรมในวันที่ {p_date} ({p_time} น.) โปรดกดยืนยันเข้ารับบริการ:</p>
                                        
                                        <div style="background-color: #fef2f2; border: 2.5px dashed #ef4444; border-radius: 12px; padding: 18px 12px; text-align: center; margin: 15px 0;">
                                            <span style="font-size: 16px; color: #991b1b; font-weight: bold;">🏥 เวลาที่ต้องมาติดต่อห้องเวชระเบียน</span><br>
                                            <span style="font-size: 38px; color: #dc2626; font-weight: 900;">{arrival_time}</span>
                                        </div>
                                        
                                        <div style="text-align: center; margin: 25px 0;">
                                            <a href="{final_confirm_url}" style="background-color: #16a34a; color: white !important; padding: 16px 36px; text-decoration: none; border-radius: 10px; font-weight: 800; font-size: 17px; display: inline-block;">
                                                ✅ ยืนยันเข้ารับบริการแน่นอน
                                            </a>
                                            <div style="margin-top: 15px;">
                                                <a href="{cancel_url}" style="background-color: #dc2626; color: white !important; padding: 10px 22px; text-decoration: none; border-radius: 8px; font-size: 14px; display: inline-block;">
                                                    ❌ กดยกเลิกการนัดหมาย
                                                </a>
                                            </div>
                                        </div>
                                        {TERMS_AND_CONDITIONS_HTML}
                                    </div>
                                    """
                                    sent = send_email(clean_mail, "เตือนนัดหมายทันตกรรม (โปรดยืนยันรับบริการ)", remind_body)
                                    if sent:
                                        st.success(f"🎉 ส่งอีเมลแจ้งเตือนไปยัง `{clean_mail}` สำเร็จแล้ว!")
                                    else:
                                        st.warning("บันทึกสำเร็จ แต่ส่งอีเมลแจ้งเตือนไม่ผ่าน")
                                    st.rerun()

                        if col_btn3.button("💾 บันทึกเฉพาะอีเมล (ไม่ส่งเมล)", use_container_width=True):
                            clean_mail = new_email_input.strip()
                            if not clean_mail:
                                st.error("❌ กรุณาระบุอีเมล")
                            else:
                                with st.spinner("กำลังบันทึก..."):
                                    ws_a = sh.worksheet("appointments")
                                    a_rows = ws_a.get_all_values()
                                    a_headers = [h.strip().lower() for h in a_rows[0]]
                                    id_col = a_headers.index("id")
                                    mail_col = a_headers.index("email") + 1
                                    for idx, r in enumerate(a_rows[1:], start=2):
                                        if str(r[id_col]).strip() == str(selected_edit_id).strip():
                                            ws_a.update_cell(idx, mail_col, clean_mail)
                                            break
                                            
                                    if pt_id:
                                        try:
                                            ws_p = sh.worksheet("patients")
                                            p_rows = ws_p.get_all_values()
                                            p_headers = [h.strip().lower() for h in p_rows[0]]
                                            p_id_col = p_headers.index("id")
                                            p_mail_col = p_headers.index("email") + 1
                                            for idx, r in enumerate(p_rows[1:], start=2):
                                                if str(r[p_id_col]).strip() == str(pt_id).strip():
                                                    ws_p.update_cell(idx, p_mail_col, clean_mail)
                                                    break
                                        except Exception:
                                            pass
                                            
                                    st.cache_data.clear()
                                    st.success(f"✅ บันทึกอีเมล `{clean_mail}` เรียบร้อยแล้ว (ไม่มีการส่งอีเมล)")
                                    st.rerun()
                else:
                    st.info("ไม่พบรายการที่ตรงกับคำค้นหา")
            else:
                st.info("ยังไม่มีข้อมูลนัดหมายในระบบ")

    # 4. จัดการ Slot และปฏิทิน
    elif menu == "🗓️ จัดการ Slot และปฏิทิน":
        st.subheader("🗓️ กำหนด Slot ย่อย และกระดานวันทำการ")
        
        col_pick1, _ = st.columns([2, 3])
        start_of_current_week = get_bangkok_today() - timedelta(days=get_bangkok_today().weekday())
        selected_monday = col_pick1.date_input("เลือกวันจันทร์ของสัปดาห์ที่ต้องการดู", value=start_of_current_week)
        week_monday = selected_monday - timedelta(days=selected_monday.weekday())
        week_sunday = week_monday + timedelta(days=6)
        
        m_year_be = week_monday.year + 543
        s_year_be = week_sunday.year + 543
        st.markdown(f"#### วันทำการหลัก ({week_monday.strftime('%d/%m')}/{m_year_be} - {week_sunday.strftime('%d/%m')}/{s_year_be})")

        week_days_thai = ["วันจันทร์", "วันอังคาร", "วันพุธ", "วันพฤหัสบดี", "วันศุกร์", "วันเสาร์", "วันอาทิตย์"]
        cols = st.columns(7)
        
        df_sched = get_table_df("daily_schedule")
        if not df_sched.empty:
            df_sched['norm_date'] = df_sched['schedule_date'].apply(normalize_date_str)

        for i in range(7):
            cur_date = week_monday + timedelta(days=i)
            cur_date_str = cur_date.strftime('%Y-%m-%d')
            
            day_slots = df_sched[df_sched['norm_date'] == cur_date_str].sort_values(by=['start_time']) if not df_sched.empty else pd.DataFrame()
            
            with cols[i]:
                if day_slots.empty:
                    time_sub = "-"
                    slot_content = "<div style='text-align:center; color:#94a3b8; margin: 20px 0;'>-</div>"
                    total_q = 0
                elif (day_slots['is_open'].astype(int) == 0).any():
                    time_sub = "ปิดทำการ"
                    slot_content = "<div style='text-align:center; color:#ef4444; font-weight:600; margin: 20px 0;'>ปิดทำการ</div>"
                    total_q = 0
                else:
                    earliest = day_slots.iloc[0]['start_time']
                    latest = day_slots.iloc[-1]['end_time']
                    time_sub = f"{earliest} - {latest}"
                    
                    slot_htmls = []
                    total_q = 0
                    for _, row in day_slots.iterrows():
                        cap = int(row.get('max_patients', 4))
                        total_q += cap
                        s_t = row.get('start_time')
                        e_t = row.get('end_time')
                        slot_htmls.append(f'<div class="slot-pill-box"><span>{s_t} - {e_t}</span><span class="pill-badge">{cap}</span></div>')
                    slot_content = "".join(slot_htmls)
                
                card_html = f'<div class="day-card"><div><div class="day-header">{week_days_thai[i]}</div><div class="day-sub">{time_sub}</div>{slot_content}</div><div class="day-footer">{total_q}</div></div>'
                st.markdown(card_html, unsafe_allow_html=True)

        st.markdown("<br><hr>", unsafe_allow_html=True)
        
        tab_slot1, tab_slot2, tab_slot3, tab_slot4 = st.tabs([
            "⚡ กำหนด Slot เหมายกเดือน", 
            "➕ เพิ่ม Slot เจาะจงเฉพาะวัน", 
            "🚫 สั่งปิดทำการทั้งวัน", 
            "🗑️ ดูรายการและล้าง Slot"
        ])

        with tab_slot1:
            st.markdown("##### กำหนดช่วงเวลาและคิว เหมายกช่วง/ยกเดือน (เช่น 1-30 ก.ย.)")
            with st.form("form_batch_slot"):
                cb_d1, cb_d2 = st.columns(2)
                b_start = cb_d1.date_input("ตั้งแต่วันที่", value=get_bangkok_today())
                b_end = cb_d2.date_input("จนถึงวันที่", value=get_bangkok_today() + timedelta(days=30))
                
                day_opts = {0: "วันจันทร์", 1: "วันอังคาร", 2: "วันพุธ", 3: "วันพฤหัสบดี", 4: "วันศุกร์", 5: "วันเสาร์", 6: "วันอาทิตย์"}
                b_days = st.multiselect("เลือกวันในสัปดาห์", options=list(day_opts.keys()), default=[0, 1, 2, 3, 4], format_func=lambda x: day_opts[x])
                
                c_t1, c_t2, c_t3 = st.columns(3)
                bs_time = c_t1.time_input("เวลาเริ่ม", value=time(15, 0))
                be_time = c_t2.time_input("เวลาสิ้นสุด", value=time(16, 0))
                bq_cap = c_t3.number_input("จำนวนคิวที่รับ", min_value=1, max_value=50, value=3)
                
                clear_first = st.checkbox("🧹 ลบ Slot เดิมในช่วงวันที่เลือกทั้งหมดก่อนสร้างใหม่", value=False)
                
                if st.form_submit_button("🚀 บันทึกช่วงเวลานี้ลงปฏิทิน", type="primary"):
                    if b_start > b_end:
                        st.error("วันที่เริ่มต้นต้องไม่มากกว่าวันที่สิ้นสุด")
                    elif not b_days:
                        st.error("กรุณาเลือกวันในสัปดาห์อย่างน้อย 1 วัน")
                    else:
                        ws_s = sh.worksheet("daily_schedule")
                        df_cur = get_table_df("daily_schedule")
                        
                        if clear_first and not df_cur.empty:
                            df_cur['norm_date'] = df_cur['schedule_date'].apply(normalize_date_str)
                            mask = (df_cur['norm_date'] >= b_start.strftime('%Y-%m-%d')) & (df_cur['norm_date'] <= b_end.strftime('%Y-%m-%d'))
                            df_kept = df_cur[~mask]
                            ws_s.clear()
                            safe_append_row(ws_s, TABLE_SCHEMAS["daily_schedule"])
                            if not df_kept.empty:
                                safe_append_rows(ws_s, df_kept[TABLE_SCHEMAS["daily_schedule"]].values.tolist())

                        next_id = int(pd.to_numeric(df_cur['id'], errors='coerce').fillna(0).max()) + 1 if not df_cur.empty else 1
                        new_rows = []
                        cur_d = b_start
                        while cur_d <= b_end:
                            if cur_d.weekday() in b_days:
                                new_rows.append([
                                    next_id, cur_d.strftime('%Y-%m-%d'), 1, 
                                    bs_time.strftime('%H:%M'), be_time.strftime('%H:%M'), int(bq_cap), 'เปิดทำการ'
                                ])
                                next_id += 1
                            cur_d += timedelta(days=1)
                            
                        if new_rows:
                            safe_append_rows(ws_s, new_rows)
                        st.cache_data.clear()
                        st.success(f"✅ บันทึก Slot {bs_time.strftime('%H:%M')}-{be_time.strftime('%H:%M')} น. รวม {len(new_rows)} วัน เรียบร้อยแล้ว")
                        st.rerun()

        with tab_slot2:
            st.markdown("##### เพิ่มช่วงเวลาย่อยในวันใดวันหนึ่ง")
            with st.form("form_single_slot"):
                c_s1, c_s2, c_s3, c_s4 = st.columns(4)
                s_date = c_s1.date_input("เลือกวันที่", value=get_bangkok_today())
                s_start = c_s2.time_input("เวลาเริ่ม", value=time(8, 30))
                s_end = c_s3.time_input("เวลาสิ้นสุด", value=time(11, 0))
                s_cap = c_s4.number_input("จำนวนคิว", min_value=1, max_value=50, value=10)
                
                if st.form_submit_button("➕ เพิ่มช่วงเวลานี้ในวันที่เลือก", type="primary"):
                    ws_s = sh.worksheet("daily_schedule")
                    df_cur = get_table_df("daily_schedule")
                    next_id = int(pd.to_numeric(df_cur['id'], errors='coerce').fillna(0).max()) + 1 if not df_cur.empty else 1
                    safe_append_row(ws_s, [
                        next_id, s_date.strftime('%Y-%m-%d'), 1, 
                        s_start.strftime('%H:%M'), s_end.strftime('%H:%M'), int(s_cap), 'เปิดทำการ'
                    ])
                    st.cache_data.clear()
                    st.success(f"✅ เพิ่ม Slot {s_start.strftime('%H:%M')}-{s_end.strftime('%H:%M')} น. เรียบร้อย")
                    st.rerun()

        with tab_slot3:
            st.markdown("##### สั่งปิดทำการทั้งวัน (เช่น วันหยุดนักขัตฤกษ์ / ปิดซ่อมยูนิต)")
            with st.form("form_close_day"):
                cc1, cc2 = st.columns(2)
                cl_date = cc1.date_input("เลือกวันที่ต้องการปิดทำการ", value=get_bangkok_today())
                cl_note = cc2.text_input("สาเหตุที่ปิด", placeholder="เช่น วันหยุดราชการ, วันหยุดนักขัตฤกษ์")
                
                if st.form_submit_button("🔴 สั่งปิดทำการทั้งวัน", type="primary"):
                    ws_s = sh.worksheet("daily_schedule")
                    df_cur = get_table_df("daily_schedule")
                    if not df_cur.empty:
                        df_cur['norm_date'] = df_cur['schedule_date'].apply(normalize_date_str)
                        df_kept = df_cur[df_cur['norm_date'] != cl_date.strftime('%Y-%m-%d')]
                        ws_s.clear()
                        safe_append_row(ws_s, TABLE_SCHEMAS["daily_schedule"])
                        if not df_kept.empty:
                            safe_append_rows(ws_s, df_kept[TABLE_SCHEMAS["daily_schedule"]].values.tolist())
                    
                    next_id = int(pd.to_numeric(df_cur['id'], errors='coerce').fillna(0).max()) + 1 if not df_cur.empty else 1
                    safe_append_row(ws_s, [next_id, cl_date.strftime('%Y-%m-%d'), 0, "", "", 0, cl_note])
                    st.cache_data.clear()
                    st.success(f"กำหนดให้วันที่ {cl_date.strftime('%d/%m/%Y')} ปิดทำการทั้งวันเรียบร้อย")
                    st.rerun()

        with tab_slot4:
            st.markdown("##### 🗓️ ลบ Slot ตามช่วงวันที่ (แนะนำ)")
            col_del_r1, col_del_r2, col_del_r3 = st.columns([2, 2, 2])
            del_from = col_del_r1.date_input("ลบตั้งแต่วันที่", value=get_bangkok_today(), key="del_from_d")
            del_to = col_del_r2.date_input("จนถึงวันที่", value=get_bangkok_today() + timedelta(days=30), key="del_to_d")
            col_del_r3.markdown("### ")
            if col_del_r3.button("🗑️ ล้าง Slot ในช่วงนี้", type="primary", use_container_width=True):
                ws_s = sh.worksheet("daily_schedule")
                df_cur = get_table_df("daily_schedule")
                if not df_cur.empty:
                    df_cur['norm_date'] = df_cur['schedule_date'].apply(normalize_date_str)
                    mask = (df_cur['norm_date'] >= del_from.strftime('%Y-%m-%d')) & (df_cur['norm_date'] <= del_to.strftime('%Y-%m-%d'))
                    del_cnt = mask.sum()
                    df_kept = df_cur[~mask]
                    ws_s.clear()
                    safe_append_row(ws_s, TABLE_SCHEMAS["daily_schedule"])
                    if not df_kept.empty:
                        safe_append_rows(ws_s, df_kept[TABLE_SCHEMAS["daily_schedule"]].values.tolist())
                    st.cache_data.clear()
                    st.success(f"✅ ล้าง Slot เรียบร้อยแล้วทั้งหมด {del_cnt} รายการ")
                else:
                    st.warning("⚠️ ไม่พบรายการ Slot ในช่วงวันที่เลือก")
                st.rerun()

            st.markdown("---")
            st.markdown("##### 📋 ตรวจสอบรายการ Slot ทั้งหมดในระบบ")
            view_d = st.date_input("เลือกดูตั้งแต่ช่วงวันที่", value=get_bangkok_today() - timedelta(days=7))
            df_cur = get_table_df("daily_schedule")
            if not df_cur.empty:
                df_cur['norm_date'] = df_cur['schedule_date'].apply(normalize_date_str)
                v_mask = df_cur['norm_date'] >= view_d.strftime('%Y-%m-%d')
                st.dataframe(df_cur[v_mask].sort_values(by=['schedule_date', 'start_time']), use_container_width=True)
            else:
                st.info("ไม่มีรายการ Slot")
            
            cd1, cd2 = st.columns(2)
            del_id = cd1.number_input("ใส่ 'รหัส (ID)' ของ Slot ที่ต้องการลบเฉพาะจุด", min_value=1, step=1)
            if cd1.button("🗑 ลบเฉพาะ Slot รหัสนี้"):
                ws_s = sh.worksheet("daily_schedule")
                df_cur = get_table_df("daily_schedule")
                if not df_cur.empty and (df_cur['id'].astype(str) == str(del_id)).any():
                    df_kept = df_cur[df_cur['id'].astype(str) != str(del_id)]
                    ws_s.clear()
                    safe_append_row(ws_s, TABLE_SCHEMAS["daily_schedule"])
                    if not df_kept.empty:
                        safe_append_rows(ws_s, df_kept[TABLE_SCHEMAS["daily_schedule"]].values.tolist())
                    st.cache_data.clear()
                    st.success(f"ลบ Slot รหัส {del_id} สำเร็จ")
                else:
                    st.warning(f"ไม่พบ Slot รหัส {del_id}")
                st.rerun()
                
            del_all_date = cd2.date_input("หรือเลือกลบ Slot ทั้งหมดของวันใดวันหนึ่ง", value=get_bangkok_today(), key="del_single_day")
            if cd2.button("🗑️ ล้างตารางเวลาทั้งหมดของวันนี้"):
                ws_s = sh.worksheet("daily_schedule")
                df_cur = get_table_df("daily_schedule")
                if not df_cur.empty:
                    df_cur['norm_date'] = df_cur['schedule_date'].apply(normalize_date_str)
                    if (df_cur['norm_date'] == del_all_date.strftime('%Y-%m-%d')).any():
                        df_kept = df_cur[df_cur['norm_date'] != del_all_date.strftime('%Y-%m-%d')]
                        ws_s.clear()
                        safe_append_row(ws_s, TABLE_SCHEMAS["daily_schedule"])
                        if not df_kept.empty:
                            safe_append_rows(ws_s, df_kept[TABLE_SCHEMAS["daily_schedule"]].values.tolist())
                        st.cache_data.clear()
                        st.success(f"ล้างตารางเวลาของวันที่ {del_all_date.strftime('%d/%m/%Y')} เรียบร้อย")
                    else:
                        st.warning("ไม่พบ Slot ในวันที่เลือก")
                st.rerun()

    # 5. ทะเบียนผู้ป่วย
    elif menu == "👥 ทะเบียนผู้ป่วย":
        st.subheader("👥 รายชื่อผู้ป่วยทั้งหมดในระบบ")
        df_p = get_table_df("patients")
        if not df_p.empty:
            st.dataframe(df_p.sort_values(by=['id'], ascending=False), use_container_width=True)
        else:
            st.info("ยังไม่มีข้อมูลผู้ป่วย")

    # 6. ระบบส่งแจ้งเตือน
    elif menu == "📧 ระบบส่งแจ้งเตือน":
        st.subheader("📧 ส่งอีเมลแจ้งเตือนนัดหมาย")
        st.caption("ระบบจะส่งอีเมลแจ้งเตือนนัดหมาย พร้อมปุ่มให้คนไข้กดยืนยันเข้ารับบริการ (สถานะจะเปลี่ยนเป็นสีเขียว reconfirmed) หรือกดยกเลิกนัดได้ทันที")

        tab_remind_tomorrow, tab_remind_today = st.tabs([
            "⏰ แจ้งเตือนนัดหมายวันพรุ่งนี้ (ล่วงหน้า 1 วัน)", 
            "⚡ แจ้งเตือนนัดหมายวันนี้ (เคสจองกระชั้นชิด/วันเดียวกัน)"
        ])

        today_dt = get_bangkok_today()
        today_str = today_dt.strftime('%Y-%m-%d')
        today_formatted = today_dt.strftime('%d/%m/%Y')

        tomorrow_dt = today_dt + timedelta(days=1)
        tomorrow_str = tomorrow_dt.strftime('%Y-%m-%d')
        tomorrow_formatted = tomorrow_dt.strftime('%d/%m/%Y')

        df_appts_t = pd.DataFrame()
        if not df_appts.empty:
            df_appts_t = df_appts.copy()
            df_appts_t['norm_date'] = df_appts_t['appointment_date'].apply(normalize_date_str)
            df_appts_t['norm_status'] = df_appts_t['status'].astype(str).str.strip().str.lower()

        with tab_remind_tomorrow:
            targets_tomorrow = pd.DataFrame()
            if not df_appts_t.empty:
                targets_tomorrow = df_appts_t[
                    (df_appts_t['norm_date'] == tomorrow_str) & 
                    (df_appts_t['norm_status'].isin(['confirmed', 'pending'])) & 
                    (df_appts_t['reminder_sent'].astype(str).isin(['0', 0, '']))
                ]
            
            c_tm1, c_tm2 = st.columns([3, 1])
            c_tm1.markdown(f"**รอบนัดหมายวันพรุ่งนี้:** วันที่ `{tomorrow_formatted}` (รอส่งแจ้งเตือน: **{len(targets_tomorrow)}** ราย)")
            
            if not targets_tomorrow.empty:
                st.dataframe(targets_tomorrow[['appointment_time', 'full_name', 'service_type', 'phone', 'email']], use_container_width=True)
            else:
                st.info("ไม่มีรายการนัดหมายวันพรุ่งนี้ที่ค้างส่งแจ้งเตือน")
                
            if c_tm2.button("🚀 ส่งแจ้งเตือนวันพรุ่งนี้", type="primary", use_container_width=True, disabled=targets_tomorrow.empty):
                send_reminder_batch(targets_tomorrow, "วันพรุ่งนี้", tomorrow_formatted)

        with tab_remind_today:
            targets_today = pd.DataFrame()
            if not df_appts_t.empty:
                targets_today = df_appts_t[
                    (df_appts_t['norm_date'] == today_str) & 
                    (df_appts_t['norm_status'].isin(['confirmed', 'pending'])) & 
                    (df_appts_t['reminder_sent'].astype(str).isin(['0', 0, '']))
                ]
            
            c_td1, c_td2 = st.columns([3, 1])
            c_td1.markdown(f"**รอบนัดหมายวันนี้:** วันที่ `{today_formatted}` (รอส่งแจ้งเตือน: **{len(targets_today)}** ราย)")
            
            if not targets_today.empty:
                st.dataframe(targets_today[['appointment_time', 'full_name', 'service_type', 'phone', 'email']], use_container_width=True)
            else:
                st.info("ไม่มีรายการนัดหมายวันนี้ที่ค้างส่งแจ้งเตือน")
                
            if c_td2.button("⚡ ส่งแจ้งเตือนวันนี้ทันที", type="primary", use_container_width=True, disabled=targets_today.empty):
                send_reminder_batch(targets_today, "วันนี้", today_formatted)

    # 7. จัดการ Blacklist
    elif menu == "🚫 จัดการ Blacklist":
        st.subheader("🚫 การจัดการระงับสิทธิ์การจองคิว (Blacklist)")
        tab_bl1, tab_bl2 = st.tabs(["📋 รายชื่อผู้ถูกระงับสิทธิ์ & ปลดบล็อก", "➕ สั่งระงับสิทธิ์ (บล็อกผู้ป่วย)"])
        
        with tab_bl1:
            df_bl = get_table_df("blacklist")
            if not df_bl.empty:
                st.dataframe(df_bl.sort_values(by=['blacklisted_until'], ascending=False), use_container_width=True)
                st.markdown("---")
                st.markdown("##### 🔓 ปลดบล็อกผู้ป่วย (คืนสิทธิ์การจอง)")
                col_ub1, col_ub2 = st.columns([3, 1])
                unblock_id = col_ub1.number_input("ระบุ 'รหัส (ID)' ในตารางที่ต้องการปลดบล็อก", min_value=1, step=1)
                col_ub2.markdown("### ")
                if col_ub2.button("🔓 ปลดบล็อกทันที", type="primary", use_container_width=True):
                    ws_bl = sh.worksheet("blacklist")
                    if (df_bl['id'].astype(str) == str(unblock_id)).any():
                        df_kept = df_bl[df_bl['id'].astype(str) != str(unblock_id)]
                        ws_bl.clear()
                        safe_append_row(ws_bl, TABLE_SCHEMAS["blacklist"])
                        if not df_kept.empty:
                            safe_append_rows(ws_bl, df_kept[TABLE_SCHEMAS["blacklist"]].values.tolist())
                        st.cache_data.clear()
                        st.success(f"✅ ปลดบล็อกรหัส {unblock_id} เรียบร้อยแล้ว")
                        st.rerun()
                    else:
                        st.warning(f"ไม่พบข้อมูลรหัส {unblock_id}")
            else:
                st.info("ไม่มีรายชื่อผู้ถูกระงับสิทธิ์ในขณะนี้")
                
        with tab_bl2:
            st.markdown("##### ➕ เพิ่มรายชื่อผู้ป่วยเข้าสู่ระบบระงับสิทธิ์")
            df_p = get_table_df("patients")
            with st.form("form_manual_blacklist"):
                if not df_p.empty:
                    patient_options = {row['id']: f"{row['full_name']} (บัตร: {row['id_card']}, โทร: {row['phone']})" for _, row in df_p.iterrows()}
                    selected_p_id = st.selectbox("เลือกผู้ป่วยจากประวัติในระบบ", options=list(patient_options.keys()), format_func=lambda x: patient_options[x])
                else:
                    st.warning("ยังไม่มีข้อมูลผู้ป่วยในระบบ")
                    selected_p_id = None
                
                col_b1, col_b2 = st.columns([2, 1])
                bl_reason = col_b1.text_input("ระบุสาเหตุการระงับสิทธิ์ *", placeholder="เช่น ไม่มาตามนัดหลายครั้ง, ก่อกวนระบบ, ขอยกเลิกสิทธิ์ชั่วคราว")
                penalty_days = col_b2.selectbox("ระยะเวลาที่ต้องการระงับสิทธิ์", [
                    (30, "30 วัน (1 เดือน)"),
                    (60, "60 วัน (2 เดือน)"),
                    (90, "90 วัน (3 เดือน)"),
                    (180, "180 วัน (6 เดือน)"),
                    (365, "365 วัน (1 ปี)")
                ], format_func=lambda x: x[1])
                
                if st.form_submit_button("🚫 ยืนยันการสั่งระงับสิทธิ์ (บล็อก)", type="primary"):
                    if not selected_p_id:
                        st.error("กรุณาเลือกผู้ป่วย")
                    elif not bl_reason.strip():
                        st.error("กรุณาระบุสาเหตุการระงับสิทธิ์")
                    else:
                        success = add_to_blacklist(
                            patient_id=selected_p_id, 
                            reason=bl_reason.strip(), 
                            days_penalty=penalty_days[0], 
                            reported_by=st.session_state.admin_user
                        )
                        if success:
                            st.success("✅ บันทึกระงับสิทธิ์ผู้ป่วยเรียบร้อยแล้ว")
                            st.rerun()
                        else:
                            st.error("ไม่สามารถบันทึกได้ กรุณาลองใหม่อีกครั้ง")

    # 8. No-Show
    elif menu == "📋 ประวัติ No-Show":
        st.subheader("📋 บันทึกประวัติผู้ไม่มาตามนัดหมาย")
        df_ns = get_table_df("no_show_records")
        if not df_ns.empty:
            st.dataframe(df_ns.sort_values(by=['appointment_date'], ascending=False), use_container_width=True)
        else:
            st.info("ยังไม่มีประวัติการไม่มาตามนัด")

# ========== ฟังก์ชันจัดการลิงก์จากอีเมล ==========
def handle_final_confirmation():
    token = st.query_params.get("final_confirm")
    if not token:
        st.error("❌ ลิงก์ไม่ถูกต้อง")
        return
    sh = get_spreadsheet()
    if not sh:
        st.error("❌ ไม่สามารถเชื่อมต่อฐานข้อมูลได้")
        return
    
    ws_a = sh.worksheet("appointments")
    all_rows = ws_a.get_all_values()
    headers = [h.strip().lower() for h in all_rows[0]]
    token_col = headers.index("token") if "token" in headers else -1
    status_col = headers.index("status") + 1 if "status" in headers else -1
    
    found = False
    for idx, r in enumerate(all_rows[1:], start=2):
        if token_col != -1 and str(r[token_col]).strip() == str(token).strip():
            ws_a.update_cell(idx, status_col, "reconfirmed")
            st.cache_data.clear()
            found = True
            st.markdown(f"""
            <div style="text-align: center; padding: 40px 20px; background: #f0fdf4; border: 2px solid #86efac; border-radius: 16px; margin: 30px auto; max-width: 600px;">
                <h1 style="color: #166534; font-size: 26px;">✅ ยืนยันการเข้ารับบริการสำเร็จ</h1>
                <p style="font-size: 16px; color: #1e293b;">ระบบได้บันทึกการยืนยันของท่านเรียบร้อยแล้ว กรุณามาติดต่อห้องเวชระเบียนตามเวลาที่ระบุในใบนัด</p>
            </div>
            """, unsafe_allow_html=True)
            break
            
    if not found:
        st.warning("⚠️ ไม่พบข้อมูลนัดหมาย หรือลิงก์นี้ถูกใช้งาน/ยกเลิกไปแล้ว")

def handle_cancellation():
    token = st.query_params.get("cancel")
    if not token:
        st.error("❌ ลิงก์ไม่ถูกต้อง")
        return
    sh = get_spreadsheet()
    if not sh:
        st.error("❌ ไม่สามารถเชื่อมต่อฐานข้อมูลได้")
        return
        
    ws_a = sh.worksheet("appointments")
    all_rows = ws_a.get_all_values()
    headers = [h.strip().lower() for h in all_rows[0]]
    token_col = headers.index("token") if "token" in headers else -1
    status_col = headers.index("status") + 1 if "status" in headers else -1
    
    found = False
    for idx, r in enumerate(all_rows[1:], start=2):
        if token_col != -1 and str(r[token_col]).strip() == str(token).strip():
            ws_a.update_cell(idx, status_col, "cancelled")
            st.cache_data.clear()
            found = True
            st.markdown(f"""
            <div style="text-align: center; padding: 40px 20px; background: #fef2f2; border: 2px solid #fca5a5; border-radius: 16px; margin: 30px auto; max-width: 600px;">
                <h1 style="color: #991b1b; font-size: 26px;">❌ ยกเลิกการนัดหมายสำเร็จ</h1>
                <p style="font-size: 16px; color: #1e293b;">ระบบได้ทำการยกเลิกนัดและคืนคิวว่างเรียบร้อยแล้ว ขอบพระคุณที่แจ้งให้เราทราบล่วงหน้า</p>
            </div>
            """, unsafe_allow_html=True)
            break
            
    if not found:
        st.warning("⚠️ ไม่พบข้อมูลนัดหมาย หรือรายการนี้ถูกยกเลิกไปแล้ว")

# ========== ควบคุมการทำงานหลัก ==========
def main():
    sh = get_spreadsheet()
    
    if not sh:
        st.error("⚠️ **ยังไม่ได้เชื่อมต่อ Google Sheets**")
        st.info("""
        **ขั้นตอนการเปิดใช้งานฐานข้อมูลถาวร:**
        1. นำ `gspread`, `google-auth` และ `reportlab` ไปใส่ในไฟล์ `requirements.txt` บน GitHub
        2. ใส่การตั้งค่า `[sheets]` (URL ของ Google Sheet) และ `[gcp_service_account]` ในเมนู **Settings > Secrets** ของ Streamlit Cloud
        3. กดแชร์ Google Sheet แผ่นนั้นให้กับอีเมล Service Account ให้มีสิทธิ์เป็น **Editor (ผู้แก้ไข)**
        """)
        return

    ensure_worksheets_initialized(sh)

    if 'final_confirm' in st.query_params:
        handle_final_confirmation()
        return
    elif 'cancel' in st.query_params:
        handle_cancellation()
        return

    st.sidebar.markdown("### 🦷 ศบส.65 รักษาศุข บางบอน")
    page = st.sidebar.radio("เลือกหน้าต่างทำงาน", ["📅 นัดหมายบริการ", "⚙️ ผู้ดูแลระบบ"])
    
    if page == "📅 นัดหมายบริการ":
        show_booking_form()
    elif page == "⚙️ ผู้ดูแลระบบ":
        show_admin_dashboard()

if __name__ == "__main__":
    main()