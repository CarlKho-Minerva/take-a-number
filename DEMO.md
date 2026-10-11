# Take a Number: demo script

> **DRAFT, written by Carl's AI agent on 2026-10-11. Carl has not said or approved these words.** Every line under "you say" is drafted for him to rewrite in his own voice. Nothing here has been sent, posted or submitted.

Your agent waits in line for you, and the front desk's agent calls your number. Underneath it is a queue with leases and visibility timeouts, spoken agent to agent.

Everything below runs on one laptop. The clinic (Corner Clinic), its schedule and the three people in line (Dev, Ana, Sam) are invented fixtures. What runs live: the line itself (tickets, leased offers, pass, confirm with an idempotency key), real HTTP between the Front Desk agent and three Line Sitter agents on the loopback socket, and real model calls for the two jobs the model has. What is simulated today: the text message to the person (Dial), the directory listing (Darwin) and the sequence database (Keewano). The page says "simulated" next to each one. Say that first.

## Before you walk up (5 minutes)

| # | Do this | Ready when |
|---|---|---|
| 1 | `cd ~/CODELocalProjects/hack-1011/take-a-number && uv run uvicorn take_a_number.app:app --host 127.0.0.1 --port 8041` | `curl -s localhost:8041/demo/health` prints `"ok":true` and a non-empty `model_providers` list |
| 2 | Browser tab A: `http://127.0.0.1:8041/?mode=live` | Header reads `live, model: gemini then ...` and the headline reads **Three people are in line.** |
| 3 | Browser tab B: `deck/index.html` (double-click it; it is one self-contained file) | Title slide with the dot strand. Press `N` once to check the notes drawer, `N` again to hide it |
| 4 | In tab A press **Reset**, leave the lease on `lease 60 s` | Tickets 41, 42, 43 all read `waiting` |

Zoom tab A so the headline, the buttons, the Front desk table and the three ticket cards fit without scrolling (Cmd-minus once or twice on a projector).

Deck keys: Right finishes the slide or goes to the next, a digit jumps to that slide, End jumps to the ask, `N` shows notes.

Kill switch: the **Replay** button in the page header. It plays a recording of a real run from `docs/replay.json` and needs no network.

## The 3-minute cut

Start on tab A. The words are a draft; the clicks and the screen text are what the build does today.

| clock | you click | you say (DRAFTED FOR CARL) | the screen shows |
|---|---|---|---|
| 0:00 | nothing | "Every waitlist still needs a person standing in it: refresh the page, call back, click within minutes or lose it." | **Three people are in line.** Tickets 41, 42, 43 |
| 0:08 | **Maria cancels 3:00 PM** | "A patient texts that she cannot make three o'clock tomorrow. Nobody at the clinic touches anything from here." | **A message reaches the front desk.** Then `read as: cancel, Maria, <tomorrow> 15:00` and **3:00 PM is free. Offering it down the line.** |
| 0:16 | nothing | "Ticket 41 is first. Its agent gets the slot, held for sixty seconds. Its person is presenting then, so it passes and keeps its place." | **Ticket 41 has 60 seconds to answer.** A row of lease dots drains on card 41. Then **Ticket 41 passes and keeps its place.** with the reason and the facts it cited |
| 0:26 | nothing | "Ticket 42 is next. Its agent checks the calendar and the travel time, accepts, and confirms." | **Ticket 42 accepts.** Then **Ticket 42 has the 3:00 PM slot.** with the seconds it took. The schedule row for 3:00 PM reads `rebooked` |
| 0:36 | nothing; point at the last two rows of **On the wire** | "It sent that confirm twice on purpose. Same key, same booking. One booking, not two." | **The retry got the same booking back. Still 1.** Last wire row: `confirm (the same request again, sent on purpose)` with `replayed: true` |
| 0:46 | point at the grey rows | "Grey rows never left the person's side. The clinic heard 'pass'. It never saw the calendar." | `10 sent between agents, 4 kept private (grey)` (counts vary a little per run) |
| 0:55 | tab B, press `2` | "This slide is my agent's account, not mine. September 28: I asked it to pick volunteer shifts for a conference. It recommended shifts and stopped, waiting for me to click. I went to sleep. By morning the three I wanted were gone." | Slide 2: **I recommended shifts. Then I stopped.** A night passes on a timeline and three squares empty out |
| 1:15 | press `4` | "So the agent takes a ticket, like at a deli. Five verbs. Take a ticket. Ask your position. Get an offer. Accept or pass. Confirm." | Slide 4: a ticket with the number 42, the five verbs printing one at a time |
| 1:30 | press `5` | "An offer is a lease. It runs out. That is a queue with leases and visibility timeouts, spoken agent to agent. One slow agent cannot block everyone behind it." | Slide 5: a row of dots empties, the slot moves to the next ticket |
| 1:45 | tab A. Set the dropdown to `lease 10 s` (changing it resets the line), then **Tom cancels 9:30 AM** | "The hard case: a 9:30 slot. Two of these people wrote 'no mornings before ten unless it is urgent.' The agent cannot know that, so it must not guess." | **3 waiting, lease 10 s**. Then **Ticket 41's agent asks its person.** Card 41 shows one question with **Yes** and **No** |
| 2:00 | do not answer for Dev. Let the dots drain | "It asks its person one yes or no question. I am not going to answer. Ten seconds." | Lease dots drain on card 41. Then **Ticket 41's lease ran out. Next ticket.** Card 41: `missed 1 of 2, kept position 1` |
| 2:12 | on card 42 press **No** within the ten seconds | "Ana's agent asks too. She says no. She keeps her place." | **Ana says no.** Card 42: `passed, kept position 2` |
| 2:20 | nothing | "Sam wrote 'any time of day works.' His agent takes it without asking anyone." | **Ticket 43 has the 9:30 AM slot.** with the seconds it took, then **The retry got the same booking back. Still 1.** |
| 2:28 | scroll to **Asked of the event log** and **Partner tools in this run** | "Every event goes to a log, and the page answers 'how long, and why' from it. Partner tools: Dial sends that question as a real text, Darwin lists this desk so agents can find it, Keewano holds the log. Today the page marks all three simulated." | Two plain sentences read back from the log, with event numbers. Three partner rows, each with its status |
| 2:42 | tab B, press `End` | "Point your agent at a front desk. One POST takes a ticket. Tell me which line you want opened next." | Slide 10: **Point your agent at a front desk.** `POST /desk/a2a/take_ticket` types itself out |
| 3:00 | stop | | |

Never fill the lease countdown with talk you did not plan. The ten quiet seconds at 2:00 are the demo.

## The 60-second cut

Tab A only until the last line. Lease stays on 60 s.

| clock | you click | you say (DRAFTED FOR CARL) | the screen shows |
|---|---|---|---|
| 0:00 | nothing | "Every waitlist still needs a person standing in it. Take a Number lets your agent stand there instead." | **Three people are in line.** |
| 0:06 | **Maria cancels 3:00 PM** | "A patient cancels by text. The clinic's agent frees the slot and offers it to ticket 41, held for sixty seconds." | **3:00 PM is free. Offering it down the line.** then **Ticket 41 has 60 seconds to answer.** |
| 0:18 | nothing | "41 is presenting then, so it passes and keeps its place. 42 accepts and confirms." | **Ticket 41 passes and keeps its place.** then **Ticket 42 has the 3:00 PM slot.** |
| 0:30 | point at the last wire row | "It confirmed twice on purpose. One booking. That is the backend idea: a queue with leases and visibility timeouts, spoken agent to agent." | **The retry got the same booking back. Still 1.** |
| 0:42 | nothing | "The clinic and the people are made up. The line, the messages and the model calls are real. The partner tools are marked simulated today." | Footer and the **Partner tools in this run** rows |
| 0:52 | tab B, press `End` | "Point your agent at a front desk. One POST takes a ticket. Tell me which line to open next." | Slide 10 |
| 1:00 | stop | | |

## If the Wi-Fi dies

The agents run on this laptop, so the page stays up. Only the model calls need the internet.

Say (DRAFTED FOR CARL): "The Wi-Fi just dropped, so I am switching to a recording of a real run from this morning. Same page, same events, no network."

Then press **Replay** in the header. The status line changes to `replay of a recorded run`, and three buttons appear: **Maria cancels 3:00 PM**, **Tom cancels 9:30 AM (agents must ask)**, **Nobody answers: the lease runs out**. Press the first for the 0:08 beat and the third for the 1:45 beat. In Replay the Yes and No buttons are disabled and show what was answered in the recording, marked `(recorded)`. Do not claim a Replay run is live.

If you pressed a Live button before noticing: the headline reads **The desk could not act on that. Staff get it.** and the Front desk box says the model did not answer. That is the designed behaviour, not a crash: the desk does not guess. It can take up to 24 seconds to appear (three model providers, 8 seconds each). Say so, then press **Replay**.

## If something breaks

- The **Live** button is greyed out: the server is not running. Run step 1 again and reload. Until then the page is already in Replay.
- A red line `Could not reach the agents ... Switch to Replay to keep going.`: do what it says.
- The headline reads **Something failed. See the last line on the wire.**: read that row aloud, press **Reset**, run it again once. If it fails twice, **Replay**.
- A ticket's agent asks a question you did not expect: models vary a little between runs. Press **Yes** or **No** on the card and keep talking. The line rules are plain code and do not vary.
- The server will not start: `python3 -m http.server 8755 --directory docs`, open `http://127.0.0.1:8755/`. It opens in Replay with no agents.
- No laptop at all: the hosted page at the demo URL is the same Replay.

## What to say when asked

Three questions a founder who builds agent products is likely to ask. Answers are drafted and short on purpose.

**"Is this a booking app?"**
No. A booking app owns both sides. This is the wire between two agents that have never met: an agent card, five verbs, and a lease so one slow agent cannot hold a slot hostage. The clinic keeps its own schedule and only has to run the line. The demo page is the consumer face; the protocol is the product.

**"What does the model do, and what happens when it is wrong or down?"**
It does two jobs. At the desk it turns a free-text cancellation into a name, a date and a time; plain code then has to match exactly one appointment, or the message goes to staff. For the person it judges an offer against their written rules, and it must cite the facts it used; code checks every citation and computes calendar overlaps itself, so a model cannot accept over a conflict. If the model is down the agent never accepts on its own: a hard overlap becomes a pass, anything else becomes a question for the person. There is a `model outage (simulated)` checkbox on the page to show it.

**"What is real and what is simulated?"**
Real: the line rules (unit tests with a clock moved by hand), HTTP between the agents on one machine, the model calls, the idempotent confirm. Invented: the clinic, the schedule, the three people. Simulated today: the text to the person (the Dial adapter is written to their documented API and sends only when a key and two numbers are set), the Darwin listing (the file and the commands are generated, nothing is submitted), and Keewano (the event log is a JSONL file behind one small interface). Not built: identity. Today a ticket is tied to an agent URL and a ticket secret, and nothing proves who is behind the URL.

## For the technical judge

```bash
curl -s localhost:8041/.well-known/agent-card.json            # the front desk's card: five skills, lease length, where offers are pushed
curl -s localhost:8041/sitters/ana/.well-known/agent-card.json # one line sitter's card
curl -s -X POST localhost:8041/desk/a2a/take_ticket -H 'Content-Type: application/json' \
  -d '{"agent_url":"http://127.0.0.1:9999/me","label":"judge"}'  # your own ticket: number, position, ticket_secret, lease_seconds
curl -s localhost:8041/demo/line                                # the whole line: tickets, slots, offers, bookings
uv run pytest -q                                                # the line rules, with a clock moved by hand
```

A ticket taken with curl has no agent listening at its URL. When a slot reaches it the desk reports `could not reach ticket N's agent`, counts a miss, and moves on. That is the designed behaviour.
