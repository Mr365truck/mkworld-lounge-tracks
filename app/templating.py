"""Jinja environment and the filters the templates lean on."""
from fastapi.templating import Jinja2Templates

from . import config

templates = Jinja2Templates(directory=str(config.BASE_DIR / "templates"))


def fmt_dt(value, pattern: str = "%a %-d %b, %-I:%M%p") -> str:
    """UTC-stored timestamp rendered in the configured TZ (spec section 8)."""
    if value is None:
        return "—"
    local = config.to_local(value)
    # The Unix `-` flag suppresses zero padding, but Windows' strftime rejects it.
    # Replace the two forms used by the UI before handing the rest to strftime.
    portable = pattern.replace("%-d", "{day}").replace("%-I", "{hour12}")
    rendered = local.strftime(portable).format(
        day=local.day,
        hour12=local.hour % 12 or 12,
    )
    return rendered.replace("AM", "am").replace("PM", "pm")


def fmt_num(value, places: int = 2, dash: str = "—") -> str:
    if value is None:
        return dash
    return f"{value:.{places}f}"


def fmt_signed(value, places: int = 2, dash: str = "—") -> str:
    if value is None:
        return dash
    return f"{value:+.{places}f}"


def fmt_pct(value, places: int = 0, dash: str = "—") -> str:
    if value is None:
        return dash
    return f"{value * 100:.{places}f}%"


def fmt_int(value, dash: str = "—") -> str:
    if value is None:
        return dash
    return f"{int(value):,}"


def ordinal(value) -> str:
    if value is None:
        return "—"
    n = int(value)
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


templates.env.filters.update(
    dt=fmt_dt, num=fmt_num, signed=fmt_signed, pct=fmt_pct,
    intcomma=fmt_int, ordinal=ordinal,
)
templates.env.globals.update(TZ_NAME=config.TZ_NAME)
