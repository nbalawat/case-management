"""File formats a pipeline can read. Every reader yields one dict of text values per record, whatever the format,
so mapping and validation are written once."""
from __future__ import annotations

import csv
import gzip
import io
import json
from collections.abc import Iterator
from datetime import date, datetime
from typing import Any, BinaryIO

FORMATS = ("csv", "jsonl", "parquet")
EXTENSIONS = {".csv": "csv", ".tsv": "csv", ".txt": "csv", ".jsonl": "jsonl", ".ndjson": "jsonl", ".json": "jsonl", ".parquet": "parquet"}


class FormatError(ValueError):
    """The file is not readable in the stated format."""


def detect(key: str) -> tuple[str | None, bool]:
    """(format, gzip-compressed) from the object's name; format is None when the name does not say."""
    name = key.lower()
    gz = name.endswith(".gz")
    if gz:
        name = name[:-3]
    return next((fmt for ext, fmt in EXTENSIONS.items() if name.endswith(ext)), None), gz


def _text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, datetime):
        return v.replace(microsecond=0).isoformat(sep=" ")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _csv(stream: BinaryIO, delimiter: str, encoding: str) -> Iterator[dict]:
    text = io.TextIOWrapper(stream, encoding=encoding, newline="")
    reader = csv.DictReader(text, delimiter=delimiter)
    if not reader.fieldnames:
        return
    for row in reader:
        if None in row:
            raise FormatError(f"line {reader.line_num} has more values than the header has columns")
        yield {k: (v or "") for k, v in row.items()}


def _jsonl(stream: BinaryIO, encoding: str) -> Iterator[dict]:
    for n, line in enumerate(io.TextIOWrapper(stream, encoding=encoding), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as e:
            raise FormatError(f"line {n} is not valid JSON: {e.msg}") from None
        if not isinstance(row, dict):
            raise FormatError(f"line {n} is not a JSON object")
        yield {str(k): _text(v) for k, v in row.items()}


def _parquet(stream: BinaryIO) -> Iterator[dict]:
    try:
        import pyarrow.parquet as pq
    except ImportError:
        raise FormatError("Parquet needs the 'pyarrow' package") from None
    try:
        source = stream if getattr(stream, "seekable", lambda: False)() else io.BytesIO(stream.read())
        file = pq.ParquetFile(source)
        for batch in file.iter_batches(batch_size=5000):
            for row in batch.to_pylist():
                yield {str(k): _text(v) for k, v in row.items()}
    except FormatError:
        raise
    except Exception as e:
        raise FormatError(f"not a readable Parquet file: {str(e).splitlines()[0][:160]}") from None


def read_records(stream: BinaryIO, fmt: str, *, gz: bool = False, delimiter: str = ",", encoding: str = "utf-8") -> Iterator[dict]:
    """Records from an open binary stream. Raises FormatError when the content does not match the format."""
    if fmt not in FORMATS:
        raise FormatError(f"Unknown format '{fmt}'. Use one of: {', '.join(FORMATS)}")
    try:
        if gz:
            stream = gzip.GzipFile(fileobj=stream)  # type: ignore[assignment]
        if fmt == "csv":
            yield from _csv(stream, delimiter, encoding)
        elif fmt == "jsonl":
            yield from _jsonl(stream, encoding)
        else:
            yield from _parquet(stream)
    except (gzip.BadGzipFile, EOFError):
        raise FormatError("not a valid gzip file") from None
    except UnicodeDecodeError:
        raise FormatError(f"not {encoding} text") from None
    except csv.Error as e:
        raise FormatError(f"not readable as delimited text: {e}") from None
