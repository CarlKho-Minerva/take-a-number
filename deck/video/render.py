#!/usr/bin/env python3
"""Render the silent, captioned demo video from the REPLAY page, frame by frame.

  <python with playwright> deck/video/render.py            # 30 fps, the real thing
  <python with playwright> deck/video/render.py --fps 5    # quick look, same timing

What it films, in order:
  1. the deck's title slide            (deck/index.html, window.seek)
  2. docs/ in Replay, run "maria-3pm"   (the page's own timers, on a clock this script steps)
  3. the wire table, the event-log answer, the partner rows
  4. docs/ in Replay, run "lease-out"
  5. the deck's ask slide

Nothing is live. The page is served from docs/ by a throwaway local server with no agents behind it, so it can
only play docs/replay.json, a recording of real runs. Captions are drawn into the page before each screenshot,
so they are burned in. The same captions are written to captions.srt.

Fails loudly: a page error, a run that never reaches a booking, or a headline no caption matches stops the
render with a non-zero exit. Headless only.
"""
from __future__ import annotations

import datetime
import functools
import http.server
import json
import pathlib
import re
import shutil
import subprocess
import sys
import threading
import time

from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
DOCS = ROOT / "docs"
DECK = HERE.parent / "index.html"
FRAMES = HERE / "_frames"
W, H = 1920, 1080
FPS = int(sys.argv[sys.argv.index("--fps") + 1]) if "--fps" in sys.argv else 30
FINAL = FPS == 30  # anything else is a quick look and must not replace the real file
(HERE / "_work").mkdir(exist_ok=True)
OUT = HERE / "take-a-number-demo.mp4" if FINAL else HERE / "_work" / f"preview_{FPS}fps.mp4"
SRT = HERE / "captions.srt" if FINAL else HERE / "_work" / "captions_preview.srt"
TAG_PAGE = "SILENT DEMO<br>REPLAY OF A RECORDED RUN"
TAG_DECK = "SILENT DEMO"


class Film:
    def __init__(self) -> None:
        self.n = 0
        self.caps: list[tuple[float, str]] = []
        self.cap = None
        self.cap_n = 0
        self.frozen = 0.0
        self.errors: list[str] = []
        if FRAMES.exists():
            shutil.rmtree(FRAMES)
        FRAMES.mkdir(parents=True)

    @property
    def t(self) -> float:
        return self.n / FPS

    def shot(self, pg) -> None:
        pg.screenshot(path=str(FRAMES / f"{self.n:05d}.jpg"), type="jpeg", quality=92)
        self.n += 1

    def read_time(self) -> float:
        """Seconds the caption on screen still needs before it may change (3.2 words a second)."""
        if not self.cap:
            return 0.0
        return max(2.2, len(self.cap.split()) / 3.2) - (self.n - self.cap_n) / FPS

    def freeze(self, seconds: float) -> None:
        """Repeat the last frame. The page clock does not move, so nothing on screen is skipped."""
        last = FRAMES / f"{self.n - 1:05d}.jpg"
        for _ in range(frames(seconds)):
            shutil.copyfile(last, FRAMES / f"{self.n:05d}.jpg")
            self.n += 1
        self.frozen += seconds

    def caption(self, pg, text: str) -> None:
        if text == self.cap:
            return
        self.cap = text
        self.cap_n = self.n
        self.caps.append((self.t, text))
        pg.evaluate("t => { document.getElementById('__capt').textContent = t }", text)

    def watch(self, pg) -> None:
        pg.on("pageerror", lambda e: self.errors.append(str(e)))


def overlay(pg, scale: float, pad: int, tag: str) -> None:
    """A caption bar fixed to the bottom of the frame. Sizes are in CSS px times `scale`."""
    pg.evaluate(
        """([s, pad, tag]) => {
          const b = document.createElement('div'); b.id = '__cap';
          b.style.cssText = `position:fixed;left:0;right:0;bottom:0;height:${84*s}px;background:#07070a;border-top:1px solid rgba(180,200,230,.28);display:flex;align-items:center;justify-content:space-between;gap:${32*s}px;padding:0 ${pad}px;z-index:99999`;
          b.innerHTML = `<span id="__capt" style="font:400 ${25*s}px/1.25 Inter,system-ui,sans-serif;color:#f2f6ff;max-width:${1060*s}px"></span>` +
            `<span style="font:400 ${12*s}px/1.4 'JetBrains Mono',monospace;letter-spacing:.08em;color:#737f93;text-align:right;white-space:nowrap">${tag}</span>`;
          document.body.appendChild(b); document.body.style.paddingBottom = (84*s) + 'px';
        }""",
        [scale, pad, tag],
    )


def frames(seconds: float) -> int:
    return max(1, round(seconds * FPS))


# ---- the deck: window.seek(ms) is its only clock --------------------------------------------------------

def deck_segment(browser, film: Film, beat: int, seconds: float | None, text: str) -> None:
    ctx = browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=1)
    pg = ctx.new_page()
    film.watch(pg)
    pg.goto(DECK.as_uri() + "?t=0")
    pg.wait_for_function("window.__ready===true", timeout=15000)
    starts = pg.evaluate("window.BEAT_STARTS")
    total = pg.evaluate("window.DURATION_MS")
    t0 = starts[beat]
    t1 = (starts[beat + 1] if beat + 1 < len(starts) else total) - 1
    if seconds is not None:
        t1 = min(t1, t0 + seconds * 1000)
    pg.add_style_tag(content="#hud,#bar{display:none!important}")
    overlay(pg, 4 / 3, 160, TAG_DECK)
    film.cap = None
    film.caption(pg, text)
    for i in range(frames((t1 - t0) / 1000)):
        pg.evaluate(f"window.seek({t0 + i * 1000 / FPS:.2f})")
        film.shot(pg)
    ctx.close()


# ---- the page: its own timers, stepped by Playwright's clock ---------------------------------------------

class Stepper:
    """Advance the page's clock by exactly one video frame at a time."""

    def __init__(self, pg) -> None:
        self.pg = pg
        self.owed = 0.0

    def tick(self) -> None:
        self.owed += 1000 / FPS
        whole = int(self.owed)
        self.owed -= whole
        if whole:
            self.pg.clock.run_for(whole)


def pick(rules: list[tuple[str, str]], verdict: str) -> str | None:
    for pattern, text in rules:
        if re.search(pattern, verdict):
            return text
    return None


def play_run(pg, film: Film, step: Stepper, run_id: str, rules: list[tuple[str, str]], hold: float, on_frame=None) -> None:
    pg.click(f'[data-run="{run_id}"]')
    done_at = None
    limit = frames(75)
    for i in range(limit):
        step.tick()
        verdict = pg.inner_text("#verdict")
        text = pick(rules, verdict)
        if text is None:
            sys.exit(f"FAIL: run {run_id}: no caption matches the headline {verdict!r}. Add a rule in render.py.")
        if text != film.cap and film.n and film.read_time() > 0:
            film.freeze(film.read_time())  # hold the previous step until its caption can be read
        film.caption(pg, text)
        if on_frame:
            on_frame(pg, i)
        film.shot(pg)
        if done_at is None and re.search(r"retry got the same booking|Nobody took it|could not act|Something failed", verdict):
            if not re.search(r"retry got the same booking", verdict):
                sys.exit(f"FAIL: run {run_id} ended on {verdict!r}, not on a booking. Not filming that as a success.")
            done_at = i
        if done_at is not None and i - done_at >= frames(hold) and film.read_time() <= 0:
            return
    sys.exit(f"FAIL: run {run_id} did not reach a booking within 75 s of page time")


def scroll_to(pg, film: Film, step: Stepper, selector: str | None, seconds: float, offset: int = 18) -> None:
    y0 = pg.evaluate("window.scrollY")
    y1 = 0 if selector is None else pg.evaluate(
        "([s, o]) => Math.max(0, Math.min(document.documentElement.scrollHeight - innerHeight, document.querySelector(s).getBoundingClientRect().top + scrollY - o))",
        [selector, offset])
    n = frames(seconds)
    for i in range(1, n + 1):
        u = i / n
        e = 4 * u ** 3 if u < 0.5 else 1 - (-2 * u + 2) ** 3 / 2
        pg.evaluate(f"window.scrollTo(0, {y0 + (y1 - y0) * e:.1f})")
        step.tick()
        film.shot(pg)


def hold(pg, film: Film, step: Stepper, seconds: float, text: str | None = None) -> None:
    if text is not None:
        film.caption(pg, text)
    for _ in range(frames(seconds)):
        step.tick()
        film.shot(pg)


def partner_caption(run: dict, search: dict | None = None) -> str:
    """Say only what the recording itself reports about the partner tools."""
    reset = next((e for e in run["events"] if e["type"] == "reset"), None)
    flags = (reset or {}).get("integrations")
    if not flags:
        sys.exit("FAIL: the recording does not say how the partner tools were wired; not captioning a guess")
    dial = "no text was sent" if flags["dial"].get("simulated") else "the question went out as a real text"
    darwin = "a listing was published" if flags["darwin"].get("listed") else "no listing was published"
    keewano = "the log is a plain file" if flags["keewano"].get("simulated") else "the log is in KeewanoDB"
    real_search = bool(search and search.get("ok") and not search.get("simulated"))
    lead = "one real Darwin search, " if real_search else ""
    return f"Partner tools in this run: {lead}{dial}, {darwin}, and {keewano}."


def page_segment(browser, film: Film, base: str, replay: dict) -> None:
    runs = {r["id"]: r for r in replay["runs"]}
    for need in ("maria-3pm", "lease-out"):
        if need not in runs:
            sys.exit(f"FAIL: docs/replay.json has no run {need!r}; runs are {list(runs)}")
    ctx = browser.new_context(viewport={"width": 1600, "height": 900}, device_scale_factor=1.2, reduced_motion="reduce")
    pg = ctx.new_page()
    film.watch(pg)
    t0 = datetime.datetime(2026, 10, 11, 12, 0, 0)
    pg.clock.install(time=t0)
    pg.goto(base + "/index.html?mode=replay")
    pg.clock.pause_at(t0 + datetime.timedelta(seconds=30))  # from here the page's time moves only when this script says
    for _ in range(100):  # the page boots on real fetches and fake timers: step both
        pg.clock.run_for(100)
        if pg.query_selector('[data-run="maria-3pm"]'):
            break
        time.sleep(0.05)
    else:
        sys.exit("FAIL: the Replay buttons never appeared")
    status = pg.inner_text("#modeStatus")
    if "replay" not in status.lower():
        sys.exit(f"FAIL: the page is not in Replay (status reads {status!r}); refusing to label it a replay")
    overlay(pg, 1.1, 248, TAG_PAGE)
    step = Stepper(pg)
    film.cap = None
    recorded = replay.get("recorded_at", "")

    intro = "Three agents hold tickets at a clinic. The clinic and the people are invented."
    hold(pg, film, step, 4.2, intro)

    # run 1: one cancellation, one pass, one booking
    freed = "A patient texts that she cannot make 3:00 PM. The front desk's agent frees the slot."
    passed = "Its person is presenting at that hour. The agent passes and keeps its place."
    booked = "Ticket 42's agent checks the calendar, accepts and confirms. Booked."
    maria = [
        (r"Three people are in line", intro),
        (r"A message reaches", freed),
        (r"is free\. Offering", freed),
        (r"Ticket 41 has \d+ seconds", "Ticket 41 is first. The slot is held for it for 60 seconds: a lease."),
        (r"Ticket 41 passes", passed),
        (r"Ticket 42 has \d+ seconds", passed),
        (r"Ticket 42 accepts", booked),
        (r"Ticket 42 has the", booked),
        (r"retry got the same booking", "The confirm was sent twice on purpose, with the same key. Still one booking."),
    ]
    play_run(pg, film, step, "maria-3pm", maria, hold=0.8)

    scroll_to(pg, film, step, "#wireHead", 0.8)
    hold(pg, film, step, 4.0, "Every message between the agents. Grey rows stayed on the person's side.")
    scroll_to(pg, film, step, "#logHead", 0.8)
    hold(pg, film, step, 4.5, "The event log, read back: how long the ticket waited, and why another passed.")
    hold(pg, film, step, 6.6, partner_caption(runs["maria-3pm"], replay.get("darwin_search")))
    scroll_to(pg, film, step, None, 0.8)

    # run 2: a lease that runs out
    events = runs["lease-out"]["events"]
    first = next((e for e in events if e["type"] == "decision" and e.get("ticket") == 41), {})
    lease = int(runs["lease-out"].get("lease_seconds", 10))
    opened = f"Second recording: a 9:30 AM slot, and a {lease} second lease."
    asked = ("The model did not answer here, so ticket 41's agent asks its person instead of guessing."
             if first.get("source") == "code" else
             "Ticket 41's agent cannot know if the visit is urgent, so it asks its person one question.")
    ran_out = "Nobody answers. The lease runs out, ticket 41 keeps its place, and the slot moves on."
    ana = "Ticket 42's agent asks too. Ana says no (recorded) and keeps her place."
    sam = "Sam wrote 'any time of day works'. His agent accepts without asking."
    out = [
        (r"Three people are in line", opened),
        (r"A message reaches", opened),
        (r"is free\. Offering", opened),
        (r"Ticket 41 has \d+ seconds", opened),
        (r"Ticket 41's agent asks", asked),
        (r"Ticket 41's lease ran out", ran_out),
        (r"Ticket 42 has \d+ seconds", ran_out),
        (r"Ticket 42's agent asks", ana),
        (r"Ana says", ana),
        (r"Ticket 42 passes", ana),
        (r"Ticket 43 has \d+ seconds", sam),
        (r"Ticket 43 accepts", sam),
        (r"Ticket 43 has the", sam),
        (r"retry got the same booking", "Booked, and again the confirm sent twice made one booking."),
    ]
    state = {"down": False}

    def follow(pg_, i):  # nudge the view down once a question box opens, so it is on screen
        if not state["down"] and pg_.query_selector(".ticket .ask"):
            state["down"] = True
            pg_.evaluate("window.scrollTo(0, 92)")

    play_run(pg, film, step, "lease-out", out, hold=0.8, on_frame=follow)
    ctx.close()
    print(f"page segment done at {film.t:.1f}s (recording made {recorded})", flush=True)


def write_srt(film: Film) -> None:
    def stamp(t: float) -> str:
        ms = int(round(t * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"

    lines = []
    for k, (t, text) in enumerate(film.caps):
        end = film.caps[k + 1][0] if k + 1 < len(film.caps) else film.t
        lines.append(f"{k + 1}\n{stamp(t)} --> {stamp(end)}\n{text}\n")
    SRT.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    replay = json.loads((DOCS / "replay.json").read_text())
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a, **k):  # the 404 for demo/health is expected: there are no agents here
            pass

    handler = functools.partial(Quiet, directory=str(DOCS))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    film = Film()
    started = time.time()
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--allow-file-access-from-files"])
        deck_segment(browser, film, 0, 3.8, "Silent demo. The page that follows plays a recording of a real run.")
        print(f"title done at {film.t:.1f}s", flush=True)
        page_segment(browser, film, base, replay)
        deck_segment(browser, film, 9, 5.5, "Take a Number: a deli ticket, spoken between agents.")
        browser.close()
    server.shutdown()
    if film.errors:
        sys.exit("FAIL: page errors during render: " + "; ".join(film.errors)[:600])
    write_srt(film)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", str(FRAMES / "%05d.jpg"),
                    "-vf", "scale=in_range=pc:out_range=tv,format=yuv420p", "-color_range", "tv", "-colorspace", "bt709",
                    "-color_primaries", "bt709", "-color_trc", "bt709",
                    "-c:v", "libx264", "-crf", "20", "-preset", "medium", "-r", "30",
                    "-movflags", "+faststart", str(OUT)], check=True)
    if "--keep-frames" not in sys.argv:
        shutil.rmtree(FRAMES)
    print(f"playback was held for {film.frozen:.1f}s in total so captions could be read")
    print(f"OK {film.n} frames at {FPS} fps, {film.t:.1f}s of video, {time.time() - started:.0f}s to render -> {OUT} ({OUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
