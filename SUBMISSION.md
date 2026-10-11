# Take a Number: submission text

> **DRAFT, written by Carl's AI agent on 2026-10-11. Carl has not said or approved these words, and nothing here has been submitted.** Paste, then rewrite in your own voice. Every claim was checked against the code and the recorded run on 2026-10-11; if the build changes, re-check the section "What is real and what is simulated".

## Project name

Take a Number

## Tagline (79 characters)

Your agent waits in line for you, and the front desk's agent calls your number.

Alternates, both under 100 characters:

- A deli ticket for agents: take a number, get an offer on a timer, confirm once.
- Waitlists still need a person standing in them. This lets your agent stand there.

## Links

- Repo: https://github.com/CarlKho-Minerva/take-a-number (placeholder until Carl pushes)
- Demo: https://carlkho-minerva.github.io/take-a-number/ (placeholder until GitHub Pages is turned on; it serves `docs/`, which opens in Replay)
- Deck: `deck/index.html` (one self-contained file), PDF at `deck/take-a-number.pdf`
- Script: `DEMO.md`

## 100-word description

Waitlists still need a person standing in them: refresh, call back, click within minutes or lose the slot. Take a Number is a deli-ticket protocol between two agents. Your agent, the Line Sitter, takes a ticket. The service's agent, the Front Desk, offers a freed slot to one ticket at a time under a lease that expires. Your agent accepts, or passes and keeps its place, then confirms with a key that makes a retry safe. In the demo a patient cancels by text and the slot is rebooked in about five seconds. Nobody at the clinic touches anything.

## 300-word description

Theme: give people their time back.

Finding an open slot is easy for an agent. Waiting for one is still a human job: stay near the phone, refresh the page, answer within minutes. Carl's own agent showed the gap on September 28: it recommended volunteer shifts, stopped to wait for a click, and by morning the three he wanted were gone.

Take a Number puts the agent in the line. It is a small protocol between a person's agent (the Line Sitter) and a service's agent (the Front Desk), with five verbs: take_ticket, position, offer, accept or pass, confirm.

The backend idea is a queue with leases and visibility timeouts, spoken agent to agent. A freed slot is offered to one ticket at a time, held under a lease. If the agent accepts, it confirms with an idempotency key, so a retry returns the same booking. If it passes, the slot moves on and the ticket keeps its place. If the lease runs out, the slot moves on anyway. One slow agent cannot block everyone behind it.

The model has two narrow jobs: read a free-text cancellation into a name, date and time, and judge an offer against rules the person wrote in their own words. Plain code does the rest. It matches the cancellation to exactly one appointment or hands it to staff. It checks that every reason cites a written fact. It computes calendar overlaps itself. When the agent cannot know, it asks its person one yes or no question. When the model is down, it never accepts on its own.

The demo is a clinic with three people in line. The clinic and the people are invented. The line, the HTTP between agents and the model calls are real. It is agent infrastructure with a consumer face.

## What it does

A patient texts a clinic: "hi this is Maria I can't make my 3pm tomorrow". The Front Desk agent reads the message, matches it to exactly one appointment and frees the slot. Three people's agents are already in line with tickets 41, 42 and 43.

- Ticket 41 is offered the slot under a 60 second lease. Its person is presenting at that hour, so the agent passes and keeps its place.
- Ticket 42's agent checks the calendar and the travel time, accepts, and confirms. The slot is booked.
- The confirm is sent a second time on purpose with the same key. The desk returns the same booking. One booking, not two.

Measured on 2026-10-11 over seven live runs on one laptop, message to booking took a median of 4.7 seconds (3.6 to 5.5), nearly all of it model time. The protocol itself added a median of 16 milliseconds per offer round (`scripts/latency.py`; the numbers are in the README).

A second scenario covers what an agent should not decide alone. A 9:30 AM slot opens, and two people wrote "no mornings before 10 unless it is urgent". Their agents do not know whether the visit is urgent, so each asks its person one yes or no question. If nobody answers, the lease runs out, the ticket gets one miss and keeps its place, and the slot goes to the next ticket. Two misses and the ticket is marked a no-show and leaves the line.

The page shows the front desk's schedule, the three tickets with their lease countdowns, every message on the wire (the ones that stayed on the person's side are grey), and two sentences read back from the event log: how long the booked ticket waited, and why the other one passed.

## How we built it

- **The line** (`take_a_number/queue.py`): plain Python with no model, no network and no wall clock. The clock is passed in, so every rule is tested by moving time by hand. Tickets in order, one leased offer per slot, pass without losing your place, confirm keyed by an idempotency key, misses and no-shows.
- **Two agents** (`front_desk.py`, `line_sitter.py`): they talk over real HTTP on the loopback socket, and each publishes an agent card at `/.well-known/agent-card.json`. The desk pushes offers to the sitter's own URL; the sitter posts accept, pass and confirm back with a ticket secret.
- **One model seam** (`llm.py`): Gemini 3.8 Flash first, then OpenAI, then Azure OpenAI, eight seconds each, JSON only. If none answers the caller has to handle it out loud: the desk hands the message to staff, the sitter passes on a hard overlap or asks its person.
- **Guards in code**: the model's reason must cite fact ids that exist; code computes calendar overlaps and turns "accept over a conflict" into a question; the reason sentence stays on the person's side and the desk receives only pass and a coarse code.
- **The page** (`docs/`): one HTML file, one script, no framework. One function turns the event transcript into what you see. Live mode polls the running agents. Replay mode plays `docs/replay.json`, an unedited recording of real live runs, and needs no network.
- **Stack**: Python 3.11+, FastAPI, uvicorn, httpx, pydantic, pytest, uv.

## What is real and what is simulated

| Part | Status |
|---|---|
| The line: tickets, leases, pass, idempotent confirm, misses, no-show | **Real.** Plain code. 49 tests pass with no network (`uv run pytest -q`), 25 of them on the queue alone |
| Messages between the Front Desk and the Line Sitters | **Real HTTP**, on one machine, over the loopback socket. Not yet across two hosts |
| Model calls (reading the cancellation, judging an offer) | **Real** in Live mode. Bring your own key in `.env` |
| Replay mode | **A recording of real live runs**, saved by `scripts/record.py` and not edited. The page labels it as a replay |
| Corner Clinic, its schedule, Dev, Ana, Sam, Maria, Tom | **Invented** for the demo |
| "Human phone minutes not spent" on the page | **An assumption**, labelled as one on the page (8.1 minutes per scheduling call, Accenture 2013), with a field to change it |
| The text message to the person (Dial) | **Simulated today. No real text has ever been sent by this code.** The adapter is written to Dial's documented API, untested against the live service, and sends a real SMS only when `DIAL_API_KEY`, `DIAL_FROM_NUMBER` and `DEMO_TO_NUMBER` are set. Without them nothing leaves the machine and the page says "simulated, no text sent" |
| Darwin | **Search is real, listing is not published.** The page makes one keyless call to Darwin's search and shows what came back (on build day: nothing eligible). The listing file and the two commands to publish it are generated from the agent card. Nothing is submitted by the code |
| The sequence database (Keewano) | **Simulated.** Events go to a JSONL file behind one small interface. KeewanoDB Local had no public install on build day |
| Identity | **Not built.** A ticket is tied to an agent URL and a ticket secret. Nothing proves who is behind the URL |

## Partner tools used

Stated as they stood on 2026-10-11, checked against `GET /demo/health`, the README and the page.

| Tool | What it is for here | State |
|---|---|---|
| Dial | When a Line Sitter must ask its person, it texts the one yes or no question; a Y or N reply answers it. Inbound webhooks are signature-checked | Adapter written, **simulated until three values are set**. No real text sent yet |
| Darwin | Makes the Front Desk findable, so a person's agent can ask where the line for a clinic is and get its agent card back | **Search is a real call** (keyless, shown on the page). Listing generated, **not submitted** |
| Keewano | Holds the complete event sequence, to answer "what happened to ticket 42, in order, and why" | Seam cut, **JSONL behind it** |
| Alike, Sparta | | **Not used** |

If Dial goes live before judging, change this section and slide 9 of the deck. Until a real text has been received on a phone, do not claim "best use of Dial". The honest partner claim today is Darwin search, and it returned no match.

## What is next

1. A real Dial number, so the one question arrives as a text and a Y reply books the slot.
2. A public Darwin listing, so an agent that has never seen this clinic can find its line.
3. A second Front Desk that is not a clinic (a DMV office, a restaurant, a volunteer roster), to show the same five verbs hold.
4. Two hosts instead of one, and identity for the agent behind a ticket.
5. KeewanoDB behind the event log interface once it can be installed.

## Needed from Carl

Only a person can supply these. The first three decide whether the submission is complete; the rest only matter if you want them.

1. **Your words.** Read the "you say" column in `DEMO.md` and rewrite it in your own voice. About 340 words for the 3-minute cut and 120 for the 60-second cut, as drafted. Nothing drafted here should be read out as if you wrote it.
2. **Push and publish.** Create the public repo `CarlKho-Minerva/take-a-number`, push `main`, and turn on GitHub Pages from `/docs`. Both links above are placeholders until then. Nothing was pushed or uploaded by the agents.
3. **A demo video in the form's required format.** `deck/video/take-a-number-demo.mp4` is a silent captioned cut made from the Replay page (the "Video" section of `deck/NOTES.md` says what it shows and how it was made). If the form wants narration, either record yourself running the 60-second cut (screen plus microphone, 1920x1080, MP4 H.264, 60 to 90 seconds, under 100 MB), or record a voiceover only (one take, 60 to 75 seconds, M4A or WAV, mono, 48 kHz, quiet room) and ask for it to be laid over the silent cut.
4. **Dial, only if you want a real text in the demo.** Your Dial API key, a Dial from-number, and the phone number that should receive the text, in E.164 form (`+1XXXXXXXXXX`). Put them in `.env` as `DIAL_API_KEY`, `DIAL_FROM_NUMBER`, `DEMO_TO_NUMBER`; never in the repo or in chat. Add `DIAL_WEBHOOK_SECRET` if you want the Y or N reply to come back by text, which also needs a public URL for the webhook.
5. **Darwin, only if you want the listing public.** Your decision and a `DARWIN_API_KEY`. The two commands are printed at `GET /demo/darwin`.
6. **A photo, if the form asks.** One selfie or team photo, square, at least 800 by 800 pixels, JPG or PNG, taken at the venue.
7. **Form fields only you know.** Team member names and emails, which partner prizes to enter, and the submit click itself.
