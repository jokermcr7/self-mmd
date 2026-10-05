import asyncio
import base64
import logging
import os
import random
from datetime import datetime, timezone
from html import escape
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet, InvalidToken
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, RPCError, PhoneCodeInvalidError, PhoneCodeExpiredError, FloodWaitError
from telethon.tl.functions.account import UpdateProfileRequest, UpdateUsernameRequest

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import ReactionTypeEmoji
from sqlalchemy import Boolean, DateTime, Integer, String, Text, select, func, inspect, text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("self-panel")

TOKEN = os.environ["BOT_TOKEN"]
OWNER_ID = int(os.environ["OWNER_ID"])
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DEFAULT_TZ = os.getenv("DEFAULT_TIMEZONE", "Europe/Berlin")
TG_API_ID = int(os.getenv("TG_API_ID", "0"))
TG_API_HASH = os.getenv("TG_API_HASH", "").strip()
SESSION_SECRET = os.getenv("SESSION_SECRET", "").strip()
if SESSION_SECRET:
    try:
        FERNET = Fernet(SESSION_SECRET.encode())
    except Exception as e:
        raise RuntimeError("SESSION_SECRET باید یک کلید Fernet معتبر باشد") from e
else:
    FERNET = None

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://") and "+asyncpg" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
if not DATABASE_URL or "://" not in DATABASE_URL:
    DATABASE_URL = "sqlite+aiosqlite:///./bot.db"

class Base(DeclarativeBase): pass

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(255))
    first_name: Mapped[str | None] = mapped_column(String(255))
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    banned: Mapped[bool] = mapped_column(Boolean, default=False)
    timezone: Mapped[str] = mapped_column(String(64), default=DEFAULT_TZ)
    auto_reaction: Mapped[str | None] = mapped_column(String(32))
    auto_reply: Mapped[str | None] = mapped_column(Text)
    notifications: Mapped[bool] = mapped_column(Boolean, default=True)
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    self_session: Mapped[str | None] = mapped_column(Text)
    phone_masked: Mapped[str | None] = mapped_column(String(64))
    self_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    clock_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    clock_target: Mapped[str] = mapped_column(String(16), default="bio")
    clock_font: Mapped[str] = mapped_column(String(32), default="classic")
    clock_prefix: Mapped[str] = mapped_column(String(40), default="🕐 ")
    smart_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    name_lock: Mapped[bool] = mapped_column(Boolean, default=False)
    locked_name: Mapped[str | None] = mapped_column(String(255))
    word_filter: Mapped[bool] = mapped_column(Boolean, default=False)
    word_filter_text: Mapped[str | None] = mapped_column(Text)
    media_lock: Mapped[bool] = mapped_column(Boolean, default=False)
    comments_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    button_theme: Mapped[str] = mapped_column(String(32), default="orange")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
dp = Dispatcher()
LOGIN_FLOWS: dict[int, dict] = {}
LOGIN_CANCEL_TEXT = "❌ لغو ورود"
CLOCK_TASKS: dict[int, asyncio.Task] = {}

# فونت‌های ساعت؛ همگی فقط تبدیل ظاهری اعداد هستند و به متن اصلی دست نمی‌زنند.
CLOCK_FONTS = {
    "classic": ("کلاسیک", "0123456789", "0123456789"),
    "bold": ("ضخیم", "0123456789", "𝟎𝟏𝟐𝟑𝟒𝟓𝟔𝟕𝟖𝟗"),
    "double": ("دوبل", "0123456789", "𝟘𝟙𝟚𝟛𝟜𝟝𝟞𝟟𝟠𝟡"),
    "sans": ("سن‌سریف", "0123456789", "𝟢𝟣𝟤𝟥𝟦𝟧𝟨𝟩𝟪𝟫"),
    "mono": ("تک‌عرض", "0123456789", "𝟶𝟷𝟸𝟹𝟺𝟻𝟼𝟽𝟾𝟿"),
    "full": ("فول‌ویدث", "0123456789", "０１２３４５６７８９"),
    "bubble": ("حبابی", "0123456789", "⓪①②③④⑤⑥⑦⑧⑨"),
    "superscript": ("بالانویس", "0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹"),
    "subscript": ("زیرنویس", "0123456789", "₀₁₂₃₄₅₆₇₈₉"),
}

def stylize_time(value: str, font: str) -> str:
    mapping = CLOCK_FONTS.get(font, CLOCK_FONTS["classic"])[2]
    return value.translate(str.maketrans("0123456789", mapping))

def main_kb():
    # منوی اصلی؛ ارزها و دکمه ورود سلف عمداً حذف شده‌اند. ورود بعد از تأیید مدیر خودکار شروع می‌شود.
    rows = [
        [("⏰ زمان و پروفایل", "time"), ("🖼 پروفایل", "profile"), ("✍️ استایل متن", "text_style")],
        [("✨ انیمیشن", "animation"), ("👤 کاربران", "users_menu"), ("🔒 قفل رسانه", "media")],
        [("💬 کامنت", "comments"), ("📌 عمومی", "public"), ("🎭 اکشن", "actions")],
        [("🎮 بازی‌ها", "games"), ("🌐 ترجمه", "translate"), ("🔎 گوگل", "google")],
        [("ℹ️ اطلاعات", "info"), ("✉️ مدیریت پیام", "messages"), ("👍 ری‌اکشن", "reaction")],
        [("🚫 مسدودی‌ها", "enemies"), ("✏️ تغییر پروفایل", "edit"), ("🔎 فیلتر کلمات", "filter")],
        [("🛡 حفاظت اسم", "namelock"), ("🤖 هوشمند", "smart"), ("📣 گزارش", "report")],
        [("🛠 ابزارها", "tools"), ("🧠 منشی", "secretary"), ("📢 اطلاعیه", "broadcast")],
        [("🔮 فال", "fortune"), ("🔐 متن رمزی", "secret"), ("🧩 ابزارک‌ها", "widgets")],
        [("📦 بکاپ", "backup"), ("🎨 دکمه‌ها", "buttons"), ("❓ راهنمای کامل", "help_all")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=c) for t,c in r] for r in rows] + [[InlineKeyboardButton(text="✖️ بستن", callback_data="close")]])

def login_stage_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="📱 ارسال شماره من", request_contact=True)], [KeyboardButton(text="❌ لغو ورود")]], resize_keyboard=True, one_time_keyboard=True)

def code_stage_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ لغو ورود")]], resize_keyboard=True, one_time_keyboard=True)

def back(): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ منوی اصلی", callback_data="main")]])
def clock_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟢 ساعت روشن", callback_data="clock:on"), InlineKeyboardButton(text="🔴 ساعت خاموش", callback_data="clock:off")],
        [InlineKeyboardButton(text="📝 فقط بیو", callback_data="clocktarget:bio"), InlineKeyboardButton(text="👤 فقط نام", callback_data="clocktarget:name")],
        [InlineKeyboardButton(text="📝👤 بیو + نام", callback_data="clocktarget:both")],
        [InlineKeyboardButton(text="🔤 انتخاب فونت", callback_data="clockfonts")],
        [InlineKeyboardButton(text="🌍 تهران", callback_data="tz:Asia/Tehran"), InlineKeyboardButton(text="🇩🇪 برلین", callback_data="tz:Europe/Berlin"), InlineKeyboardButton(text="🇹🇷 استانبول", callback_data="tz:Europe/Istanbul")],
        [InlineKeyboardButton(text="⬅️ برگشت", callback_data="main")],
    ])
def fonts_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"{v[0]}  12:34", callback_data=f"font:{k}")] for k,v in CLOCK_FONTS.items()] + [[InlineKeyboardButton(text="⬅️ زمان", callback_data="time")]])

def feature_page(title, body, buttons=None):
    kb = buttons or []
    kb.append([InlineKeyboardButton(text="⬅️ منوی اصلی", callback_data="main")])
    return InlineKeyboardMarkup(inline_keyboard=kb), f"<b>{title}</b>\n\n{body}"

async def get_user(s, uid): return (await s.execute(select(User).where(User.id == uid))).scalar_one_or_none()
async def allowed(uid):
    if uid == OWNER_ID: return True
    async with Session() as s:
        u = await get_user(s, uid)
        return bool(u and u.approved and not u.banned)

async def ensure_schema():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        def cols(c): return {x["name"] for x in inspect(c).get_columns("users")}
        existing = await conn.run_sync(cols)
        additions = {
            "clock_enabled":"BOOLEAN DEFAULT FALSE", "clock_target":"VARCHAR(16) DEFAULT 'bio'", "clock_font":"VARCHAR(32) DEFAULT 'classic'", "clock_prefix":"VARCHAR(40) DEFAULT '🕐 '",
            "notifications":"BOOLEAN DEFAULT TRUE", "message_count":"INTEGER DEFAULT 0", "last_seen":"TIMESTAMP NULL", "self_session":"TEXT NULL", "phone_masked":"VARCHAR(64) NULL", "self_enabled":"BOOLEAN DEFAULT FALSE", "smart_mode":"BOOLEAN DEFAULT FALSE", "name_lock":"BOOLEAN DEFAULT FALSE", "locked_name":"VARCHAR(255) NULL", "word_filter":"BOOLEAN DEFAULT FALSE", "word_filter_text":"TEXT NULL", "media_lock":"BOOLEAN DEFAULT FALSE", "comments_mode":"BOOLEAN DEFAULT FALSE", "button_theme":"VARCHAR(32) DEFAULT 'orange'"
        }
        for col, definition in additions.items():
            if col not in existing: await conn.execute(sql_text(f"ALTER TABLE users ADD COLUMN {col} {definition}"))

def now_for(u):
    try: return datetime.now(ZoneInfo(u.timezone))
    except Exception: return datetime.now(ZoneInfo(DEFAULT_TZ))
def masked(phone):
    d="".join(x for x in phone if x.isdigit()); return "***" if len(d)<=4 else "+"+"*"*(len(d)-4)+d[-4:]
def encrypt(v):
    if not FERNET: raise RuntimeError("SESSION_SECRET تنظیم نشده است")
    return FERNET.encrypt(v.encode()).decode()
def decrypt(v):
    if not v or not FERNET: return None
    try: return FERNET.decrypt(v.encode()).decode()
    except (InvalidToken, ValueError): return None

def self_ready(): return TG_API_ID > 0 and bool(TG_API_HASH) and FERNET is not None

async def get_self_client(uid):
    if not self_ready(): return None
    async with Session() as s:
        u=await get_user(s,uid); raw=decrypt(u.self_session) if u and u.self_enabled else None
    if not raw: return None
    c=TelegramClient(StringSession(raw), TG_API_ID, TG_API_HASH); await c.connect()
    if not await c.is_user_authorized(): await c.disconnect(); return None
    return c

async def update_clock_once(uid):
    c = await get_self_client(uid)
    if not c: return False
    try:
        async with Session() as s: u=await get_user(s,uid)
        if not u or not u.clock_enabled: return True
        tm=stylize_time(now_for(u).strftime("%H:%M"),u.clock_font)
        bio = f"{u.clock_prefix}{tm}"
        name = f"{u.locked_name or u.first_name or 'User'} {tm}"
        kwargs={}
        if u.clock_target in ("bio","both"): kwargs["about"]=bio[:70]
        if u.clock_target in ("name","both"): kwargs["first_name"]=name[:64]
        if kwargs: await c(UpdateProfileRequest(**kwargs))
        return True
    except Exception as e:
        log.warning("clock update %s: %s",uid,e); return False
    finally: await c.disconnect()

async def clock_loop(uid):
    try:
        while True:
            ok=await update_clock_once(uid)
            if not ok: break
            await asyncio.sleep(60)
    except asyncio.CancelledError: pass
    finally: CLOCK_TASKS.pop(uid,None)

def start_clock(uid):
    old=CLOCK_TASKS.get(uid)
    if old and not old.done(): old.cancel()
    CLOCK_TASKS[uid]=asyncio.create_task(clock_loop(uid))

def stop_clock(uid):
    t=CLOCK_TASKS.pop(uid,None)
    if t and not t.done(): t.cancel()

async def profile_text(uid):
    async with Session() as s:
        u=await get_user(s,uid)
        if not u:return "پروفایل پیدا نشد."
        t=now_for(u).strftime("%H:%M:%S")
        return f"👤 <b>پروفایل شما</b>\n\n🆔 <code>{u.id}</code>\n🔗 @{escape(u.username or 'ندارد')}\n🌍 {u.timezone}\n🕐 {stylize_time(t,u.clock_font)}\n💬 {u.message_count or 0} پیام\n🔐 سلف: {'فعال' if u.self_enabled else 'خاموش'}\n⏰ ساعت پروفایل: {'روشن' if u.clock_enabled else 'خاموش'}\n🔤 فونت: {CLOCK_FONTS.get(u.clock_font,('',))[0]}"

@dp.message(CommandStart())
async def start(m: Message):
    uid=m.from_user.id
    if uid==OWNER_ID:
        async with Session() as s:
            u=await get_user(s,uid)
            if not u:
                s.add(User(id=uid,username=m.from_user.username,first_name=m.from_user.first_name,approved=True))
                await s.commit()
        await m.answer("👑 <b>پنل مدیریت سلف</b>\n\nاز منوی زیر همه بخش‌ها را مدیریت کن.",reply_markup=main_kb()); return
    async with Session() as s:
        u=await get_user(s,uid)
        if not u:
            u=User(id=uid,username=m.from_user.username,first_name=m.from_user.first_name); s.add(u); await s.commit()
            await m.answer("⏳ <b>درخواست دسترسی ثبت شد.</b>\n\nابتدا مدیر باید درخواستت را تأیید کند. تا قبل از تأیید، هیچ بخش پنل یا ورود سلف قابل استفاده نیست.")
            try: await m.bot.send_message(OWNER_ID,f"📥 درخواست جدید\n👤 {escape(m.from_user.full_name)}\n🆔 <code>{uid}</code>",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ تأیید",callback_data=f"approve:{uid}"),InlineKeyboardButton(text="❌ رد",callback_data=f"reject:{uid}")]]))
            except Exception: pass
            return
    if not await allowed(uid): await m.answer("⛔ دسترسی شما هنوز تأیید نشده است."); return
    await m.answer("🔥 <b>دسترسی تأیید شد.</b>\n\nورود به اکانت سلف را همین حالا شروع می‌کنیم.",reply_markup=login_stage_kb())


@dp.message(Command("panel"))
async def panel(m:Message):
    if await allowed(m.from_user.id): await m.answer("🎛 <b>پنل کامل</b>",reply_markup=main_kb())
    else: await m.answer("⛔ ابتدا باید توسط مدیر تأیید شوید.")

@dp.message(Command("help"))
async def help_cmd(m:Message):
    await m.answer("📚 <b>راهنمای سریع</b>\n\n/login +شماره → ورود سلف\n/code کد → کد ورود\n/password رمز → تأیید دومرحله‌ای\n/selfstatus → وضعیت سلف\n/selfoff → خاموش‌کردن سلف\n/clock → تنظیم ساعت\n/profile → پروفایل\n/autoreply متن → پاسخ خودکار\n/reaction ❤️ → واکنش خودکار\n/setname متن → تغییر نام\n/setbio متن → تغییر بیو\n/setusername نام → تغییر نام کاربری\n\nبرای توضیح کامل هر بخش، از خود پنل روی «راهنمای کامل» بزن.")

@dp.message(Command("login"))
async def login(m:Message):
    if not await allowed(m.from_user.id):
        await m.answer("⛔ ابتدا باید درخواست دسترسی شما توسط مدیر تأیید شود.")
        return
    if not self_ready():
        await m.answer("❌ ورود سلف آماده نیست. مدیر باید TG_API_ID، TG_API_HASH و SESSION_SECRET را در Railway تنظیم کند.")
        return
    # اگر شماره در دستور آمده باشد، همان مسیر سریع حفظ می‌شود.
    p=(m.text or "").split(maxsplit=1)
    if len(p)==2:
        await begin_login(m, p[1].strip())
        return
    await m.answer("🔐 <b>ورود امن به اکانت</b>\n\nبرای ورود راحت‌تر، دکمه «ارسال شماره من» را بزن. شماره فقط برای درخواست کد ورود تلگرام استفاده می‌شود.", reply_markup=login_stage_kb())

async def begin_login(m:Message, phone:str):
    if not phone or not any(ch.isdigit() for ch in phone):
        await m.answer("⚠️ شماره معتبر نیست. مثلاً +49123456789")
        return
    old=LOGIN_FLOWS.pop(m.from_user.id,None)
    if old:
        try: await old["client"].disconnect()
        except Exception: pass
    c=TelegramClient(StringSession(),TG_API_ID,TG_API_HASH); await c.connect()
    try:
        sent=await c.send_code_request(phone)
        LOGIN_FLOWS[m.from_user.id]={"client":c,"phone":phone,"hash":sent.phone_code_hash,"stage":"code"}
        await m.answer("📨 <b>کد ورود ارسال شد</b>\n\nحالا کد آخرین پیام تلگرام را وارد کن. اعداد فارسی و انگلیسی هر دو قبول‌اند.\nمثال: <code>۱۲۳۴۵</code> یا <code>12345</code>", reply_markup=code_stage_kb())
    except Exception as e:
        await c.disconnect(); await m.answer("❌ ارسال کد ناموفق بود. شماره و تنظیمات API را بررسی کن.")
        log.warning("login request failed: %s",e)

@dp.message(F.contact)
async def contact_login(m:Message):
    if not await allowed(m.from_user.id): return
    if not self_ready():
        await m.answer("❌ ورود سلف آماده نیست.")
        return
    contact=m.contact
    if contact.user_id and contact.user_id != m.from_user.id:
        await m.answer("⚠️ فقط شماره خودت را ارسال کن.", reply_markup=login_stage_kb())
        return
    await begin_login(m, contact.phone_number if contact.phone_number.startswith("+") else "+"+contact.phone_number)

@dp.message(F.text == LOGIN_CANCEL_TEXT)
async def cancel_login(m:Message):
    f=LOGIN_FLOWS.pop(m.from_user.id,None)
    if f:
        try: await f["client"].disconnect()
        except Exception: pass
    await m.answer("❎ ورود لغو شد.", reply_markup=__import__("aiogram").types.ReplyKeyboardRemove())

@dp.message(Command("code"))
async def code(m:Message):
    p=(m.text or "").split(maxsplit=1)
    if len(p)!=2:
        await m.answer("🔢 فقط خود کد را بفرست؛ مثال: <code>۱۲۳۴۵</code>")
        return
    await submit_login_code(m, p[1])

# مهم: وقتی ورود سلف در انتظار کد است، پیام‌هایی مثل ۱۲۳۴۵ یا 12345
# مستقیماً به تأیید کد می‌روند؛ هیچ /code یا پیشوندی لازم نیست.
@dp.message(F.text.regexp(r"^[0-9۰-۹٠-٩]{4,8}$"))
async def plain_login_code(m:Message):
    f=LOGIN_FLOWS.get(m.from_user.id)
    if f and f.get("stage")=="code":
        await submit_login_code(m, m.text or "")
        return

@dp.message(Command("password"))
async def password(m:Message):
    p=(m.text or "").split(maxsplit=1)
    await submit_login_password(m, p[1] if len(p)==2 else "")

async def submit_login_password(m:Message, password_value:str):
    f=LOGIN_FLOWS.get(m.from_user.id)
    if not f:
        await m.answer("❌ نشست ورود پیدا نشد. دوباره /login را شروع کن.")
        return
    if not password_value:
        await m.answer("🔐 رمز دومرحله‌ای را وارد کن.", reply_markup=code_stage_kb())
        return
    try:
        await f["client"].sign_in(password=password_value)
        await finish_login(m,f)
    except Exception:
        await m.answer("❌ رمز دومرحله‌ای درست نیست. دوباره واردش کن.")

@dp.message(F.text & ~F.text.regexp(r"^/"))
async def login_text_stages(m:Message):
    # فقط وقتی واقعاً در یکی از مراحل ورود هستیم پیام را مصرف می‌کنیم؛ سایر متن‌ها به fallback می‌روند.
    f=LOGIN_FLOWS.get(m.from_user.id)
    if not f: return
    text=m.text or ""
    if text == LOGIN_CANCEL_TEXT: return
    if f.get("stage")=="password":
        await submit_login_password(m,text)
    else:
        await submit_login_code(m,text)

async def finish_login(m,f):
    uid=m.from_user.id; raw=f["client"].session.save(); await f["client"].disconnect(); LOGIN_FLOWS.pop(uid,None)
    async with Session() as s:
        u=await get_user(s,uid); u.self_session=encrypt(raw); u.phone_masked=masked(f["phone"]); u.self_enabled=True; await s.commit()
    await m.answer("✅ <b>ورود موفق بود!</b>\n\nسلف برای حساب شما فعال شد. نشست به‌صورت رمزنگاری‌شده ذخیره شد.\nحالا پنل کامل را از دکمه زیر باز کن.",reply_markup=__import__("aiogram").types.ReplyKeyboardRemove())
    await m.answer("🎛 <b>پنل حرفه‌ای آماده است</b>",reply_markup=main_kb())

@dp.message(Command("selfstatus"))
async def selfstatus(m:Message):
    async with Session() as s:u=await get_user(s,m.from_user.id)
    await m.answer(f"🔐 <b>وضعیت سلف</b>\n\nفعال: {'✅' if u and u.self_enabled else '❌'}\nشماره: {escape(u.phone_masked or 'ثبت نشده') if u else 'ثبت نشده'}\nساعت زنده: {'✅' if u and u.clock_enabled else '❌'}")

@dp.message(Command("selfoff"))
async def selfoff(m:Message):
    stop_clock(m.from_user.id)
    async with Session() as s:
        u=await get_user(s,m.from_user.id)
        if u: u.self_enabled=False; u.self_session=None; u.phone_masked=None; u.clock_enabled=False; await s.commit()
    await m.answer("🔴 سلف خاموش شد و نشست ذخیره‌شده حذف شد.")

@dp.message(Command("clock"))
async def clock_cmd(m:Message):
    if not await allowed(m.from_user.id): return
    await m.answer("⏰ <b>ساعت زنده پروفایل</b>\n\nاین قابلیت ساعت را به‌صورت خودکار در بیو یا نام پروفایل حساب سلف قرار می‌دهد.\n\n⚠️ برای جلوگیری از محدودیت تلگرام، بروزرسانی هر دقیقه انجام می‌شود و ممکن است تلگرام در بعضی حساب‌ها سرعت تغییرات پروفایل را محدود کند.",reply_markup=clock_kb())

@dp.message(Command("profile"))
async def profile(m:Message):
    if await allowed(m.from_user.id): await m.answer(await profile_text(m.from_user.id))

@dp.message(Command("setname"))
async def setname(m:Message):
    if not await allowed(m.from_user.id): return
    p=(m.text or "").split(maxsplit=1)
    if len(p)!=2: await m.answer("مثال: /setname نام جدید"); return
    c=await get_self_client(m.from_user.id)
    if not c: await m.answer("❌ ابتدا با /login وارد سلف شو."); return
    try: await c(UpdateProfileRequest(first_name=p[1][:64])); await m.answer("✅ نام تغییر کرد.")
    except Exception: await m.answer("❌ تغییر نام ناموفق بود.")
    finally: await c.disconnect()

@dp.message(Command("setbio"))
async def setbio(m:Message):
    if not await allowed(m.from_user.id): return
    p=(m.text or "").split(maxsplit=1)
    if len(p)!=2: await m.answer("مثال: /setbio متن بیو"); return
    c=await get_self_client(m.from_user.id)
    if not c: await m.answer("❌ ابتدا /login را انجام بده."); return
    try: await c(UpdateProfileRequest(about=p[1][:70])); await m.answer("✅ بیو تغییر کرد.")
    except Exception: await m.answer("❌ تغییر بیو ناموفق بود.")
    finally: await c.disconnect()

@dp.message(Command("setusername"))
async def setusername(m:Message):
    if not await allowed(m.from_user.id): return
    p=(m.text or "").split(maxsplit=1)
    if len(p)!=2: await m.answer("مثال: /setusername MyName"); return
    c=await get_self_client(m.from_user.id)
    if not c: await m.answer("❌ ابتدا /login را انجام بده."); return
    try: await c(UpdateUsernameRequest(p[1].lstrip("@"))); await m.answer("✅ نام کاربری تغییر کرد.")
    except Exception: await m.answer("❌ نام کاربری آزاد نیست یا شرایط تلگرام را ندارد.")
    finally: await c.disconnect()

@dp.message(Command("autoreply"))
async def autoreply(m:Message):
    if not await allowed(m.from_user.id): return
    p=(m.text or "").split(maxsplit=1)
    async with Session() as s:
        u=await get_user(s,m.from_user.id); u.auto_reply=p[1] if len(p)==2 else None; await s.commit()
    await m.answer("🤖 پاسخ خودکار " + ("فعال شد." if len(p)==2 else "خاموش شد."))

@dp.message(Command("reaction"))
async def reaction(m:Message):
    if not await allowed(m.from_user.id): return
    p=(m.text or "").split(maxsplit=1)
    async with Session() as s:
        u=await get_user(s,m.from_user.id); u.auto_reaction=p[1][:8] if len(p)==2 else None; await s.commit()
    await m.answer("👍 واکنش خودکار " + (f"روی {escape(p[1])} تنظیم شد." if len(p)==2 else "خاموش شد."))

@dp.callback_query(F.data=="main")
async def main_cb(c:CallbackQuery): await c.message.edit_text("🎛 <b>منوی اصلی</b>\n\nهر بخش راهنمای داخلی دارد.",reply_markup=main_kb()); await c.answer()
@dp.callback_query(F.data=="close")
async def close_cb(c:CallbackQuery): await c.message.delete(); await c.answer()

FEATURES={
"animation":("✨ انیمیشن","برای جلوه‌های نمایشی پیام‌هاست. از این بخش می‌توانی حالت‌های نمایشی را فعال/غیرفعال کنی. قابلیت‌های نمایشی روی پیام‌های پنل اجرا می‌شوند و روی محدودیت‌های خود تلگرام غلبه نمی‌کنند."),
"users_menu":("👤 کاربران","مدیریت وضعیت دسترسی کاربران، مشاهده آمار و رسیدگی به درخواست‌ها از این بخش انجام می‌شود. مدیر می‌تواند کاربران را تأیید یا مسدود کند."),
"media":("🔒 قفل رسانه","با این قابلیت می‌توانی دریافت رسانه در گفت‌وگوهای مدیریت‌شده را کنترل کنی. برای روشن/خاموش کردن از دکمه زیر استفاده کن."),
"comments":("💬 کامنت","حالت مدیریت کامنت برای جریان‌های مرتبط با گروه/کانال. این قابلیت فقط در جاهایی که حساب سلف دسترسی لازم دارد عمل می‌کند."),
"public":("📌 عمومی","تنظیمات عمومی سلف و دسترسی‌های پنل در این قسمت قرار می‌گیرند."),
"actions":("🎭 اکشن","مجموعه ابزارهای واکنش و عملیات سریع روی پیام‌ها. برای واکنش خودکار از /reaction استفاده کن."),
"games":("🎮 بازی‌ها","بازی‌های کوچک داخلی مثل تاس و فال. دستور نمونه: /dice"),
"translate":("🌐 ترجمه","متن را با /translate متن بفرست تا لینک ترجمه آماده شود. برای ترجمه زنده نیاز به سرویس ترجمه جداگانه است."),
"google":("🔎 گوگل","/google عبارت را بفرست تا لینک جست‌وجوی آماده دریافت کنی."),
"info":("ℹ️ اطلاعات","اطلاعات حساب و وضعیت سلف را با /profile و /selfstatus ببین."),
"profile":("🖼 پروفایل","مدیریت اطلاعات حساب سلف: نام، بیو و نام کاربری. دستورات /setname، /setbio و /setusername هستند."),
"text_style":("✍️ استایل متن","برای متن ضخیم /bold متن و برای متن کدی /codeText متن را استفاده کن."),
"messages":("✉️ مدیریت پیام","پاسخ خودکار با /autoreply متن فعال می‌شود و بدون متن خاموش می‌شود. مدیریت انبوه مزاحم ارائه نمی‌شود."),
"reaction":("👍 ری‌اکشن","/reaction ❤️ واکنش خودکار را فعال می‌کند؛ /reaction بدون متن آن را خاموش می‌کند."),
"enemies":("🚫 مسدودی‌ها","مدیریت مسدودی کاربران پنل توسط مدیر با /ban ID و /unban ID انجام می‌شود."),
"edit":("✏️ تغییر پروفایل","نام، بیو و نام کاربری سلف را تغییر بده. هر تغییر از طریق API رسمی تلگرام انجام می‌شود."),
"filter":("🔎 فیلتر کلمات","فیلتر کلمات را برای پیام‌های دریافتی مدیریت کن. این قابلیت فقط روی پیام‌هایی که بات امکان مدیریتشان را دارد عمل می‌کند."),
"namelock":("🛡 حفاظت اسم","برای جلوگیری از تغییر ناخواسته نام توسط ابزار ساعت، هدف ساعت را روی بیو بگذار یا نام پایه را در تنظیمات نگه دار."),
"smart":("🤖 هوشمند","حالت پاسخ هوشمند ساده برای پیام‌های دریافتی. برای هوش مصنوعی واقعی باید API مدل را در محیط اضافه کرد."),
"report":("📣 گزارش","مشکل را با /report متن برای مدیر ارسال کن."),
"tools":("🛠 ابزارها","ابزارهای کمکی: زمان، پروفایل، تبدیل متن، جست‌وجو و مدیریت نشست."),
"secretary":("🧠 منشی","پاسخ خودکار با /autoreply و حالت هوشمند از این بخش قابل مدیریت است."),
"broadcast":("📢 اطلاعیه","مدیر می‌تواند با ریپلای روی یک پیام و /broadcast آن را با سرعت کنترل‌شده برای کاربران تأییدشده ارسال کند. اسپم و ارسال مزاحم عمداً محدود شده است."),
"fortune":("🔮 فال","/fortune یک فال سرگرمی تصادفی می‌دهد."),
"secret":("🔐 متن رمزی","/secret متن برای کدگذاری Base64 و /unsecret کد برای بازکردن آن. این رمزنگاری امن برای اطلاعات حساس نیست."),
"widgets":("🧩 ابزارک‌ها","ابزارک‌های داخلی مثل ساعت، پروفایل و تنظیمات سریع در این قسمت قرار دارند."),
"backup":("📦 بکاپ","اطلاعات تنظیمات در دیتابیس نگهداری می‌شود. نشست سلف رمزنگاری‌شده ذخیره می‌شود؛ توکن‌ها را در GitHub قرار نده."),
"buttons":("🎨 دکمه‌ها","منوی فارسی و توضیح هر بخش برای استفاده راحت اعضای کانال آماده شده است."),
}

@dp.callback_query(F.data=="time")
async def time_page(c:CallbackQuery): await c.message.edit_text("⏰ <b>زمان و پروفایل</b>\n\nساعت زنده می‌تواند در بیو، نام یا هر دو قرار بگیرد. منطقه زمانی و فونت را خودت انتخاب می‌کنی.\n\nراهنما: اول وارد سلف شو، فونت را انتخاب کن، منطقه زمانی را بزن و بعد «ساعت روشن» را بزن.",reply_markup=clock_kb()); await c.answer()
@dp.callback_query(F.data=="clockfonts")
async def clockfonts(c:CallbackQuery): await c.message.edit_text("🔤 <b>فونت ساعت</b>\n\nیکی را انتخاب کن. نمونه زیر هر دکمه نشان می‌دهد ساعت چگونه دیده می‌شود.",reply_markup=fonts_kb()); await c.answer()
@dp.callback_query(F.data.startswith("font:"))
async def font_cb(c:CallbackQuery):
    font=c.data.split(":",1)[1]
    async with Session() as s:u=await get_user(s,c.from_user.id); u.clock_font=font; await s.commit()
    await c.answer("فونت ذخیره شد")
    await c.message.edit_text("✅ فونت ساعت ذخیره شد.\n\nحالا می‌توانی از بخش زمان، ساعت را روشن کنی.",reply_markup=clock_kb())
@dp.callback_query(F.data.startswith("clock:"))
async def clock_toggle(c:CallbackQuery):
    val=c.data.split(":",1)[1]
    async with Session() as s:
        u=await get_user(s,c.from_user.id)
        if not u: await c.answer("کاربر پیدا نشد",show_alert=True); return
        if val=="on" and not u.self_enabled:
            await c.answer("اول اتصال اکانت را کامل کن",show_alert=True); return
        u.clock_enabled=val=="on"; enabled=u.clock_enabled; await s.commit()
    if enabled:
        start_clock(c.from_user.id); await update_clock_once(c.from_user.id); await c.answer("⏰ ساعت زنده روشن شد")
    else: stop_clock(c.from_user.id); await c.answer("⏰ ساعت خاموش شد")
@dp.callback_query(F.data.startswith("clocktarget:"))
async def target_cb(c:CallbackQuery):
    target=c.data.split(":",1)[1]
    async with Session() as s:u=await get_user(s,c.from_user.id); u.clock_target=target; await s.commit()
    await c.answer("محل نمایش ساعت ذخیره شد")
@dp.callback_query(F.data.startswith("tz:"))
async def tz_cb(c:CallbackQuery):
    tz=c.data.split(":",1)[1]
    async with Session() as s:u=await get_user(s,c.from_user.id); u.timezone=tz; await s.commit()
    await c.answer("منطقه زمانی ذخیره شد")
    if u.clock_enabled: await update_clock_once(c.from_user.id)

@dp.callback_query(F.data=="users_menu")
async def users_menu(c:CallbackQuery):
    if c.from_user.id!=OWNER_ID:
        await c.answer("این بخش فقط برای مدیر است",show_alert=True); return
    async with Session() as s:
        total=(await s.execute(select(func.count()).select_from(User))).scalar() or 0
        pending=(await s.execute(select(func.count()).select_from(User).where(User.approved==False,User.banned==False))).scalar() or 0
        approved=(await s.execute(select(func.count()).select_from(User).where(User.approved==True,User.banned==False))).scalar() or 0
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📥 درخواست‌های در انتظار",callback_data="pending_users")],[InlineKeyboardButton(text="📊 آمار کاربران",callback_data="user_stats")],[InlineKeyboardButton(text="⬅️ منوی اصلی",callback_data="main")]])
    await c.message.edit_text(f"👤 <b>مدیریت کاربران</b>\n\nکل: {total}\nتأییدشده: {approved}\nدر انتظار: {pending}\n\nهر کاربر تا قبل از تأیید مدیر کاملاً مسدود است.",reply_markup=kb); await c.answer()

@dp.callback_query(F.data=="user_stats")
async def user_stats(c:CallbackQuery):
    if c.from_user.id!=OWNER_ID: return
    async with Session() as s:
        rows=(await s.execute(select(User).order_by(User.message_count.desc()).limit(10))).scalars().all()
    lines=["📊 <b>آمار کاربران</b>",""]
    for i,u in enumerate(rows,1): lines.append(f"{i}. <code>{u.id}</code> — {u.message_count or 0} پیام — {'فعال' if u.approved and not u.banned else 'غیرفعال'}")
    await c.message.edit_text("\n".join(lines),reply_markup=back()); await c.answer()

@dp.callback_query(F.data=="pending_users")
async def pending_users(c:CallbackQuery):
    if c.from_user.id!=OWNER_ID: return
    async with Session() as s:
        rows=(await s.execute(select(User).where(User.approved==False,User.banned==False).order_by(User.created_at.desc()).limit(15))).scalars().all()
    if not rows:
        await c.message.edit_text("📥 <b>درخواست معلقی وجود ندارد.</b>",reply_markup=back()); await c.answer(); return
    buttons=[]
    for u in rows: buttons.append([InlineKeyboardButton(text=f"👤 {u.first_name or u.id}",callback_data=f"pending:{u.id}"),InlineKeyboardButton(text="✅",callback_data=f"approve:{u.id}"),InlineKeyboardButton(text="❌",callback_data=f"reject:{u.id}")])
    buttons.append([InlineKeyboardButton(text="⬅️ برگشت",callback_data="users_menu")])
    await c.message.edit_text("📥 <b>درخواست‌های در انتظار</b>",reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)); await c.answer()

@dp.callback_query(F.data.startswith("pending:"))
async def pending_detail(c:CallbackQuery):
    if c.from_user.id!=OWNER_ID: return
    uid=int(c.data.split(":",1)[1])
    async with Session() as s: u=await get_user(s,uid)
    if not u: await c.answer("کاربر پیدا نشد",show_alert=True); return
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ تأیید",callback_data=f"approve:{uid}"),InlineKeyboardButton(text="❌ رد",callback_data=f"reject:{uid}")],[InlineKeyboardButton(text="⬅️ درخواست‌ها",callback_data="pending_users")]])
    await c.message.edit_text(f"👤 <b>جزئیات درخواست</b>\n\nنام: {escape(u.first_name or '—')}\nیوزرنیم: @{escape(u.username or 'ندارد')}\nID: <code>{uid}</code>",reply_markup=kb); await c.answer()

@dp.callback_query(F.data=="profile")
async def profile_page(c:CallbackQuery):
    if not await allowed(c.from_user.id): await c.answer("دسترسی ندارید",show_alert=True); return
    text=await profile_text(c.from_user.id)
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✏️ تغییر نام",callback_data="edit_name"),InlineKeyboardButton(text="📝 تغییر بیو",callback_data="edit_bio")],[InlineKeyboardButton(text="🔗 تغییر یوزرنیم",callback_data="edit_username")],[InlineKeyboardButton(text="🔄 بروزرسانی",callback_data="profile"),InlineKeyboardButton(text="⬅️ منو",callback_data="main")]])
    await c.message.edit_text(text+"\n\n<b>تغییر سریع:</b> از دستورهای /setname، /setbio و /setusername هم می‌توانی استفاده کنی.",reply_markup=kb); await c.answer()

@dp.callback_query(F.data=="text_style")
async def text_style_page(c:CallbackQuery):
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="𝐁 ضخیم",callback_data="style:bold"),InlineKeyboardButton(text="𝙼 تک‌عرض",callback_data="style:mono")],[InlineKeyboardButton(text="𝘐 کج",callback_data="style:italic"),InlineKeyboardButton(text="S̶ خط‌خورده",callback_data="style:strike")],[InlineKeyboardButton(text="🔐 رمزی",callback_data="secret")],[InlineKeyboardButton(text="⬅️ منو",callback_data="main")]])
    await c.message.edit_text("✍️ <b>استایل متن</b>\n\nبرای تست سریع، دستورهای /bold، /codeText و /secret را استفاده کن.\nاین بخش عمداً متن را بدون تغییر خطرناک روی اکانت ارسال نمی‌کند.",reply_markup=kb); await c.answer()

@dp.callback_query(F.data=="tools")
async def tools_page(c:CallbackQuery):
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⏰ ساعت",callback_data="time"),InlineKeyboardButton(text="👤 پروفایل",callback_data="profile")],[InlineKeyboardButton(text="🎲 تاس",callback_data="tool:dice"),InlineKeyboardButton(text="🔮 فال",callback_data="tool:fortune")],[InlineKeyboardButton(text="📚 راهنما",callback_data="help_all"),InlineKeyboardButton(text="⬅️ منو",callback_data="main")]])
    await c.message.edit_text("🛠 <b>ابزارهای سریع</b>\n\nابزارهای پرکاربرد را مستقیم از همین صفحه اجرا کن.",reply_markup=kb); await c.answer()

@dp.callback_query(F.data=="tool:dice")
async def tool_dice(c:CallbackQuery): await c.answer(f"🎲 {random.randint(1,6)}",show_alert=True)
@dp.callback_query(F.data=="tool:fortune")
async def tool_fortune(c:CallbackQuery): await c.answer("🔮 "+random.choice(["امروز برای شروع یک کار خوب مناسب است.","کمی صبر کن؛ نتیجه بهتر خواهد شد.","یک خبر خوب می‌تواند نزدیک باشد."]),show_alert=True)

@dp.callback_query(F.data.in_({"edit_name","edit_bio","edit_username"}))
async def edit_shortcuts(c:CallbackQuery):
    cmd={"edit_name":"/setname نام جدید","edit_bio":"/setbio متن بیو","edit_username":"/setusername username"}[c.data]
    await c.answer("دستور آماده شد",show_alert=False)
    await c.message.edit_text(f"✏️ <b>تغییر پروفایل</b>\n\nاین دستور را ارسال کن:\n<code>{cmd}</code>\n\nمثال را با مقدار دلخواه خودت جایگزین کن.",reply_markup=back())

@dp.callback_query(F.data.in_(list(FEATURES.keys())))
async def feature(c:CallbackQuery):
    title,body=FEATURES[c.data]; kb=back()
    if c.data=="media":
        kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔒 روشن/خاموش",callback_data="toggle:media")],[InlineKeyboardButton(text="⬅️ منوی اصلی",callback_data="main")]])
    await c.message.edit_text(f"{title}\n\n{body}\n\n<b>راهنما:</b> دکمه‌ها یا دستورهای همین صفحه را استفاده کن؛ وضعیت‌ها در دیتابیس ذخیره می‌شوند.",reply_markup=kb); await c.answer()

@dp.callback_query(F.data.startswith("toggle:"))
async def toggle(c:CallbackQuery):
    field=c.data.split(":",1)[1]
    if field not in {"media"}: return
    async with Session() as s:
        u=await get_user(s,c.from_user.id); u.media_lock=not u.media_lock; v=u.media_lock; await s.commit()
    await c.answer("قفل رسانه "+("روشن شد 🔒" if v else "خاموش شد 🔓"))

@dp.callback_query(F.data.startswith("approve:"))
async def approve(c:CallbackQuery):
    if c.from_user.id!=OWNER_ID:return
    uid=int(c.data.split(":")[1])
    async with Session() as s:u=await get_user(s,uid); u.approved=True; u.banned=False; await s.commit()
    await c.answer("تأیید شد"); await c.message.edit_reply_markup(reply_markup=None)
    try: await c.bot.send_message(uid,"🎉 <b>درخواستت تأیید شد.</b>\n\nحالا مستقیم وارد مرحله اتصال اکانت می‌شوی. شماره خودت را با دکمه زیر ارسال کن.",reply_markup=login_stage_kb())
    except Exception: pass
@dp.callback_query(F.data.startswith("reject:"))
async def reject(c:CallbackQuery):
    if c.from_user.id!=OWNER_ID:return
    uid=int(c.data.split(":")[1])
    async with Session() as s:u=await get_user(s,uid); u.approved=False; await s.commit()
    await c.answer("رد شد"); await c.message.edit_reply_markup(reply_markup=None)

@dp.message(Command("ban"))
async def ban(m:Message):
    if m.from_user.id!=OWNER_ID:return
    p=(m.text or "").split(maxsplit=1)
    if len(p)!=2 or not p[1].isdigit(): await m.answer("مثال: /ban 123456789"); return
    async with Session() as s:u=await get_user(s,int(p[1]));
    if not u: await m.answer("کاربر پیدا نشد."); return
    async with Session() as s:u=await get_user(s,int(p[1])); u.banned=True;u.approved=False;await s.commit()
    stop_clock(int(p[1])); await m.answer("🚫 کاربر مسدود شد.")
@dp.message(Command("unban"))
async def unban(m:Message):
    if m.from_user.id!=OWNER_ID:return
    p=(m.text or "").split(maxsplit=1)
    if len(p)!=2 or not p[1].isdigit(): await m.answer("مثال: /unban 123456789"); return
    async with Session() as s:u=await get_user(s,int(p[1]));
    if not u: await m.answer("کاربر پیدا نشد."); return
    async with Session() as s:u=await get_user(s,int(p[1])); u.banned=False;await s.commit()
    await m.answer("✅ رفع مسدودی شد.")

@dp.message(Command("google"))
async def google(m:Message):
    p=(m.text or "").split(maxsplit=1)
    await m.answer(f"🔎 <a href=\"https://www.google.com/search?q={quote_plus(p[1])}\">جست‌وجوی گوگل</a>" if len(p)==2 else "مثال: /google عبارت")
@dp.message(Command("translate"))
async def translate(m:Message):
    p=(m.text or "").split(maxsplit=1)
    await m.answer(f"🌐 <a href=\"https://translate.google.com/?sl=auto&tl=fa&text={quote_plus(p[1])}\">بازکردن ترجمه</a>" if len(p)==2 else "مثال: /translate متن")
@dp.message(Command("dice"))
async def dice(m:Message): await m.answer(f"🎲 نتیجه: <b>{random.randint(1,6)}</b>")
@dp.message(Command("fortune"))
async def fortune(m:Message): await m.answer("🔮 "+random.choice(["امروز برای شروع یک کار خوب مناسب است.","کمی صبر کن؛ نتیجه بهتر خواهد شد.","یک خبر خوب می‌تواند نزدیک باشد.","روی چیزی که کنترلش می‌کنی تمرکز کن."]))
@dp.message(Command("bold"))
async def bold(m:Message):
    p=(m.text or "").split(maxsplit=1); await m.answer(f"<b>{escape(p[1])}</b>" if len(p)==2 else "مثال: /bold سلام")
@dp.message(Command("codeText"))
async def codetext(m:Message):
    p=(m.text or "").split(maxsplit=1); await m.answer(f"<code>{escape(p[1])}</code>" if len(p)==2 else "مثال: /codeText hello")
@dp.message(Command("secret"))
async def secret(m:Message):
    p=(m.text or "").split(maxsplit=1); await m.answer("🔐 <code>"+base64.b64encode(p[1].encode()).decode()+"</code>" if len(p)==2 else "مثال: /secret متن")
@dp.message(Command("unsecret"))
async def unsecret(m:Message):
    p=(m.text or "").split(maxsplit=1)
    if len(p)!=2: await m.answer("مثال: /unsecret کد"); return
    try: await m.answer("🔓 "+escape(base64.b64decode(p[1]).decode()))
    except Exception: await m.answer("❌ کد معتبر نیست.")
@dp.message(Command("report"))
async def report(m:Message):
    p=(m.text or "").split(maxsplit=1)
    if len(p)!=2: await m.answer("مثال: /report مشکل"); return
    await m.bot.send_message(OWNER_ID,f"📣 <b>گزارش</b>\n👤 <code>{m.from_user.id}</code>\n{escape(p[1])}"); await m.answer("✅ گزارش ارسال شد.")

@dp.message(Command("broadcast"))
async def broadcast(m:Message):
    if m.from_user.id!=OWNER_ID or not m.reply_to_message: await m.answer("📢 برای اطلاعیه، روی پیام ریپلای کن و /broadcast بزن."); return
    async with Session() as s: users=(await s.execute(select(User).where(User.approved==True,User.banned==False))).scalars().all()
    status=await m.answer(f"📢 ارسال کنترل‌شده برای {len(users)} کاربر…"); ok=bad=0
    for u in users:
        try: await m.bot.copy_message(u.id,m.chat.id,m.reply_to_message.message_id); ok+=1
        except Exception: bad+=1
        await asyncio.sleep(.08)
    await status.edit_text(f"✅ تمام شد\n📨 موفق: {ok}\n❌ ناموفق: {bad}")

@dp.message()
async def fallback(m:Message):
    uid=m.from_user.id
    if not await allowed(uid): return
    async with Session() as s:
        u=await get_user(s,uid)
        if u:
            u.username=m.from_user.username;u.first_name=m.from_user.first_name;u.last_seen=datetime.now(timezone.utc);u.message_count=(u.message_count or 0)+1
            reply=u.auto_reply; reaction=u.auto_reaction; media=u.media_lock
            await s.commit()
    if media and m.content_type in {"photo","video","document","audio","voice","animation","sticker"}:
        try: await m.delete()
        except Exception: pass
        return
    if reply and not (m.text or "").startswith("/"): await m.answer(reply)
    if reaction:
        try: await m.bot.set_message_reaction(m.chat.id,m.message_id,[ReactionTypeEmoji(emoji=reaction)])
        except Exception: pass

async def main():
    await ensure_schema()
    bot=Bot(TOKEN,default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    # ساعت‌های فعال بعد از Restart دوباره راه‌اندازی می‌شوند.
    async with Session() as s:
        ids=(await s.execute(select(User.id).where(User.self_enabled==True,User.clock_enabled==True))).scalars().all()
    for uid in ids: start_clock(uid)
    log.info("Bot started")
    try: await dp.start_polling(bot)
    finally:
        for t in list(CLOCK_TASKS.values()): t.cancel()
        await bot.session.close()

if __name__=="__main__": asyncio.run(main())
