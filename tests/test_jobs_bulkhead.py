"""Worker-pool isolation for bounded native caption provider calls."""

import pytest

from app import jobs

pytestmark = pytest.mark.unit


class _CapturingExecutor:
    def __init__(self):
        self.calls = []

    def submit(self, function, *args):
        self.calls.append((function, args))


def test_caption_jobs_never_consume_generic_worker_capacity(monkeypatch):
    generic = _CapturingExecutor()
    content = _CapturingExecutor()
    monkeypatch.setattr(jobs, "_pool", generic)
    monkeypatch.setattr(jobs, "_content_pool", content)

    # Even multiple slow provider jobs remain entirely in the dedicated queue.
    jobs._submit(11, 7, "mobile_caption_suggestion")
    jobs._submit(12, 8, "mobile_caption_suggestion")
    jobs._submit(13, 7, "image_derivatives")

    assert [call[1] for call in content.calls] == [(11, 7), (12, 8)]
    assert [call[1] for call in generic.calls] == [(13, 7)]
    assert all(call[0] is jobs._execute for call in content.calls + generic.calls)


def test_unknown_and_generic_jobs_fail_closed_to_the_reserved_generic_pool(monkeypatch):
    generic = _CapturingExecutor()
    content = _CapturingExecutor()
    monkeypatch.setattr(jobs, "_pool", generic)
    monkeypatch.setattr(jobs, "_content_pool", content)

    jobs._submit(21, None, "future_unknown_job")

    assert [call[1] for call in generic.calls] == [(21, None)]
    assert content.calls == []
