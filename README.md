# سوبر رامي 🦸‍♂️

مساعد رامي الشخصي للأسهم الأمريكية والسعودية، يشتغل كـ skill داخل Claude.

| القدرة | السكربت |
|---|---|
| نسخ محافظ كبار المستثمرين من تقارير 13F (Buffett، Burry، Ackman…) | `copycat.py` |
| تحليل فني لأي سهم أمريكي أو سعودي | `analyze.py` |
| تقسيم الميزانية بحد أعلى لكل سهم وكاش احتياطي | `budget.py` |
| قواعد مخاطر تفحص كل أمر | `risk.py` |
| محفظة وهمية بالدولار والريال | `paper.py` |
| استراتيجية SPX Smart Rally: إشارات وباك تست | `strategy.py` |

- بايثون 3.9 أو أحدث، بدون مكتبات إضافية.
- ما فيه أي تداول حقيقي. كل أمر يحتاج `--yes`، ويمر على قواعد المخاطر قبل التنفيذ.

## التثبيت

داخل Claude Code:

```bash
git clone <رابط المستودع> super-rami
```

```bash
mkdir -p ~/.claude/skills && cp -r super-rami/skills/super-rami ~/.claude/skills/
```

بعدها قل لـ Claude: "سوبر رامي، حلل لي سهم أرامكو".

## أول تشغيل

```bash
python3 ~/.claude/skills/super-rami/scripts/setup.py --sec-user-agent "Rami your@email.com"
```

```bash
python3 ~/.claude/skills/super-rami/scripts/paper.py init --usd 10000 --sar 37500
```

## الإعدادات

في `~/super-rami-data/config.json`:

| الإعداد | الافتراضي | المعنى |
|---|---|---|
| `cash_buffer_pct` | 10 | نسبة الكاش الاحتياطي |
| `max_position_pct` | 20 | أقصى وزن لسهم واحد |
| `max_trade_pct` | 25 | أقصى حجم لصفقة واحدة |
| `min_price` | 1 | يرفض الأسهم الرخيصة جداً |
| `spx_strategy.*` | — | إعدادات الاستراتيجية (`strategy.py rules`) |

## التنفيذ التلقائي مع Interactive Brokers

`autotrader.py` يراقب SPX دقيقة بدقيقة. إذا طلعت إشارة، يشتري عقد SPXW 0DTE من نوع OTM بأمر bracket فيه دخول ووقف خسارة وجني ربح.

### خطوات التجهيز

1. افتح حساب في [Interactive Brokers](https://www.interactivebrokers.com)، وفعّل صلاحية تداول الخيارات (Options).
2. اشترك في بيانات مؤشرات CBOE عشان تحصل أسعار SPX لحظية.
3. حمّل **IB Gateway** أو **TWS**، وسجّل دخول على **الحساب التجريبي (Paper)**.
4. فعّل الـ API من: Configure ← API ← Settings ← Enable ActiveX and Socket Clients. المنفذ 7497 لـ TWS أو 4002 لـ Gateway.
5. ثبّت المكتبة:

```bash
pip install ib_async
```

6. جرب المحاكاة أول، بعدها شغّل على الحساب التجريبي:

```bash
python3 ~/.claude/skills/super-rami/scripts/autotrader.py sim --csv spx_1m.csv
```

```bash
python3 ~/.claude/skills/super-rami/scripts/autotrader.py ibkr
```

### حدود الأمان (`spx_auto` في config.json)

| الإعداد | الافتراضي |
|---|---|
| `mode` | `paper`: يرفض الاتصال إذا الحساب حقيقي |
| `qty` | 1 عقد |
| `account_usd` | 1,000$ (رأس المال) |
| `max_premium_pct` | 20% = 200$ كحد أعلى لتكلفة الصفقة |
| `stop_loss_pct` / `take_profit_pct` | 50% / 100% من سعر الدخول |
| `max_trades_per_day` | 3 |
| `max_daily_loss_pct` | 10% = 100$، بعدها يوقف الدخول لنهاية اليوم |
| `extra_otm_pts` | 15: إذا العقد أغلى من الحد، يبعد الـ strike لين 15 نقطة زيادة |
| `max_spread_pct` | 15% |
| `start` / `last_entry` / `flatten` | 09:45 / 15:30 / 15:50 بتوقيت نيويورك |

- **إيقاف فوري:** `autotrader.py stop` يقفل كل الصفقات ويوقف البوت.
- **التداول الحقيقي:** يحتاج تغيّر `mode` إلى `live` بنفسك، وتكتب في الطرفية `export SUPER_RAMI_LIVE=I_UNDERSTAND`.

## بوت العملات الرقمية (باينانس)

```bash
python3 ~/.claude/skills/super-rami/scripts/crypto_bot.py backtest --days 30 --apply
```

```bash
python3 ~/.claude/skills/super-rami/scripts/crypto_bot.py run
```

| الإعداد | الافتراضي | المعنى |
|---|---|---|
| `market` | `spot` | `spot` للسبوت أو `futures` للعقود الآجلة (يدعم البيع على المكشوف) |
| `testnet` | `true` | الحساب التجريبي. الحقيقي يحتاج `false` و `export SUPER_RAMI_LIVE=I_UNDERSTAND` |
| `entry_mode` / `exit_mode` | `alert` | `auto` ينفذ بنفسه، و`alert` يرسل لك تنبيه |
| `per_trade_usdt` | فاضي | مبلغ الصفقة. لين تحدده التنفيذ التلقائي مقفول |
| `total_budget_usdt` | فاضي | الميزانية الكلية |
| `meme_size_mult` | 0.5 | الميمز بنص المبلغ |
| `leverage` / `max_leverage` | 1 / 5 | الرافعة للعقود الآجلة |
| `max_open_positions` | 3 | أقصى عدد صفقات مفتوحة |
| `strong` / `memes` | BTC وETH وSOL وBNB وXRP / DOGE وPEPE وSHIB وWIF وBONK | قوائم العملات |

مثال لتغيير إعداد:

```bash
python3 ~/.claude/skills/super-rami/scripts/crypto_bot.py set per_trade_usdt 50
```

**مفاتيح باينانس:** سوّ مفتاح API بصلاحية التداول فقط، واقفل صلاحية السحب، واربطه بعنوان IP جهازك. بعدها حط المفاتيح في متغيرات البيئة على جهازك:

```bash
export BINANCE_API_KEY=... BINANCE_API_SECRET=...
```

**تنبيهات تيليجرام (اختياري):**

```bash
export TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=...
```

## الاختبارات

```bash
python3 -m unittest discover -s tests -v
```

## تنبيه

هذي أداة شخصية للتعلم والتنظيم، **وليست نصيحة مالية**. تقارير 13F متأخرة حوالي 45 يوم، والباك تست ما يضمن نتائج المستقبل، وعقود 0DTE عالية المخاطر جداً.
