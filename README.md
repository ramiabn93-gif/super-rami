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

## الاختبارات

```bash
python3 -m unittest discover -s tests -v
```

## تنبيه

هذي أداة شخصية للتعلم والتنظيم، **وليست نصيحة مالية**. تقارير 13F متأخرة حوالي 45 يوم، والباك تست ما يضمن نتائج المستقبل، وعقود 0DTE عالية المخاطر جداً.
