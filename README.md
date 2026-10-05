# Telegram Self Manager Pro — Railway

نسخه ارتقایافته پنل با ظاهر و منوی نزدیک به نمونه ارسالی. این نسخه برای قابلیت‌های مدیریت شخصی و کنترل‌شده طراحی شده و قابلیت ارسال مزاحم/اسپم انبوه ندارد.

## قابلیت‌ها
- پنل دکمه‌ای چندبخشی: زمان و پروفایل، انیمیشن، کاربران، قفل رسانه، کامنت، عمومی، اکشن، بازی‌ها، ترجمه، گوگل، اطلاعات، پروفایل، استایل متن، مدیریت پیام، ری‌اکشن، دشمنان/لیست مسدود، تغییر پروفایل، فیلتر کلمات، حفاظت اسم، منشی هوشمند، گزارش، ابزار، ارز، متن رمزی، ابزارک، بکاپ و تنظیم دکمه‌ها.
- احراز هویت سلف با شماره + کد تلگرام + 2FA اختیاری.
- Session با Fernet رمزنگاری می‌شود.
- ساعت زنده ثانیه‌ای و Timezone.
- تغییر نام، Bio و Username اکانت از طریق Telethon بعد از ورود.
- پاسخ خودکار، ری‌اکشن خودکار، حالت منشی، فیلتر رسانه در سطح Bot.
- مدیریت کاربران، تأیید/رد، Ban/Unban، آمار و Broadcast کنترل‌شده برای کاربران تأییدشده.
- دیتابیس سازگار با نسخه قبلی؛ ستون‌های جدید خودکار اضافه می‌شوند.

## Railway Environment Variables
```text
BOT_TOKEN=...
OWNER_ID=...
DATABASE_URL=...
DEFAULT_TIMEZONE=Europe/Berlin
TG_API_ID=...
TG_API_HASH=...
SESSION_SECRET=...
```

`SESSION_SECRET` باید یک Fernet key معتبر باشد و بعد از اولین ورود آن را تغییر ندهید.

## اجرای ورود سلف
```text
/login +491234567890
/code 12345
/password YOUR_2FA_PASSWORD   # فقط اگر 2FA درخواست شد
/selfstatus
/selfoff
```

## چند فرمان
```text
/panel
/profile
/time
/clock
/autoreply متن
/autoff
/reaction 👍
/reaction off
/smart on
/smart off
/setname نام
/setbio متن بیو
/setusername username
/google عبارت
/translate متن
/currency USD EUR
/dice
/fortune
/secret متن
/unsecret کد
/report متن
```

## نکته امنیتی
کد ورود، رمز 2FA و Session را در لاگ یا چت عمومی منتشر نکنید. قابلیت‌های اسپم، ارسال انبوه مزاحم و تگ‌کردن انبوه خودکار عمداً در این نسخه پیاده‌سازی نشده‌اند.
