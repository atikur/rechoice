"""A local page for blind gate-study-v2 labels, one rater at a time. Standard library only; nothing leaves this machine.

Shows each published render with the photo before it, the blouse bought on the same photo and the product
photo, in the registered shuffled order, without SKUs, roles or any gate output. Every answer is saved at once
to .local/gate-study-v2-labels-<rater>.json, so the page can be closed and reopened. Raters other than the owner
label under the addendum in assets/gate_labels_addendum.json; publish_human_labels publishes them under a pseudonym.

Run from the project root:  .venv/bin/python -I scripts/label_gate_study_local.py [port] [--rater ID]
"""
import argparse
import importlib.util
import json
import re
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "exchange" / "static"
STUDY = STATIC / "exchange" / "evidence" / "gate-study-v2"
RATER = "owner"
OUT = ROOT / ".local" / "gate-study-v2-labels-owner.json"


def output_for(rater):
    """Each rater's answers in a file of their own; the id is a short slug, never a full name."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,31}", rater):
        raise SystemExit("--rater must be a short id of lowercase letters, digits and hyphens, such as p1.")
    return ROOT / ".local" / f"gate-study-v2-labels-{rater}.json"

spec = importlib.util.spec_from_file_location("gate_labels", ROOT / "exchange" / "gate_labels.py")
labels = importlib.util.module_from_spec(spec)
spec.loader.exec_module(labels)

ITEMS = labels.items(ROOT, json.loads((STUDY / "report.json").read_text()),
                     json.loads((STATIC / "exchange/evidence/gate-study-v1/report.json").read_text()))
QUESTIONS = labels.questions(ROOT)
CAPTIONS = {"photo": "Before", "bought": "Blouse bought, same photo", "product": "Product photo", "preview": "Preview"}

PAGE = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Gate study labels</title>
<style>
:root{--bg:#f7f6f3;--card:#fff;--ink:#1d1d1f;--muted:#5d5d63;--line:#d9d7d0;--accent:#2f5d50}
@media (prefers-color-scheme:dark){:root{--bg:#141414;--card:#1f1f1f;--ink:#eee;--muted:#aaa;--line:#3a3a3a;--accent:#8cc5b3}}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 system-ui,sans-serif}
header{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);padding:12px 16px;z-index:1}
main{max-width:1200px;margin:auto;padding:16px}
.item{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin:0 0 24px}
.item.done{border-color:var(--accent)}
.imgs{display:flex;flex-wrap:wrap;gap:12px}
figure{margin:0;flex:0 0 auto}figure img{height:300px;max-width:100%;width:auto;display:block;border-radius:6px;cursor:zoom-in;object-fit:contain}
figcaption{font-size:14px;color:var(--muted);margin-top:4px}
fieldset{border:0;padding:0;margin:14px 0 0}legend{font-weight:600;margin-bottom:6px}
label{margin-right:18px;white-space:nowrap}input{accent-color:var(--accent)}
.big{position:fixed;inset:0;background:rgba(0,0,0,.85);display:none;align-items:center;justify-content:center}
.big img{max-width:96vw;max-height:96vh}
</style>
<header><strong>Gate study v2: blind labels by __RATER__</strong> · <span id="count"></span> · answers save as you click.
Judge from the images alone; <em>unclear</em> is a fine answer.</header>
<main id="list"></main><div class="big" id="big"><img alt=""></div>
<script>
const data = __DATA__;
const saved = __SAVED__;
const list = document.getElementById('list');
function count(){const done=data.items.filter(i=>i.questions.every(q=>saved[i.id]&&saved[i.id][q])).length;
  document.getElementById('count').textContent=done+' of '+data.items.length+' done';
  data.items.forEach(i=>document.getElementById('c-'+i.id).classList.toggle('done',i.questions.every(q=>saved[i.id]&&saved[i.id][q])));}
data.items.forEach((item,n)=>{const card=document.createElement('section');card.className='item';card.id='c-'+item.id;
  const h=document.createElement('h2');h.textContent='Item '+(n+1);card.append(h);
  const imgs=document.createElement('div');imgs.className='imgs';
  for(const role of ['photo','bought','product','preview']){if(!item.images[role])continue;const f=document.createElement('figure');
    const im=document.createElement('img');im.src='/img/'+n+'/'+role;im.alt=data.captions[role];im.loading='lazy';
    im.onclick=()=>{const b=document.getElementById('big');b.querySelector('img').src=im.src;b.style.display='flex'};
    const c=document.createElement('figcaption');c.textContent=data.captions[role];f.append(im,c);imgs.append(f)}
  card.append(imgs);
  for(const q of item.questions){const fs=document.createElement('fieldset');const lg=document.createElement('legend');lg.textContent=data.questions[q].text;fs.append(lg);
    for(const a of data.questions[q].answers){const l=document.createElement('label');const r=document.createElement('input');r.type='radio';r.name=item.id+'-'+q;r.value=a;
      r.checked=!!(saved[item.id]&&saved[item.id][q]===a);
      r.onchange=async()=>{saved[item.id]=saved[item.id]||{};saved[item.id][q]=a;count();
        await fetch('/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:item.id,question:q,answer:a})})};
      l.append(r,' '+a);fs.append(l)}
    card.append(fs)}
  list.append(card)});
document.getElementById('big').onclick=e=>e.currentTarget.style.display='none';
count();
</script></html>"""


def load():
    return json.loads(OUT.read_text()) if OUT.exists() else {"answers": {}}


class Handler(BaseHTTPRequestHandler):
    def send(self, body, kind, status=200):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/":
            # Image addresses are item positions, so neither the page nor its URLs carry a SKU or file name.
            shown = [{"id": str(n), "questions": i["questions"], "images": {role: True for role in i["images"]}}
                     for n, i in enumerate(ITEMS)]
            data = {"items": shown, "captions": CAPTIONS,
                    "questions": {k: {"text": q["text"], "answers": q["answers"]} for k, q in QUESTIONS.items()}}
            saved = {str(n): load()["answers"].get(i["id"], {}) for n, i in enumerate(ITEMS)}
            page = (PAGE.replace("__DATA__", json.dumps(data)).replace("__SAVED__", json.dumps(saved))
                    .replace("__RATER__", RATER))
            return self.send(page.encode(), "text/html; charset=utf-8")
        parts = url.path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "img" and parts[1].isdigit() and int(parts[1]) < len(ITEMS):
            path = ITEMS[int(parts[1])]["images"].get(parts[2])
            if path:
                return self.send((STATIC / path).read_bytes(), "image/jpeg")
        self.send(b"not found", "text/plain", 404)

    def do_POST(self):
        if self.path != "/save":
            return self.send(b"not found", "text/plain", 404)
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        n = str(body.get("id", ""))
        item = ITEMS[int(n)] if n.isdigit() and int(n) < len(ITEMS) else None
        if not item or body.get("question") not in item["questions"] or body.get("answer") not in QUESTIONS[body["question"]]["answers"]:
            return self.send(b"invalid", "text/plain", 400)
        saved = load()
        saved.update({"rater": RATER, "blind_to": "gate verdicts and measurements, catalog role and SKU",
                      "order_seed": labels.SEED, "updated_at_utc": datetime.now(timezone.utc).isoformat()})
        saved["answers"].setdefault(item["id"], {})[body["question"]] = body["answer"]
        OUT.parent.mkdir(exist_ok=True)
        OUT.write_text(json.dumps(saved, indent=1) + "\n")
        self.send(b"ok", "text/plain")

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Blind labels for gate-study-v2, saved locally per rater.")
    parser.add_argument("port", nargs="?", type=int, default=8020)
    parser.add_argument("--rater", default="owner", help="A short id such as p1; the owner labels as 'owner'.")
    args = parser.parse_args()
    RATER, OUT = args.rater, output_for(args.rater)
    print(f"{len(ITEMS)} items for rater {RATER} at http://127.0.0.1:{args.port}/ · answers in {OUT.relative_to(ROOT)}",
          flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
