from datetime import date, datetime, time, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import io
import os
import secrets
import smtplib
import threading
import urllib.request

from google.oauth2.service_account import Credentials
import gspread
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
import streamlit as st

# ตัวล็อกระดับโปรเซส ป้องกันการแย่งกันเขียนฐานข้อมูลเมื่อกดย้ำ (Race Condition Lock)
BOOKING_LOCK = threading.Lock()

# ========== คอนฟิกหน้าเว็บ ==========
st.set_page_config(
    page_title="ระบบจองคิวทันตกรรม ศูนย์บริการสาธารณสุข 65 รักษาศุข บางบอน",
    page_icon="🦷",
    layout="wide",
    initial_sidebar_state="expanded",
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
</style>"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

SERVICES = ["ถอนฟัน", "อุดฟัน", "ขูดหินปูน", "ตรวจสุขภาพช่องปาก"]

DEFAULT_ADMIN_CREDENTIALS = {
    "arsm": "4969",
    "kn1": "kn1",
    "dental665": "dent665",
}


def verify_admin_login(username: str, password: str) -> bool:
    if not username or not password:
        return False
    u = username.strip().lower().replace(" ", "")
    p = password.strip()

    if "admin_users" in st.secrets:
        try:
            sec_users = {
                str(k).strip().lower().replace(" ", ""): str(v).strip()
                for k, v in st.secrets["admin_users"].items()
            }
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
    "appointments": [
        "id",
        "patient_id",
        "full_name",
        "id_card",
        "phone",
        "email",
        "service_type",
        "appointment_date",
        "appointment_time",
        "status",
        "token",
        "reminder_sent",
        "notes",
        "created_at",
    ],
    "patients": ["id", "full_name", "id_card", "phone", "email", "created_at"],
    "daily_schedule": [
        "id",
        "schedule_date",
        "is_open",
        "start_time",
        "end_time",
        "max_patients",
        "note",
    ],
    "blacklist": [
        "id",
        "patient_id",
        "full_name",
        "id_card",
        "phone",
        "reason",
        "no_show_count",
        "blacklisted_until",
        "created_by",
        "created_at",
    ],
    "no_show_records": [
        "id",
        "appointment_id",
        "patient_id",
        "appointment_date",
        "status",
        "reported_by",
        "notes",
        "created_at",
    ],
    "system_settings": ["setting_key", "setting_value", "updated_at"],
}


# ========== ฟังก์ชันเวลาประเทศไทย (UTC+7) ==========
def get_bangkok_now():
    return datetime.utcnow() + timedelta(hours=7)


def get_bangkok_today():
    return get_bangkok_now().date()


def clean_sheet_val(v):
    if pd.isna(v) or v is None:
        return ""
    if hasattr(v, "item"):
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
        return d_val.strftime("%Y-%m-%d")

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


def check_duplicate_appointment(
    all_appts_list, id_card, phone, full_name, appt_date_str
):
    clean_id = str(id_card).strip().replace("-", "").replace(" ", "")
    clean_phone = str(phone).strip().replace("-", "").replace(" ", "")
    clean_name = str(full_name).strip().replace(" ", "").lower()
    clean_date = normalize_date_str(appt_date_str)

    for row in all_appts_list:
        r_id = str(row.get("id_card", "")).strip().replace("-", "").replace(" ", "")
        r_phone = str(row.get("phone", "")).strip().replace("-", "").replace(" ", "")
        r_name = str(row.get("full_name", "")).strip().replace(" ", "").lower()
        r_status = str(row.get("status", "")).strip().lower()
        r_date = normalize_date_str(row.get("appointment_date", ""))

        if r_status not in ["pending", "confirmed", "reconfirmed"]:
            continue

        if clean_id and r_id:
            if clean_id == r_id or (
                clean_id.lstrip("0") == r_id.lstrip("0") and len(clean_id) > 6
            ):
                return True, row

        if clean_phone and r_phone:
            if clean_phone == r_phone or (
                clean_phone.lstrip("0") == r_phone.lstrip("0") and len(clean_phone) > 6
            ):
                return True, row

        if clean_name and r_name and (clean_name == r_name):
            if clean_date == r_date:
                return True, row

    return False, None


def get_system_status() -> bool:
    df_settings = get_table_df("system_settings")
    if df_settings.empty or "setting_key" not in df_settings.columns:
        return True
    row = df_settings[
        df_settings["setting_key"].astype(str) == "booking_system_status"
    ]
    if row.empty:
        return True
    val = str(row.iloc[0].get("setting_value", "open")).strip().lower()
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
    now_str = get_bangkok_now().strftime("%Y-%m-%d %H:%M:%S")
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
        v_col = (
            headers.index("setting_value") + 1
            if "setting_value" in headers
            else 2
        )
        t_col = (
            headers.index("updated_at") + 1 if "updated_at" in headers else 3
        )
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
            <li><b>การยืนยันนัด:</b> ผู้รับบริการต้อง <b>ยืนยันนัดหมายใน E- mail ที่ส่งให้ท่าน ก่อนเข้ารับบริการ 1 วัน หรือโทรยืนยันนัดหมาย เบอร์ 02 453 0526 ต่อ 302</b></li>
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
            urllib.request.urlretrieve(
                "https://raw.githubusercontent.com/google/fonts/main/ofl/sarabun/Sarabun-Regular.ttf",
                font_reg,
            )
        except Exception:
            pass
    if not os.path.exists(font_bold):
        try:
            urllib.request.urlretrieve(
                "https://raw.githubusercontent.com/google/fonts/main/ofl/sarabun/Sarabun-Bold.ttf",
                font_bold,
            )
        except Exception:
            pass

    has_font = False
    if os.path.exists(font_reg):
        try:
            pdfmetrics.registerFont(TTFont("Sarabun", font_reg))
            has_font = True
        except Exception:
            pass
    if os.path.exists(font_bold):
        try:
            pdfmetrics.registerFont(TTFont("Sarabun-Bold", font_bold))
            has_font = True
        except Exception:
            pass
    return has_font


def generate_daily_appointments_pdf(
    df_day: pd.DataFrame, target_date: date
) -> bytes:
    has_font = init_pdf_fonts()
    font_name = "Sarabun" if has_font else "Helvetica"
    font_bold = "Sarabun-Bold" if has_font else "Helvetica-Bold"

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=30,
        leftMargin=30,
        topMargin=30,
        bottomMargin=30,
    )

    elements = []
    style_title = ParagraphStyle(
        "TitleStyle",
        fontName=font_bold,
        fontSize=15,
        leading=19,
        alignment=1,
        textColor=colors.HexColor("#0369a1"),
    )
    style_sub = ParagraphStyle(
        "SubTitleStyle",
        fontName=font_bold,
        fontSize=12,
        leading=16,
        alignment=1,
        textColor=colors.HexColor("#1e293b"),
    )
    style_meta = ParagraphStyle(
        "MetaStyle",
        fontName=font_name,
        fontSize=9,
        leading=12,
        alignment=2,
        textColor=colors.HexColor("#64748b"),
    )
    style_th = ParagraphStyle(
        "THStyle",
        fontName=font_bold,
        fontSize=9,
        leading=11,
        alignment=1,
        textColor=colors.white,
    )
    style_td = ParagraphStyle(
        "TDStyle",
        fontName=font_name,
        fontSize=8.5,
        leading=11,
        alignment=0,
        textColor=colors.HexColor("#1e293b"),
    )
    style_td_center = ParagraphStyle(
        "TDCenterStyle",
        fontName=font_name,
        fontSize=8.5,
        leading=11,
        alignment=1,
        textColor=colors.HexColor("#1e293b"),
    )

    d_be = target_date.strftime("%d/%m/") + str(target_date.year + 543)
    now_be = (
        get_bangkok_now().strftime("%d/%m/")
        + str(get_bangkok_now().year + 543)
        + get_bangkok_now().strftime(" %H:%M น.")
    )

    elements.append(
        Paragraph("ศูนย์บริการสาธารณสุข 65 รักษาศุข บางบอน", style_title)
    )
    elements.append(Spacer(1, 3))
    elements.append(
        Paragraph(
            f"ใบรายชื่อผู้เข้ารับบริการทันตกรรม ประจำวันที่ {d_be}", style_sub
        )
    )
    elements.append(Spacer(1, 3))
    elements.append(
        Paragraph(
            f"พิมพ์รายงานเมื่อ: {now_be} | รวมทั้งหมด: {len(df_day)} ท่าน",
            style_meta,
        )
    )
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
        "reconfirmed": "🟢 ยืนยันแล้ว (มาแน่นอน)",
        "confirmed": "🟡 ลงทะเบียนสำเร็จ",
        "pending": "⚪ รอยืนยัน",
        "completed": "✅ รับบริการแล้ว",
        "no_show": "🔴 ไม่มาตามนัด",
        "cancelled": "❌ ยกเลิก",
    }

    for idx, (_, row) in enumerate(df_day.iterrows(), start=1):
        appt_time = str(row.get("appointment_time", ""))
        arrival_t = get_arrival_time_str(appt_time)
        full_name = str(row.get("full_name", ""))
        service = str(row.get("service_type", ""))
        phone = str(row.get("phone", ""))
        raw_stat = str(row.get("status", ""))
        stat_th = status_map.get(raw_stat, raw_stat)
        notes = (
            str(row.get("notes", ""))
            if row.get("notes") and str(row.get("notes")).strip() != "None"
            else ""
        )

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
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0284c7")),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]

    for r_idx in range(1, len(table_data)):
        if r_idx % 2 == 0:
            t_style.append(
                ("BACKGROUND", (0, r_idx), (-1, r_idx), colors.HexColor("#f8fafc"))
            )
        else:
            t_style.append(("BACKGROUND", (0, r_idx), (-1, r_idx), colors.white))

    table.setStyle(TableStyle(t_style))
    elements.append(table)

    doc.build(elements)
    buffer.seek(0)
    return buffer.getvalue()


@st.cache_resource
def get_gspread_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    if "gcp_service_account" in st.secrets:
        creds = Credentials.from_service_account_info(
            st.secrets["gcp_service_account"], scopes=scopes
        )
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
                row = row[: len(headers)]
            norm_data.append(row)

        df = pd.DataFrame(norm_data, columns=headers)
        df = df.loc[:, [c for c in df.columns if c != ""]]

        expected = TABLE_SCHEMAS.get(table_name, [])
        for col in expected:
            if col not in df.columns:
                df[col] = None

        if not df.empty:
            if "id" in df.columns:
                df = df[df["id"].astype(str).str.strip() != ""]
            elif "setting_key" in df.columns:
                df = df[df["setting_key"].astype(str).str.strip() != ""]
        return df
    except Exception as e:
        st.error(f"⚠️ เกิดข้อผิดพลาดในการโหลดตาราง {table_name}: {e}")
        return pd.DataFrame(columns=TABLE_SCHEMAS.get(table_name, []))


def send_email(
    to_email: str,
    subject: str,
    body: str,
    cc_email: str = "dental665@gmail.com",
) -> bool:
    if not to_email or not str(to_email).strip():
        return False
    try:
        sender_email = st.secrets["email"]["sender"]
        sender_password = st.secrets["email"]["password"]

        msg = MIMEMultipart()
        msg["From"] = f"คลินิกทันตกรรม ศบส.65 <{sender_email}>"
        msg["To"] = to_email.strip()
        msg["Cc"] = cc_email
        msg["Reply-To"] = "dental665@gmail.com"
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "html"))

        recipients = [to_email.strip()]
        if cc_email and cc_email != to_email.strip():
            recipients.append(cc_email)

        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as server:
            server.login(sender_email, sender_password)
            server.send_message(msg, to_addrs=recipients)
        return True
    except KeyError:
        st.warning("⚠️ ไม่พบคีย์การตั้งค่าอีเมลใน .streamlit/secrets.toml")
        return False
    except Exception as e:
        st.error(f"❌ ส่งอีเมลล้มเหลว: {str(e)}")
        return False


def send_booking_confirmation_email(
    to_email: str,
    full_name: str,
    service_name: str,
    appointment_date_str: str,
    norm_time_label: str,
    arrival_time_str: str,
    token: str,
) -> bool:
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
    return send_email(
        to_email.strip(),
        "ท่านได้ลงทะเบียนนัดหมายบริการทันตกรรมสำเร็จ",
        email_body,
    )


def check_blacklist(id_card=None, phone=None, patient_id=None):
    df_bl = get_table_df("blacklist")
    if df_bl.empty:
        return None
    today_str = get_bangkok_today().strftime("%Y-%m-%d")
    active_bl = df_bl[df_bl["blacklisted_until"].astype(str) >= today_str]

    if id_card:
        match = active_bl[active_bl["id_card"].astype(str) == str(id_card).strip()]
        if not match.empty:
            return match.iloc[0].to_dict()
    if phone:
        match = active_bl[active_bl["phone"].astype(str) == str(phone).strip()]
        if not match.empty:
            return match.iloc[0].to_dict()
    if patient_id:
        match = active_bl[active_bl["patient_id"].astype(str) == str(patient_id)]
        if not match.empty:
            return match.iloc[0].to_dict()
    return None


def add_to_blacklist(
    patient_id, reason, days_penalty=30, reported_by="system"
):
    sh = get_spreadsheet()
    df_p = get_table_df("patients")
    match_p = df_p[df_p["id"].astype(str) == str(patient_id)]
    if match_p.empty:
        return False
    p_info = match_p.iloc[0]

    ws_bl = sh.worksheet("blacklist")
    df_bl = get_table_df("blacklist")
    existing = check_blacklist(patient_id=patient_id)

    until_d = (get_bangkok_today() + timedelta(days=days_penalty)).strftime(
        "%Y-%m-%d"
    )
    now_str = get_bangkok_now().strftime("%Y-%m-%d %H:%M:%S")

    if existing:
        new_cnt = int(existing.get("no_show_count", 1)) + 1
        all_rows = ws_bl.get_all_values()
        headers = [h.strip().lower() for h in all_rows[0]]
        p_id_col = headers.index("patient_id")
        for idx, row in enumerate(all_rows[1:], start=2):
            if row[p_id_col].strip() == str(patient_id).strip():
                ws_bl.update_cell(idx, headers.index("no_show_count") + 1, new_cnt)
                ws_bl.update_cell(idx, headers.index("blacklisted_until") + 1, until_d)
                ws_bl.update_cell(
                    idx, headers.index("reason") + 1, f"{reason} (ครั้งที่ {new_cnt})"
                )
                break
    else:
        next_id = (
            int(pd.to_numeric(df_bl["id"], errors="coerce").fillna(0).max()) + 1
            if not df_bl.empty
            else 1
        )
        safe_append_row(ws_bl, [
            next_id,
            int(patient_id),
            str(p_info.get("full_name")),
            str(p_info.get("id_card")),
            str(p_info.get("phone")),
            reason,
            1,
            until_d,
            reported_by,
            now_str,
        ])
    st.cache_data.clear()
    return True


def record_no_show(appointment_id, reported_by="system", notes=""):
    sh = get_spreadsheet()
    df_appts = get_table_df("appointments")
    match_a = df_appts[df_appts["id"].astype(str) == str(appointment_id)]
    if match_a.empty:
        return False

    a_info = match_a.iloc[0]
    patient_id = a_info.get("patient_id")
    appt_date = a_info.get("appointment_date")

    ws_a = sh.worksheet("appointments")
    all_rows = ws_a.get_all_values()
    headers = [h.strip().lower() for h in all_rows[0]]
    id_col = headers.index("id")
    status_col = headers.index("status") + 1

    for idx, row in enumerate(all_rows[1:], start=2):
        if row[id_col].strip() == str(appointment_id).strip():
            ws_a.update_cell(idx, status_col, "no_show")
            break

    ws_ns = sh.worksheet("no_show_records")
    df_ns = get_table_df("no_show_records")
    next_id = (
        int(pd.to_numeric(df_ns["id"], errors="coerce").fillna(0).max()) + 1
        if not df_ns.empty
        else 1
    )
    now_str = get_bangkok_now().strftime("%Y-%m-%d %H:%M:%S")
    safe_append_row(ws_ns, [
        next_id,
        int(appointment_id),
        int(patient_id),
        appt_date,
        "no_show",
        reported_by,
        notes,
        now_str,
    ])
    st.cache_data.clear()

    cutoff_date = (get_bangkok_today() - timedelta(days=90)).strftime("%Y-%m-%d")
    df_ns_all = get_table_df("no_show_records")
    patient_no_shows = df_ns_all[
        (df_ns_all["patient_id"].astype(str) == str(patient_id))
        & (df_ns_all["appointment_date"].astype(str) >= cutoff_date)
        & (df_ns_all["status"] == "no_show")
    ]
    if len(patient_no_shows) >= 2:
        add_to_blacklist(
            patient_id,
            f"ไม่มาตามนัด {len(patient_no_shows)} ครั้งในรอบ 90 วัน",
            days_penalty=30 * len(patient_no_shows),
            reported_by="auto_system",
        )
    return True


def batch_update_appointments_status(
    selected_ids, target_status, reported_by="admin"
):
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
            record_no_show(
                appt_id,
                reported_by=reported_by,
                notes="เจ้าหน้าที่ระบุไม่มาตามนัด (แบบกลุ่ม)",
            )
            count += 1
        else:
            for idx, row in enumerate(all_rows[1:], start=2):
                if row[id_col].strip() == str(appt_id).strip():
                    ws_a.update_cell(idx, status_col, target_status)
                    count += 1
                    break
    st.cache_data.clear()
    return count


def get_available_slots(
    appointment_date: date, allow_admin_override: bool = False
):
    today_dt = get_bangkok_today()
    now_dt = get_bangkok_now()

    if (
        not allow_admin_override
        and appointment_date == today_dt
        and now_dt.time() >= time(15, 30)
    ):
        return [], "หมดเวลาจองนัดหมายรับบริการแล้ว"

    date_str = appointment_date.strftime("%Y-%m-%d")
    df_sched = get_table_df("daily_schedule")
    if df_sched.empty:
        return [], "คลินิกยังไม่ได้เปิดรับจองในวันนี้"

    df_sched["norm_date"] = df_sched["schedule_date"].apply(normalize_date_str)
    day_sched = df_sched[df_sched["norm_date"] == date_str]
    if day_sched.empty:
        return [], "คลินิกยังไม่ได้เปิดรับจองในวันนี้"

    if (day_sched["is_open"].astype(int) == 0).any():
        close_row = day_sched[day_sched["is_open"].astype(int) == 0].iloc[0]
        note = close_row.get("note", "ปิดทำการพิเศษ")
        return [], f"ปิดทำการ ({note})"

    df_appts = get_table_df("appointments")
    if not df_appts.empty:
        df_appts["norm_date"] = df_appts["appointment_date"].apply(
            normalize_date_str
        )
        df_appts["norm_time"] = df_appts["appointment_time"].apply(
            normalize_time_slot
        )
        df_appts["norm_status"] = (
            df_appts["status"].astype(str).str.strip().str.lower()
        )

        active_appts = df_appts[
            (df_appts["norm_date"] == date_str)
            & (
                df_appts["norm_status"].isin(
                    ["pending", "confirmed", "reconfirmed", "completed"]
                )
            )
            & (df_appts["id_card"].astype(str).str.strip() != "")
        ]
    else:
        active_appts = pd.DataFrame(columns=["norm_time"])

    all_slots = []
    day_sched_sorted = day_sched.sort_values(by=["start_time"])
    for _, row in day_sched_sorted.iterrows():
        s_t = str(row.get("start_time", "")).strip()
        e_t = str(row.get("end_time", "")).strip()
        if not s_t or not e_t:
            continue
        raw_slot_label = f"{s_t} - {e_t}"
        norm_slot = normalize_time_slot(raw_slot_label)
        max_cap = int(row.get("max_patients", 4))

        booked_count = (
            len(active_appts[active_appts["norm_time"] == norm_slot])
            if not active_appts.empty
            else 0
        )

        if booked_count < max_cap:
            all_slots.append({
                "label": norm_slot,
                "available": max(0, max_cap - booked_count),
                "total_slots": max_cap,
            })

    if not all_slots:
        return [], "คิวนัดหมายเต็มแล้ว"

    return all_slots, "เปิดทำการ"


def show_booking_form():
    # ---------- [NEW: Initialize submitting state] ----------
    if "is_submitting" not in st.session_state:
        st.session_state.is_submitting = False

    def disable_submit_button():
        st.session_state.is_submitting = True
    # --------------------------------------------------------

    is_sys_open = get_system_status()
    is_admin_logged_in = bool(st.session_state.get("admin_user"))

    if not is_sys_open:
        if not is_admin_logged_in:
            st.markdown(
                """
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
            """,
                unsafe_allow_html=True,
            )

            with st.expander(
                "🔑 เข้าสู่ระบบสำหรับเจ้าหน้าที่ (เพื่อทดสอบระบบขณะปิดปรับปรุง)"
            ):
                test_username = st.text_input(
                    "ชื่อผู้ใช้งาน (Username)", key="test_admin_user"
                )
                test_pwd = st.text_input("รหัสผ่าน", type="password", key="test_admin_pwd")
                if st.button(
                    "🔓 เข้าสู่โหมดทดสอบการจอง",
                    type="primary",
                    use_container_width=True,
                ):
                    if verify_admin_login(test_username, test_pwd):
                        st.session_state.admin_user = (
                            test_username.strip().lower().replace(" ", "")
                        )
                        st.session_state.is_admin = True
                        st.success("เข้าสู่โหมดทดสอบสำเร็จ!")
                        st.rerun()
                    else:
                        st.error("❌ ชื่อผู้ใช้งานหรือรหัสผ่านไม่ถูกต้อง")
            return
        else:
            st.markdown(
                f"""
            <div style="background-color: #fef2f2; border: 2px solid #ef4444; border-radius: 12px; padding: 14px 20px; margin-bottom: 20px;">
                <span style="font-size: 1.1rem; color: #b91c1c; font-weight: bold;">
                    🛠️ ขณะนี้ระบบปิดปรับปรุงอยู่ (บุคคลภายนอกไม่สามารถเข้าใช้งานได้)
                </span><br>
                <span style="font-size: 0.92rem; color: #7f1d1d;">
                    คุณเข้าสู่ระบบในฐานะเจ้าหน้าที่ <b>{st.session_state.admin_user}</b> สามารถทดสอบการลงทะเบียนจองและระบบอีเมลได้ตามปกติ
                </span>
            </div>
            """,
                unsafe_allow_html=True,
            )

    if "just_booked_data" in st.session_state and st.session_state.just_booked_data:
        bk = st.session_state.just_booked_data
        st.markdown(
            f"""
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
        """,
            unsafe_allow_html=True,
        )

        if st.button(
            "🏠 เสร็จสิ้น / จองรายการอื่นเพิ่มเติม",
            type="primary",
            use_container_width=True,
        ):
            st.session_state.just_booked_data = None
            st.session_state.is_submitting = False # Reset status
            st.rerun()
        return

    st.markdown(
        """<div class="hero-banner">
        <h1>🦷 ระบบจองคิวทันตกรรม</h1>
        <p>ศูนย์บริการสาธารณสุข 65 รักษาศุข บางบอน<br>กรุณากรอกข้อมูลส่วนตัว เลือกบริการ และนัดหมายวันเวลาที่สะดวกเข้ารับบริการ</p>
    </div>""",
        unsafe_allow_html=True,
    )

    with st.container(border=True):
        st.subheader("1. ข้อมูลผู้เข้ารับบริการ")
        col1, col2 = st.columns(2)
        with col1:
            full_name = st.text_input(
                "ชื่อ - นามสกุล *", placeholder="ระบุชื่อและนามสกุลจริง"
            )
            id_card = st.text_input(
                "เลขประจำตัวประชาชน (13 หลัก) *",
                placeholder="xxxxxxxxxxxxx",
                max_chars=13,
            )
        with col2:
            phone = st.text_input(
                "เบอร์โทรศัพท์ติดต่อ *", placeholder="08xxxxxxxx", max_chars=10
            )
            email = st.text_input(
                "อีเมลสำหรับรับการยืนยัน *", placeholder="your_email@example.com"
            )

        st.markdown("<br>", unsafe_allow_html=True)
        st.subheader("2. เลือกบริการและวันเวลา")

        service_name = st.selectbox("บริการที่ต้องการรับการรักษา *", SERVICES)

        today_bkk = get_bangkok_today()
        now_bkk = get_bangkok_now()
        is_past_today_cutoff = now_bkk.time() >= time(15, 30)

        col_date, col_slot = st.columns(2)
        with col_date:
            min_date = today_bkk
            max_date = min_date + timedelta(days=120)
            default_date = (
                today_bkk + timedelta(days=1)
                if is_past_today_cutoff
                else today_bkk
            )
            appointment_date = st.date_input(
                "เลือกวันที่ต้องการนัดหมาย *",
                min_value=min_date,
                max_value=max_date,
                value=default_date,
            )

            if appointment_date == today_bkk and is_past_today_cutoff:
                st.warning(
                    "⛔ ปิดรับจองออนไลน์สำหรับวันนี้แล้วตั้งแต่เวลา 15.30 น."
                    " เพื่อสรุปยอดคิว โปรดจองวันรับบริการวันถัดไป"
                )

        available_slots, status_msg = get_available_slots(
            appointment_date, allow_admin_override=False
        )

        with col_slot:
            if not available_slots:
                st.selectbox("ช่วงเวลา *", [f"⛔ {status_msg}"], disabled=True)
                selected_time_slot = None
            else:
                slot_options = [
                    f"{slot['label']} น. (ว่าง"
                    f" {slot['available']}/{slot['total_slots']} คิว)"
                    for slot in available_slots
                ]
                selected_time_slot = st.selectbox(
                    "เลือกช่วงเวลานัดหมาย *", slot_options
                )

        notes = st.text_area("หมายเหตุเพิ่มเติม / อาการเบื้องต้น / โรคประจำตัว (ถ้ามี)")
        st.markdown(TERMS_AND_CONDITIONS_HTML, unsafe_allow_html=True)
        agree_terms = st.checkbox(
            "ข้าพเจ้าได้อ่านและยอมรับเงื่อนไขและข้อตกลงการเข้ารับบริการนัดหมายออนไลน์ข้างต้น"
            " *"
        )
        st.markdown("<br>", unsafe_allow_html=True)

        # ---------- [NEW: Button with Disable State] ----------
        submitted = st.button(
            "📅 ยืนยันข้อมูลและส่งคำขอจองคิว",
            type="primary",
            use_container_width=True,
            on_click=disable_submit_button,
            disabled=st.session_state.is_submitting
        )
        # ------------------------------------------------------

    if submitted:
        if not agree_terms:
            st.error(
                "❌"
                " กรุณาทำเครื่องหมายถูกเพื่อยอมรับเงื่อนไขและข้อตกลงก่อนส่งคำขอจองคิว"
            )
            st.session_state.is_submitting = False
            return

        if not available_slots or not selected_time_slot:
            st.error(f"❌ วันที่เลือกไม่สามารถจองได้: {status_msg}")
            st.session_state.is_submitting = False
            return

        if not all(
            [full_name.strip(), id_card.strip(), phone.strip(), email.strip()]
        ):
            st.error("❌ กรุณากรอกข้อมูลที่มีเครื่องหมาย * ให้ครบทุกช่อง")
            st.session_state.is_submitting = False
            return

        if len(id_card.strip()) != 13 or not id_card.strip().isdigit():
            st.error("❌ เลขประจำตัวประชาชนต้องเป็นตัวเลข 13 หลักเท่านั้น")
            st.session_state.is_submitting = False
            return

        if check_blacklist(id_card=id_card.strip()):
            st.error(
                "⚠️"
                " บัญชีนี้ถูกระงับสิทธิ์ชั่วคราวเนื่องจากไม่มาตามเวลานัดหมาย"
                " กรุณาติดต่อคลินิก"
            )
            st.session_state.is_submitting = False
            return
        if check_blacklist(phone=phone.strip()):
            st.error("⚠️ เบอร์โทรศัพท์นี้ถูกระงับสิทธิ์ชั่วคราว กรุณาติดต่อคลินิก")
            st.session_state.is_submitting = False
            return

        clean_time_label = selected_time_slot.split(" น.")[0]
        norm_time_label = normalize_time_slot(clean_time_label)
        date_str = appointment_date.strftime("%Y-%m-%d")
        arrival_time_str = get_arrival_time_str(norm_time_label)

        booking_source_tag = "[จองผ่านระบบออนไลน์]"
        final_user_notes = (
            f"{booking_source_tag} {notes}".strip()
            if notes
            else booking_source_tag
        )

        with st.spinner(
            "⏳ กำลังบันทึกข้อมูลและออกใบนัดหมาย กรุณารอสักครู่"
            " (อย่ากดย้ำหรือปิดหน้าจอ)..."
        ):
            with BOOKING_LOCK:
                st.cache_data.clear()
                df_appts_fresh = get_table_df("appointments")
                all_appts_list = (
                    df_appts_fresh.to_dict("records")
                    if not df_appts_fresh.empty
                    else []
                )

                is_dup, dup_row = check_duplicate_appointment(
                    all_appts_list, id_card, phone, full_name, date_str
                )
                if is_dup:
                    st.cache_data.clear()
                    st.session_state.just_booked_data = {
                        "full_name": full_name.strip(),
                        "service_type": dup_row.get("service_type", service_name),
                        "appointment_date": dup_row.get("appointment_date", date_str),
                        "appointment_time": dup_row.get(
                            "appointment_time", norm_time_label
                        ),
                        "arrival_time": get_arrival_time_str(
                            dup_row.get("appointment_time", norm_time_label)
                        ),
                        "email": email.strip(),
                    }
                    st.session_state.is_submitting = False
                    st.rerun()

                sh = get_spreadsheet()
                ws_p = sh.worksheet("patients")
                df_p = get_table_df("patients")
                p_match = df_p[df_p["id_card"].astype(str) == id_card.strip()]
                now_str = get_bangkok_now().strftime("%Y-%m-%d %H:%M:%S")

                if not p_match.empty:
                    p_id = int(p_match.iloc[0]["id"])
                    all_p_rows = ws_p.get_all_values()
                    p_headers = [h.strip().lower() for h in all_p_rows[0]]
                    p_id_col = p_headers.index("id")
                    for idx, row in enumerate(all_p_rows[1:], start=2):
                        if row[p_id_col].strip() == str(p_id).strip():
                            ws_p.update_cell(
                                idx, p_headers.index("full_name") + 1, full_name.strip()
                            )
                            ws_p.update_cell(idx, p_headers.index("phone") + 1, phone.strip())
                            ws_p.update_cell(idx, p_headers.index("email") + 1, email.strip())
                            break
                else:
                    p_id = (
                        int(pd.to_numeric(df_p["id"], errors="coerce").fillna(0).max())
                        + 1
                        if not df_p.empty
                        else 1
                    )
                    safe_append_row(ws_p, [
                        p_id,
                        full_name.strip(),
                        id_card.strip(),
                        phone.strip(),
                        email.strip(),
                        now_str,
                    ])

                ws_a = sh.worksheet("appointments")
                next_appt_id = (
                    int(
                        pd.to_numeric(df_appts_fresh["id"], errors="coerce")
                        .fillna(0)
                        .max()
                    )
                    + 1
                    if not df_appts_fresh.empty
                    else 1
                )
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
                    "created_at": now_str,
                }
                append_appointment_mapped(ws_a, appt_dict)
                st.cache_data.clear()

            send_booking_confirmation_email(
                to_email=email.strip(),
                full_name=full_name.strip(),
                service_name=service_name,
                appointment_date_str=appointment_date.strftime("%d/%m/%Y"),
                norm_time_label=norm_time_label,
                arrival_time_str=arrival_time_str,
                token=token,
            )

            st.session_state.just_booked_data =