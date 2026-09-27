"""Record actual Chromium workflows; run with advice-ai-lab's installed Python environment.

The browser is isolated, the server stores data in memory, and the backend is scripted.
No retrieval model is imported here. See the committed protocol before running.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

import httpx
import uvicorn
from filenote.config import Settings
from filenote.corpus import generate_corpus
from filenote.fake import Corruption, GoldFakeChatModel
from filenote.web import create_app
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts" / "controlled-pilot-v1"

MONITOR = r"""({epoch}) => {
  const visible = (el) => {
    if (!el || !el.checkVisibility()) return false;
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) return false;
    let l=Math.max(0,r.left), t=Math.max(0,r.top), rr=Math.min(innerWidth,r.right), b=Math.min(innerHeight,r.bottom);
    for(let p=el.parentElement;p;p=p.parentElement){
      const s=getComputedStyle(p), pr=p.getBoundingClientRect();
      if(/auto|scroll|hidden|clip/.test(s.overflowX)){l=Math.max(l,pr.left);rr=Math.min(rr,pr.right);}
      if(/auto|scroll|hidden|clip/.test(s.overflowY)){t=Math.max(t,pr.top);b=Math.min(b,pr.bottom);}
    }
    return Math.max(0,rr-l)*Math.max(0,b-t)/(r.width*r.height)>=0.5;
  };
  const q=(s)=>document.querySelector(s);
  window.pilotSamples=[];
  const badge=document.createElement('div');
  badge.style='position:fixed;bottom:0;left:0;z-index:10000;background:#172b4d;color:white;padding:3px 8px;font:12px monospace;pointer-events:none';
  document.body.appendChild(badge);
  window.pilotTimer=setInterval(()=>{
    const stamp=Date.now()/1000-epoch;
    badge.textContent='AI controlled pilot | synthetic meeting | silent | '+stamp.toFixed(1)+'s';
    window.pilotSamples.push({time:stamp,states:{
      transcript_before_draft:visible(q('#transcript')) && !!q('#transcript').value && q('#workspace').hidden,
      unsupported_visible:visible(q('.claim.unsupported .flag')),
      editor_visible:visible(q('.claim.unsupported .editor')),
      approval_visible:visible(q('#approval-meta')) && visible(q('#approval-diff')) && /Approved by/.test(q('#approval-meta').textContent),
      verified_strategy_visible:visible(q('#strategy')) && q('#strategy').value==='verified',
      highlight_visible:visible(q('.segment.highlight')),
      feedback_comment_visible:visible(q('#feedback-comment')) && !!q('#feedback-comment').value && q('#thumbs-down').getAttribute('aria-pressed')==='true',
      feedback_recorded_visible:visible(q('#status')) && /Feedback recorded/.test(q('#status').textContent),
      never:false
    }});
  },100);
}"""


def write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ARTIFACTS)
    parser.add_argument("--chromium", type=Path, help="Optional existing Chromium executable; no download")
    args = parser.parse_args()
    protocol = json.loads((ARTIFACTS / "protocol.json").read_text(encoding="utf-8"))
    if any((args.output / t["asset_id"] / "source.webm").exists() for t in protocol["tasks"]):
        raise SystemExit("Refusing to overwrite an existing pilot recording")
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "D:/ms-playwright")
    corpus = generate_corpus(12, 11)
    model = GoldFakeChatModel(
        {m.id: m for m in corpus},
        corruption=Corruption(p=1.0, seed=0, kinds=frozenset({"invented_decision", "repair_keep"})),
    )
    app = create_app(Settings(), model=model)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(url + "/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    else:
        raise RuntimeError("Temporary app did not start")
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True, executable_path=str(args.chromium) if args.chromium else None
            )
            for task in protocol["tasks"]:
                out = args.output / task["asset_id"]
                out.mkdir(parents=True, exist_ok=True)
                context = browser.new_context(
                    viewport={"width": 1280, "height": 900},
                    color_scheme="light",
                    record_video_dir=str(out),
                    record_video_size={"width": 1280, "height": 900},
                )
                epoch = time.time()
                page = context.new_page()
                page.set_default_timeout(20000)
                page.goto(url)
                page.evaluate(MONITOR, {"epoch": epoch})
                events = []

                def event(
                    name: str, hold: float = 0, *, events=events, epoch=epoch, task=task, page=page
                ) -> None:
                    events.append({"time": time.time() - epoch, "event": name})
                    print(task["asset_id"], name, round(time.time() - epoch, 2), flush=True)
                    if hold:
                        page.wait_for_timeout(hold * 1000)

                event("app_loaded", 3)
                page.fill("#transcript", corpus[task["meeting_index"]].transcript.to_text())
                event("transcript_pasted", 9)
                page.select_option("#strategy", "verified")
                event("verified_strategy_selected", 9)
                page.click("#draft-button")
                page.wait_for_function(
                    "() => /Draft ready/.test(document.querySelector('#status').textContent)"
                )
                event("draft_ready", 6)
                if task["asset_id"] == "correction-approval":
                    flag = page.locator(".claim.unsupported")
                    flag.scroll_into_view_if_needed()
                    event("unsupported_claim_visible", 10)
                    flag.locator(".edit-button").click()
                    page.fill(
                        ".claim.unsupported .editor",
                        "Client agreed to nothing further (AI controlled test correction).",
                    )
                    event("correction_in_editor", 10)
                    page.click(".save-edit")
                    page.wait_for_function("() => !document.querySelector('#approve-button').disabled")
                    event("correction_saved", 5)
                    page.fill("#approver", "ai-pilot@example.invalid")
                    page.click("#approve-button")
                    page.wait_for_selector("#approval:not([hidden])")
                    page.locator("#approval").scroll_into_view_if_needed()
                    event("approval_and_diff_visible", 16)
                else:
                    chip = page.locator(".claim:not(.unsupported) .chip").first
                    chip.click()
                    page.wait_for_selector(".segment.highlight")
                    event("evidence_segment_highlighted", 13)
                    page.click("#thumbs-down")
                    page.fill("#feedback-comment", "The decision needs review against its cited transcript.")
                    event("needs_work_comment_entered", 13)
                    page.click("#feedback-button")
                    page.wait_for_function(
                        "() => /Feedback recorded/.test(document.querySelector('#status').textContent)"
                    )
                    # The application status lives at the top of the page.
                    page.locator("#status").scroll_into_view_if_needed()
                    event("feedback_confirmation_visible", 15)
                samples = page.evaluate(
                    "() => {clearInterval(window.pilotTimer); return window.pilotSamples;}"
                )
                event("recording_end")
                video = page.video
                context.close()
                assert video is not None
                generated = Path(video.path())
                source = out / "source.webm"
                generated.rename(source)
                probe = subprocess.check_output(
                    ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(source)],
                    text=True,
                )
                write(
                    out / "events.json",
                    {
                        "clock": "seconds since page creation request; <=0.25s video alignment uncertainty",
                        "events": events,
                        "samples": samples,
                    },
                )
                write(
                    out / "source.json",
                    {
                        "asset_id": task["asset_id"],
                        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                        "bytes": source.stat().st_size,
                        "ffprobe": json.loads(probe),
                        "browser": browser.version,
                        "operator": "AI via Playwright",
                        "content": "seeded synthetic meeting; scripted app model; no private data",
                        "audio": False,
                        "epoch_utc": epoch,
                    },
                )
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)


if __name__ == "__main__":
    main()
