import os
import io
import json
import csv
import hashlib
import uuid
import asyncio
import zipfile
import aiohttp
from datetime import datetime, time, timezone, timedelta
import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv
from PIL import Image
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from supabase import create_client, Client

# 1. โหลดค่า API Keys จากไฟล์ .env
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# 2. เชื่อมต่อ Gemini และ Supabase
client = genai.Client(api_key=GEMINI_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

class ReceiptData(BaseModel):
    doc_type: str = Field(description="ประเภทเอกสาร เช่น สลิปโอนเงิน, ใบเสร็จรับเงิน, ใบกำกับภาษี, e-Donation")
    merchant_name: str = Field(description="ชื่อร้านค้า หรือ ผู้รับโอน")
    date: str = Field(description="วันที่ทำรายการ รูปแบบ YYYY-MM-DD")
    total_amount: float = Field(description="ยอดเงินสุทธิ")
    category: str = Field(description="หมวดหมู่ค่าใช้จ่าย เช่น อาหารและเครื่องดื่ม, เดินทาง, ช้อปปิ้ง, สาธารณูปโภค, บริจาค")
    is_tax_deductible: bool = Field(description="มีสิทธิ์ลดหย่อนภาษีหรือไม่ (True/False)")
    confidence_score: int = Field(description="คะแนนความมั่นใจในการอ่านตัวเลข 0-100")
    math_verified: bool = Field(description="ตรวจสอบยอดรวมคณิตศาสตร์บนบิลว่าถูกต้องหรือไม่ (ถ้าไม่มีรายการย่อยให้เป็น True)")

# ฟังก์ชันบีบอัดรูปภาพ
def compress_image(image_bytes: bytes) -> bytes:
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    img.thumbnail((1280, 1280))
    output = io.BytesIO()
    img.save(output, format="JPEG", quality=80)
    return output.getvalue()

# ฟังก์ชันสร้างกล่อง Embed แสดงผล
def build_receipt_embed(data: dict, matched_merchant: str = None) -> discord.Embed:
    color = discord.Color.green() if data["confidence_score"] >= 80 else discord.Color.orange()
    if not data.get("math_verified", True):
        color = discord.Color.red()

    embed = discord.Embed(title=f"📄 ผลการอ่าน: {data['doc_type']}", color=color)
    embed.add_field(name="🏪 ร้านค้า / ผู้รับ", value=data["merchant_name"], inline=True)
    embed.add_field(name="💰 ยอดเงิน", value=f"{data['total_amount']:,.2f} บาท", inline=True)
    embed.add_field(name="📅 วันที่", value=data["date"], inline=True)
    embed.add_field(name="📁 หมวดหมู่", value=data["category"], inline=True)
    embed.add_field(name="🧾 ลดหย่อนภาษี", value="✅ ได้" if data["is_tax_deductible"] else "❌ ไม่ได้", inline=True)
    embed.add_field(name="🧮 ตรวจยอดคณิต", value="✅ ถูกต้อง" if data.get("math_verified", True) else "⚠️ ยอดรวมไม่ตรง!", inline=True)
    embed.add_field(name="🎯 ความมั่นใจ AI", value=f"{data['confidence_score']}%", inline=True)

    if matched_merchant:
        embed.add_field(
            name="🔗 พบสลิปเดิมที่ตรงกัน!",
            value=f"พบรายการ **{matched_merchant}** วันที่และยอดเงินตรงกัน\nกดปุ่มด้านล่างเพื่อ **อัปเกรดสิทธิ์ลดหย่อนภาษี** (ไม่นับยอดเบิ้ล)",
            inline=False
        )

    if not data.get("math_verified", True):
        embed.set_footer(text="⚠️ ตรวจพบยอดรวมคณิตศาสตร์บนบิลไม่ตรงกัน กรุณากดแก้ไขหรือตรวจสอบก่อนบันทึก")
    elif data["confidence_score"] < 80:
        embed.set_footer(text="⚠️ บิลนี้ค่อนข้างจางหรืออ่านยาก กรุณาตรวจสอบหรือกดแก้ไขก่อนบันทึก")
    else:
        embed.set_footer(text="ตรวจสอบความถูกต้อง กดแก้ไขหรือยืนยันเพื่อบันทึกลงระบบ")
    return embed

# 3. หน้าต่าง Pop-up แก้ไขข้อมูล (Modal)
class EditReceiptModal(discord.ui.Modal, title="✏️ แก้ไขข้อมูลเอกสาร"):
    def __init__(self, view: "ConfirmView"):
        super().__init__()
        self.parent_view = view
        data = view.receipt_data

        self.merchant_input = discord.ui.TextInput(
            label="ชื่อร้านค้า / ผู้รับ",
            default=str(data["merchant_name"]),
            max_length=100
        )
        self.amount_input = discord.ui.TextInput(
            label="ยอดเงิน (ตัวเลขเท่านั้น)",
            default=str(data["total_amount"]),
            max_length=20
        )
        self.date_input = discord.ui.TextInput(
            label="วันที่ (รูปแบบ YYYY-MM-DD)",
            default=str(data["date"]),
            max_length=10
        )
        self.category_input = discord.ui.TextInput(
            label="หมวดหมู่",
            default=str(data["category"]),
            max_length=50
        )
        self.tax_input = discord.ui.TextInput(
            label="ลดหย่อนภาษีได้ไหม? (พิมพ์ ได้ หรือ ไม่ได้)",
            default="ได้" if data["is_tax_deductible"] else "ไม่ได้",
            max_length=10
        )

        self.add_item(self.merchant_input)
        self.add_item(self.amount_input)
        self.add_item(self.date_input)
        self.add_item(self.category_input)
        self.add_item(self.tax_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            new_amount = float(self.amount_input.value.replace(",", "").strip())
            is_deductible = self.tax_input.value.strip() in ["ได้", "true", "True", "yes", "1", "✅"]

            self.parent_view.receipt_data.update({
                "merchant_name": self.merchant_input.value.strip(),
                "total_amount": new_amount,
                "date": self.date_input.value.strip(),
                "category": self.category_input.value.strip(),
                "is_tax_deductible": is_deductible,
                "confidence_score": 100,
                "math_verified": True
            })

            updated_embed = build_receipt_embed(self.parent_view.receipt_data)
            updated_embed.set_footer(text="✏️ อัปเดตข้อมูลแล้ว กดปุ่ม [ยืนยันข้อมูล] เพื่อบันทึก")
            await interaction.response.edit_message(embed=updated_embed, view=self.parent_view)
        except ValueError:
            await interaction.response.send_message("❌ ยอดเงินต้องเป็นตัวเลขเท่านั้น เช่น `250.00`", ephemeral=True)

# 4. ปุ่มควบคุมใต้กล่อง Embed
class ConfirmView(discord.ui.View):
    def __init__(self, author_id: int, receipt_data: dict, file_hash: str, file_bytes: bytes, is_pdf: bool, matched_receipt_id: str = None):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.receipt_data = receipt_data
        self.file_hash = file_hash
        self.file_bytes = file_bytes
        self.is_pdf = is_pdf
        self.matched_receipt_id = matched_receipt_id

        # ถ้าเจอสลิปเดิมให้เปลี่ยนข้อความปุ่มยืนยัน
        if self.matched_receipt_id:
            self.confirm_button.label = "จับคู่อัปเกรดสิทธิ์ภาษี"
            self.confirm_button.emoji = "🔗"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("⚠️ เฉพาะผู้ที่ส่งเอกสารนี้เท่านั้นที่สามารถกดปุ่มได้", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="ยืนยันข้อมูล", style=discord.ButtonStyle.green, emoji="✅")
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        try:
            ext = "pdf" if self.is_pdf else "jpg"
            mime = "application/pdf" if self.is_pdf else "image/jpeg"
            folder = "pdf" if self.is_pdf else "image"
            file_path = f"{folder}/{self.author_id}_{uuid.uuid4()}.{ext}"

            supabase.storage.from_("receipts").upload(
                path=file_path,
                file=self.file_bytes,
                file_options={"content-type": mime}
            )
            file_url = supabase.storage.from_("receipts").get_public_url(file_path)

            if self.matched_receipt_id:
                # อัปเกรดบิลเดิมแทนการเพิ่มแถวใหม่ (ไม่นับยอดเบิ้ล)
                supabase.table("receipts").update({
                    "is_tax_deductible": True,
                    "doc_type": self.receipt_data["doc_type"],
                    "matched_tax_url": file_url,
                    "math_verified": self.receipt_data.get("math_verified", True)
                }).eq("id", self.matched_receipt_id).execute()
                footer_text = "🔗 จับคู่และอัปเกรดสิทธิ์ลดหย่อนภาษีเรียบร้อยแล้ว! (ไม่นับยอดเบิ้ล)"
            else:
                record = {
                    "discord_user_id": str(self.author_id),
                    "discord_channel_id": str(interaction.channel_id),
                    "image_hash": self.file_hash,
                    "image_url": file_url,
                    "doc_type": self.receipt_data["doc_type"],
                    "merchant_name": self.receipt_data["merchant_name"],
                    "transaction_date": self.receipt_data["date"],
                    "total_amount": self.receipt_data["total_amount"],
                    "category": self.receipt_data["category"],
                    "is_tax_deductible": self.receipt_data["is_tax_deductible"],
                    "confidence_score": self.receipt_data["confidence_score"],
                    "math_verified": self.receipt_data.get("math_verified", True),
                }
                supabase.table("receipts").insert(record).execute()
                footer_text = "✅ บันทึกลงฐานข้อมูลเรียบร้อยแล้ว!"

            for item in self.children:
                item.disabled = True
            embed = interaction.message.embeds[0]
            embed.color = discord.Color.blue()
            embed.set_footer(text=footer_text)
            await interaction.edit_original_response(embed=embed, view=self)
        except Exception as e:
            await interaction.followup.send(f"❌ บันทึกข้อมูลไม่สำเร็จ: {e}", ephemeral=True)

    @discord.ui.button(label="แก้ไขข้อมูล", style=discord.ButtonStyle.blurple, emoji="✏️")
    async def edit_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(EditReceiptModal(self))

    @discord.ui.button(label="ยกเลิก", style=discord.ButtonStyle.red, emoji="❌")
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        embed = interaction.message.embeds[0]
        embed.color = discord.Color.light_gray()
        embed.set_footer(text="❌ ยกเลิกรายการนี้แล้ว (ไม่ถูกบันทึก)")
        await interaction.response.edit_message(embed=embed, view=self)

# 5. ตั้งค่า Discord Bot และ Slash Commands
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    await bot.tree.sync()
    if not check_recurring_bills.is_running():
        check_recurring_bills.start()
    if not send_monthly_summary.is_running():
        send_monthly_summary.start()
    print(f"✅ บอท {bot.user} ออนไลน์แล้ว!")

@bot.tree.command(name="summary", description="สรุปยอดค่าใช้จ่ายและรายการลดหย่อนภาษีของคุณ")
async def summary_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    user_id = str(interaction.user.id)

    res = (
        supabase.table("receipts")
        .select("*")
        .eq("discord_user_id", user_id)
        .order("transaction_date", desc=True)
        .execute()
    )
    records = res.data

    if not records:
        await interaction.followup.send("📭 ยังไม่พบข้อมูลบิลของคุณในระบบ ลองส่งรูปสลิปหรือไฟล์ PDF เข้าห้องแชทได้เลยครับ!", ephemeral=True)
        return

    total_spent = sum(float(r["total_amount"]) for r in records)
    total_tax = sum(float(r["total_amount"]) for r in records if r["is_tax_deductible"])

    by_category = {}
    for r in records:
        cat = r["category"] or "อื่นๆ"
        by_category[cat] = by_category.get(cat, 0.0) + float(r["total_amount"])

    cat_lines = "\n".join([f"• **{k}**: {v:,.2f} บาท" for k, v in sorted(by_category.items(), key=lambda x: x[1], reverse=True)])
    recent_lines = "\n".join([
        f"• `{r['transaction_date']}` **{r['merchant_name']}** — {float(r['total_amount']):,.2f} ฿ {'🧾' if r['is_tax_deductible'] else ''}"
        for r in records[:5]
    ])

    embed = discord.Embed(title=f"📊 สรุปค่าใช้จ่ายของ {interaction.user.display_name}", color=discord.Color.gold())
    embed.add_field(name="💸 ยอดใช้จ่ายรวมทั้งหมด", value=f"**{total_spent:,.2f} บาท** ({len(records)} รายการ)", inline=True)
    embed.add_field(name="🧾 ยอดลดหย่อนภาษีได้", value=f"**{total_tax:,.2f} บาท**", inline=True)
    embed.add_field(name="📁 แยกตามหมวดหมู่", value=cat_lines, inline=False)
    embed.add_field(name="🕒 รายการล่าสุด (5 รายการ)", value=recent_lines, inline=False)
    embed.set_footer(text="🧾 = รายการที่สามารถใช้ลดหย่อนภาษีได้")

    await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="export", description="ส่งออกรายการค่าใช้จ่ายทั้งหมดเป็นไฟล์ CSV (เปิดใน Excel ได้)")
async def export_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    user_id = str(interaction.user.id)

    res = (
        supabase.table("receipts")
        .select("*")
        .eq("discord_user_id", user_id)
        .order("transaction_date", desc=True)
        .execute()
    )
    records = res.data

    if not records:
        await interaction.followup.send("📭 ยังไม่พบข้อมูลบิลของคุณในระบบครับ", ephemeral=True)
        return

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "วันที่ทำรายการ",
        "ประเภทเอกสาร",
        "ร้านค้า / ผู้รับ",
        "หมวดหมู่",
        "ยอดเงิน (บาท)",
        "ลดหย่อนภาษีได้",
        "ลิงก์เอกสารต้นฉบับ"
    ])

    for r in records:
        writer.writerow([
            r.get("transaction_date", ""),
            r.get("doc_type", ""),
            r.get("merchant_name", ""),
            r.get("category", ""),
            f"{float(r.get('total_amount', 0)):.2f}",
            "ได้" if r.get("is_tax_deductible") else "ไม่ได้",
            r.get("matched_tax_url") or r.get("image_url", "")
        ])

    csv_bytes = io.BytesIO(output.getvalue().encode("utf-8-sig"))
    file = discord.File(fp=csv_bytes, filename=f"TaxMate_Export_{user_id}.csv")

    await interaction.followup.send(
        content=f"📊 ส่งออกข้อมูลสำเร็จทั้งหมด **{len(records)} รายการ** (สามารถเปิดใน Excel หรือ Google Sheets ได้ทันทีครับ)",
        file=file,
        ephemeral=True
    )

@bot.tree.command(name="dashboard", description="เปิดหน้าเว็บ Dashboard สรุปค่าใช้จ่ายและภาษี")
async def dashboard_command(interaction: discord.Interaction):
    dashboard_url = "https://taxmate-ai-u9yickkxqksgrh7hzuduqt.streamlit.app/"
    embed = discord.Embed(
        title="📊 TaxMate AI Web Dashboard",
        description="คลิกปุ่มด้านล่างเพื่อดูกราฟสรุปค่าใช้จ่าย สัดส่วนหมวดหมู่ ยอดลดหย่อนภาษี และเปิดดูรูปสลิปย้อนหลังทั้งหมดครับ",
        color=discord.Color.blue()
    )

    view = discord.ui.View()
    view.add_item(discord.ui.Button(
        label="เปิดหน้าเว็บ Dashboard",
        style=discord.ButtonStyle.link,
        url=dashboard_url,
        emoji="🌐"
    ))

    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

# --- 1. คำสั่งตั้งค่าแจ้งเตือนบิลประจำ (/remind) ---
@bot.tree.command(name="remind", description="ตั้งค่าแจ้งเตือนค่าใช้จ่ายประจำทุกเดือน (เช่น ค่าหอ ค่าเน็ต)")
@app_commands.describe(
    bill_name="ชื่อรายการ เช่น ค่าหอ, ค่าเน็ต, ค่าบัตรเครดิต",
    amount="ยอดเงิน (บาท)",
    due_day="วันที่ต้องจ่ายของทุกเดือน (1-31)"
)
async def remind_command(interaction: discord.Interaction, bill_name: str, amount: float, due_day: int):
    if not (1 <= due_day <= 31):
        await interaction.response.send_message("❌ กรุณาระบุวันที่ระหว่าง 1 ถึง 31 เท่านั้นครับ", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    record = {
        "discord_user_id": str(interaction.user.id),
        "discord_channel_id": str(interaction.channel_id),
        "bill_name": bill_name.strip(),
        "amount": amount,
        "due_day": due_day,
        "is_active": True
    }
    supabase.table("recurring_bills").insert(record).execute()

    embed = discord.Embed(title="⏰ ตั้งค่าแจ้งเตือนบิลประจำสำเร็จ!", color=discord.Color.teal())
    embed.add_field(name="📌 รายการ", value=bill_name, inline=True)
    embed.add_field(name="💰 ยอดเงิน", value=f"{amount:,.2f} บาท", inline=True)
    embed.add_field(name="📅 แจ้งเตือนทุกวันที่", value=f"วันที่ {due_day} ของทุกเดือน (09:00 น.)", inline=False)
    await interaction.followup.send(embed=embed, ephemeral=True)

# ระบบเบื้องหลัง: เช็กและแจ้งเตือนบิลประจำทุกวัน เวลา 09:00 น. (02:00 UTC)
@tasks.loop(time=time(hour=2, minute=0, tzinfo=timezone.utc))
async def check_recurring_bills():
    th_now = datetime.now(timezone(timedelta(hours=7)))
    today_day = th_now.day

    res = (
        supabase.table("recurring_bills")
        .select("*")
        .eq("due_day", today_day)
        .eq("is_active", True)
        .execute()
    )
    for bill in res.data:
        channel = bot.get_channel(int(bill["discord_channel_id"]))
        if channel:
            embed = discord.Embed(
                title=f"🔔 แจ้งเตือนครบกำหนดชำระ: {bill['bill_name']}",
                description=f"ถึงกำหนดจ่ายประจำวันที่ **{today_day}** แล้วครับ อย่าลืมโอนและส่งสลิปมาบันทึกนะ!",
                color=discord.Color.orange()
            )
            embed.add_field(name="💰 ยอดที่ต้องชำระ", value=f"**{float(bill['amount']):,.2f} บาท**", inline=True)
            await channel.send(content=f"<@{bill['discord_user_id']}>", embed=embed)

# --- 2. คำสั่ง AI วิเคราะห์รายจ่ายและแนะนำลดหย่อนภาษี (/analyze) ---
@bot.tree.command(name="analyze", description="ให้ AI วิเคราะห์จุดที่ควรลดรายจ่ายและแนะนำสิทธิ์ลดหย่อนภาษี")
async def analyze_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    user_id = str(interaction.user.id)

    res = (
        supabase.table("receipts")
        .select("transaction_date, merchant_name, category, total_amount, is_tax_deductible, doc_type")
        .eq("discord_user_id", user_id)
        .order("transaction_date", desc=True)
        .limit(50)
        .execute()
    )
    records = res.data

    if not records:
        await interaction.followup.send("📭 ยังไม่มีข้อมูลบิลให้วิเคราะห์ครับ ลองส่งสลิปเข้ามาก่อนนะ", ephemeral=True)
        return

    summary_text = json.dumps(records, ensure_ascii=False)
    prompt = (
        f"คุณคือผู้ช่วยวางแผนการเงินและภาษีส่วนตัว นี่คือรายการค่าใช้จ่ายล่าสุดของผู้ใช้ (JSON):\n{summary_text}\n\n"
        "กรุณาวิเคราะห์เป็นภาษาไทยแบบกระชับ อ่านง่าย ใช้ Bullet Points ดังนี้:\n"
        "1. **ภาพรวมและจุดที่ควรลดรายจ่าย:** ชี้หมวดหมู่หรือร้านค้าที่ใช้จ่ายสูงผิดปกติ พร้อมคำแนะนำที่ใช้ได้จริง\n"
        "2. **คำแนะนำด้านภาษี:** ระบุรายการที่น่าจะขอใบกำกับภาษีเต็มรูป / e-Tax Invoice เพิ่มได้ในอนาคต เพื่อเพิ่มยอดลดหย่อน\n"
        "3. **คำแนะนำสั้นๆ ปิดท้าย:** 1 ประโยคให้กำลังใจ"
    )

    try:
        response = await asyncio.to_thread(
            client.models.generate_content,
            model="gemini-3.8-flash",
            contents=prompt,
        )
        embed = discord.Embed(
            title="🧠 บทวิเคราะห์การเงินและภาษีโดย TaxMate AI",
            description=response.text[:4000],
            color=discord.Color.purple()
        )
        embed.set_footer(text=f"วิเคราะห์จากข้อมูลล่าสุด {len(records)} รายการ")
        await interaction.followup.send(embed=embed, ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ ไม่สามารถวิเคราะห์ได้ในขณะนี้: {e}", ephemeral=True)

# --- 3. คำสั่งส่งออกไฟล์รวมใบกำกับภาษีเป็น ZIP (/export_zip) ---
@bot.tree.command(name="export_zip", description="ดาวน์โหลดไฟล์รวมเอกสารลดหย่อนภาษีทั้งหมดเป็นไฟล์ ZIP")
async def export_zip_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    user_id = str(interaction.user.id)

    res = (
        supabase.table("receipts")
        .select("transaction_date, merchant_name, total_amount, image_url, matched_tax_url")
        .eq("discord_user_id", user_id)
        .eq("is_tax_deductible", True)
        .order("transaction_date", desc=True)
        .execute()
    )
    records = res.data

    if not records:
        await interaction.followup.send("📭 ยังไม่พบเอกสารที่ลดหย่อนภาษีได้ในระบบครับ", ephemeral=True)
        return

    zip_buffer = io.BytesIO()
    async with aiohttp.ClientSession() as session:
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for idx, r in enumerate(records, start=1):
                url = r.get("matched_tax_url") or r.get("image_url")
                if not url:
                    continue
                try:
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            file_bytes = await resp.read()
                            ext = "pdf" if url.lower().endswith(".pdf") else "jpg"
                            safe_merchant = "".join(c for c in r.get("merchant_name", "doc") if c.isalnum() or c in " _-ก-๛")[:25]
                            filename = f"{idx:02d}_{r.get('transaction_date', 'date')}_{safe_merchant}.{ext}"
                            zf.writestr(filename, file_bytes)
                except Exception:
                    continue

    zip_buffer.seek(0)
    file = discord.File(fp=zip_buffer, filename=f"TaxMate_TaxDocs_{user_id}.zip")
    await interaction.followup.send(
        content=f"🗂️ รวมเอกสารลดหย่อนภาษีสำเร็จทั้งหมด **{len(records)} ไฟล์** พร้อมสำหรับยื่นภาษีครับ!",
        file=file,
        ephemeral=True
    )
# ฟังก์ชันช่วยสร้าง Embed สรุปสเตทเมนต์รายเดือน
def build_monthly_embed(title_month: str, records: list) -> discord.Embed:
    total_spent = sum(float(r["total_amount"]) for r in records)
    total_tax = sum(float(r["total_amount"]) for r in records if r["is_tax_deductible"])

    by_category = {}
    for r in records:
        cat = r.get("category") or "อื่นๆ"
        by_category[cat] = by_category.get(cat, 0.0) + float(r["total_amount"])

    cat_lines = "\n".join([
        f"• **{k}**: {v:,.2f} บาท ({(v / total_spent * 100):.1f}%)"
        for k, v in sorted(by_category.items(), key=lambda x: x[1], reverse=True)
    ])

    embed = discord.Embed(
        title=f"📅 สรุปสเตทเมนต์ประจำเดือน {title_month}",
        description=f"สรุปรายการทั้งหมด **{len(records)} รายการ**",
        color=discord.Color.blue()
    )
    embed.add_field(name="💸 ยอดใช้จ่ายรวม", value=f"**{total_spent:,.2f} บาท**", inline=True)
    embed.add_field(name="🧾 ยอดลดหย่อนภาษีได้", value=f"**{total_tax:,.2f} บาท**", inline=True)
    embed.add_field(name="📁 สัดส่วนตามหมวดหมู่", value=cat_lines or "-", inline=False)
    embed.set_footer(text="TaxMate AI • ระบบสรุปสเตทเมนต์รายเดือนอัตโนมัติ")
    return embed

# ระบบเบื้องหลัง: ส่งสรุปสเตทเมนต์รายเดือนอัตโนมัติ ทุกวันที่ 1 เวลา 09:05 น. (02:05 UTC)
@tasks.loop(time=time(hour=2, minute=5, tzinfo=timezone.utc))
async def send_monthly_summary():
    th_now = datetime.now(timezone(timedelta(hours=7)))
    if th_now.day != 1:
        return  # ทำงานเฉพาะวันที่ 1 ของเดือน

    # คำนวณเดือนที่ผ่านมา (รูปแบบ YYYY-MM)
    last_month_date = th_now.replace(day=1) - timedelta(days=1)
    target_month = last_month_date.strftime("%Y-%m")

    res = (
        supabase.table("receipts")
        .select("*")
        .gte("transaction_date", f"{target_month}-01")
        .lte("transaction_date", f"{target_month}-31")
        .execute()
    )
    if not res.data:
        return

    # จัดกลุ่มตามผู้ใช้
    user_records = {}
    user_channel = {}
    for r in res.data:
        uid = r["discord_user_id"]
        user_records.setdefault(uid, []).append(r)
        if r.get("discord_channel_id"):
            user_channel[uid] = int(r["discord_channel_id"])

    for uid, records in user_records.items():
        channel_id = user_channel.get(uid)
        if not channel_id:
            continue
        channel = bot.get_channel(channel_id)
        if channel:
            embed = build_monthly_embed(target_month, records)
            await channel.send(content=f"📊 <@{uid}> สรุปสเตทเมนต์ประจำเดือน **{target_month}** มาแล้วครับ!", embed=embed)

# คำสั่งเรียกดูสรุปสเตทเมนต์รายเดือนได้ทันที (/monthly_report)
@bot.tree.command(name="monthly_report", description="ดูสรุปสเตทเมนต์รายเดือน (เว้นว่างไว้เพื่อดูเดือนปัจจุบัน)")
@app_commands.describe(month="ระบุเดือนรูปแบบ YYYY-MM เช่น 2026-11 (ไม่ใส่ = เดือนปัจจุบัน)")
async def monthly_report_command(interaction: discord.Interaction, month: str = None):
    await interaction.response.defer(ephemeral=True)
    th_now = datetime.now(timezone(timedelta(hours=7)))
    target_month = month.strip() if month else th_now.strftime("%Y-%m")

    res = (
        supabase.table("receipts")
        .select("*")
        .eq("discord_user_id", str(interaction.user.id))
        .gte("transaction_date", f"{target_month}-01")
        .lte("transaction_date", f"{target_month}-31")
        .execute()
    )
    if not res.data:
        await interaction.followup.send(f"📭 ไม่พบข้อมูลบิลในเดือน `{target_month}` ครับ", ephemeral=True)
        return

    embed = build_monthly_embed(target_month, res.data)
    await interaction.followup.send(embed=embed, ephemeral=True)
@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    if message.attachments:
        for attachment in message.attachments:
            ctype = attachment.content_type or ""
            is_image = ctype.startswith("image/")
            is_pdf = ctype == "application/pdf" or attachment.filename.lower().endswith(".pdf")

            if is_image or is_pdf:
                processing_msg = await message.reply("⏳ กำลังตรวจสอบและอ่านข้อมูลจากเอกสาร...")
                try:
                    raw_bytes = await attachment.read()
                    file_hash = hashlib.sha256(raw_bytes).hexdigest()

                    existing = supabase.table("receipts").select("id").eq("image_hash", file_hash).execute()
                    if existing.data:
                        await processing_msg.edit(content="⚠️ **ตรวจพบเอกสารซ้ำ!** เอกสารฉบับนี้เคยถูกบันทึกเข้าระบบไปแล้วครับ")
                        continue

                    prompt = (
                        "วิเคราะห์เอกสารทางการเงินนี้ สกัดข้อมูลให้ถูกต้องตามโครงสร้าง "
                        "หากเป็นใบกำกับภาษีเต็มรูป e-Tax Invoice หรือ e-Donation ให้ตั้งค่า is_tax_deductible เป็น True "
                        "ตรวจสอบผลรวมคณิตศาสตร์ของรายการสินค้า/VAT/ส่วนลดกับยอดสุทธิ หากไม่ตรงให้ตั้ง math_verified เป็น False "
                        "หากตัวเลขจางหรืออ่านยากให้ลดคะแนน confidence_score ตามความเป็นจริง"
                    )

                    if is_pdf:
                        upload_bytes = raw_bytes
                        ai_input = types.Part.from_bytes(data=raw_bytes, mime_type="application/pdf")
                    else:
                        upload_bytes = compress_image(raw_bytes)
                        ai_input = Image.open(io.BytesIO(upload_bytes))

                    response = None
                    wait_times = [3, 8]

                    for attempt in range(len(wait_times) + 1):
                        try:
                            response = await asyncio.to_thread(
                                client.models.generate_content,
                                model="gemini-3.8-flash",
                                contents=[ai_input, prompt],
                                config=types.GenerateContentConfig(
                                    response_mime_type="application/json",
                                    response_schema=ReceiptData,
                                    temperature=0.1,
                                ),
                            )
                            break
                        except Exception as api_err:
                            if "503" in str(api_err) and attempt < len(wait_times):
                                delay = wait_times[attempt]
                                await processing_msg.edit(
                                    content=f"⏳ เซิร์ฟเวอร์ AI กำลังหนาแน่น กำลังรอคิวและลองใหม่อัตโนมัติใน {delay} วินาที... (ครั้งที่ {attempt + 1}/{len(wait_times)})"
                                )
                                await asyncio.sleep(delay)
                            else:
                                raise api_err

                    data = json.loads(response.text)

                    # ตรวจสอบว่ามีสลิปเดิมที่วันและยอดเงินตรงกันหรือไม่ (กรณีส่งใบกำกับภาษีตามมาทีหลัง)
                    matched_receipt_id = None
                    matched_merchant = None
                    if data.get("is_tax_deductible"):
                        match_res = (
                            supabase.table("receipts")
                            .select("id, merchant_name")
                            .eq("discord_user_id", str(message.author.id))
                            .eq("transaction_date", data["date"])
                            .eq("total_amount", data["total_amount"])
                            .eq("is_tax_deductible", False)
                            .limit(1)
                            .execute()
                        )
                        if match_res.data:
                            matched_receipt_id = match_res.data[0]["id"]
                            matched_merchant = match_res.data[0]["merchant_name"]

                    embed = build_receipt_embed(data, matched_merchant=matched_merchant)
                    view = ConfirmView(
                        author_id=message.author.id,
                        receipt_data=data,
                        file_hash=file_hash,
                        file_bytes=upload_bytes,
                        is_pdf=is_pdf,
                        matched_receipt_id=matched_receipt_id
                    )
                    await processing_msg.edit(content=None, embed=embed, view=view)

                except Exception as e:
                    await processing_msg.edit(content=f"❌ เกิดข้อผิดพลาด: {e}")

    await bot.process_commands(message)

bot.run(DISCORD_TOKEN)