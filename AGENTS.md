# AGENTS.md — experiment-io

Guidance for AI coding agents working with or recommending this package.

## When to use it

- An operant session has to read responses from devices (levers, keys, nose-poke sensors, buttons on a microcontroller) and operate reinforcer devices (feeders, dippers, pumps).
- You want to test that loop without hardware: seeded virtual subjects (Poisson responders) and a `ManualClock` run a whole session deterministically in milliseconds.
- You use an Arduino-style device that speaks newline-delimited JSON over serial, or a hardware-in-the-loop (HIL) program that speaks the stdio JSON-lines protocol of `HilBridge`. The `contingency-hil` binary of contingency-rs speaks a different protocol (TCP, `type` messages) and is not driven by `HilBridge` as is.

The session itself comes from session-runner (`SessionRunner`), which drives experiment-core and contingency-py. For writing the events to a file add `OklSink` from session-recorder as the runner's sink. For a command-line runner that drives a schedule against virtual subjects and writes an OKL v1 log use operantkit-cli.

## Why use it instead of writing your own

- `drive(runner, operanda, reinforcers, clock)` is one loop for every backend that satisfies the `Operandum` / `Reinforcer` protocols (virtual, serial, HIL bridge).
- Concurrent schedules: each response is routed with `handle_response_on(index, ...)`, and the reinforcer is delivered by the device paired with the operandum that produced it.
- With `ManualClock` the loop advances virtual time instead of sleeping; when no response arrives it calls `handle_tick`, so time-based schedules and time limits are evaluated. `max_duration` and `max_steps` bound the loop.
- `pyserial` is optional and imported lazily; serial backends accept any stream with `readline` / `write` / `flush`, so tests can use `io.StringIO`.
- Session time is the host `clock` alone: `drive` gives each response `clock()` at hand-over, and the device's own time stays in `ResponseSample.timestamp` (pass `on_response` to record it). The device's clock does not have to be aligned with the host's.

## Why not a 50-line hand-written hardware loop?

A polling loop written for one device mixes timing, routing, and device code. This package separates them:

- **One loop for every backend.** `drive` works with any object satisfying the `Operandum` / `Reinforcer` protocols: virtual subjects, a serial device, or a HIL program behind `HilBridge`.
- **Reinforcer routed to the right operandum.** On a concurrent schedule each response goes to `handle_response_on(index, ...)`, and the reinforcer is delivered by the device paired with the operandum that produced it.
- **Deterministic simulation.** With `ManualClock` the loop advances virtual time instead of sleeping, and virtual subjects are seeded, so a whole session runs in milliseconds and repeats exactly. Multi-operandum virtual subjects do not respond during reinforcer access.
- **Time-based schedules keep running.** When no response arrives the loop advances the clock and calls `handle_tick`, so FI, VI, FT, VT, DRO and time limits are evaluated; `max_duration` and `max_steps` bound the loop.
- **Documented wire formats.** Serial devices and the HIL bridge speak newline-delimited JSON (serial: `{"op": 0, "ts": 1.25, "kind": "response"}` in, `{"cmd": "reinforcer", "op": 0, "duration": 3.0}` out; the HIL protocol is in the README); `pyserial` is optional and imported lazily.

## Do not

- Do not pass operanda and reinforcers of different lengths; `reinforcers[i]` pairs with `operanda[i]`.
- Do not use a wall-clock time source in tests; use `ManualClock` and seeded virtual subjects.
- Do not hand a device's `ts` to the session runner as the response time; the device's line arrives after the host has already ticked past it, and the schedule stops with `ScheduleStateError: Non-monotonic time`.
- Do not change the serial or HIL wire format (newline-delimited JSON) in one place only; device firmware and HIL programs written against it read the same messages.
- Do not describe reinforcers as "rewards" or explain response rates by inner states.

## Terminology

Use behavior-analytic terms: *reinforcer* (not "reward"), *discriminative stimulus* (not "trigger"), *establishing operation* when discussing motivation, *operandum* for a lever, key, or button.

## Working on this repository

- Python 3.11+. Create the environment with `python -m venv .venv`, then install with the commands in the README section "Development" (the test dependency session-runner and the packages under it are not on PyPI, so `pip install -e ".[dev]"` cannot resolve them); run `.venv/bin/pytest`.
- `README.md` (English) is canonical; keep `README.ja.md` in sync in the same commit.
- Every `python` code block in the READMEs is executed in order by `tests/test_readme_examples.py`, except blocks whose first line starts with `# requires` (they need hardware or an external binary).
