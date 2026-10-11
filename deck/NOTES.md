# Take a Number: deck notes

> **DRAFT, written by Carl's AI agent on 2026-10-11. Carl has not said or approved these words.** The "say" lines are drafted for him to rewrite.

One HTML file, one 1920x1080 stage, one clock. Ten slides, four beats. Every number lives here or in the notes drawer (press `N`), never on a slide.

| File | What it is |
|---|---|
| `deck/index.html` | The deck. Self-contained (fonts and script inlined), opens by double-click, works offline |
| `deck/take-a-number.pdf` | The same ten slides at their last frame, one per page, 1920x1080 |
| `deck/src.html` | The source. Edit this, then run `deck/build.sh` |
| `deck/build.sh`, `deck/shoot.py` | Lint, inline, screenshot every slide into `deck/shots/`, print the PDF. Exits non-zero on any page error |
| `deck/video/take-a-number-demo.mp4`, `deck/video/captions.srt` | The silent captioned demo video and its captions. Made by `deck/video/render.py` (see "Video" below) |

## Keys

| Key | Does |
|---|---|
| Right, click | Finish the slide's motion, then go to the next slide |
| Left | Restart this slide, or go back one |
| Space | Pause or resume |
| a digit | Jump to that slide (type `1` `0` quickly for 10). End jumps to the ask |
| `N` | Notes drawer |
| `A` | Autoplay through all ten (99 seconds) |
| `R` | Replay this slide |

URL switches: `?autoplay=1` plays it through, `?print=1` stacks every slide for the PDF, `?t=<ms>` draws one instant (`window.seek(ms)` is the only clock). On a phone it stacks the slides with the notes under each.

## The story, in four beats

1. **Setup.** An agent recommended, then stopped one step short of the line, and the person lost the thing he wanted. A line still needs a person standing in it.
2. **The mechanism.** The agent takes a ticket. Five verbs between two agents. An offer is a lease, and it runs out.
3. **The proof.** One cancellation goes down a line of three tickets with nobody touching it. A confirm sent twice books once. The partner tools, stated as they are wired today.
4. **The ask.** Point your agent at a front desk.

## Slide by slide

| # | Beat | Headline on the slide | What moves | Seconds | Say (DRAFTED FOR CARL) |
|---|---|---|---|---|---|
| 1 | setup | Take a Number. | The dot strand surges, the name rises | 6 | "Your agent waits in line for you, and the front desk's agent calls your number." |
| 2 | setup | I recommended shifts. Then I stopped. | A night sweeps along a timeline; the three squares he wanted empty out | 13 | "This slide is my agent's account, not mine." Then read the timeline left to right. |
| 3 | setup | A line still needs a person standing in it. | A queue that does not move; a clock fills twice over one person | 8 | "An agent can search and recommend. The waiting is still yours." |
| 4 | mechanism | So your agent takes a ticket. | A deli ticket draws itself, the number lands, five verbs print | 12 | "Five verbs. Take a ticket. Ask your position. Get an offer. Accept or pass. Confirm." |
| 5 | mechanism | An offer is a lease. It runs out. | A row of dots empties; the slot hops to the next ticket, which answers in time | 10.5 | "That is a queue with leases and visibility timeouts, spoken agent to agent." Say the backend name here and nowhere else. |
| 6 | mechanism | Two agents. One line between them. | Two boxes draw themselves; messages run both ways | 9.5 | "One agent per person, one per service. The desk hears pass and a code. It never sees the calendar." |
| 7 | proof | Maria cancels. The line moves on its own. | The freed slot arcs to 41, which passes, then to 42, which books | 13 | "This is the run you just watched. Nobody refreshed a page. Nobody was put on hold." |
| 8 | proof | It confirms twice. It books once. | Two confirms travel to the desk; one booking square lights, once | 8.5 | "A retry with the same key gets the same booking back." |
| 9 | proof | Partner tools, as wired today. | Four rows, each with a mark and a status | 9.5 | "One real call so far, the Darwin search. The Dial text and the Keewano log are simulated, and the page says so." |
| 10 | ask | Point your agent at a front desk. | One request types itself; the reply appears | 9 | "One POST takes a ticket. Tell me which line you want opened next." |

Total as a self-playing cut: 99 seconds. In the 3-minute live demo only slides 2, 4, 5 and 10 are shown (see `DEMO.md`); the others are for questions and for the PDF.

## The story slide: the only facts it may use

Told by Carl's AI agent and labelled that way on the slide. No sentence is attributed to Carl.

- 2026-09-28: he asked his agent to pick volunteer shifts for a conference.
- The agent recommended shifts and stopped, waiting for him to click.
- He went to sleep.
- By morning the three shifts he wanted were gone.
- He wrote the rule down so no later agent would repeat it.

The twelve squares and which ones empty first are a drawing, not data. Do not add detail to this story on stage.

## Numbers, and where each one comes from

| Number | Value on 2026-10-11 | Source |
|---|---|---|
| Lease length | 60 s by default; the page offers 60 or 10; the API accepts 0.5 to 600 | `take_a_number/app.py` `ResetBody`, `docs/app.js` |
| Misses before a ticket is a no-show | 2 | `take_a_number/queue.py` `max_misses` |
| First ticket number in the demo | 41 | `take_a_number/front_desk.py` `first_ticket` |
| How often expired leases are swept | every 0.25 s | `take_a_number/app.py` `_sweep_forever` |
| Model timeout | 8 s per provider, up to three providers | `take_a_number/llm.py` |
| Gap between the two confirms | 0.4 s, same idempotency key | `take_a_number/line_sitter.py` |
| Maria run, message to booking | Median 4.7 s over 7 live runs (3.6 to 5.5 s). A recording made that morning showed 3.8 s; the recording has been remade since and a live run differs, so read it off the page | README "Latency", `scripts/latency.py`; `docs/replay.json` run `maria-3pm`, events 5 and 18 |
| One offer round | Median 1.5 s (1.1 to 1.9 s), of which the model call is 1.49 s and the protocol (two HTTP hops and the queue) 16 ms | README "Latency" |
| Model calls in that run | 3 (one at the desk, one per sitter that decided), each a little over 1 s, gemini-3.8-flash | `docs/replay.json` |
| Ticket 44, position 4 on slide 10 | The real reply a fourth agent gets after a reset | checked with curl against a local copy on 2026-10-11 |
| Tests | 55 pass with no network at 11:01 PDT on 2026-10-11. The count rose all morning; run `uv run pytest -q` again before quoting | `tests/` |
| "8.1 minutes per scheduling call" on the page | An assumption the page labels as one (Accenture, 2013). Do not quote it as a result | `docs/app.js` |

`docs/replay.json` was recorded several times on build day and its numbers change each time. Read the number from the page before saying one.

## Partner slide: keep it true

Slide 9 reads from the `PARTNERS` table at the top of the script in `deck/src.html`. Checked on 2026-10-11 against `GET /demo/health`, the README and the page:

| Tool | In the code | Live today |
|---|---|---|
| Dial | `integrations/dial.py`: sends one SMS and verifies inbound webhook signatures, written to their documented API | No. No real text has ever been sent. Needs `DIAL_API_KEY`, `DIAL_FROM_NUMBER`, `DEMO_TO_NUMBER`. Until then the page says "simulated, no text sent" |
| Darwin | `integrations/darwin.py`: one real, keyless search call shown on the page; builds the listing from the front desk's agent card and prints the two curl commands | Half. The search is a real call (on build day it returned no eligible match). The listing is not submitted; publishing is left to a person |
| Keewano | `integrations/keewano.py`, `eventlog.py`: every event appended to a log behind one small interface; the "how long, and why" answer is read from it | No. The backend is a JSONL file |
| Alike, Sparta | Not used | No |

If Dial goes live before the demo, change its row to `mark:'real'` and `status:'live, real texts'`, run `deck/build.sh`, and look at `deck/shots/09_end.png` before presenting.

## Video

`deck/video/take-a-number-demo.mp4`: silent, captions burned in, 1920x1080, 30 fps, H.264, 87.6 seconds, 5.4 MB. `deck/video/captions.srt` holds the same 17 captions with their timings.

What it shows, in order:

1. The deck's title slide (4 s).
2. The demo page in Replay, playing the recording **Maria cancels 3:00 PM**: ticket 41 passes, ticket 42 books, the confirm is sent twice.
3. The **On the wire** table, then **Asked of the event log** and **Partner tools in this run**.
4. The recording **Nobody answers: the lease runs out**: ticket 41's agent asks its person, the 10 second lease drains, Ana says no, Sam's agent books.
5. The deck's ask slide (5.5 s).

How it was made: `deck/video/render.py` serves `docs/` from a throwaway local server with no agents behind it, so the page can only play `docs/replay.json` (the recording made at 11:01 PDT on 2026-10-11). The script steps the page's clock one video frame at a time and takes a screenshot. Where a caption needs longer to read than the step stays on screen, the last frame is repeated, 8.8 seconds in total, so the video runs slower than the recorded run. Every number on the page is the recording's own. The partner caption is built from what the recording reports, not typed by hand.

What it does not show: a live run, a real text message, a published Darwin listing, or anything stored in Keewano. The corner tag reads "REPLAY OF A RECORDED RUN" for every second the page is on screen.

Rebuild (about 6 minutes; `--fps 5` writes a one-minute-to-render preview into `deck/video/_work/`):

```bash
/path/to/venv/bin/python deck/video/render.py
```

The script stops with a non-zero exit if the page is not in Replay, if a run does not end in a booking, or if the page shows a headline no caption was written for.

## Rebuild

```bash
deck/build.sh            # lint, inline, screenshots, PDF
deck/build.sh --pdf-only # PDF only
```

Needs the design kit at `/Users/carl/CODELocalProjects/lifeos/design/` and a Python that has Playwright with its headless Chromium: `PW=/path/to/venv/bin/python deck/build.sh`. Without one the script stops and says so. Headless only.
