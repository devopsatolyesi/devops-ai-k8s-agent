from app.models import build_fingerprint


def test_same_problem_has_same_fingerprint() -> None:
    first = build_fingerprint("ns", "pod", "app", "CrashLoopBackOff", "same reason")
    second = build_fingerprint("ns", "pod", "app", "CrashLoopBackOff", "same reason")
    assert first == second


def test_different_reason_changes_fingerprint() -> None:
    first = build_fingerprint("ns", "pod", "app", "CrashLoopBackOff", "reason one")
    second = build_fingerprint("ns", "pod", "app", "CrashLoopBackOff", "reason two")
    assert first != second

