import os
import io
import json
import csv
import hashlib
import uuid
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
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
def build_receipt_embed(data: dict) -> discord.Embed:
    color = discord.Color.green() if data["confidence_score"] >= 80 else discord.Color.orange()
    embed = discord.Embed(title=f"📄 ผลการอ่าน: {data['doc_type']}", color=color)
    embed.add_field(name="🏪 ร้านค้า / ผู้รับ", value=data["merchant_name"], inline=True)
    embed.add_field(name="💰 ยอดเงิน", value=f"{data['total_amount']:,.2f} บาท", inline=True)
    embed.add_field(name="📅 วันที่", value=data["date"], inline=True)
    embed.add_field(name="📁 หมวดหมู่", value=data["category"], inline=True)
    embed.add_field(name="🧾 ลดหย่อนภาษี", value="✅ ได้" if data["is_tax_deductible"] else "❌ ไม่ได้", inline=True)
    embed.add_field(name="🎯 ความมั่นใจ AI", value=f"{data['confidence_score']}%", inline=True)

    if data["confidence_score"] < 80:
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
                "confidence_score": 100  # ผู้ใช้ตรวจสอบเองแล้ว
            })

            updated_embed = build_receipt_embed(self.parent_view.receipt_data)
            updated_embed.set_footer(text="✏️ อัปเดตข้อมูลแล้ว กดปุ่ม [ยืนยันข้อมูล] เพื่อบันทึก")
            await interaction.response.edit_message(embed=updated_embed, view=self.parent_view)
        except ValueError:
            await interaction.response.send_message("❌ ยอดเงินต้องเป็นตัวเลขเท่านั้น เช่น `250.00`", ephemeral=True)

# 4. ปุ่มควบคุมใต้กล่อง Embed
class ConfirmView(discord.ui.View):
    def __init__(self, author_id: int, receipt_data: dict, file_hash: str, file_bytes: bytes, is_pdf: bool):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.receipt_data = receipt_data
        self.file_hash = file_hash
        self.file_bytes = file_bytes
        self.is_pdf = is_pdf

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

            record = {
                "discord_user_id": str(self.author_id),
                "image_hash": self.file_hash,
                "image_url": file_url,
                "doc_type": self.receipt_data["doc_type"],
                "merchant_name": self.receipt_data["merchant_name"],
                "transaction_date": self.receipt_data["date"],
                "total_amount": self.receipt_data["total_amount"],
                "category": self.receipt_data["category"],
                "is_tax_deductible": self.receipt_data["is_tax_deductible"],
                "confidence_score": self.receipt_data["confidence_score"],
            }
            supabase.table("receipts").insert(record).execute()

            for item in self.children:
                item.disabled = True
            embed = interaction.message.embeds[0]
            embed.color = discord.Color.blue()
            embed.set_footer(text="✅ บันทึกลงฐานข้อมูลเรียบร้อยแล้ว!")
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
    await bot.tree.sync()  # ซิงค์คำสั่ง /summary เข้า Discord
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

    # รวมยอดแยกตามหมวดหมู่
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

    # สร้างไฟล์ CSV ในหน่วยความจำ
    output = io.StringIO()
    writer = csv.writer(output)

    # เขียนหัวตาราง (Header)
    writer.writerow([
        "วันที่ทำรายการ",
        "ประเภทเอกสาร",
        "ร้านค้า / ผู้รับ",
        "หมวดหมู่",
        "ยอดเงิน (บาท)",
        "ลดหย่อนภาษีได้",
        "ลิงก์เอกสารต้นฉบับ"
    ])

    # เขียนข้อมูลแต่ละแถว
    for r in records:
        writer.writerow([
            r.get("transaction_date", ""),
            r.get("doc_type", ""),
            r.get("merchant_name", ""),
            r.get("category", ""),
            f"{float(r.get('total_amount', 0)):.2f}",
            "ได้" if r.get("is_tax_deductible") else "ไม่ได้",
            r.get("image_url", "")
        ])

    # แปลงเป็น Bytes แบบ utf-8-sig เพื่อให้ Excel อ่านภาษาไทยได้สมบูรณ์
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
                        "หากตัวเลขจางหรืออ่านยากให้ลดคะแนน confidence_score ตามความเป็นจริง"
                    )

                    if is_pdf:
                        upload_bytes = raw_bytes
                        ai_input = types.Part.from_bytes(data=raw_bytes, mime_type="application/pdf")
                    else:
                        upload_bytes = compress_image(raw_bytes)
                        ai_input = Image.open(io.BytesIO(upload_bytes))
                    response = None
                    wait_times = [3, 8]  # หน่วงเวลารอเพิ่มขึ้นตามลำดับ

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
                    embed = build_receipt_embed(data)
                    view = ConfirmView(
                        author_id=message.author.id,
                        receipt_data=data,
                        file_hash=file_hash,
                        file_bytes=upload_bytes,
                        is_pdf=is_pdf
                    )
                    await processing_msg.edit(content=None, embed=embed, view=view)

                except Exception as e:
                    await processing_msg.edit(content=f"❌ เกิดข้อผิดพลาด: {e}")

    await bot.process_commands(message)

bot.run(DISCORD_TOKEN)