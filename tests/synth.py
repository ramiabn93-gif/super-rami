import datetime as dt
from zoneinfo import ZoneInfo
NY=ZoneInfo("America/New_York")
def make(day=4):
    t0=dt.datetime(2026,11,day,9,30,tzinfo=NY).timestamp()
    closes=[]; p=5030.0
    for i in range(40): p-=0.75; closes.append(p)
    for i in range(30): p+=0.7; closes.append(p)
    for i in range(54): p += (-1.5 if (i//3)%2==0 else 0.8); closes.append(p)
    for i in range(30): p+=0.6; closes.append(p)
    bars=[]; prev=closes[0]
    for i,c in enumerate(closes):
        up=c>=prev
        bars.append({"t":t0+60*i,"o":prev,"c":c,"h":(c+0.3) if up else (prev+0.1),"l":(prev-0.1) if up else (c-0.3)}); prev=c
    return bars
def mirror(bars):
    return [{"t":b["t"],"o":10000-b["o"],"c":10000-b["c"],"h":10000-b["l"],"l":10000-b["h"]} for b in bars]
