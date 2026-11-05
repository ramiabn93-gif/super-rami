"""Copy a big investor: read their latest 13F-HR filing from SEC EDGAR and
turn it into portfolio weights. US stocks only (13F is a US filing).

Examples:
  python3 copycat.py --list
  python3 copycat.py buffett
  python3 copycat.py burry --compare
  python3 copycat.py --cik 1067983 --top 15 --write
"""
import argparse
import json
import re
import xml.etree.ElementTree as ET

from common import RamiError, path, run, save_json, sec_get

INVESTORS = {
    "buffett": ("Warren Buffett — Berkshire Hathaway", 1067983),
    "ackman": ("Bill Ackman — Pershing Square", 1336528),
    "burry": ("Michael Burry — Scion Asset Management", 1649339),
    "dalio": ("Ray Dalio — Bridgewater Associates", 1350694),
    "druckenmiller": ("Stanley Druckenmiller — Duquesne Family Office", 1536411),
    "tepper": ("David Tepper — Appaloosa", 1656456),
}

_SUFFIXES = r"\b(INC|INCORPORATED|CORP|CORPORATION|CO|COMPANY|LTD|LIMITED|PLC|LLC|LP|HLDGS|HOLDINGS|" \
            r"GROUP|GRP|DEL|NEW|THE|CL [A-Z]|CLASS [A-Z]|COM|SA|NV|AG|N V|S A)\b"


def norm_name(s):
    s = re.sub(r"[^A-Z0-9 ]", " ", s.upper().replace("&", " AND "))
    s = re.sub(_SUFFIXES, " ", s)
    return " ".join(s.split())


# ---------------------------------------------------------------- EDGAR

def list_13f(cik):
    data = json.loads(sec_get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json"))
    r = data["filings"]["recent"]
    out = []
    for form, acc, fdate, rdate in zip(r["form"], r["accessionNumber"], r["filingDate"], r["reportDate"]):
        if form == "13F-HR":
            out.append({"accession": acc, "filed": fdate, "period": rdate})
    if not out:
        raise RamiError(f"ما لقيت تقارير 13F-HR للرقم CIK {cik}")
    return data.get("name", str(cik)), out


def info_table_xml(cik, accession):
    folder = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}"
    idx = json.loads(sec_get(folder + "/index.json"))
    names = [i["name"] for i in idx["directory"]["item"]]
    xmls = [n for n in names if n.lower().endswith(".xml") and n.lower() != "primary_doc.xml"]
    if not xmls:
        raise RamiError(f"ما لقيت جدول الممتلكات في التقرير {accession}")
    return sec_get(f"{folder}/{xmls[0]}")


def parse_info_table(xml_bytes, include_options=False):
    """Return holdings aggregated by CUSIP: name, cusip, shares, value."""
    root = ET.fromstring(xml_bytes)
    for el in root.iter():                      # drop XML namespaces
        el.tag = el.tag.split("}", 1)[-1]
    agg = {}
    for it in root.iter("infoTable"):
        def g(tag):
            e = it.find(".//" + tag)
            return e.text.strip() if e is not None and e.text else ""
        if g("putCall") and not include_options:
            continue
        if g("sshPrnamtType") and g("sshPrnamtType") != "SH":
            continue                             # skip bonds (PRN)
        cusip = g("cusip")
        h = agg.setdefault(cusip, {"name": g("nameOfIssuer"), "cusip": cusip,
                                   "title": g("titleOfClass"), "shares": 0, "value": 0.0})
        h["shares"] += int(float(g("sshPrnamt") or 0))
        h["value"] += float(g("value") or 0)
    holdings = sorted(agg.values(), key=lambda h: -h["value"])
    total = sum(h["value"] for h in holdings) or 1
    for h in holdings:
        h["weight"] = h["value"] / total * 100
    return holdings


def attach_tickers(holdings, tickers_json):
    """Best-effort CUSIP-less match from issuer name to ticker."""
    by_name = {}
    for row in tickers_json.values():
        by_name.setdefault(norm_name(row["title"]), row["ticker"])
    for h in holdings:
        h["ticker"] = by_name.get(norm_name(h["name"]))
    return holdings


def compare(new, old):
    old_by = {h["cusip"]: h for h in old}
    new_by = {h["cusip"]: h for h in new}
    changes = []
    for c, h in new_by.items():
        o = old_by.get(c)
        if not o:
            changes.append(("جديد", h["name"], 0, h["shares"]))
        elif h["shares"] != o["shares"]:
            changes.append(("زاد" if h["shares"] > o["shares"] else "قلّل", h["name"], o["shares"], h["shares"]))
    for c, o in old_by.items():
        if c not in new_by:
            changes.append(("باع كامل", o["name"], o["shares"], 0))
    return changes


# ---------------------------------------------------------------- CLI

def main():
    ap = argparse.ArgumentParser(description="نسخ محفظة مستثمر من تقارير 13F")
    ap.add_argument("investor", nargs="?", help="اسم مختصر مثل buffett")
    ap.add_argument("--cik", type=int, help="رقم CIK لأي صندوق آخر")
    ap.add_argument("--list", action="store_true", help="عرض المستثمرين الجاهزين")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--compare", action="store_true", help="مقارنة بالربع السابق")
    ap.add_argument("--options", action="store_true", help="تضمين عقود الخيارات")
    ap.add_argument("--write", action="store_true", help="حفظ الأوزان لأداة الميزانية")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.list or not (a.investor or a.cik):
        for k, (label, cik) in INVESTORS.items():
            print(f"  {k:<14} {label}  (CIK {cik})")
        return
    if a.cik:
        key, cik = f"cik{a.cik}", a.cik
    else:
        key = a.investor.lower()
        if key not in INVESTORS:
            raise RamiError(f"ما أعرف '{a.investor}'. استخدم --list أو --cik")
        cik = INVESTORS[key][1]

    name, filings = list_13f(cik)
    latest = filings[0]
    holdings = parse_info_table(info_table_xml(cik, latest["accession"]), a.options)
    try:
        attach_tickers(holdings, json.loads(sec_get("https://www.sec.gov/files/company_tickers.json")))
    except RamiError:
        for h in holdings:
            h.setdefault("ticker", None)

    top = holdings[:a.top]
    top_total = sum(h["weight"] for h in top) or 1
    result = {"investor": name, "cik": cik, "period": latest["period"], "filed": latest["filed"],
              "positions": len(holdings),
              "holdings": [dict(h, target_weight=h["weight"] / top_total * 100) for h in top]}

    changes = []
    if a.compare and len(filings) > 1:
        prev = parse_info_table(info_table_xml(cik, filings[1]["accession"]), a.options)
        changes = compare(holdings, prev)
        result["changes"] = [dict(zip(("action", "name", "old", "new"), c)) for c in changes]

    if a.write:
        weights = {h["ticker"]: round(h["target_weight"], 2) for h in result["holdings"] if h["ticker"]}
        out = path("targets", f"{key}.json")
        save_json(out, {"source": name, "period": latest["period"], "weights": weights})
        result["saved_to"] = out

    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    print(f"📊 {name}")
    print(f"الربع المنتهي في {latest['period']} — قُدّم في {latest['filed']} — عدد المراكز {len(holdings)}")
    print("تنبيه: التقرير يطلع بعد نهاية الربع بحوالي 45 يوم، فهذي صفقات قديمة.\n")
    print(f"{'#':>2}  {'الرمز':<7} {'الوزن الكلي':>10} {'الوزن المستهدف':>14}  الشركة")
    for i, h in enumerate(result["holdings"], 1):
        print(f"{i:>2}  {(h['ticker'] or '?'):<7} {h['weight']:>9.1f}% {h['target_weight']:>13.1f}%  {h['name']}")
    if changes:
        print("\nالتغييرات عن الربع السابق:")
        for act, nm, old, new in changes[:25]:
            print(f"  {act:<9} {nm}  ({old:,} ← {new:,} سهم)")
    if a.write:
        print(f"\nتم حفظ الأوزان في {result['saved_to']}")
        missing = [h["name"] for h in result["holdings"] if not h["ticker"]]
        if missing:
            print("ما لقيت رمز لهذي الشركات فما انحفظت:", "، ".join(missing))


if __name__ == "__main__":
    run(main)
