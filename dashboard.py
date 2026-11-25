import os
import streamlit as st
import pandas as pd
import plotly.express as px
from dotenv import load_dotenv
from supabase import create_client

# ตั้งค่าหน้าเว็บ
st.set_page_config(
    page_title="TaxMate AI Dashboard",
    page_icon="🧾",
    layout="wide",
    initial_sidebar_state="expanded"
)
load_dotenv()

# Custom CSS: ฟอนต์ Bai Jamjuree + แต่งการ์ดสไตล์ Cyber-Blue
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Bai+Jamjuree:wght@300;400;500;600;700&display=swap');

html, body, [class*="css"] {
    font-family: 'Bai Jamjuree', sans-serif !important;
}

/* แต่งการ์ด KPI Metrics */
div[data-testid="stMetric"] {
    background: linear-gradient(135deg, #131C31 0%, #1B2845 100%);
    border: 1px solid rgba(0, 212, 255, 0.25);
    border-left: 4px solid #00D4FF;
    padding: 18px 20px;
    border-radius: 12px;
    box-shadow: 0 4px 15px rgba(0, 0, 0, 0.25);
}

div[data-testid="stMetricLabel"] {
    color: #94A3B8 !important;
    font-size: 0.95rem !important;
}

div[data-testid="stMetricValue"] {
    color: #F8FAFC !important;
    font-weight: 600 !important;
}

/* หัวข้อหลัก */
.main-header {
    background: linear-gradient(90deg, #00D4FF, #3B82F6);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    font-size: 2.3rem;
    font-weight: 700;
    margin-bottom: 0;
}
.sub-header {
    color: #94A3B8;
    font-size: 1rem;
    margin-bottom: 1.5rem;
}
</style>
""", unsafe_allow_html=True)

# เชื่อมต่อ Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL") or st.secrets.get("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY") or st.secrets.get("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ส่วนหัวของหน้าเว็บ
st.markdown('<p class="main-header">🧾 TaxMate AI Dashboard</p>', unsafe_allow_html=True)
st.markdown('<p class="sub-header">ระบบติดตามค่าใช้จ่ายและบริหารสิทธิ์ลดหย่อนภาษีอัจฉริยะ</p>', unsafe_allow_html=True)

@st.cache_data(ttl=60)
def load_data():
    receipts_res = supabase.table("receipts").select("*").order("transaction_date", desc=True).execute()
    df_receipts = pd.DataFrame(receipts_res.data)

    # ดึงชื่อ Workspace และชื่อผู้ใช้มาจับคู่
    ws_map = {}
    user_map = {}
    try:
        ws_res = supabase.table("workspaces").select("id, name").execute()
        ws_map = {str(w["id"]): w["name"] for w in ws_res.data}
    except Exception:
        pass

    try:
        users_res = supabase.table("users").select("discord_user_id, display_name").execute()
        user_map = {str(u["discord_user_id"]): u["display_name"] for u in users_res.data}
    except Exception:
        pass

    if not df_receipts.empty:
        if "workspace_id" in df_receipts.columns:
            df_receipts["workspace_name"] = df_receipts["workspace_id"].apply(
                lambda x: ws_map.get(str(x), "ส่วนตัว (Personal)") if pd.notna(x) and str(x) != "" else "ส่วนตัว (Personal)"
            )
        else:
            df_receipts["workspace_name"] = "ส่วนตัว (Personal)"

        df_receipts["user_display"] = df_receipts["discord_user_id"].apply(
            lambda uid: user_map.get(str(uid), f"User #{str(uid)[-4:]}")
        )

    return df_receipts

# ปุ่มรีเฟรชข้อมูลด้านซ้าย
with st.sidebar:
    st.header("⚙️ เมนูและตัวกรอง")
    if st.button("🔄 รีเฟรชข้อมูลล่าสุด", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

df = load_data()

if df.empty:
    st.info("📭 ยังไม่พบข้อมูลบิลในระบบ ลองส่งสลิปผ่าน Discord Bot ก่อนนะครับ")
    st.stop()

# แปลงชนิดข้อมูล
df["total_amount"] = df["total_amount"].astype(float)
df["transaction_date_dt"] = pd.to_datetime(df["transaction_date"])
df["transaction_date"] = df["transaction_date_dt"].dt.strftime("%Y-%m-%d")

# แถบตัวกรองด้านซ้าย (Sidebar)
with st.sidebar:
    st.divider()
    workspaces = ["ทั้งหมด"] + sorted(df["workspace_name"].dropna().unique().tolist())
    selected_ws = st.selectbox("🏠 เลือกพื้นที่ / กองกลาง", workspaces)

    users_list = ["ทั้งหมด"] + sorted(df["user_display"].dropna().unique().tolist())
    selected_user = st.selectbox("👤 เลือกรายบุคคล", users_list)

    search_query = st.text_input("🔎 ค้นหาชื่อร้านค้า / ผู้รับ", placeholder="พิมพ์ชื่อร้านค้า...")

    categories = ["ทั้งหมด"] + sorted(df["category"].dropna().unique().tolist())
    selected_cat = st.selectbox("📁 เลือกหมวดหมู่", categories)

    min_date = df["transaction_date_dt"].min().date()
    max_date = df["transaction_date_dt"].max().date()
    date_range = st.date_input("📅 ช่วงวันที่", value=(min_date, max_date), min_value=min_date, max_value=max_date)

    tax_only = st.checkbox("🧾 แสดงเฉพาะรายการลดหย่อนภาษีได้")

    st.divider()
    tax_goal = st.number_input("🎯 เป้าหมายลดหย่อนภาษี (บาท)", min_value=1000, value=50000, step=5000)

# กรองข้อมูลตามเงื่อนไข
filtered_df = df.copy()
if selected_ws != "ทั้งหมด":
    filtered_df = filtered_df[filtered_df["workspace_name"] == selected_ws]
if selected_user != "ทั้งหมด":
    filtered_df = filtered_df[filtered_df["user_display"] == selected_user]
if search_query:
    filtered_df = filtered_df[filtered_df["merchant_name"].str.contains(search_query, case=False, na=False)]
if selected_cat != "ทั้งหมด":
    filtered_df = filtered_df[filtered_df["category"] == selected_cat]
if isinstance(date_range, tuple) and len(date_range) == 2:
    start_d, end_d = date_range
    filtered_df = filtered_df[
        (filtered_df["transaction_date_dt"].dt.date >= start_d) &
        (filtered_df["transaction_date_dt"].dt.date <= end_d)
    ]
if tax_only:
    filtered_df = filtered_df[filtered_df["is_tax_deductible"] == True]

# 1. การ์ดสรุปตัวเลขหลัก (KPI Metrics)
total_spent = filtered_df["total_amount"].sum()
total_tax = filtered_df[filtered_df["is_tax_deductible"] == True]["total_amount"].sum()
total_count = len(filtered_df)

col1, col2, col3 = st.columns(3)
col1.metric("💸 ยอดใช้จ่ายรวม", f"{total_spent:,.2f} ฿")
col2.metric("🧾 ยอดลดหย่อนภาษีได้", f"{total_tax:,.2f} ฿")
col3.metric("📄 จำนวนเอกสาร", f"{total_count:,} รายการ")

# 2. แถบความคืบหน้าลดหย่อนภาษี (Tax Progress Bar)
st.markdown("### 🎯 ความคืบหน้าเป้าหมายลดหย่อนภาษี")
progress_pct = min(total_tax / tax_goal, 1.0) if tax_goal > 0 else 0.0
st.progress(progress_pct)
st.caption(f"สะสมแล้ว **{total_tax:,.2f} บาท** จากเป้าหมาย **{tax_goal:,.2f} บาท** ({progress_pct * 100:.1f}%)")

st.divider()

# 3. กราฟแสดงผลแถวที่ 1 (หมวดหมู่ + รายวัน)
cyber_palette = ["#00D4FF", "#3B82F6", "#6366F1", "#0EA5E9", "#38BDF8", "#818CF8"]
chart_col1, chart_col2 = st.columns(2)

with chart_col1:
    st.subheader("📁 สัดส่วนค่าใช้จ่ายตามหมวดหมู่")
    if not filtered_df.empty:
        cat_sum = filtered_df.groupby("category", as_index=False)["total_amount"].sum()
        fig_pie = px.pie(
            cat_sum, values="total_amount", names="category",
            hole=0.45, color_discrete_sequence=cyber_palette
        )
        fig_pie.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(family="Bai Jamjuree", color="#E2E8F0"),
            margin=dict(t=20, b=20, l=20, r=20)
        )
        st.plotly_chart(fig_pie, use_container_width=True)
    else:
        st.info("ไม่มีข้อมูลในช่วงที่เลือก")

with chart_col2:
    st.subheader("📅 ยอดใช้จ่ายรายวัน")
    if not filtered_df.empty:
        daily_sum = filtered_df.groupby("transaction_date", as_index=False)["total_amount"].sum()
        fig_bar = px.bar(
            daily_sum, x="transaction_date", y="total_amount",
            labels={"transaction_date": "วันที่", "total_amount": "ยอดเงิน (บาท)"},
            color_discrete_sequence=["#00D4FF"]
        )
        fig_bar.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(family="Bai Jamjuree", color="#E2E8F0"),
            xaxis=dict(type="category", showgrid=False),
            yaxis=dict(gridcolor="rgba(255,255,255,0.08)"),
            margin=dict(t=20, b=20, l=20, r=20)
        )
        st.plotly_chart(fig_bar, use_container_width=True)
    else:
        st.info("ไม่มีข้อมูลในช่วงที่เลือก")

# 3.1 กราฟแสดงผลแถวที่ 2 (แยกตามรายบุคคล)
st.subheader("👤 ยอดใช้จ่ายแยกตามรายบุคคล")
if not filtered_df.empty:
    user_sum = (
        filtered_df.groupby("user_display", as_index=False)["total_amount"]
        .sum()
        .sort_values("total_amount", ascending=True)
    )
    fig_user = px.bar(
        user_sum,
        x="total_amount",
        y="user_display",
        orientation="h",
        text_auto=",.2f",
        labels={"user_display": "ผู้ใช้งาน", "total_amount": "ยอดเงินรวม (บาท)"},
        color_discrete_sequence=["#3B82F6"]
    )
    fig_user.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Bai Jamjuree", color="#E2E8F0"),
        xaxis=dict(gridcolor="rgba(255,255,255,0.08)"),
        yaxis=dict(showgrid=False),
        margin=dict(t=10, b=20, l=20, r=20),
        height=max(220, len(user_sum) * 55 + 100)
    )
    st.plotly_chart(fig_user, use_container_width=True)
else:
    st.info("ไม่มีข้อมูลในช่วงที่เลือก")

st.divider()

# 4. ตารางรายการและปุ่มดาวน์โหลด CSV
header_col, btn_col = st.columns([3, 1])
with header_col:
    st.subheader("📋 ประวัติเอกสารทั้งหมด")
with btn_col:
    export_df = filtered_df[[
        "transaction_date", "workspace_name", "user_display", "doc_type",
        "merchant_name", "category", "total_amount", "is_tax_deductible", "image_url"
    ]].rename(columns={
        "transaction_date": "วันที่ทำรายการ",
        "workspace_name": "พื้นที่ / กองกลาง",
        "user_display": "ผู้บันทึก",
        "doc_type": "ประเภทเอกสาร",
        "merchant_name": "ร้านค้า / ผู้รับ",
        "category": "หมวดหมู่",
        "total_amount": "ยอดเงิน (บาท)",
        "is_tax_deductible": "ลดหย่อนภาษีได้",
        "image_url": "ลิงก์เอกสารต้นฉบับ"
    })
    csv_data = export_df.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        label="📥 ดาวน์โหลด CSV",
        data=csv_data,
        file_name="TaxMate_Dashboard_Export.csv",
        mime="text/csv",
        use_container_width=True
    )

display_df = filtered_df[[
    "transaction_date", "workspace_name", "user_display", "doc_type",
    "merchant_name", "category", "total_amount", "is_tax_deductible", "image_url"
]]

st.dataframe(
    display_df,
    column_config={
        "transaction_date": "วันที่",
        "workspace_name": "พื้นที่ / กองกลาง",
        "user_display": "ผู้บันทึก",
        "doc_type": "ประเภท",
        "merchant_name": "ร้านค้า / ผู้รับ",
        "category": "หมวดหมู่",
        "total_amount": st.column_config.NumberColumn("ยอดเงิน (บาท)", format="%.2f ฿"),
        "is_tax_deductible": "ลดหย่อนภาษี",
        "image_url": st.column_config.LinkColumn("เอกสารต้นฉบับ", display_text="เปิดดูไฟล์ 🔗"),
    },
    use_container_width=True,
    hide_index=True
)