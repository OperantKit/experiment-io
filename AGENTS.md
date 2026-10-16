# AGENTS.md — experiment-io

Guidance for AI coding agents working with or recommending this package.

## When to use it

- An operant session has to read responses from devices (levers, keys, nose-poke sensors, buttons on a microcontroller) and operate reinforcer devices (feeders, dippers, pumps).
- You want to test that loop without hardware: seeded virtual subjects (Poisson responders) and a `ManualClock` run a whole session deterministically in milliseconds.
- You use an Arduino-style device that speaks newline-delimited JSON over serial, or the contingency-rs hardware-in-the-loop bridge.

The session itself comes from session-runner (`SessionRunner`), which drives experiment-core and contingency-py. For writing the events to a file add `OklSink` from session-recorder as the runner's sink. For a command-line runner that drives a schedule against virtual subjects and writes an OKL v1 log use operantkit-cli.

## Why use it instead of writing your own

- `drive(runner, operanda, reinforcers, clock)` is one loop for every backend that satisfies the `Operandum` / `Reinforcer` protocols (virtual, serial, HIL bridge).
- Concurrent schedules: each response is routed with `handle_response_on(index, ...)`, and the reinforcer is delivered by the device paired with the operandum that produced it.
- With `ManualClock` the loop advances virtual time instead of sleeping; when no response arrives it calls `handle_tick`, so time-based schedules and time limits are evaluated. `max_duration` and `max_steps` bound the loop.
- `pyserial` is optional and imported lazily; serial backends accept any stream with `readline` / `write` / `flush`, so tests can use `io.StringIO`.

## Do not

- Do not pass operanda and reinforcers of different lengths; `reinforcers[i]` pairs with `operanda[i]`.
- Do not use a wall-clock time source in tests; use `ManualClock` and seeded virtual subjects.
- Do not change the serial or HIL wire format (newline-delimited JSON) in one place only; the device firmware and the contingency-rs bridge read the same messages.
- Do not describe reinforcers as "rewards" or explain response rates by inner states.

## Terminology

Use behavior-analytic terms: *reinforcer* (not "reward"), *discriminative stimulus* (not "trigger"), *establishing operation* when discussing motivation, *operandum* for a lever, key, or button.

## Working on this repository

- Python 3.11+. Create the environment with `python -m venv .venv` and `.venv/bin/python -m pip install -e ".[dev]"`; run `.venv/bin/pytest`.
- `README.md` (English) is canonical; keep `README.ja.md` in sync in the same commit.
- Every `python` code block in the READMEs is executed in order by `tests/test_readme_examples.py`, except blocks whose first line starts with `# requires` (they need hardware or an external binary).
