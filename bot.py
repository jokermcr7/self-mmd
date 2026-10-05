import asyncio
import logging
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from sqlalchemy import Boolean, DateTime, Integer, String, Text, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

logging.basicConfig(level=logging.INFO)
TOKEN = os.environ["BOT_TOKEN"]
OWNER_ID = int(os.environ["OWNER_ID"])
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./bot.db")
DEFAULT_TZ = os.getenv("DEFAULT_TIMEZONE", "Europe/Berlin")

# Render's PostgreSQL URL may start with postgres://; asyncpg expects postgresql+asyncpg://.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
dp = Dispatcher()

def owner_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 کاربران", callback_data="users"),
         InlineKeyboardButton(text="📝 درخواست‌ها", callback_data="requests")],
        [InlineKeyboardButton(text="📊 آمار", callback_data="stats")],
        [InlineKeyboardButton(text="📢 پیام همگانی", callback_data="broadcast_help")],
    ])

def approval_kb(uid: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ تأیید", callback_data=f"approve:{uid}"),
         InlineKeyboardButton(text="❌ رد", callback_data=f"reject:{uid}")],
    ])

async def get_user(session, uid):
    return (await session.execute(select(User).where(User.id == uid))).scalar_one_or_none()

async def is_allowed(uid: int):
    if uid == OWNER_ID:
        return True
    async with Session() as s:
        u = await get_user(s, uid)
        return bool(u and u.approved and not u.banned)

@dp.message(CommandStart())
async def start(message: Message, bot: Bot):
    uid = message.from_user.id
    async with Session() as s:
        u = await get_user(s, uid)
        if not u:
            u = User(id=uid, username=message.from_user.username,
                     first_name=message.from_user.first_name)
            s.add(u)
            await s.commit()
            await bot.send_message(
                OWNER_ID,
                f"🔔 درخواست دسترسی جدید\n\n"
                f"👤 {message.from_user.full_name}\n"
                f"🔗 @{message.from_user.username or 'بدون username'}\n"
                f"🆔 <code>{uid}</code>",
                reply_markup=approval_kb(uid)
            )
        elif u.banned:
            await message.answer("⛔ دسترسی شما مسدود شده است.")
            return
        elif not u.approved:
            await message.answer("⏳ درخواست شما قبلاً ارسال شده؛ منتظر تأیید مدیر باشید.")
            return
    await message.answer("✅ دسترسی شما فعال است. /help را بزنید.")

@dp.message(Command("help"))
async def help_cmd(message: Message):
    if not await is_allowed(message.from_user.id):
        await message.answer("⏳ ابتدا باید درخواست دسترسی شما تأیید شود.")
        return
    await message.answer(
        "🧰 دستورات:\n"
        "/panel - پنل مدیریت (Owner)\n"
        "/id - شناسه شما\n"
        "/time - ساعت فعلی\n"
        "/settings - تنظیمات شخصی\n"
        "/help - راهنما"
    )

@dp.message(Command("id"))
async def id_cmd(message: Message):
    await message.answer(f"🆔 <code>{message.from_user.id}</code>")

@dp.message(Command("panel"))
async def panel(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    await message.answer("👑 پنل مدیریت مرکزی", reply_markup=owner_kb())

@dp.message(Command("time"))
async def time_cmd(message: Message):
    if not await is_allowed(message.from_user.id):
        await message.answer("⏳ دسترسی شما هنوز تأیید نشده است.")
        return
    async with Session() as s:
        u = await get_user(s, message.from_user.id)
        tz = u.timezone if u else DEFAULT_TZ
    try:
        now = datetime.now(ZoneInfo(tz))
        await message.answer(f"🕐 {now:%Y-%m-%d %H:%M:%S}\n🌍 {tz}")
    except Exception:
        await message.answer(f"🕐 ساعت: {datetime.now():%H:%M:%S}\n🌍 {DEFAULT_TZ}")

@dp.message(Command("settings"))
async def settings(message: Message):
    if not await is_allowed(message.from_user.id):
        await message.answer("⏳ ابتدا دسترسی بگیرید.")
        return
    await message.answer(
        "⚙️ تنظیمات از این نسخه آماده است و می‌توانیم در ادامه گزینه‌های بیشتری اضافه کنیم:\n"
        "• Timezone\n• Auto Reply\n• Auto Reaction\n• فیلترها\n• تنظیمات چت"
    )

@dp.callback_query(F.data.startswith("approve:"))
async def approve(call: CallbackQuery, bot: Bot):
    if call.from_user.id != OWNER_ID:
        await call.answer("دسترسی ندارید.", show_alert=True); return
    uid = int(call.data.split(":")[1])
    async with Session() as s:
        u = await get_user(s, uid)
        if not u:
            await call.answer("کاربر پیدا نشد.", show_alert=True); return
        u.approved = True
        u.banned = False
        await s.commit()
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer(f"✅ کاربر <code>{uid}</code> تأیید شد.")
    try:
        await bot.send_message(uid, "🎉 درخواست شما تأیید شد. اکنون می‌توانید از بات استفاده کنید.")
    except Exception:
        pass
    await call.answer("تأیید شد.")

@dp.callback_query(F.data.startswith("reject:"))
async def reject(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        await call.answer("دسترسی ندارید.", show_alert=True); return
    uid = int(call.data.split(":")[1])
    async with Session() as s:
        u = await get_user(s, uid)
        if u:
            u.approved = False
            await s.commit()
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer(f"❌ درخواست <code>{uid}</code> رد شد.")
    await call.answer("رد شد.")

@dp.callback_query(F.data == "users")
async def users(call: CallbackQuery):
    if call.from_user.id != OWNER_ID: return
    async with Session() as s:
        rows = (await s.execute(select(User))).scalars().all()
    active = sum(1 for x in rows if x.approved and not x.banned)
    banned = sum(1 for x in rows if x.banned)
    pending = sum(1 for x in rows if not x.approved and not x.banned)
    await call.message.answer(f"👥 کاربران: {len(rows)}\n🟢 فعال: {active}\n⏳ در انتظار: {pending}\n⛔ مسدود: {banned}")
    await call.answer()

@dp.callback_query(F.data == "requests")
async def requests(call: CallbackQuery):
    if call.from_user.id != OWNER_ID: return
    async with Session() as s:
        rows = (await s.execute(select(User).where(User.approved == False, User.banned == False))).scalars().all()
    if not rows:
        await call.message.answer("✅ درخواست جدیدی نیست.")
    else:
        for u in rows[:20]:
            await call.message.answer(
                f"👤 {u.first_name or '-'} | @{u.username or '-'}\n🆔 <code>{u.id}</code>",
                reply_markup=approval_kb(u.id)
            )
    await call.answer()

@dp.callback_query(F.data == "stats")
async def stats(call: CallbackQuery):
    if call.from_user.id != OWNER_ID: return
    async with Session() as s:
        rows = (await s.execute(select(User))).scalars().all()
    await call.message.answer(f"📊 آمار\nکل ثبت‌نام: {len(rows)}\nفعال: {sum(u.approved and not u.banned for u in rows)}")
    await call.answer()

@dp.callback_query(F.data == "broadcast_help")
async def broadcast_help(call: CallbackQuery):
    if call.from_user.id == OWNER_ID:
        await call.message.answer("برای Broadcast امن، یک پیام را با /broadcast ریپلای کنید. این قابلیت را می‌توان بر اساس نیاز فعال کرد.")
    await call.answer()

@dp.message()
async def fallback(message: Message):
    if not await is_allowed(message.from_user.id):
        return
    # Auto-reply is intentionally opt-in and per-user.
    async with Session() as s:
        u = await get_user(s, message.from_user.id)
        reply = u.auto_reply if u else None
    if reply:
        await message.answer(reply)

async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
