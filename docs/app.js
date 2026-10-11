/* Take a Number demo page. No build step, no framework.
 *
 * One function, apply(event), turns the transcript into what you see. LIVE
 * mode feeds it events polled from the running agents. REPLAY mode feeds it
 * the same kind of events from replay.json, a recording of a real LIVE run.
 * Events are shown at reading speed; the T column is the real clock.
 */
(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const params = new URLSearchParams(location.search);
  const FAST = params.has('fast');

  // How long each kind of event stays on screen before the next one is shown.
  const DWELL = { reset: 0, ticket_taken: 150, cancellation_received: 1100, cancellation_parsed: 900, slot_freed: 900, offer: 900,
    deciding: 500, decision: 1500, escalation: 400, human_answer: 600, wire: 550, passed: 350, held: 350, confirmed: 1100, lease_expired: 1100 };
  const dwell = (type) => (FAST ? 5 : DWELL[type] ?? 450);

  // Source for the default minutes per phone call. An assumption the viewer can change.
  const CALL_SOURCE = 'https://www.slideshare.net/AdamBurke5/accenturewhyfirstimpressionsmatterhealthcareprovidersscheduling';

  const ui = { mode: null, liveOk: false, health: null, replay: null, run: 0, queue: [], timer: null, poll: null, lastSeq: 0,
    busy: false, minPerCall: 8.1, wireRendered: 0, replayRunId: null, lease: 60, modelOff: false, error: null };
  let S = blank();

  function blank() {
    return { clinic: 'Corner Clinic', today: '', schedule: [], people: {}, order: [], byTicket: {}, lease: 60, maxMisses: 2, wire: [],
      verdict: 'Three people are in line.', fail: false, desk: { msg: null, channel: null, read: null, staff: null },
      offers: 0, tCancel: null, slot: null, booked: null, retry: null, modelMs: [], integrations: null, answer: null, started: false, done: false };
  }

  // ---- small formatters -------------------------------------------------

  const clock = (iso) => {
    const [h, m] = iso.slice(11, 16).split(':').map(Number);
    return `${((h + 11) % 12) + 1}:${String(m).padStart(2, '0')} ${h < 12 ? 'AM' : 'PM'}`;
  };
  const dayName = (iso) => new Date(iso.slice(0, 10) + 'T12:00:00').toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
  const daysBetween = (a, b) => Math.round((new Date(a.slice(0, 10) + 'T12:00:00') - new Date(b.slice(0, 10) + 'T12:00:00')) / 86400000);
  const person = (ticket) => S.people[S.byTicket[ticket]];
  const actorName = (actor) => {
    if (actor === 'desk') return 'front desk';
    if (actor && actor.startsWith('sitter:')) return `${S.people[actor.slice(7)]?.name ?? actor.slice(7)}'s agent`;
    return actor || '';
  };
  const compact = (obj) => esc(JSON.stringify(obj).replace(/"([a-z_]+)":/g, '$1: ').replace(/,/g, ', '));

  function wire(e, from, to, html, cls) {
    S.wire.push({ t: e.t_ms, from, to, html, cls: cls || '' });
  }

  // ---- the one place events become state --------------------------------

  function apply(e) {
    const p = e.ticket != null ? person(e.ticket) : null;
    switch (e.type) {
      case 'reset': {
        S = blank();
        ui.wireRendered = 0;
        $('wire').innerHTML = '';
        Object.assign(S, { clinic: e.clinic, today: e.today, schedule: e.schedule.map((a) => ({ ...a })), lease: e.lease_seconds,
          maxMisses: e.max_misses, integrations: e.integrations, modelOff: e.model_forced_off });
        for (const who of e.people) {
          S.people[who.id] = { ...who, ticket: null, position: null, status: 'no ticket', misses: 0 };
          S.order.push(who.id);
        }
        break;
      }
      case 'ticket_taken': {
        const who = Object.values(S.people).find((x) => x.name === e.label);
        if (who) { who.ticket = e.ticket; who.position = e.position; who.status = 'waiting'; S.byTicket[e.ticket] = who.id; }
        wire(e, `${e.label}'s agent`, 'front desk', `<b>take_ticket</b> returns ticket ${e.ticket}, position ${e.position}`);
        break;
      }
      case 'text_in':
        wire(e, e.simulated ? 'a text (simulated)' : 'a text (Dial)', actorName(e.routed_to), esc(e.text));
        break;
      case 'cancellation_received':
        Object.assign(S, { tCancel: e.t_ms, booked: null, retry: null, done: false, started: true, fail: false, slot: null, modelMs: [], answer: null });
        S.desk = { msg: e.text, channel: e.channel, read: null, staff: null };
        S.verdict = 'A message reaches the front desk.';
        wire(e, 'a patient', 'front desk', `&ldquo;${esc(e.text)}&rdquo;`);
        break;
      case 'cancellation_parsed':
        S.desk.read = e;
        S.modelMs.push(e.ms);
        wire(e, 'front desk', 'itself', `model read it as ${compact(e.parsed)} in ${e.ms} ms (${esc(e.model)})`, 'private');
        break;
      case 'needs_staff':
        S.desk.staff = e.why;
        S.verdict = 'The desk could not act on that. Staff get it.';
        S.fail = true; S.done = true;
        wire(e, 'front desk', 'staff', `not acted on: ${esc(e.why)}`);
        break;
      case 'slot_freed': {
        S.slot = e.slot;
        const row = S.schedule.find((a) => a.slot_id === e.slot.slot_id);
        if (row) { row.patient = null; row.status = 'free'; row.hot = true; }
        S.verdict = `${clock(e.slot.start)} is free. Offering it down the line.`;
        wire(e, 'front desk', 'itself', `matched exactly one appointment (${esc(e.freed_from)}) and freed <b>${clock(e.slot.start)}</b>`, 'private');
        break;
      }
      case 'offer':
        S.offers += 1;
        if (p) Object.assign(p, { status: 'offered', leaseStart: performance.now(), leaseSeconds: e.lease_seconds, reason: null, question: null,
          delivery: null, answer: null, source: null, evidence: null, offerId: e.offer_id });
        S.verdict = `Ticket ${e.ticket} has ${Math.round(e.lease_seconds)} seconds to answer.`;
        wire(e, 'front desk', `ticket ${e.ticket}`, `<b>offer</b> ${esc(e.offer_id)}: ${dayName(e.slot.start)} ${clock(e.slot.start)}, lease ${Math.round(e.lease_seconds)} s`);
        break;
      case 'deciding':
        if (p) p.status = 'deciding';
        break;
      case 'decision':
        if (p) {
          Object.assign(p, { reason: e.reason, source: e.source, evidence: e.evidence, model: e.model, ms: e.ms });
          if (e.decision === 'escalate') { p.status = 'asked'; p.question = e.question; }
        }
        if (e.ms) S.modelMs.push(e.ms);
        S.verdict = e.decision === 'pass' ? `Ticket ${e.ticket} passes and keeps its place.`
          : e.decision === 'accept' ? `Ticket ${e.ticket} accepts.` : `Ticket ${e.ticket}'s agent asks its person.`;
        wire(e, actorName(e.actor), 'its person only', `<b>${esc(e.decision)}</b> (${esc(e.source)}${e.ms ? `, ${e.ms} ms` : ''}): ${esc(e.reason)}`, 'private');
        break;
      case 'escalation':
        if (p) { p.status = 'asked'; p.question = e.question; p.delivery = e.delivery; }
        wire(e, actorName(e.actor), p ? p.name : 'its person',
          `${e.delivery?.simulated ? 'text not sent (simulated)' : esc(`text ${e.delivery?.sent ? 'sent to ' + e.delivery.to : 'failed: ' + (e.delivery?.error || 'page only')}`)}: &ldquo;${esc(e.delivery?.body || e.question)}&rdquo;`, 'private');
        break;
      case 'human_answer':
        if (p) p.answer = e.answer;
        S.verdict = `${p ? p.name : 'The person'} says ${e.answer}.`;
        wire(e, p ? p.name : 'person', actorName(e.actor), `<b>${esc(e.answer)}</b>`, 'private');
        break;
      case 'escalation_unanswered':
        wire(e, actorName(e.actor), 'nobody', 'no answer from its person; it does nothing and lets the lease run out', 'private');
        break;
      case 'wire': {
        const retry = e.retry_on_purpose ? ' <b>(the same request again, sent on purpose)</b>' : '';
        wire(e, actorName(e.actor), 'front desk', `<b>${esc(e.verb)}</b>${retry} ${compact(e.request)} returns ${e.status} ${compact(e.response)}`);
        break;
      }
      case 'held':
        if (p) p.status = 'held';
        break;
      case 'passed':
        if (p) { p.status = 'passed'; p.position = e.position; p.leaseStart = null; }
        break;
      case 'confirmed':
        if (!e.replayed) {
          if (p) { p.status = 'booked'; p.leaseStart = null; p.position = null; p.bookedSlot = S.slot; }
          const row = S.schedule.find((a) => a.slot_id === e.slot_id);
          if (row && p) { row.patient = p.name; row.status = 'booked'; }
          (e.waiting || []).forEach((t, i) => { const w = person(t); if (w) w.position = i + 1; });
          S.booked = { ticket: e.ticket, t: e.t_ms, id: e.booking_id, who: p };
          S.verdict = `Ticket ${e.ticket} has the ${S.slot ? clock(S.slot.start) : ''} slot. ${((e.t_ms - S.tCancel) / 1000).toFixed(1)} s.`;
        } else {
          S.retry = { id: e.booking_id, count: e.bookings_for_slot };
          S.verdict = `The retry got the same booking back. Still ${e.bookings_for_slot}.`;
          S.done = true;
        }
        break;
      case 'lease_expired':
        if (p) { p.status = e.ticket_status === 'no_show' ? 'no_show' : 'missed'; p.misses = e.misses; p.leaseStart = null; p.position = e.position; }
        S.verdict = `Ticket ${e.ticket}'s lease ran out. Next ticket.`;
        wire(e, 'front desk', 'itself', `lease on ${esc(e.offer_id)} ran out: ticket ${e.ticket} has ${e.misses} of ${e.max_misses} misses, ${e.ticket_status === 'no_show' ? 'marked no-show and out of line' : 'keeps its place'}`, 'private');
        break;
      case 'offer_undeliverable':
        if (p) { p.status = e.ticket_status === 'no_show' ? 'no_show' : 'missed'; p.misses = e.misses; p.leaseStart = null; }
        wire(e, 'front desk', 'itself', `could not reach ticket ${e.ticket}'s agent (${esc(e.error)}); counted as a miss`, 'private');
        break;
      case 'too_late':
        if (p) p.reason = 'Too late: the lease had run out.';
        break;
      case 'slot_unfilled':
        S.verdict = 'Nobody took it. The slot goes back to normal booking.';
        S.done = true;
        wire(e, 'front desk', 'itself', `asked tickets ${esc((e.asked || []).join(', ') || 'nobody')}; slot returned to normal booking`, 'private');
        break;
      case 'agent_error':
      case 'integration_error':
        S.verdict = 'Something failed. See the last line on the wire.';
        S.fail = true;
        wire(e, actorName(e.actor), 'this page', `error: ${esc(e.message)}`);
        break;
      default:
        break;
    }
    render();
  }

  // ---- drawing ----------------------------------------------------------

  const ST = {
    'no ticket': ['off', () => 'no ticket yet'],
    waiting: ['', (p) => `waiting, position ${p.position}`],
    offered: ['running', () => 'offered'],
    deciding: ['running', () => 'agent deciding'],
    asked: ['warn', () => 'asked its person'],
    held: ['running', () => 'held'],
    passed: ['', (p) => `passed, kept position ${p.position}`],
    booked: ['', (p) => `booked ${p.bookedSlot ? clock(p.bookedSlot.start) : ''}`],
    missed: ['warn ring', (p) => `missed ${p.misses} of ${S.maxMisses}, kept position ${p.position}`],
    no_show: ['fail ring', () => 'no-show, out of line'],
  };

  function leaseLeft(p) {
    if (!p.leaseStart) return null;
    return Math.max(0, p.leaseSeconds - (performance.now() - p.leaseStart) / 1000);
  }

  function leaseDots(p) {
    const left = leaseLeft(p);
    if (left == null) return '';
    const on = Math.ceil((left / p.leaseSeconds) * 30);
    let dots = '';
    for (let i = 0; i < 30; i += 1) dots += `<i class="${i < on ? 'on' : ''}${i === on - 1 ? ' last' : ''}"></i>`;
    return `<div><div class="lease" id="lease-${p.id}" role="img" aria-label="lease, ${Math.ceil(left)} of ${Math.round(p.leaseSeconds)} seconds left">${dots}</div>
      <span class="meta" id="left-${p.id}">lease: ${Math.ceil(left)} s of ${Math.round(p.leaseSeconds)} left</span></div>`;
  }

  function renderTickets() {
    const tomorrow = S.schedule[0]?.start.slice(0, 10);
    $('tickets').innerHTML = S.order.map((id) => {
      const p = S.people[id];
      const [cls, word] = ST[p.status] || ['', () => p.status];
      const frame = ['offered', 'deciding', 'asked', 'held'].includes(p.status) ? 'offered' : p.status === 'booked' ? 'booked' : p.status === 'no_show' ? 'gone' : '';
      const cal = p.calendar.filter((c) => c.start.slice(0, 10) === tomorrow).map((c) => `<li class="cal">${clock(c.start)} to ${clock(c.end)} ${esc(c.title)}</li>`).join('');
      let ask = '';
      if (p.question && (p.status === 'asked' || p.answer)) {
        const can = ui.mode === 'live' && p.status === 'asked' && !p.answer;
        const note = p.delivery ? (p.delivery.simulated ? 'Dial is simulated here: no text was sent. Answer on the page.' : p.delivery.sent ? `Texted to ${esc(p.delivery.to)} through Dial. Reply there, or answer here.` : `Text failed (${esc(p.delivery.error || 'unknown')}). Answer here.`) : '';
        ask = `<div class="ask"><span class="eyebrow">One question for ${esc(p.name)}</span><span class="q">${esc(p.question)}</span>
          <div class="row"><button class="btn" data-answer="yes" data-pid="${p.id}" ${can ? '' : 'disabled'} ${p.answer === 'yes' ? 'aria-pressed="true"' : ''}>Yes</button>
          <button class="btn btn--ghost" data-answer="no" data-pid="${p.id}" ${can ? '' : 'disabled'} ${p.answer === 'no' ? 'aria-pressed="true"' : ''}>No</button></div>
          <span class="meta">${p.answer ? `${esc(p.name)} answered ${esc(p.answer)}${ui.mode === 'replay' ? ' (recorded)' : ''}.` : esc(note)}</span></div>`;
      }
      const reason = p.reason ? `<div><div class="reason">${esc(p.reason)}</div><span class="meta">decided by ${esc(p.source)}${p.evidence?.length ? `, citing ${esc(p.evidence.join(', '))}` : ''}${p.ms ? `, ${p.ms} ms` : ''}</span></div>` : '';
      return `<article class="ticket ${frame}" aria-label="Ticket ${p.ticket ?? ''} held by ${esc(p.name)}'s agent">
        <div class="ticket-top"><span class="numeral">${p.ticket ?? '..'}</span><span class="st ${cls}">${esc(word(p))}</span></div>
        <div><div class="who">${esc(p.name)}'s agent</div><span class="meta">${esc(p.visit_reason)}, now booked ${dayName(p.current_appointment)}</span></div>
        ${leaseDots(p)}${reason}${ask}
        <div><span class="eyebrow">In ${esc(p.name)}'s words</span><ul>${p.constraints.map((c, i) => `<li><span class="meta">c${i + 1}</span> ${esc(c)}</li>`).join('')}</ul></div>
        <div><span class="eyebrow">Calendar, ${tomorrow ? dayName(tomorrow) : ''}</span><ul>${cal || '<li class="cal">nothing</li>'}</ul></div>
      </article>`;
    }).join('');
    const waiting = S.order.filter((id) => ['waiting', 'passed', 'missed', 'offered', 'deciding', 'asked', 'held'].includes(S.people[id].status)).length;
    $('lineMeta').textContent = `${waiting} waiting, lease ${Math.round(S.lease)} s, ${S.maxMisses} misses is a no-show`;
  }

  function renderDesk() {
    const d = S.desk;
    let top = '<p class="empty">No messages yet. The desk is holding tomorrow\'s schedule and a line of three tickets.</p>';
    if (d.msg) {
      top = `<span class="eyebrow">Message in (${esc(d.channel || 'page')})</span><p class="msg">&ldquo;${esc(d.msg)}&rdquo;</p>`;
      if (d.read) {
        const r = d.read.parsed;
        top += `<p class="read">read as: ${esc(r.intent)}${r.patient_name ? ', ' + esc(r.patient_name) : ''}${r.date ? ', ' + esc(r.date) : ''}${r.time ? ' ' + esc(r.time) : ''}<br>${esc(d.read.model)}, ${d.read.ms} ms. Matching it to the schedule is plain code.</p>`;
      } else if (!d.staff) {
        top += '<p class="read"><span class="st running">reading the message</span></p>';
      }
      if (d.staff) top += `<p class="empty err">Not acted on: ${esc(d.staff)}.</p>`;
    }
    const rows = S.schedule.map((a) => `<tr class="${a.hot ? 'hot' : ''}"><td class="num">${clock(a.start)}</td><td>${esc(a.with)}</td>
      <td class="t">${a.patient ? esc(a.patient) : ''}</td><td><span class="st ${a.status === 'free' ? 'running' : a.hot ? '' : 'off'}">${a.status === 'free' ? 'free' : a.hot ? 'rebooked' : 'booked'}</span></td></tr>`).join('');
    $('desk').innerHTML = `${top}<span class="eyebrow">${esc(S.clinic)}, ${S.schedule[0] ? dayName(S.schedule[0].start) : ''}</span>
      <div class="tbl-wrap"><table class="tbl"><thead><tr><th>Time</th><th>With</th><th>Patient</th><th>Status</th></tr></thead><tbody>${rows}</tbody></table></div>`;
    const busy = S.started && !S.done;
    $('deskStatus').className = `st ${S.fail ? 'fail' : busy ? 'running' : ''}`;
    $('deskStatus').textContent = S.fail ? 'needs staff' : busy ? 'working the line' : 'open';
  }

  function renderWire() {
    const body = $('wire');
    for (; ui.wireRendered < S.wire.length; ui.wireRendered += 1) {
      const w = S.wire[ui.wireRendered];
      const tr = document.createElement('tr');
      tr.className = w.cls;
      tr.innerHTML = `<td class="num">${w.t}</td><td class="id">${esc(w.from)}</td><td class="id">${esc(w.to)}</td><td class="wire">${w.html}</td>`;
      body.appendChild(tr);
    }
    $('wireEmpty').hidden = S.wire.length > 0;
    const sent = S.wire.filter((w) => w.cls !== 'private').length;
    $('wireMeta').textContent = `${sent} sent between agents, ${S.wire.length - sent} kept private (grey)`;
  }

  function renderReadouts() {
    const calls = S.offers;
    const minutes = calls * ui.minPerCall * 2;
    const b = S.booked;
    let sooner = '<b>0 days</b><span class="eyebrow">Sooner for the person</span><p class="sum">Waiting for a booking.</p>';
    let took = '<b>0.0 s</b><span class="eyebrow">Agents took (measured)</span><p class="sum">Waiting for a booking.</p>';
    if (b && b.who && S.slot) {
      const days = daysBetween(b.who.current_appointment, S.slot.start);
      sooner = `<b>${days} days</b><span class="eyebrow">Sooner for ${esc(b.who.name)}</span>
        <p class="sum">${esc(b.who.name)} held ${dayName(b.who.current_appointment)}. Now ${dayName(S.slot.start)}.<br>${dayName(b.who.current_appointment)} minus ${dayName(S.slot.start)} = ${days} days.</p>
        <p class="note">From the invented demo schedule. The arithmetic is real, the dates are made up.</p>`;
      took = `<b>${((b.t - S.tCancel) / 1000).toFixed(1)} s</b><span class="eyebrow">Agents took (measured)</span>
        <p class="sum">booked at T=${b.t} ms, message at T=${S.tCancel} ms.<br>${b.t} minus ${S.tCancel} = ${b.t - S.tCancel} ms.<br>model calls: ${S.modelMs.join(' + ') || 'none'} ms.</p>
        <p class="note">${S.retry ? `The confirm was sent twice. Bookings for the slot: ${S.retry.count}.` : 'Measured on this run, on one machine.'}</p>`;
    }
    $('readouts').innerHTML = `<div class="readout"><b>${minutes.toFixed(1)} min</b><span class="eyebrow">Human phone minutes not spent</span>
        <p class="sum">${calls} offer${calls === 1 ? '' : 's'} = ${calls} phone call${calls === 1 ? '' : 's'} not made<br>
        ${calls} &times; <label class="sr-only" for="mpc">minutes per call</label><input id="mpc" type="number" min="0" max="60" step="0.1" value="${ui.minPerCall}"> min &times; 2 people on each call = ${minutes.toFixed(1)} min</p>
        <p class="note">8.1 minutes is the average scheduling call <a href="${CALL_SOURCE}">Accenture measured in 2013</a>. Here it is an assumption. Change it.</p></div>
      <div class="readout">${sooner}</div><div class="readout">${took}</div>`;
    $('countMeta').textContent = 'nothing here is estimated without saying so';
  }

  function renderAnswer() {
    const a = S.answer;
    if (!a) {
      $('answer').innerHTML = '<p class="empty">How long did the booked ticket wait, and why did the other one pass? The answer appears here once a slot is booked, read back from the log of events.</p>';
      $('logMeta').textContent = '';
      return;
    }
    $('answer').innerHTML = `<p class="answer">${esc(a.served?.answer || '')}</p><p class="answer">${esc(a.passed?.answer || '')}</p>`;
    $('logMeta').textContent = `${a.events_read} events read, backend ${a.backend}${a.simulated_keewano ? ' (Keewano simulated)' : ''}`;
  }

  function renderPartners() {
    const i = S.integrations;
    if (!i) { $('partners').innerHTML = '<p class="empty">No run loaded yet.</p>'; return; }
    const dial = i.dial.simulated ? ['off', 'simulated, no text sent'] : ['', 'live, real texts'];
    $('partners').innerHTML = `
      <div><h3>Dial</h3><span class="st ${dial[0]}">${dial[1]}</span><p class="note">When an agent must ask its person, it texts the one yes or no question. ${i.dial.simulated ? 'Needs ' + esc(i.dial.missing.join(', ')) + '.' : ''}</p></div>
      <div><h3>Darwin</h3><span class="st off">listing built, not submitted</span><p class="note">The front desk's agent card is turned into a Darwin listing file, so a person's agent could find this line. Submitting it is left to a human.</p></div>
      <div><h3>Keewano</h3><span class="st off">simulated with a JSONL log</span><p class="note">Every event is appended to a log, and the question above is answered from the full sequence. KeewanoDB Local had no public install on build day.</p></div>`;
  }

  function render() {
    $('verdict').textContent = S.verdict;
    $('verdict').className = `verdict${S.fail ? ' fail' : ''}`;
    document.title = `${S.verdict} Take a Number`;
    renderDesk(); renderTickets(); renderReadouts(); renderWire(); renderAnswer(); renderPartners(); renderControls();
  }

  // Lease countdowns tick without redrawing everything.
  setInterval(() => {
    for (const id of S.order) {
      const p = S.people[id];
      const left = leaseLeft(p);
      const row = $(`lease-${id}`);
      if (left == null || !row) continue;
      const on = Math.ceil((left / p.leaseSeconds) * 30);
      [...row.children].forEach((dot, i) => { dot.className = `${i < on ? 'on' : ''}${i === on - 1 ? ' last' : ''}`; });
      $(`left-${id}`).textContent = `lease: ${Math.ceil(left)} s of ${Math.round(p.leaseSeconds)} left`;
    }
  }, 250);

  // ---- playing events at reading speed -----------------------------------

  function enqueue(events) {
    ui.queue.push(...events);
    if (!ui.timer) step(ui.run);
  }

  function step(run) {
    ui.timer = null;
    if (run !== ui.run || !ui.queue.length) { if (run === ui.run) afterDrain(); return; }
    const e = ui.queue.shift();
    apply(e);
    let wait = dwell(e.type);
    const next = ui.queue[0];
    if (ui.mode === 'replay' && next && !FAST) {
      const gap = next.t_ms - e.t_ms;
      if (next.type === 'lease_expired') wait = Math.max(wait, gap); // let the recorded lease really run out
      if (next.type === 'human_answer') wait = Math.max(wait, Math.min(gap, 2500));
    }
    ui.timer = setTimeout(() => step(run), wait);
  }

  async function afterDrain() {
    if (!S.booked || S.answer || !S.done) return;
    if (ui.mode === 'replay') {
      S.answer = ui.replay.runs.find((r) => r.id === ui.replayRunId)?.answer || null;
    } else {
      try { S.answer = await (await fetch('demo/ask')).json(); } catch (err) { S.answer = null; }
    }
    renderAnswer();
  }

  // ---- controls -----------------------------------------------------------

  let controlsKey = '';
  function renderControls() {
    const running = S.started && !S.done;
    const key = [ui.mode, running, ui.liveOk, ui.lease, ui.modelOff, ui.error, ui.replay?.runs.length].join('|');
    if (key === controlsKey) return;
    controlsKey = key;
    let html = '';
    if (ui.error) html += `<p class="empty err" style="flex-basis:100%">${esc(ui.error)}</p>`;
    if (ui.mode === 'replay' && ui.replay) {
      html += ui.replay.runs.map((r, i) => `<button class="btn${i ? ' btn--ghost' : ''}" data-run="${esc(r.id)}" ${running ? 'disabled' : ''}>${esc(r.label)}</button>`).join('');
      html += `<span class="meta">Recording of a real run, ${esc(ui.replay.recorded_at || '')}. No network needed.</span>`;
    } else if (ui.mode === 'live') {
      const presets = ui.health?.presets || [];
      html += presets.map((r, i) => `<button class="btn${i ? ' btn--ghost' : ''}" data-preset="${i}" ${running ? 'disabled' : ''}>${esc(r.label)}</button>`).join('');
      html += `<label class="sr-only" for="free">Text the front desk</label><input class="field" id="free" maxlength="300" placeholder="or text the front desk in your own words (simulated inbound text)">
        <button class="btn btn--ghost" id="send" ${running ? 'disabled' : ''}>Send</button>
        <label class="sr-only" for="lease">Lease length</label><select class="field" id="lease"><option value="60" ${ui.lease === 60 ? 'selected' : ''}>lease 60 s</option><option value="10" ${ui.lease === 10 ? 'selected' : ''}>lease 10 s</option></select>
        <label class="check"><input type="checkbox" id="modelOff" ${ui.modelOff ? 'checked' : ''}> model outage (simulated)</label>
        <button class="btn btn--ghost" id="reset">Reset</button>`;
    }
    $('controls').innerHTML = html;
  }

  document.addEventListener('click', async (ev) => {
    const t = ev.target.closest('button');
    if (!t) return;
    if (t.id === 'modeReplay') return setMode('replay');
    if (t.id === 'modeLive') return setMode('live');
    if (t.dataset.run) return playRun(t.dataset.run);
    if (t.dataset.preset) return post('demo/cancel', { text: ui.health.presets[+t.dataset.preset].text });
    if (t.id === 'send') { const v = $('free').value.trim(); if (v) { $('free').value = ''; post('demo/text', { text: v }); } return; }
    if (t.id === 'reset') return liveReset();
    if (t.dataset.answer) { t.disabled = true; return post(`sitters/${t.dataset.pid}/human/answer`, { answer: t.dataset.answer === 'yes' }); }
  });
  document.addEventListener('change', (ev) => {
    if (ev.target.id === 'mpc') { ui.minPerCall = Math.max(0, Math.min(60, Number(ev.target.value) || 0)); renderReadouts(); }
    if (ev.target.id === 'lease') { ui.lease = Number(ev.target.value); liveReset(); }
    if (ev.target.id === 'modelOff') { ui.modelOff = ev.target.checked; liveReset(); }
  });
  document.addEventListener('keydown', (ev) => { if (ev.key === 'Enter' && ev.target.id === 'free') $('send').click(); });

  async function post(path, body) {
    try {
      const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      if (!r.ok) throw new Error(`${path} returned ${r.status}`);
      setError(null);
      return await r.json();
    } catch (err) {
      setError(`Could not reach the agents: ${err.message}. Switch to Replay to keep going.`);
      return null;
    }
  }
  function setError(msg) { if (ui.error !== msg) { ui.error = msg; renderControls(); } }

  // ---- modes ---------------------------------------------------------------

  function stop() {
    ui.run += 1;
    clearTimeout(ui.timer); ui.timer = null;
    clearInterval(ui.poll); ui.poll = null;
    ui.queue = [];
  }

  function modeStatus() {
    const el = $('modeStatus');
    if (ui.mode === 'live') {
      const off = ui.modelOff;
      el.className = `st ${off ? 'warn' : 'running'}`;
      el.textContent = off ? 'live, model outage simulated' : `live, model: ${(ui.health?.model_providers || []).join(' then ') || 'none set'}`;
    } else {
      el.className = 'st';
      el.textContent = ui.liveOk ? 'replay of a recorded run' : 'replay of a recorded run (no agents reachable from this page)';
    }
    $('modeReplay').setAttribute('aria-pressed', String(ui.mode === 'replay'));
    $('modeLive').setAttribute('aria-pressed', String(ui.mode === 'live'));
    $('modeLive').disabled = !ui.liveOk;
  }

  async function setMode(mode) {
    stop();
    ui.mode = mode;
    ui.error = null;
    controlsKey = '';
    modeStatus();
    if (mode === 'live') return liveReset();
    if (!ui.replay) {
      try { ui.replay = await (await fetch('replay.json', { cache: 'no-store' })).json(); }
      catch (err) { ui.error = 'Could not read replay.json: ' + err.message; S = blank(); render(); return; }
    }
    showRunStart(ui.replay.runs[0].id);
    if (params.has('autoplay')) playRun(params.get('autoplay') || ui.replay.runs[0].id);
  }

  function showRunStart(id) {
    const run = ui.replay.runs.find((r) => r.id === id) || ui.replay.runs[0];
    ui.replayRunId = run.id;
    const cut = run.events.findIndex((e) => e.type === 'cancellation_received' || e.type === 'text_in');
    run.events.slice(0, cut < 0 ? run.events.length : cut).forEach(apply);
    return cut;
  }

  function playRun(id) {
    stop();
    const run = ui.replay.runs.find((r) => r.id === id) || ui.replay.runs[0];
    const cut = showRunStart(run.id);
    if (cut >= 0) enqueue(run.events.slice(cut));
  }

  async function liveReset() {
    stop();
    modeStatus();
    const ok = await post('demo/reset', { lease_seconds: ui.lease, retry_confirm: true, model_off: ui.modelOff });
    if (!ok) { render(); return; }
    ui.lastSeq = 0;
    const run = ui.run;
    let failures = 0;
    const tick = async () => {
      if (run !== ui.run || ui.busy) return;
      ui.busy = true;
      try {
        const r = await fetch(`demo/events?after=${ui.lastSeq}`);
        if (!r.ok) throw new Error(`events returned ${r.status}`);
        const { events } = await r.json();
        failures = 0;
        if (run === ui.run && events.length) { ui.lastSeq = events[events.length - 1].seq; enqueue(events); }
        if (ui.error && run === ui.run) setError(null);
      } catch (err) {
        failures += 1;
        if (failures >= 4 && run === ui.run) setError(`Lost contact with the agents (${err.message}). Switch to Replay to keep going.`);
      } finally { ui.busy = false; }
    };
    await tick();
    ui.poll = setInterval(tick, 250);
  }

  async function boot() {
    render();
    try {
      const ctl = new AbortController();
      const timer = setTimeout(() => ctl.abort(), 1500);
      const r = await fetch('demo/health', { signal: ctl.signal });
      clearTimeout(timer);
      if (r.ok) { ui.health = await r.json(); ui.liveOk = Boolean(ui.health.ok); }
    } catch (err) { ui.liveOk = false; }
    const want = params.get('mode');
    setMode(want === 'live' && ui.liveOk ? 'live' : want === 'replay' || !ui.liveOk ? 'replay' : 'live');
  }

  boot();
})();
