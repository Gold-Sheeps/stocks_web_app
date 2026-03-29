from __future__ import annotations

import argparse
import html as html_lib
import json
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.db.database import Database
from app.services.rag_ingest_service import RagIngestService


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Ingest external RSS/Atom news into rag_documents for 12-month backfill (best-effort)."
    )
    p.add_argument("--asof", required=True, help="As-of date YYYY-MM-DD")
    p.add_argument("--months-back", type=int, default=12, help="Backfill window in months (default: 12)")
    p.add_argument("--symbol", default=None, help="Single symbol_key (e.g., US:AAPL)")
    p.add_argument("--tickers-file", default=None, help="Optional ticker file (US:AAPL per line)")
    p.add_argument("--max-symbols", type=int, default=50, help="Max symbols from tickers-file")
    p.add_argument("--feeds-file", default=None, help="Optional JSON feed specs; overrides generated Google RSS URLs")
    p.add_argument(
        "--provider",
        choices=["google-news-rss"],
        default="google-news-rss",
        help="Provider for generated URLs when --feeds-file is omitted",
    )
    p.add_argument("--timeout", type=int, default=15, help="Per-feed HTTP timeout seconds")
    p.add_argument("--dry-run", action="store_true", help="Parse and log only; do not write rag_documents")
    return p.parse_args()


def _load_tickers(path: str | None, symbol: str | None, limit: int) -> List[str]:
    out: List[str] = []
    if symbol:
        s = symbol.strip().upper()
        if ":" not in s:
            s = f"US:{s}"
        out.append(s)
    if path:
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"tickers file not found: {path}")
        for line in p.read_text(encoding="utf-8").splitlines():
            s = line.strip().upper()
            if not s:
                continue
            if ":" not in s:
                s = f"US:{s}"
            if s not in out:
                out.append(s)
            if len(out) >= int(limit):
                break
    return out


def _google_news_rss_url(symbol_key: str, months_back: int) -> str:
    ticker = symbol_key.split(":", 1)[1] if ":" in symbol_key else symbol_key
    company_name = _lookup_company_name(symbol_key)
    # Keep provider query simple; date window is enforced locally via published_at filter.
    # Prefer ticker + company name to reduce ambiguous ticker-only hits.
    q_parts = [ticker]
    if company_name:
        q_parts.append(f'"{company_name}"')
    q_parts.extend(["earnings", "guidance"])
    q = " ".join(q_parts)
    encoded = urllib.parse.quote(q)
    return f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"


def _lookup_company_name(symbol_key: str) -> str | None:
    db = Database()
    if not db.connect():
        return None
    try:
        rows = db.execute_query(
            "SELECT name FROM instruments WHERE symbol_key = %s LIMIT 1",
            (symbol_key.upper(),),
        ) or []
        if rows and rows[0] and rows[0][0]:
            name = str(rows[0][0]).strip()
            # Avoid noisy fund/index labels in fallback names.
            if name and not name.lower().startswith(("index ", "future ", "sector etf ")):
                return name
    except Exception:
        return None
    finally:
        db.disconnect()
    return None


def _load_feed_specs(args: argparse.Namespace, symbols: List[str]) -> List[Dict[str, Any]]:
    if args.feeds_file:
        p = Path(args.feeds_file)
        if not p.exists():
            raise FileNotFoundError(f"feeds file not found: {p}")
        # Accept Windows PowerShell UTF-8 BOM files as well.
        raw = json.loads(p.read_text(encoding="utf-8-sig"))
        if not isinstance(raw, list):
            raise ValueError("feeds file root must be JSON array")
        specs: List[Dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            sym = str(item.get("symbol_key") or "").strip().upper()
            if sym and ":" not in sym:
                sym = f"US:{sym}"
            url = str(item.get("url") or "").strip()
            if not sym or not url:
                continue
            specs.append(
                {
                    "symbol_key": sym,
                    "url": url,
                    "source_type": str(item.get("source_type") or "external_news_rss"),
                    "metadata": dict(item.get("metadata") or {}),
                }
            )
        return specs

    specs = []
    for sym in symbols:
        specs.append(
            {
                "symbol_key": sym,
                "url": _google_news_rss_url(sym, args.months_back),
                "source_type": "google_news_rss",
                "metadata": {"provider": "google-news-rss"},
            }
        )
    return specs


def _http_get(url: str, timeout: int) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "stocks_web_app/1.0 (rss-ingest)",
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.1",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _parse_pub_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        dtv = parsedate_to_datetime(raw)
        if dtv.tzinfo is None:
            dtv = dtv.replace(tzinfo=timezone.utc)
        return dtv.astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dtv = datetime.strptime(raw, fmt)
            if dtv.tzinfo is not None:
                dtv = dtv.astimezone(timezone.utc).replace(tzinfo=None)
            return dtv
        except Exception:
            continue
    return None


def _first_text(parent: ET.Element, tags: Iterable[str]) -> str | None:
    for tag in tags:
        el = parent.find(tag)
        if el is not None and el.text:
            text = el.text.strip()
            if text:
                return text
    return None


def _strip_html(text: str | None) -> str:
    """Remove HTML tags and decode HTML entities, returning clean plain text."""
    if not text:
        return ""
    text = html_lib.unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _rss_or_atom_entries(xml_bytes: bytes) -> List[Dict[str, Any]]:
    root = ET.fromstring(xml_bytes)
    entries: List[Dict[str, Any]] = []
    atom_ns = {"atom": "http://www.w3.org/2005/Atom"}

    # RSS
    rss_items = root.findall("./channel/item")
    if rss_items:
        for item in rss_items:
            link = _first_text(item, ["link"])
            title = _first_text(item, ["title"])
            desc = _strip_html(_first_text(item, ["description"]))
            pub = _first_text(item, ["pubDate"])
            entries.append(
                {
                    "source_ref": link or title or "rss-item",
                    "title": title,
                    "content_text": desc or title or "",
                    "published_at": _parse_pub_dt(pub),
                }
            )
        return entries

    # Atom
    atom_entries = root.findall("./atom:entry", atom_ns)
    if atom_entries:
        for ent in atom_entries:
            title = _first_text(ent, ["{http://www.w3.org/2005/Atom}title"])
            summary = _strip_html(_first_text(ent, ["{http://www.w3.org/2005/Atom}summary"]))
            published = _first_text(
                ent,
                [
                    "{http://www.w3.org/2005/Atom}published",
                    "{http://www.w3.org/2005/Atom}updated",
                ],
            )
            link = None
            for link_el in ent.findall("{http://www.w3.org/2005/Atom}link"):
                href = (link_el.attrib or {}).get("href")
                if href:
                    link = href
                    break
            entries.append(
                {
                    "source_ref": link or title or "atom-entry",
                    "title": title,
                    "content_text": summary or title or "",
                    "published_at": _parse_pub_dt(published),
                }
            )
        return entries

    return entries


def _filter_window(entries: List[Dict[str, Any]], asof_date: date, start_date: date) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for e in entries:
        pub = e.get("published_at")
        if pub is None:
            # Keep undated items for visibility, but tag them as low-trust timing.
            meta = dict(e.get("metadata") or {})
            meta["timing_quality"] = "missing_published_at"
            e["metadata"] = meta
            out.append(e)
            continue
        pub_date = pub.date()
        if pub_date < start_date or pub_date > asof_date:
            continue
        out.append(e)
    return out


def main() -> int:
    args = _parse_args()
    asof_date = datetime.strptime(args.asof, "%Y-%m-%d").date()
    start_date = asof_date - timedelta(days=max(1, int(args.months_back)) * 31)
    symbols = _load_tickers(args.tickers_file, args.symbol, args.max_symbols)
    specs = _load_feed_specs(args, symbols)
    if not specs:
        print("[INFO] no feed specs to process (provide --symbol/--tickers-file or --feeds-file)")
        return 0

    ingest = RagIngestService()
    processed = 0
    network_failures = 0
    parse_failures = 0
    written_symbols = 0
    written_docs = 0

    for spec in specs:
        processed += 1
        sym = str(spec["symbol_key"]).upper()
        url = str(spec["url"])
        source_type = str(spec.get("source_type") or "external_news_rss")
        base_meta = dict(spec.get("metadata") or {})
        try:
            payload = _http_get(url, timeout=int(args.timeout))
        except Exception as e:
            network_failures += 1
            print(f"[WARN] fetch failed symbol={sym} url={url} error={type(e).__name__}: {e}")
            continue
        try:
            parsed = _rss_or_atom_entries(payload)
        except Exception as e:
            parse_failures += 1
            print(f"[WARN] parse failed symbol={sym} url={url} error={type(e).__name__}: {e}")
            continue

        filtered = _filter_window(parsed, asof_date=asof_date, start_date=start_date)
        docs: List[Dict[str, Any]] = []
        for e in filtered:
            meta = dict(base_meta)
            meta.update(
                {
                    "ingest_kind": "external_news_rss",
                    "backfill_start": start_date.isoformat(),
                    "backfill_end": asof_date.isoformat(),
                    "feed_url": url,
                }
            )
            docs.append(
                {
                    "source_type": source_type,
                    "source_ref": e.get("source_ref"),
                    "published_at": e.get("published_at"),
                    "title": e.get("title"),
                    "content_text": e.get("content_text"),
                    "metadata": meta,
                }
            )

        if args.dry_run:
            print(
                f"[DRYRUN] symbol={sym} fetched={len(parsed)} in_window={len(docs)} "
                f"window={start_date.isoformat()}..{asof_date.isoformat()}"
            )
            continue

        if not docs:
            print(
                f"[INFO] symbol={sym} fetched={len(parsed)} in_window=0 "
                f"window={start_date.isoformat()}..{asof_date.isoformat()}"
            )
            continue
        res = ingest.ingest_documents(sym, docs)
        if bool(res.get("ok")):
            written_symbols += 1
            written_docs += int(res.get("inserted", 0))
        print(
            f"symbol={sym} fetched={len(parsed)} in_window={len(docs)} ingest_ok={res.get('ok')} "
            f"inserted={res.get('inserted')} updated={res.get('updated')}"
        )

    print(
        "done "
        f"processed={processed} written_symbols={written_symbols} written_docs={written_docs} "
        f"network_failures={network_failures} parse_failures={parse_failures} "
        f"window={start_date.isoformat()}..{asof_date.isoformat()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
