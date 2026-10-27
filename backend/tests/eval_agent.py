import argparse
import time
from datetime import datetime

from anthropic import APIError

from app import store
from tests.eval_utils import EVAL_SESSION, seed_events, use_temp_db
from app.llm.agent.loop import run_agent
from app.models import Event

# Test a few cases of agent functionality
# Compare final state with expected final state

now = datetime(2026, 10, 26, 11, 50) # Mon oct 26 11:50 AM

# Week of Mon Oct 26: Tue 27, Wed 28, Thu 29, Fri 30, Sat 31, Sun Nov 1
SEED: dict[str, Event] = {
    "dentist1": Event(title="Dentist appointment", start_day_time=datetime(2026, 10, 27, 10, 0)),  # Tue
    "standup1": Event(title="Team standup",        start_day_time=datetime(2026, 10, 28, 9, 0)),   # Wed
    "standup2": Event(title="Team standup",        start_day_time=datetime(2026, 10, 29, 9, 0)),   # Thu
    "lunch1":   Event(title="Lunch with Alex",     start_day_time=datetime(2026, 10, 29, 12, 0)),  # Thu
    "dinner1":  Event(title="Dinner with Alex",    start_day_time=datetime(2026, 10, 30, 19, 0)),  # Fri
    "gym1":     Event(title="Gym session",         start_day_time=datetime(2026, 10, 31, 8, 0)),   # Sat
    "flight1":  Event(title="Flight to Denver",    start_day_time=datetime(2026, 11, 4, 6, 30)),   # Wed next week
}

# A check takes (events after the run, run_agent result) and returns None on pass,
# or a short failure message.

def new_events(events: dict[str, Event]) -> list[Event]:
    """Events that were not in the seed (added by the agent)."""
    return [e for event_id, e in events.items() if event_id not in SEED]

def untouched(events: dict[str, Event], changed: set[str], expected_new: int = 0) -> str | None:
    """Seed events not in `changed` must be identical, and exactly `expected_new` events were added."""
    for event_id, original in SEED.items():
        if event_id not in changed and events.get(event_id) != original:
            return f"{event_id} was modified or removed"
    if len(new_events(events)) != expected_new:
        return f"expected {expected_new} new events, got {len(new_events(events))}"
    return None

def add_two_events(events, result):
    new = new_events(events)
    gym = next((e for e in new if "gym" in e.title.casefold()), None)
    school = next((e for e in new if "school" in e.title.casefold()), None)
    if gym is None or school is None:
        return f"expected a gym and a school event, got {[e.title for e in new]}"
    if gym.start_day_time != datetime(2026, 10, 30, 17, 0) or gym.end_day_time != datetime(2026, 10, 30, 18, 0):
        return f"gym is {gym.start_day_time} to {gym.end_day_time}"
    if school.start_day_time != datetime(2026, 10, 29, 7, 0) or school.end_day_time != datetime(2026, 10, 29, 9, 0):
        return f"school is {school.start_day_time} to {school.end_day_time}"
    return untouched(events, set(), expected_new=2)

def move_two_standups(events, result):
    if events["standup1"].start_day_time != datetime(2026, 10, 28, 10, 0):
        return f"standup1 starts {events['standup1'].start_day_time}"
    if events["standup2"].start_day_time != datetime(2026, 10, 29, 10, 0):
        return f"standup2 starts {events['standup2'].start_day_time}"
    return untouched(events, {"standup1", "standup2"})

def move_thursday(events, result):
    if events["standup2"].start_day_time != datetime(2026, 10, 30, 9, 0):
        return f"standup2 starts {events['standup2'].start_day_time}"
    if events["lunch1"].start_day_time != datetime(2026, 10, 30, 12, 0):
        return f"lunch1 starts {events['lunch1'].start_day_time}"
    return untouched(events, {"standup2", "lunch1"})

def dinner_on_flight_day(events, result):
    if events["dinner1"].start_day_time != datetime(2026, 11, 4, 19, 0):
        return f"dinner1 starts {events['dinner1'].start_day_time}"
    return untouched(events, {"dinner1"})

def rename_and_move(events, result):
    gym = events["gym1"]
    if gym.title.strip().casefold() != "yoga":
        return f"gym1 title is {gym.title!r}"
    if gym.start_day_time != datetime(2026, 10, 31, 9, 0):
        return f"gym1 starts {gym.start_day_time}"
    return untouched(events, {"gym1"})

def swap_saturday(events, result):
    if "gym1" in events:
        return "gym1 should be gone"
    new = new_events(events)
    if len(new) != 1:
        return f"expected 1 new event, got {len(new)}"
    if "hike" not in new[0].title.casefold() or new[0].start_day_time != datetime(2026, 10, 31, 9, 0):
        return f"wrong new event: {new[0].title!r} at {new[0].start_day_time}"
    return untouched(events, {"gym1"}, expected_new=1)

def conflict_is_held(events, result):
    # Overlaps the Thursday standup: must not be saved, must be waiting for confirmation
    if result["pending"] is None:
        return "nothing is pending confirmation"
    return untouched(events, set())

def nothing_changes(events, result):
    return untouched(events, set())

# (Name, Prompt, Test Function)
CASES = [
    ("Add 2", "add gym on friday 5-6, add school thursday 7-9am", add_two_events),
    ("Move two standups", "Move both standups to 10am", move_two_standups),
    ("Move Thursday", "Move everything I have on Thursday to Friday, same times", move_thursday),
    ("Dependent lookup", "Move dinner with Alex to the day of my flight, same time", dinner_on_flight_day),
    ("Rename and move", "Rename the gym session to Yoga and start it at 9am", rename_and_move),
    ("Remove and add", "Cancel everything on Saturday and add a hike Saturday at 9am", swap_saturday),
    ("Conflict held", "Add a project sync on Thursday at 9am", conflict_is_held),
    ("No match", "Cancel my haircut", nothing_changes),
    ("Ambiguous", "Cancel the standup", nothing_changes),
]

def run_case(name: str, prompt: str, check) -> dict:
    seed_events(SEED)  # fresh calendar every case, the agent changes it
    start = time.perf_counter()
    try:
        result = run_agent(EVAL_SESSION, prompt, now)
    except APIError as e:
        elapsed = time.perf_counter() - start
        print(f"FAIL  {name:18} API error: {e}")
        return {"passed": False, "steps": 0, "time": elapsed}
    elapsed = time.perf_counter() - start

    events = store.list_events(EVAL_SESSION)
    try:
        error = check(events, result)
    except Exception as e:  
        error = f"check crashed: {type(e).__name__}: {e}"
    if result["truncated"] and error is None:
        error = "hit the step limit"

    steps = len(result["actions"])
    mark = "PASS" if error is None else "FAIL"
    print(f"{mark}  {name:18} steps={steps}  time={elapsed:.2f}s" + (f"  {error}" if error else ""))
    if error:
        print(f"      prompt: {prompt!r}")
        print(f"      reply:  {result['reply']!r}")
        print(f"      trace:  {[a['tool'] + ('(error)' if a['is_error'] else '') for a in result['actions']]}")
    return {"passed": error is None, "steps": steps, "time": elapsed}

def main():
    parser = argparse.ArgumentParser(description="Eval the calendar agent")
    parser.add_argument("case", nargs="?", default="all", help="case name (e.g. Dentist) or 'all'")
    args = parser.parse_args()

    cases = [c for c in CASES if args.case == "all" or c[0].casefold() == args.case.casefold()]
    if not cases:
        parser.error(f"unknown case {args.case!r}, choose from: {', '.join(c[0] for c in CASES)}")

    use_temp_db()
    print("TEST AGENT\n")
    runs = [run_case(*case) for case in cases]

    times = sorted(r["time"] for r in runs)
    passed = sum(r["passed"] for r in runs)
    print(f"\n{len(runs)} cases | passed: {passed}/{len(runs)} | "
          f"average steps: {sum(r['steps'] for r in runs)/len(runs):.1f} | "
          f"average time: {sum(times)/len(times):.2f}s | max time: {times[-1]:.2f}s")

if __name__ == "__main__":
    main()
