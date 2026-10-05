import asyncio
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
from telethon.errors import SessionPasswordNeededError
from telethon.tl.functions.account import UpdateProfileRequest

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ReactionTypeEmoji
from sqlalchemy import Boolean, DateTime, Integer, String, Text, select, func, inspect, text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("telegram-manager")

TOKEN = os.environ["BOT_TOKEN"]
OWNER_ID = int(os.environ["OWNER_ID"])
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DEFAULT_TZ = os.getenv("DEFAULT_TIMEZONE", "Europe/Berlin")
TG_API_ID = int(os.environ.get("TG_API_ID", "0"))
TG_API_HASH = os.environ.get("TG_API_HASH", "").strip()
SESSION_SECRET = os.environ.get("SESSION_SECRET", "").strip()
if SESSION_SECRET:
    try:
        SESSION_FERNET = Fernet(SESSION_SECRET.encode())
    except Exception:
        raise RuntimeError("SESSION_SECRET must be a valid Fernet key")
else:
    SESSION_FERNET = None

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://") and "+asyncpg" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
if not DATABASE_URL or "://" not in DATABASE_URL:
    DATABASE_URL = "sqlite+aiosqlite:///./bot.db"

class Base(DeclarativeBase):
    pass

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
    smart_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    name_lock: Mapped[bool] = mapped_column(Boolean, default=False)
    locked_name: Mapped[str | None] = mapped_column(String(255))
    word_filter: Mapped[bool] = mapped_column(Boolean, default=False)
    word_filter_text: Mapped[str | None] = mapped_column(Text)
    media_lock: Mapped[bool] = mapped_column(Boolean, default=False)
    comments_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    captcha_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    button_theme: Mapped[str] = mapped_column(String(32), default="orange")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
dp = Dispatcher()
LIVE_CLOCK_TASKS: dict[int, asyncio.Task] = {}
LOGIN_FLOWS: dict[int, dict] = {}

# ---------- Keyboards ----------
def main_kb():
    rows = [
        [("⏰ زمان و پروفایل", "time_profile"), ("✨ انیمیشن", "animation"), ("👤 کاربران", "users_menu")],
        [("🔒 قفل رسانه", "media_lock"), ("💬 کامنت", "comments"), ("📌 عمومی", "public")],
        [("🎭 اکشن", "actions"), ("🎮 بازی‌ها", "games"), ("🌐 ترجمه", "translate")],
        [("🔎 گوگل", "google"), ("ℹ️ اطلاعات", "info"), ("🖼 پروفایل", "profile")],
        [("✍️ استایل متن", "text_style"), ("✉️ مدیریت پیام", "message_manage"), ("👍 ری‌اکشن", "reaction_menu")],
        [("👹 دشمنان", "enemies"), ("✏️ تغییر پروفایل", "edit_profile"), ("🚫 فیلتر کلمات", "word_filter")],
        [("🛡 حفاظت اسم", "name_lock"), ("🤖 هوش مصنوعی", "smart"), ("📣 گزارش", "report")],
        [("🛠 ابزار", "tools"), ("💰 ارزها", "currency"), ("🧠 منشی هوشمند", "secretary")],
        [("📢 تگ همه", "tagall"), ("🔮 فال", "fortune"), ("🔐 متن رمزی", "secret_text")],
        [("🧩 ابزارک‌ها", "widgets"), ("📦 بکاپ‌گیری", "backup"), ("🎨 تنظیم دکمه‌ها", "button_settings")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=c) for t, c in r] for r in rows] + [[InlineKeyboardButton(text="✖️ بستن پنل", callback_data="close")]])

def back_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ منوی اصلی", callback_data="main")]])

def owner_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 کاربران", callback_data="users"), InlineKeyboardButton(text="📥 درخواست‌ها", callback_data="requests")],
        [InlineKeyboardButton(text="📊 آمار", callback_data="stats"), InlineKeyboardButton(text="📢 Broadcast", callback_data="broadcast_help")],
        [InlineKeyboardButton(text="🚫 دسترسی", callback_data="access_help"), InlineKeyboardButton(text="🕐 ساعت زنده", callback_data="liveclock_help")],
    ])

def approval_kb(uid: int):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ تأیید", callback_data=f"approve:{uid}"), InlineKeyboardButton(text="❌ رد", callback_data=f"reject:{uid}")]])

# ---------- DB ----------
async def get_user(session: AsyncSession, uid: int):
    return (await session.execute(select(User).where(User.id == uid))).scalar_one_or_none()

async def is_allowed(uid: int) -> bool:
    if uid == OWNER_ID:
        return True
    async with Session() as s:
        u = await get_user(s, uid)
        return bool(u and u.approved and not u.banned)

async def touch_user(message: Message):
    if not message.from_user or message.from_user.id == OWNER_ID:
        return
    async with Session() as s:
        u = await get_user(s, message.from_user.id)
        if u:
            u.username = message.from_user.username
            u.first_name = message.from_user.first_name
            u.last_seen = datetime.now(timezone.utc)
            u.message_count = (u.message_count or 0) + 1
            await s.commit()

async def ensure_schema():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        def cols(sync_conn):
            return {c["name"] for c in inspect(sync_conn).get_columns("users")}
        existing = await conn.run_sync(cols)
        additions = {
            "notifications": "BOOLEAN DEFAULT TRUE", "message_count": "INTEGER DEFAULT 0", "last_seen": "TIMESTAMP NULL",
            "self_session": "TEXT NULL", "phone_masked": "VARCHAR(64) NULL", "self_enabled": "BOOLEAN DEFAULT FALSE",
            "smart_mode": "BOOLEAN DEFAULT FALSE", "name_lock": "BOOLEAN DEFAULT FALSE", "locked_name": "VARCHAR(255) NULL",
            "word_filter": "BOOLEAN DEFAULT FALSE", "word_filter_text": "TEXT NULL", "media_lock": "BOOLEAN DEFAULT FALSE",
            "comments_mode": "BOOLEAN DEFAULT FALSE", "captcha_mode": "BOOLEAN DEFAULT FALSE", "button_theme": "VARCHAR(32) DEFAULT 'orange'",
        }
        for col, definition in additions.items():
            if col not in existing:
                await conn.execute(sql_text(f"ALTER TABLE users ADD COLUMN {col} {definition}"))

# ---------- Helpers ----------
def user_now(u: User):
    try:
        return datetime.now(ZoneInfo(u.timezone))
    except Exception:
        return datetime.now(ZoneInfo(DEFAULT_TZ))

async def profile_text(uid: int) -> str:
    async with Session() as s:
        u = await get_user(s, uid)
        if not u: return "پروفایل پیدا نشد."
        now = user_now(u)
        return (f"👤 <b>پروفایل</b>\n\n🆔 <code>{u.id}</code>\n🔗 @{escape(u.username or 'ندارد')}\n"
                f"🌍 {u.timezone}\n🕐 {now:%H:%M:%S}\n💬 {u.message_count or 0} پیام\n"
                f"🔐 سلف: {'فعال' if u.self_enabled else 'غیرفعال'}\n🤖 منشی: {'روشن' if u.smart_mode else 'خاموش'}")

def self_configured():
    return TG_API_ID > 0 and bool(TG_API_HASH) and SESSION_FERNET is not None

def mask_phone(phone: str):
    d = "".join(c for c in phone if c.isdigit())
    return "***" if len(d) <= 4 else "+" + "*" * (len(d) - 4) + d[-4:]

def encrypt_session(raw: str):
    if not SESSION_FERNET: raise RuntimeError("SESSION_SECRET is not configured")
    return SESSION_FERNET.encrypt(raw.encode()).decode()

def decrypt_session(value: str | None):
    if not value or not SESSION_FERNET: return None
    try: return SESSION_FERNET.decrypt(value.encode()).decode()
    except (InvalidToken, ValueError): return None

async def self_client_for(uid: int):
    if not self_configured(): return None
    async with Session() as s:
        u = await get_user(s, uid)
        if not u or not u.self_enabled or not u.self_session: return None
        raw = decrypt_session(u.self_session)
    if not raw: return None
    c = TelegramClient(StringSession(raw), TG_API_ID, TG_API_HASH)
    await c.connect()
    if not await c.is_user_authorized():
        await c.disconnect(); return None
    return c

async def require_self(message: Message):
    uid = message.from_user.id
    if not await is_allowed(uid):
        await message.answer("⏳ ابتدا باید دسترسی بات شما تأیید شود."); return None
    async with Session() as s:
        u = await get_user(s, uid)
        if not u or not u.self_enabled:
            await message.answer("🔐 برای این قابلیت باید ابتدا با شماره وارد شوی.\n/login +شماره")
            return None
    c = await self_client_for(uid)
    if not c:
        await message.answer("⚠️ نشست سلف معتبر نیست؛ دوباره /login را انجام بده.")
    return c

# ---------- Start / menu ----------
@dp.message(CommandStart())
async def start(message: Message, bot: Bot):
    uid = message.from_user.id
    async with Session() as s:
        u = await get_user(s, uid)
        if not u:
            u = User(id=uid, username=message.from_user.username, first_name=message.from_user.first_name, last_seen=datetime.now(timezone.utc))
            s.add(u); await s.commit()
            if uid != OWNER_ID:
                await bot.send_message(OWNER_ID, f"🔔 <b>درخواست دسترسی</b>\n👤 {escape(message.from_user.full_name)}\n🔗 @{escape(message.from_user.username or '-') }\n🆔 <code>{uid}</code>", reply_markup=approval_kb(uid))
        elif u.banned:
            await message.answer("⛔ دسترسی شما مسدود است."); return
        elif not u.approved and uid != OWNER_ID:
            await message.answer("⏳ منتظر تأیید مدیر باشید."); return
    await message.answer("🚀 <b>پنل سلف آماده است</b>\nاز دکمه‌های زیر استفاده کن.", reply_markup=main_kb())

@dp.message(Command("panel"))
async def panel(message: Message):
    if not await is_allowed(message.from_user.id): return
    await message.answer("🎛 <b>پنل اصلی</b>", reply_markup=main_kb())

@dp.message(Command("help"))
async def help_cmd(message: Message):
    await message.answer("📚 /panel پنل اصلی\n/login +شماره ورود سلف\n/code کد ورود\n/password رمز 2FA\n/selfstatus وضعیت سلف\n/selfoff خروج از سلف\n/profile پروفایل\n/clock ساعت زنده\n/settings تنظیمات")

# ---------- Self login ----------
@dp.message(Command("login"))
async def self_login(message: Message):
    uid = message.from_user.id
    if not await is_allowed(uid): await message.answer("⏳ ابتدا تأیید مدیر را بگیر."); return
    if not self_configured(): await message.answer("⚠️ TG_API_ID، TG_API_HASH و SESSION_SECRET را در Railway تنظیم کن."); return
    if uid in LOGIN_FLOWS: await message.answer("⏳ ورود در حال انجام است؛ کد را با /code بفرست."); return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip().startswith("+"):
        await message.answer("📱 مثال: <code>/login +491234567890</code>"); return
    phone = parts[1].strip()
    c = TelegramClient(StringSession(), TG_API_ID, TG_API_HASH)
    try:
        await c.connect(); sent = await c.send_code_request(phone)
        LOGIN_FLOWS[uid] = {"client": c, "phone": phone, "phone_code_hash": sent.phone_code_hash}
        await message.answer("📨 کد ورود ارسال شد.\n<code>/code 12345</code>\nکد را برای هیچ‌کس نفرست.")
    except Exception:
        await c.disconnect(); await message.answer("❌ ارسال کد انجام نشد.")

@dp.message(Command("code"))
async def self_code(message: Message):
    uid = message.from_user.id; flow = LOGIN_FLOWS.get(uid)
    if not flow: await message.answer("❌ ابتدا /login را اجرا کن."); return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2: await message.answer("مثال: <code>/code 12345</code>"); return
    try:
        await flow["client"].sign_in(phone=flow["phone"], code=parts[1].strip(), phone_code_hash=flow["phone_code_hash"])
    except SessionPasswordNeededError:
        flow["needs_password"] = True; await message.answer("🔐 رمز 2FA را با /password وارد کن."); return
    except Exception:
        await flow["client"].disconnect(); LOGIN_FLOWS.pop(uid, None); await message.answer("❌ کد نامعتبر یا منقضی شده."); return
    await finish_self_login(uid, flow["client"], flow["phone"], message)

@dp.message(Command("password"))
async def self_password(message: Message):
    uid = message.from_user.id; flow = LOGIN_FLOWS.get(uid)
    if not flow or not flow.get("needs_password"): await message.answer("❌ رمز 2FA درخواستی نیست."); return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2: await message.answer("مثال: <code>/password رمز</code>"); return
    try: await flow["client"].sign_in(password=parts[1])
    except Exception: await message.answer("❌ رمز 2FA اشتباه است."); return
    await finish_self_login(uid, flow["client"], flow["phone"], message)

async def finish_self_login(uid, client, phone, message):
    try:
        encrypted = encrypt_session(client.session.save())
        async with Session() as s:
            u = await get_user(s, uid)
            u.self_session = encrypted; u.phone_masked = mask_phone(phone); u.self_enabled = True
            await s.commit()
        await client.disconnect(); LOGIN_FLOWS.pop(uid, None)
        await message.answer(f"✅ <b>سلف فعال شد</b>\n📱 {mask_phone(phone)}\nحالا قابلیت‌های سلف پنل فعال هستند.", reply_markup=main_kb())
    except Exception:
        try: await client.disconnect()
        except Exception: pass
        LOGIN_FLOWS.pop(uid, None); await message.answer("❌ ذخیره امن نشست انجام نشد.")

@dp.message(Command("selfstatus"))
async def self_status(message: Message):
    async with Session() as s:
        u = await get_user(s, message.from_user.id)
        ok = bool(u and u.self_enabled and u.self_session)
        await message.answer(f"{'🟢' if ok else '🔴'} سلف {'فعال' if ok else 'غیرفعال'}\n📱 {u.phone_masked if u and ok else '-'}")

@dp.message(Command("selfoff"))
async def self_off(message: Message):
    async with Session() as s:
        u = await get_user(s, message.from_user.id)
        if u: u.self_session = None; u.phone_masked = None; u.self_enabled = False; await s.commit()
    await message.answer("✅ سلف غیرفعال و نشست ذخیره‌شده پاک شد.")

# ---------- Basic profile/time ----------
@dp.message(Command("profile"))
async def profile(message: Message):
    if await is_allowed(message.from_user.id): await message.answer(await profile_text(message.from_user.id))

@dp.message(Command("time"))
async def time_cmd(message: Message):
    if not await is_allowed(message.from_user.id): return
    async with Session() as s:
        u = await get_user(s, message.from_user.id); n = user_now(u)
    await message.answer(f"🕐 <b>{n:%Y-%m-%d %H:%M:%S}</b>\n🌍 {u.timezone}")

@dp.message(Command("clock"))
async def live_clock(message: Message, bot: Bot):
    if not await is_allowed(message.from_user.id): return
    uid = message.from_user.id
    old = LIVE_CLOCK_TASKS.pop(uid, None)
    if old: old.cancel()
    sent = await message.answer("🕐 ساعت زنده…")
    async def updater():
        try:
            while True:
                async with Session() as s:
                    u = await get_user(s, uid)
                    if not u: break
                    n = user_now(u); txt = f"🕐 <b>ساعت زنده</b>\n\n<code>{n:%H:%M:%S}</code>\n📅 {n:%Y-%m-%d}\n🌍 {u.timezone}"
                await bot.edit_message_text(chat_id=sent.chat.id, message_id=sent.message_id, text=txt)
                await asyncio.sleep(1)
        except (asyncio.CancelledError, Exception): pass
    LIVE_CLOCK_TASKS[uid] = asyncio.create_task(updater())

@dp.message(Command("settings"))
async def settings(message: Message):
    if not await is_allowed(message.from_user.id): return
    await message.answer("⚙️ تنظیمات سریع", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇮🇷 تهران", callback_data="tz:Asia/Tehran"), InlineKeyboardButton(text="🇩🇪 برلین", callback_data="tz:Europe/Berlin")],
        [InlineKeyboardButton(text="🇹🇷 استانبول", callback_data="tz:Europe/Istanbul"), InlineKeyboardButton(text="🔔 اعلان", callback_data="toggle_notifications")],
        [InlineKeyboardButton(text="⬅️ پنل", callback_data="main")],
    ]))

# ---------- Feature callbacks ----------
FEATURES = {
    "media_lock": "🔒 قفل رسانه\n\nحالت حذف خودکار رسانه‌های دریافتی در چت‌هایی که حساب شما اجازه مدیریت پیام دارد.",
    "comments": "💬 کامنت\n\nحالت کامنت در سطح پنل ذخیره می‌شود؛ اجرای واقعی آن به دسترسی همان چت نیاز دارد.",
    "animation": "✨ انیمیشن\n\nپنل با پیام‌های متحرک/مرحله‌ای طراحی شده؛ برای انیمیشن واقعی در پیام‌ها از GIF/استیکر خودت استفاده کن.",
    "users_menu": "👤 کاربران\n\nمدیریت کاربران از پنل مدیر انجام می‌شود. /panel",
    "public": "📌 عمومی\n\nقابلیت‌های عمومی: پروفایل، ساعت، اعلان، تنظیمات و دسترسی‌ها.",
    "actions": "🎭 اکشن\n\nری‌اکشن خودکار و پاسخ خودکار از بخش فرمان‌ها فعال می‌شوند: /reaction و /autoreply",
    "games": "🎮 بازی‌ها\n\n🎲 تاس: /dice\n🔮 فال: /fortune",
    "translate": "🌐 ترجمه\n\nبرای جست‌وجوی ترجمه، متن را بعد از /translate بنویس. این نسخه لینک Google Translate را می‌سازد.",
    "google": "🔎 گوگل\n\nبرای جست‌وجو: /google عبارت",
    "info": "ℹ️ اطلاعات\n\nنسخه پنل: Self Manager Pro\nپایگاه‌داده: فعال\nاحراز هویت سلف: فعال",
    "text_style": "✍️ استایل متن\n\nفرمان‌ها: /bold متن و /codeText متن",
    "message_manage": "✉️ مدیریت پیام\n\nابزارهای حذف/مدیریت پیام فقط روی پیام‌هایی اعمال می‌شوند که حساب شما اجازه مدیریتشان را دارد.",
    "enemies": "👹 دشمنان\n\nلیست دشمن/کاربر مسدودشده را فقط در سطح مدیریتی نگه‌دار؛ از مزاحمت یا ارسال انبوه استفاده نکن.",
    "report": "📣 گزارش\n\nبا /report متن، گزارش برای مدیر ارسال می‌شود.",
    "tools": "🛠 ابزار\n\n🔎 /google\n🌐 /translate\n🎲 /dice\n🔮 /fortune\n🆔 /id",
    "currency": "💰 ارزها\n\nبرای نرخ روز از /currency USD EUR استفاده کن؛ نتیجه به شکل لینک جست‌وجوی نرخ نمایش داده می‌شود.",
    "secretary": "🧠 منشی هوشمند\n\nبا /smart on و /smart off فعال/غیرفعال می‌شود و از پاسخ خودکار تنظیم‌شده استفاده می‌کند.",
    "tagall": "📢 تگ همه\n\nبرای جلوگیری از مزاحمت و اسپم، تگ انبوه خودکار در این نسخه فعال نمی‌شود. می‌توانی از Broadcast کنترل‌شده برای کاربران تأییدشده استفاده کنی.",
    "secret_text": "🔐 متن رمزی\n\nبا /secret متن، متن به Base64 تبدیل می‌شود؛ /unsecret برای برگرداندن آن.",
    "widgets": "🧩 ابزارک‌ها\n\nساعت زنده، پروفایل و تنظیمات در این پنل فعال هستند.",
    "backup": "📦 بکاپ‌گیری\n\nاز دیتابیس Railway/Volume خودت بکاپ بگیر. اطلاعات نشست سلف رمزنگاری شده است.",
    "button_settings": "🎨 تنظیم دکمه‌ها\n\nساختار پنل قابل تغییر است؛ در نسخه فعلی چیدمان امن و ثابت برای جلوگیری از خراب‌شدن Callbackها نگه داشته شده.",
}

@dp.callback_query(F.data == "main")
async def main_cb(call: CallbackQuery):
    await call.message.edit_text("🎛 <b>پنل اصلی</b>", reply_markup=main_kb()); await call.answer()

@dp.callback_query(F.data == "close")
async def close_cb(call: CallbackQuery):
    await call.message.delete(); await call.answer()

@dp.callback_query(F.data.in_(list(FEATURES.keys())))
async def feature_cb(call: CallbackQuery):
    key = call.data
    if key == "media_lock":
        await toggle_setting(call, "media_lock", "🔒 قفل رسانه")
        return
    if key == "comments":
        await toggle_setting(call, "comments_mode", "💬 حالت کامنت")
        return
    if key == "word_filter":
        await toggle_setting(call, "word_filter", "🚫 فیلتر کلمات")
        return
    if key == "name_lock":
        await toggle_name_lock(call); return
    if key == "smart":
        await toggle_setting(call, "smart_mode", "🤖 هوش/منشی هوشمند"); return
    if key == "profile":
        await call.message.edit_text(await profile_text(call.from_user.id), reply_markup=back_kb()); await call.answer(); return
    if key == "time_profile":
        async with Session() as s:
            u = await get_user(s, call.from_user.id); n = user_now(u)
        await call.message.edit_text(f"⏰ <b>زمان و پروفایل</b>\n\n🕐 {n:%H:%M:%S}\n📅 {n:%Y-%m-%d}\n🌍 {u.timezone}\n\n/profile برای جزئیات", reply_markup=back_kb()); await call.answer(); return
    await call.message.edit_text(FEATURES[key], reply_markup=back_kb()); await call.answer()

@dp.callback_query(F.data == "edit_profile")
async def edit_profile_cb(call: CallbackQuery):
    await call.message.edit_text("✏️ تغییر پروفایل\n\nدستورها:\n/setname نام جدید\n/setbio بیو جدید\n/setusername username\n\nاین قابلیت‌ها نیاز به ورود سلف دارند.", reply_markup=back_kb()); await call.answer()

@dp.callback_query(F.data == "reaction_menu")
async def reaction_menu(call: CallbackQuery):
    await call.message.edit_text("👍 ری‌اکشن\n\n/reaction 👍\n/reaction ❤️\n/reaction off", reply_markup=back_kb()); await call.answer()

async def toggle_setting(call, field, title):
    if not await is_allowed(call.from_user.id): await call.answer("دسترسی ندارید", show_alert=True); return
    async with Session() as s:
        u = await get_user(s, call.from_user.id); value = not bool(getattr(u, field)); setattr(u, field, value); await s.commit()
    await call.message.edit_text(f"{title}: {'🟢 روشن' if value else '🔴 خاموش'}", reply_markup=back_kb()); await call.answer()

async def toggle_name_lock(call):
    c = await self_client_for(call.from_user.id)
    if not c: await call.answer("ابتدا سلف را فعال کن", show_alert=True); return
    try:
        me = await c.get_me()
        async with Session() as s:
            u = await get_user(s, call.from_user.id); u.name_lock = not u.name_lock; u.locked_name = me.first_name if u.name_lock else None; value = u.name_lock; await s.commit()
        await c.disconnect(); await call.message.edit_text(f"🛡 حفاظت اسم: {'🟢 روشن' if value else '🔴 خاموش'}", reply_markup=back_kb()); await call.answer()
    except Exception:
        await c.disconnect(); await call.answer("خطا در دسترسی سلف", show_alert=True)

# ---------- Commands for useful features ----------
@dp.message(Command("id"))
async def id_cmd(message: Message): await message.answer(f"🆔 <code>{message.from_user.id}</code>")

@dp.message(Command("setname"))
async def setname(message: Message):
    c = await require_self(message)
    if not c: return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2: await message.answer("مثال: /setname نام جدید"); await c.disconnect(); return
    try:
        await c(UpdateProfileRequest(first_name=parts[1][:64]))
        await message.answer("✅ نام پروفایل تغییر کرد.")
    except Exception: await message.answer("❌ تغییر نام انجام نشد.")
    finally: await c.disconnect()

@dp.message(Command("setbio"))
async def setbio(message: Message):
    c = await require_self(message)
    if not c: return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2: await message.answer("مثال: /setbio متن بیو"); await c.disconnect(); return
    try:
        from telethon.tl.functions.account import UpdateProfileRequest
        await c(UpdateProfileRequest(about=parts[1][:70])); await message.answer("✅ بیو تغییر کرد.")
    except Exception: await message.answer("❌ تغییر بیو انجام نشد.")
    finally: await c.disconnect()

@dp.message(Command("setusername"))
async def setusername(message: Message):
    c = await require_self(message)
    if not c: return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2: await message.answer("مثال: /setusername MyUser"); await c.disconnect(); return
    try:
        from telethon.tl.functions.account import UpdateUsernameRequest
        await c(UpdateUsernameRequest(username=parts[1].lstrip("@")[:32])); await message.answer("✅ username تغییر کرد.")
    except Exception: await message.answer("❌ username در دسترس نیست یا معتبر نیست.")
    finally: await c.disconnect()

@dp.message(Command("autoreply"))
async def autoreply(message: Message):
    if not await is_allowed(message.from_user.id): return
    p = (message.text or "").split(maxsplit=1)
    if len(p) != 2: await message.answer("/autoreply متن پاسخ"); return
    async with Session() as s:
        u = await get_user(s, message.from_user.id); u.auto_reply = p[1][:4000]; await s.commit()
    await message.answer("✅ پاسخ خودکار فعال شد.")

@dp.message(Command("autoff"))
async def autoff(message: Message):
    async with Session() as s:
        u = await get_user(s, message.from_user.id)
        if u: u.auto_reply = None; await s.commit()
    await message.answer("✅ پاسخ خودکار خاموش شد.")

@dp.message(Command("reaction"))
async def reaction(message: Message):
    if not await is_allowed(message.from_user.id): return
    p = (message.text or "").split(maxsplit=1); value = p[1].strip() if len(p) == 2 else ""
    if value.lower() == "off":
        async with Session() as s: u = await get_user(s, message.from_user.id); u.auto_reaction = None; await s.commit()
        await message.answer("✅ خاموش شد"); return
    if value not in {"👍","❤️","🔥","😂","🎉","👏","😎"}: await message.answer("👍 ❤️ 🔥 😂 🎉 👏 😎"); return
    async with Session() as s: u = await get_user(s, message.from_user.id); u.auto_reaction = value; await s.commit()
    await message.answer(f"✅ ری‌اکشن {value}")

@dp.message(Command("smart"))
async def smart(message: Message):
    p = (message.text or "").split(maxsplit=1); value = p[1].lower() if len(p) == 2 else ""
    async with Session() as s:
        u = await get_user(s, message.from_user.id)
        if not u: return
        u.smart_mode = value == "on"; await s.commit()
    await message.answer("🤖 منشی هوشمند " + ("روشن شد." if value == "on" else "خاموش شد."))

@dp.message(Command("google"))
async def google(message: Message):
    p = (message.text or "").split(maxsplit=1)
    if len(p) != 2: await message.answer("مثال: /google اخبار تلگرام"); return
    await message.answer(f"🔎 <a href=\"https://www.google.com/search?q={quote_plus(p[1])}\">نتیجه جست‌وجوی گوگل</a>")

@dp.message(Command("translate"))
async def translate(message: Message):
    p = (message.text or "").split(maxsplit=1)
    if len(p) != 2: await message.answer("مثال: /translate hello world"); return
    q = quote_plus(p[1]); await message.answer(f"🌐 <a href=\"https://translate.google.com/?sl=auto&tl=fa&text={q}\">باز کردن ترجمه</a>")

@dp.message(Command("currency"))
async def currency(message: Message):
    p = (message.text or "").split()
    if len(p) != 3: await message.answer("مثال: /currency USD EUR"); return
    q = quote_plus(f"{p[1]} to {p[2]} exchange rate"); await message.answer(f"💰 <a href=\"https://www.google.com/search?q={q}\">نرخ {p[1]} به {p[2]}</a>")

@dp.message(Command("dice"))
async def dice(message: Message): await message.answer(f"🎲 عدد شما: <b>{random.randint(1,6)}</b>")

@dp.message(Command("fortune"))
async def fortune(message: Message):
    vals = ["امروز برای شروع یک کار خوب مناسب است.", "کمی صبر کن؛ نتیجه بهتر از عجله خواهد بود.", "یک خبر خوب می‌تواند نزدیک باشد.", "روی چیزی که کنترلش می‌کنی تمرکز کن."]
    await message.answer("🔮 " + random.choice(vals))

@dp.message(Command("bold"))
async def bold(message: Message):
    p = (message.text or "").split(maxsplit=1); await message.answer(f"<b>{escape(p[1])}</b>" if len(p)==2 else "مثال: /bold سلام")

@dp.message(Command("codeText"))
async def codetext(message: Message):
    p = (message.text or "").split(maxsplit=1); await message.answer(f"<code>{escape(p[1])}</code>" if len(p)==2 else "مثال: /codeText hello")

@dp.message(Command("secret"))
async def secret(message: Message):
    import base64
    p = (message.text or "").split(maxsplit=1)
    if len(p)!=2: await message.answer("مثال: /secret متن"); return
    await message.answer("🔐 <code>" + base64.b64encode(p[1].encode()).decode() + "</code>")

@dp.message(Command("unsecret"))
async def unsecret(message: Message):
    import base64
    p = (message.text or "").split(maxsplit=1)
    if len(p)!=2: await message.answer("مثال: /unsecret کد"); return
    try: await message.answer("🔓 " + escape(base64.b64decode(p[1]).decode()))
    except Exception: await message.answer("❌ کد معتبر نیست.")

@dp.message(Command("report"))
async def report(message: Message, bot: Bot):
    p = (message.text or "").split(maxsplit=1)
    if len(p)!=2: await message.answer("مثال: /report مشکل پنل"); return
    await bot.send_message(OWNER_ID, f"📣 <b>گزارش</b>\n👤 <code>{message.from_user.id}</code>\n{escape(p[1])}")
    await message.answer("✅ گزارش برای مدیر ارسال شد.")

# ---------- Admin ----------
@dp.message(Command("ban"))
async def ban(message: Message):
    if message.from_user.id != OWNER_ID: return
    p=(message.text or "").split(maxsplit=1)
    if len(p)!=2 or not p[1].isdigit(): await message.answer("/ban ID"); return
    async with Session() as s:
        u=await get_user(s,int(p[1]));
        if not u: await message.answer("کاربر پیدا نشد."); return
        u.banned=True; u.approved=False; await s.commit()
    await message.answer("🚫 مسدود شد.")

@dp.message(Command("unban"))
async def unban(message: Message):
    if message.from_user.id != OWNER_ID: return
    p=(message.text or "").split(maxsplit=1)
    if len(p)!=2 or not p[1].isdigit(): await message.answer("/unban ID"); return
    async with Session() as s:
        u=await get_user(s,int(p[1]));
        if not u: await message.answer("کاربر پیدا نشد."); return
        u.banned=False; await s.commit()
    await message.answer("✅ رفع مسدودی شد.")

@dp.callback_query(F.data.startswith("approve:"))
async def approve(call: CallbackQuery, bot: Bot):
    if call.from_user.id != OWNER_ID: return
    uid=int(call.data.split(":")[1])
    async with Session() as s:
        u=await get_user(s,uid)
        if u: u.approved=True; u.banned=False; await s.commit()
    await call.message.edit_reply_markup(reply_markup=None); await bot.send_message(uid,"🎉 درخواست شما تأیید شد. /panel"); await call.answer("تأیید شد")

@dp.callback_query(F.data.startswith("reject:"))
async def reject(call: CallbackQuery):
    if call.from_user.id != OWNER_ID: return
    uid=int(call.data.split(":")[1])
    async with Session() as s:
        u=await get_user(s,uid)
        if u: u.approved=False; await s.commit()
    await call.message.edit_reply_markup(reply_markup=None); await call.answer("رد شد")

@dp.callback_query(F.data == "users")
async def users(call: CallbackQuery):
    if call.from_user.id != OWNER_ID: return
    async with Session() as s:
        total=await s.scalar(select(func.count()).select_from(User)); active=await s.scalar(select(func.count()).select_from(User).where(User.approved==True,User.banned==False)); banned=await s.scalar(select(func.count()).select_from(User).where(User.banned==True)); pending=await s.scalar(select(func.count()).select_from(User).where(User.approved==False,User.banned==False))
    await call.message.answer(f"👥 کل: {total}\n🟢 فعال: {active}\n⏳ در انتظار: {pending}\n⛔ مسدود: {banned}"); await call.answer()

@dp.callback_query(F.data == "requests")
async def requests(call: CallbackQuery):
    if call.from_user.id != OWNER_ID: return
    async with Session() as s: rows=(await s.execute(select(User).where(User.approved==False,User.banned==False).limit(20))).scalars().all()
    if not rows: await call.message.answer("✅ درخواستی نیست.")
    for u in rows: await call.message.answer(f"👤 {escape(u.first_name or '-')} | @{escape(u.username or '-')}\n🆔 <code>{u.id}</code>", reply_markup=approval_kb(u.id))
    await call.answer()

@dp.callback_query(F.data == "stats")
async def stats(call: CallbackQuery):
    if call.from_user.id != OWNER_ID: return
    async with Session() as s:
        total=await s.scalar(select(func.count()).select_from(User)); msgs=await s.scalar(select(func.coalesce(func.sum(User.message_count),0))); selfusers=await s.scalar(select(func.count()).select_from(User).where(User.self_enabled==True))
    await call.message.answer(f"📊 کاربران: {total}\n💬 پیام‌ها: {msgs}\n🔐 سلف‌های فعال: {selfusers}"); await call.answer()

@dp.callback_query(F.data == "broadcast_help")
async def broadcast_help(call: CallbackQuery):
    if call.from_user.id == OWNER_ID: await call.message.answer("📢 برای Broadcast روی پیام موردنظر ریپلای کن و /broadcast بزن.")
    await call.answer()

@dp.message(Command("broadcast"))
async def broadcast(message: Message, bot: Bot):
    if message.from_user.id != OWNER_ID or not message.reply_to_message: return
    async with Session() as s: users=(await s.execute(select(User).where(User.approved==True,User.banned==False))).scalars().all()
    ok=failed=0
    status=await message.answer(f"📢 ارسال کنترل‌شده برای {len(users)} کاربر…")
    for u in users:
        try: await bot.copy_message(chat_id=u.id,from_chat_id=message.chat.id,message_id=message.reply_to_message.message_id); ok+=1
        except Exception: failed+=1
        await asyncio.sleep(0.08)
    await status.edit_text(f"✅ تمام شد\n📨 موفق: {ok}\n❌ ناموفق: {failed}")

@dp.callback_query(F.data == "access_help")
async def access_help(call: CallbackQuery):
    if call.from_user.id==OWNER_ID: await call.message.answer("/ban ID و /unban ID")
    await call.answer()
@dp.callback_query(F.data == "liveclock_help")
async def liveclock_help(call: CallbackQuery):
    if call.from_user.id==OWNER_ID: await call.message.answer("/clock")
    await call.answer()

@dp.callback_query(F.data == "tz:Asia/Tehran")
@dp.callback_query(F.data == "tz:Europe/Berlin")
@dp.callback_query(F.data == "tz:Europe/Istanbul")
async def tz_cb(call: CallbackQuery):
    if not await is_allowed(call.from_user.id): return
    tz=call.data.split(":",1)[1]
    async with Session() as s: u=await get_user(s,call.from_user.id); u.timezone=tz; await s.commit()
    await call.answer("منطقه زمانی ذخیره شد")

@dp.callback_query(F.data == "toggle_notifications")
async def notif_cb(call: CallbackQuery):
    async with Session() as s: u=await get_user(s,call.from_user.id); u.notifications=not u.notifications; v=u.notifications; await s.commit()
    await call.answer("اعلان‌ها " + ("روشن 🔔" if v else "خاموش 🔕"))

# ---------- Hooks ----------
@dp.message()
async def fallback(message: Message):
    uid=message.from_user.id
    if not await is_allowed(uid): return
    await touch_user(message)
    async with Session() as s:
        u=await get_user(s,uid); reply=u.auto_reply if u else None; reaction=u.auto_reaction if u else None; smart_mode=u.smart_mode if u else False; media_lock=u.media_lock if u else False
    if media_lock and message.content_type in {"photo","video","document","audio","voice","animation","sticker"}:
        try: await message.delete()
        except Exception: pass
        return
    if reply and not (message.text or "").startswith("/"): await message.answer(reply)
    elif smart_mode and not (message.text or "").startswith("/"): await message.answer("🤖 پیام شما دریافت شد.")
    if reaction:
        try: await message.bot.set_message_reaction(chat_id=message.chat.id,message_id=message.message_id,reaction=[ReactionTypeEmoji(emoji=reaction)])
        except Exception: pass

async def main():
    await ensure_schema()
    bot=Bot(TOKEN,default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    log.info("Bot starting")
    await dp.start_polling(bot)

if __name__ == "__main__": asyncio.run(main())
