# Telegram Manager Bot

بات مدیریتی Telegram با Python + aiogram + PostgreSQL/SQLite.

## امکانات

- سیستم درخواست دسترسی و تأیید توسط Owner
- پنل Owner داخل تلگرام
- مدیریت وضعیت کاربران
- آمار کاربران
- Timezone و نمایش ساعت
- Auto Reply قابل توسعه
- ساختار آماده Docker و Render
- PostgreSQL در Render
- نگهداری Token فقط در Environment Variables

> این پروژه برای مدیریت قانونی بات و کاربران خودش طراحی شده است. برای اسپم انبوه، Flood یا دورزدن محدودیت‌های تلگرام استفاده نشود.

## نصب محلی

Python 3.12+:

```bash
pip install -r requirements.txt
```

متغیرها را تنظیم کنید:

```bash
BOT_TOKEN=توکن_بات
OWNER_ID=آیدی_عددی_تلگرام_شما
DEFAULT_TIMEZONE=Europe/Berlin
DATABASE_URL=sqlite+aiosqlite:///./bot.db
```

سپس:

```bash
python bot.py
```

## Deploy روی Render

1. این پوشه را در یک repository جدید GitHub قرار دهید.
2. در Render گزینه New > Blueprint را انتخاب کنید.
3. repository را وصل کنید.
4. Render فایل `render.yaml` را می‌خواند.
5. مقدار `BOT_TOKEN` و `OWNER_ID` را در Environment وارد کنید.
6. Deploy را بزنید.

### گرفتن BOT_TOKEN

در Telegram به BotFather بروید، Bot بسازید و Token را فقط در Environment Variables قرار دهید.

### گرفتن OWNER_ID

به بات `/id` بفرستید و عدد نمایش‌داده‌شده را در `OWNER_ID` قرار دهید.

## ساختار

```text
telegram-manager-bot/
├── bot.py
├── requirements.txt
├── Dockerfile
├── render.yaml
├── .env.example
├── .gitignore
└── README.md
```

## توسعه بعدی

ماژول‌های پیشنهادی:
- تنظیم Reaction برای چت‌های مجاز
- فیلتر و Moderation
- مدیریت چند چت
- دستورات سفارشی
- پنل Web Admin
- Backup
- سیستم نقش‌های Owner/Admin
- تنظیمات Timezone برای هر کاربر


## Deploy روی Railway

برای Railway:
1. Repository را به سرویس `telegram-manager-bot` وصل کنید.
2. در Variables همین سرویس این موارد را قرار دهید:
   - `BOT_TOKEN`
   - `OWNER_ID`
   - `DATABASE_URL` (اگر PostgreSQL را به سرویس وصل کرده‌اید، Railway معمولاً این مقدار را در اختیار سرویس می‌گذارد.)
3. بعد از تغییر `requirements.txt` یک Redeploy بزنید.
4. در Deploy Logs باید برنامه بدون Traceback بالا بیاید.


## Railway: DATABASE_URL

اگر Deploy روی Railway خطای `Could not parse SQLAlchemy URL` داد، مقدار `DATABASE_URL`
در Variables سرویس اشتباه یا ناقص است. برای تست سریع می‌توانید آن Variable را حذف کنید؛
کد در این حالت با SQLite بالا می‌آید. برای دیتابیس دائمی، PostgreSQL Railway را به سرویس
وصل کنید و `DATABASE_URL` معتبر آن را به سرویس بدهید.
