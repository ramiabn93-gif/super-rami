"""Super Rami menu: pick a number, no commands to remember."""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "skills", "super-rami", "scripts")
if not os.path.isdir(SCRIPTS):                                   # installed copy
    SCRIPTS = os.path.join(os.path.expanduser("~"), ".claude", "skills", "super-rami", "scripts")
PY = sys.executable or "python3"


def sh(*args):
    print()
    subprocess.call([PY, os.path.join(SCRIPTS, args[0]), *args[1:]], cwd=SCRIPTS)
    input("\nاضغط Enter للرجوع للقائمة...")


def ask(q, default=None):
    a = input(f"{q}{f' [{default}]' if default else ''}: ").strip()
    return a or default


def yes(q):
    return ask(q + " (نعم/لا)", "لا") in ("نعم", "ن", "y", "yes", "اي", "إي")


def invest():
    market = "sa" if ask("السوق: 1 أمريكي، 2 سعودي", "1") == "2" else "us"
    budget = ask("كم الميزانية؟ (فلوس وهمية)", "2000")
    sh("picker.py", "--market", market, "--budget", budget, "--write")
    sh("paper.py", "apply-plan")
    if yes("أنفّذ الخطة على المحفظة الوهمية؟"):
        sh("paper.py", "apply-plan", "--yes")


def crypto_settings():
    print("\n1) مبلغ الصفقة  2) الميزانية الكلية  3) الشراء تلقائي أو تنبيه  4) البيع تلقائي أو تنبيه  5) سبوت أو عقود آجلة")
    c = ask("اختر")
    if c == "1":
        sh("crypto_bot.py", "set", "per_trade_usdt", ask("كم دولار للصفقة؟"))
    elif c == "2":
        sh("crypto_bot.py", "set", "total_budget_usdt", ask("كم الميزانية الكلية بالدولار؟"))
    elif c in ("3", "4"):
        key = "entry_mode" if c == "3" else "exit_mode"
        sh("crypto_bot.py", "set", key, "auto" if ask("1 تلقائي، 2 تنبيه", "2") == "1" else "alert")
    elif c == "5":
        sh("crypto_bot.py", "set", "market", "futures" if ask("1 سبوت، 2 عقود آجلة", "1") == "2" else "spot")


def advise():
    b = ask("كم ميزانيتك بالدولار؟ (اتركها فاضية إذا ما تبي مبالغ)")
    sh("advisor.py", *(["--budget", b] if b else []))


MENU = [
    ("⭐ وش أشتري اليوم؟ (نصيحة سوبر رامي)", advise),
    ("🎯 اختر لي أسهم أمريكية", lambda: sh("picker.py", "--market", "us")),
    ("🎯 اختر لي أسهم سعودية", lambda: sh("picker.py", "--market", "sa")),
    ("💰 اختر لي واستثمر (محفظة وهمية)", invest),
    ("🪙 اختر لي عملات (باينانس)", lambda: sh("crypto_bot.py", "autopick")),
    ("📈 حلل سهم", lambda: sh("analyze.py", *ask("رمز السهم (مثل AAPL أو 2222)", "2222").split())),
    ("📒 محفظتي الوهمية", lambda: sh("paper.py", "status")),
    ("🤖 شغّل بوت العملات", lambda: sh("crypto_bot.py", "run")),
    ("⚙️ إعدادات بوت العملات", crypto_settings),
    ("📊 إشارات SPX الحين", lambda: sh("strategy.py", "scan")),
    ("🤖 شغّل بوت SPX (IBKR)", lambda: sh("autotrader.py", "ibkr")),
    ("⛔ أوقف كل البوتات واقفل الصفقات", lambda: (sh("crypto_bot.py", "stop"), sh("autotrader.py", "stop"))),
    ("▶️ إلغاء الإيقاف", lambda: (sh("crypto_bot.py", "resume"), sh("autotrader.py", "resume"))),
]


def main():
    if not os.path.isdir(SCRIPTS):
        print("ما لقيت ملفات سوبر رامي. شغّل ملف التثبيت أول.")
        return
    while True:
        print("\n" + "═" * 40)
        print("   🦸 سوبر رامي")
        print("═" * 40)
        for i, (label, _) in enumerate(MENU, 1):
            print(f" {i:>2}) {label}")
        print("  0) خروج")
        c = input("\nاختر رقم: ").strip()
        if c == "0":
            break
        if c.isdigit() and 1 <= int(c) <= len(MENU):
            try:
                MENU[int(c) - 1][1]()
            except KeyboardInterrupt:
                print("\nرجعنا للقائمة.")
        else:
            print("رقم غير صحيح.")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nمع السلامة 👋")
