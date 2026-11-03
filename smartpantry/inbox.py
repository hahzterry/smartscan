"""Folder drop: batch-scan a directory of receipts and invoices.

Drop scans in any mix of formats; each file becomes one receipt source:

- ``.txt`` — scaffold text format, parsed offline (one ``qty x name @ $``
  line per item);
- ``.json`` — structured line items, parsed offline;
- ``.jpg`` / ``.png`` / ``.pdf`` — photos and scans, read through the
  model vision backend (needs ``META_MODEL_API_*``; otherwise transcribe
  to ``.txt`` / ``.json``).

Name files ``YYYY-MM-DD_Store_rest.ext`` (e.g.
``2026-11-01_Marianos_eggs.jpg``) so date and store are picked up
automatically; ``--date`` / ``--store`` fill in whatever is missing.
Successfully scanned files move to ``processed/`` unless ``--keep``.
"""

import os
import re
from typing import Any, Dict, List, Optional, Tuple

TEXT_EXTENSIONS = {".txt"}
STRUCTURED_EXTENSIONS = {".json"}
VISION_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf"}
SUPPORTED_EXTENSIONS = (TEXT_EXTENSIONS | STRUCTURED_EXTENSIONS
                        | VISION_EXTENSIONS)

_FILENAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})[_-]([^_.][^_.]*)")


def parse_filename(name: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (date, store) from a ``YYYY-MM-DD_Store_...`` filename."""
    match = _FILENAME_RE.match(os.path.basename(name))
    if not match:
        return None, None
    return match.group(1), match.group(2).replace("-", " ").replace("_", " ")


def scan_inbox(path: str, default_date: str,
               default_store: str) -> Dict[str, Any]:
    """Collect receipt sources from every supported file in ``path``."""
    if not os.path.isdir(path):
        raise NotADirectoryError("inbox not found: %s" % path)
    entries: List[Dict[str, str]] = []
    skipped: List[str] = []
    for name in sorted(os.listdir(path)):
        full = os.path.join(path, name)
        if name.startswith(".") or name == "processed":
            continue
        if not os.path.isfile(full):
            skipped.append(name)
            continue
        ext = os.path.splitext(name)[1].lower()
        if ext not in SUPPORTED_EXTENSIONS:
            skipped.append(name)
            continue
        file_date, file_store = parse_filename(name)
        if ext in TEXT_EXTENSIONS:
            with open(full, "r", encoding="utf-8") as handle:
                source = handle.read()
        else:
            source = full
        entries.append({"source": source,
                        "date": file_date or default_date,
                        "store": file_store or default_store,
                        "path": full})
    return {"entries": entries, "skipped": skipped}


def archive(entries: List[Dict[str, str]], inbox_path: str) -> List[str]:
    """Move scanned files into ``processed/``; return new paths."""
    processed = os.path.join(inbox_path, "processed")
    os.makedirs(processed, exist_ok=True)
    moved = []
    for entry in entries:
        src = entry["path"]
        dest = os.path.join(processed, os.path.basename(src))
        os.rename(src, dest)
        moved.append(dest)
    return moved
