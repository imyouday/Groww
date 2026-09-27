"""Phase 0 corpus spike: fetch the candidate scheme pages and report fact-family coverage.

Throwaway diagnostic (implementation.md Phase 0). It answers one question: are the 7 fact
families actually present in the retrievable text of the public pages we plan to cite? A
"no" here means the chunking decision in PRD §9.3 has to be revisited before any pipeline
code is written (risk R1).

Usage:
    python scripts/spike_fetch.py
    python scripts/spike_fetch.py --url S6 https://example.org/page --scheme-id S6
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "data" / "raw" / "spike"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

DEFAULT_TARGETS: list[tuple[str, str]] = [
    ("S1", "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth"),
    ("S2", "https://groww.in/mutual-funds/hdfc-equity-fund-direct-growth"),
    ("S3", "https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-growth"),
    ("S4", "https://groww.in/mutual-funds/hdfc-small-cap-fund-direct-growth"),
    ("S5", "https://groww.in/mutual-funds/hdfc-balanced-advantage-fund-direct-growth"),
]

STRIP_SELECTORS = (
    "script",
    "style",
    "noscript",
    "iframe",
    "svg",
    "header",
    "footer",
    "nav",
    "[role='navigation']",
    "[class*='cookie' i]",
    "[id*='consent' i]",
)

FACT_FAMILIES: dict[str, list[str]] = {
    "expense_ratio": [r"expense\s+ratio", r"\bter\b", r"expense\s+ratio\s+and\s+other\s+fees"],
    "exit_load": [r"exit\s+load", r"exit\s+charges"],
    "min_sip": [r"minimum\s+sip", r"min\s+sip", r"minimum\s+(?:investment|amount|investment\s+amount)"],
    "min_lumpsum": [r"minimum\s+(?:lump\s*sum|purchase)", r"lump\s*sum"],
    "lock_in": [r"lock[-\s]?in", r"\b80c\b", r"tax\s+saver", r"elss"],
    "riskometer": [r"riskometer", r"risk\s+ometer"],
    "benchmark": [r"benchmark", r"\bindex\b"],
    "statements": [r"capital\s+gains?", r"statement", r"tax\s+report", r"download"],
}

PII_HINTS = re.compile(
    r"(pan|aadhaar|aadhar|folio\s*(?:no|number)|account\s*(?:no|number)|otp|"
    r"\b\d{10}\b|\+91)",
    re.IGNORECASE,
)


def extract_text(html: str) -> str:
    """Strip non-content nodes and collapse whitespace, as architecture.md §7.3 requires."""
    soup = BeautifulSoup(html, "lxml")
    for node in soup.select(",".join(STRIP_SELECTORS)):
        node.decompose()
    text = soup.get_text("\n")
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def fetch(url: str, dest: Path, timeout: float, retries: int) -> tuple[str, int]:
    """Fetch url and save the raw body; returns (body, status_code)."""
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            response = httpx.get(
                url,
                headers={"User-Agent": USER_AGENT, "Accept-Language": "en-IN,en;q=0.9"},
                timeout=timeout,
                follow_redirects=True,
            )
            content_type = response.headers.get("content-type", "")
            extension = ".md" if "markdown" in content_type else ".html"
            dest.write_bytes(response.content)
            return response.text, response.status_code if dest.suffix == extension else response.status_code
        except httpx.HTTPError as exc:
            last_error = exc
            time.sleep(2**attempt)
    raise RuntimeError(f"fetch failed after {retries} attempts: {last_error}")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Phase 0 corpus spike")
    parser.add_argument("--url", action="append", default=[], metavar="SCHEME_ID URL",
                        help="extra target as 'scheme_id url'; repeatable")
    parser.add_argument("--timeout", type=float, default=25.0)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--delay", type=float, default=1.0)
    args = parser.parse_args()

    targets = list(DEFAULT_TARGETS)
    for pair in args.url:
        scheme_id, url = pair.split(maxsplit=1)
        targets.append((scheme_id, url))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[tuple[str, int, int, dict[str, tuple[bool, str]]]] = []

    for index, (scheme_id, url) in enumerate(targets):
        if index:
            time.sleep(args.delay)
        try:
            body, status = fetch(url, OUT_DIR / f"{scheme_id}.html", args.timeout, args.retries)
        except Exception as exc:  # noqa: BLE001 - a diagnostic must report, not die
            print(f"\n=== {scheme_id}  UNREACHABLE  {url}\n    {type(exc).__name__}: {exc}")
            continue
        text = extract_text(body)
        (OUT_DIR / f"{scheme_id}.txt").write_text(text, encoding="utf-8")

        results: dict[str, tuple[bool, str]] = {}
        for family, patterns in FACT_FAMILIES.items():
            hit = ""
            for pattern in patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    start = max(0, match.start() - 60)
                    hit = " ".join(text[start : match.end() + 100].split())
                    break
            results[family] = (bool(hit), hit)

        pii_hits = len(set(m.group(0).lower() for m in PII_HINTS.finditer(text)))
        rows.append((scheme_id, status, len(text), results))
        print(f"\n=== {scheme_id}  status={status}  chars={len(text)}  pii_hint_types={pii_hits}")
        for family, (found, snippet) in results.items():
            mark = "FOUND    " if found else "NOT_FOUND"
            print(f"  {family:<13} {mark}  {snippet[:110]}")

    print("\n\nSUMMARY (scheme x fact_family)")
    header = "scheme  " + "  ".join(f"{f:<12}" for f in FACT_FAMILIES)
    print(header)
    print("-" * len(header))
    for scheme_id, _, _, results in rows:
        cells = "  ".join(f"{('FOUND' if results[f][0] else '-'):<12}" for f in FACT_FAMILIES)
        print(f"{scheme_id:<7} {cells}")

    missing_everywhere = [f for f in FACT_FAMILIES if not any(r[3][f][0] for r in rows)]
    thin = [
        f
        for f in FACT_FAMILIES
        if 0 < sum(1 for r in rows if r[3][f][0]) < len(rows)
    ]
    print(f"\nabsent from every scheme : {missing_everywhere or 'none'}")
    print(f"present on some, not all  : {thin or 'none'}")
    print(f"snapshots written to      : {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
