from datetime import datetime
from zoneinfo import ZoneInfo

PACIFIC_TZ = ZoneInfo("America/Los_Angeles")


def now_pacific():
    return datetime.now(PACIFIC_TZ)


def now_pacific_str():
    return now_pacific().strftime("%Y-%m-%d %H:%M:%S")


def now_pacific_clock():
    return now_pacific().strftime("%H:%M:%S")
