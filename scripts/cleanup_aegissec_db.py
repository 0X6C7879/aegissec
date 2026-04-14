from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path


DEFAULT_DB_PATH = Path("apps/api/data/aegissec.db")


@dataclass(slots=True)
class DbSnapshot:
    file_size_bytes: int
    wal_size_bytes: int
    page_size: int
    page_count: int
    freelist_count: int
    session_event_rows: int


def _non_negative_int(raw: str) -> int:
    value = int(raw)
    if value < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return value


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "One-click cleanup for aegissec SQLite DB. "
            "No backup will be created."
        )
    )
    parser.add_argument(
        "db_path",
        nargs="?",
        default=str(DEFAULT_DB_PATH),
        help="SQLite database path (default: apps/api/data/aegissec.db)",
    )
    parser.add_argument(
        "--keep-events-per-session",
        type=_non_negative_int,
        default=0,
        help=(
            "Keep latest N rows per session in session_event_log. "
            "0 means delete all rows from session_event_log."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be deleted (no changes written).",
    )
    parser.add_argument(
        "--skip-vacuum",
        action="store_true",
        help="Skip VACUUM step (faster, but file may not shrink immediately).",
    )
    parser.add_argument(
        "--delete-batch-size",
        type=_non_negative_int,
        default=2000,
        help=(
            "Batch size used by low-space fallback delete path. "
            "Only used when standard delete fails with disk full."
        ),
    )
    return parser.parse_args()


def _format_mb(size_bytes: int) -> str:
    return f"{size_bytes / 1024 / 1024:.2f}"


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def _count_rows(conn: sqlite3.Connection, table_name: str) -> int:
    if not _table_exists(conn, table_name):
        return 0
    row = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
    if row is None:
        return 0
    return int(row[0])


def _snapshot(conn: sqlite3.Connection, db_path: Path) -> DbSnapshot:
    page_size = int(conn.execute("PRAGMA page_size;").fetchone()[0])
    page_count = int(conn.execute("PRAGMA page_count;").fetchone()[0])
    freelist_count = int(conn.execute("PRAGMA freelist_count;").fetchone()[0])
    wal_path = Path(f"{db_path}-wal")
    wal_size = wal_path.stat().st_size if wal_path.exists() else 0
    session_event_rows = _count_rows(conn, "session_event_log")
    return DbSnapshot(
        file_size_bytes=db_path.stat().st_size,
        wal_size_bytes=wal_size,
        page_size=page_size,
        page_count=page_count,
        freelist_count=freelist_count,
        session_event_rows=session_event_rows,
    )


def _cleanup_with_window_function(conn: sqlite3.Connection, keep_events_per_session: int) -> None:
    conn.execute(
        """
        DELETE FROM session_event_log
        WHERE cursor IN (
            SELECT cursor
            FROM (
                SELECT
                    cursor,
                    ROW_NUMBER() OVER (PARTITION BY session_id ORDER BY cursor DESC) AS rn
                FROM session_event_log
            ) ranked
            WHERE rn > ?
        );
        """,
        (keep_events_per_session,),
    )


def _cleanup_with_fallback_loop(conn: sqlite3.Connection, keep_events_per_session: int) -> None:
    session_rows = conn.execute(
        "SELECT DISTINCT session_id FROM session_event_log"
    ).fetchall()
    for session_row in session_rows:
        session_id = str(session_row[0])
        conn.execute(
            """
            DELETE FROM session_event_log
            WHERE session_id = ?
              AND cursor NOT IN (
                  SELECT cursor
                  FROM session_event_log
                  WHERE session_id = ?
                  ORDER BY cursor DESC
                  LIMIT ?
              );
            """,
            (session_id, session_id, keep_events_per_session),
        )


def _cleanup_session_event_log(conn: sqlite3.Connection, keep_events_per_session: int) -> int:
    if not _table_exists(conn, "session_event_log"):
        return 0

    before_count = _count_rows(conn, "session_event_log")
    if before_count == 0:
        return 0

    if keep_events_per_session == 0:
        conn.execute("DELETE FROM session_event_log;")
    else:
        try:
            _cleanup_with_window_function(conn, keep_events_per_session)
        except sqlite3.OperationalError:
            _cleanup_with_fallback_loop(conn, keep_events_per_session)

    after_count = _count_rows(conn, "session_event_log")
    return max(0, before_count - after_count)


def _cleanup_session_event_log_low_space(
    conn: sqlite3.Connection,
    *,
    keep_events_per_session: int,
    delete_batch_size: int,
) -> int:
    if not _table_exists(conn, "session_event_log"):
        return 0

    if keep_events_per_session != 0:
        raise sqlite3.OperationalError(
            "Low-space fallback currently supports only --keep-events-per-session 0."
        )

    if delete_batch_size <= 0:
        raise sqlite3.OperationalError("--delete-batch-size must be > 0 for low-space fallback.")

    conn.execute("PRAGMA temp_store = MEMORY;")
    conn.execute("PRAGMA synchronous = OFF;")
    conn.execute("PRAGMA journal_mode = OFF;")

    total_deleted = 0
    while True:
        conn.execute(
            """
            DELETE FROM session_event_log
            WHERE cursor IN (
                SELECT cursor
                FROM session_event_log
                ORDER BY cursor
                LIMIT ?
            );
            """,
            (delete_batch_size,),
        )
        deleted_in_batch = int(conn.execute("SELECT changes();").fetchone()[0])
        conn.commit()
        if deleted_in_batch <= 0:
            break
        total_deleted += deleted_in_batch

    return total_deleted


def _print_snapshot(title: str, snapshot: DbSnapshot) -> None:
    estimated_free_bytes = snapshot.page_size * snapshot.freelist_count
    print(title)
    print(f"  main db size: {_format_mb(snapshot.file_size_bytes)} MB")
    print(f"  wal size: {_format_mb(snapshot.wal_size_bytes)} MB")
    print(
        "  page_size/page_count/freelist_count: "
        f"{snapshot.page_size}/{snapshot.page_count}/{snapshot.freelist_count}"
    )
    print(f"  estimated free bytes: {_format_mb(estimated_free_bytes)} MB")
    print(f"  session_event_log rows: {snapshot.session_event_rows}")


def _is_disk_full_error(exc: sqlite3.Error) -> bool:
    return "database or disk is full" in str(exc).lower()


def main() -> int:
    args = _parse_args()
    db_path = Path(args.db_path).expanduser().resolve()

    if not db_path.is_file():
        print(f"[ERROR] DB file not found: {db_path}")
        return 1

    print("[INFO] Running cleanup (no backup).")
    print(f"[INFO] Target DB: {db_path}")
    print(
        "[INFO] keep-events-per-session="
        f"{args.keep_events_per_session} | dry-run={args.dry_run} | skip-vacuum={args.skip_vacuum}"
    )
    print(f"[INFO] delete-batch-size={args.delete_batch_size}")

    current_stage = "open"

    try:
        with sqlite3.connect(db_path) as conn:
            current_stage = "snapshot-before"
            conn.execute("PRAGMA busy_timeout = 120000;")
            before = _snapshot(conn, db_path)
            _print_snapshot("[BEFORE]", before)

            current_stage = "delete"
            delete_used_low_space_fallback = False
            try:
                conn.execute("BEGIN IMMEDIATE;")
                deleted_rows = _cleanup_session_event_log(conn, args.keep_events_per_session)
                current_stage = "commit"
                conn.commit()
            except sqlite3.Error as delete_exc:
                conn.rollback()
                if not _is_disk_full_error(delete_exc):
                    raise

                print(
                    "[WARN] Standard delete path failed due to low disk space. "
                    "Retrying low-space delete fallback..."
                )
                current_stage = "delete-low-space"
                deleted_rows = _cleanup_session_event_log_low_space(
                    conn,
                    keep_events_per_session=args.keep_events_per_session,
                    delete_batch_size=args.delete_batch_size,
                )
                delete_used_low_space_fallback = True

            if args.dry_run:
                conn.rollback()
                print(f"[DRY-RUN] Would delete {deleted_rows} rows from session_event_log.")
                return 0

            current_stage = "wal-checkpoint"
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            except sqlite3.Error as checkpoint_exc:
                if _is_disk_full_error(checkpoint_exc):
                    print(
                        "[WARN] WAL checkpoint skipped due to low disk space. "
                        "Cleanup rows are already committed."
                    )
                else:
                    raise

            vacuum_skipped_due_to_disk_full = False
            if not args.skip_vacuum:
                current_stage = "vacuum"
                try:
                    conn.execute("VACUUM;")
                except sqlite3.Error as vacuum_exc:
                    if _is_disk_full_error(vacuum_exc):
                        vacuum_skipped_due_to_disk_full = True
                        print(
                            "[WARN] VACUUM skipped: disk space is insufficient. "
                            "Row cleanup has been committed, but file shrink is not complete."
                        )
                    else:
                        raise

            current_stage = "analyze"
            try:
                conn.execute("ANALYZE;")
            except sqlite3.Error as analyze_exc:
                if _is_disk_full_error(analyze_exc):
                    print("[WARN] ANALYZE skipped due to low disk space.")
                else:
                    raise

            current_stage = "snapshot-after"
            after = _snapshot(conn, db_path)
            _print_snapshot("[AFTER]", after)

        reclaimed_main = max(0, before.file_size_bytes - after.file_size_bytes)
        reclaimed_wal = max(0, before.wal_size_bytes - after.wal_size_bytes)
        print("[DONE] Cleanup finished.")
        print(f"[DONE] Deleted rows from session_event_log: {deleted_rows}")
        print(f"[DONE] Main DB reclaimed: {_format_mb(reclaimed_main)} MB")
        print(f"[DONE] WAL reclaimed: {_format_mb(reclaimed_wal)} MB")
        if delete_used_low_space_fallback:
            print(
                "[DONE] Low-space delete fallback was used "
                "(journal_mode=OFF, synchronous=OFF)."
            )
        if vacuum_skipped_due_to_disk_full:
            print(
                "[DONE] To physically shrink main DB later, free disk space and rerun without --skip-vacuum."
            )
        return 0
    except sqlite3.Error as exc:
        print(f"[ERROR] SQLite cleanup failed at stage '{current_stage}': {exc}")
        if _is_disk_full_error(exc):
            print(
                "[ERROR] Host disk space is insufficient for this stage. "
                "Try rerunning with --skip-vacuum first, then run VACUUM after freeing space."
            )
        return 2


if __name__ == "__main__":
    sys.exit(main())
