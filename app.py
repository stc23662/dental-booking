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
    page_title="ระบบจองคิวทันตกรรมออนไลน์",
    page_icon="🦷",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ========== Custom CSS สไตล์ Medical Modern ==========
CUSTOM_CSS = """
<style>
    @import url('https://fonts.googleapis.com/css2?family=Sarabun:wght@300;400;500;600;700&display=swap');
    html, body, [class*="css"] { font-family: 'Sarabun', sans-serif; }
    
    .hero-banner {
        background: linear-gradient(135deg, #0ea5e9 0%, #0284c7 50%, #0369a1 100%);
        color: white;
        padding: 2.2rem 2rem;
        border-radius: 16px;
        margin-bottom: 2rem;
        box-shadow: 0 10px 25px -5px rgba(14, 165, 233, 0.25);
    }
    .hero-banner h1 { margin: 0; font-size: 2rem; font-weight: 700; color: white; }
    .hero-banner p { margin-top: 0.5rem; margin-bottom: 0; font-size: 1.05rem; opacity: 0.95; }

    [data-testid="stMetric"] {
        background: #ffffff;
        border: 1px solid #e2e8f0;
        padding: 1.25rem 1.5rem;
        border-radius: 12px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.02);
    }
    [data-testid="stForm"] {
        background: #ffffff;
        border: 1px solid #e2e8f0;
        padding: 2.2rem;
        border-radius: 16px;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.03);
    }
    button[kind="primary"] {
        background: linear-gradient(135deg, #0284c7 0%, #0369a1 100%) !important;
        border: none !important;
        border-radius: 10px !important;
        padding: 0.6rem 1.5rem !important;
        font-weight: 600 !important;
        box-shadow: 0 4px 12px rgba(2, 132, 199, 0.3) !important;
    }
    .clinic-card {
        background-color: #f0f9ff;
        border-left: 4px solid #0284c7;
        padding: 1rem 1.25rem;
        border-radius: 0 10px 10px 0;
        margin: 1rem 0;
    }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

DB_PATH = "clinic.db"

# ========== ฐานข้อมูล ==========
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
            appointment_time TIME NOT NULL,
            status TEXT DEFAULT 'pending',
            token TEXT UNIQUE,
            reminder_sent INTEGER DEFAULT 0,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (patient_id) REFERENCES patients (id)
        )
    ''')
    
    # 3. บริการ
    c.execute('''
        CREATE TABLE IF NOT EXISTS services (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            duration_minutes INTEGER DEFAULT 30,
            is_active INTEGER DEFAULT 1
        )
    ''')

    # ตรวจสอบและปรับปรุงโครงสร้างตาราง daily_schedule เดิมอัตโนมัติ
    c.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='daily_schedule'")
    table_exists = c.fetchone()[0] > 0
    
    need_recreate = False
    if table_exists:
        c.execute("PRAGMA table_info(daily_schedule)")
        columns = [row[1] for row in c.fetchall()]
        if "id" not in columns:
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
                slot_duration INTEGER DEFAULT 30,
                max_patients INTEGER DEFAULT 4,
                note TEXT
            )
        ''')
        c.execute('''
            INSERT INTO daily_schedule (schedule_date, is_open, start_time, end_time, note)
            SELECT schedule_date, is_open, start_time, end_time, note FROM daily_schedule_old
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
                slot_duration INTEGER DEFAULT 30,
                max_patients INTEGER DEFAULT 4,
                note TEXT
            )
        ''')

    # 4. Blacklist
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

    # 5. บันทึกประวัติ No-Show
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
    
    c.execute("SELECT COUNT(*) FROM services")
    if c.fetchone()[0] == 0:
        services = [
            ("ตรวจฟัน", 30),
            ("ขูดหินปูน", 45),
            ("อุดฟัน", 30),
            ("ถอนฟัน", 60),
            ("ผ่าฟันคุด", 120),
            ("รักษารากฟัน", 90),
            ("ทำความสะอาด", 45)
        ]
        c.executemany("INSERT INTO services (name, duration_minutes) VALUES (?, ?)", services)
    
    conn.commit()
    conn.close()

init_database()

# ========== ฟังก์ชันส่งอีเมล ==========
def send_email(to_email: str, subject: str, body: str, cc_email: str = "dental665@gmail.com") -> bool:
    try:
        sender_email = st.secrets["email"]["sender"]
        sender_password = st.secrets["email"]["password"]
        
        msg = MIMEMultipart()
        msg['From'] = f"คลินิกทันตกรรม <{sender_email}>"
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

# ========== คำนวณช่วงเวลาว่าง ==========
def get_available_slots(appointment_date: date, service_duration: int = 30):
    conn = sqlite3.connect(DB_PATH)
    date_str = appointment_date.strftime('%Y-%m-%d')
    c = conn.cursor()
    
    c.execute('''
        SELECT is_open, start_time, end_time, slot_duration, max_patients, note 
        FROM daily_schedule 
        WHERE schedule_date = ?
        ORDER BY start_time ASC
    ''', (date_str,))
    schedule_records = c.fetchall()
    
    if not schedule_records:
        conn.close()
        return [], "คลินิกยังไม่ได้เปิดรับจองในวันนี้"
        
    if any(r[0] == 0 for r in schedule_records):
        note = next((r[5] for r in schedule_records if r[0] == 0 and r[5]), "ปิดทำการพิเศษ")
        conn.close()
        return [], f"ปิดทำการ ({note})"
    
    all_slots = []
    for _, start_str, end_str, slot_dur, max_patients, _ in schedule_records:
        if not start_str or not end_str:
            continue
        start_time = datetime.strptime(start_str, '%H:%M').time()
        end_time = datetime.strptime(end_str, '%H:%M').time()
        
        current = datetime.combine(date.today(), start_time)
        end_dt = datetime.combine(date.today(), end_time)
        
        step_minutes = slot_dur if slot_dur else 30
        
        while current + timedelta(minutes=service_duration) <= end_dt:
            slot_time = current.time().strftime('%H:%M')
            
            c.execute('''
                SELECT COUNT(*) FROM appointments 
                WHERE appointment_date = ? AND appointment_time = ? AND status IN ('pending', 'confirmed')
            ''', (date_str, slot_time))
            booked_count = c.fetchone()[0]
            
            if booked_count < max_patients:
                all_slots.append({
                    'time': slot_time,
                    'available': max_patients - booked_count,
                    'total_slots': max_patients
                })
            current += timedelta(minutes=step_minutes)
            
    conn.close()
    return all_slots, "เปิดทำการ"

# ========== หน้าจองคิว ==========
def show_booking_form():
    st.markdown("""
        <div class="hero-banner">
            <h1>🦷 จองคิวรับบริการทันตกรรม</h1>
            <p>กรุณากรอกข้อมูลส่วนตัว เลือกหัตถการ และนัดหมายวันเวลาที่เปิดรับบริการ</p>
        </div>
    """, unsafe_allow_html=True)
    
    with st.form("booking_form", clear_on_submit=False):
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
        services_df = pd.read_sql_query("SELECT id, name, duration_minutes FROM services WHERE is_active = 1", conn)
        conn.close()
        
        col_srv1, col_srv2 = st.columns([2, 1])
        with col_srv1:
            service_name = st.selectbox("บริการที่ต้องการรับการรักษา *", services_df['name'].tolist())
        with col_srv2:
            service_duration = int(services_df[services_df['name'] == service_name]['duration_minutes'].iloc[0])
            st.markdown(f"""
                <div class="clinic-card" style="margin: 0; padding: 0.6rem 1rem;">
                    ⏱️ <b>ระยะเวลาตรวจ:</b> {service_duration} นาที
                </div>
            """, unsafe_allow_html=True)
        
        col_date, col_slot = st.columns(2)
        with col_date:
            min_date = date.today()
            max_date = min_date + timedelta(days=120)
            appointment_date = st.date_input("เลือกวันที่ต้องการนัดหมาย *", min_value=min_date, max_value=max_date, value=min_date + timedelta(days=1))
            
        available_slots, status_msg = get_available_slots(appointment_date, service_duration)
        
        with col_slot:
            if not available_slots:
                st.selectbox("ช่วงเวลา *", [f"⛔ {status_msg}"], disabled=True)
                appointment_time = None
            else:
                slot_options = [f"{slot['time']} น. (ว่าง {slot['available']}/{slot['total_slots']} ที่)" for slot in available_slots]
                selected_slot = st.selectbox("เลือกช่วงเวลานัดหมาย *", slot_options)
                appointment_time = selected_slot.split(" ")[0]

        notes = st.text_area("หมายเหตุเพิ่มเติม / อาการเบื้องต้น / โรคประจำตัว (ถ้ามี)")
        st.markdown("<br>", unsafe_allow_html=True)
        submitted = st.form_submit_button("📅 ยืนยันข้อมูลและส่งคำขอจองคิว", type="primary", use_container_width=True)

    if submitted:
        if not available_slots or not appointment_time:
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

        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        try:
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
                INSERT INTO appointments (patient_id, service_type, appointment_date, appointment_time, status, token, notes)
                VALUES (?, ?, ?, ?, 'pending', ?, ?)
            ''', (patient_id, service_name, appointment_date.strftime('%Y-%m-%d'), appointment_time, token, notes))
            appointment_id = c.lastrowid
            conn.commit()

            base_url = "https://your-app.streamlit.app"
            confirmation_url = f"{base_url}/?confirm={token}"
            
            email_body = f"""
            <div style="font-family: Arial, sans-serif; line-height: 1.6; max-width: 600px; margin: auto; padding: 24px; border: 1px solid #e2e8f0; border-radius: 12px; background-color: #ffffff;">
                <div style="background: #0284c7; padding: 16px; border-radius: 8px; text-align: center; color: white;">
                    <h2 style="margin:0;">ยืนยันการนัดหมายทันตกรรม</h2>
                </div>
                <p style="margin-top: 20px;">เรียนคุณ <b>{full_name}</b>,</p>
                <p>ระบบได้รับคำขอจองคิวของท่านแล้ว รายละเอียด:</p>
                <ul>
                    <li><b>บริการ:</b> {service_name}</li>
                    <li><b>วันที่:</b> {appointment_date.strftime('%d/%m/%Y')}</li>
                    <li><b>เวลา:</b> {appointment_time} น.</li>
                </ul>
                <div style="text-align: center; margin: 30px 0;">
                    <a href="{confirmation_url}" style="background-color: #16a34a; color: white; padding: 12px 30px; text-decoration: none; border-radius: 8px; font-weight: bold; display: inline-block;">
                        ✅ กดยืนยันการนัดหมาย
                    </a>
                </div>
                <hr style="border: 0; border-top: 1px solid #e2e8f0; margin: 20px 0;">
                <p style="font-size: 12px; color: #94a3b8; text-align: center; margin: 0;">คลินิกทันตกรรม | ติดต่อสอบถาม: dental665@gmail.com</p>
            </div>
            """
            send_email(email.strip(), "ยืนยันการนัดหมายทันตกรรม", email_body)
            st.success(f"🎉 **จองคิวสำเร็จ!** หมายเลขอ้างอิง `APPT-{appointment_id:05d}` กรุณาตรวจสอบอีเมลเพื่อกดยืนยันนัดหมาย")
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
        SELECT a.id, p.full_name, a.appointment_date, a.appointment_time, a.status 
        FROM appointments a
        JOIN patients p ON a.patient_id = p.id
        WHERE a.token = ?
    ''', (token,))
    appointment = c.fetchone()
    
    st.markdown("""<div class="hero-banner"><h1>🦷 ผลการยืนยันนัดหมาย</h1></div>""", unsafe_allow_html=True)
    if appointment:
        appt_id, name, appt_date, appt_time, status = appointment
        if status == 'pending':
            c.execute("UPDATE appointments SET status = 'confirmed' WHERE id = ?", (appt_id,))
            conn.commit()
            st.success(f"✅ **ยืนยันนัดหมายสำเร็จ!** คุณ {name} สำหรับวันที่ {appt_date} เวลา {appt_time} น.")
        elif status == 'confirmed':
            st.info("ℹ️ นัดหมายนี้ได้รับการยืนยันเรียบร้อยแล้ว")
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
        ["📊 ภาพรวมสถิติ", "📅 จัดการคิวนัดหมาย", "🗓️ จัดการ Slot และปฏิทินรายเดือน", "👥 ทะเบียนผู้ป่วย", 
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
        st.subheader("📋 ตารางนัดหมายประจำวันนี้")
        df_today = pd.read_sql_query('''
            SELECT a.appointment_time as เวลา, p.full_name as ชื่อผู้ป่วย, a.service_type as บริการ, 
                   a.status as สถานะ, p.phone as เบอร์โทรศัพท์, a.notes as หมายเหตุ
            FROM appointments a JOIN patients p ON a.patient_id = p.id
            WHERE a.appointment_date = ? ORDER BY a.appointment_time
        ''', conn, params=(today,))
        
        # ปรับเป็น if-else บล็อกมาตรฐาน ป้องกันบั๊ก DeltaGenerator
        if not df_today.empty:
            st.dataframe(df_today, use_container_width=True)
        else:
            st.info("ไม่มีรายการนัดหมายในวันนี้")

    # 2. จัดการคิวนัดหมาย
    elif menu == "📅 จัดการคิวนัดหมาย":
        col1, col2 = st.columns(2)
        start_d = col1.date_input("ตั้งแต่วันที่", value=date.today())
        end_d = col2.date_input("ถึงวันที่", value=date.today() + timedelta(days=7))
        
        df_appts = pd.read_sql_query('''
            SELECT a.id as รหัสนัด, a.appointment_date as วันที่, a.appointment_time as เวลา, 
                   p.full_name as ชื่อผู้ป่วย, p.phone as โทรศัพท์, a.service_type as บริการ, a.status as สถานะ
            FROM appointments a JOIN patients p ON a.patient_id = p.id
            WHERE a.appointment_date BETWEEN ? AND ? ORDER BY a.appointment_date, a.appointment_time
        ''', conn, params=(start_d.strftime('%Y-%m-%d'), end_d.strftime('%Y-%m-%d')))
        st.dataframe(df_appts, use_container_width=True)

        st.markdown("---")
        st.subheader("เปลี่ยนสถานะนัดหมาย")
        c1, c2, c3 = st.columns([1, 2, 1])
        appt_id = c1.number_input("รหัสนัดหมาย (ID)", min_value=1, step=1)
        new_status = c2.selectbox("สถานะใหม่", [
            ("pending", "รอยืนยัน (Pending)"),
            ("confirmed", "ยืนยันแล้ว (Confirmed)"),
            ("completed", "เข้ารับบริการแล้ว (Completed)"),
            ("no_show", "ไม่มาตามนัด (No-Show)"),
            ("cancelled", "ยกเลิกนัด (Cancelled)")
        ], format_func=lambda x: x[1])
        
        c3.markdown("### ")
        if c3.button("💾 บันทึก", type="primary", use_container_width=True):
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

    # 3. จัดการ Slot และปฏิทินรายเดือน
    elif menu == "🗓️ จัดการ Slot และปฏิทินรายเดือน":
        st.subheader("🗓️ กำหนด Slot ย่อย และเปิด-ปิดทำการแบบรายเดือน")
        
        tab1, tab2, tab3 = st.tabs(["⚡ สร้าง Slot เหมายกเดือน/ซอยเวลา", "🚫 สั่งปิดทำการทั้งวัน", "📋 ดูและลบตารางเวลาปัจจุบัน"])
        
        with tab1:
            st.markdown("##### กำหนดช่วงเวลาและจำนวนคิวไปยังวันต่างๆ พร้อมกัน")
            with st.form("batch_slot_form"):
                c_d1, c_d2 = st.columns(2)
                start_range = c_d1.date_input("ตั้งแต่วันที่", value=date.today())
                end_range = c_d2.date_input("จนถึงวันที่", value=date.today() + timedelta(days=30))
                
                day_map = {0: "จันทร์", 1: "อังคาร", 2: "พุธ", 3: "พฤหัสบดี", 4: "ศุกร์", 5: "เสาร์", 6: "อาทิตย์"}
                selected_weekdays = st.multiselect(
                    "เลือกใช้วันไหนบ้างในสัปดาห์ *",
                    options=list(day_map.keys()),
                    default=[0, 1, 2, 3, 4],
                    format_func=lambda x: day_map[x]
                )
                
                st.markdown("---")
                st.markdown("###### ⏰ ระบุช่วงเวลาที่ต้องการซอย และโควตาคิว")
                col_t1, col_t2, col_t3, col_t4 = st.columns(4)
                t_start = col_t1.time_input("เวลาเริ่ม", value=time(16, 0))
                t_end = col_t2.time_input("เวลาสิ้นสุด", value=time(17, 0))
                q_cap = col_t3.number_input("จำนวนรับสูงสุด (คิว)", min_value=1, max_value=50, value=6)
                slot_step = col_t4.selectbox("ความยาว Slot ย่อย (นาที)", [15, 30, 45, 60], index=3)
                
                batch_submit = st.form_submit_button("🚀 บันทึกช่วงเวลานี้ลงปฏิทิน", type="primary")
                
                if batch_submit:
                    if start_range > end_range:
                        st.error("วันที่เริ่มต้นต้องไม่มากกว่าวันที่สิ้นสุด")
                    elif not selected_weekdays:
                        st.error("กรุณาเลือกวันในสัปดาห์อย่างน้อย 1 วัน")
                    else:
                        c = conn.cursor()
                        cur_dt = start_range
                        count_days = 0
                        
                        while cur_dt <= end_range:
                            if cur_dt.weekday() in selected_weekdays:
                                c.execute('''
                                    INSERT INTO daily_schedule (schedule_date, is_open, start_time, end_time, slot_duration, max_patients, note)
                                    VALUES (?, 1, ?, ?, ?, ?, 'เปิดทำการ')
                                ''', (cur_dt.strftime('%Y-%m-%d'), t_start.strftime('%H:%M'), t_end.strftime('%H:%M'), slot_step, q_cap))
                                count_days += 1
                            cur_dt += timedelta(days=1)
                            
                        conn.commit()
                        st.success(f"✅ เพิ่ม Slot ช่วงเวลา {t_start.strftime('%H:%M')}-{t_end.strftime('%H:%M')} น. ({q_cap} คิว) รวม {count_days} วัน เรียบร้อยแล้ว")
                        st.rerun()

        with tab2:
            st.markdown("##### กำหนดวันหยุดพิเศษ / ปิดทำการทั้งวัน")
            with st.form("close_day_form"):
                col_close1, col_close2 = st.columns(2)
                close_date = col_close1.date_input("เลือกวันที่ต้องการปิดทำการ", value=date.today())
                close_note = col_close2.text_input("สาเหตุการปิด", placeholder="เช่น วันหยุดนักขัตฤกษ์, ซ่อมยูนิต")
                
                close_submit = st.form_submit_button("🔴 สั่งปิดทำการวันนี้", type="primary")
                if close_submit:
                    c = conn.cursor()
                    c.execute("DELETE FROM daily_schedule WHERE schedule_date = ?", (close_date.strftime('%Y-%m-%d'),))
                    c.execute('''
                        INSERT INTO daily_schedule (schedule_date, is_open, note)
                        VALUES (?, 0, ?)
                    ''', (close_date.strftime('%Y-%m-%d'), close_note))
                    conn.commit()
                    st.success(f"กำหนดให้วันที่ {close_date.strftime('%d/%m/%Y')} ปิดทำการทั้งวันเรียบร้อย")
                    st.rerun()

        with tab3:
            st.markdown("##### ตรวจสอบและจัดการ Slot ในระบบ")
            view_month = st.date_input("เลือกดู Slot ตั้งแต่วันที่", value=date.today())
            
            df_sched = pd.read_sql_query('''
                SELECT id as รหัส, schedule_date as วันที่,
                       CASE WHEN is_open = 1 THEN '🟢 เปิด' ELSE '🔴 ปิด' END as สถานะ,
                       start_time as เวลาเริ่ม, end_time as เวลาสิ้นสุด, max_patients as 'รับได้ (คิว)', note as หมายเหตุ
                FROM daily_schedule
                WHERE schedule_date >= ?
                ORDER BY schedule_date ASC, start_time ASC
            ''', conn, params=(view_month.strftime('%Y-%m-%d'),))
            
            st.dataframe(df_sched, use_container_width=True)
            
            st.markdown("---")
            c_del1, c_del2 = st.columns(2)
            del_id = c_del1.number_input("ใส่ 'รหัส (ID)' ของ Slot ที่ต้องการลบ", min_value=1, step=1)
            if c_del1.button("🗑️ ลบเฉพาะ Slot นี้"):
                c = conn.cursor()
                c.execute("DELETE FROM daily_schedule WHERE id = ?", (del_id,))
                conn.commit()
                st.success(f"ลบ Slot รหัส {del_id} เรียบร้อยแล้ว")
                st.rerun()
                
            del_date = c_del2.date_input("หรือเลือกลบทุก Slot ของวันนั้น", value=date.today())
            if c_del2.button("🗑️ ล้าง Slot ทั้งหมดของวันนี้"):
                c = conn.cursor()
                c.execute("DELETE FROM daily_schedule WHERE schedule_date = ?", (del_date.strftime('%Y-%m-%d'),))
                conn.commit()
                st.success(f"ล้างตารางเวลาของวันที่ {del_date.strftime('%d/%m/%Y')} ทั้งหมดเรียบร้อย")
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
                SELECT p.email, p.full_name, a.appointment_time, a.service_type, a.id
                FROM appointments a JOIN patients p ON a.patient_id = p.id
                WHERE a.appointment_date = ? AND a.status = 'confirmed' AND a.reminder_sent = 0
            ''', (tomorrow,))
            targets = c.fetchall()
            
            sent_count = 0
            for email_addr, name, appt_t, srv, appt_id in targets:
                body = f"""
                <div style="font-family: Arial, sans-serif; padding: 20px; border: 1px solid #e2e8f0; border-radius: 8px;">
                    <h3 style="color: #0284c7;">⏰ แจ้งเตือนนัดหมายทันตกรรมวันพรุ่งนี้</h3>
                    <p>เรียนคุณ <b>{name}</b>,</p>
                    <p>ท่านมีนัดหมายบริการ <b>{srv}</b> ในวันพรุ่งนี้ ({tomorrow}) เวลา <b>{appt_t} น.</b></p>
                    <p>กรุณาเดินทางมาถึงก่อนเวลานัดหมาย 15 นาที</p>
                </div>
                """
                if send_email(email_addr, "เตือนนัดหมายทันตกรรม (ล่วงหน้า 1 วัน)", body):
                    c.execute("UPDATE appointments SET reminder_sent = 1 WHERE id = ?", (appt_id,))
                    sent_count += 1
            conn.commit()
            st.success(f"ส่งการแจ้งเตือนสำเร็จทั้งหมด {sent_count}/{len(targets)} รายการ")

    # 6. Blacklist
    elif menu == "🚫 จัดการ Blacklist":
        st.subheader("🚫 รายชื่อผู้ถูกระงับสิทธิ์การจอง")
        df_bl = pd.read_sql_query('''
            SELECT b.id as รหัส, p.full_name as ชื่อ, b.id_card as เลขบัตร, b.phone as เบอร์โทร, 
                   b.reason as สาเหตุ, b.no_show_count as ครั้งที่ผิดนัด, b.blacklisted_until as ระงับถึงวันที่,
                   CASE WHEN DATE(b.blacklisted_until) < DATE('now') THEN 'หมดอายุ' ELSE 'กำลังลงโทษ' END as สถานะ
            FROM blacklist b JOIN patients p ON b.patient_id = p.id
            ORDER BY b.blacklisted_until DESC
        ''', conn)
        if not df_bl.empty:
            st.dataframe(df_bl, use_container_width=True)
        else:
            st.info("ไม่มีรายชื่อผู้ถูกระงับสิทธิ์ในขณะนี้")

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

    st.sidebar.markdown("### 🦷 ทันตกรรมออนไลน์")
    page = st.sidebar.radio("เลือกหน้าต่างทำงาน", ["📅 นัดหมายบริการ", "⚙️ ผู้ดูแลระบบ"])
    
    if page == "📅 นัดหมายบริการ":
        show_booking_form()
    elif page == "⚙️ ผู้ดูแลระบบ":
        show_admin_dashboard()

if __name__ == "__main__":
    main()