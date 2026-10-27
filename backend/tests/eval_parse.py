import argparse
from datetime import datetime, date
import time

from instructor.core.exceptions import InstructorRetryException 

from app.llm.parse import parse_command
from app.models import Event
from app.date_utils import Weekdays, next_weekday
from tests.eval_utils import EVAL_SESSION, use_temp_db, seed_events

# 3 Goals:
# Test add, test remove, test correct date arithmetic

# Add and Date:
starting_datetimes: list[datetime] = [
    datetime(2026, 10, 5, 9, 0, 0, 0),
    datetime(2026, 10, 7, 9, 0, 0, 0),
    datetime(2026, 10, 9, 9, 0, 0, 0),
    datetime(2026, 12, 30, 9, 0, 0, 0),  # Wed, crosses into a new month and year
]

# One prompt per weekday, phrased differently
prompts: dict[str, Weekdays] = {
    "Lunch with Alex Friday 1pm": Weekdays.FRIDAY,
    "Bowling Thursday": Weekdays.THURSDAY,
    "Math midterm on Monday": Weekdays.MONDAY,
    "Dentist appointment Tuesday at 10am": Weekdays.TUESDAY,
    "Coffee with Sam on Wednesday morning": Weekdays.WEDNESDAY,
    "soccer game saturday 3pm": Weekdays.SATURDAY,
    "Add brunch with my parents this Sunday at 11": Weekdays.SUNDAY,
}

def eval_add_and_dates():
    add_fails = 0
    date_fails = 0
    num_total = 0
    times: list[float] = []

    print("TEST ADD + DATE\n")
    for now in starting_datetimes:
        for prompt in prompts:
            num_total += 1

            # Log the time before and after
            start = time.perf_counter()
            result = parse_command(EVAL_SESSION, prompt, now).command
            delta_time = time.perf_counter() - start
            times.append(delta_time)

            expected = next_weekday(now, prompts[prompt])
            # Check add
            if (result.action != "add"):
                add_fails += 1
                print(f"FAIL  now={now:%a %m-%d}  {prompt!r:32}  expected add, got {result.action},      inference_time: {delta_time:.2f}")
                continue
            elif (result.start_day_time.date() != expected):
                date_fails += 1
            mark = "PASS" if result.start_day_time.date() == expected else "FAIL"
            print(f"{mark}  now={now:%a %m-%d}  {prompt!r:32}  expected {expected:%a %m-%d}  got {result.start_day_time:%a %m-%d},      inference_time: {delta_time:.2f}")

    print(f"\n{num_total} cases | action fails: {add_fails} | date fails: {date_fails} | average inference time: {sum(times)/len(times):.2f} | max time {max(times):.2f}")

# Test Remove
remove_now = datetime(2026, 10, 5, 9, 0) # Only need to set one date for remove test

seed: dict[str, Event] = {
    "dentist1": Event(title="Dentist appointment", start_day_time=datetime(2026, 10, 6, 10, 0)),  # Tue (tomorrow)
    "dinner1":  Event(title="Dinner with Alex",   start_day_time=datetime(2026, 10, 9, 19, 0)),  # Fri
    "standup1": Event(title="Team standup",        start_day_time=datetime(2026, 10, 7, 9, 0)),   # Wed
    "standup2": Event(title="Team standup",        start_day_time=datetime(2026, 10, 8, 9, 0)),   # Thu
    "call1":    Event(title="Call with mom",       start_day_time=datetime(2026, 10, 5, 18, 0)),  # Mon (today)
    "lunch1":   Event(title="Lunch with Alex",     start_day_time=datetime(2026, 10, 8, 12, 0)),  # Thu
    "gym1":     Event(title="Gym session",         start_day_time=datetime(2026, 10, 10, 8, 0)),  # Sat
    "flight1":  Event(title="Flight to Denver",    start_day_time=datetime(2026, 10, 14, 6, 30)), # Wed next week
}

remove_prompts: dict[str, str | None] = {
    "Cancel my dentist appointment": "dentist1",      # match by name
    "Remove dinner with Alex": "dinner1",
    "Cancel Wednesday's standup": "standup1",         # same title, pick by day
    "Delete the standup on Thursday": "standup2",
    "Cancel tomorrow's appointment": "dentist1",      # relative day
    "Cancel my haircut": None,                        # nothing matches
    "Cancel lunch with Alex": "lunch1",               # same person as dinner1, different event
    "Delete my dinner on Friday": "dinner1",
    "I can't make the call with mom tonight": "call1",  # indirect wording, "tonight" = today
    "Cancel my gym session on Saturday": "gym1",
    "Remove my flight next week": "flight1",
    "Cancel the meeting with my professor": None,
}

def eval_remove():
    print("\nTEST REMOVE:\n")
    num_total = 0
    fails = 0
    times: list[float] = []
    seed_events(seed)
    for prompt in remove_prompts:
        num_total +=1
        start = time.perf_counter()
        try:
            result = parse_command(EVAL_SESSION, prompt, remove_now).command
        except InstructorRetryException as e:
            fails += 1 
            print(f"FAIL  prompt={prompt!r:32} no valid answer after {e.n_attempts} attempts,      inference time: {time.perf_counter() - start:.2f}")        
            continue
        delta_time = time.perf_counter() - start
        times.append(delta_time)
        mark = "PASS"
        # Check that action == "remove"
        if (result.action != "remove"):
            fails += 1
            print(f"FAIL prompt={prompt!r:32}  expected remove, got {result.action}, inference time {delta_time:.2f}")
            continue
        # If remove, check that the corresponding ID for the remove is the same as the seed
        elif (result.event_id != remove_prompts[prompt]):
            fails += 1
            mark="FAIL"
        print(f"{mark}  prompt={prompt!r:32}  expected_id={remove_prompts[prompt]} got={result.event_id},       inference time: {delta_time:.2f}")
    summary = f"\n{num_total} cases | remove fails: {fails}"
    if times:
        summary += f" | average inference time: {sum(times)/len(times):.2f} | max time {max(times):.2f}"
    print(summary)
    
# Test Update
# prompt -> (expected event id, expected changed fields). Any field not listed must come back as None.
UPDATE_FIELDS = ("title", "start_day_time", "end_day_time", "location")

update_prompts: dict[str, tuple[str | None, dict]] = {
    "Move my dentist appointment to Thursday at 2pm": ("dentist1", {"start_day_time": datetime(2026, 10, 8, 14, 0)}),
    "Rename the gym session to Yoga": ("gym1", {"title": "Yoga"}),
    "Change the location of lunch with Alex to Cafe Roma": ("lunch1", {"location": "Cafe Roma"}),
    "Push Wednesday's standup to 10am": ("standup1", {"start_day_time": datetime(2026, 10, 7, 10, 0)}),  # same title, pick by day
    "Make the dentist appointment run until 11am": ("dentist1", {"end_day_time": datetime(2026, 10, 6, 11, 0)}),
    "Move dinner with Alex to 8pm and call it Team dinner": ("dinner1", {"start_day_time": datetime(2026, 10, 9, 20, 0), "title": "Team dinner"}),
    "Move my haircut to Friday": (None, {}),  # nothing matches
}

def _field_matches(got, expected) -> bool:
    if isinstance(expected, str) and isinstance(got, str):
        return got.strip().casefold() == expected.casefold()
    return got == expected

def eval_update():
    print("\nTEST UPDATE:\n")
    num_total = 0
    action_fails = 0
    id_fails = 0
    field_fails = 0
    times: list[float] = []
    seed_events(seed)
    for prompt, (expected_id, expected_fields) in update_prompts.items():
        num_total += 1
        start = time.perf_counter()
        try:
            result = parse_command(EVAL_SESSION, prompt, remove_now).command
        except InstructorRetryException as e:
            action_fails += 1
            print(f"FAIL  prompt={prompt!r:32} no valid answer after {e.n_attempts} attempts,      inference time: {time.perf_counter() - start:.2f}")
            continue
        delta_time = time.perf_counter() - start
        times.append(delta_time)

        if result.action != "update":
            action_fails += 1
            print(f"FAIL  prompt={prompt!r:32}  expected update, got {result.action}, inference time {delta_time:.2f}")
            continue
        if result.event_id != expected_id:
            id_fails += 1
            print(f"FAIL  prompt={prompt!r:32}  expected_id={expected_id} got={result.event_id},       inference time: {delta_time:.2f}")
            continue

        # No match: nothing else to check
        if expected_id is None:
            print(f"PASS  prompt={prompt!r:32}  expected_id=None got=None,       inference time: {delta_time:.2f}")
            continue

        # Listed fields must match, every other field must be None
        bad_fields = []
        for field in UPDATE_FIELDS:
            got = getattr(result, field)
            if field in expected_fields:
                if not _field_matches(got, expected_fields[field]):
                    bad_fields.append(f"{field}: expected {expected_fields[field]!r} got {got!r}")
            elif got is not None:
                bad_fields.append(f"{field}: expected None got {got!r}")

        if bad_fields:
            field_fails += 1
            print(f"FAIL  prompt={prompt!r:32}  {'; '.join(bad_fields)},       inference time: {delta_time:.2f}")
        else:
            print(f"PASS  prompt={prompt!r:32}  id={result.event_id},       inference time: {delta_time:.2f}")

    summary = f"\n{num_total} cases | action fails: {action_fails} | id fails: {id_fails} | field fails: {field_fails}"
    if times:
        summary += f" | average inference time: {sum(times)/len(times):.2f} | max time {max(times):.2f}"
    print(summary)

# Test times without am/pm, and today's weekday (remove_now is a Monday)
time_prompts: dict[str, datetime] = {
    "Dinner at 7 on Friday": datetime(2026, 10, 9, 19, 0),
    "Dinner at 7 on Monday": datetime(2026, 10, 12, 19, 0),  # today's weekday means next week
    "Lunch at 1 today": datetime(2026, 10, 5, 13, 0),        # "today" still means today
}

def eval_times():
    print("\nTEST TIMES:\n")
    fails = 0
    for prompt, expected in time_prompts.items():
        start = time.perf_counter()
        result = parse_command(EVAL_SESSION, prompt, remove_now).command
        delta_time = time.perf_counter() - start

        got = result.start_day_time if result.action == "add" else None
        mark = "PASS" if got == expected else "FAIL"
        if mark == "FAIL":
            fails += 1
        print(f"{mark}  prompt={prompt!r:32}  expected {expected:%a %m-%d %H:%M}  got {got},      inference time: {delta_time:.2f}")
    print(f"\n{len(time_prompts)} cases | time fails: {fails}")

def test():

    parser = argparse.ArgumentParser(description="Eval parse_command")
    parser.add_argument("suite", nargs="?", default="all", choices=["all", "add", "remove", "update", "times"])
    args = parser.parse_args()

    use_temp_db()

    if (args.suite == "add"):
        eval_add_and_dates()
    elif(args.suite == "remove"):
        eval_remove()
    elif(args.suite == "update"):
        eval_update()
    elif args.suite == "times":
        eval_times()
    else:
        eval_add_and_dates()
        eval_remove()
        eval_update()
        eval_times()
if __name__ == "__main__":
    test()