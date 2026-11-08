"""Append-only, hash-chained audit log — ARCHITECTURE.md §5.5 (R5, R6).

Every routed decision appends one JSON line carrying the digest of the
record before it. Editing or reordering a record breaks the chain;
a sequence number makes truncation detectable too, which the chain alone
cannot do (a truncated prefix is still internally consistent).

This is *tamper-evident*, not tamper-proof: anything that can write the
file can rewrite the whole chain. The defence is that the executor's
allow-listed scripts have no path that reaches this file — the agent
cannot edit its own evidence.

Concurrency: computing the chain head and appending must be one atomic
step, so `append` holds an exclusive `flock` across the whole read-then-
write sequence. Without it, two writers read the same tail and produce
duplicate sequence numbers — measured, not theoretical: 8 threads x 25
appends produced 95 duplicate seqs and 119 uncaught exceptions before
this lock existed. `flock` is per-open-file-description, so it works
across threads and processes on the same machine, which is what M1.5's
process split (§4.1) needs.

Known limits, stated rather than papered over:

- If the lock file is replaced *faster than a writer can acquire and
  verify it* (a process unlinking it in a tight loop), `append` gives up
  and raises after exhausting its retry budget. That is deliberate — refusing to
  write loses one record, writing without a lock corrupts the chain —
  but it does mean a hostile local process can deny logging. Anything
  with that access can also delete the log outright, so this is not a
  new capability, and it fails loudly.
- Durability rests on fsync of the data file, the side-car, and the
  containing directory. On a filesystem that ignores fsync (or hardware
  that lies about flushing), a crash can still lose the tail. That case
  is reported as `incomplete`/`rolled_back`, never as `ok`.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

GENESIS = "0" * 64

# M4 retention default (ARCHITECTURE.md §M4): 90 days, user-configurable
# via Settings (`audit_retention_days`) and `--retain-days`. "Weeks, not
# hours" (R5) — incidents get noticed days later.
DEFAULT_RETENTION_DAYS = 90
MIN_RETENTION_DAYS = 7
MAX_RETENTION_DAYS = 365


def _fsync_dir(directory: Path) -> None:
    """Make a rename or file creation in `directory` durable.

    fsync on a file persists its *contents*; it does not persist the
    directory entry that names it. Without this, a crash just after
    `os.replace` can lose the rename itself — the new side-car's bytes
    are safely on disk under a name nothing points to, and the log comes
    back claiming a stale count. Best-effort: some filesystems refuse to
    fsync a directory, and failing the append over that would be worse
    than the durability gap it closes.
    """
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _digest(payload: dict) -> str:
    """Hash over the record's canonical form, excluding the hash field."""
    body = {k: v for k, v in payload.items() if k != "hash"}
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


class AuditLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _tail(self) -> tuple[str, int]:
        """Return (last hash, next sequence number)."""
        if not self.path.exists():
            base = self._read_retention()
            return base["pruned_through_hash"], base["pruned_count"]
        last = None
        with self.path.open() as fh:
            for line in fh:
                if line.strip():
                    last = line
        if last is None:
            return GENESIS, 0
        rec = json.loads(last)
        return rec["hash"], rec["seq"] + 1

    @property
    def _head_path(self) -> Path:
        """Side-car recording the expected tail.

        The chain alone cannot detect truncation: lopping records off the
        end leaves a self-consistent prefix. Keeping the expected length
        and final digest in a separate file means an attacker must edit
        both, in agreement, to hide a deletion.
        """
        return self.path.with_suffix(self.path.suffix + ".head")

    @property
    def _lock_path(self) -> Path:
        return self.path.with_suffix(self.path.suffix + ".lock")

    @property
    def _retention_path(self) -> Path:
        """Checkpoint written only by compliant expiry (M4).

        A raw prefix deletion without this file reads as tampered /
        rolled_back; a prune through `enforce_retention` records the
        deleted count and tail hash here so verification can tell
        policy-driven expiry apart from an attack.
        """
        return self.path.with_suffix(self.path.suffix + ".retention")

    def _read_retention(self) -> dict:
        try:
            data = json.loads(self._retention_path.read_text())
        except (OSError, ValueError):
            return {"pruned_count": 0, "pruned_through_hash": GENESIS}
        count = data.get("pruned_count", 0)
        digest = data.get("pruned_through_hash", GENESIS)
        if not isinstance(count, int) or count < 0:
            count = 0
        if not isinstance(digest, str) or len(digest) != 64:
            digest = GENESIS
        return {"pruned_count": count, "pruned_through_hash": digest}

    def _acquire_lock(self, attempts: int = 25) -> int:
        """Lock the file, confirming we locked the *live* inode.

        `flock` attaches to an open file description, so if the lock
        file is unlinked while a writer holds it (a cleanup script
        mistaking it for a stale artifact), the next writer's O_CREAT
        makes a *new* inode and locks that one instead. Both writers
        then believe they hold the same lock, silently restoring the
        race this lock exists to prevent.

        Re-stat the path after locking: if it no longer names the inode
        we hold, someone replaced it — drop it and retry.

        `attempts` covers both retryable cases: the lock file being
        replaced under us, and an interrupted acquisition (EINTR). Other
        errors are real and propagate immediately rather than being
        retried into a delay.

        The budget is 25, not a handful. Measured with six writers and a
        deleter racing: a contended acquire used up to 4 attempts on an
        idle machine, so a limit of 5 left almost no headroom and raised
        intermittently under load — a ~1-in-5 flake in a *safety* test,
        which teaches whoever sees it to re-run rather than investigate.
        Each retry also yields briefly, so they do not spin straight back
        into the same collision.
        """
        for attempt in range(attempts):
            fd = os.open(self._lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
            keep = False
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                held = os.fstat(fd)
                current = os.stat(self._lock_path)
                if (held.st_dev, held.st_ino) == (current.st_dev, current.st_ino):
                    keep = True
                    return fd
            except (FileNotFoundError, InterruptedError):
                # Replaced under us, or a signal interrupted the call.
                pass
            finally:
                # Every path that does not return the fd must release
                # it. Closing is what frees the lock, so it happens even
                # if the unlock call itself fails.
                if not keep:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_UN)
                    except OSError:
                        pass
                    os.close(fd)
                    # Yield before retrying so contended writers interleave
                    # rather than colliding again immediately. Skipped on
                    # the last attempt: there is nothing left to retry, so
                    # sleeping only delays the raise.
                    if attempt < attempts - 1:
                        time.sleep(0.001)
        raise OSError(
            f"could not acquire a stable lock on {self._lock_path}: "
            "the lock file keeps being replaced"
        )

    def append(self, record: dict) -> dict:
        """Append one record. Safe across threads and processes.

        Reading the tail and writing the next record must be atomic
        together: doing them under separate locks (or none) lets two
        writers derive the same `seq` and `prev` from the same tail.
        """
        lock_fd = self._acquire_lock()
        try:
            prev, seq = self._tail()
            entry = {**record, "seq": seq, "ts": time.time(), "prev": prev}
            entry["hash"] = _digest(entry)

            line = json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n"
            is_new = not self.path.exists()
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, line.encode())
                os.fsync(fd)
            finally:
                os.close(fd)
            if is_new:
                # Persist the directory entry for the log itself, not
                # just its contents.
                _fsync_dir(self.path.parent)

            self._write_head(seq + 1, entry["hash"])
            return entry
        finally:
            # Closing the fd releases the lock, so the close is what
            # actually matters — an unguarded LOCK_UN that raised would
            # skip it and strand the exclusive lock, deadlocking every
            # later append and silently stopping the agent logging.
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(lock_fd)

    def _write_head(self, count: int, digest: str) -> None:
        """Replace the side-car atomically.

        The temp file gets a unique name: a shared one races with other
        writers, who delete or rename it mid-flight.
        """
        head = json.dumps({"count": count, "hash": digest})
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self._head_path.parent),
            prefix=self._head_path.name + ".",
            suffix=".tmp",
        )
        try:
            try:
                os.write(fd, head.encode())
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(tmp_name, self._head_path)
            _fsync_dir(self._head_path.parent)
        except BaseException:
            # Don't leave the temp file behind on a failing disk.
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
            raise

    def entries(self) -> list[dict]:
        if not self.path.exists():
            return []
        with self.path.open() as fh:
            return [json.loads(ln) for ln in fh if ln.strip()]

    def _write_retention(
        self, pruned_count: int, pruned_through_hash: str, cutoff: float
    ) -> None:
        """Replace the retention checkpoint atomically (M4)."""
        payload = json.dumps(
            {
                "pruned_count": pruned_count,
                "pruned_through_hash": pruned_through_hash,
                "cutoff": cutoff,
                "pruned_at": time.time(),
            }
        )
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self._retention_path.parent),
            prefix=self._retention_path.name + ".",
            suffix=".tmp",
        )
        try:
            try:
                os.write(fd, payload.encode())
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(tmp_name, self._retention_path)
            _fsync_dir(self._retention_path.parent)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
            raise


def enforce_retention(
    path: Path, retain_days: int = DEFAULT_RETENTION_DAYS, now: float | None = None
) -> int:
    """Delete records older than `retain_days`, tamper-evidently (M4, R5).

    Records with `ts` below the cutoff are dropped; the survivors keep
    their original `seq`/`prev`/`hash` verbatim, and a `.retention`
    checkpoint records the deleted count and tail hash so `verify_chain`
    can tell compliant expiry apart from a prefix-deletion attack (which
    writes no checkpoint and verifies as tampered/rolled_back).

    Returns the number of records pruned. Never raises on a missing log.
    """
    try:
        days = int(retain_days)
    except (TypeError, ValueError):
        days = DEFAULT_RETENTION_DAYS
    days = max(MIN_RETENTION_DAYS, min(MAX_RETENTION_DAYS, days))
    moment = time.time() if now is None else now
    cutoff = moment - days * 86400

    log = AuditLog(path)
    lock_fd = log._acquire_lock()
    try:
        base = log._read_retention()
        if not log.path.exists():
            return 0
        with log.path.open() as fh:
            entries = [json.loads(ln) for ln in fh if ln.strip()]
        split = 0
        for entry in entries:
            ts = entry.get("ts")
            # A record without a timestamp is evidence: keep it rather
            # than deleting what cannot be aged.
            if isinstance(ts, (int, float)) and ts < cutoff:
                split += 1
            else:
                break
        if split <= 0:
            return 0
        remaining = entries[split:]
        through_hash = entries[split - 1].get("hash", base["pruned_through_hash"])
        new_base = base["pruned_count"] + split
        if remaining:
            tmp = log.path.with_suffix(log.path.suffix + ".prune.tmp")
            with tmp.open("w") as fh:
                for entry in remaining:
                    fh.write(
                        json.dumps(entry, sort_keys=True, separators=(",", ":"))
                        + "\n"
                    )
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, log.path)
            _fsync_dir(log.path.parent)
            log._write_retention(new_base, through_hash, cutoff)
            log._write_head(new_base + len(remaining), remaining[-1]["hash"])
        else:
            try:
                log.path.unlink()
            except FileNotFoundError:
                pass
            log._write_retention(new_base, through_hash, cutoff)
            log._write_head(new_base, through_hash)
        return split
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(lock_fd)


def verify_chain(path: Path, detailed: bool = False):
    """Verify the log end to end.

    Returns (ok, index_of_first_bad_record), or with `detailed=True`
    (ok, index, status) where status is one of:

      "ok"          - chain and side-car agree
      "missing"     - no log, or an empty one
      "tampered"    - a record was edited, reordered, or removed
      "incomplete"  - the side-car lags the data by a suffix of records,
                      which is what an interrupted append looks like
      "rolled_back" - the side-car claims MORE records than exist, i.e.
                      the tail was cut off
      "pruned"      - the log was correctly expired by retention policy
                      (M4): no live records remain, checkpoint present

    The incomplete/tampered split matters: a crash between the data
    write and the side-car update is recoverable and ordinary, while an
    edited record is an attack. Reporting a power cut as tampering would
    train whoever reads these alarms to ignore them.

    A missing or empty log is *not* a pass — absence of evidence is not
    evidence of integrity.
    """

    def result(ok, index, status):
        return (ok, index, status) if detailed else (ok, index)

    path = Path(path)
    retention_path = path.with_suffix(path.suffix + ".retention")
    base_count, base_hash = 0, GENESIS
    if retention_path.exists():
        try:
            checkpoint = json.loads(retention_path.read_text())
            count = checkpoint.get("pruned_count", 0)
            digest = checkpoint.get("pruned_through_hash", GENESIS)
            if isinstance(count, int) and count >= 0:
                base_count = count
            if isinstance(digest, str) and len(digest) == 64:
                base_hash = digest
        except (OSError, ValueError):
            pass
    if not path.exists():
        if base_count > 0:
            return result(True, None, "pruned")
        return result(False, None, "missing")

    # Read the data file and the side-car under a shared lock, so the
    # two reads cannot straddle an in-flight append. Without it, a
    # healthy log being written to reports "incomplete" (or, depending
    # on read ordering, "rolled_back") — measured at ~38% of reads
    # against a live writer. A verifier that cries wolf that often
    # teaches its operator to ignore the alarm that matters.
    head_path = path.with_suffix(path.suffix + ".head")
    lock_path = path.with_suffix(path.suffix + ".lock")

    # The lock is an optimisation for reading a *live* log, never a
    # requirement: verification must work for an auditor who can read
    # the evidence but not write beside it (R6) — an archived or copied
    # log usually has no lock file and lives in a directory the reader
    # cannot create one in. Fall back to an unlocked read, which is
    # correct for a quiescent log and at worst reports a transient
    # "incomplete" on a live one.
    #
    # Two properties verified on this platform rather than assumed:
    #  - O_RDONLY is enough for LOCK_SH, and such a shared lock really
    #    does block a LOCK_EX writer (flock ignores the fd's access
    #    mode, unlike fcntl/POSIX locks). So this is genuine mutual
    #    exclusion, not a decorative call.
    #  - The unlocked fallback still detects edits, reordering,
    #    truncation and an emptied log. It can cost availability of the
    #    lock, never the integrity verdict: no false "ok" is reachable.
    # Only ever *open* the lock, never create it: verification is a read
    # and must leave no state in the directory holding the evidence. A
    # lock file created here would be owned by the verifier at 0600, and
    # would then deny the lock to an agent running as another uid —
    # a read-only operation breaking the write path.
    lock_fd = None
    try:
        lock_fd = os.open(lock_path, os.O_RDONLY)
    except OSError:
        lock_fd = None  # absent or unreadable: read without it

    locked = False
    try:
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_SH)
                locked = True
            except OSError:
                # The lock is optional. Failing to take it (EINTR, or a
                # filesystem that does not support flock) must not fail
                # the verification of an otherwise intact log.
                locked = False
        raw = path.read_text()
        head_raw = head_path.read_text() if head_path.exists() else None
    finally:
        if lock_fd is not None:
            if locked:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            os.close(lock_fd)

    entries = [json.loads(ln) for ln in raw.splitlines() if ln.strip()]
    if not entries:
        if base_count > 0:
            return result(True, None, "pruned")
        return result(False, None, "missing")

    # M4: after compliant pruning the survivors keep their original
    # seq numbers, so the chain starts at base_count (not 0) and the
    # first prev must equal the checkpoint's tail hash. A raw prefix
    # deletion writes no checkpoint, so base stays 0 and the shifted
    # seqs/prev read as tampered — which is the point.
    prev = base_hash
    for offset, entry in enumerate(entries):
        if entry.get("prev") != prev:
            return result(False, offset, "tampered")
        if entry.get("seq") != base_count + offset:
            return result(False, offset, "tampered")
        if entry.get("hash") != _digest(entry):
            return result(False, offset, "tampered")
        prev = entry["hash"]

    # Cross-check the tail against the side-car, which is what makes
    # truncation visible — the chain alone cannot see a missing tail.
    # Counts are global (base + live), not live-only.
    expected_count = base_count + len(entries)
    if head_raw is not None:
        head = json.loads(head_raw)
        count = head.get("count")

        if count == expected_count and head.get("hash") == prev:
            return result(True, None, "ok")

        if (
            isinstance(count, int)
            and base_count < count < expected_count
        ):
            # The side-car points at an earlier record that is still
            # present and intact: an append was interrupted, not forged.
            if head.get("hash") == entries[count - base_count - 1].get("hash"):
                return result(False, count, "incomplete")

        # The side-car claims records the file no longer has: the tail
        # was removed.
        return result(False, len(entries), "rolled_back")

    return result(True, None, "ok")
