"""Audit log tests — ARCHITECTURE.md §5.5 (R5, R6).

The log is evidence. These tests are about it being *hard to falsify*,
not about it being convenient.
"""

import json
import os

import pytest

from friday.audit import AuditLog, verify_chain

# root bypasses directory permission bits, so a chmod-based test would
# pass without testing anything. Skip loudly instead of silently.
requires_unprivileged = pytest.mark.skipif(
    os.geteuid() == 0,
    reason="runs as root; chmod-based permission restrictions do not apply",
)


@pytest.fixture
def log(tmp_path):
    return AuditLog(tmp_path / "audit.jsonl")


def a_record(**over):
    rec = {
        "utterance": "what time is it",
        "stt_confidence": 0.93,
        "tool": "speak_time",
        "arguments": {},
        "act_probability": 0.94,
        "action_class": "read",
        "verdict": "allow",
        "reason": "confidence above threshold",
        "outcome": "ok",
    }
    rec.update(over)
    return rec


def test_appends_and_reads_back(log):
    log.append(a_record())
    log.append(a_record(utterance="open safari", tool="open_app"))
    entries = log.entries()
    assert len(entries) == 2
    assert entries[1]["utterance"] == "open safari"


def test_chain_verifies_on_untouched_log(log):
    for i in range(5):
        log.append(a_record(utterance=f"utterance {i}"))
    assert verify_chain(log.path) == (True, None)


def test_detects_edited_record(log):
    """The Replit failure mode: the record says something that did not
    happen. An edit must be detectable."""
    for i in range(3):
        log.append(a_record(utterance=f"utterance {i}"))

    lines = log.path.read_text().splitlines()
    tampered = json.loads(lines[1])
    tampered["tool"] = "delete_everything"
    lines[1] = json.dumps(tampered)
    log.path.write_text("\n".join(lines) + "\n")

    ok, bad_index = verify_chain(log.path)
    assert not ok
    assert bad_index == 1


def test_detects_truncation(log):
    """Dropping the tail must not leave a valid-looking chain."""
    for i in range(5):
        log.append(a_record(utterance=f"utterance {i}"))
    lines = log.path.read_text().splitlines()
    log.path.write_text("\n".join(lines[:3]) + "\n")

    # Truncation alone leaves a self-consistent prefix, so the log records
    # its own length: verification must notice the count no longer matches.
    ok, _ = verify_chain(log.path)
    assert not ok


def test_detects_reordering(log):
    for i in range(4):
        log.append(a_record(utterance=f"utterance {i}"))
    lines = log.path.read_text().splitlines()
    lines[1], lines[2] = lines[2], lines[1]
    log.path.write_text("\n".join(lines) + "\n")

    ok, bad_index = verify_chain(log.path)
    assert not ok
    assert bad_index == 1


def test_each_record_carries_prior_digest(log):
    log.append(a_record())
    log.append(a_record())
    entries = log.entries()
    assert entries[0]["prev"] == "0" * 64  # genesis
    assert entries[1]["prev"] == entries[0]["hash"]


def test_records_the_decision_inputs_not_just_the_outcome(log):
    """A log that only shows 'file deleted' hides the decision point
    that mattered (R5)."""
    log.append(a_record())
    e = log.entries()[0]
    for field in (
        "utterance",
        "tool",
        "arguments",
        "act_probability",
        "action_class",
        "verdict",
        "reason",
        "outcome",
    ):
        assert field in e, field


def test_records_are_timestamped(log):
    log.append(a_record())
    assert "ts" in log.entries()[0]


def test_append_is_atomic_per_record(log):
    """Sequential appends produce one well-formed line each."""
    for i in range(50):
        log.append(a_record(utterance=f"u{i}"))
    lines = log.path.read_text().splitlines()
    assert len(lines) == 50
    for line in lines:
        json.loads(line)  # raises if a line was torn


def test_concurrent_appends_do_not_lose_or_corrupt_records(tmp_path):
    """The real concurrency test ARCHITECTURE.md §10 asks for.

    M1's CLI is single-threaded, but M1.5 splits the UI into its own
    process (§4.1), so this must hold before that lands. An earlier
    version of this module lost records and raised FileNotFoundError
    under exactly this load while still *claiming* concurrency safety
    in its own comments.
    """
    import threading

    path = tmp_path / "audit.jsonl"
    threads, per_thread = 8, 25
    errors: list[Exception] = []

    def worker(n):
        log = AuditLog(path)  # separate instance, same file
        for i in range(per_thread):
            try:
                log.append(a_record(utterance=f"w{n}-{i}"))
            except Exception as exc:  # noqa: BLE001 - recorded, then asserted
                errors.append(exc)

    workers = [threading.Thread(target=worker, args=(n,)) for n in range(threads)]
    for t in workers:
        t.start()
    for t in workers:
        t.join()

    assert not errors, f"append raised under concurrency: {errors[:3]}"

    entries = AuditLog(path).entries()
    assert len(entries) == threads * per_thread

    seqs = [e["seq"] for e in entries]
    assert len(set(seqs)) == len(seqs), "duplicate sequence numbers"
    assert sorted(seqs) == list(range(threads * per_thread))

    ok, bad = verify_chain(path)
    assert ok, f"chain broken at {bad}"


def test_concurrent_appends_keep_every_utterance(tmp_path):
    """A decision that executed but whose record vanished is the exact
    failure the audit log exists to prevent (R5/R6)."""
    import threading

    path = tmp_path / "audit.jsonl"
    expected = {f"w{n}-{i}" for n in range(6) for i in range(20)}

    def worker(n):
        log = AuditLog(path)
        for i in range(20):
            log.append(a_record(utterance=f"w{n}-{i}"))

    workers = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
    for t in workers:
        t.start()
    for t in workers:
        t.join()

    assert {e["utterance"] for e in AuditLog(path).entries()} == expected


def test_failed_head_write_leaves_no_temp_file(tmp_path, monkeypatch):
    """A failing disk must not accumulate temp files forever."""
    import friday.audit as audit_mod

    log = AuditLog(tmp_path / "audit.jsonl")
    log.append(a_record())

    real_write = audit_mod.os.write
    calls = {"n": 0}

    def flaky(fd, data):
        calls["n"] += 1
        if calls["n"] == 2:  # the head write within this append
            raise OSError("simulated ENOSPC")
        return real_write(fd, data)

    monkeypatch.setattr(audit_mod.os, "write", flaky)
    with pytest.raises(OSError):
        log.append(a_record(utterance="two"))
    monkeypatch.undo()

    leaked = [p.name for p in tmp_path.iterdir() if ".tmp" in p.name]
    assert not leaked, f"leaked temp files: {leaked}"


def test_crash_between_data_and_head_is_reported_as_incomplete(tmp_path):
    """A crash mid-append leaves the head lagging the data. That is a
    *recoverable* state, not evidence of tampering, and the two must be
    distinguishable — otherwise ordinary power loss makes the log accuse
    itself and nobody trusts the real alarm."""
    log = AuditLog(tmp_path / "audit.jsonl")
    for i in range(3):
        log.append(a_record(utterance=f"u{i}"))

    # Rewind the head by one record, as a crash after the data write would.
    head_path = log.path.with_suffix(log.path.suffix + ".head")
    entries = log.entries()
    head_path.write_text(
        json.dumps({"count": 2, "hash": entries[1]["hash"]})
    )

    ok, bad, status = verify_chain(log.path, detailed=True)
    assert not ok
    assert status == "incomplete"

    # Whereas an edited record is tampering, not an interrupted write.
    log2 = AuditLog(tmp_path / "other.jsonl")
    for i in range(3):
        log2.append(a_record(utterance=f"u{i}"))
    lines = log2.path.read_text().splitlines()
    rec = json.loads(lines[1])
    rec["tool"] = "delete_everything"
    lines[1] = json.dumps(rec)
    log2.path.write_text("\n".join(lines) + "\n")

    ok2, bad2, status2 = verify_chain(log2.path, detailed=True)
    assert not ok2
    assert status2 == "tampered"


def test_verify_chain_is_stable_during_concurrent_appends(tmp_path):
    """Verification must not report a healthy log as damaged just
    because a write was in flight.

    Without a shared lock this returned "incomplete" ~36% of the time
    against a log being actively appended to — the window between the
    data line landing and the side-car update. A verifier that cries
    wolf a third of the time is worse than no verifier.
    """
    import threading

    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    log.append(a_record())

    stop = threading.Event()
    statuses: list[str] = []

    def writer():
        writer_log = AuditLog(path)
        while not stop.is_set():
            writer_log.append(a_record())

    t = threading.Thread(target=writer)
    t.start()
    try:
        for _ in range(300):
            statuses.append(verify_chain(path, detailed=True)[2])
    finally:
        stop.set()
        t.join()

    assert set(statuses) == {"ok"}, f"unstable verification: {set(statuses)}"


def test_lock_file_deleted_externally_does_not_reintroduce_the_race(tmp_path):
    """A cleanup script removing the lock file must not silently break
    mutual exclusion.

    flock attaches to an open file description, so a writer whose lock
    file was unlinked holds a lock on an orphaned inode while the next
    writer creates and locks a fresh one — both believing they are
    serialized. _acquire_lock re-stats after locking to catch this.
    """
    import threading
    import time

    path = tmp_path / "audit.jsonl"
    lock_path = path.with_suffix(path.suffix + ".lock")
    errors: list[Exception] = []

    def writer(n):
        log = AuditLog(path)
        for i in range(80):
            try:
                log.append(a_record(utterance=f"w{n}-{i}"))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

    def deleter():
        for _ in range(20):
            time.sleep(0.005)
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(6)]
    d = threading.Thread(target=deleter)
    d.start()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    d.join()

    assert not errors, f"append raised: {errors[:3]}"
    entries = AuditLog(path).entries()
    seqs = [e["seq"] for e in entries]
    assert len(set(seqs)) == len(seqs), "duplicate seqs: lock was not exclusive"
    assert sorted(seqs) == list(range(len(entries)))
    assert verify_chain(path)[0]


def test_lock_thrashing_refuses_rather_than_corrupts(tmp_path, monkeypatch):
    """If the lock file is replaced faster than it can be held, appending
    must fail loudly rather than write with a lock it does not own.

    Refusing is the correct outcome: a lost record is recoverable from
    the caller's error, a corrupted chain is not.
    """
    import friday.audit as audit_mod

    log = AuditLog(tmp_path / "audit.jsonl")
    real_stat = audit_mod.os.stat

    def always_different(path_arg, *a, **kw):
        # Simulate the lock file being replaced between every attempt.
        result = real_stat(path_arg, *a, **kw)
        if str(path_arg).endswith(".lock"):
            raise FileNotFoundError(str(path_arg))
        return result

    monkeypatch.setattr(audit_mod.os, "stat", always_different)
    with pytest.raises(OSError, match="stable lock"):
        log.append(a_record())


def test_verify_chain_works_without_write_access(tmp_path):
    """An auditor who can read the log must be able to verify it.

    The log is evidence (R6), so verification cannot require write
    access to the directory holding it — an archived or copied log
    typically has no lock file beside it, and the reader typically has
    no permission to create one.
    """
    import os
    import stat

    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    for i in range(3):
        log.append(a_record(utterance=f"u{i}"))

    # An archived copy: the log and its side-car, no lock file.
    path.with_suffix(path.suffix + ".lock").unlink()
    os.chmod(tmp_path, stat.S_IRUSR | stat.S_IXUSR)  # r-x, no write
    try:
        assert verify_chain(path)[0]
    finally:
        os.chmod(tmp_path, stat.S_IRWXU)


def test_verify_chain_detects_tampering_without_write_access(tmp_path):
    """The read-only path must not become a path that skips checking."""
    import os
    import stat

    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    for i in range(3):
        log.append(a_record(utterance=f"u{i}"))

    lines = path.read_text().splitlines()
    rec = json.loads(lines[1])
    rec["tool"] = "delete_everything"
    lines[1] = json.dumps(rec)
    path.write_text("\n".join(lines) + "\n")
    path.with_suffix(path.suffix + ".lock").unlink()

    os.chmod(tmp_path, stat.S_IRUSR | stat.S_IXUSR)
    try:
        ok, bad, status = verify_chain(path, detailed=True)
        assert not ok
        assert status == "tampered"
        assert bad == 1
    finally:
        os.chmod(tmp_path, stat.S_IRWXU)


@requires_unprivileged
@pytest.mark.parametrize("attack", ["edit", "truncate", "reorder", "empty"])
def test_unlocked_fallback_never_reports_a_tampered_log_as_ok(tmp_path, attack):
    """The fallback may cost availability of the lock; it must never
    cost the verdict.

    A false "incomplete" on a live log is tolerable. A false "ok" on a
    tampered one would make the whole mechanism worthless, so it is
    checked on the exact path an auditor takes: an archived copy with
    no lock file, in a directory they cannot write to.
    """
    import stat

    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    for i in range(5):
        log.append(a_record(utterance=f"u{i}"))

    lines = path.read_text().splitlines()
    if attack == "edit":
        rec = json.loads(lines[2])
        rec["tool"] = "delete_everything"
        lines[2] = json.dumps(rec)
        path.write_text("\n".join(lines) + "\n")
    elif attack == "truncate":
        path.write_text("\n".join(lines[:2]) + "\n")
    elif attack == "reorder":
        lines[1], lines[2] = lines[2], lines[1]
        path.write_text("\n".join(lines) + "\n")
    elif attack == "empty":
        path.write_text("")

    # An archived copy: no lock file, unwritable directory.
    path.with_suffix(path.suffix + ".lock").unlink()
    os.chmod(tmp_path, stat.S_IRUSR | stat.S_IXUSR)
    try:
        ok, _, status = verify_chain(path, detailed=True)
        assert not ok, f"{attack} reported as ok"
        assert status != "ok"
    finally:
        os.chmod(tmp_path, stat.S_IRWXU)


def test_verify_chain_survives_a_failing_lock(tmp_path, monkeypatch):
    """The lock is optional, so a failure to take it must fall back to
    an unlocked read — not fail the verification.

    A transient EINTR on flock would otherwise make an auditor unable to
    check an intact log, which is the availability failure the read-only
    fallback exists to prevent.
    """
    import fcntl

    import friday.audit as audit_mod

    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    for i in range(3):
        log.append(a_record(utterance=f"u{i}"))

    real_flock = audit_mod.fcntl.flock

    def failing_flock(fd, op):
        if op == fcntl.LOCK_SH:
            raise InterruptedError(4, "Interrupted system call")
        return real_flock(fd, op)

    monkeypatch.setattr(audit_mod.fcntl, "flock", failing_flock)
    ok, _, status = verify_chain(path, detailed=True)
    assert ok, "a failing optional lock must not fail verification"
    assert status == "ok"


def test_verification_creates_no_state_in_the_audited_directory(tmp_path):
    """Verifying is a read. It must not write to the evidence's home.

    A verifier creating the lock file 0600 under *its* uid would deny it
    to a writer running as anyone else — a read-only operation breaking
    the agent's ability to log. Not creating it at all avoids the
    question entirely, and costs only the lock on a log that has never
    been written by this build.
    """
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    log.append(a_record())

    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.unlink()
    before = {p.name for p in tmp_path.iterdir()}

    assert verify_chain(path)[0]

    assert {p.name for p in tmp_path.iterdir()} == before, "verification wrote to the directory"

    # The agent must still be able to append afterwards.
    log.append(a_record(utterance="after verification"))
    assert verify_chain(path)[0]


def test_failing_unlock_does_not_deadlock_later_appends(tmp_path, monkeypatch):
    """A failed LOCK_UN must not leave the lock held forever.

    The fd carries the exclusive lock, so skipping its close strands the
    lock: the first append raises and every later one blocks forever.
    The agent then stops logging silently, which is the one failure mode
    this module exists to prevent. Unlikely to trigger on local flock,
    but the cost of guarding is a try/except and the cost of not
    guarding is permanent, invisible data loss.
    """
    import fcntl
    import signal

    import friday.audit as audit_mod

    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    log.append(a_record())

    real_flock = audit_mod.fcntl.flock

    def unlock_fails(fd, op):
        if op == fcntl.LOCK_UN:
            raise InterruptedError(4, "Interrupted system call")
        return real_flock(fd, op)

    monkeypatch.setattr(audit_mod.fcntl, "flock", unlock_fails)
    # The record is already durably written by the time the unlock runs,
    # so a failed unlock is not a failed append: it succeeds, and the
    # lock is released by closing the fd regardless.
    log.append(a_record(utterance="unlock fails here"))
    monkeypatch.undo()

    # The lock must be free again: this append must not hang.
    def on_timeout(*_):
        raise TimeoutError("append blocked on a stranded lock")

    previous = signal.signal(signal.SIGALRM, on_timeout)
    signal.alarm(5)
    try:
        log.append(a_record(utterance="after"))
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)

    assert verify_chain(path)[0]


@pytest.fixture
def no_hang():
    """Fail a lock test instead of letting it hang forever.

    A stranded lock makes appends block, so a regression here shows up
    as a test run that never finishes — the worst way to learn about it.
    This turns that into a normal failure.
    """
    import signal

    def on_timeout(*_):
        raise TimeoutError("blocked on a lock for 10s; likely stranded")

    previous = signal.signal(signal.SIGALRM, on_timeout)
    signal.alarm(10)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def test_acquire_lock_returns_a_genuinely_locked_fd(tmp_path):
    """The success path must hand back a still-locked fd.

    It returns from inside a try/finally, so a mistake in the `keep`
    flag would either close the fd before returning it or return one
    that was already unlocked — both silently removing mutual exclusion
    while every other test still passed.
    """
    import fcntl

    log = AuditLog(tmp_path / "audit.jsonl")
    log.append(a_record())

    fd = log._acquire_lock()
    try:
        other = os.open(log._lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(other)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


@pytest.mark.parametrize("attempts", [1, 3, 5])
def test_acquire_lock_retries_exactly_attempts_times(tmp_path, monkeypatch, attempts):
    """A permanently mismatching inode must retry `attempts` times and
    then raise — never return, never loop forever."""
    import friday.audit as audit_mod

    log = AuditLog(tmp_path / "audit.jsonl")
    log.append(a_record())

    real_stat = audit_mod.os.stat
    calls = {"n": 0}

    def never_matches(path_arg, *a, **kw):
        result = real_stat(path_arg, *a, **kw)
        if str(path_arg).endswith(".lock"):
            calls["n"] += 1

            class Mismatched:
                st_dev = result.st_dev
                st_ino = result.st_ino + 999

            return Mismatched()
        return result

    monkeypatch.setattr(audit_mod.os, "stat", never_matches)
    with pytest.raises(OSError, match="stable lock"):
        log._acquire_lock(attempts=attempts)
    assert calls["n"] == attempts


def test_acquire_lock_retries_a_transient_interrupt(tmp_path, monkeypatch):
    """`attempts` must cover an interrupted lock acquisition, not only
    the inode-replaced case.

    EINTR on flock is exactly the transient this module already guards
    against on unlock; leaving acquisition unretried means one unlucky
    signal makes an append raise when the parameter's name promises it
    would retry.
    """
    import fcntl

    import friday.audit as audit_mod

    log = AuditLog(tmp_path / "audit.jsonl")
    log.append(a_record())

    real_flock = audit_mod.fcntl.flock
    calls = {"n": 0}

    def interrupt_once(fd, op):
        if op == fcntl.LOCK_EX:
            calls["n"] += 1
            if calls["n"] == 1:
                raise InterruptedError(4, "Interrupted system call")
        return real_flock(fd, op)

    monkeypatch.setattr(audit_mod.fcntl, "flock", interrupt_once)
    log.append(a_record(utterance="after one interrupt"))
    monkeypatch.undo()

    assert calls["n"] >= 2, "did not retry after EINTR"
    assert verify_chain(log.path)[0]


@pytest.mark.parametrize(
    "target,name,exc",
    [
        ("fcntl", "flock", OSError(9, "Bad file descriptor")),
        ("os", "fstat", OSError(9, "Bad file descriptor")),
        ("os", "stat", FileNotFoundError("gone")),
    ],
)
def test_acquire_lock_leaks_no_fd_on_any_failure(tmp_path, monkeypatch, target, name, exc):
    """Every path that does not return the fd must release it."""
    import friday.audit as audit_mod

    log = AuditLog(tmp_path / "audit.jsonl")
    log.append(a_record())

    def count_fds():
        return len(os.listdir("/dev/fd"))

    module = audit_mod.fcntl if target == "fcntl" else audit_mod.os

    def boom(*a, **kw):
        raise exc

    before = count_fds()
    monkeypatch.setattr(module, name, boom)
    with pytest.raises(OSError):
        log._acquire_lock(attempts=3)
    monkeypatch.undo()

    assert count_fds() == before


def test_verify_chain_on_missing_file_is_not_a_pass(tmp_path):
    """An absent log must never read as 'verified'."""
    ok, _ = verify_chain(tmp_path / "nope.jsonl")
    assert not ok
