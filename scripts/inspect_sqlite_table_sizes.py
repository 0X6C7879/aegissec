from __future__ import annotations

import argparse
import csv
import io
import json
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class TableSize:
    table_name: str
    data_bytes: int
    index_bytes: int

    @property
    def total_bytes(self) -> int:
        return self.data_bytes + self.index_bytes


def _quote_ident(identifier: str) -> str:
    return f'"{identifier.replace("\"", "\"\"")}"'


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect SQLite table sizes (data/index/total)."
    )
    parser.add_argument("db_path", help="Path to SQLite database file, e.g. apps/api/data/aegissec.db")
    parser.add_argument(
        "--top",
        type=int,
        default=0,
        help="Only show top N tables by total size (0 means all).",
    )
    parser.add_argument(
        "--sort-by",
        choices=("total", "data", "index", "name"),
        default="total",
        help="Sort key for output rows.",
    )
    parser.add_argument(
        "--asc",
        action="store_true",
        help="Sort ascending (default is descending for size sorts).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON instead of text table.",
    )
    return parser.parse_args()


def _format_mb(size_bytes: int) -> str:
    return f"{size_bytes / 1024 / 1024:.2f}"


def _fetch_pragma_int(conn: sqlite3.Connection, pragma_name: str) -> int:
    row = conn.execute(f"PRAGMA {pragma_name};").fetchone()
    if row is None:
        return 0
    value = row[0]
    return int(value) if isinstance(value, int | float) else int(str(value))


def _raw_object_size_query() -> str:
    return """
        SELECT
            m.type AS object_type,
            m.name AS object_name,
            COALESCE(m.tbl_name, m.name) AS table_name,
            SUM(d.pgsize) AS object_bytes
        FROM dbstat AS d
        JOIN sqlite_master AS m
            ON d.name = m.name
        WHERE m.type IN ('table', 'index')
          AND COALESCE(m.tbl_name, m.name) NOT LIKE 'sqlite_%'
        GROUP BY m.type, m.name, COALESCE(m.tbl_name, m.name)
    """


def _fetch_raw_object_rows_via_cli(db_path: Path, sql: str) -> list[tuple[str, str, str, int]] | None:
    try:
        completed = subprocess.run(
            ["sqlite3", "-readonly", "-header", "-csv", str(db_path), sql],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None

    if completed.returncode != 0:
        return None

    csv_text = completed.stdout.strip()
    if not csv_text:
        return []

    rows: list[tuple[str, str, str, int]] = []
    reader = csv.DictReader(io.StringIO(csv_text))
    for line in reader:
        object_type = str(line.get("object_type", ""))
        object_name = str(line.get("object_name", ""))
        table_name = str(line.get("table_name", ""))
        object_bytes = int(line.get("object_bytes", "0") or 0)
        rows.append((object_type, object_name, table_name, object_bytes))
    return rows


def _build_table_sizes(rows: list[tuple[str, str, str, int]]) -> list[TableSize]:
    data_bytes_by_table: dict[str, int] = {}
    index_bytes_by_table: dict[str, int] = {}
    for object_type, _object_name, table_name, object_bytes in rows:
        table_key = str(table_name)
        size = int(object_bytes or 0)
        if object_type == "table":
            data_bytes_by_table[table_key] = data_bytes_by_table.get(table_key, 0) + size
        elif object_type == "index":
            index_bytes_by_table[table_key] = index_bytes_by_table.get(table_key, 0) + size

    table_names = sorted(set(data_bytes_by_table) | set(index_bytes_by_table))
    return [
        TableSize(
            table_name=name,
            data_bytes=data_bytes_by_table.get(name, 0),
            index_bytes=index_bytes_by_table.get(name, 0),
        )
        for name in table_names
    ]


def _fetch_table_sizes_approx(conn: sqlite3.Connection) -> list[TableSize]:
    table_rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()

    approx_rows: list[TableSize] = []
    for (table_name_raw,) in table_rows:
        table_name = str(table_name_raw)
        pragma_sql = f"PRAGMA table_info({_quote_ident(table_name)});"
        column_rows = conn.execute(pragma_sql).fetchall()
        column_names = [str(column_row[1]) for column_row in column_rows]

        if not column_names:
            approx_rows.append(TableSize(table_name=table_name, data_bytes=0, index_bytes=0))
            continue

        data_expr = " + ".join(
            f"COALESCE(LENGTH(CAST({_quote_ident(column_name)} AS BLOB)), 0)"
            for column_name in column_names
        )
        estimate_sql = (
            f"SELECT COALESCE(SUM({data_expr}), 0), COUNT(*) "
            f"FROM {_quote_ident(table_name)}"
        )
        sum_bytes_raw, row_count_raw = conn.execute(estimate_sql).fetchone()
        sum_bytes = int(sum_bytes_raw or 0)
        row_count = int(row_count_raw or 0)

        # 近似补偿每行记录头与变长字段元信息开销。
        estimated_row_overhead_bytes = row_count * 16
        approx_rows.append(
            TableSize(
                table_name=table_name,
                data_bytes=sum_bytes + estimated_row_overhead_bytes,
                index_bytes=0,
            )
        )

    return approx_rows


def _fetch_table_sizes(conn: sqlite3.Connection, *, db_path: Path) -> tuple[list[TableSize], str]:
    sql = _raw_object_size_query()
    try:
        raw_rows = conn.execute(sql).fetchall()
        normalized_rows = [
            (
                str(row[0]),
                str(row[1]),
                str(row[2]),
                int(row[3] or 0),
            )
            for row in raw_rows
        ]
        return _build_table_sizes(normalized_rows), "exact-dbstat"
    except sqlite3.OperationalError:
        cli_rows = _fetch_raw_object_rows_via_cli(db_path, sql)
        if cli_rows is not None:
            return _build_table_sizes(cli_rows), "exact-cli-dbstat"

    approx_rows = _fetch_table_sizes_approx(conn)
    return approx_rows, "approximate-no-dbstat"



def _sort_table_sizes(rows: list[TableSize], *, sort_by: str, asc: bool) -> list[TableSize]:
    if sort_by == "name":
        return sorted(rows, key=lambda row: row.table_name, reverse=not asc)

    if sort_by == "data":
        key_fn = lambda row: (row.data_bytes, row.total_bytes, row.table_name)
    elif sort_by == "index":
        key_fn = lambda row: (row.index_bytes, row.total_bytes, row.table_name)
    else:
        key_fn = lambda row: (row.total_bytes, row.data_bytes, row.table_name)
    return sorted(rows, key=key_fn, reverse=not asc)


def _print_text(
    *,
    db_path: Path,
    rows: list[TableSize],
    measurement_mode: str,
    file_size_bytes: int,
    page_size: int,
    page_count: int,
    freelist_count: int,
    wal_size_bytes: int,
) -> None:
    total_table_bytes = sum(row.total_bytes for row in rows)
    free_bytes = page_size * freelist_count

    print(f"Database: {db_path}")
    print(f"Measurement mode: {measurement_mode}")
    print(f"File size (main db): {_format_mb(file_size_bytes)} MB")
    if wal_size_bytes > 0:
        print(f"WAL size: {_format_mb(wal_size_bytes)} MB")
    print(f"Page size: {page_size} bytes | page_count: {page_count} | freelist_count: {freelist_count}")
    print(f"Estimated free bytes in main db: {_format_mb(free_bytes)} MB")
    print()

    headers = ["table_name", "data_mb", "index_mb", "total_mb", "total_pct"]
    body: list[list[str]] = []
    for row in rows:
        pct = (row.total_bytes / total_table_bytes * 100) if total_table_bytes else 0.0
        body.append(
            [
                row.table_name,
                _format_mb(row.data_bytes),
                _format_mb(row.index_bytes),
                _format_mb(row.total_bytes),
                f"{pct:.2f}%",
            ]
        )

    widths = [len(header) for header in headers]
    for line in body:
        for index, cell in enumerate(line):
            widths[index] = max(widths[index], len(cell))

    header_line = "  ".join(header.ljust(widths[index]) for index, header in enumerate(headers))
    split_line = "  ".join("-" * width for width in widths)
    print(header_line)
    print(split_line)
    for line in body:
        print(
            "  ".join(
                (
                    line[index].ljust(widths[index])
                    if index == 0
                    else line[index].rjust(widths[index])
                )
                for index in range(len(line))
            )
        )


def _print_json(
    *,
    db_path: Path,
    rows: list[TableSize],
    measurement_mode: str,
    file_size_bytes: int,
    page_size: int,
    page_count: int,
    freelist_count: int,
    wal_size_bytes: int,
) -> None:
    total_table_bytes = sum(row.total_bytes for row in rows)
    payload = {
        "database": str(db_path),
        "measurement_mode": measurement_mode,
        "file_size_bytes": file_size_bytes,
        "wal_size_bytes": wal_size_bytes,
        "page_size": page_size,
        "page_count": page_count,
        "freelist_count": freelist_count,
        "estimated_free_bytes": page_size * freelist_count,
        "tables": [
            {
                "table_name": row.table_name,
                "data_bytes": row.data_bytes,
                "index_bytes": row.index_bytes,
                "total_bytes": row.total_bytes,
                "total_pct": (
                    (row.total_bytes / total_table_bytes * 100) if total_table_bytes else 0.0
                ),
            }
            for row in rows
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> int:
    args = _parse_args()
    db_path = Path(args.db_path).expanduser().resolve()
    if not db_path.is_file():
        print(f"Database file not found: {db_path}", file=sys.stderr)
        return 1

    wal_path = Path(f"{db_path}-wal")
    wal_size_bytes = wal_path.stat().st_size if wal_path.exists() else 0

    try:
        with sqlite3.connect(db_path) as conn:
            page_size = _fetch_pragma_int(conn, "page_size")
            page_count = _fetch_pragma_int(conn, "page_count")
            freelist_count = _fetch_pragma_int(conn, "freelist_count")
            rows, measurement_mode = _fetch_table_sizes(conn, db_path=db_path)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except sqlite3.Error as exc:
        print(f"Failed to inspect database: {exc}", file=sys.stderr)
        return 3

    sorted_rows = _sort_table_sizes(rows, sort_by=args.sort_by, asc=args.asc)
    if args.top > 0:
        sorted_rows = sorted_rows[: args.top]

    if args.json:
        _print_json(
            db_path=db_path,
            rows=sorted_rows,
            measurement_mode=measurement_mode,
            file_size_bytes=db_path.stat().st_size,
            page_size=page_size,
            page_count=page_count,
            freelist_count=freelist_count,
            wal_size_bytes=wal_size_bytes,
        )
    else:
        _print_text(
            db_path=db_path,
            rows=sorted_rows,
            measurement_mode=measurement_mode,
            file_size_bytes=db_path.stat().st_size,
            page_size=page_size,
            page_count=page_count,
            freelist_count=freelist_count,
            wal_size_bytes=wal_size_bytes,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
