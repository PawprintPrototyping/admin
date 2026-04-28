"""Property-based tests for agenda_generator.py using Hypothesis."""

import calendar
import datetime
import os
import re
import tempfile

from hypothesis import given, settings
from hypothesis import strategies as st

import frontmatter as fm

from agenda_generator import (
    DEFAULTS,
    CalendarEvent,
    TemplateConfig,
    build_agenda_path,
    build_notification_message,
    build_template_context,
    compute_month_bounds,
    discover_templates,
    find_matching_event,
    ordinal,
    render_template,
)


# Feature: auto-agenda-generation, Property 5: Ordinal suffix correctness
@settings(max_examples=100)
@given(day=st.integers(min_value=1, max_value=31))
def test_ordinal_suffix_correctness(day: int):
    """For any day 1-31, ordinal returns the correct English ordinal suffix.

    Validates: Requirements 6.2
    """
    result = ordinal(day)

    # Must start with the day number
    assert result.startswith(str(day))

    # Extract suffix
    suffix = result[len(str(day)):]

    # Determine expected suffix
    if 11 <= (day % 100) <= 13:
        expected_suffix = "th"
    elif day % 10 == 1:
        expected_suffix = "st"
    elif day % 10 == 2:
        expected_suffix = "nd"
    elif day % 10 == 3:
        expected_suffix = "rd"
    else:
        expected_suffix = "th"

    assert suffix == expected_suffix, (
        f"ordinal({day}) = {result!r}, expected suffix {expected_suffix!r} but got {suffix!r}"
    )


# Feature: auto-agenda-generation, Property 3: Agenda path construction follows pattern
@settings(max_examples=100)
@given(
    output_dir=st.text(
        alphabet=st.characters(whitelist_categories=("L", "N", "P"), blacklist_characters="\x00/\\"),
        min_size=1,
        max_size=50,
    ),
    date=st.dates(
        min_value=datetime.date(2000, 1, 1),
        max_value=datetime.date(2100, 12, 31),
    ),
)
def test_agenda_path_construction_follows_pattern(output_dir: str, date: datetime.date):
    """For any non-empty output_dir and valid date, build_agenda_path returns
    a path matching {output_dir}/{year}/agenda_{YYYY-MM-DD}.md with proper zero-padding.

    Validates: Requirements 3.3, 5.4, 6.4
    """
    result = build_agenda_path(output_dir, date)

    # Verify year is present as a directory component
    year_str = str(date.year)
    month_str = f"{date.month:02d}"
    day_str = f"{date.day:02d}"
    expected_filename = f"agenda_{date.year}-{month_str}-{day_str}.md"

    # The path should end with {year}/agenda_{YYYY-MM-DD}.md
    assert result.endswith(f"{year_str}/{expected_filename}"), (
        f"Expected path to end with '{year_str}/{expected_filename}', got {result!r}"
    )

    # The path should start with the output_dir
    assert result.startswith(output_dir), (
        f"Expected path to start with {output_dir!r}, got {result!r}"
    )

    # Verify the full pattern matches {output_dir}/{year}/agenda_{YYYY-MM-DD}.md
    # Use regex to validate the date portion is zero-padded
    pattern = re.escape(output_dir) + r"[\\/]" + re.escape(year_str) + r"[\\/]agenda_\d{4}-\d{2}-\d{2}\.md$"
    assert re.match(pattern, result), (
        f"Path {result!r} does not match expected pattern"
    )

    # Verify month and day are zero-padded correctly
    assert f"-{month_str}-" in result, f"Month not zero-padded in {result!r}"
    assert result.endswith(f"-{day_str}.md"), f"Day not zero-padded in {result!r}"


# Feature: auto-agenda-generation, Property 4: Front matter parsing with defaults merge
@settings(max_examples=100)
@given(
    keys_present=st.sets(
        st.sampled_from(["event_name", "output_dir", "meeting_type", "telegram_notify", "telegram_chat_id"])
    ),
    event_name=st.text(
        alphabet=st.characters(whitelist_categories=("L", "N", "Pd"), blacklist_characters="\x00"),
        min_size=1,
        max_size=30,
    ),
    output_dir=st.text(
        alphabet=st.characters(whitelist_categories=("L", "N", "Pd"), blacklist_characters="\x00"),
        min_size=1,
        max_size=30,
    ),
    meeting_type=st.text(
        alphabet=st.characters(whitelist_categories=("L", "N", "Zs"), blacklist_characters="\x00"),
        min_size=1,
        max_size=30,
    ),
    telegram_notify=st.booleans(),
    telegram_chat_id=st.one_of(st.none(), st.text(min_size=1, max_size=20, alphabet="0123456789-")),
)
def test_front_matter_defaults_merge(
    keys_present: set,
    event_name: str,
    output_dir: str,
    meeting_type: str,
    telegram_notify: bool,
    telegram_chat_id: str | None,
):
    """For any subset of recognized keys, verify merge produces correct TemplateConfig.

    Keys present in front matter use the front matter value; absent keys use DEFAULTS.

    Validates: Requirements 4.1, 4.4, 4.5
    """
    # Build the front matter dict with only the selected keys
    values = {
        "event_name": event_name,
        "output_dir": output_dir,
        "meeting_type": meeting_type,
        "telegram_notify": telegram_notify,
        "telegram_chat_id": telegram_chat_id,
    }
    front_matter = {k: values[k] for k in keys_present}

    # If event_name or output_dir are absent, they default to "" which causes skip.
    # We need both present and non-empty for the template to not be skipped.
    if "event_name" not in keys_present or "output_dir" not in keys_present:
        # Template will be skipped — verify it's not in results
        with tempfile.TemporaryDirectory() as tmpdir:
            fm_lines = ["---"]
            for k, v in front_matter.items():
                if isinstance(v, bool):
                    fm_lines.append(f"{k}: {'true' if v else 'false'}")
                elif v is None:
                    fm_lines.append(f"{k}: null")
                else:
                    fm_lines.append(f'{k}: "{v}"')
            fm_lines.append("---")
            fm_lines.append("Template body content")
            content = "\n".join(fm_lines)

            filepath = os.path.join(tmpdir, "test_template.j2")
            with open(filepath, "w") as f:
                f.write(content)

            results = discover_templates(tmpdir)
            assert len(results) == 0, (
                f"Expected template to be skipped when event_name or output_dir is absent, "
                f"but got {len(results)} results. keys_present={keys_present}"
            )
        return

    # Both required keys are present — template should be discovered
    with tempfile.TemporaryDirectory() as tmpdir:
        fm_lines = ["---"]
        for k, v in front_matter.items():
            if isinstance(v, bool):
                fm_lines.append(f"{k}: {'true' if v else 'false'}")
            elif v is None:
                fm_lines.append(f"{k}: null")
            else:
                fm_lines.append(f'{k}: "{v}"')
        fm_lines.append("---")
        fm_lines.append("Template body content")
        content = "\n".join(fm_lines)

        filepath = os.path.join(tmpdir, "test_template.j2")
        with open(filepath, "w") as f:
            f.write(content)

        results = discover_templates(tmpdir)
        assert len(results) == 1, (
            f"Expected 1 template, got {len(results)}. front_matter={front_matter}"
        )

        config = results[0]

        # Verify each key: present keys use front matter value, absent keys use default
        if "event_name" in keys_present:
            assert config.event_name == event_name
        else:
            assert config.event_name == DEFAULTS["event_name"]

        if "output_dir" in keys_present:
            assert config.output_dir == output_dir
        else:
            assert config.output_dir == DEFAULTS["output_dir"]

        if "meeting_type" in keys_present:
            assert config.meeting_type == meeting_type
        else:
            assert config.meeting_type == DEFAULTS["meeting_type"]

        if "telegram_notify" in keys_present:
            assert config.telegram_notify == telegram_notify
        else:
            assert config.telegram_notify == DEFAULTS["telegram_notify"]

        if "telegram_chat_id" in keys_present:
            assert config.telegram_chat_id == telegram_chat_id
        else:
            assert config.telegram_chat_id == DEFAULTS["telegram_chat_id"]

        # Verify template content and path are set correctly
        assert config.template_content == "Template body content"
        assert config.template_path == filepath


# Feature: auto-agenda-generation, Property 1: Month time bounds are exact
@settings(max_examples=100)
@given(
    year=st.integers(min_value=2000, max_value=2100),
    month=st.integers(min_value=1, max_value=12),
)
def test_month_time_bounds_are_exact(year: int, month: int):
    """For any year/month, compute_month_bounds returns timeMin/timeMax spanning exactly one month in UTC.

    **Validates: Requirements 2.1**
    """
    from agenda_generator import compute_month_bounds

    time_min, time_max = compute_month_bounds(year, month)

    # Parse the returned strings
    min_dt = datetime.datetime.fromisoformat(time_min.replace("Z", "+00:00"))
    max_dt = datetime.datetime.fromisoformat(time_max.replace("Z", "+00:00"))

    # timeMin should be the first day of the month at 00:00:00 UTC
    assert min_dt.year == year
    assert min_dt.month == month
    assert min_dt.day == 1
    assert min_dt.hour == 0
    assert min_dt.minute == 0
    assert min_dt.second == 0

    # timeMax should be the first day of the NEXT month at 00:00:00 UTC
    if month == 12:
        expected_next_year = year + 1
        expected_next_month = 1
    else:
        expected_next_year = year
        expected_next_month = month + 1

    assert max_dt.year == expected_next_year
    assert max_dt.month == expected_next_month
    assert max_dt.day == 1
    assert max_dt.hour == 0
    assert max_dt.minute == 0
    assert max_dt.second == 0

    # Verify the span is exactly one calendar month (timeMax - timeMin == days in month)
    days_in_month = calendar.monthrange(year, month)[1]
    assert (max_dt - min_dt).days == days_in_month


# Feature: auto-agenda-generation, Property 2: Event name matching correctness
@settings(max_examples=100)
@given(
    events=st.lists(
        st.builds(
            CalendarEvent,
            summary=st.text(min_size=0, max_size=50),
            date=st.dates(
                min_value=datetime.date(2000, 1, 1),
                max_value=datetime.date(2100, 12, 31),
            ),
        ),
        min_size=0,
        max_size=20,
    ),
    target_name=st.text(min_size=0, max_size=50),
)
def test_event_name_matching_correctness(events: list, target_name: str):
    """For any list of CalendarEvents and target name, find_matching_event returns the first match or None.

    **Validates: Requirements 2.2, 2.3**
    """
    from agenda_generator import find_matching_event

    result = find_matching_event(events, target_name)

    # Find the expected first matching event manually
    expected = None
    for event in events:
        if event.summary == target_name:
            expected = event
            break

    if expected is not None:
        # Should return the first matching event
        assert result is not None, (
            f"Expected a match for target_name={target_name!r} but got None"
        )
        assert result is expected, (
            f"Expected the FIRST matching event but got a different one"
        )
        assert result.summary == target_name
    else:
        # No match exists — result should be None
        assert result is None, (
            f"Expected None when no event matches target_name={target_name!r}, but got {result}"
        )


# Feature: auto-agenda-generation, Property 6: Date context variables are complete and correct
@settings(max_examples=100)
@given(
    date=st.dates(
        min_value=datetime.date(2000, 1, 1),
        max_value=datetime.date(2100, 12, 31),
    ),
    meeting_type=st.text(min_size=1, max_size=50),
    next_meeting_date=st.one_of(
        st.none(),
        st.dates(
            min_value=datetime.date(2000, 1, 1),
            max_value=datetime.date(2100, 12, 31),
        ),
    ),
)
def test_date_context_variables_complete_and_correct(
    date: datetime.date, meeting_type: str, next_meeting_date: datetime.date | None
):
    """For any valid date, verify all context keys present with correct values.

    **Validates: Requirements 7.2**
    """
    ctx = build_template_context(date, meeting_type, next_meeting_date)

    # Verify all required keys are present
    required_keys = {
        "year", "month", "day", "month_name",
        "day_ordinal", "day_plain", "date_iso", "meeting_type",
        "next_meeting_date", "next_meeting_month_name",
        "next_meeting_day_ordinal", "next_meeting_day_plain",
        "next_meeting_weekday",
    }
    assert set(ctx.keys()) == required_keys, (
        f"Expected keys {required_keys}, got {set(ctx.keys())}"
    )

    # Verify core date values
    assert ctx["year"] == date.year
    assert ctx["month"] == date.month
    assert ctx["day"] == date.day
    assert ctx["month_name"] == calendar.month_name[date.month]
    assert ctx["day_ordinal"] == ordinal(date.day)
    assert ctx["day_plain"] == str(date.day)
    assert ctx["date_iso"] == date.isoformat()
    assert ctx["meeting_type"] == meeting_type

    # Verify next meeting date values
    assert ctx["next_meeting_date"] == next_meeting_date
    if next_meeting_date is not None:
        assert ctx["next_meeting_month_name"] == calendar.month_name[next_meeting_date.month]
        assert ctx["next_meeting_day_ordinal"] == ordinal(next_meeting_date.day)
        assert ctx["next_meeting_day_plain"] == str(next_meeting_date.day)
        assert ctx["next_meeting_weekday"] == calendar.day_name[next_meeting_date.weekday()]
    else:
        assert ctx["next_meeting_month_name"] == ""
        assert ctx["next_meeting_day_ordinal"] == ""
        assert ctx["next_meeting_day_plain"] == ""
        assert ctx["next_meeting_weekday"] == ""


# Feature: auto-agenda-generation, Property 7: Template rendering heading round-trip
@settings(max_examples=100)
@given(
    date=st.dates(
        min_value=datetime.date(2000, 1, 1),
        max_value=datetime.date(2100, 12, 31),
    ),
)
def test_template_rendering_heading_round_trip(date: datetime.date):
    """For any valid date, verify rendered heading matches expected format per meeting type.

    **Validates: Requirements 5.2, 6.2, 7.3**
    """
    # Load actual template files using python-frontmatter
    board_post = fm.load("templates/board_meeting.j2")
    general_post = fm.load("templates/general_meeting.j2")

    board_content = board_post.content
    general_content = general_post.content

    # Render board template
    board_rendered = render_template(board_content, date, "Board Meeting")
    assert board_rendered is not None, "Board template rendering returned None"

    board_first_line = board_rendered.split("\n")[0]
    expected_board_heading = (
        f"# Board Meeting Agenda {calendar.month_name[date.month]} {date.day}, {date.year}"
    )
    assert board_first_line == expected_board_heading, (
        f"Board heading mismatch: got {board_first_line!r}, expected {expected_board_heading!r}"
    )

    # Render general template
    general_rendered = render_template(general_content, date, "General Body Meeting")
    assert general_rendered is not None, "General template rendering returned None"

    general_first_line = general_rendered.split("\n")[0]
    expected_general_heading = (
        f"# General Body Meeting Agenda for {calendar.month_name[date.month]} {ordinal(date.day)}, {date.year}"
    )
    assert general_first_line == expected_general_heading, (
        f"General heading mismatch: got {general_first_line!r}, expected {expected_general_heading!r}"
    )


# Feature: auto-agenda-generation, Property 8: Template rendering preserves required sections in order
@settings(max_examples=100)
@given(
    date=st.dates(
        min_value=datetime.date(2000, 1, 1),
        max_value=datetime.date(2100, 12, 31),
    ),
)
def test_template_rendering_preserves_required_sections_in_order(date: datetime.date):
    """For any valid date, verify all required sections appear in correct order.

    **Validates: Requirements 5.3, 6.3**
    """
    # Load actual template files using python-frontmatter
    board_post = fm.load("templates/board_meeting.j2")
    general_post = fm.load("templates/general_meeting.j2")

    board_content = board_post.content
    general_content = general_post.content

    # Render board template
    board_rendered = render_template(board_content, date, "Board Meeting")
    assert board_rendered is not None, "Board template rendering returned None"

    board_sections = [
        "Attendees",
        "Space Management",
        "Tech",
        "Finances",
        "Events",
        "Corporate Filings",
        "Motions and Voting",
        "Next Meeting Date",
        "Sidebars",
    ]

    # Verify all board sections appear and are in strictly increasing order
    board_indices = []
    for section in board_sections:
        idx = board_rendered.find(section)
        assert idx != -1, (
            f"Board template missing required section: {section!r}"
        )
        board_indices.append(idx)

    for i in range(len(board_indices) - 1):
        assert board_indices[i] < board_indices[i + 1], (
            f"Board sections out of order: {board_sections[i]!r} (index {board_indices[i]}) "
            f"should come before {board_sections[i + 1]!r} (index {board_indices[i + 1]})"
        )

    # Render general template
    general_rendered = render_template(general_content, date, "General Body Meeting")
    assert general_rendered is not None, "General template rendering returned None"

    general_sections = [
        "Roll Call",
        "General News",
        "Tech Updates",
        "Meetings, events and Interest Groups",
        "Treasurer Update",
        "Motions and Voting",
        "Reminders",
        "Pupporri",
        "Next meeting date",
        "How to join",
    ]

    # Verify all general sections appear and are in strictly increasing order
    general_indices = []
    for section in general_sections:
        idx = general_rendered.find(section)
        assert idx != -1, (
            f"General template missing required section: {section!r}"
        )
        general_indices.append(idx)

    for i in range(len(general_indices) - 1):
        assert general_indices[i] < general_indices[i + 1], (
            f"General sections out of order: {general_sections[i]!r} (index {general_indices[i]}) "
            f"should come before {general_sections[i + 1]!r} (index {general_indices[i + 1]})"
        )


# Feature: auto-agenda-generation, Property 9: Notification message contains meeting type and date
@settings(max_examples=100)
@given(
    meeting_type=st.text(
        alphabet=st.characters(whitelist_categories=("L", "N", "Zs", "Pd")),
        min_size=1,
        max_size=50,
    ),
    date=st.dates(
        min_value=datetime.date(2000, 1, 1),
        max_value=datetime.date(2100, 12, 31),
    ),
)
def test_notification_message_contains_meeting_type_and_date(meeting_type: str, date: datetime.date):
    """For any meeting type and valid date, the notification message contains both
    the meeting type string and a human-readable representation of the date.

    **Validates: Requirements 8.3**
    """
    from agenda_generator import build_notification_message

    message = build_notification_message(meeting_type, date)

    # Message must contain the meeting type
    assert meeting_type in message, (
        f"Message {message!r} does not contain meeting_type {meeting_type!r}"
    )

    # Message must contain a human-readable date representation
    # At minimum: month name and year
    month_name = calendar.month_name[date.month]
    assert month_name in message, (
        f"Message {message!r} does not contain month name {month_name!r}"
    )
    assert str(date.year) in message, (
        f"Message {message!r} does not contain year {date.year}"
    )


# =============================================================================
# Unit tests for main orchestrator (Task 8.2)
# =============================================================================

import sys
from unittest.mock import patch, MagicMock

import pytest

from agenda_generator import main


class TestMainEndToEnd:
    """Test end-to-end flow with mocked Calendar API and temp directory."""

    def test_main_end_to_end_with_mocked_calendar(self, tmp_path):
        """Test full orchestrator flow: discovers template, fetches event,
        renders agenda, writes file.

        Validates: Requirements 3.1, 3.2
        """
        # Create a minimal board_meeting.j2 template in a temp templates dir
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        output_dir = tmp_path / "board" / "agendas"

        template_content = (
            "---\n"
            f'event_name: "Board Meeting"\n'
            f'output_dir: "{output_dir}"\n'
            f'meeting_type: "Board Meeting"\n'
            "telegram_notify: false\n"
            "---\n"
            "# Board Meeting Agenda {{ month_name }} {{ day_plain }}, {{ year }}\n"
        )
        (templates_dir / "board_meeting.j2").write_text(template_content)

        meeting_date = datetime.date(2025, 7, 21)
        mock_event = CalendarEvent(summary="Board Meeting", date=meeting_date)
        next_mock_event = CalendarEvent(summary="Board Meeting", date=datetime.date(2025, 8, 18))

        env = {
            "GOOGLE_SERVICE_ACCOUNT_JSON": "/tmp/fake_creds.json",
            "GOOGLE_CALENDAR_ID": "test@group.calendar.google.com",
        }

        # Discover templates BEFORE patching
        from agenda_generator import discover_templates as real_discover
        discovered = real_discover(str(templates_dir))

        with patch.dict(os.environ, env, clear=True), \
             patch("agenda_generator.discover_templates") as mock_discover, \
             patch("agenda_generator.get_events_in_month") as mock_get_events:

            # First call = current month events, second call = next month events
            mock_get_events.side_effect = [[mock_event], [next_mock_event]]
            mock_discover.return_value = discovered

            main()

        # Verify the agenda file was created
        expected_path = output_dir / "2025" / "agenda_2025-07-21.md"
        assert expected_path.exists(), f"Expected agenda file at {expected_path}"

        content = expected_path.read_text()
        assert "# Board Meeting Agenda July 21, 2025" in content


class TestMainIdempotency:
    """Test that existing agenda files are not overwritten."""

    def test_main_idempotency_skips_existing(self, tmp_path):
        """When an agenda file already exists, main() should NOT overwrite it.

        Validates: Requirements 3.1, 3.2
        """
        # Create a minimal template
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        output_dir = tmp_path / "board" / "agendas"

        template_content = (
            "---\n"
            f'event_name: "Board Meeting"\n'
            f'output_dir: "{output_dir}"\n'
            f'meeting_type: "Board Meeting"\n'
            "telegram_notify: false\n"
            "---\n"
            "# Board Meeting Agenda {{ month_name }} {{ day_plain }}, {{ year }}\n"
        )
        (templates_dir / "board_meeting.j2").write_text(template_content)

        # Pre-create the agenda file with known content
        meeting_date = datetime.date(2025, 7, 21)
        year_dir = output_dir / "2025"
        year_dir.mkdir(parents=True)
        existing_file = year_dir / "agenda_2025-07-21.md"
        original_content = "# Pre-existing agenda content\n"
        existing_file.write_text(original_content)

        mock_event = CalendarEvent(summary="Board Meeting", date=meeting_date)

        env = {
            "GOOGLE_SERVICE_ACCOUNT_JSON": "/tmp/fake_creds.json",
            "GOOGLE_CALENDAR_ID": "test@group.calendar.google.com",
        }

        # Discover templates BEFORE patching
        from agenda_generator import discover_templates as real_discover
        discovered = real_discover(str(templates_dir))

        with patch.dict(os.environ, env, clear=True), \
             patch("agenda_generator.discover_templates") as mock_discover, \
             patch("agenda_generator.get_events_in_month") as mock_get_events:

            mock_get_events.side_effect = [[mock_event], [mock_event]]
            mock_discover.return_value = discovered

            main()

        # Verify the file was NOT overwritten
        assert existing_file.read_text() == original_content


class TestMainEnvVars:
    """Test environment variable handling."""

    def test_main_missing_required_env_vars_exits(self):
        """main() should call sys.exit(1) when required env vars are missing.

        Validates: Requirements 10.5
        """
        env = {}  # No env vars set

        with patch.dict(os.environ, env, clear=True), \
             pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1

    def test_main_missing_optional_env_vars_disables_telegram(self, tmp_path):
        """When TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID are missing, Telegram
        notifications should be disabled (send_telegram_notification not called).

        Validates: Requirements 10.5
        """
        # Create a minimal template with telegram_notify: true
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        output_dir = tmp_path / "board" / "agendas"

        template_content = (
            "---\n"
            f'event_name: "Board Meeting"\n'
            f'output_dir: "{output_dir}"\n'
            f'meeting_type: "Board Meeting"\n'
            "telegram_notify: true\n"
            "---\n"
            "# Board Meeting Agenda {{ month_name }} {{ day_plain }}, {{ year }}\n"
        )
        (templates_dir / "board_meeting.j2").write_text(template_content)

        meeting_date = datetime.date(2025, 7, 21)
        mock_event = CalendarEvent(summary="Board Meeting", date=meeting_date)

        # Only required env vars, no Telegram vars
        env = {
            "GOOGLE_SERVICE_ACCOUNT_JSON": "/tmp/fake_creds.json",
            "GOOGLE_CALENDAR_ID": "test@group.calendar.google.com",
        }

        # Discover templates BEFORE patching
        from agenda_generator import discover_templates as real_discover
        discovered = real_discover(str(templates_dir))

        with patch.dict(os.environ, env, clear=True), \
             patch("agenda_generator.discover_templates") as mock_discover, \
             patch("agenda_generator.get_events_in_month") as mock_get_events, \
             patch("agenda_generator.send_telegram_notification") as mock_telegram:

            mock_get_events.side_effect = [[mock_event], [mock_event]]
            mock_discover.return_value = discovered

            main()

        # Telegram should NOT have been called
        mock_telegram.assert_not_called()


class TestMainTelegramFailure:
    """Test that Telegram failure does not fail the workflow."""

    def test_main_telegram_failure_does_not_fail_workflow(self, tmp_path):
        """When send_telegram_notification returns False, main() should
        complete without raising an exception.

        Validates: Requirements 8.6
        """
        # Create a minimal template with telegram_notify: true
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        output_dir = tmp_path / "board" / "agendas"

        template_content = (
            "---\n"
            f'event_name: "Board Meeting"\n'
            f'output_dir: "{output_dir}"\n'
            f'meeting_type: "Board Meeting"\n'
            "telegram_notify: true\n"
            "---\n"
            "# Board Meeting Agenda {{ month_name }} {{ day_plain }}, {{ year }}\n"
        )
        (templates_dir / "board_meeting.j2").write_text(template_content)

        meeting_date = datetime.date(2025, 7, 21)
        mock_event = CalendarEvent(summary="Board Meeting", date=meeting_date)

        env = {
            "GOOGLE_SERVICE_ACCOUNT_JSON": "/tmp/fake_creds.json",
            "GOOGLE_CALENDAR_ID": "test@group.calendar.google.com",
            "TELEGRAM_BOT_TOKEN": "fake-bot-token",
            "TELEGRAM_CHAT_ID": "-123456789",
        }

        # Discover templates BEFORE patching
        from agenda_generator import discover_templates as real_discover
        discovered = real_discover(str(templates_dir))

        with patch.dict(os.environ, env, clear=True), \
             patch("agenda_generator.discover_templates") as mock_discover, \
             patch("agenda_generator.get_events_in_month") as mock_get_events, \
             patch("agenda_generator.send_telegram_notification") as mock_telegram:

            mock_get_events.side_effect = [[mock_event], [mock_event]]
            mock_telegram.return_value = False  # Simulate Telegram failure
            mock_discover.return_value = discovered

            # Should NOT raise any exception
            main()

        # Verify Telegram was called (it was attempted)
        mock_telegram.assert_called_once()

        # Verify the agenda file was still created despite Telegram failure
        expected_path = output_dir / "2025" / "agenda_2025-07-21.md"
        assert expected_path.exists()
