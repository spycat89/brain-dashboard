# -*- coding: utf-8 -*-
"""Brain Webapp — server untuk dashboard Brain (baca SOUL/MEMORY/USER/skills).
Jalankan: python brain_server.py  →  http://127.0.0.1:8800
"""
import json
import os
import re
import subprocess
import threading
import time as _time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

HOME = r"C:\Users\Adie\AppData\Local\hermes"
PORT = 8800

# Zernio/Meta Ads — import helper (path tambahan)
import sys as _sys
_META_DIR = r"C:\Users\Adie\meta-ads-dashboard"
if _META_DIR not in _sys.path:
    _sys.path.insert(0, _META_DIR)
HERMES = os.path.join(HOME, "hermes-agent", "venv", "Scripts", "hermes.exe")
if not os.path.exists(HERMES):
    HERMES = "hermes"

# ===== AI Weekly Analysis store (ONE SOURCE OF TRUTH — dikongsi dashboard + PDF) =====
ANALYSIS_STORE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "analysis_store.json")


def _analysis_key(acct, f, t):
    return "%s|%s|%s" % (acct or "", f or "", t or "")


def _read_analysis_store():
    try:
        with open(ANALYSIS_STORE, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def load_analysis(acct, f, t):
    return _read_analysis_store().get(_analysis_key(acct, f, t))


def save_analysis(acct, f, t, analysis):
    st = _read_analysis_store()
    st[_analysis_key(acct, f, t)] = analysis
    tmp = ANALYSIS_STORE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(st, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, ANALYSIS_STORE)
    return True

# Senarai dokumen "otak"
BRAIN_FILES = [
    {"id": "soul", "name": "SOUL.md", "desc": "Identiti teras Ana", "path": os.path.join(HOME, "SOUL.md")},
    {"id": "memory", "name": "MEMORY.md", "desc": "Nota kerja & ingatan", "path": os.path.join(HOME, "memories", "MEMORY.md")},
    {"id": "user", "name": "USER.md", "desc": "Profil & preference user", "path": os.path.join(HOME, "memories", "USER.md")},
]

SKILLS_ROOT = os.path.join(HOME, "skills")

def read_text(path, limit=200000):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except Exception as e:
        return "Error membaca: %s" % e

def list_skills():
    """Senarai semua skill: category/name + description dari frontmatter."""
    out = []
    for root, dirs, files in os.walk(SKILLS_ROOT):
        if "SKILL.md" in files:
            p = os.path.join(root, "SKILL.md")
            rel = os.path.relpath(p, SKILLS_ROOT)
            parts = rel.split(os.sep)
            if len(parts) >= 2:
                category, name = parts[0], parts[1]
            else:
                category, name = "other", parts[0]
            desc = ""
            try:
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    head = f.read(3000)
                m = re.search(r"^description:\s*[\"']?(.+?)[\"']?\s*$", head, re.M)
                if m:
                    desc = m.group(1).strip()
            except Exception:
                pass
            out.append({"category": category, "name": name, "desc": desc, "path": p})
    out.sort(key=lambda x: (x["category"], x["name"]))
    return out

chat_workers = {}  # id -> {message, status, output, started}


def run_chat_worker(worker_id, msg):
    """Panggil hermes chat -q dalam thread; simpan output."""
    try:
        proc = subprocess.run(
            [HERMES, "chat", "-q", msg, "--source", "dashboard"],
            capture_output=True, timeout=300, text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        # ambil bahagian jawapan (selepas metadata hermes)
        chat_workers[worker_id]["output"] = out.strip()[-6000:]
        chat_workers[worker_id]["status"] = "done"
    except subprocess.TimeoutExpired:
        chat_workers[worker_id]["output"] = "⏱️ Timeout (300s) — mesej terlalu panjang/kompleks."
        chat_workers[worker_id]["status"] = "done"
    except Exception as e:
        chat_workers[worker_id]["output"] = "Error: %s" % e
        chat_workers[worker_id]["status"] = "done"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _handle_meta(self, path, u):
        """Proxy ke Zernio API — MULTI-CLIENT. `adAccountId` query param menentukan akaun.
        Default: JFR Auto (z.AD_ACCOUNT_ID) — supaya setup asal terus berfungsi."""
        try:
            import zernio_api as z
        except Exception as e:
            self._send(500, json.dumps({"error": "zernio import fail: %s" % e}))
            return
        try:
            qs = parse_qs(u.query)
            # akaun terpilih dari query; fallback JFR Auto
            adid = (qs.get("adAccountId", [""])[0] or z.AD_ACCOUNT_ID)
            from_date = qs.get("from", [""])[0] or ""
            to_date = qs.get("to", [""])[0] or ""
            FIELDS_C = "campaign_name,spend,impressions,reach,frequency,clicks,ctr,cpc,cpm,inline_link_clicks,inline_link_click_ctr,actions,cost_per_action_type,objective"
            FIELDS_A = FIELDS_C + ",adset_name,ad_name"
            # daily: WAJIB ada actions + inline_link_clicks supaya setiap metric chart
            # (Results/CTR/CPC) boleh dikira dari sumber SAMA dgn KPI/WoW/hierarchy
            FIELDS_DAILY = "ad_name,campaign_name,spend,impressions,clicks,reach,inline_link_clicks,actions,date_start"

            if path == "/api/meta/analysis":
                # AI Weekly Analysis tersimpan (override manual) — ONE SOURCE OF TRUTH
                aid2 = qs.get("adAccountId", [""])[0]
                f2 = qs.get("from", [""])[0]
                t2 = qs.get("to", [""])[0]
                self._send(200, json.dumps({"analysis": load_analysis(aid2, f2, t2)}))
                return
            if path == "/api/meta/accounts":
                # SEMUA connected ad accounts utk dropdown
                d = z.get_ad_accounts()
                accs = d.get("accounts", []) if isinstance(d, dict) else d
                out = []
                for a in accs or []:
                    out.append({"id": a.get("id"), "name": a.get("name"),
                                "currency": a.get("currency"),
                                "status": a.get("accountStatus"),
                                "timezone": a.get("timezoneName"),
                                "business": a.get("businessName"),
                                "tz_offset": a.get("timezoneOffsetHoursUtc")})
                out.sort(key=lambda x: (x.get("name") or "").lower())
                self._send(200, json.dumps({"accounts": out, "default": z.AD_ACCOUNT_ID,
                                             "default_name": "JFR Auto Ad"}))
            elif path == "/api/meta/overview":
                d = z.get_tree(adid, from_date or None, to_date or None)
                camps = d.get("campaigns", [])
                # aggregate
                total_spend = 0; total_impr = 0; total_clicks = 0; total_reach = 0
                total_wa = 0
                for c in camps:
                    m = c.get("metrics", {})
                    total_spend += m.get("spend", 0) or 0
                    total_impr += m.get("impressions", 0) or 0
                    total_clicks += m.get("clicks", 0) or 0
                    total_reach += m.get("reach", 0) or 0
                    acts = m.get("actions", {})
                    total_wa += (acts.get("onsite_conversion.messaging_conversation_started_7d", 0) or 0)
                fin = z.get_finance(adid)
                self._send(200, json.dumps({
                    "balance": fin.get("balance"),
                    "amountSpent": fin.get("amountSpent"),
                    "funding": (fin.get("fundingSource") or {}).get("displayString"),
                    "currency": fin.get("currency"),
                    "total_spend": round(total_spend, 2),
                    "total_impressions": total_impr,
                    "total_clicks": total_clicks,
                    "total_reach": total_reach,
                    "total_whatsapp": total_wa,
                    "campaign_count": len(camps),
                    "adAccountId": adid,
                    "campaigns": camps
                }))
            elif path == "/api/meta/campaigns":
                d = z.get_campaigns(adid)
                self._send(200, json.dumps(d))
            elif path == "/api/meta/finance":
                self._send(200, json.dumps(z.get_finance(adid)))
            elif path == "/api/meta/tree":
                self._send(200, json.dumps(z.get_tree(adid, from_date or None, to_date or None)))
            elif path == "/api/meta/insights":
                lvl = qs.get("level", ["campaign"])[0]
                obj = qs.get("objectId", [adid])[0]
                fld = FIELDS_A if lvl != "campaign" else FIELDS_C
                d = z.api_get("/ads/insights", {
                    "accountId": z.ZERNIO_ACCOUNT_ID,
                    "adAccountId": adid,
                    "objectId": obj,
                    "level": lvl,
                    "fields": fld,
                    "fromDate": from_date or "2026-08-01", "toDate": to_date or "2026-09-05",
                })
                self._send(200, json.dumps(d))
            elif path == "/api/meta/dash":
                # Dashboard penuh multi-client: tree + finance + insights campaign
                # + daily + previous-period (WoW) + ad-level (Top Ads)
                f = from_date or "2026-08-01"
                t = to_date or _time.strftime("%Y-%m-%d")
                # previous period = sama panjang, terus sebelum from
                try:
                    f0 = _time.mktime(_time.strptime(f, "%Y-%m-%d"))
                except Exception:
                    f0 = _time.time() - 30 * 86400
                try:
                    t0 = _time.mktime(_time.strptime(t, "%Y-%m-%d"))
                except Exception:
                    t0 = _time.time()
                span_days = max(1, int(round((t0 - f0) / 86400)) + 1)
                prev_to = f0 - 86400
                prev_from = prev_to - (span_days - 1) * 86400
                fmt = lambda ts: _time.strftime("%Y-%m-%d", _time.localtime(ts))
                tree = z.api_get("/ads/tree", {
                    "accountId": z.ZERNIO_ACCOUNT_ID, "adAccountId": adid,
                    "timeIncrement": "1", "fromDate": f, "toDate": t})
                fin = z.get_finance(adid)
                ins = z.api_get("/ads/insights", {
                    "accountId": z.ZERNIO_ACCOUNT_ID, "adAccountId": adid,
                    "objectId": adid, "level": "campaign", "fields": FIELDS_C, "limit": "500",
                    "fromDate": f, "toDate": t})
                prev_ins = z.api_get("/ads/insights", {
                    "accountId": z.ZERNIO_ACCOUNT_ID, "adAccountId": adid,
                    "objectId": adid, "level": "campaign", "fields": FIELDS_C, "limit": "500",
                    "fromDate": fmt(prev_from), "toDate": fmt(prev_to)})
                ads_ins = z.api_get("/ads/insights", {
                    "accountId": z.ZERNIO_ACCOUNT_ID, "adAccountId": adid,
                    "objectId": adid, "level": "ad", "fields": FIELDS_A, "limit": "500",
                    "fromDate": f, "toDate": t})
                daily_ins = z.api_get("/ads/insights", {
                    "accountId": z.ZERNIO_ACCOUNT_ID, "adAccountId": adid,
                    "objectId": adid, "level": "ad", "timeIncrement": "1",
                    "fields": FIELDS_DAILY, "limit": "500",
                    "fromDate": f, "toDate": t})
                self._send(200, json.dumps({"tree": tree, "finance": fin,
                                             "insights": ins,
                                             "prev_insights": prev_ins,
                                             "ads_insights": ads_ins,
                                             "daily_insights": daily_ins,
                                             "from": f, "to": t,
                                             "prev_from": fmt(prev_from), "prev_to": fmt(prev_to),
                                             "adAccountId": adid}))
            elif path == "/api/meta/pm":
                # Performance Marketing dashboard — current vs previous period
                t = to_date or _time.strftime("%Y-%m-%d")
                try:
                    _to = _time.strptime(t, "%Y-%m-%d")
                except Exception:
                    _to = _time.localtime()
                _from = _time.mktime(_to) - 6 * 86400
                f = from_date or _time.strftime("%Y-%m-%d", _time.localtime(_from))
                nd = _time.mktime(_to) - _time.mktime(_to) % 86400
                try:
                    f0 = _time.mktime(_time.strptime(f, "%Y-%m-%d"))
                except Exception:
                    f0 = nd - 6 * 86400
                span_days = max(1, int(round((nd - f0) / 86400)) + 1)
                prev_to = f0 - 86400
                prev_from = prev_to - (span_days - 1) * 86400
                fmt = lambda ts: _time.strftime("%Y-%m-%d", _time.localtime(ts))
                fields = FIELDS_A
                cur = z.api_get("/ads/insights", {
                    "accountId": z.ZERNIO_ACCOUNT_ID, "adAccountId": adid,
                    "objectId": adid, "level": "ad", "fields": fields, "limit": "500",
                    "fromDate": f, "toDate": t})
                prev = z.api_get("/ads/insights", {
                    "accountId": z.ZERNIO_ACCOUNT_ID, "adAccountId": adid,
                    "objectId": adid, "level": "ad", "fields": fields, "limit": "500",
                    "fromDate": fmt(prev_from), "toDate": fmt(prev_to)})
                daily = z.api_get("/ads/insights", {
                    "accountId": z.ZERNIO_ACCOUNT_ID, "adAccountId": adid,
                    "objectId": adid, "level": "ad", "timeIncrement": "1",
                    "fields": "ad_name,spend,impressions,clicks,reach,date_start", "limit": "500",
                    "fromDate": f, "toDate": t})
                fin = z.get_finance(adid)
                self._send(200, json.dumps({
                    "current": cur, "previous": prev, "daily": daily,
                    "finance": fin,
                    "from": f, "to": t,
                    "prev_from": fmt(prev_from), "prev_to": fmt(prev_to),
                    "adAccountId": adid}))
            else:
                self._send(404, json.dumps({"error": "meta endpoint tak jumpa"}))
        except Exception as e:
            self._send(500, json.dumps({"error": str(e)}))

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/api/meta/analysis":
            # Simpan AI Weekly Analysis (manual edit) — ONE SOURCE OF TRUTH
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                save_analysis(body.get("adAccountId"), body.get("from"), body.get("to"),
                              body.get("analysis") or {})
                self._send(200, json.dumps({"ok": True, "saved": True}))
            except Exception as e:
                try:
                    self._send(500, json.dumps({"error": str(e)}))
                except Exception:
                    pass
            return
        if u.path == "/api/chat":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                msg = body.get("message", "")[:2000]
                # chat worker — jalan dalam thread, poll status
                worker_id = str(int(_time.time() * 1000))
                chat_workers[worker_id] = {"message": msg, "status": "running",
                                           "output": "", "started": _time.time()}
                threading.Thread(target=run_chat_worker, args=(worker_id, msg),
                                 daemon=True).start()
                self._send(200, json.dumps({"worker_id": worker_id, "status": "running"}))
            except Exception as e:
                self._send(400, json.dumps({"error": str(e)}))
        elif u.path == "/api/meta/pdf":
            # Export PDF report — data datang dari frontend (dashboard state semasa)
            try:
                import meta_pdf
            except Exception as e:
                self._send(500, json.dumps({"error": "meta_pdf import fail: %s" % e}))
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                ctx = body.get("context", {})
                dash_data = body.get("data", {})
                if not ctx or not dash_data:
                    self._send(400, json.dumps({"error": "context/data diperlukan"}))
                    return
                pdf_path, fname = meta_pdf.generate(ctx, dash_data)
                with open(pdf_path, "rb") as f:
                    blob = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Disposition",
                                 "attachment; filename*=UTF-8''" + fname.replace(" ", "%20"))
                self.send_header("Content-Length", str(len(blob)))
                self.send_header("X-PDF-Filename", fname)
                self.end_headers()
                self.wfile.write(blob)
            except Exception as e:
                import traceback
                traceback.print_exc()
                print("[meta_pdf] ERROR:", e, flush=True)
                try:
                    self._send(500, json.dumps({"error": str(e)}))
                except Exception:
                    pass
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_GET(self):
        u = urlparse(self.path)
        path = u.path

        if path == "/api/chat/status":
            qs = parse_qs(u.query)
            wid = qs.get("id", [""])[0]
            w = chat_workers.get(wid)
            if not w:
                self._send(404, json.dumps({"error": "worker tak jumpa"}))
            else:
                self._send(200, json.dumps(w))
        elif path.startswith("/api/meta"):
            self._handle_meta(path, u)
        elif path == "/" or path == "/index.html":
            html = read_text(os.path.join(os.path.dirname(os.path.abspath(__file__)), "brain.html"))
            self._send(200, html, "text/html")
        elif path == "/api/brain":
            items = []
            for bf in BRAIN_FILES:
                content = read_text(bf["path"])
                items.append({"id": bf["id"], "name": bf["name"], "desc": bf["desc"],
                              "content": content, "chars": len(content)})
            self._send(200, json.dumps({"files": items}))
        elif path == "/api/skills":
            skills = list_skills()
            cats = {}
            for s in skills:
                cats.setdefault(s["category"], []).append(s)
            self._send(200, json.dumps({"categories": cats, "total": len(skills)}))
        elif path == "/api/skill":
            q = u.query
            qs = dict(x.split("=", 1) for x in q.split("&") if "=" in x)
            name = qs.get("name", "")
            # cari skill by name (unique)
            target = None
            for root, dirs, files in os.walk(SKILLS_ROOT):
                if "SKILL.md" in files and os.path.basename(root) == name:
                    target = os.path.join(root, "SKILL.md")
                    break
            if target:
                self._send(200, json.dumps({"name": name, "content": read_text(target)}))
            else:
                self._send(404, json.dumps({"error": "skill tidak jumpa"}))
        elif path == "/api/status":
            # status "Ana bekerja" — boleh extend: baca flag dari hermes
            busy = os.path.exists(os.path.join(HOME, "state.db"))
            self._send(200, json.dumps({"busy": busy}))
        elif path == "/api/activity":
            # Deteksi "Ana bekerja": mtime terkini fail2 otak + log gateway.
            # Kalau ada fail berubah dalam 3 minit lepas → Ana tengah buat kerja.
            candidates = [os.path.join(HOME, "state.db"),
                          os.path.join(HOME, "memories", "MEMORY.md"),
                          os.path.join(HOME, "memories", "USER.md"),
                          os.path.join(HOME, "logs", "agent.log")]
            newest = 0
            for c in candidates:
                try:
                    newest = max(newest, os.path.getmtime(c))
                except Exception:
                    pass
            import time as _t
            active = (newest and (_t.time() - newest) < 180)
            self._send(200, json.dumps({"last_change": newest, "active": active}))
        else:
            self._send(404, json.dumps({"error": "not found"}))

if __name__ == "__main__":
    print("Brain dashboard: http://127.0.0.1:%d" % PORT)
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
