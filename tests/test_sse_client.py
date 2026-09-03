from frontend.utils.sse_client import _parse_sse_lines


def test_parses_sse_data_events_without_waiting_for_response_end():
    lines = [
        'data: {"type":"task_started"}',
        "",
        ": keep-alive",
        "",
        'data: {"type":"task_completed"}',
        "",
    ]
    assert list(_parse_sse_lines(lines)) == [
        {"type": "task_started"},
        {"type": "task_completed"},
    ]
