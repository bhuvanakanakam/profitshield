"""Bounded CSV reader shared by both upload paths; no financial guessing."""
import csv
import io
import re


class CSVImportError(ValueError):
    """A deliberately user-safe CSV error that may be returned by the API."""


def csv_reader(text, max_columns=100):
    if not isinstance(text, str):
        raise CSVImportError('Choose a CSV text file.')
    if '\x00' in text:
        raise CSVImportError('The CSV encoding could not be read. Export CSV UTF-8 or a BOM-marked UTF-16 CSV.')
    source = text.lstrip('\ufeff')
    offset, start = 0, 0
    while start < len(source):
        end = source.find('\n', start)
        if end < 0 or source[start:end].strip():
            break
        offset += 1
        start = end + 1
    source = source[start:]
    if not source.strip():
        raise CSVImportError('The CSV is empty. Include column headers and at least one data row.')
    first, separator, rest = source.partition('\n')
    directive = re.fullmatch(r'sep=([,;\t])', first.strip(' \r'), re.I)
    delimiter = directive.group(1) if directive else None
    if directive:
        source = rest
        offset += 1
    if not source.strip():
        raise CSVImportError('The CSV has no column headers or data rows.')
    if delimiter is None:
        # Use the header record, not money/description cells, to choose the separator.
        candidates = []
        for candidate in (',', ';', '\t'):
            try:
                header = next(csv.reader(io.StringIO(source), delimiter=candidate, strict=True), [])
            except csv.Error:
                continue
            candidates.append((len(header), candidate))
        if not candidates:
            raise CSVImportError('The CSV header has invalid quoting. Export a fresh CSV and try again.')
        delimiter = max(candidates, key=lambda item: item[0])[1]
    reader = csv.DictReader(io.StringIO(source), delimiter=delimiter, strict=True)
    try:
        headers = [header.strip() for header in (reader.fieldnames or [])]
    except csv.Error:
        raise CSVImportError('The CSV header has invalid quoting. Export a fresh CSV and try again.') from None
    if not headers or len(headers) > max_columns or any(not header or len(header) > 100 for header in headers):
        raise CSVImportError('CSV requires nonempty column headers (up to '+str(max_columns)+' columns, 100 characters each).')
    if len(set(headers)) != len(headers):
        raise CSVImportError('CSV column names must be distinct after trimming spaces. Rename duplicate columns and try again.')
    reader.fieldnames = headers
    reader.source_offset = offset
    reader.separator = delimiter
    return reader


def csv_rows(reader):
    try:
        yield from reader
    except csv.Error:
        raise CSVImportError('The CSV has invalid quoting or an oversized cell. Export a fresh CSV and try again.') from None
