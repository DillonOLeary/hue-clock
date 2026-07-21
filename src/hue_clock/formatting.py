import datetime as dt


def format_duration(seconds: float) -> str:
    hours, minutes = int(seconds) // 3600, (int(seconds) % 3600) // 60
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"


def format_clock(when: dt.datetime) -> str:
    return when.strftime("%-I:%M%p").lower()[:-1]
