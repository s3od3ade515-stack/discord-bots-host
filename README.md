# 🤖 استضافة بوتات ديسكورد 24/7

مشغّل واحد يشغّل **كل البوتات** اللي داخل مجلد `bots/` ويعيد تشغيلها تلقائيًا إذا وقفت، مع صفحة ويب لحالة البوتات.

```
├── main.py            ← المشغّل (يشغّل كل البوتات + صفحة الحالة)
├── Dockerfile         ← يثبّت ffmpeg والمكتبات
├── requirements.txt
└── bots/
    └── music_bot/     ← كل بوت في مجلد خاص فيه bot.py
        └── bot.py
```

## إضافة بوت جديد
1. سوّ مجلد جديد داخل `bots/` مثل `bots/my_bot/`
2. حط ملف البوت باسم `bot.py` (أو `main.py`)
3. أي مكتبات إضافية ضيفها في `requirements.txt`
4. ارفع التغييرات على GitHub — المنصة تعيد النشر وتشغّله مع البقية

## التوكنات (مهم 🔒)
**لا ترفع التوكن على GitHub أبدًا.** حطه في *Environment Variables* في الاستضافة:

| المتغير | الوصف |
|---|---|
| `MUSIC_BOT_TOKEN` | توكن بوت الموسيقى |
| `STATUS_KEY` | (اختياري) كلمة سر لعرض سجلات البوتات في `/?key=...` |
| `YTDLP_COOKIES` | (اختياري) مسار ملف كوكيز يوتيوب |
| `AUTO_UPDATE_YTDLP` | `1` (افتراضي) يحدّث yt-dlp عند كل تشغيل |

لو عندك أكثر من بوت، كل بوت يقرأ متغير باسمه الخاص (أو ملف `.env` داخل مجلده — وهو مستثنى من Git).

## النشر

### 1) رفع على GitHub
```bash
git init
git add .
git commit -m "discord bots host"
git branch -M main
git remote add origin https://github.com/USERNAME/REPO.git
git push -u origin main
```

### 2) الاستضافة (اختر واحدة)
**Railway / Koyeb / Fly.io / Render** — كلها تدعم Docker:
1. New Project ← Deploy from GitHub ← اختر الريبو
2. تلقائيًا تكتشف `Dockerfile`
3. أضف Environment Variables (`MUSIC_BOT_TOKEN`)
4. Deploy ✅ — افتح رابط المشروع تشوف صفحة الحالة

**VPS (الأضمن للبوتات الصوتية):**
```bash
git clone https://github.com/USERNAME/REPO.git && cd REPO
docker build -t bots .
docker run -d --restart unless-stopped --name bots \
  -e MUSIC_BOT_TOKEN=xxxxx -p 8080:8080 bots
```

## ⚠️ ملاحظات مهمة
- **الباقات المجانية**: بعضها (مثل Render المجاني) ينام بعد فترة خمول فيوقف البوت. للتشغيل الفعلي 24/7 استخدم باقة مدفوعة صغيرة أو VPS.
- **يوتيوب وسيرفرات الاستضافة**: يوتيوب كثير يحظر IP شركات الاستضافة ويطلع خطأ "Sign in to confirm you're not a bot". الحل: صدّر كوكيز حساب يوتيوب (يفضّل حساب ثانوي) بصيغة `cookies.txt`، وارفعه كـ Secret File في المنصة، وحط `YTDLP_COOKIES` على مساره. على VPS منزلي/عادي غالبًا ما تحتاجه.
- بوت الموسيقى يحتاج صلاحيات Connect و Speak، وما يحتاج Privileged Intents.
- إذا وقف التشغيل من يوتيوب، أعد النشر (Redeploy) عشان يتحدّث yt-dlp.
