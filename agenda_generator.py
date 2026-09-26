"""Automated meeting agenda generation for Pawprint Prototyping."""

from __future__ import annotations

import calendar
import datetime
import glob
import logging
import os
import sys
from dataclasses import dataclass

import frontmatter
import jinja2
import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)


@dataclass
class TemplateConfig:
    """Parsed from YAML front matter with defaults applied."""

    event_name: str  # Google Calendar event name to match
    output_dir: str  # e.g., "board/agendas"
    meeting_type: str  # e.g., "Board Meeting"
    telegram_notify: bool  # default: True
    telegram_chat_id: str | None  # override default chat ID, default: None
    template_content: str  # Jinja2 template body (after front matter)
    template_path: str  # path to the template file (for logging)


DEFAULTS = {
    "event_name": "",  # required — empty triggers skip
    "output_dir": "",  # required — empty triggers skip
    "meeting_type": "Meeting",
    "telegram_notify": True,
    "telegram_chat_id": None,
}


@dataclass
class CalendarEvent:
    """A Google Calendar event with summary and date."""

    summary: str  # event name/title
    date: datetime.date  # event date


def ordinal(n: int) -> str:
    """Return English ordinal string for a day number.

    Examples: 1 -> '1st', 2 -> '2nd', 3 -> '3rd', 11 -> '11th', 21 -> '21st'
    """
    if 11 <= (n % 100) <= 13:
        suffix = "th"
    elif n % 10 == 1:
        suffix = "st"
    elif n % 10 == 2:
        suffix = "nd"
    elif n % 10 == 3:
        suffix = "rd"
    else:
        suffix = "th"
    return f"{n}{suffix}"


def build_agenda_path(output_dir: str, date: datetime.date) -> str:
    """Construct the full agenda file path.

    Returns: {output_dir}/{year}/agenda_{YYYY-MM-DD}.md
    """
    return os.path.join(output_dir, str(date.year), f"agenda_{date.isoformat()}.md")


def agenda_file_exists(output_dir: str, date: datetime.date) -> bool:
    """Check if an agenda file already exists for the given date."""
    return os.path.exists(build_agenda_path(output_dir, date))


def discover_templates(templates_dir: str) -> list[TemplateConfig]:
    """Scan templates_dir for *.j2 files and parse YAML front matter.

    Returns list of TemplateConfig with merged defaults.
    Skips templates with invalid front matter (logs error).
    Skips templates missing required keys event_name or output_dir (logs warning).
    """
    templates: list[TemplateConfig] = []
    pattern = os.path.join(templates_dir, "*.j2")

    for filepath in sorted(glob.glob(pattern)):
        try:
            post = frontmatter.load(filepath)
        except Exception as e:
            logger.error("Failed to parse front matter in %s: %s", filepath, e)
            continue

        metadata = dict(post.metadata)
        merged = {**DEFAULTS, **metadata}

        if not merged["event_name"] or not merged["output_dir"]:
            logger.warning(
                "Skipping %s: missing required key event_name or output_dir",
                filepath,
            )
            continue

        config = TemplateConfig(
            event_name=merged["event_name"],
            output_dir=merged["output_dir"],
            meeting_type=merged["meeting_type"],
            telegram_notify=merged["telegram_notify"],
            telegram_chat_id=merged["telegram_chat_id"],
            template_content=post.content,
            template_path=filepath,
        )
        templates.append(config)

    return templates


def compute_month_bounds(year: int, month: int) -> tuple[str, str]:
    """Compute timeMin and timeMax spanning exactly the target month in UTC.

    Returns (timeMin, timeMax) as ISO 8601 strings with Z suffix.
    timeMin = first day of month at 00:00:00 UTC
    timeMax = first day of next month at 00:00:00 UTC
    """
    time_min = f"{year:04d}-{month:02d}-01T00:00:00Z"

    if month == 12:
        next_year = year + 1
        next_month = 1
    else:
        next_year = year
        next_month = month + 1

    time_max = f"{next_year:04d}-{next_month:02d}-01T00:00:00Z"
    return time_min, time_max


def get_events_in_month(
    calendar_id: str,
    credentials_path: str,
    year: int,
    month: int,
) -> list[CalendarEvent]:
    """Query Google Calendar API for all events in the given month.

    Authenticates via service account credentials file.
    Returns list of CalendarEvent(summary, date).
    On auth/API errors: logs error and exits with non-zero status.
    """
    SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

    try:
        credentials = service_account.Credentials.from_service_account_file(
            credentials_path, scopes=SCOPES
        )
    except Exception as e:
        logger.error("Failed to authenticate with Google Calendar API: %s", e)
        sys.exit(1)

    try:
        service = build("calendar", "v3", credentials=credentials)
    except Exception as e:
        logger.error("Failed to build Google Calendar service: %s", e)
        sys.exit(1)

    time_min, time_max = compute_month_bounds(year, month)

    try:
        events_result = (
            service.events()
            .list(
                calendarId=calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
            )
            .execute()
        )
    except Exception as e:
        logger.error("Failed to fetch events from Google Calendar API: %s", e)
        sys.exit(1)

    events: list[CalendarEvent] = []
    for item in events_result.get("items", []):
        summary = item.get("summary", "")
        # Extract date from either 'date' (all-day) or 'dateTime' field
        start = item.get("start", {})
        date_str = start.get("date") or start.get("dateTime", "")
        if date_str:
            # Parse date portion (first 10 chars for YYYY-MM-DD)
            event_date = datetime.date.fromisoformat(date_str[:10])
        else:
            continue
        events.append(CalendarEvent(summary=summary, date=event_date))

    return events


def find_matching_event(
    events: list[CalendarEvent],
    event_name: str,
) -> CalendarEvent | None:
    """Find first event whose summary matches event_name.

    Returns None if no match.
    """
    for event in events:
        if event.summary == event_name:
            return event
    return None


def build_template_context(
    date: datetime.date,
    meeting_type: str,
    next_meeting_date: datetime.date | None = None,
) -> dict:
    """Build the context dict for Jinja2 template rendering.

    Returns a dict with keys: year, month, day, month_name, day_ordinal,
    day_plain, date_iso, meeting_type, and next_meeting_* keys if available.
    """
    ctx = {
        "year": date.year,
        "month": date.month,
        "day": date.day,
        "month_name": calendar.month_name[date.month],
        "day_ordinal": ordinal(date.day),
        "day_plain": str(date.day),
        "date_iso": date.isoformat(),
        "meeting_type": meeting_type,
        "next_meeting_date": next_meeting_date,
    }
    if next_meeting_date is not None:
        ctx["next_meeting_month_name"] = calendar.month_name[next_meeting_date.month]
        ctx["next_meeting_day_ordinal"] = ordinal(next_meeting_date.day)
        ctx["next_meeting_day_plain"] = str(next_meeting_date.day)
        ctx["next_meeting_weekday"] = calendar.day_name[next_meeting_date.weekday()]
    else:
        ctx["next_meeting_month_name"] = ""
        ctx["next_meeting_day_ordinal"] = ""
        ctx["next_meeting_day_plain"] = ""
        ctx["next_meeting_weekday"] = ""
    return ctx


def write_agenda(path: str, content: str) -> None:
    """Create parent directories if needed and write content to path."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def render_template(
    template_content: str,
    date: datetime.date,
    meeting_type: str,
    next_meeting_date: datetime.date | None = None,
) -> str | None:
    """Render a Jinja2 template string with date context variables.

    Returns rendered markdown string, or None on Jinja2 rendering errors.
    """
    context = build_template_context(date, meeting_type, next_meeting_date)
    try:
        template = jinja2.Template(template_content)
        return template.render(context)
    except jinja2.TemplateError as e:
        logger.error("Jinja2 rendering error: %s", e)
        return None


def build_notification_message(meeting_type: str, meeting_date: datetime.date) -> str:
    """Build a human-readable notification message for Telegram.

    Includes the meeting type and a formatted date (e.g., "January 26, 2026").
    """
    formatted_date = meeting_date.strftime("%B %d, %Y").replace(" 0", " ")
    return f"📋 {meeting_type} agenda has been generated for {formatted_date}."


def send_telegram_notification(
    bot_token: str,
    chat_id: str,
    meeting_type: str,
    meeting_date: datetime.date,
) -> bool:
    """Send a Telegram notification about a newly generated agenda.

    POSTs to https://api.telegram.org/bot{token}/sendMessage.
    Returns True on success, False on failure (logs error, does not raise).
    """
    message = build_notification_message(meeting_type, meeting_date)
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    try:
        response = requests.post(url, json={"chat_id": chat_id, "text": message})
        if response.status_code == 200:
            return True
        logger.error(
            "Telegram API returned status %d: %s",
            response.status_code,
            response.text,
        )
        return False
    except Exception as e:
        logger.error("Failed to send Telegram notification: %s", e)
        return False


def main() -> None:
    """Main orchestrator for agenda generation.

    1. Read env vars for credentials and config
    2. Discover templates
    3. Fetch calendar events for current month
    4. For each template: find matching event, check idempotency,
       render and write agenda, send notification if configured
    5. Exit with summary log
    """
    logging.basicConfig(level=logging.INFO)

    # Read required environment variables
    credentials_path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    calendar_id = os.environ.get("GOOGLE_CALENDAR_ID")

    if not credentials_path or not calendar_id:
        logger.error(
            "Missing required environment variables: "
            "GOOGLE_SERVICE_ACCOUNT_JSON and GOOGLE_CALENDAR_ID must be set."
        )
        sys.exit(1)

    # Read optional environment variables for Telegram
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    default_chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    telegram_enabled = True

    if not bot_token or not default_chat_id:
        logger.warning(
            "Telegram environment variables (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID) "
            "not fully configured. Telegram notifications disabled."
        )
        telegram_enabled = False

    # Discover templates
    templates = discover_templates("templates")
    if not templates:
        logger.info("No templates found. Nothing to do.")
        return

    # Get current date and fetch calendar events for this month
    today = datetime.date.today()
    year = today.year
    month = today.month

    events = get_events_in_month(calendar_id, credentials_path, year, month)
    if not events:
        logger.info("No events found for %04d-%02d. Nothing to do.", year, month)
        return

    # Fetch next month's events for next meeting date lookup
    if month == 12:
        next_year, next_month = year + 1, 1
    else:
        next_year, next_month = year, month + 1
    next_month_events = get_events_in_month(
        calendar_id, credentials_path, next_year, next_month
    )

    # Process each template
    generated_count = 0

    for config in templates:
        # Find matching event
        event = find_matching_event(events, config.event_name)
        if event is None:
            logger.warning(
                "No matching event '%s' found for template %s. Skipping.",
                config.event_name,
                config.template_path,
            )
            continue

        # Check idempotency
        if agenda_file_exists(config.output_dir, event.date):
            logger.info(
                "Agenda already exists for %s on %s. Skipping.",
                config.meeting_type,
                event.date.isoformat(),
            )
            continue

        # Look up next meeting date from next month's events
        next_event = find_matching_event(next_month_events, config.event_name)
        next_meeting_date = next_event.date if next_event else None
        if next_meeting_date is None:
            logger.info(
                "No next month event found for '%s'. Next meeting date will be empty.",
                config.event_name,
            )

        # Render template
        rendered = render_template(
            config.template_content, event.date, config.meeting_type, next_meeting_date
        )
        if rendered is None:
            continue

        # Write agenda file
        agenda_path = build_agenda_path(config.output_dir, event.date)
        write_agenda(agenda_path, rendered)
        generated_count += 1
        logger.info(
            "Generated agenda: %s (%s)", agenda_path, config.meeting_type
        )

        # Send Telegram notification if configured
        if telegram_enabled and config.telegram_notify:
            chat_id = config.telegram_chat_id or default_chat_id
            send_telegram_notification(bot_token, chat_id, config.meeting_type, event.date)

    # Log summary
    logger.info("Agenda generation complete. %d agenda(s) generated.", generated_count)


if __name__ == "__main__":
    main()
