import os
import streamlit as st
import pandas as pd
import plotly.express as px
from dotenv import load_dotenv
from supabase import create_client

# ตั้งค่าหน้าเว็บ
st.set_page_config(page_title="TaxMate AI Dashboard", page_icon="🧾", layout="wide")
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

st.title("🧾 TaxMate AI — แดชบอร์ดสรุปค่าใช้จ่ายและภาษี")

# ดึงข้อมูลทั้งหมดจาก Supabase
@st.cache_data(ttl=60)
def load_data():
    res = supabase.table("receipts").select("*").order("transaction_date", desc=True).execute()
    return pd.DataFrame(res.data)

df = load_data()

if df.empty:
    st.info("📭 ยังไม่พบข้อมูลบิลในระบบ ลองส่งสลิปผ่าน Discord Bot ก่อนนะครับ")
    st.stop()

# แปลงชนิดข้อมูล
df["total_amount"] = df["total_amount"].astype(float)
df["transaction_date"] = pd.to_datetime(df["transaction_date"]).dt.strftime("%Y-%m-%d")

# แถบตัวกรองด้านซ้าย (Sidebar)
st.sidebar.header("🔍 ตัวกรองข้อมูล")
categories = ["ทั้งหมด"] + sorted(df["category"].dropna().unique().tolist())
selected_cat = st.sidebar.selectbox("เลือกหมวดหมู่", categories)
tax_only = st.sidebar.checkbox("แสดงเฉพาะรายการลดหย่อนภาษีได้")

filtered_df = df.copy()
if selected_cat != "ทั้งหมด":
    filtered_df = filtered_df[filtered_df["category"] == selected_cat]
if tax_only:
    filtered_df = filtered_df[filtered_df["is_tax_deductible"] == True]

# 1. การ์ดสรุปตัวเลขหลัก (KPI Metrics)
total_spent = filtered_df["total_amount"].sum()
total_tax = filtered_df[filtered_df["is_tax_deductible"] == True]["total_amount"].sum()
total_count = len(filtered_df)

col1, col2, col3 = st.columns(3)
col1.metric("💸 ยอดใช้จ่ายรวม", f"{total_spent:,.2f} บาท")
col2.metric("🧾 ยอดลดหย่อนภาษีได้", f"{total_tax:,.2f} บาท")
col3.metric("📄 จำนวนเอกสาร", f"{total_count} รายการ")

st.divider()

# 2. กราฟแสดงผล
chart_col1, chart_col2 = st.columns(2)

with chart_col1:
    st.subheader("📁 สัดส่วนค่าใช้จ่ายตามหมวดหมู่")
    cat_sum = filtered_df.groupby("category", as_index=False)["total_amount"].sum()
    fig_pie = px.pie(cat_sum, values="total_amount", names="category", hole=0.4)
    st.plotly_chart(fig_pie, use_container_width=True)

with chart_col2:
    st.subheader("📅 ยอดใช้จ่ายรายวัน")
    daily_sum = filtered_df.groupby("transaction_date", as_index=False)["total_amount"].sum()
    fig_bar = px.bar(daily_sum, x="transaction_date", y="total_amount", labels={"transaction_date": "วันที่", "total_amount": "ยอดเงิน (บาท)"})
    st.plotly_chart(fig_bar, use_container_width=True)

st.divider()

# 3. ตารางรายการและลิงก์เปิดดูรูปสลิปต้นฉบับ
st.subheader("📋 ประวัติเอกสารทั้งหมด")
display_df = filtered_df[[
    "transaction_date", "doc_type", "merchant_name", "category", "total_amount", "is_tax_deductible", "image_url"
]]

st.dataframe(
    display_df,
    column_config={
        "transaction_date": "วันที่",
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