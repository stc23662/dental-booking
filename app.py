import streamlit as st
import pandas as pd
from datetime import datetime, date, time, timedelta
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import secrets
import gspread
from google.oauth2.service_account import Credentials

# ========== คอนฟิกหน้าเว็บ ==========
st.set_page_config(
    page_title="ระบบจองคิวทันตกรรม ศูนย์บริการสาธารณสุข 65 รักษาศุข บางบอน",
    page_icon="🦷",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ========== Custom CSS (ชิดซ้ายทั้งหมด ป้องกัน Markdown ตีความเป็น Code Block) ==========
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

# รายการบริการทันตกรรม 4 หัตถการ
SERVICES = ["ถอนฟัน", "อุดฟัน", "ขูดหินปูน", "ตรวจสุขภาพช่องปาก"]

# กำหนดโครงสร้างตารางข้อมูลใน Google Sheets
TABLE_SCHEMAS = {
    "appointments": ["id", "patient_id", "full_name", "id_card", "phone", "email", "service_type", "appointment_date", "appointment_time", "queue_number", "status", "token", "reminder_sent", "notes", "created_at"],
    "patients": ["id", "full_name", "id_card", "phone", "email", "created_at"],
    "daily_schedule": ["id", "schedule_date", "is_open", "start_time", "end_time", "max_patients", "note"],
    "blacklist": ["id", "patient_id", "full_name", "id_card", "phone", "reason", "no_show_count", "blacklisted_until", "created_by", "created_at"],
    "no_show_records": ["id", "appointment_id", "patient_id", "appointment_date", "status", "reported_by", "notes", "created_at"]
}

# ========== เชื่อมต่อ Google Sheets ==========
@st.cache_resource
def get_gspread_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    if "gcp_service_account" in st.secrets:
        creds = Credentials.from_service_account_info(
            st.secrets["gcp_service_account"],
            scopes=scopes
        )
        return gspread.authorize(creds)
    return None

def get_spreadsheet():
    gc = get_gspread_client()
    if not gc:
        return None
    sheet_url = st.secrets.get("sheets", {}).get("spreadsheet_url")
    if not sheet_url:
        return None
    try:
        sh = gc.open_by_url(sheet_url)
        # ตรวจสอบและสร้างแท็บอัตโนมัติหากยังไม่มี
        existing_sheets = [ws.title for ws in sh.worksheets()]
        for title, headers in TABLE_SCHEMAS.items():
            if title not in existing_sheets:
                ws = sh.add_worksheet(title=title, rows=1000, cols=len(headers) + 2)
                ws.append_row(headers)
        return sh
    except Exception as e:
        st.error(f"❌ ไม่สามารถเปิด Google Sheet ได้: {e}")
        return None

def get_table_df(sh, table_name):
    try:
        ws = sh.worksheet(table_name)
        records = ws.get_all_records()
        df = pd.DataFrame(records)
        expected_cols = TABLE_SCHEMAS[table_name]
        for col in expected_cols:
            if col not in df.columns:
                df[col] = None
        return df
    except Exception:
        return pd.DataFrame(columns=TABLE_SCHEMAS[table_name])

# ========== ฟังก์ชันส่งอีเมล ==========
def send_email(to_email: str, subject: str, body: str, cc_email: str = "dental665@gmail.com") -> bool:
    if not to_email or not str(to_email).strip():
        return False
    try:
        sender_email = st.secrets["email"]["sender"]
        sender_password = st.secrets["email"]["password"]
        
        msg = MIMEMultipart()
        msg['From'] = f"คลินิกทันตกรรม ศบส.65 <{sender_email}>"
        msg['To'] = to_email.strip()
        msg['Cc'] = cc_email
        msg['Reply-To'] = "dental665@gmail.com"
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

# ========== ฟังก์ชัน Blacklist ==========
def check_blacklist(sh, id_card=None, phone=None, patient_id=None):
    df_bl = get_table_df(sh, "blacklist")
    if df_bl.empty:
        return None
    today_str = date.today().strftime('%Y-%m-%d')
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

def add_to_blacklist(sh, patient_id, reason, days_penalty=30, reported_by="system"):
    df_p = get_table_df(sh, "patients")
    match_p = df_p[df_p['id'].astype(str) == str(patient_id)]
    if match_p.empty:
        return False
    p_info = match_p.iloc[0]
    
    ws_bl = sh.worksheet("blacklist")
    df_bl = get_table_df(sh, "blacklist")
    existing = check_blacklist(sh, patient_id=patient_id)
    
    until_d = (date.today() + timedelta(days=days_penalty)).strftime('%Y-%m-%d')
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    if existing:
        new_cnt = int(existing.get('no_show_count', 1)) + 1
        records = ws_bl.get_all_records()
        for idx, r in enumerate(records, start=2):
            if str(r.get('patient_id')) == str(patient_id):
                headers = ws_bl.row_values(1)
                ws_bl.update_cell(idx, headers.index('no_show_count') + 1, new_cnt)
                ws_bl.update_cell(idx, headers.index('blacklisted_until') + 1, until_d)
                ws_bl.update_cell(idx, headers.index('reason') + 1, f"{reason} (ครั้งที่ {new_cnt})")
                break
    else:
        next_id = int(df_bl['id'].max()) + 1 if not df_bl.empty and df_bl['id'].max() else 1
        ws_bl.append_row([
            next_id, patient_id, str(p_info.get('full_name')), str(p_info.get('id_card')), 
            str(p_info.get('phone')), reason, 1, until_d, reported_by, now_str
        ])
    return True

def record_no_show(sh, appointment_id, reported_by="system", notes=""):
    df_appts = get_table_df(sh, "appointments")
    match_a = df_appts[df_appts['id'].astype(str) == str(appointment_id)]
    if match_a.empty:
        return False
    
    a_info = match_a.iloc[0]
    patient_id = a_info.get('patient_id')
    appt_date = a_info.get('appointment_date')
    
    # อัปเดตสถานะใน appointments
    ws_a = sh.worksheet("appointments")
    records = ws_a.get_all_records()
    for idx, r in enumerate(records, start=2):
        if str(r.get('id')) == str(appointment_id):
            headers = ws_a.row_values(1)
            ws_a.update_cell(idx, headers.index('status') + 1, 'no_show')
            break
            
    # บันทึกประวัติ no_show_records
    ws_ns = sh.worksheet("no_show_records")
    df_ns = get_table_df(sh, "no_show_records")
    next_id = int(df_ns['id'].max()) + 1 if not df_ns.empty and df_ns['id'].max() else 1
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    ws_ns.append_row([next_id, appointment_id, patient_id, appt_date, 'no_show', reported_by, notes, now_str])
    
    # ตรวจสอบว่าใน 90 วันผิดนัดเกิน 2 ครั้งหรือไม่
    cutoff_date = (date.today() - timedelta(days=90)).strftime('%Y-%m-%d')
    df_ns_all = get_table_df(sh, "no_show_records")
    patient_no_shows = df_ns_all[
        (df_ns_all['patient_id'].astype(str) == str(patient_id)) &
        (df_ns_all['appointment_date'].astype(str) >= cutoff_date) &
        (df_ns_all['status'] == 'no_show')
    ]
    if len(patient_no_shows) >= 2:
        add_to_blacklist(
            sh, patient_id, 
            f"ไม่มาตามนัด {len(patient_no_shows)} ครั้งในรอบ 90 วัน", 
            days_penalty=30 * len(patient_no_shows), 
            reported_by="auto_system"
        )
    return True

# ========== คำนวณช่วงเวลาว่างของวันที่เลือก ==========
def get_available_slots(sh, appointment_date: date):
    date_str = appointment_date.strftime('%Y-%m-%d')
    df_sched = get_table_df(sh, "daily_schedule")
    if df_sched.empty:
        return [], "คลินิกยังไม่ได้เปิดรับจองในวันนี้"
        
    day_sched = df_sched[df_sched['schedule_date'].astype(str) == date_str]
    if day_sched.empty:
        return [], "คลินิกยังไม่ได้เปิดรับจองในวันนี้"
        
    if (day_sched['is_open'].astype(int) == 0).any():
        close_row = day_sched[day_sched['is_open'].astype(int) == 0].iloc[0]
        note = close_row.get('note', 'ปิดทำการพิเศษ')
        return [], f"ปิดทำการ ({note})"
        
    df_appts = get_table_df(sh, "appointments")
    active_appts = df_appts[
        (df_appts['appointment_date'].astype(str) == date_str) & 
        (df_appts['status'].isin(['pending', 'confirmed']))
    ]
    
    all_slots = []
    day_sched_sorted = day_sched.sort_values(by=['start_time'])
    for _, row in day_sched_sorted.iterrows():
        s_t = str(row.get('start_time', '')).strip()
        e_t = str(row.get('end_time', '')).strip()
        if not s_t or not e_t:
            continue
        slot_label = f"{s_t} - {e_t}"
        max_cap = int(row.get('max_patients', 4))
        booked_count = len(active_appts[active_appts['appointment_time'].astype(str) == slot_label])
        
        if booked_count < max_cap:
            all_slots.append({
                'label': slot_label,
                'available': max_cap - booked_count,
                'total_slots': max_cap
            })
            
    return all_slots, "เปิดทำการ"

# ========== หน้าจองคิวออนไลน์ (สำหรับคนไข้) ==========
def show_booking_form(sh):
    st.markdown("""<div class="hero-banner">
        <h1>🦷 ระบบจองคิวทันตกรรม</h1>
        <p>ศูนย์บริการสาธารณสุข 65 รักษาศุข บางบอน<br>กรุณากรอกข้อมูลส่วนตัว เลือกบริการ และนัดหมายวันเวลาที่สะดวกเข้ารับบริการ</p>
    </div>""", unsafe_allow_html=True)
    
    with st.container(border=True):
        st.subheader("1. ข้อมูลผู้เข้ารับบริการ")
        col1, col2 = st.columns(2)
        with col1:
            full_name = st.text_input("ชื่อ - นามสกุล *", placeholder="ระบุชื่อและนามสกุลจริง")
            id_card = st.text_input("เลขประจำตัวประชาชน (13 หลัก) *", placeholder="xxxxxxxxxxxxx", max_chars=13)
        with col2:
            phone = st.text_input("เบอร์โทรศัพท์ติดต่อ *", placeholder="08xxxxxxxx", max_chars=10)
            email = st.text_input("อีเมลสำหรับรับการยืนยัน *", placeholder="your_email@example.com")
            
        st.markdown("<br>", unsafe_allow_html=True)
        st.subheader("2. เลือกบริการและวันเวลา")
        
        service_name = st.selectbox("บริการที่ต้องการรับการรักษา *", SERVICES)
        
        col_date, col_slot = st.columns(2)
        with col_date:
            min_date = date.today()
            max_date = min_date + timedelta(days=120)
            appointment_date = st.date_input("เลือกวันที่ต้องการนัดหมาย *", min_value=min_date, max_value=max_date, value=min_date + timedelta(days=1))
            
        available_slots, status_msg = get_available_slots(sh, appointment_date)
        
        with col_slot:
            if not available_slots:
                st.selectbox("ช่วงเวลา *", [f"⛔ {status_msg}"], disabled=True)
                selected_time_slot = None
            else:
                slot_options = [f"{slot['label']} น. (ว่าง {slot['available']}/{slot['total_slots']} คิว)" for slot in available_slots]
                selected_time_slot = st.selectbox("เลือกช่วงเวลานัดหมาย *", slot_options)

        notes = st.text_area("หมายเหตุเพิ่มเติม / อาการเบื้องต้น / โรคประจำตัว (ถ้ามี)")
        
        # 🏥 กล่องเงื่อนไขและข้อตกลง
        st.markdown("""
        <div style="background-color: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 12px; padding: 1.25rem; margin-top: 1.5rem; margin-bottom: 1rem;">
            <h4 style="color: #166534; margin-top: 0; margin-bottom: 0.75rem; font-size: 1.05rem;">🏥 เงื่อนไขและข้อตกลงการเข้ารับบริการนัดหมายออนไลน์</h4>
            <div style="font-size: 0.88rem; color: #1e293b; line-height: 1.6;">
                <p style="margin-bottom: 4px;"><b>1. การเตรียมตัวก่อนมาถึง</b></p>
                <ul style="margin-top: 0; margin-bottom: 8px; padding-left: 20px; color: #334155;">
                    <li><b>การยืนยันนัด:</b> ผู้รับบริการต้อง <b>โทรยืนยันนัดหมาย ล่วงหน้า ก่อนเข้ารับบริการ 1 วัน</b> (โทร. 02 453 0526 ต่อ 302)</li>
                    <li><b>การลงทะเบียน:</b> ผู้รับบริการต้องมาติดต่อที่เคาน์เตอร์<b>ก่อนเวลานัดหมายอย่างน้อย 30 นาทีเท่านั้น</b> เพื่อตรวจสอบสิทธิ์และทำประวัติ (เช่น หากท่านจองรอบเวลา 16.00 - 17.00 น. <b>ต้องมาถึงศูนย์เวลา 15.30 น.</b> / หากท่านจองรอบ 17.00 - 18.00 น. <b>ต้องมาถึงเวลา 16.30 น.</b>) หากท่านมาแสดงตนเกินเวลาที่กำหนด ขอยกเลิกนัดหมาย เพื่อไม่ให้กระทบการให้บริการคิวถัดไป</li>
                    <li><b>เอกสารที่ต้องเตรียม:</b> โปรดนำ <b>บัตรประจำตัวประชาชนตัวจริง</b> มาแสดงทุกครั้งที่เข้ารับบริการ</li>
                    <li><b>ประวัติสุขภาพ:</b> หากมีโรคประจำตัว โปรดนำยาทั้งหมดมาด้วย หากแพ้ยา โปรดนำบัตรแพ้ยามาด้วย</li>
                </ul>
                <p style="margin-bottom: 4px;"><b>2. ข้อกำหนดเรื่องเวลาและการรักษาคิว</b></p>
                <ul style="margin-top: 0; margin-bottom: 8px; padding-left: 20px; color: #334155;">
                    <li><b>การมาสาย:</b> หากมาสายเกินเวลาที่กำหนด ทางศูนย์ขอสงวนสิทธิ์ในการ <b>ยกเลิกนัดหมาย</b> ของท่านทันที เพื่อไม่ให้กระทบต่อคิวถัดไป</li>
                    <li><b>การจองคิว:</b> ระบบจำกัดสิทธิ์ <b>1 ชื่อ ต่อ 1 คิวนัดหมาย</b> เท่านั้น</li>
                </ul>
                <p style="margin-bottom: 4px;"><b>3. การยกเลิกหรือเลื่อนนัด</b></p>
                <ul style="margin-top: 0; margin-bottom: 0; padding-left: 20px; color: #334155;">
                    <li><b>การแจ้งยกเลิก:</b> หากไม่สามารถมาตามนัดได้ โปรดแจ้งล่วงหน้าอย่างน้อย 1 วันทำการ ผ่านทางหมายเลขโทรศัพท์ <b>02 453 0526 ต่อ 302</b></li>
                </ul>
            </div>
        </div>
        """, unsafe_allow_html=True)
        
        agree_terms = st.checkbox("ข้าพเจ้าได้อ่านและยอมรับเงื่อนไขและข้อตกลงการเข้ารับบริการนัดหมายออนไลน์ข้างต้น *")
        st.markdown("<br>", unsafe_allow_html=True)
        
        submitted = st.button("📅 ยืนยันข้อมูลและส่งคำขอจองคิว", type="primary", use_container_width=True)

    if submitted:
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

        if check_blacklist(sh, id_card=id_card.strip()):
            st.error("⚠️ บัญชีนี้ถูกระงับสิทธิ์ชั่วคราวเนื่องจากไม่มาตามเวลานัดหมาย กรุณาติดต่อคลินิก")
            return
        if check_blacklist(sh, phone=phone.strip()):
            st.error("⚠️ เบอร์โทรศัพท์นี้ถูกระงับสิทธิ์ชั่วคราว กรุณาติดต่อคลินิก")
            return

        clean_time_label = selected_time_slot.split(" น.")[0]
        date_str = appointment_date.strftime('%Y-%m-%d')

        # ตรวจสอบนัดหมายค้างอยู่ในระบบ
        df_appts = get_table_df(sh, "appointments")
        if not df_appts.empty:
            active_existing = df_appts[
                (df_appts['id_card'].astype(str) == id_card.strip()) &
                (df_appts['status'].isin(['pending', 'confirmed'])) &
                (df_appts['appointment_date'].astype(str) >= date.today().strftime('%Y-%m-%d'))
            ]
            if not active_existing.empty:
                ex = active_existing.iloc[0]
                th_stat = "รอยืนยัน" if ex['status'] == "pending" else "ยืนยันแล้ว"
                q_txt = f" (คิวที่ {ex['queue_number']})" if ex.get('queue_number') else ""
                st.error(f"⛔ **ไม่สามารถจองซ้ำได้:** ท่านมีนัดหมายบริการ **{ex['service_type']}** ในวันที่ **{ex['appointment_date']}** ช่วงเวลา **{ex['appointment_time']} น.**{q_txt} อยู่แล้ว (สถานะ: {th_stat})\n\n*(คนไข้ 1 ท่านสามารถมีคิวนัดหมายที่รอรับบริการได้ 1 คิวเท่านั้น หากต้องการเลื่อนหรือยกเลิกกรุณาติดต่อคลินิก)*")
                return

        # บันทึกคนไข้
        ws_p = sh.worksheet("patients")
        df_p = get_table_df(sh, "patients")
        p_match = df_p[df_p['id_card'].astype(str) == id_card.strip()]
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        if not p_match.empty:
            p_id = p_match.iloc[0]['id']
            # อัปเดตข้อมูลคนไข้เดิม
            for idx, r in enumerate(ws_p.get_all_records(), start=2):
                if str(r.get('id')) == str(p_id):
                    headers = ws_p.row_values(1)
                    ws_p.update_cell(idx, headers.index('full_name') + 1, full_name.strip())
                    ws_p.update_cell(idx, headers.index('phone') + 1, phone.strip())
                    ws_p.update_cell(idx, headers.index('email') + 1, email.strip())
                    break
        else:
            p_id = int(df_p['id'].max()) + 1 if not df_p.empty and df_p['id'].max() else 1
            ws_p.append_row([p_id, full_name.strip(), id_card.strip(), phone.strip(), email.strip(), now_str])

        # คำนวณลำดับคิวประจำวัน
        day_appts = df_appts[df_appts['appointment_date'].astype(str) == date_str] if not df_appts.empty else pd.DataFrame()
        next_q = (int(day_appts['queue_number'].max()) if not day_appts.empty and not pd.isna(day_appts['queue_number'].max()) and day_appts['queue_number'].max() != '' else 0) + 1

        # บันทึกนัดหมาย
        ws_a = sh.worksheet("appointments")
        next_appt_id = int(df_appts['id'].max()) + 1 if not df_appts.empty and df_appts['id'].max() else 1
        token = secrets.token_urlsafe(32)

        ws_a.append_row([
            next_appt_id, p_id, full_name.strip(), id_card.strip(), phone.strip(), email.strip(),
            service_name, date_str, clean_time_label, next_q, 'pending', token, 0, notes, now_str
        ])

        base_url = "https://dental-booking-s7ybkcswqp4qkxg2am8dvl.streamlit.app"
        confirmation_url = f"{base_url}/?confirm={token}"

        email_body = f"""
        <div style="font-family: Arial, sans-serif; line-height: 1.6; max-width: 600px; margin: auto; padding: 24px; border: 1px solid #e2e8f0; border-radius: 12px;">
            <div style="background: #0284c7; padding: 16px; border-radius: 8px; text-align: center; color: white;">
                <h2 style="margin:0;">ยืนยันการนัดหมายทันตกรรม</h2>
                <p style="margin:5px 0 0 0; font-size: 14px;">ศูนย์บริการสาธารณสุข 65 รักษาศุข บางบอน</p>
            </div>
            
            <div style="text-align: center; background-color: #f0f9ff; border: 2px dashed #0284c7; border-radius: 10px; padding: 15px; margin: 20px 0;">
                <span style="font-size: 14px; color: #0369a1; font-weight: bold;">ลำดับคิวประจำวันของท่าน</span><br>
                <span style="font-size: 32px; color: #0284c7; font-weight: 800;">คิวที่ {next_q}</span>
            </div>

            <p>เรียนคุณ <b>{full_name}</b>,</p>
            <p>ระบบได้รับคำขอจองคิวของท่านแล้ว รายละเอียดการนัดหมาย:</p>
            <ul>
                <li><b>ลำดับคิว:</b> คิวที่ {next_q} ของวัน</li>
                <li><b>บริการ:</b> {service_name}</li>
                <li><b>วันที่:</b> {appointment_date.strftime('%d/%m/%Y')}</li>
                <li><b>ช่วงเวลา:</b> {clean_time_label} น.</li>
            </ul>

            <div style="text-align: center; margin: 25px 0; padding: 16px; background-color: #fff7ed; border: 1px solid #fed7aa; border-radius: 10px;">
                <p style="color: #c2410c; font-weight: bold; margin: 0 0 10px 0; font-size: 15px;">
                    ⚠️ ผู้รับบริการต้องโทรยืนยันนัดหมาย ล่วงหน้าก่อนเข้ารับบริการ 1 วัน
                </p>
                <a href="tel:024530526" style="background-color: #0284c7; color: white; padding: 12px 28px; text-decoration: none; border-radius: 8px; font-weight: bold; display: inline-block; font-size: 16px;">
                    📞 กดโทรยืนยันนัด: 02 453 0526 ต่อ 302
                </a>
                <div style="margin-top: 14px;">
                    <span style="font-size: 12px; color: #64748b;">หรือกดยืนยันผ่านระบบออนไลน์:</span><br>
                    <a href="{confirmation_url}" style="color: #16a34a; font-weight: bold; font-size: 13px; text-decoration: underline;">✅ กดยืนยันผ่านลิงก์นี้</a>
                </div>
            </div>

            <div style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 14px; margin-top: 20px; font-size: 13px; color: #334155;">
                <b style="color: #0f172a;">📌 เงื่อนไขและข้อแนะนำในการเข้ารับบริการ:</b>
                <ul style="margin: 6px 0 0 0; padding-left: 18px; line-height: 1.6;">
                    <li><b>การยืนยันนัด:</b> ผู้รับบริการต้องโทรยืนยันนัดหมาย ล่วงหน้าก่อนเข้ารับบริการ 1 วัน</li>
                    <li><b>การลงทะเบียน:</b> กรุณามาติดต่อเคาน์เตอร์<b>ก่อนเวลานัดหมายอย่างน้อย 30 นาทีเท่านั้น</b> เพื่อตรวจสอบสิทธิ์และทำประวัติ (เช่น หากจองรอบ 16.00 น. ต้องมาถึง 15.30 น. / หากจองรอบ 17.00 น. ต้องมาถึง 16.30 น.)</li>
                    <li><b>เอกสารที่ต้องเตรียม:</b> โปรดนำ <b>บัตรประจำตัวประชาชนตัวจริง</b> มาแสดงทุกครั้ง</li>
                    <li><b>ประวัติสุขภาพ:</b> หากมีโรคประจำตัว โปรดนำยาทั้งหมดมาด้วย หากแพ้ยา โปรดนำบัตรแพ้ยามาด้วย</li>
                    <li><b>การมาสาย:</b> หากมาสายเกินเวลาที่กำหนด ทางศูนย์ขอสงวนสิทธิ์ยกเลิกนัดทันที เพื่อไม่ให้กระทบคิวถัดไป</li>
                    <li><b>การแจ้งยกเลิก:</b> หากไม่สามารถมาตามนัดได้ โปรดแจ้งล่วงหน้าอย่างน้อย 1 วันทำการ โทร. <b>02 453 0526 ต่อ 302</b></li>
                </ul>
            </div>
            <hr style="border: 0; border-top: 1px solid #e2e8f0; margin: 20px 0;">
            <p style="font-size: 12px; color: #94a3b8; text-align: center; margin: 0;">ศูนย์บริการสาธารณสุข 65 รักษาศุข บางบอน | โทร. 02 453 0526 ต่อ 302 | dental665@gmail.com</p>
        </div>
        """
        send_email(email.strip(), f"ยืนยันการนัดหมายทันตกรรม (คิวที่ {next_q})", email_body)
        st.success(f"🎉 **จองคิวสำเร็จ! ท่านได้ [คิวที่ {next_q}] ประจำวัน** (รหัสอ้างอิง `APPT-{next_appt_id:05d}`)\n\nข้อมูลถูกบันทึกลงระบบอย่างถาวรแล้ว กรุณาโทรยืนยันนัดหมายล่วงหน้า 1 วันทำการ ที่เบอร์ 02 453 0526 ต่อ 302")

# ========== ยืนยันนัดผ่าน URL ==========
def handle_confirmation(sh):
    token = st.query_params.get('confirm')
    ws = sh.worksheet("appointments")
    records = ws.get_all_records()
    found = None
    row_idx = None
    
    for idx, r in enumerate(records, start=2):
        if str(r.get("token")) == str(token):
            found = r
            row_idx = idx
            break

    st.markdown("""<div class="hero-banner"><h1>🦷 ผลการยืนยันนัดหมาย</h1><p>ศูนย์บริการสาธารณสุข 65 รักษาศุข บางบอน</p></div>""", unsafe_allow_html=True)
    if found:
        name = found.get('full_name')
        appt_date = found.get('appointment_date')
        appt_time = found.get('appointment_time')
        q_num = found.get('queue_number')
        status = found.get('status')
        q_label = f"คิวที่ {q_num}" if q_num else ""
        
        if status == 'pending':
            headers = ws.row_values(1)
            ws.update_cell(row_idx, headers.index('status') + 1, 'confirmed')
            st.success(f"✅ **ยืนยันนัดหมายสำเร็จ!** คุณ {name} ได้รับ **[{q_label}]** สำหรับวันที่ {appt_date} ช่วงเวลา {appt_time} น.")
            st.info("ℹ️ กรุณาโทรยืนยันนัดหมายล่วงหน้า 1 วันทำการ (โทร. 02 453 0526 ต่อ 302) และเดินทางมาถึงก่อนเวลานัดหมายอย่างน้อย 30 นาทีเท่านั้น")
        elif status == 'confirmed':
            st.info(f"ℹ️ นัดหมายนี้ได้รับการยืนยันเรียบร้อยแล้ว ({q_label})")
        else:
            st.warning("⚠️ นัดหมายนี้เสร็จสิ้นหรือถูกยกเลิกไปแล้ว")
    else:
        st.error("❌ ลิงก์ไม่ถูกต้องหรือหมดอายุการใช้งาน")
    
    if st.button("🏠 กลับสู่หน้าหลัก"):
        st.query_params.clear()
        st.rerun()

# ========== หน้า Dashboard แอดมิน ==========
def show_admin_dashboard(sh):
    try:
        allowed_admins = st.secrets["admin_auth"]["allowed_emails"]
        admin_password_correct = st.secrets["admin_auth"]["password"]
    except KeyError:
        allowed_admins = ["healtheducation.hc65@gmail.com", "dental665@gmail.com"]
        admin_password_correct = "admin123"

    if "admin_user" not in st.session_state:
        st.session_state.admin_user = None

    if not st.session_state.admin_user:
        st.sidebar.subheader("🔒 เข้าสู่ระบบเจ้าหน้าที่")
        user_email = st.sidebar.selectbox("เลือกบัญชีผู้ปฏิบัติงาน", allowed_admins)
        pwd = st.sidebar.text_input("รหัสผ่านผู้ดูแล", type="password")
        if st.sidebar.button("เข้าสู่ระบบ", type="primary", use_container_width=True):
            if pwd == admin_password_correct:
                st.session_state.admin_user = user_email
                st.session_state.is_admin = True
                st.rerun()
            else:
                st.sidebar.error("❌ รหัสผ่านไม่ถูกต้อง")
        st.info("กรุณากรอกรหัสผ่านทางแถบด้านซ้ายเพื่อเข้าจัดการระบบ")
        return

    st.sidebar.success(f"👤 ผู้ใช้งาน:\n{st.session_state.admin_user}")
    if st.sidebar.button("🚪 ออกจากระบบ", use_container_width=True):
        st.session_state.admin_user = None
        st.session_state.is_admin = False
        st.rerun()

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

    df_appts = get_table_df(sh, "appointments")

    # 1. ภาพรวมสถิติ
    if menu == "📊 ภาพรวมสถิติ":
        today_str = date.today().strftime('%Y-%m-%d')
        col1, col2, col3, col4 = st.columns(4)
        
        future_cnt = len(df_appts[df_appts['appointment_date'].astype(str) >= today_str]) if not df_appts.empty else 0
        today_cnt = len(df_appts[df_appts['appointment_date'].astype(str) == today_str]) if not df_appts.empty else 0
        conf_today_cnt = len(df_appts[(df_appts['appointment_date'].astype(str) == today_str) & (df_appts['status'] == 'confirmed')]) if not df_appts.empty else 0
        pending_cnt = len(df_appts[df_appts['status'] == 'pending']) if not df_appts.empty else 0

        col1.metric("นัดหมายล่วงหน้า", f"{future_cnt} ราย")
        col2.metric("นัดหมายวันนี้", f"{today_cnt} ราย")
        col3.metric("ยืนยันแล้ววันนี้", f"{conf_today_cnt} ราย")
        col4.metric("รอยืนยันทั้งหมด", f"{pending_cnt} ราย")
            
        st.markdown("<br>", unsafe_allow_html=True)
        st.subheader("📋 ตารางนัดหมายประจำวันนี้ (เรียงตามลำดับคิว)")
        
        if not df_appts.empty:
            df_today = df_appts[df_appts['appointment_date'].astype(str) == today_str].copy()
            if not df_today.empty:
                df_today = df_today.sort_values(by=['queue_number'])
                display_cols = {
                    'queue_number': 'คิวที่',
                    'appointment_time': 'ช่วงเวลา',
                    'full_name': 'ชื่อผู้ป่วย',
                    'service_type': 'บริการ',
                    'status': 'สถานะ',
                    'phone': 'เบอร์โทรศัพท์',
                    'notes': 'หมายเหตุ'
                }
                df_view = df_today[list(display_cols.keys())].rename(columns=display_cols)
                st.dataframe(df_view, use_container_width=True)

                # ปุ่มเช็คชื่อด่วน
                st.markdown("---")
                st.markdown("##### ⚡ เช็คชื่อผู้เข้ารับบริการ (ปลดล็อกให้คนไข้จองรอบใหม่ได้ทันที)")
                active_today = df_today[df_today['status'].isin(['pending', 'confirmed'])]
                if not active_today.empty:
                    col_act1, col_act2 = st.columns([3, 1])
                    act_opts = {row['id']: f"[คิวที่ {row['queue_number']} | {row['appointment_time']} น.] {row['full_name']} - {row['service_type']}" for _, row in active_today.iterrows()}
                    sel_id = col_act1.selectbox("เลือกคนไข้ที่รับการรักษาเสร็จเรียบร้อยแล้ว", options=list(act_opts.keys()), format_func=lambda x: act_opts[x])
                    col_act2.markdown("### ")
                    if col_act2.button("✅ ยืนยันรับบริการแล้ว", type="primary", use_container_width=True):
                        ws_a = sh.worksheet("appointments")
                        for idx, r in enumerate(ws_a.get_all_records(), start=2):
                            if str(r.get('id')) == str(sel_id):
                                headers = ws_a.row_values(1)
                                ws_a.update_cell(idx, headers.index('status') + 1, 'completed')
                                break
                        st.success("บันทึกเข้ารับบริการสำเร็จ! คนไข้รายนี้สามารถจองคิวรับบริการครั้งต่อไปได้แล้ว")
                        st.rerun()
                else:
                    st.info("คิวนัดหมายของวันนี้ได้รับการบันทึกครบถ้วนแล้ว")
            else:
                st.info("ไม่มีรายการนัดหมายในวันนี้")
        else:
            st.info("ไม่มีรายการนัดหมายในวันนี้")

    # 2. รับโทรจอง / Walk-in
    elif menu == "📞 รับโทรจอง / Walk-in":
        st.subheader("📞 รับโทรจองคิว / ผู้ป่วย Walk-in ประจำศูนย์ฯ")
        st.caption("ระบบบันทึกการนัดหมายสำหรับเจ้าหน้าที่รับสายโทรศัพท์ หรือคนไข้ที่เข้ามาติดต่อเคาน์เตอร์โดยตรง (ไม่จำเป็นต้องใช้อีเมล)")
        
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
                p_date = st.date_input("เลือกวันที่นัดหมาย *", min_value=date.today(), value=date.today() + timedelta(days=1))
            
            avail_slots, s_msg = get_available_slots(sh, p_date)
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
                p_status = st.selectbox("สถานะการนัด", ["confirmed (ยืนยันนัดทันที)", "pending (รอยืนยัน)"], index=0)
                send_mail_chk = st.checkbox("ส่งอีเมลแจ้งเตือน (หากระบุอีเมล)", value=True)

            st.markdown("<br>", unsafe_allow_html=True)
            submit_walkin = st.button("💾 บันทึกการนัดหมาย (ออกคิวทันที)", type="primary", use_container_width=True)

        if submit_walkin:
            if not avail_slots or not p_slot:
                st.error(f"❌ ไม่สามารถจองวันที่เลือกได้: {s_msg}")
            elif not all([p_name.strip(), p_idcard.strip(), p_phone.strip()]):
                st.error("❌ กรุณากรอก ชื่อ-นามสกุล, เลขบัตรประชาชน และเบอร์โทรศัพท์ ให้ครบถ้วน")
            elif len(p_idcard.strip()) != 13 or not p_idcard.strip().isdigit():
                st.error("❌ เลขประจำตัวประชาชนต้องเป็นตัวเลข 13 หลักเท่านั้น")
            else:
                if check_blacklist(sh, id_card=p_idcard.strip()):
                    st.error("⚠️ บัญชีนี้ถูกระงับสิทธิ์ชั่วคราวเนื่องจากไม่มาตามเวลานัดหมาย")
                    return

                p_slot_clean = p_slot.split(" น.")[0]
                p_date_str = p_date.strftime('%Y-%m-%d')

                # ตรวจสอบนัดซ้ำ
                if not df_appts.empty:
                    dup_appt = df_appts[
                        (df_appts['id_card'].astype(str) == p_idcard.strip()) &
                        (df_appts['status'].isin(['pending', 'confirmed'])) &
                        (df_appts['appointment_date'].astype(str) >= date.today().strftime('%Y-%m-%d'))
                    ]
                    if not dup_appt.empty:
                        d_row = dup_appt.iloc[0]
                        st.warning(f"⚠️ คนไข้รายนี้มีนัดอยู่แล้วในวันที่ {d_row['appointment_date']} ช่วงเวลา {d_row['appointment_time']} น. (คิวที่ {d_row['queue_number']})")
                        return

                # คำนวณคิว
                day_appts = df_appts[df_appts['appointment_date'].astype(str) == p_date_str] if not df_appts.empty else pd.DataFrame()
                next_q = (int(day_appts['queue_number'].max()) if not day_appts.empty and not pd.isna(day_appts['queue_number'].max()) and day_appts['queue_number'].max() != '' else 0) + 1

                # บันทึกคนไข้
                ws_p = sh.worksheet("patients")
                df_p = get_table_df(sh, "patients")
                p_match = df_p[df_p['id_card'].astype(str) == p_idcard.strip()]
                now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                email_save = p_email.strip() if p_email.strip() else ""

                if not p_match.empty:
                    pt_id = p_match.iloc[0]['id']
                else:
                    pt_id = int(df_p['id'].max()) + 1 if not df_p.empty and df_p['id'].max() else 1
                    ws_p.append_row([pt_id, p_name.strip(), p_idcard.strip(), p_phone.strip(), email_save, now_str])

                # บันทึกนัดหมาย
                ws_a = sh.worksheet("appointments")
                new_appt_id = int(df_appts['id'].max()) + 1 if not df_appts.empty and df_appts['id'].max() else 1
                token = secrets.token_urlsafe(32)
                t_stat = 'confirmed' if "confirmed" in p_status else 'pending'
                final_notes = f"[{channel}] {p_note}".strip()

                ws_a.append_row([
                    new_appt_id, pt_id, p_name.strip(), p_idcard.strip(), p_phone.strip(), email_save,
                    p_service, p_date_str, p_slot_clean, next_q, t_stat, token, 0, final_notes, now_str
                ])

                if send_mail_chk and email_save:
                    email_body = f"""
                    <div style="font-family: Arial, sans-serif; line-height: 1.6; max-width: 600px; margin: auto; padding: 24px; border: 1px solid #e2e8f0; border-radius: 12px;">
                        <div style="background: #0284c7; padding: 16px; border-radius: 8px; text-align: center; color: white;">
                            <h2 style="margin:0;">ใบนัดหมายทันตกรรม</h2>
                            <p style="margin:5px 0 0 0; font-size: 14px;">ศูนย์บริการสาธารณสุข 65 รักษาศุข บางบอน</p>
                        </div>
                        <div style="text-align: center; background-color: #f0f9ff; border: 2px dashed #0284c7; border-radius: 10px; padding: 15px; margin: 20px 0;">
                            <span style="font-size: 14px; color: #0369a1; font-weight: bold;">ลำดับคิวประจำวันของท่าน</span><br>
                            <span style="font-size: 32px; color: #0284c7; font-weight: 800;">คิวที่ {next_q}</span>
                        </div>
                        <p>เรียนคุณ <b>{p_name}</b>,</p>
                        <p>เจ้าหน้าที่ได้ลงทะเบียนนัดหมายให้ท่านเรียบร้อยแล้ว:</p>
                        <ul>
                            <li><b>ลำดับคิว:</b> คิวที่ {next_q} ของวัน</li>
                            <li><b>บริการ:</b> {p_service}</li>
                            <li><b>วันที่:</b> {p_date.strftime('%d/%m/%Y')}</li>
                            <li><b>ช่วงเวลา:</b> {p_slot_clean} น.</li>
                            <li><b>ช่องทางการนัด:</b> {channel}</li>
                        </ul>
                        <div style="text-align: center; margin: 20px 0;">
                            <a href="tel:024530526" style="background-color: #0284c7; color: white; padding: 12px 25px; text-decoration: none; border-radius: 8px; font-weight: bold; display: inline-block;">
                                📞 โทรยืนยันนัด/สอบถาม: 02 453 0526 ต่อ 302
                            </a>
                        </div>
                        <p style="font-size: 13px; color: #64748b;">* กรุณาเดินทางมาถึงเคาน์เตอร์ก่อนเวลานัดหมายอย่างน้อย 30 นาที และนำบัตรประชาชนตัวจริงมาด้วยทุกครั้ง</p>
                    </div>
                    """
                    send_email(email_save, f"ใบนัดหมายทันตกรรม (คิวที่ {next_q})", email_body)

                st.success(f"🎉 **บันทึกนัดหมายสำเร็จ!** คนไข้ได้ **[คิวที่ {next_q}]** ประจำวันที่ {p_date.strftime('%d/%m/%Y')} ช่วงเวลา {p_slot_clean} น. (รหัสอ้างอิง `APPT-{new_appt_id:05d}`)")

    # 3. จัดการคิวนัดหมาย
    elif menu == "📅 จัดการคิวนัดหมาย":
        col1, col2 = st.columns(2)
        start_d = col1.date_input("ตั้งแต่วันที่", value=date.today())
        end_d = col2.date_input("ถึงวันที่", value=date.today() + timedelta(days=7))
        
        if not df_appts.empty:
            mask = (df_appts['appointment_date'].astype(str) >= start_d.strftime('%Y-%m-%d')) & (df_appts['appointment_date'].astype(str) <= end_d.strftime('%Y-%m-%d'))
            df_filtered = df_appts[mask].sort_values(by=['appointment_date', 'queue_number'])
            st.dataframe(df_filtered, use_container_width=True)
        else:
            st.info("ไม่มีข้อมูลนัดหมายในช่วงเวลานี้")

        st.markdown("---")
        st.subheader("เปลี่ยนสถานะนัดหมาย")
        c1, c2, c3 = st.columns([1, 2, 1])
        appt_id = c1.number_input("รหัสนัดหมาย (ID)", min_value=1, step=1)
        new_status = c2.selectbox("สถานะใหม่", [
            ("completed", "เข้ารับบริการแล้ว (Completed) - ปลดล็อกให้จองใหม่ได้"),
            ("confirmed", "ยืนยันแล้ว (Confirmed)"),
            ("pending", "รอยืนยัน (Pending)"),
            ("no_show", "ไม่มาตามนัด (No-Show)"),
            ("cancelled", "ยกเลิกนัด (Cancelled)")
        ], format_func=lambda x: x[1])
        
        c3.markdown("### ")
        if c3.button("💾 บันทึกสถานะ", type="primary", use_container_width=True):
            target_stat = new_status[0]
            if target_stat == "no_show":
                record_no_show(sh, appt_id, reported_by=st.session_state.admin_user, notes="เจ้าหน้าที่ระบุไม่มาตามนัด")
                st.warning(f"บันทึก No-Show ให้รหัสนัด {appt_id} เรียบร้อย")
            else:
                ws_a = sh.worksheet("appointments")
                for idx, r in enumerate(ws_a.get_all_records(), start=2):
                    if str(r.get('id')) == str(appt_id):
                        headers = ws_a.row_values(1)
                        ws_a.update_cell(idx, headers.index('status') + 1, target_stat)
                        break
                st.success(f"อัปเดตสถานะรหัสนัด {appt_id} สำเร็จ")
            st.rerun()

    # 4. จัดการ Slot และปฏิทิน
    elif menu == "🗓️ จัดการ Slot และปฏิทิน":
        st.subheader("🗓️ กำหนด Slot ย่อย และกระดานวันทำการ")
        
        col_pick1, _ = st.columns([2, 3])
        start_of_current_week = date.today() - timedelta(days=date.today().weekday())
        selected_monday = col_pick1.date_input("เลือกวันจันทร์ของสัปดาห์ที่ต้องการดู", value=start_of_current_week)
        week_monday = selected_monday - timedelta(days=selected_monday.weekday())
        week_sunday = week_monday + timedelta(days=6)
        
        m_year_be = week_monday.year + 543
        s_year_be = week_sunday.year + 543
        st.markdown(f"#### วันทำการหลัก ({week_monday.strftime('%d/%m')}/{m_year_be} - {week_sunday.strftime('%d/%m')}/{s_year_be})")

        week_days_thai = ["วันจันทร์", "วันอังคาร", "วันพุธ", "วันพฤหัสบดี", "วันศุกร์", "วันเสาร์", "วันอาทิตย์"]
        cols = st.columns(7)
        
        df_sched = get_table_df(sh, "daily_schedule")

        for i in range(7):
            cur_date = week_monday + timedelta(days=i)
            cur_date_str = cur_date.strftime('%Y-%m-%d')
            
            day_slots = df_sched[df_sched['schedule_date'].astype(str) == cur_date_str].sort_values(by=['start_time']) if not df_sched.empty else pd.DataFrame()
            
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

        # 1. กำหนดเหมายกเดือน
        with tab_slot1:
            st.markdown("##### กำหนดช่วงเวลาและคิว เหมายกช่วง/ยกเดือน (เช่น 1-30 ก.ย.)")
            with st.form("form_batch_slot"):
                cb_d1, cb_d2 = st.columns(2)
                b_start = cb_d1.date_input("ตั้งแต่วันที่", value=date.today())
                b_end = cb_d2.date_input("จนถึงวันที่", value=date.today() + timedelta(days=30))
                
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
                        df_cur = get_table_df(sh, "daily_schedule")
                        
                        if clear_first and not df_cur.empty:
                            mask = (df_cur['schedule_date'].astype(str) >= b_start.strftime('%Y-%m-%d')) & (df_cur['schedule_date'].astype(str) <= b_end.strftime('%Y-%m-%d'))
                            df_cur = df_cur[~mask]
                            ws_s.clear()
                            ws_s.append_row(TABLE_SCHEMAS["daily_schedule"])
                            if not df_cur.empty:
                                ws_s.append_rows(df_cur[TABLE_SCHEMAS["daily_schedule"]].values.tolist())

                        next_id = int(df_cur['id'].max()) + 1 if not df_cur.empty and df_cur['id'].max() else 1
                        new_rows = []
                        cur_d = b_start
                        while cur_d <= b_end:
                            if cur_d.weekday() in b_days:
                                new_rows.append([
                                    next_id, cur_d.strftime('%Y-%m-%d'), 1, 
                                    bs_time.strftime('%H:%M'), be_time.strftime('%H:%M'), bq_cap, 'เปิดทำการ'
                                ])
                                next_id += 1
                            cur_d += timedelta(days=1)
                            
                        if new_rows:
                            ws_s.append_rows(new_rows)
                        st.success(f"✅ บันทึก Slot {bs_time.strftime('%H:%M')}-{be_time.strftime('%H:%M')} น. ({bq_cap} คิว) รวม {len(new_rows)} วัน เรียบร้อยแล้ว")
                        st.rerun()

        # 2. เพิ่ม Slot เฉพาะวัน
        with tab_slot2:
            st.markdown("##### เพิ่มช่วงเวลาย่อยในวันใดวันหนึ่ง (กดเพิ่มได้หลายช่วงเวลาใน 1 วัน)")
            with st.form("form_single_slot"):
                c_s1, c_s2, c_s3, c_s4 = st.columns(4)
                s_date = c_s1.date_input("เลือกวันที่", value=date.today())
                s_start = c_s2.time_input("เวลาเริ่ม", value=time(8, 30))
                s_end = c_s3.time_input("เวลาสิ้นสุด", value=time(11, 0))
                s_cap = c_s4.number_input("จำนวนคิว", min_value=1, max_value=50, value=10)
                
                if st.form_submit_button("➕ เพิ่มช่วงเวลานี้ในวันที่เลือก", type="primary"):
                    ws_s = sh.worksheet("daily_schedule")
                    df_cur = get_table_df(sh, "daily_schedule")
                    next_id = int(df_cur['id'].max()) + 1 if not df_cur.empty and df_cur['id'].max() else 1
                    ws_s.append_row([
                        next_id, s_date.strftime('%Y-%m-%d'), 1, 
                        s_start.strftime('%H:%M'), s_end.strftime('%H:%M'), s_cap, 'เปิดทำการ'
                    ])
                    st.success(f"✅ เพิ่ม Slot {s_start.strftime('%H:%M')}-{s_end.strftime('%H:%M')} น. ให้วันที่ {s_date.strftime('%d/%m/%Y')} เรียบร้อย")
                    st.rerun()

        # 3. สั่งปิดทำการทั้งวัน
        with tab_slot3:
            st.markdown("##### สั่งปิดทำการทั้งวัน (เช่น วันหยุดนักขัตฤกษ์ / ปิดซ่อมยูนิต)")
            with st.form("form_close_day"):
                cc1, cc2 = st.columns(2)
                cl_date = cc1.date_input("เลือกวันที่ต้องการปิดทำการ", value=date.today())
                cl_note = cc2.text_input("สาเหตุที่ปิด", placeholder="เช่น วันหยุดราชการ, วันหยุดนักขัตฤกษ์")
                
                if st.form_submit_button("🔴 สั่งปิดทำการทั้งวัน", type="primary"):
                    ws_s = sh.worksheet("daily_schedule")
                    df_cur = get_table_df(sh, "daily_schedule")
                    if not df_cur.empty:
                        df_kept = df_cur[df_cur['schedule_date'].astype(str) != cl_date.strftime('%Y-%m-%d')]
                        ws_s.clear()
                        ws_s.append_row(TABLE_SCHEMAS["daily_schedule"])
                        if not df_kept.empty:
                            ws_s.append_rows(df_kept[TABLE_SCHEMAS["daily_schedule"]].values.tolist())
                    
                    next_id = int(df_cur['id'].max()) + 1 if not df_cur.empty and df_cur['id'].max() else 1
                    ws_s.append_row([next_id, cl_date.strftime('%Y-%m-%d'), 0, "", "", 0, cl_note])
                    st.success(f"กำหนดให้วันที่ {cl_date.strftime('%d/%m/%Y')} ปิดทำการทั้งวันเรียบร้อย")
                    st.rerun()

        # 4. ดูรายการและล้าง Slot
        with tab_slot4:
            st.markdown("##### 🗓️ ลบ Slot ตามช่วงวันที่ (แนะนำ)")
            col_del_r1, col_del_r2, col_del_r3 = st.columns([2, 2, 2])
            del_from = col_del_r1.date_input("ลบตั้งแต่วันที่", value=date.today(), key="del_from_d")
            del_to = col_del_r2.date_input("จนถึงวันที่", value=date.today() + timedelta(days=30), key="del_to_d")
            col_del_r3.markdown("### ")
            if col_del_r3.button("🗑️ ล้าง Slot ในช่วงนี้", type="primary", use_container_width=True):
                ws_s = sh.worksheet("daily_schedule")
                df_cur = get_table_df(sh, "daily_schedule")
                if not df_cur.empty:
                    mask = (df_cur['schedule_date'].astype(str) >= del_from.strftime('%Y-%m-%d')) & (df_cur['schedule_date'].astype(str) <= del_to.strftime('%Y-%m-%d'))
                    del_cnt = mask.sum()
                    df_kept = df_cur[~mask]
                    ws_s.clear()
                    ws_s.append_row(TABLE_SCHEMAS["daily_schedule"])
                    if not df_kept.empty:
                        ws_s.append_rows(df_kept[TABLE_SCHEMAS["daily_schedule"]].values.tolist())
                    st.success(f"✅ ล้าง Slot เรียบร้อยแล้วทั้งหมด {del_cnt} รายการ")
                else:
                    st.warning("⚠️ ไม่พบรายการ Slot ในช่วงวันที่เลือก")
                st.rerun()

            st.markdown("---")
            st.markdown("##### 📋 ตรวจสอบรายการ Slot ทั้งหมดในระบบ")
            view_d = st.date_input("เลือกดูตั้งแต่ช่วงวันที่", value=date.today() - timedelta(days=7))
            df_cur = get_table_df(sh, "daily_schedule")
            if not df_cur.empty:
                v_mask = df_cur['schedule_date'].astype(str) >= view_d.strftime('%Y-%m-%d')
                st.dataframe(df_cur[v_mask].sort_values(by=['schedule_date', 'start_time']), use_container_width=True)
            else:
                st.info("ไม่มีรายการ Slot")
            
            st.markdown("---")
            cd1, cd2 = st.columns(2)
            del_id = cd1.number_input("ใส่ 'รหัส (ID)' ของ Slot ที่ต้องการลบเฉพาะจุด", min_value=1, step=1)
            if cd1.button("🗑️ ลบเฉพาะ Slot รหัสนี้"):
                ws_s = sh.worksheet("daily_schedule")
                df_cur = get_table_df(sh, "daily_schedule")
                if not df_cur.empty and (df_cur['id'].astype(str) == str(del_id)).any():
                    df_kept = df_cur[df_cur['id'].astype(str) != str(del_id)]
                    ws_s.clear()
                    ws_s.append_row(TABLE_SCHEMAS["daily_schedule"])
                    if not df_kept.empty:
                        ws_s.append_rows(df_kept[TABLE_SCHEMAS["daily_schedule"]].values.tolist())
                    st.success(f"ลบ Slot รหัส {del_id} สำเร็จ")
                else:
                    st.warning(f"ไม่พบ Slot รหัส {del_id}")
                st.rerun()
                
            del_all_date = cd2.date_input("หรือเลือกลบ Slot ทั้งหมดของวันใดวันหนึ่ง", value=date.today(), key="del_single_day")
            if cd2.button("🗑️ ล้างตารางเวลาทั้งหมดของวันนี้"):
                ws_s = sh.worksheet("daily_schedule")
                df_cur = get_table_df(sh, "daily_schedule")
                if not df_cur.empty and (df_cur['schedule_date'].astype(str) == del_all_date.strftime('%Y-%m-%d')).any():
                    df_kept = df_cur[df_cur['schedule_date'].astype(str) != del_all_date.strftime('%Y-%m-%d')]
                    ws_s.clear()
                    ws_s.append_row(TABLE_SCHEMAS["daily_schedule"])
                    if not df_kept.empty:
                        ws_s.append_rows(df_kept[TABLE_SCHEMAS["daily_schedule"]].values.tolist())
                    st.success(f"ล้างตารางเวลาของวันที่ {del_all_date.strftime('%d/%m/%Y')} เรียบร้อย")
                else:
                    st.warning("ไม่พบ Slot ในวันที่เลือก")
                st.rerun()

    # 5. ทะเบียนผู้ป่วย
    elif menu == "👥 ทะเบียนผู้ป่วย":
        st.subheader("👥 รายชื่อผู้ป่วยทั้งหมดในระบบ")
        df_p = get_table_df(sh, "patients")
        if not df_p.empty:
            st.dataframe(df_p.sort_values(by=['id'], ascending=False), use_container_width=True)
        else:
            st.info("ยังไม่มีข้อมูลผู้ป่วย")

    # 6. ระบบส่งแจ้งเตือน
    elif menu == "📧 ระบบส่งแจ้งเตือน":
        st.subheader("📧 ส่งอีเมลแจ้งเตือนล่วงหน้า 1 วัน")
        if st.button("🚀 ส่งอีเมลแจ้งเตือนทันที", type="primary"):
            tomorrow_str = (date.today() + timedelta(days=1)).strftime('%Y-%m-%d')
            targets = df_appts[
                (df_appts['appointment_date'].astype(str) == tomorrow_str) & 
                (df_appts['status'] == 'confirmed') & 
                (df_appts['reminder_sent'].astype(str).isin(['0', 0, '']))
            ] if not df_appts.empty else pd.DataFrame()
            
            sent_count = 0
            ws_a = sh.worksheet("appointments")
            records = ws_a.get_all_records()
            headers = ws_a.row_values(1)
            
            for _, row in targets.iterrows():
                email_addr = row.get('email')
                name = row.get('full_name')
                appt_t = row.get('appointment_time')
                srv = row.get('service_type')
                q_num = row.get('queue_number')
                appt_id = row.get('id')
                
                q_badge = f"<p style='font-size: 20px; font-weight: bold; color: #0284c7; margin: 10px 0;'>ลำดับคิวของท่าน: คิวที่ {q_num}</p>" if q_num else ""
                body = f"""
                <div style="font-family: Arial, sans-serif; padding: 20px; border: 1px solid #e2e8f0; border-radius: 8px;">
                    <h3 style="color: #0284c7;">⏰ แจ้งเตือนนัดหมายทันตกรรมวันพรุ่งนี้</h3>
                    <p>เรียนคุณ <b>{name}</b>,</p>
                    {q_badge}
                    <p>ท่านมีนัดหมายบริการ <b>{srv}</b> ในวันพรุ่งนี้ ({tomorrow_str}) ช่วงเวลา <b>{appt_t} น.</b></p>
                    
                    <div style="text-align: center; margin: 15px 0;">
                        <a href="tel:024530526" style="background-color: #0284c7; color: white; padding: 10px 22px; text-decoration: none; border-radius: 8px; font-weight: bold; display: inline-block;">
                            📞 โทรยืนยันนัด/สอบถาม: 02 453 0526 ต่อ 302
                        </a>
                    </div>

                    <div style="background-color: #f8fafc; border-left: 4px solid #0284c7; padding: 10px 14px; margin: 15px 0; font-size: 13px; color: #334155;">
                        <b>ข้อปฏิบัติก่อนเข้ารับบริการ:</b>
                        <ul style="margin: 5px 0 0 0; padding-left: 18px; line-height: 1.6;">
                            <li>กรุณาเดินทางมาถึงเคาน์เตอร์<b>ก่อนเวลานัดหมายอย่างน้อย 30 นาทีเท่านั้น</b> เพื่อทำประวัติและตรวจสอบสิทธิ์</li>
                            <li>โปรดนำ <b>บัตรประจำตัวประชาชนตัวจริง</b> และยาประจำตัว/บัตรแพ้ยา (ถ้ามี) มาด้วยทุกครั้ง</li>
                            <li>หากมาสายเกินเวลาที่กำหนด ทางศูนย์ขอสงวนสิทธิ์ยกเลิกนัดหมายทันที</li>
                            <li>หากต้องการยกเลิก/เลื่อนนัด โปรดแจ้งล่วงหน้าอย่างน้อย 1 วันทำการ โทร. <b>02 453 0526 ต่อ 302</b></li>
                        </ul>
                    </div>
                    <hr style="border: 0; border-top: 1px solid #e2e8f0; margin: 15px 0;">
                    <p style="font-size: 12px; color: #94a3b8; text-align: center; margin: 0;">ศูนย์บริการสาธารณสุข 65 รักษาศุข บางบอน | โทร. 02 453 0526 ต่อ 302</p>
                </div>
                """
                if send_email(email_addr, f"เตือนนัดหมายทันตกรรม (คิวที่ {q_num})", body):
                    for idx, r in enumerate(records, start=2):
                        if str(r.get('id')) == str(appt_id):
                            ws_a.update_cell(idx, headers.index('reminder_sent') + 1, 1)
                            break
                    sent_count += 1
            st.success(f"ส่งการแจ้งเตือนสำเร็จทั้งหมด {sent_count}/{len(targets)} รายการ")

    # 7. จัดการ Blacklist
    elif menu == "🚫 จัดการ Blacklist":
        st.subheader("🚫 การจัดการระงับสิทธิ์การจองคิว (Blacklist)")
        tab_bl1, tab_bl2 = st.tabs(["📋 รายชื่อผู้ถูกระงับสิทธิ์ & ปลดบล็อก", "➕ สั่งระงับสิทธิ์ (บล็อกผู้ป่วย)"])
        
        with tab_bl1:
            df_bl = get_table_df(sh, "blacklist")
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
                        ws_bl.append_row(TABLE_SCHEMAS["blacklist"])
                        if not df_kept.empty:
                            ws_bl.append_rows(df_kept[TABLE_SCHEMAS["blacklist"]].values.tolist())
                        st.success(f"✅ ปลดบล็อกรหัส {unblock_id} เรียบร้อยแล้ว")
                        st.rerun()
                    else:
                        st.warning(f"ไม่พบข้อมูลรหัส {unblock_id}")
            else:
                st.info("ไม่มีรายชื่อผู้ถูกระงับสิทธิ์ในขณะนี้")
                
        with tab_bl2:
            st.markdown("##### ➕ เพิ่มรายชื่อผู้ป่วยเข้าสู่ระบบระงับสิทธิ์")
            df_p = get_table_df(sh, "patients")
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
                            sh, patient_id=selected_p_id, 
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
        df_ns = get_table_df(sh, "no_show_records")
        if not df_ns.empty:
            st.dataframe(df_ns.sort_values(by=['appointment_date'], ascending=False), use_container_width=True)
        else:
            st.info("ยังไม่มีประวัติการไม่มาตามนัด")

# ========== ควบคุมการทำงานหลัก ==========
def main():
    sh = get_spreadsheet()
    
    # กรณีที่ยังไม่ได้ตั้งค่า Google Sheets Credentials
    if not sh:
        st.error("⚠️ **ยังไม่ได้เชื่อมต่อ Google Sheets**")
        st.info("""
        **ขั้นตอนการเปิดใช้งานฐานข้อมูลถาวร:**
        1. นำ `gspread` และ `google-auth` ไปใส่ในไฟล์ `requirements.txt` บน GitHub
        2. ใส่การตั้งค่า `[sheets]` (URL ของ Google Sheet) และ `[gcp_service_account]` ในเมนู **Settings > Secrets** ของ Streamlit Cloud
        3. กดแชร์ Google Sheet แผ่นนั้นให้กับอีเมล Service Account ให้มีสิทธิ์เป็น **Editor (ผู้แก้ไข)**
        """)
        return

    if 'confirm' in st.query_params:
        handle_confirmation(sh)
        return

    st.sidebar.markdown("### 🦷 ศบส.65 รักษาศุข บางบอน")
    page = st.sidebar.radio("เลือกหน้าต่างทำงาน", ["📅 นัดหมายบริการ", "⚙️ ผู้ดูแลระบบ"])
    
    if page == "📅 นัดหมายบริการ":
        show_booking_form(sh)
    elif page == "⚙️ ผู้ดูแลระบบ":
        show_admin_dashboard(sh)

if __name__ == "__main__":
    main()