"""Show or change Super Rami settings (stored in ~/super-rami-data/config.json)."""
import argparse
import json

from common import HOME, load_config, run, save_config


def main():
    ap = argparse.ArgumentParser(description="إعدادات سوبر رامي")
    ap.add_argument("--sec-user-agent", help='اسمك وإيميلك لـ SEC مثل "Rami you@example.com"')
    ap.add_argument("--cash-buffer-pct", type=float, help="نسبة الكاش الاحتياطي")
    ap.add_argument("--max-position-pct", type=float, help="أقصى نسبة لسهم واحد")
    ap.add_argument("--max-trade-pct", type=float, help="أقصى نسبة لصفقة واحدة")
    ap.add_argument("--min-price", type=float, help="أقل سعر سهم مسموح")
    a = ap.parse_args()

    cfg = load_config()
    changes = {k: v for k, v in vars(a).items() if v is not None}
    for k, v in changes.items():
        if k.endswith("_pct") and not 0 <= v <= 100:
            ap.error(f"{k} لازم يكون بين 0 و 100")
        cfg[k] = v
    if changes:
        save_config(cfg)
        print("تم حفظ الإعدادات.")
    print(f"مجلد البيانات: {HOME}")
    print(json.dumps(cfg, ensure_ascii=False, indent=2))
    if not cfg.get("sec_user_agent"):
        print("\nتنبيه: ما حددت sec_user_agent، وأداة نسخ المستثمرين تحتاجه.")


if __name__ == "__main__":
    run(main)
