"""Reusable readers for file-based source formats."""

from __future__ import annotations

import csv
import io
import json
import logging
from collections import deque
import xml.etree.ElementTree as ET
from typing import Any, Dict, Optional

import pandas as pd
from fastapi import HTTPException

logger = logging.getLogger(__name__)


def _normalize_delimiter(value: Optional[str], file_type: str) -> str:
    """Normalize CSV/TSV delimiters."""
    if value not in (None, "", False):
        text = str(value)
        if text in (r"\t", r"\\t"):
            return "\t"
        if text in (r"\n", r"\\n"):
            return "\n"
        if text in (r"\r", r"\\r"):
            return "\r"
        return text[0]
    return "\t" if str(file_type).lower() == "tsv" else ","


def _normalize_quotechar(value: Optional[str]) -> Optional[str]:
    """Normalize a quote character."""
    if value in (None, "", False):
        return None
    text = str(value)
    if text in (r"\"", r"\\\""):
        return '"'
    return text[0]


def _normalize_escapechar(value: Optional[str]) -> Optional[str]:
    """Normalize an escape character."""
    if value in (None, "", False):
        return None
    text = str(value)
    if text in (r"\\", r"\\\\"):
        return "\\"
    if text in (r"\t", r"\\t"):
        return "\t"
    if text in (r"\n", r"\\n"):
        return "\n"
    if text in (r"\r", r"\\r"):
        return "\r"
    return text[0]


def _reset_stream(stream) -> None:
    """Rewind a stream when it supports seeking."""
    if not hasattr(stream, "seek") or not callable(getattr(stream, "seek", None)):
        raise ValueError("Stream does not support seeking")
    stream.seek(0)


def _stream_text_prefix(stream, max_bytes: int) -> str:
    """Read only a bounded prefix from a stream as text."""
    _reset_stream(stream)
    data = stream.read(max_bytes)
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="ignore")
    return str(data or "")


def _stream_text_lines(stream):
    """Yield decoded text lines without buffering the full file."""
    _reset_stream(stream)
    while True:
        line = stream.readline()
        if not line:
            break
        if isinstance(line, bytes):
            yield line.decode("utf-8", errors="ignore")
        else:
            yield str(line)


def _read_json_lines_sample(stream, sample_rows: int):
    rows = []
    for line in _stream_text_lines(stream):
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
        if len(rows) >= sample_rows:
            break
    return rows


def _read_json_array_sample(stream, sample_rows: int):
    decoder = json.JSONDecoder()
    records = []
    buffer = ""
    started = False
    finished = False

    _reset_stream(stream)

    while not finished and len(records) < sample_rows:
        chunk = stream.read(65536)
        if not chunk:
            finished = True
        else:
            if isinstance(chunk, bytes):
                buffer += chunk.decode("utf-8", errors="ignore")
            else:
                buffer += str(chunk)

        while True:
            buffer = buffer.lstrip()
            if not buffer:
                break
            if not started:
                if buffer[0] != "[":
                    raise ValueError("JSON content is not an array")
                started = True
                buffer = buffer[1:]
                continue
            if buffer[0] == "]":
                finished = True
                buffer = buffer[1:]
                break
            if buffer[0] == ",":
                buffer = buffer[1:]
                continue
            try:
                record, offset = decoder.raw_decode(buffer)
            except json.JSONDecodeError:
                break
            records.append(record)
            buffer = buffer[offset:]
            if len(records) >= sample_rows:
                break

    return records


def _read_xml_packed_prefix(stream, sample_rows: int) -> str:
    preview_bytes = max(1024, min(sample_rows * 8, 65536))
    return _stream_text_prefix(stream, preview_bytes).strip()


def _read_xml_records_sample(stream, sample_rows: int):
    records = []
    root = None
    depth = 0

    _reset_stream(stream)

    context = ET.iterparse(stream, events=("start", "end"))
    for event, elem in context:
        if event == "start":
            if root is None:
                root = elem
                depth = 1
            else:
                depth += 1
            continue

        if root is None:
            continue

        if elem is root:
            if not records and len(list(root)) == 0:
                record = {root.tag: (root.text or "").strip()}
                if any(value for value in record.values()):
                    records.append(record)
            break

        if depth == 2:
            record: Dict[str, Any] = {}
            for child in list(elem):
                record[child.tag] = (child.text or "").strip()
            if not record:
                record[elem.tag] = (elem.text or "").strip()
            records.append(record)
            elem.clear()
            if len(records) >= sample_rows:
                break

        depth -= 1

    return records


def read_dataframe_sample(
    stream,
    *,
    file_type: str,
    delimiter: Optional[str] = None,
    quotechar: Optional[str] = None,
    escape_char: Optional[str] = None,
    skipheaderlines: Optional[int] = None,
    skip_footer_lines: Optional[int] = None,
    line_separator: Optional[str] = None,
    json_mode: Optional[str] = None,
    xml_mode: Optional[str] = None,
    sample_rows: int = 1000,
):
    """Read a sample DataFrame from a remote file stream.

    Args:
        stream: File-like object from SFTP.
        file_type: File type to read.
        delimiter: CSV/TSV delimiter.
        quotechar: CSV/TSV quote character.
        escape_char: CSV/TSV escape character.
        skipheaderlines: Number of header rows to skip.
        skip_footer_lines: Number of footer rows to skip.
        line_separator: Custom line separator for delimited files.
        json_mode: JSON parsing mode.
        xml_mode: XML parsing mode.
        sample_rows: Maximum number of rows to read.

    Returns:
        pandas.DataFrame with sampled data.

    Raises:
        HTTPException: If the file format cannot be parsed.
    """
    file_type = str(file_type or "").strip().lower()
    if file_type in ("csv", "tsv"):
        return _read_delimited_sample(
            stream,
            file_type=file_type,
            delimiter=delimiter,
            quotechar=quotechar,
            escape_char=escape_char,
            skipheaderlines=skipheaderlines,
            skip_footer_lines=skip_footer_lines,
            line_separator=line_separator,
            sample_rows=sample_rows,
        )
    if file_type == "json":
        return _read_json_sample(stream, json_mode=json_mode, sample_rows=sample_rows)
    if file_type == "xml":
        return _read_xml_sample(stream, xml_mode=xml_mode, sample_rows=sample_rows)
    if file_type == "avro":
        return _read_avro_sample(stream, sample_rows=sample_rows)
    if file_type == "parquet":
        return _read_parquet_sample(stream, sample_rows=sample_rows)
    raise HTTPException(status_code=422, detail=f"Unsupported file_type: {file_type}")


def _read_delimited_sample(
    stream,
    *,
    file_type: str,
    delimiter: Optional[str],
    quotechar: Optional[str],
    escape_char: Optional[str],
    skipheaderlines: Optional[int],
    skip_footer_lines: Optional[int],
    line_separator: Optional[str],
    sample_rows: int,
):
    sep = _normalize_delimiter(delimiter, file_type)
    q = _normalize_quotechar(quotechar)
    esc = _normalize_escapechar(escape_char)
    skiprows = int(skipheaderlines or 0)
    if skiprows < 0:
        skiprows = 0

    kwargs: Dict[str, Any] = {
        "sep": sep,
        "skiprows": skiprows,
        "nrows": sample_rows,
        "engine": "python",
    }
    if q is not None:
        kwargs["quotechar"] = q
    else:
        kwargs["quoting"] = csv.QUOTE_NONE
    if esc is not None:
        kwargs["escapechar"] = esc

    def _parse_text(text: str):
        # pandas engine="python" rejects any explicit lineterminator; default \n is fine.
        return pd.read_csv(io.StringIO(text), **kwargs)

    def _decode_bytes(raw: bytes) -> str:
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("ISO-8859-1", errors="ignore")

    def _as_text_stream(source_stream, encoding: str):
        if isinstance(source_stream, io.TextIOBase):
            return source_stream, False
        _reset_stream(source_stream)
        return io.TextIOWrapper(source_stream, encoding=encoding, newline=""), True

    skip_footer_count = max(int(skip_footer_lines or 0), 0)

    if skip_footer_count > 0:
        try:
            _reset_stream(stream)
            buffered_lines = []
            footer_window = deque()
            skipped = 0
            emitted = 0

            while emitted < sample_rows:
                raw_line = stream.readline()
                if not raw_line:
                    break
                if skipped < skiprows:
                    skipped += 1
                    continue
                footer_window.append(raw_line)
                if len(footer_window) <= skip_footer_count:
                    continue
                buffered_lines.append(footer_window.popleft())
                emitted += 1

            if not buffered_lines:
                return pd.DataFrame()

            text = _decode_bytes(b"".join(buffered_lines))
            return _parse_text(text)
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Failed to parse {file_type.upper()} file: {exc}",
            ) from exc

    try:
        direct_kwargs = dict(kwargs)
        text_stream, should_detach = _as_text_stream(stream, "utf-8")
        try:
            return pd.read_csv(text_stream, **direct_kwargs)
        finally:
            if should_detach:
                try:
                    text_stream.detach()
                except Exception:
                    pass
    except UnicodeDecodeError:
        _reset_stream(stream)
        direct_kwargs = dict(kwargs)
        text_stream, should_detach = _as_text_stream(stream, "ISO-8859-1")
        try:
            return pd.read_csv(text_stream, **direct_kwargs)
        finally:
            if should_detach:
                try:
                    text_stream.detach()
                except Exception:
                    pass
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Failed to parse {file_type.upper()} file: {exc}",
        ) from exc


def _read_json_sample(stream, *, json_mode: Optional[str], sample_rows: int):
    mode = str(json_mode or "unpacked").strip().lower()
    try:
        if mode == "packed":
            preview_bytes = max(1024, min(sample_rows * 8, 65536))
            return pd.DataFrame({"_DATA": [_stream_text_prefix(stream, preview_bytes).strip()]})

        prefix = _stream_text_prefix(stream, 4096).lstrip()
        if not prefix:
            return pd.DataFrame()

        if prefix.startswith("["):
            payload = _read_json_array_sample(stream, sample_rows)
            return pd.json_normalize(payload)

        rows = _read_json_lines_sample(stream, sample_rows)
        return pd.json_normalize(rows)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Failed to parse JSON file: {exc}") from exc


def _read_xml_sample(stream, *, xml_mode: Optional[str], sample_rows: int):
    mode = str(xml_mode or "unpacked").strip().lower()
    if mode == "packed":
        return pd.DataFrame({"_DATA": [_read_xml_packed_prefix(stream, sample_rows)]})

    try:
        records = _read_xml_records_sample(stream, sample_rows)
        return pd.json_normalize(records)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Failed to parse XML file: {exc}") from exc


def _read_avro_sample(stream, *, sample_rows: int):
    try:
        import fastavro
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Avro dependency missing: fastavro is not installed") from exc

    try:
        records = []
        stream.seek(0)
        for record in fastavro.reader(stream):
            records.append(record)
            if len(records) >= sample_rows:
                break
        return pd.json_normalize(records)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Failed to parse Avro file: {exc}") from exc


def _read_parquet_sample(stream, *, sample_rows: int):
    try:
        import pyarrow.parquet as pq
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Parquet dependency missing: pyarrow is not installed") from exc

    try:
        _reset_stream(stream)
        parquet_file = pq.ParquetFile(stream)
        batches = parquet_file.iter_batches(batch_size=sample_rows)
        first_batch = next(batches, None)
        if first_batch is None:
            return pd.DataFrame()
        return first_batch.to_pandas()
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Failed to parse Parquet file: {exc}") from exc
