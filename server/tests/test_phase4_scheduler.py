from backend import phase4_scheduler


def test_forced_automation_sweep_runs_each_active_candidate(monkeypatch):
    seen = []
    monkeypatch.setattr(phase4_scheduler, "_active_candidates", lambda: [(11, 101), (12, 102)])
    monkeypatch.setattr(phase4_scheduler, "run_candidate_cycle", lambda candidate_id, user_id: seen.append((candidate_id, user_id)))
    result = phase4_scheduler.run_automation_sweep(force=True)
    assert seen == [(11, 101), (12, 102)]
    assert result["last_success_count"] == 2
    assert result["last_error_count"] == 0
    assert result["skipped"] is False
