"""Adapters for outside tools. Each one sits behind an environment variable.

When the variable is absent the adapter runs simulated and says so in its
return value (`"simulated": True`), which the page prints. Nothing here
pretends to have called a service it did not call.

  dial.py      text the person a yes/no question; receive texts (Dial)
  darwin.py    make the front desk findable; search for a line (Darwin)
  eventlog.py  the event-log interface, with a JSONL backend
  keewano.py   answer a question from the complete event sequence
"""
