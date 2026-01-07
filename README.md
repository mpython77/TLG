# Telegram Multi-Account Message Forwarder

Web-ilova orqali ko'p Telegram akkauntlarni boshqarish va xabarlarni avtomatik yuborish tizimi.

## 🚀 Asosiy Xususiyatlar

- ✅ **PostgreSQL Persistence** - Barcha ma'lumotlar va sessionlar PostgreSQL bazada saqlanadi
- ✅ **Session Management** - Redeploy qilganda qayta kod talab qilinmaydi
- ✅ **Multi-Account Support** - Ko'p akkauntlarni bittadan boshqarish
- ✅ **Message Forwarding** - Kanallar o'rtasida xabar yuborish
- ✅ **Scheduling** - Vaqt bo'yicha xabar yuborish
- ✅ **Real-time Monitoring** - Live status va loglar
- ✅ **2FA Support** - Ikki faktorli autentifikatsiya

## 📋 Talablar

- Python 3.10+
- PostgreSQL (production) yoki SQLite (local test)
- Telegram API credentials (api_id, api_hash)

## 🔧 O'rnatish

### 1. Repository ni clone qiling

```bash
git clone <repository-url>
cd my
```

### 2. Virtual environment yarating

```bash
python3 -m venv venv
source venv/bin/activate  # Linux/Mac
# yoki
venv\Scripts\activate  # Windows
```

### 3. Dependencies o'rnating

```bash
pip install -r requirements.txt
```

### 4. Environment sozlamalari

`.env` faylini yarating (yoki `.env.example` dan nusxa oling):

```bash
cp .env.example .env
```

**Local test uchun (SQLite):**
```env
DATABASE_URL=sqlite:///telegram_forwarder.db
SECRET_KEY=your-secret-key-here
```

**Production uchun (PostgreSQL):**
```env
DATABASE_URL=postgresql://username:password@hostname:5432/database_name
SECRET_KEY=your-production-secret-key
```

### 5. Login credentials

`password.txt` faylida login ma'lumotlarini tekshiring:
```
Login: test
Password: 123
```

## 🚀 Ishlatish

### Local Test (SQLite)

```bash
python app.py
```

Keyin brauzerda: `http://localhost:5000`

### Railway Deploy

1. Railway ga login qiling:
```bash
railway login
```

2. Railway project yarating:
```bash
railway init
```

3. PostgreSQL qo'shing:
```bash
railway add
# "PostgreSQL" ni tanlang
```

4. Deploy qiling:
```bash
railway up
```

**MUHIM:** Railway avtomatik ravishda `DATABASE_URL` ni sozlaydi. Siz hech narsa sozlamasdan ishlaydi!

### Heroku Deploy

1. Heroku ga login qiling:
```bash
heroku login
```

2. App yarating:
```bash
heroku create your-app-name
```

3. PostgreSQL qo'shing:
```bash
heroku addons:create heroku-postgresql:mini
```

4. Deploy qiling:
```bash
git push heroku main
```

## 📖 Qanday Ishlaydi

### 1️⃣ **Birinchi Marta Connect Qilish**

Connect All tugmasini bosganda:

1. ✅ Database'dan session_string tekshiriladi
2. ❌ Session yo'q bo'lsa → **Kod so'raladi**
3. ✅ Kod kiritiladi → Session saqlanadi
4. ✅ Keyingi safar kod kerak bo'lmaydi!

**Logda ko'rasiz:**
```
[12:34:56] [+998901234567] ✗ No session in database - will request authorization code
[12:34:57] [+998901234567] Authorization required - sending code
[12:35:10] [+998901234567] Connected successfully: John
[12:35:11] [+998901234567] Session saved to database
```

### 2️⃣ **Keyingi Safar Connect Qilish** (Redeploy keyin)

1. ✅ Database'dan session_string yuklanadi
2. ✅ Session bilan avtomatik kiriladi
3. ✅ **Kod kerak emas!**

**Logda ko'rasiz:**
```
[12:40:00] [+998901234567] ✓ Session found in database - attempting to connect with saved session
[12:40:02] [+998901234567] ✓ Connected with saved session: John (no code required)
```

### 3️⃣ **Session Muddati O'tgan Bo'lsa**

1. ✅ Database'dan session olinadi
2. ⚠️ Session yaroqsiz
3. ✅ Yangi kod so'raladi

**Logda ko'rasiz:**
```
[12:45:00] [+998901234567] ✓ Session found in database - attempting to connect with saved session
[12:45:02] [+998901234567] ⚠ Saved session is invalid or expired - requesting new authorization code
```

## 🗄️ Database Schema

### `accounts` Jadvali

| Ustun | Tur | Tavsif |
|-------|-----|--------|
| `id` | Integer | Primary key |
| `name` | String | Akkaunt nomi |
| `api_id` | String | Telegram API ID |
| `api_hash` | String | Telegram API Hash |
| `phone` | String | Telefon raqami (unique) |
| `source_channel` | String | Manba kanal ID |
| `target_channels` | JSON | Maqsad kanallar ro'yxati |
| `status` | String | Holat (Added/Connected) |
| `session_file` | String | Session fayl nomi |
| **`session_string`** | **Text** | **Session string (persistent!)** |
| `created_at` | DateTime | Yaratilgan vaqt |
| `updated_at` | DateTime | Yangilangan vaqt |

### `scheduled_posts` Jadvali

| Ustun | Tur | Tavsif |
|-------|-----|--------|
| `id` | Integer | Primary key |
| `post` | String | Message ID |
| `target_datetime` | DateTime | Yuborish vaqti |
| `channels` | JSON | Kanallar ro'yxati |
| `status` | String | Holat (Pending/Sent) |
| `created_at` | DateTime | Yaratilgan vaqt |

## 🔐 Xavfsizlik

**⚠️ MUHIM XAVFSIZLIK MASALALARI:**

1. **API Credentials:** `password.txt` da hardcoded parol. Production uchun o'zgartiring!
2. **Session Strings:** Ma'lumotlar bazasida shifrsiz saqlanadi
3. **SECRET_KEY:** `.env` da o'zgartiring!

**Yaxshilash Tavsiyalari:**
- Environment variable orqali login/password
- Session stringlarni shifrlash
- HTTPS majburiy qilish
- Rate limiting qo'shish

## 📊 API Endpoints

| Endpoint | Method | Tavsif |
|----------|--------|--------|
| `/login` | POST | Login qilish |
| `/logout` | GET | Logout qilish |
| `/api/accounts` | GET | Barcha akkauntlar |
| `/api/accounts` | POST | Akkaunt qo'shish |
| `/api/accounts/<phone>` | DELETE | Akkaunt o'chirish |
| `/api/connect` | POST | Barcha akkauntlarni ulash |
| `/api/disconnect` | POST | Barcha ulanishlarni uzish |
| `/api/auth/code` | POST | Autentifikatsiya kodi |
| `/api/auth/password` | POST | 2FA paroli |
| `/api/monitor/start` | POST | Monitoring boshlash |
| `/api/monitor/stop` | POST | Monitoring to'xtatish |
| `/api/scheduled` | GET/POST | Rejalashtirilgan postlar |
| `/api/scheduler/start` | POST | Scheduler boshlash |

## 🔄 Migration

Eski JSON fayldan PostgreSQL ga ko'chirish avtomatik amalga oshadi:

1. Ilova ishga tushganda `accounts_config.json` tekshiriladi
2. Agar topilsa va database bo'sh bo'lsa, migration boshlanadi
3. JSON fayl `.backup` ga qayta nomlanadi

## 🐛 Debugging

### Database Connection Tekshirish

```python
# models.py da echo=True qiling
self.engine = create_engine(database_url, echo=True)
```

### Telegram Client Logs

```python
# app.py da logging.WARNING ni logging.INFO ga o'zgartiring
level=logging.INFO
```

## 📝 Development

### Local Development

```bash
# SQLite bilan test qilish
DATABASE_URL=sqlite:///telegram_forwarder.db python app.py

# PostgreSQL bilan test qilish (Docker)
docker run --name postgres -e POSTGRES_PASSWORD=test -p 5432:5432 -d postgres
DATABASE_URL=postgresql://postgres:test@localhost:5432/postgres python app.py
```

### Code Structure

```
my/
├── app.py              # Asosiy Flask ilova
├── models.py           # Database modellari va migratsiya
├── templates/
│   ├── index.html      # Dashboard UI
│   └── login.html      # Login sahifasi
├── requirements.txt    # Python dependencies
├── Procfile           # Railway/Heroku config
├── railway.json       # Railway sozlamalari
├── .env               # Environment variables (git ignore)
├── .env.example       # Environment namunasi
└── password.txt       # Login credentials

```

## 🤝 Contributing

Pull request'lar qabul qilinadi. Katta o'zgarishlar uchun avval issue oching.

## 📜 License

MIT License

## 💡 Muammolarni Hal Qilish

### Session Saqlanmayapti?

1. DATABASE_URL to'g'ri sozlanganini tekshiring
2. PostgreSQL ulanishini tekshiring:
   ```bash
   psql $DATABASE_URL -c "SELECT * FROM accounts;"
   ```

### Redeploy keyin kod so'rayapti?

1. Session string saqlanganini tekshiring:
   ```sql
   SELECT phone, session_string IS NOT NULL as has_session FROM accounts;
   ```
2. Logda "Session saved to database" xabarini qidiring

### Database Migration Ishlamayapti?

1. Manual migration:
   ```python
   from models import DatabaseManager
   db = DatabaseManager()
   db.run_migrations()
   ```

## 📞 Yordam

Savollar yoki muammolar bo'lsa, issue oching yoki pull request yuboring!

---

**Ishlab chiquvchi:** [Your Name]
**Yaratilgan:** 2026-01-07
**Python:** 3.10+
**Framework:** Flask 2.3.3 + Telethon 1.40.0
