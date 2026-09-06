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
        """Proxy ke Zernio API untuk Meta Ads dashboard (JFR Auto)."""
        try:
            import zernio_api as z
        except Exception as e:
            self._send(500, json.dumps({"error": "zernio import fail: %s" % e}))
            return
        try:
            if path == "/api/meta/overview":
                d = z.get_tree()
                camps = d.get("campaigns", [])
                # aggregate
                total_spend = 0; total_impr = 0; total_clicks = 0; total_reach = 0
                total_wa = 0; total_ctr_w = 0
                for c in camps:
                    m = c.get("metrics", {})
                    total_spend += m.get("spend", 0) or 0
                    total_impr += m.get("impressions", 0) or 0
                    total_clicks += m.get("clicks", 0) or 0
                    total_reach += m.get("reach", 0) or 0
                    acts = m.get("actions", {})
                    total_wa += (acts.get("onsite_conversion.messaging_conversation_started_7d", 0) or 0)
                fin = z.get_finance()
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
                    "campaigns": camps
                }))
            elif path == "/api/meta/campaigns":
                d = z.get_campaigns()
                self._send(200, json.dumps(d))
            elif path == "/api/meta/finance":
                self._send(200, json.dumps(z.get_finance()))
            elif path == "/api/meta/tree":
                self._send(200, json.dumps(z.get_tree()))
            elif path == "/api/meta/insights":
                qs = parse_qs(u.query)
                from_date = qs.get("from", [""])[0] or "2026-08-01"
                to_date = qs.get("to", [""])[0] or "2026-09-05"
                lvl = qs.get("level", ["campaign"])[0]
                obj = qs.get("objectId", [z.AD_ACCOUNT_ID])[0]
                d = z.api_get("/ads/insights", {
                    "accountId": z.ZERNIO_ACCOUNT_ID,
                    "adAccountId": z.AD_ACCOUNT_ID,
                    "objectId": obj,
                    "level": lvl,
                    "fields": "campaign_name,adset_name,ad_name,spend,impressions,reach,frequency,clicks,ctr,cpc,cpm,inline_link_clicks,inline_link_click_ctr,actions,cost_per_action_type,objective",
                    "fromDate": from_date, "toDate": to_date,
                })
                self._send(200, json.dumps(d))
            elif path == "/api/meta/dash":
                # Dashboard penuh: tree (dgn daily) + finance + insights campaign
                qs = parse_qs(u.query)
                from_date = qs.get("from", [""])[0] or "2026-08-01"
                to_date = qs.get("to", [""])[0] or "2026-09-05"
                tree = z.api_get("/ads/tree", {
                    "accountId": z.ZERNIO_ACCOUNT_ID,
                    "adAccountId": z.AD_ACCOUNT_ID,
                    "timeIncrement": "1",
                    "fromDate": from_date, "toDate": to_date,
                })
                fin = z.get_finance()
                ins = z.api_get("/ads/insights", {
                    "accountId": z.ZERNIO_ACCOUNT_ID,
                    "adAccountId": z.AD_ACCOUNT_ID,
                    "objectId": z.AD_ACCOUNT_ID,
                    "level": "campaign",
                    "fields": "campaign_name,spend,impressions,reach,frequency,clicks,ctr,cpc,cpm,inline_link_clicks,inline_link_click_ctr,actions,cost_per_action_type,objective",
                    "fromDate": from_date, "toDate": to_date,
                })
                self._send(200, json.dumps({"tree": tree, "finance": fin,
                                             "insights": ins,
                                             "from": from_date, "to": to_date}))
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
