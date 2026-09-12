# -*- coding: utf-8 -*-
"""Meta Ads client PDF report generator.
Terima data dash (sama macam dipaparkan dlm dashboard), bina HTML profesional,
render ke PDF guna headless Chrome (text selectable — bukan screenshot).
"""
import html as _html
import json
import os
import re
import subprocess
import tempfile
import time

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


def esc(s):
    return _html.escape(str(s if s is not None else ""))


def fmt_rm(n):
    try:
        n = float(n or 0)
    except Exception:
        n = 0
    return "RM" + "{:,.2f}".format(n)


def fmt_n(n):
    try:
        n = float(n or 0)
    except Exception:
        n = 0
    return "{:,.0f}".format(n)


def pct_str(x, dec=2):
    try:
        x = float(x or 0)
    except Exception:
        x = 0
    return ("{:." + str(dec) + "f}%").format(x)


def fmt_date_display(dstr):
    """2026-08-28 -> 28 Aug 2026"""
    try:
        import datetime
        d = datetime.datetime.strptime(str(dstr)[:10], "%Y-%m-%d")
        return d.strftime("%d %b %Y")
    except Exception:
        return str(dstr)


MONTHS_MS = {1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
             7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec"}


def fmt_range(from_d, to_d):
    try:
        import datetime
        a = datetime.datetime.strptime(str(from_d)[:10], "%Y-%m-%d")
        b = datetime.datetime.strptime(str(to_d)[:10], "%Y-%m-%d")
        return a.strftime("%d %b %Y") + " \u2013 " + b.strftime("%d %b %Y")
    except Exception:
        return str(from_d) + " \u2013 " + str(to_d)


def action_res(actions, result_type=None):
    """Kira results dari actions (sokong list ATAU dict) — sama dgn dashboard."""
    if not actions:
        return 0
    if isinstance(actions, dict):
        v = actions.get("onsite_conversion.messaging_conversation_started_7d", 0) or 0
        return int(v)
    if isinstance(actions, list):
        for a in actions:
            t = a.get("action_type", "")
            if t == "onsite_conversion.messaging_conversation_started_7d":
                return int(a.get("value") or 0)
    return 0


def res_of(row):
    if not row:
        return 0
    acts = row.get("actions")
    if not acts:
        return 0
    if isinstance(acts, dict):
        v = acts.get("onsite_conversion.messaging_conversation_started_7d", 0) or 0
        if v:
            return int(v)
        v = acts.get("onsite_conversion.messaging_first_reply", 0) or 0
        if v:
            return int(v)
        return 0
    if isinstance(acts, list):
        types = ["onsite_conversion.messaging_conversation_started_7d",
                 "onsite_conversion.messaging_first_reply",
                 "purchase", "lead", "onsite_conversion.lead"]
        for t in types:
            for a in acts:
                if a.get("action_type") == t and float(a.get("value") or 0) > 0:
                    return int(a.get("value") or 0)
    return 0


def totals(rows):
    T = {"spend": 0, "impr": 0, "reach": 0, "clicks": 0, "link": 0, "results": 0}
    for r in rows or []:
        T["spend"] += float(r.get("spend") or 0)
        T["impr"] += float(r.get("impressions") or 0)
        T["reach"] += float(r.get("reach") or 0)
        T["clicks"] += float(r.get("clicks") or 0)
        T["link"] += float(r.get("inline_link_clicks") or 0)
        T["results"] += res_of(r)
    T["freq"] = (T["impr"] / T["reach"]) if T["reach"] else 0
    return T


def delta_pct(cur, prev):
    """% change; None jika prev 0/tiada."""
    try:
        c = float(cur)
        p = float(prev)
    except Exception:
        return None
    if p == 0:
        return None
    return (c - p) / p * 100


def arrow_html(chg, invert=False):
    if chg is None:
        return '<span class="na">N/A</span>'
    if abs(chg) < 0.05:
        return '<span class="flat">\u2022 {:.1f}%</span>'.format(abs(chg))
    good = (chg > 0) if not invert else (chg < 0)
    bad = (chg < 0) if not invert else (chg > 0)
    cls = "good" if good else ("bad" if bad else "flat")
    arrow = "\u25b2" if chg > 0 else "\u25bc"
    return '<span class="%s">%s %.1f%%</span>' % (cls, arrow, abs(chg))


def metric_cards(T):
    cpr = (T["spend"] / T["results"]) if T["results"] else None
    cpm = (T["spend"] * 1000 / T["impr"]) if T["impr"] else 0
    ctr_all = (T["clicks"] * 100 / T["impr"]) if T["impr"] else 0
    ctr_link = (T["link"] * 100 / T["impr"]) if T["impr"] else 0
    cpc = (T["spend"] / T["link"]) if T["link"] else None
    cards = [
        ("Results", fmt_n(T["results"]), "Messaging conversations" if T["results"] else "\u2014"),
        ("Cost per result", fmt_rm(cpr) if cpr is not None else "\u2014", "per result"),
        ("Amount spent", fmt_rm(T["spend"]), "total spend"),
        ("CPM", fmt_rm(cpm), "per 1,000 impressions"),
        ("Impressions", fmt_n(T["impr"]), "total views"),
        ("Frequency", "{:.2f}x".format(T["freq"]) if T["freq"] else "\u2014", "impr / reach"),
        ("Reach", fmt_n(T["reach"]), "unique people"),
        ("CTR (all)", pct_str(ctr_all), "all clicks"),
        ("CTR (link click-through rate)", pct_str(ctr_link), "link clicks"),
        ("CPC (cost per link click)", fmt_rm(cpc) if cpc is not None else "\u2014", "per link click"),
    ]
    rows = ""
    for i in range(0, len(cards), 2):
        a, b = cards[i], cards[i + 1] if i + 1 < len(cards) else None
        rows += "<tr>"
        for c in (a, b):
            if not c:
                rows += "<td></td><td></td>"
                continue
            rows += ('<td class="mcard"><div class="mlbl">%s</div>'
                     '<div class="mval">%s</div><div class="msub">%s</div></td>') % (
                c[0], c[1], c[2])
        rows += "</tr>"
    return rows


def wow_table(T, P):
    rows_data = [
        ("Results", T["results"], P["results"], "n", False),
        ("Cost per result", (T["spend"] / T["results"] if T["results"] else None),
         (P["spend"] / P["results"] if P["results"] else None), "rm", True),
        ("Amount spent", T["spend"], P["spend"], "rm", None),
        ("Reach", T["reach"], P["reach"], "n", False),
        ("Impressions", T["impr"], P["impr"], "n", False),
        ("CTR (all)", (T["clicks"] * 100 / T["impr"]) if T["impr"] else None,
         (P["clicks"] * 100 / P["impr"]) if P["impr"] else None, "p", False),
        ("CPM", (T["spend"] * 1000 / T["impr"]) if T["impr"] else None,
         (P["spend"] * 1000 / P["impr"]) if P["impr"] else None, "rm", True),
    ]
    out = ""
    for label, c, p, kind, invert in rows_data:
        chg = delta_pct(c, p)
        if kind == "rm":
            cs = fmt_rm(c) if c is not None else "\u2014"
            ps = fmt_rm(p) if p is not None else "\u2014"
        elif kind == "p":
            cs = pct_str(c) if c is not None else "\u2014"
            ps = pct_str(p) if p is not None else "\u2014"
        else:
            cs = fmt_n(c) if c is not None else "\u2014"
            ps = fmt_n(p) if p is not None else "\u2014"
        out += ("<tr><td class='wlbl'>%s</td><td class='cur'>%s</td>"
                "<td class='prev'>%s</td><td class='chg'>%s</td></tr>") % (
            label, cs, ps, arrow_html(chg, invert))
    return out


def trend_svg(daily, metric="spend"):
    """Bar chart ringkas guna SVG (real data, bukan screenshot)."""
    by_day = {}
    for r in daily or []:
        k = str(r.get("date_start") or r.get("date") or "")
        if not k:
            continue
        if k not in by_day:
            by_day[k] = {"spend": 0, "impr": 0, "clicks": 0, "reach": 0}
        by_day[k]["spend"] += float(r.get("spend") or 0)
        by_day[k]["impr"] += float(r.get("impressions") or 0)
        by_day[k]["clicks"] += float(r.get("clicks") or 0)
        by_day[k]["reach"] += float(r.get("reach") or 0)
    arr = sorted(by_day.items())
    if not arr:
        return "<p class='na'>Tiada data harian.</p>"
    W, H = 720, 220
    pad_l, pad_b, pad_t, pad_r = 60, 34, 24, 12
    iw, ih = W - pad_l - pad_r, H - pad_t - pad_b
    if metric == "spend":
        vals = [v["spend"] for _, v in arr]
        fmt = lambda v: fmt_rm(v)
    elif metric == "results":
        vals = [res_of(v) for _, v in arr]
        fmt = lambda v: fmt_n(v)
    elif metric == "impr":
        vals = [v["impr"] for _, v in arr]
        fmt = lambda v: fmt_n(v)
    elif metric == "reach":
        vals = [v["reach"] for _, v in arr]
        fmt = lambda v: fmt_n(v)
    elif metric == "ctr":
        vals = [(v["clicks"] * 100 / v["impr"]) if v["impr"] else 0 for _, v in arr]
        fmt = lambda v: "{:.2f}%".format(v)
    else:  # cpm
        vals = [(v["spend"] * 1000 / v["impr"]) if v["impr"] else 0 for _, v in arr]
        fmt = lambda v: fmt_rm(v)
    maxv = max(vals + [1]) * 1.15
    step = iw / max(len(arr) - 1, 1)
    bw = min(40, step * 0.55)
    svg = ['<svg viewBox="0 0 %d %d" xmlns="http://www.w3.org/2000/svg" style="width:100%%;height:auto">' % (W, H)]
    # gridlines
    for g in range(4):
        y = pad_t + ih - g * ih / 3
        svg.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#ece8e4" stroke-width="1"/>' % (pad_l, y, W - pad_r, y))
    for i, (k, _) in enumerate(arr):
        v = vals[i]
        cx = pad_l + i * step
        bx = cx - bw / 2
        hh = max(v / maxv * ih, 2)
        label = k[5:]
        svg.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="3" fill="#f97316"/>' % (bx, pad_t + ih - hh, bw, hh))
        svg.append('<text x="%.1f" y="%d" text-anchor="middle" font-size="11" fill="#9ca3af">%s</text>' % (cx, H - 8, label))
        if v > 0:
            svg.append('<text x="%.1f" y="%.1f" text-anchor="middle" font-size="10" fill="#6b7280">%s</text>' % (cx, pad_t + ih - hh - 5, fmt(v)))
    svg.append("</svg>")
    return "".join(svg)


def hierarchy_rows(d):
    """Campaign → Ad Set → Ad (nama sebenar dari ads_insights, bukan tree).
    Aggregate ad-level insights ikut campaign/adset; budget dari tree kalau ada."""
    ads = (d.get("ads_insights") or {}).get("data", []) or []
    # budget map dari tree (by campaign/adset name)
    budget_camp = {}
    budget_adset = {}
    for c in (d.get("tree") or {}).get("campaigns", []) or []:
        cname = c.get("campaignName") or c.get("name") or ""
        cb = (c.get("budget") or {}).get("amount")
        if cb:
            budget_camp[cname] = cb
        for asc in (c.get("adSets") or []) or []:
            an = asc.get("adSetName") or ""
            ab = (asc.get("budget") or {}).get("amount")
            if ab:
                budget_adset[(cname, an)] = ab
    # aggregate
    camps = {}     # name -> totals
    adsets = {}    # (camp, adset) -> totals
    for r in ads:
        cn = r.get("campaign_name") or "(campaign)"
        an = r.get("adset_name") or "(ad set)"
        for key, store in ((cn, camps), ((cn, an), adsets)):
            t = store.setdefault(key, {"spend": 0, "impr": 0, "reach": 0, "clicks": 0, "link": 0, "results": 0})
            t["spend"] += float(r.get("spend") or 0)
            t["impr"] += float(r.get("impressions") or 0)
            t["reach"] += float(r.get("reach") or 0)
            t["clicks"] += float(r.get("clicks") or 0)
            t["link"] += float(r.get("inline_link_clicks") or 0)
            t["results"] += res_of(r)

    def metrics_cells(m, spend, res):
        cpm = (spend * 1000 / m["impr"]) if m["impr"] else 0
        ctr_all = (m["clicks"] * 100 / m["impr"]) if m["impr"] else 0
        ctr_link = (m["link"] * 100 / m["impr"]) if m["impr"] else 0
        cpc = (spend / m["link"]) if m["link"] else None
        cpr = (spend / res) if res else None
        freq = (m["impr"] / m["reach"]) if m["reach"] else 0
        return (fmt_n(res) if res else "\u2014",
                fmt_rm(cpr) if cpr is not None else "\u2014",
                fmt_rm(spend), fmt_rm(cpm), fmt_n(m["impr"]),
                ("{:.2f}x".format(freq)) if freq else "\u2014",
                fmt_n(m["reach"]), pct_str(ctr_all), pct_str(ctr_link),
                fmt_rm(cpc) if cpc is not None else "\u2014")

    def row(cls, name, note, m, spend, res):
        cells = metrics_cells(m, spend, res)
        extra = '<div class="hsub">%s</div>' % esc(note) if note else ""
        pad = (" style='padding-left:22px'" if cls == "r-adset"
               else (" style='padding-left:44px'" if cls == "r-ad" else ""))
        tds = "".join("<td>%s</td>" % c for c in cells)
        return ("<tr class='%s'><td%s><div class='hname'>%s</div>%s</td>%s</tr>"
                % (cls, pad, esc(name), extra, tds))

    out = ""
    # campaign rows
    for cn, cm_ in camps.items():
        note = ("Campaign budget %s" % fmt_rm(budget_camp[cn])) if cn in budget_camp else ""
        out += row("r-camp", cn, note, cm_, cm_["spend"], cm_["results"])
        # ad sets bawah campaign ni
        for (c2, an), as_m in adsets.items():
            if c2 != cn:
                continue
            ab = budget_adset.get((cn, an))
            note2 = ("Daily budget %s" % fmt_rm(ab)) if ab else ""
            out += row("r-adset", an, note2, as_m, as_m["spend"], as_m["results"])
            # ads bawah adset ni
            for r in ads:
                if (r.get("campaign_name") or "") != cn or (r.get("adset_name") or "") != an:
                    continue
                adname = (r.get("ad_name") or "").strip()
                if not adname:
                    adname = "(ad)"  # fallback — hanya kalau API langsung tak bagi nama
                out += row("r-ad", adname, "", {
                    "impr": float(r.get("impressions") or 0),
                    "reach": float(r.get("reach") or 0),
                    "clicks": float(r.get("clicks") or 0),
                    "link": float(r.get("inline_link_clicks") or 0)}, float(r.get("spend") or 0), res_of(r))
    return out or "<tr><td colspan='11' class='na'>Tiada data.</td></tr>"


def top_ads_rows(d):
    ads = (d.get("ads_insights") or {}).get("data", []) or []
    ranked = []
    for r in ads:
        res = res_of(r)
        sp = float(r.get("spend") or 0)
        lnk = float(r.get("inline_link_clicks") or 0)
        imp = float(r.get("impressions") or 0)
        if not res and sp == 0:
            continue
        ranked.append({
            "name": esc(r.get("ad_name") or "(ad)"), "res": res, "spend": sp,
            "ctr": (lnk * 100 / imp) if imp else 0,
            "cpr": (sp / res) if res else None,
            "camp": esc(r.get("campaign_name") or "")})
    ranked.sort(key=lambda x: (-x["res"], x["cpr"] if x["cpr"] is not None else 1e9, -x["ctr"]))
    out = ""
    for i, a in enumerate(ranked[:5], 1):
        out += (
            '<tr><td class="rank">%d</td><td><div class="hname">%s</div>'
            '<div class="hsub">%s</div></td><td><b>%s</b></td><td>%s</td>'
            "<td>%s</td><td>%s</td></tr>"
        ) % (i, a["name"], a["camp"], fmt_n(a["res"]),
             fmt_rm(a["cpr"]) if a["cpr"] is not None else "\u2014",
             fmt_rm(a["spend"]), "{:.2f}%".format(a["ctr"]))
    return out or "<tr><td colspan='6' class='na'>Tiada data ad.</td></tr>"


def ai_sections(d):
    """Analisis mingguan — bahasa santai Malaysia (bukan skema/Indonesia)."""
    C = totals((d.get("insights") or {}).get("data", []))
    P = totals((d.get("prev_insights") or {}).get("data", []))
    ads = (d.get("ads_insights") or {}).get("data", []) or []
    facts, obs, concl, take = [], [], [], []
    facts.append("%d ad aktif minggu ni." % len(ads))
    facts.append("Belanja iklan RM%.2f." % C["spend"])
    facts.append("%s orang hantar mesej (WhatsApp)." % fmt_n(C["results"]))
    facts.append("Iklan nampak kat %s orang, %s kali papar." % (fmt_n(C["reach"]), fmt_n(C["impr"])))
    ad_res = [{"name": r.get("ad_name"), "res": res_of(r), "spend": float(r.get("spend") or 0)}
              for r in ads]
    ad_res.sort(key=lambda x: -x["res"])
    if len(ad_res) > 1 and ad_res[0] and ad_res[0]["res"] > 0:
        tot = sum(a["res"] for a in ad_res) or 1
        share = ad_res[0]["res"] / tot * 100
        if share > 50:
            obs.append("Video \"%s\" bawa paling banyak mesej (%.0f%% dari total)."
                       % (ad_res[0]["name"], share))
            concl.append("Hasil iklan tertumpu kat video tu — yang lain macam slow sikit.")
    chg = delta_pct(C["results"], P["results"])
    if chg is not None and abs(chg) >= 5:
        obs.append("Mesej masuk %s %.0f%% berbanding minggu lepas."
                   % ("naik" if chg > 0 else "turun sikit", abs(chg)))
    cpr = C["spend"] / C["results"] if C["results"] else 0
    pcpr = P["spend"] / P["results"] if P["results"] else 0
    if cpr and pcpr:
        chgc = delta_pct(cpr, pcpr)
        if chgc is not None and abs(chgc) >= 5:
            obs.append("Kos per mesej %s — RM%.2f (minggu lepas RM%.2f)."
                       % ("naik" if chgc > 0 else "murah sikit", cpr, pcpr))
    if not concl:
        concl.append("Minggu ni jalan macam biasa, takde perubahan besar.")
    if chg is not None:
        take.append("Mesej masuk %s vs minggu lepas%s."
                    % ("naik" if chg >= 0 else "turun",
                       (" (%.0f%%)" % abs(chg)) if abs(chg) >= 5 else ""))
    if cpr and pcpr and abs(delta_pct(cpr, pcpr) or 0) >= 5:
        take.append("Kos per mesej %s — RM%.2f."
                    % ("makin murah" if cpr < pcpr else "naik sikit", cpr))
    if len(ad_res) > 1 and ad_res[0] and ad_res[0]["res"] > 0:
        s = ad_res[0]["res"] / (sum(a["res"] for a in ad_res) or 1) * 100
        if s > 50:
            take.append("Satu video je yang bawa majoriti mesej (%.0f%%)." % s)
    if not take:
        take.append("Kempen jalan seperti biasa minggu ni.")

    def ul(t, items):
        return ("<div class='aihead'>%s</div><ul class='ailist'>%s</ul>" % (
            t, "".join("<li>%s</li>" % esc(x) for x in items))) if items else ""
    # SYOR dibuang dari PDF (Requirement 5) — kekal FAKTA/PEMERHATIAN/KESIMPULAN/POIN PENTING
    return ul("FAKTA", facts) + ul("PEMERHATIAN", obs) + ul("KESIMPULAN", concl) \
        + ul("POIN PENTING", take)


def build_html(ctx, d):
    C = totals((d.get("insights") or {}).get("data", []))
    P = totals((d.get("prev_insights") or {}).get("data", []))
    fin = d.get("finance") or {}
    acct_name = ctx.get("accountName") or "Client"
    adid = ctx.get("adAccountId") or ""
    from_d = ctx.get("from") or d.get("from") or ""
    to_d = ctx.get("to") or d.get("to") or ""
    gen = time.strftime("%d %b %Y")
    budget_daily = 0
    for c in (d.get("tree") or {}).get("campaigns", []) or []:
        for as_ in (c.get("adSets") or []) or []:
            budget_daily += float(((as_.get("budget") or {}).get("amount")) or 0)
    budget_note = ("Gabungan daily budget ad set: %s" % fmt_rm(budget_daily)) if budget_daily else "Daily budget: tiada maklumat"
    title = "Meta Ads Performance Report"
    if acct_name.lower().startswith("jfr"):
        title = "Meta Ads Performance Report"

    return """<!doctype html>
<html><head><meta charset="utf-8"><style>
  @page {{ size: A4 landscape; margin: 14mm 12mm 14mm 12mm;
          @bottom-center {{ content: "Page " counter(page) " of " counter(pages); font-size: 8pt; color: #9ca3af; }} }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: 'Segoe UI', Arial, sans-serif; color: #1f2430; margin: 0; font-size: 9.5pt; }}
  .head {{ display: flex; justify-content: space-between; align-items: flex-end;
           border-bottom: 3px solid #f97316; padding-bottom: 10px; margin-bottom: 14px; }}
  .head h1 {{ margin: 0; font-size: 20pt; color: #1f2430; letter-spacing: .3px; }}
  .head .sub {{ color: #6b7280; font-size: 9pt; margin-top: 3px; }}
  .meta {{ display: flex; gap: 26px; flex-wrap: wrap; margin: 4px 0 14px; }}
  .meta .k {{ font-size: 7.5pt; text-transform: uppercase; letter-spacing: 1px; color: #9ca3af; }}
  .meta .v {{ font-size: 11.5pt; font-weight: 700; color: #1f2430; }}
  h2 {{ font-size: 12pt; color: #c2410c; margin: 18px 0 8px; border-left: 4px solid #f97316;
        padding-left: 8px; }}
  table {{ width: 100%; border-collapse: collapse; }}
  .mc {{ display: table; width: 100%; }}
  .mc tr td {{ padding: 3px; }}
  .mcard {{ border: 1px solid #e8e2dc; border-radius: 8px; padding: 8px 10px !important;
            background: #fdfaf7; }}
  .mval {{ font-size: 19pt; font-weight: 800; color: #1f2430; line-height: 1.1; }}
  .mlbl {{ font-size: 7.8pt; color: #6b7280; text-transform: uppercase; letter-spacing: .4px; }}
  .msub {{ font-size: 7.2pt; color: #9ca3af; }}
  .mc table td {{ width: 50%; }}
  .wt td {{ border: 1px solid #ece8e4; padding: 6px 9px; font-size: 10pt; }}
  .wt th {{ background: #fff3ec; border: 1px solid #ece8e4; padding: 7px 9px; text-align: left;
            font-size: 9.5pt; }}
  .wlbl {{ font-weight: 700; }}
  .cur {{ text-align: right; font-weight: 800; font-size: 11pt; }}
  .prev {{ text-align: right; color: #6b7280; }}
  .chg {{ text-align: right; }}
  .good {{ color: #047857; font-weight: 700; }} .bad {{ color: #b91c1c; font-weight: 700; }}
  .flat {{ color: #6b7280; }} .na {{ color: #9ca3af; }}
  .ht {{ border-collapse: collapse; }}
  .ht th {{ background: #2f3644; color: #fff; padding: 6px 7px; font-size: 8pt; text-align: left; }}
  .ht td {{ border-bottom: 1px solid #ece8e4; padding: 5px 7px; font-size: 8.5pt; }}
  .ht td.num {{ text-align: right; }}
  .hname {{ font-weight: 700; font-size: 9pt; }}
  .hsub {{ font-size: 7.5pt; color: #9ca3af; }}
  /* ===== page-break handling (elak section/table terpotong pelik) ===== */
  h2 {{ page-break-after: avoid; break-after: avoid; }}
  table.ht tr, table.wt tr, .mcard {{ page-break-inside: avoid; break-inside: avoid; }}
  table.ht thead {{ display: table-header-group; }}
  table.ht tbody tr.r-camp {{ page-break-before: auto; }}
  .aihead {{ page-break-after: avoid; break-after: avoid; }}
  .ailist li {{ page-break-inside: avoid; break-inside: avoid; }}
  .panel {{ page-break-inside: avoid; break-inside: avoid; }}
  .rank {{ font-size: 12pt; font-weight: 900; color: #f97316; text-align: center; }}
  .aihead {{ font-weight: 800; font-size: 9.5pt; color: #c2410c; margin-top: 7px; }}
  .ailist {{ margin: 2px 0 4px; padding-left: 16px; }}
  .ailist li {{ margin: 1px 0; }}
  .panel {{ background: #fdfaf7; border: 1px solid #e8e2dc; border-radius: 8px; padding: 10px 12px; }}
  .panel .big {{ font-size: 15pt; font-weight: 800; }}
  .two {{ display: flex; gap: 12px; }}
  .two > div {{ flex: 1; }}
  .avoid {{ break-inside: avoid; }}
  tr {{ break-inside: avoid; }}
  .foot {{ margin-top: 14px; color: #b0a9a0; font-size: 7.5pt; border-top: 1px solid #ece8e4; padding-top: 6px; }}
</style></head><body>

<div class="head">
  <div>
    <h1>{title}</h1>
    <div class="sub">Client reporting \u00b7 Meta Ads</div>
  </div>
  <div class="meta" style="margin:0">
    <div><div class="k">Generated</div><div class="v">{gen}</div></div>
  </div>
</div>

<div class="meta">
  <div><div class="k">Client</div><div class="v">{client}</div></div>
  <div><div class="k">Ad Account</div><div class="v">{acct}</div></div>
  <div><div class="k">Reporting Period</div><div class="v">{rng}</div></div>
  <div><div class="k">Previous Period</div><div class="v">{prng}</div></div>
</div>

<h2>Prestasi Iklan</h2>
<table class="mc"><tbody>{cards}</tbody></table>
<div style="font-size:7.5pt;color:#9ca3af;margin-top:3px">Mesej = perbualan WhatsApp (lead gen). Info bajet (daily budget per ad set) ada dalam bahagian Hierarki Iklan.</div>

<h2>Banding Dengan Minggu Lepas</h2>
<table class="wt">
  <tr><th style="width:26%">Metrik</th><th>Minggu Ni</th><th>Minggu Lepas</th><th style="width:18%">Beza</th></tr>
  {wow}
</table>

<h2>Trend Harian</h2>
<div class="panel">{trend}</div>
<div style="font-size:7.5pt;color:#9ca3af;margin-top:3px">Carta: Belanja iklan sehari-hari \u00b7 {rng}</div>

<h2>Hierarki Iklan</h2>
<table class="ht">
  <thead><tr><th>Kempen / Ad Set / Iklan</th><th>Mesej</th><th>Kos/Mesej</th><th>Belanja</th><th>CPM</th><th>Papar</th><th>Kekerapan</th><th>Jangkauan</th><th>CTR (all)</th><th>CTR (link)</th><th>CPC (link)</th></tr></thead>
  <tbody>{hier}</tbody>
</table>

<h2>KESIMPULAN IKLAN</h2>
<div class="panel">{ai}</div>

<div class="foot">Report dijana automatik dari data Meta Ads sebenar \u00b7 {client} \u00b7 {rng}</div>
</body></html>""".format(
        title=title, gen=gen,
        client=esc(ctx.get("clientName") or acct_name), acct=esc(acct_name),
        rng=fmt_range(from_d, to_d),
        prng=fmt_range(d.get("prev_from"), d.get("prev_to")) if d.get("prev_from") else "\u2014",
        cards=metric_cards(C),
        wow=wow_table(C, P),
        trend=trend_svg((d.get("daily_insights") or {}).get("data", []),
                        ctx.get("trendMetric", "spend")),
        bal=fmt_rm(fin.get("balance")),
        spend=fmt_rm(C["spend"]),
        budget_note=budget_note,
        hier=hierarchy_rows(d),
        topads=top_ads_rows(d),
        ai=ai_sections(d),
    )


def slugify(name):
    s = re.sub(r"[^A-Za-z0-9]+", "-", str(name)).strip("-")
    return s or "Client"


def generate(ctx, dash_data, out_dir=None):
    """Generate PDF. ctx: {accountName, clientName?, adAccountId, from, to, trendMetric}
    dash_data: response /api/meta/dash (data yang sama dgn dashboard).
    Return (pdf_path, filename)."""
    html_str = build_html(ctx, dash_data)
    fd, html_path = tempfile.mkstemp(suffix=".html", prefix="meta_report_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(html_str)
    out_dir = out_dir or os.path.join(os.environ.get("TEMP", "."), "meta_pdf")
    os.makedirs(out_dir, exist_ok=True)
    fname = "%s_Meta-Ads-Report_%s_to_%s.pdf" % (
        slugify(ctx.get("accountName") or "Client"),
        str(ctx.get("from") or "")[:10], str(ctx.get("to") or "")[:10])
    pdf_path = os.path.join(out_dir, fname)
    cmd = [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
           "--disable-extensions",
           "--print-to-pdf=" + pdf_path,
           "--no-pdf-header-footer",
           "file:///" + html_path.replace("\\", "/")]
    subprocess.run(cmd, timeout=90,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        os.unlink(html_path)
    except Exception:
        pass
    if not os.path.exists(pdf_path):
        raise RuntimeError("PDF tidak dihasilkan (Chrome gagal)")
    return pdf_path, fname
