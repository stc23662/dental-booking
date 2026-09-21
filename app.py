import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime, date, time, timedelta
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import secrets

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

DB_PATH = "clinic.db"

# ========== ฐานข้อมูลและการตรวจสอบโครงสร้าง ==========
def init_database():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # 1. ผู้ป่วย
    c.execute('''
        CREATE TABLE IF NOT EXISTS patients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            id_card TEXT UNIQUE NOT NULL,
            phone TEXT NOT NULL,
            email TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # 2. นัดหมาย
    c.execute('''
        CREATE TABLE IF NOT EXISTS appointments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER,
            service_type TEXT NOT NULL,
            appointment_date DATE NOT NULL,
            appointment_time TEXT NOT NULL,
            queue_number INTEGER,
            status TEXT DEFAULT 'pending',
            token TEXT UNIQUE,
            reminder_sent INTEGER DEFAULT 0,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (patient_id) REFERENCES patients (id)
        )
    ''')
    
    # Auto-Migration สำหรับ appointments: เพิ่มคอลัมน์ queue_number หากยังไม่มี
    c.execute("PRAGMA table_info(appointments)")
    appt_cols = [r[1] for r in c.fetchall()]
    if "queue_number" not in appt_cols:
        c.execute("ALTER TABLE appointments ADD COLUMN queue_number INTEGER")

    # 3. บริการ (เฉพาะ 4 รายการ)
    c.execute('''
        CREATE TABLE IF NOT EXISTS services (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            is_active INTEGER DEFAULT 1
        )
    ''')
    c.execute("DELETE FROM services")
    target_services = [("ถอนฟัน",), ("อุดฟัน",), ("ขูดหินปูน",), ("ตรวจสุขภาพช่องปาก",)]
    c.executemany("INSERT OR IGNORE INTO services (name) VALUES (?)", target_services)

    # 4. daily_schedule
    c.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='daily_schedule'")
    table_exists = c.fetchone()[0] > 0
    
    need_recreate = False
    if table_exists:
        c.execute("PRAGMA table_info(daily_schedule)")
        cols = [r[1] for r in c.fetchall()]
        if "id" not in cols:
            need_recreate = True

    if need_recreate:
        c.execute("ALTER TABLE daily_schedule RENAME TO daily_schedule_old")
        c.execute('''
            CREATE TABLE daily_schedule (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                schedule_date DATE NOT NULL,
                is_open INTEGER DEFAULT 1,
                start_time TIME,
                end_time TIME,
                max_patients INTEGER DEFAULT 4,
                note TEXT
            )
        ''')
        c.execute('''
            INSERT INTO daily_schedule (schedule_date, is_open, start_time, end_time, max_patients, note)
            SELECT schedule_date, is_open, start_time, end_time, max_patients, note FROM daily_schedule_old
        ''')
        c.execute("DROP TABLE daily_schedule_old")
    else:
        c.execute('''
            CREATE TABLE IF NOT EXISTS daily_schedule (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                schedule_date DATE NOT NULL,
                is_open INTEGER DEFAULT 1,
                start_time TIME,
                end_time TIME,
                max_patients INTEGER DEFAULT 4,
                note TEXT
            )
        ''')

    # 5. Blacklist
    c.execute('''
        CREATE TABLE IF NOT EXISTS blacklist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER UNIQUE,
            id_card TEXT UNIQUE,
            phone TEXT UNIQUE,
            reason TEXT NOT NULL,
            no_show_count INTEGER DEFAULT 1,
            blacklisted_until DATE,
            created_by TEXT DEFAULT 'system',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (patient_id) REFERENCES patients (id)
        )
    ''')

    # 6. ประวัติ No-Show
    c.execute('''
        CREATE TABLE IF NOT EXISTS no_show_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            appointment_id INTEGER,
            patient_id INTEGER,
            appointment_date DATE,
            status TEXT DEFAULT 'no_show',
            reported_by TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (appointment_id) REFERENCES appointments (id),
            FOREIGN KEY (patient_id) REFERENCES patients (id)
        )
    ''')
    
    conn.commit()
    conn.close()

init_database()

# ========== ฟังก์ชันส่งอีเมล ==========
def send_email(to_email: str, subject: str, body: str, cc_email: str = "dental665@gmail.com") -> bool:
    try:
        sender_email = st.secrets["email"]["sender"]
        sender_password = st.secrets["email"]["password"]
        
        msg = MIMEMultipart()
        msg['From'] = f"คลินิกทันตกรรม ศบส.65 <{sender_email}>"
        msg['To'] = to_email
        msg['Cc'] = cc_email
        msg['Reply-To'] = "dental665@gmail.com"
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'html'))
        
        recipients = [to_email]
        if cc_email and cc_email != to_email:
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
def check_blacklist(patient_id=None, id_card=None, phone=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    query = "SELECT * FROM blacklist WHERE (DATE(blacklisted_until) >= DATE('now')) AND ("
    params = []
    conditions = []
    
    if patient_id:
        conditions.append("patient_id = ?")
        params.append(patient_id)
    if id_card:
        conditions.append("id_card = ?")
        params.append(id_card)
    if phone:
        conditions.append("phone = ?")
        params.append(phone)
        
    if not conditions:
        conn.close()
        return None
        
    query += " OR ".join(conditions) + ")"
    c.execute(query, params)
    result = c.fetchone()
    conn.close()
    return result

def add_to_blacklist(patient_id, reason, days_penalty=30, reported_by="system"):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id_card, phone FROM patients WHERE id = ?", (patient_id,))
    patient = c.fetchone()
    
    if not patient:
        conn.close()
        return False
        
    id_card, phone = patient
    existing = check_blacklist(patient_id=patient_id)
    
    if existing:
        current_count = existing[5]
        c.execute('''
            UPDATE blacklist 
            SET no_show_count = no_show_count + 1,
                blacklisted_until = DATE('now', ? || ' days'),
                reason = ?
            WHERE patient_id = ?
        ''', (f"+{days_penalty}", f"{reason} (ครั้งที่ {current_count + 1})", patient_id))
    else:
        blacklisted_until = (date.today() + timedelta(days=days_penalty)).strftime('%Y-%m-%d')
        c.execute('''
            INSERT INTO blacklist 
            (patient_id, id_card, phone, reason, no_show_count, blacklisted_until, created_by)
            VALUES (?, ?, ?, ?, 1, ?, ?)
        ''', (patient_id, id_card, phone, reason, blacklisted_until, reported_by))
    
    conn.commit()
    conn.close()
    return True

def record_no_show(appointment_id, reported_by="system", notes=""):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT patient_id, appointment_date FROM appointments WHERE id = ?", (appointment_id,))
    appointment = c.fetchone()
    
    if not appointment:
        conn.close()
        return False
        
    patient_id, appt_date = appointment
    c.execute("UPDATE appointments SET status = 'no_show' WHERE id = ?", (appointment_id,))
    
    c.execute('''
        INSERT INTO no_show_records 
        (appointment_id, patient_id, appointment_date, status, reported_by, notes)
        VALUES (?, ?, ?, 'no_show', ?, ?)
    ''', (appointment_id, patient_id, appt_date, reported_by, notes))
    conn.commit()
    
    c.execute('''
        SELECT COUNT(*) FROM no_show_records 
        WHERE patient_id = ? AND status = 'no_show' AND appointment_date >= DATE('now', '-90 days')
    ''', (patient_id,))
    no_show_count = c.fetchone()[0]
    conn.close()
    
    if no_show_count >= 2:
        add_to_blacklist(
            patient_id, 
            f"ไม่มาตามนัด {no_show_count} ครั้งในรอบ 90 วัน", 
            days_penalty=30 * no_show_count, 
            reported_by="auto_system"
        )
    return True

# ========== คำนวณช่วงเวลาว่างของวันที่เลือก ==========
def get_available_slots(appointment_date: date):
    conn = sqlite3.connect(DB_PATH)
    date_str = appointment_date.strftime('%Y-%m-%d')
    c = conn.cursor()
    
    c.execute('''
        SELECT is_open, start_time, end_time, max_patients, note 
        FROM daily_schedule 
        WHERE schedule_date = ?
        ORDER BY start_time ASC
    ''', (date_str,))
    schedule_records = c.fetchall()
    
    if not schedule_records:
        conn.close()
        return [], "คลินิกยังไม่ได้เปิดรับจองในวันนี้"
        
    if any(r[0] == 0 for r in schedule_records):
        note = next((r[4] for r in schedule_records if r[0] == 0 and r[4]), "ปิดทำการพิเศษ")
        conn.close()
        return [], f"ปิดทำการ ({note})"
    
    all_slots = []
    for _, start_str, end_str, max_patients, _ in schedule_records:
        if not start_str or not end_str:
            continue
        
        slot_label = f"{start_str} - {end_str}"
        c.execute('''
            SELECT COUNT(*) FROM appointments 
            WHERE appointment_date = ? AND appointment_time = ? AND status IN ('pending', 'confirmed')
        ''', (date_str, slot_label))
        booked_count = c.fetchone()[0]
        
        if booked_count < max_patients:
            all_slots.append({
                'label': slot_label,
                'available': max_patients - booked_count,
                'total_slots': max_patients
            })
            
    conn.close()
    return all_slots, "เปิดทำการ"

# ========== หน้าจองคิว (รันคิวประจำวันให้อัตโนมัติ) ==========
def show_booking_form():
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
        
        conn = sqlite3.connect(DB_PATH)
        services_df = pd.read_sql_query("SELECT id, name FROM services WHERE is_active = 1", conn)
        conn.close()
        
        service_name = st.selectbox("บริการที่ต้องการรับการรักษา *", services_df['name'].tolist())
        
        col_date, col_slot = st.columns(2)
        with col_date:
            min_date = date.today()
            max_date = min_date + timedelta(days=120)
            appointment_date = st.date_input("เลือกวันที่ต้องการนัดหมาย *", min_value=min_date, max_value=max_date, value=min_date + timedelta(days=1))
            
        available_slots, status_msg = get_available_slots(appointment_date)
        
        with col_slot:
            if not available_slots:
                st.selectbox("ช่วงเวลา *", [f"⛔ {status_msg}"], disabled=True)
                selected_time_slot = None
            else:
                slot_options = [f"{slot['label']} น. (ว่าง {slot['available']}/{slot['total_slots']} คิว)" for slot in available_slots]
                selected_time_slot = st.selectbox("เลือกช่วงเวลานัดหมาย *", slot_options)

        notes = st.text_area("หมายเหตุเพิ่มเติม / อาการเบื้องต้น / โรคประจำตัว (ถ้ามี)")
        
        # 🏥 กล่องเงื่อนไขและข้อตกลง (กำหนด 30 นาทีเท่านั้น)
        st.markdown("""
        <div style="background-color: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 12px; padding: 1.25rem; margin-top: 1.5rem; margin-bottom: 1rem;">
            <h4 style="color: #166534; margin-top: 0; margin-bottom: 0.75rem; font-size: 1.05rem;">🏥 เงื่อนไขและข้อตกลงการเข้ารับบริการนัดหมายออนไลน์</h4>
            <div style="font-size: 0.88rem; color: #1e293b; line-height: 1.6;">
                <p style="margin-bottom: 4px;"><b>1. การเตรียมตัวก่อนมาถึง</b></p>
                <ul style="margin-top: 0; margin-bottom: 8px; padding-left: 20px; color: #334155;">
                    <li><b>การยืนยันนัด:</b> ผู้รับบริการต้องกดยืนยันนัดหมายผ่านอีเมลที่ได้รับ เพื่อเป็นการยืนยันการเข้ารับบริการ</li>
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

        # ตรวจสอบ Blacklist
        if check_blacklist(id_card=id_card.strip()):
            st.error("⚠️ บัญชีนี้ถูกระงับสิทธิ์ชั่วคราวเนื่องจากไม่มาตามเวลานัดหมาย กรุณาติดต่อคลินิก")
            return
        if check_blacklist(phone=phone.strip()):
            st.error("⚠️ เบอร์โทรศัพท์นี้ถูกระงับสิทธิ์ชั่วคราว กรุณาติดต่อคลินิก")
            return

        clean_time_label = selected_time_slot.split(" น.")[0]
        date_str = appointment_date.strftime('%Y-%m-%d')

        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        try:
            # 🔒 ตรวจสอบว่ามีนัดค้างอยู่หรือไม่ (เฉพาะ pending หรือ confirmed)
            c.execute('''
                SELECT a.appointment_date, a.appointment_time, a.service_type, a.status, a.queue_number
                FROM appointments a
                JOIN patients p ON a.patient_id = p.id
                WHERE p.id_card = ? 
                  AND a.status IN ('pending', 'confirmed') 
                  AND a.appointment_date >= DATE('now')
            ''', (id_card.strip(),))
            active_appointment = c.fetchone()
            
            if active_appointment:
                exist_date, exist_time, exist_srv, exist_stat, exist_q = active_appointment
                th_stat = "รอยืนยัน" if exist_stat == "pending" else "ยืนยันแล้ว"
                q_text = f" (คิวที่ {exist_q})" if exist_q else ""
                try:
                    d_obj = datetime.strptime(exist_date, '%Y-%m-%d')
                    th_date_str = f"{d_obj.strftime('%d/%m/')}{d_obj.year + 543}"
                except:
                    th_date_str = exist_date

                st.error(f"⛔ **ไม่สามารถจองซ้ำได้:** ท่านมีนัดหมายบริการ **{exist_srv}** ในวันที่ **{th_date_str}** ช่วงเวลา **{exist_time} น.**{q_text} อยู่แล้ว (สถานะ: {th_stat})\n\n*(คนไข้ 1 ท่านสามารถมีคิวนัดหมายที่รอรับบริการได้ 1 คิวเท่านั้น หากต้องการเลื่อนหรือยกเลิกกรุณาติดต่อคลินิก)*")
                return

            # Concurrency Check: ตรวจสอบความจุของ Slot อีกรอบก่อนเซฟ
            c.execute('''
                SELECT max_patients FROM daily_schedule 
                WHERE schedule_date = ? AND (start_time || ' - ' || end_time) = ?
            ''', (date_str, clean_time_label))
            sched = c.fetchone()
            if sched:
                max_cap = sched[0]
                c.execute('''
                    SELECT COUNT(*) FROM appointments 
                    WHERE appointment_date = ? AND appointment_time = ? AND status IN ('pending', 'confirmed')
                ''', (date_str, clean_time_label))
                curr_booked = c.fetchone()[0]
                if curr_booked >= max_cap:
                    st.error("❌ ขออภัย ช่วงเวลานี้เพิ่งมีผู้จองเต็ม กรุณาเลือกช่วงเวลาอื่น")
                    return

            # คำนวณลำดับคิวของวันนั้น (นับต่อไปเรื่อยๆ 1, 2, 3...)
            c.execute("SELECT COALESCE(MAX(queue_number), 0) + 1 FROM appointments WHERE appointment_date = ?", (date_str,))
            next_queue_num = c.fetchone()[0]

            # บันทึกคนไข้
            c.execute("SELECT id FROM patients WHERE id_card = ?", (id_card.strip(),))
            patient = c.fetchone()
            if patient:
                patient_id = patient[0]
                c.execute("UPDATE patients SET full_name = ?, phone = ?, email = ? WHERE id = ?",
                          (full_name.strip(), phone.strip(), email.strip(), patient_id))
            else:
                c.execute("INSERT INTO patients (full_name, id_card, phone, email) VALUES (?, ?, ?, ?)",
                          (full_name.strip(), id_card.strip(), phone.strip(), email.strip()))
                patient_id = c.lastrowid

            token = secrets.token_urlsafe(32)
            c.execute('''
                INSERT INTO appointments (patient_id, service_type, appointment_date, appointment_time, queue_number, status, token, notes)
                VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
            ''', (patient_id, service_name, date_str, clean_time_label, next_queue_num, token, notes))
            appointment_id = c.lastrowid
            conn.commit()

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
                    <span style="font-size: 32px; color: #0284c7; font-weight: 800;">คิวที่ {next_queue_num}</span>
                </div>

                <p>เรียนคุณ <b>{full_name}</b>,</p>
                <p>ระบบได้รับคำขอจองคิวของท่านแล้ว รายละเอียดการนัดหมาย:</p>
                <ul>
                    <li><b>ลำดับคิว:</b> คิวที่ {next_queue_num} ของวัน</li>
                    <li><b>บริการ:</b> {service_name}</li>
                    <li><b>วันที่:</b> {appointment_date.strftime('%d/%m/%Y')}</li>
                    <li><b>ช่วงเวลา:</b> {clean_time_label} น.</li>
                </ul>
                <div style="text-align: center; margin: 30px 0;">
                    <a href="{confirmation_url}" style="background-color: #16a34a; color: white; padding: 12px 30px; text-decoration: none; border-radius: 8px; font-weight: bold; display: inline-block;">
                        ✅ กดยืนยันการนัดหมาย
                    </a>
                </div>
                <div style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 14px; margin-top: 20px; font-size: 13px; color: #334155;">
                    <b style="color: #0f172a;">📌 เงื่อนไขและข้อแนะนำในการเข้ารับบริการ:</b>
                    <ul style="margin: 6px 0 0 0; padding-left: 18px; line-height: 1.5;">
                        <li>กรุณามาติดต่อเคาน์เตอร์<b>ก่อนเวลานัดหมายอย่างน้อย 30 นาทีเท่านั้น</b> เพื่อตรวจสอบสิทธิ์และทำประวัติ (เช่น หากจองรอบ 16.00 น. ต้องมาถึง 15.30 น.)</li>
                        <li>โปรดนำ <b>บัตรประจำตัวประชาชนตัวจริง</b> มาแสดงทุกครั้ง</li>
                        <li>หากมีโรคประจำตัวหรือแพ้ยา โปรดนำยาเดิมและบัตรแพ้ยามาด้วย</li>
                        <li>หากมาสายเกินเวลาที่กำหนด ทางศูนย์ขอสงวนสิทธิ์ยกเลิกนัดทันที เพื่อไม่ให้กระทบคิวถัดไป</li>
                        <li>หากต้องการยกเลิก/เลื่อนนัด โปรดแจ้งล่วงหน้าอย่างน้อย 1 วันทำการ โทร. <b>02 453 0526 ต่อ 302</b></li>
                    </ul>
                </div>
                <hr style="border: 0; border-top: 1px solid #e2e8f0; margin: 20px 0;">
                <p style="font-size: 12px; color: #94a3b8; text-align: center; margin: 0;">ศูนย์บริการสาธารณสุข 65 รักษาศุข บางบอน | โทร. 02 453 0526 ต่อ 302 | dental665@gmail.com</p>
            </div>
            """
            send_email(email.strip(), f"ยืนยันการนัดหมายทันตกรรม (คิวที่ {next_queue_num})", email_body)
            st.success(f"🎉 **จองคิวสำเร็จ! ท่านได้ [คิวที่ {next_queue_num}] ประจำวัน** (รหัสอ้างอิง `APPT-{appointment_id:05d}`)\n\nกรุณาตรวจสอบอีเมลเพื่อกดยืนยันนัดหมาย")
        except Exception as err:
            st.error(f"เกิดข้อผิดพลาด: {err}")
        finally:
            conn.close()

# ========== ยืนยันนัดผ่าน URL ==========
def handle_confirmation():
    token = st.query_params.get('confirm')
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        SELECT a.id, p.full_name, a.appointment_date, a.appointment_time, a.queue_number, a.status 
        FROM appointments a
        JOIN patients p ON a.patient_id = p.id
        WHERE a.token = ?
    ''', (token,))
    appointment = c.fetchone()
    
    st.markdown("""<div class="hero-banner"><h1>🦷 ผลการยืนยันนัดหมาย</h1><p>ศูนย์บริการสาธารณสุข 65 รักษาศุข บางบอน</p></div>""", unsafe_allow_html=True)
    if appointment:
        appt_id, name, appt_date, appt_time, q_num, status = appointment
        q_label = f"คิวที่ {q_num}" if q_num else ""
        if status == 'pending':
            c.execute("UPDATE appointments SET status = 'confirmed' WHERE id = ?", (appt_id,))
            conn.commit()
            st.success(f"✅ **ยืนยันนัดหมายสำเร็จ!** คุณ {name} ได้รับ **[{q_label}]** สำหรับวันที่ {appt_date} ช่วงเวลา {appt_time} น.")
            st.info("ℹ️ กรุณาเดินทางมาถึงก่อนเวลานัดหมายอย่างน้อย 30 นาทีเท่านั้น และนำบัตรประจำตัวประชาชนตัวจริงมาด้วย")
        elif status == 'confirmed':
            st.info(f"ℹ️ นัดหมายนี้ได้รับการยืนยันเรียบร้อยแล้ว ({q_label})")
        else:
            st.warning("⚠️ นัดหมายนี้เสร็จสิ้นหรือถูกยกเลิกไปแล้ว")
    else:
        st.error("❌ ลิงก์ไม่ถูกต้องหรือหมดอายุการใช้งาน")
    conn.close()
    
    if st.button("🏠 กลับสู่หน้าหลัก"):
        st.query_params.clear()
        st.rerun()

# ========== หน้า Dashboard แอดมิน ==========
def show_admin_dashboard():
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
        ["📊 ภาพรวมสถิติ", "📅 จัดการคิวนัดหมาย", "🗓️ จัดการ Slot และปฏิทิน", "👥 ทะเบียนผู้ป่วย", 
         "📧 ระบบส่งแจ้งเตือน", "🚫 จัดการ Blacklist", "📋 ประวัติ No-Show"]
    )
    conn = sqlite3.connect(DB_PATH)

    # 1. ภาพรวมสถิติ
    if menu == "📊 ภาพรวมสถิติ":
        today = date.today().strftime('%Y-%m-%d')
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            total = pd.read_sql_query("SELECT COUNT(*) FROM appointments WHERE appointment_date >= DATE('now')", conn).iloc[0,0]
            st.metric("นัดหมายล่วงหน้า", f"{total} ราย")
        with col2:
            today_total = pd.read_sql_query("SELECT COUNT(*) FROM appointments WHERE appointment_date = ?", conn, params=(today,)).iloc[0,0]
            st.metric("นัดหมายวันนี้", f"{today_total} ราย")
        with col3:
            confirmed = pd.read_sql_query("SELECT COUNT(*) FROM appointments WHERE appointment_date = ? AND status = 'confirmed'", conn, params=(today,)).iloc[0,0]
            st.metric("ยืนยันแล้ววันนี้", f"{confirmed} ราย")
        with col4:
            pending = pd.read_sql_query("SELECT COUNT(*) FROM appointments WHERE status = 'pending'", conn).iloc[0,0]
            st.metric("รอยืนยันทั้งหมด", f"{pending} ราย")
            
        st.markdown("<br>", unsafe_allow_html=True)
        st.subheader("📋 ตารางนัดหมายประจำวันนี้ (เรียงตามลำดับคิว)")
        df_today = pd.read_sql_query('''
            SELECT a.queue_number as คิวที่, a.appointment_time as ช่วงเวลา, p.full_name as ชื่อผู้ป่วย, a.service_type as บริการ, 
                   CASE 
                       WHEN a.status = 'pending' THEN '🟡 รอยืนยัน'
                       WHEN a.status = 'confirmed' THEN '🟢 ยืนยันแล้ว'
                       WHEN a.status = 'completed' THEN '✅ รับบริการแล้ว'
                       WHEN a.status = 'no_show' THEN '🔴 ไม่มาตามนัด'
                       ELSE a.status 
                   END as สถานะ,
                   p.phone as เบอร์โทรศัพท์, a.notes as หมายเหตุ
            FROM appointments a JOIN patients p ON a.patient_id = p.id
            WHERE a.appointment_date = ? ORDER BY a.queue_number ASC, a.appointment_time ASC
        ''', conn, params=(today,))
        
        if not df_today.empty:
            st.dataframe(df_today, use_container_width=True)
            
            # ปุ่มด่วนเช็คชื่อคนไข้วันนี้
            st.markdown("---")
            st.markdown("##### ⚡ เช็คชื่อผู้เข้ารับบริการ (ปลดล็อกให้คนไข้จองรอบใหม่ได้ทันที)")
            df_active_today = pd.read_sql_query('''
                SELECT a.id, a.queue_number, a.appointment_time, p.full_name, a.service_type
                FROM appointments a JOIN patients p ON a.patient_id = p.id
                WHERE a.appointment_date = ? AND a.status IN ('pending', 'confirmed')
                ORDER BY a.queue_number ASC
            ''', conn, params=(today,))
            
            if not df_active_today.empty:
                col_act1, col_act2 = st.columns([3, 1])
                active_opts = {row['id']: f"[คิวที่ {row['queue_number']} | {row['appointment_time']} น.] {row['full_name']} - {row['service_type']}" for _, row in df_active_today.iterrows()}
                selected_done_id = col_act1.selectbox("เลือกคนไข้ที่รับการรักษาเสร็จเรียบร้อยแล้ว", options=list(active_opts.keys()), format_func=lambda x: active_opts[x])
                col_act2.markdown("### ")
                if col_act2.button("✅ ยืนยันรับบริการแล้ว", type="primary", use_container_width=True):
                    c = conn.cursor()
                    c.execute("UPDATE appointments SET status = 'completed' WHERE id = ?", (selected_done_id,))
                    conn.commit()
                    st.success("บันทึกเข้ารับบริการสำเร็จ! คนไข้รายนี้สามารถจองคิวรับบริการครั้งต่อไปได้แล้ว")
                    st.rerun()
            else:
                st.info("คิวนัดหมายของวันนี้ได้รับการบันทึกครบถ้วนแล้ว")
        else:
            st.info("ไม่มีรายการนัดหมายในวันนี้")

    # 2. จัดการคิวนัดหมาย
    elif menu == "📅 จัดการคิวนัดหมาย":
        col1, col2 = st.columns(2)
        start_d = col1.date_input("ตั้งแต่วันที่", value=date.today())
        end_d = col2.date_input("ถึงวันที่", value=date.today() + timedelta(days=7))
        
        df_appts = pd.read_sql_query('''
            SELECT a.id as รหัสนัด, a.appointment_date as วันที่, a.queue_number as คิวที่, a.appointment_time as ช่วงเวลา, 
                   p.full_name as ชื่อผู้ป่วย, p.phone as โทรศัพท์, a.service_type as บริการ, a.status as สถานะ
            FROM appointments a JOIN patients p ON a.patient_id = p.id
            WHERE a.appointment_date BETWEEN ? AND ? ORDER BY a.appointment_date ASC, a.queue_number ASC
        ''', conn, params=(start_d.strftime('%Y-%m-%d'), end_d.strftime('%Y-%m-%d')))
        st.dataframe(df_appts, use_container_width=True)

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
            target_status = new_status[0]
            if target_status == "no_show":
                record_no_show(appt_id, reported_by=st.session_state.admin_user, notes="เจ้าหน้าที่ระบุไม่มาตามนัด")
                st.warning(f"บันทึก No-Show ให้รหัสนัด {appt_id} เรียบร้อย")
            else:
                c = conn.cursor()
                c.execute("UPDATE appointments SET status = ? WHERE id = ?", (target_status, appt_id))
                conn.commit()
                st.success(f"อัปเดตสถานะรหัสนัด {appt_id} สำเร็จ")
            st.rerun()

    # 3. จัดการ Slot และปฏิทิน
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
        
        for i in range(7):
            cur_date = week_monday + timedelta(days=i)
            cur_date_str = cur_date.strftime('%Y-%m-%d')
            
            c = conn.cursor()
            c.execute('''
                SELECT is_open, start_time, end_time, max_patients, note 
                FROM daily_schedule 
                WHERE schedule_date = ? 
                ORDER BY start_time ASC
            ''', (cur_date_str,))
            day_slots = c.fetchall()
            
            with cols[i]:
                if not day_slots:
                    time_sub = "-"
                    slot_content = "<div style='text-align:center; color:#94a3b8; margin: 20px 0;'>-</div>"
                    total_q = 0
                elif any(r[0] == 0 for r in day_slots):
                    time_sub = "ปิดทำการ"
                    slot_content = "<div style='text-align:center; color:#ef4444; font-weight:600; margin: 20px 0;'>ปิดทำการ</div>"
                    total_q = 0
                else:
                    earliest = day_slots[0][1]
                    latest = day_slots[-1][2]
                    time_sub = f"{earliest} - {latest}"
                    
                    slot_htmls = []
                    total_q = 0
                    for _, s_t, e_t, cap, _ in day_slots:
                        total_q += cap
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
                        c = conn.cursor()
                        if clear_first:
                            c.execute("DELETE FROM daily_schedule WHERE schedule_date BETWEEN ? AND ?", 
                                      (b_start.strftime('%Y-%m-%d'), b_end.strftime('%Y-%m-%d')))
                        
                        cur_d = b_start
                        count_d = 0
                        while cur_d <= b_end:
                            if cur_d.weekday() in b_days:
                                c.execute('''
                                    INSERT INTO daily_schedule (schedule_date, is_open, start_time, end_time, max_patients, note)
                                    VALUES (?, 1, ?, ?, ?, 'เปิดทำการ')
                                ''', (cur_d.strftime('%Y-%m-%d'), bs_time.strftime('%H:%M'), be_time.strftime('%H:%M'), bq_cap))
                                count_d += 1
                            cur_d += timedelta(days=1)
                        conn.commit()
                        st.success(f"✅ บันทึก Slot {bs_time.strftime('%H:%M')}-{be_time.strftime('%H:%M')} น. ({bq_cap} คิว) รวม {count_d} วัน เรียบร้อยแล้ว")
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
                    c = conn.cursor()
                    c.execute('''
                        INSERT INTO daily_schedule (schedule_date, is_open, start_time, end_time, max_patients, note)
                        VALUES (?, 1, ?, ?, ?, 'เปิดทำการ')
                    ''', (s_date.strftime('%Y-%m-%d'), s_start.strftime('%H:%M'), s_end.strftime('%H:%M'), s_cap))
                    conn.commit()
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
                    c = conn.cursor()
                    c.execute("DELETE FROM daily_schedule WHERE schedule_date = ?", (cl_date.strftime('%Y-%m-%d'),))
                    c.execute('''
                        INSERT INTO daily_schedule (schedule_date, is_open, note)
                        VALUES (?, 0, ?)
                    ''', (cl_date.strftime('%Y-%m-%d'), cl_note))
                    conn.commit()
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
                c = conn.cursor()
                c.execute("DELETE FROM daily_schedule WHERE schedule_date BETWEEN ? AND ?", 
                          (del_from.strftime('%Y-%m-%d'), del_to.strftime('%Y-%m-%d')))
                num_deleted = c.rowcount
                conn.commit()
                if num_deleted > 0:
                    st.success(f"✅ ล้าง Slot เรียบร้อยแล้วทั้งหมด {num_deleted} รายการ")
                else:
                    st.warning("⚠️ ไม่พบรายการ Slot ในช่วงวันที่เลือก")
                st.rerun()

            st.markdown("---")
            st.markdown("##### 📋 ตรวจสอบรายการ Slot ทั้งหมดในระบบ")
            view_d = st.date_input("เลือกดูตั้งแต่ช่วงวันที่", value=date.today() - timedelta(days=7))
            df_s = pd.read_sql_query('''
                SELECT id as รหัส, schedule_date as วันที่,
                       CASE WHEN is_open = 1 THEN '🟢 เปิด' ELSE '🔴 ปิด' END as สถานะ,
                       start_time as เวลาเริ่ม, end_time as เวลาสิ้นสุด, max_patients as 'คิวที่รับ', note as หมายเหตุ
                FROM daily_schedule
                WHERE schedule_date >= ?
                ORDER BY schedule_date ASC, start_time ASC
            ''', conn, params=(view_d.strftime('%Y-%m-%d'),))
            st.dataframe(df_s, use_container_width=True)
            
            st.markdown("---")
            cd1, cd2 = st.columns(2)
            del_id = cd1.number_input("ใส่ 'รหัส (ID)' ของ Slot ที่ต้องการลบเฉพาะจุด", min_value=1, step=1)
            if cd1.button("🗑️ ลบเฉพาะ Slot รหัสนี้"):
                c = conn.cursor()
                c.execute("DELETE FROM daily_schedule WHERE id = ?", (del_id,))
                n = c.rowcount
                conn.commit()
                if n > 0:
                    st.success(f"ลบ Slot รหัส {del_id} สำเร็จ")
                else:
                    st.warning(f"ไม่พบ Slot รหัส {del_id}")
                st.rerun()
                
            del_all_date = cd2.date_input("หรือเลือกลบ Slot ทั้งหมดของวันใดวันหนึ่ง", value=date.today(), key="del_single_day")
            if cd2.button("🗑️ ล้างตารางเวลาทั้งหมดของวันนี้"):
                c = conn.cursor()
                c.execute("DELETE FROM daily_schedule WHERE schedule_date = ?", (del_all_date.strftime('%Y-%m-%d'),))
                n = c.rowcount
                conn.commit()
                if n > 0:
                    st.success(f"ล้างตารางเวลาของวันที่ {del_all_date.strftime('%d/%m/%Y')} เรียบร้อย ({n} รายการ)")
                else:
                    st.warning("ไม่พบ Slot ในวันที่เลือก")
                st.rerun()

    # 4. ทะเบียนผู้ป่วย
    elif menu == "👥 ทะเบียนผู้ป่วย":
        st.subheader("👥 รายชื่อผู้ป่วยทั้งหมดในระบบ")
        df_p = pd.read_sql_query("SELECT id as รหัส, full_name as ชื่อ, id_card as เลขบัตร, phone as เบอร์โทร, email as อีเมล, created_at as วันที่ลงทะเบียน FROM patients ORDER BY id DESC", conn)
        st.dataframe(df_p, use_container_width=True)

    # 5. ระบบส่งแจ้งเตือน
    elif menu == "📧 ระบบส่งแจ้งเตือน":
        st.subheader("📧 ส่งอีเมลแจ้งเตือนล่วงหน้า 1 วัน")
        if st.button("🚀 ส่งอีเมลแจ้งเตือนทันที", type="primary"):
            tomorrow = (date.today() + timedelta(days=1)).strftime('%Y-%m-%d')
            c = conn.cursor()
            c.execute('''
                SELECT p.email, p.full_name, a.appointment_time, a.service_type, a.queue_number, a.id
                FROM appointments a JOIN patients p ON a.patient_id = p.id
                WHERE a.appointment_date = ? AND a.status = 'confirmed' AND a.reminder_sent = 0
            ''', (tomorrow,))
            targets = c.fetchall()
            
            sent_count = 0
            for email_addr, name, appt_t, srv, q_num, appt_id in targets:
                q_badge = f"<p style='font-size: 20px; font-weight: bold; color: #0284c7; margin: 10px 0;'>ลำดับคิวของท่าน: คิวที่ {q_num}</p>" if q_num else ""
                body = f"""
                <div style="font-family: Arial, sans-serif; padding: 20px; border: 1px solid #e2e8f0; border-radius: 8px;">
                    <h3 style="color: #0284c7;">⏰ แจ้งเตือนนัดหมายทันตกรรมวันพรุ่งนี้</h3>
                    <p>เรียนคุณ <b>{name}</b>,</p>
                    {q_badge}
                    <p>ท่านมีนัดหมายบริการ <b>{srv}</b> ในวันพรุ่งนี้ ({tomorrow}) ช่วงเวลา <b>{appt_t} น.</b></p>
                    <div style="background-color: #f8fafc; border-left: 4px solid #0284c7; padding: 10px 14px; margin: 15px 0; font-size: 13px; color: #334155;">
                        <b>ข้อปฏิบัติก่อนเข้ารับบริการ:</b>
                        <ul style="margin: 5px 0 0 0; padding-left: 18px;">
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
                    c.execute("UPDATE appointments SET reminder_sent = 1 WHERE id = ?", (appt_id,))
                    sent_count += 1
            conn.commit()
            st.success(f"ส่งการแจ้งเตือนสำเร็จทั้งหมด {sent_count}/{len(targets)} รายการ")

    # 6. จัดการ Blacklist
    elif menu == "🚫 จัดการ Blacklist":
        st.subheader("🚫 การจัดการระงับสิทธิ์การจองคิว (Blacklist)")
        tab_bl1, tab_bl2 = st.tabs(["📋 รายชื่อผู้ถูกระงับสิทธิ์ & ปลดบล็อก", "➕ สั่งระงับสิทธิ์ (บล็อกผู้ป่วย)"])
        
        with tab_bl1:
            df_bl = pd.read_sql_query('''
                SELECT b.id as รหัส, p.full_name as ชื่อผู้ป่วย, b.id_card as เลขบัตรประชาชน, b.phone as เบอร์โทร, 
                       b.reason as สาเหตุ, b.no_show_count as ครั้งที่ผิดนัด, b.blacklisted_until as ระงับถึงวันที่,
                       b.created_by as ผู้บันทึก,
                       CASE WHEN DATE(b.blacklisted_until) < DATE('now') THEN '⚪ หมดอายุ' ELSE '🔴 กำลังระงับสิทธิ์' END as สถานะ
                FROM blacklist b JOIN patients p ON b.patient_id = p.id
                ORDER BY b.blacklisted_until DESC
            ''', conn)
            
            if not df_bl.empty:
                st.dataframe(df_bl, use_container_width=True)
                st.markdown("---")
                st.markdown("##### 🔓 ปลดบล็อกผู้ป่วย (คืนสิทธิ์การจอง)")
                col_ub1, col_ub2 = st.columns([3, 1])
                unblock_id = col_ub1.number_input("ระบุ 'รหัส (ID)' ในตารางที่ต้องการปลดบล็อก", min_value=1, step=1)
                col_ub2.markdown("### ")
                if col_ub2.button("🔓 ปลดบล็อกทันที", type="primary", use_container_width=True):
                    c = conn.cursor()
                    c.execute("DELETE FROM blacklist WHERE id = ?", (unblock_id,))
                    if c.rowcount > 0:
                        conn.commit()
                        st.success(f"✅ ปลดบล็อกรหัส {unblock_id} เรียบร้อยแล้ว")
                        st.rerun()
                    else:
                        st.warning(f"ไม่พบข้อมูลรหัส {unblock_id}")
            else:
                st.info("ไม่มีรายชื่อผู้ถูกระงับสิทธิ์ในขณะนี้")
                
        with tab_bl2:
            st.markdown("##### ➕ เพิ่มรายชื่อผู้ป่วยเข้าสู่ระบบระงับสิทธิ์")
            patients_list = pd.read_sql_query("SELECT id, full_name, id_card, phone FROM patients ORDER BY full_name ASC", conn)
            
            with st.form("form_manual_blacklist"):
                if not patients_list.empty:
                    patient_options = {row['id']: f"{row['full_name']} (บัตร: {row['id_card']}, โทร: {row['phone']})" for _, row in patients_list.iterrows()}
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

    # 7. No-Show
    elif menu == "📋 ประวัติ No-Show":
        st.subheader("📋 บันทึกประวัติผู้ไม่มาตามนัดหมาย")
        df_ns = pd.read_sql_query('''
            SELECT nr.id as รหัส, p.full_name as ชื่อผู้ป่วย, p.id_card as เลขบัตร, p.phone as เบอร์โทร, 
                   nr.appointment_date as วันที่นัด, nr.reported_by as ผู้รายงาน, nr.notes as บันทึก
            FROM no_show_records nr JOIN patients p ON nr.patient_id = p.id
            ORDER BY nr.appointment_date DESC
        ''', conn)
        if not df_ns.empty:
            st.dataframe(df_ns, use_container_width=True)
        else:
            st.info("ยังไม่มีประวัติการไม่มาตามนัด")

    conn.close()

# ========== ควบคุมการทำงานหลัก ==========
def main():
    if 'confirm' in st.query_params:
        handle_confirmation()
        return

    st.sidebar.markdown("### 🦷 ศบส.65 รักษาศุข บางบอน")
    page = st.sidebar.radio("เลือกหน้าต่างทำงาน", ["📅 นัดหมายบริการ", "⚙️ ผู้ดูแลระบบ"])
    
    if page == "📅 นัดหมายบริการ":
        show_booking_form()
    elif page == "⚙️ ผู้ดูแลระบบ":
        show_admin_dashboard()

if __name__ == "__main__":
    main()