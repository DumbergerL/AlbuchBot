"""
Step 1: Web scraper - fetches PDF, extracts text, sends to LLM, returns JSON.
Outputs: output/news.json
"""
import argparse
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from google import genai
from pypdf import PdfReader
from dotenv import load_dotenv

SOURCE_URL = "https://www.steinheim.com/rathaus-service/amtsblatt/1548/albuch-bote-2026"

# Load env from repo root and from agent/.env for local development.
load_dotenv()
load_dotenv(dotenv_path=Path(__file__).with_name(".env"))

logger = logging.getLogger("albuchbot.scraper")


def configure_tls() -> None:
    custom_ca = os.getenv("SSL_CERT_FILE", "").strip()
    if custom_ca:
        logger.info("Using custom SSL_CERT_FILE: %s", custom_ca)

    try:
        import truststore

        truststore.inject_into_ssl()
        logger.info("TLS configured with OS trust store via truststore")
    except Exception as exc:
        logger.warning(
            "truststore could not be activated (%s). Falling back to default SSL handling.",
            exc,
        )


@dataclass
class NewsItem:
    title: str
    summary: str
    source_excerpt: str


@dataclass
class StructuredNews:
    gemeinderat: list[NewsItem]
    vereine: list[NewsItem]
    kirchliche: list[NewsItem]
    general: list[NewsItem]


def fetch_page_html(url: str) -> str:
    logger.info("Fetching source page: %s", url)
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    logger.info("Source page fetched successfully (status=%s)", response.status_code)
    return response.text


def _extract_year_kw_date(link_text: str) -> tuple[int | None, int | None, int | None]:
    """Extract year, KW and date (YYYYMMDD) from anchor text."""
    text = re.sub(r"\s+", " ", link_text).strip()
    lowered = text.lower()

    year: int | None = None
    kw: int | None = None
    date_value: int | None = None

    year_match = re.search(r"\b(20\d{2})\b", lowered)
    if year_match:
        year = int(year_match.group(1))

    kw_match = re.search(r"\bkw\.?\s*(\d{1,2})\b", lowered, flags=re.IGNORECASE)
    if kw_match:
        kw = int(kw_match.group(1))

    # Date in text, often in brackets, e.g. (16.01.2026), 16-01-2026, 16/01/26
    date_match = re.search(
        r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{2}|\d{4})\b",
        lowered,
    )
    if date_match:
        day = int(date_match.group(1))
        month = int(date_match.group(2))
        year_raw = int(date_match.group(3))
        full_year = year_raw if year_raw >= 100 else 2000 + year_raw
        try:
            parsed = datetime(full_year, month, day)
            date_value = parsed.year * 10000 + parsed.month * 100 + parsed.day
            if year is None:
                year = parsed.year
        except ValueError:
            # Ignore invalid dates in anchor text.
            pass

    return year, kw, date_value


def _normalize_document_title(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip().lower()


def find_current_pdf_candidate(html: str, base_url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    current_year = datetime.now().year
    candidates: list[dict[str, Any]] = []

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if ".pdf" not in href.lower():
            continue

        link_text = anchor.get_text(" ", strip=True)
        year, kw, date_value = _extract_year_kw_date(link_text)
        candidates.append(
            {
                "url": urljoin(base_url, href),
                "href": href,
                "name": link_text,
                "year": year,
                "kw": kw,
                "date": date_value,
            }
        )

    if not candidates:
        raise RuntimeError("No PDF link found on the source page.")

    logger.info("Detected %d PDF link candidates on source page", len(candidates))
    for idx, candidate in enumerate(candidates, 1):
        logger.info(
            "Candidate [%d] name='%s' | href='%s' | year=%s | kw=%s | date=%s",
            idx,
            candidate["name"] or "(no text)",
            candidate["href"],
            candidate["year"],
            candidate["kw"],
            candidate["date"],
        )

    # Keep only current-year candidates based on anchor text.
    current_year_candidates = [c for c in candidates if c["year"] == current_year]
    logger.info(
        "Current-year candidates (%d): %d",
        current_year,
        len(current_year_candidates),
    )

    if not current_year_candidates:
        raise RuntimeError(
            f"No PDF candidates from current year {current_year} found in anchor text."
        )

    # Prefer entries with explicit date, otherwise KW. Use list position as tie-breaker.
    def candidate_sort_key(item: dict[str, Any]) -> tuple[int, int, int]:
        has_date = 1 if item["date"] is not None else 0
        if has_date:
            return (2, int(item["date"]), -candidates.index(item))
        has_kw = 1 if item["kw"] is not None else 0
        if has_kw:
            return (1, int(item["kw"]), -candidates.index(item))
        return (0, -1, -candidates.index(item))

    sorted_candidates = sorted(
        current_year_candidates,
        key=candidate_sort_key,
        reverse=True,
    )

    logger.info("Selection ranking (top 10):")
    for idx, candidate in enumerate(sorted_candidates[:10], 1):
        key = candidate_sort_key(candidate)
        logger.info(
            "Rank %d -> name='%s' | href='%s' | year=%s | kw=%s | date=%s | key=%s",
            idx,
            candidate["name"] or "(no text)",
            candidate["href"],
            candidate["year"],
            candidate["kw"],
            candidate["date"],
            key,
        )

    selected = sorted_candidates[0]
    logger.info("✓ Selected PDF candidate")
    logger.info("  name: %s", selected["name"] or "(no text)")
    logger.info("  href: %s", selected["href"])
    logger.info("  url: %s", selected["url"])
    logger.info(
        "  reason: year=%s, date=%s, kw=%s",
        selected["year"],
        selected["date"],
        selected["kw"],
    )
    return selected


def write_run_state(
    run_state_path: Path,
    should_process: bool,
    selected_title: str,
    selected_url: str,
    reason: str,
) -> None:
    run_state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "should_process": should_process,
        "selected_document_title": selected_title,
        "selected_pdf_url": selected_url,
        "reason": reason,
        "generated_at_utc": datetime.utcnow().isoformat() + "Z",
    }
    run_state_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Saved run state to: %s", run_state_path.resolve())


def download_pdf(url: str, target_path: Path) -> None:
    logger.info("Downloading PDF to temporary path: %s", target_path)
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    target_path.write_bytes(response.content)
    logger.info("PDF downloaded (%d bytes)", len(response.content))


def extract_pdf_text(pdf_path: Path) -> str:
    logger.info("Extracting text from PDF: %s", pdf_path)
    reader = PdfReader(str(pdf_path))
    logger.info("PDF contains %d pages", len(reader.pages))
    pages: list[str] = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    text = "\n\n".join(pages)

    # Normalize excessive whitespace but keep paragraph separation.
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    cleaned = text.strip()
    logger.info("Extracted %d characters from PDF", len(cleaned))
    return cleaned


def call_gemini_extract_news(raw_text: str, model_name: str = "gemini-2.5-flash-lite") -> StructuredNews:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing GEMINI_API_KEY environment variable.")

    client = genai.Client(api_key=api_key)
    logger.info("Calling Gemini model '%s' with %d characters of source text", model_name, len(raw_text[:80000]))

    prompt = f"""
You are a local-news extraction assistant.

Task:
- Read the newsletter text below.
- Extract exactly 16 relevant news entries from the document.
- Return 4 items for each category: 'gemeinderat', 'vereine', 'kirchliche', and 'general'.
- If there are not enough perfect matches for a category, use the closest relevant topics.
- Target audience is a WhatsApp channel. Use a fitting emoji at the beginning of each title.

Output format:
Return valid JSON only (no markdown), with this exact structure:
{{
  "gemeinderat": [{{"title": "...", "summary": "...", "source_excerpt": "..."}}, ...],
  "vereine": [{{"title": "...", "summary": "...", "source_excerpt": "..."}}, ...],
    "kirchliche": [{{"title": "...", "summary": "...", "source_excerpt": "..."}}, ...],
  "general": [{{"title": "...", "summary": "...", "source_excerpt": "..."}}, ...]
}}

Rules:
- title: max 100 chars, starts with exactly one fitting emoji
- summary: concise German summary in 1-2 sentences
- source_excerpt: short quote/excerpt from source, max 240 chars
- Use only information present in the source text
- Keep language German

Source text:
{raw_text[:80000]}
""".strip()

    response = client.models.generate_content(model=model_name, contents=prompt)
    response_text = (response.text or "").strip()
    if not response_text:
        raise RuntimeError("Gemini returned an empty response.")

    logger.info("Received response from Gemini (%d characters)", len(response_text))

    payload = parse_json_from_response(response_text)
    return parse_structured_news(payload)


def parse_json_from_response(text: str) -> dict[str, Any]:
    candidate = text.strip()

    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?", "", candidate).strip()
        candidate = re.sub(r"```$", "", candidate).strip()

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", candidate)
        if not match:
            raise RuntimeError("Could not parse JSON from Gemini response.")
        return json.loads(match.group(0))


def _parse_items(raw_items: Any, category: str, expected_count: int) -> list[NewsItem]:
    if not isinstance(raw_items, list):
        raise RuntimeError(f"Field '{category}' is not a list.")

    items: list[NewsItem] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title", "")).strip()
        summary = str(raw.get("summary", "")).strip()
        excerpt = str(raw.get("source_excerpt", "")).strip()
        if title and summary and excerpt:
            items.append(NewsItem(title=title, summary=summary, source_excerpt=excerpt))

    if len(items) != expected_count:
        raise RuntimeError(
            f"Expected {expected_count} items in '{category}', received {len(items)}."
        )

    return items


def parse_structured_news(payload: dict[str, Any]) -> StructuredNews:
    structured = StructuredNews(
        gemeinderat=_parse_items(payload.get("gemeinderat"), "gemeinderat", 4),
        vereine=_parse_items(payload.get("vereine"), "vereine", 4),
        kirchliche=_parse_items(payload.get("kirchliche"), "kirchliche", 4),
        general=_parse_items(payload.get("general"), "general", 4),
    )
    logger.info(
        "Parsed structured news counts: gemeinderat=%d, vereine=%d, kirchliche=%d, general=%d",
        len(structured.gemeinderat),
        len(structured.vereine),
        len(structured.kirchliche),
        len(structured.general),
    )
    return structured


def extract_pdf_display_name(pdf_url: str) -> str:
    """Extract a human-readable name from PDF URL for display in HTML.
    Tries to extract KW/date from filename and format it nicely.
    Falls back to original filename if extraction fails.
    """
    filename = pdf_url.split("/")[-1]

    # Try to extract KW pattern (KW01, etc.)
    kw_match = re.search(r"kw(\d{1,2})", filename, re.IGNORECASE)
    if kw_match:
        week = int(kw_match.group(1))
        return f"AlbuchBote KW {week:02d}"

    # Try to extract date pattern DDMMYYYY
    date_match = re.search(r"(\d{2})(\d{2})(\d{4})", filename)
    if date_match:
        day, month, year = date_match.group(1), date_match.group(2), date_match.group(3)
        return f"AlbuchBote {day}.{month}.{year}"

    # Fallback: return filename without extension
    return filename.replace(".pdf", "").replace("_", " ")


def save_output(
    structured_news: StructuredNews,
    output_path: Path,
    pdf_url: str,
    pdf_title: str,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_display_name = extract_pdf_display_name(pdf_url)
    output = {
        "source": {
            "listing_url": SOURCE_URL,
            "pdf_url": pdf_url,
            "pdf_title": pdf_title,
            "pdf_link_name": pdf_display_name,
        },
        "news": {
            "gemeinderat": [item.__dict__ for item in structured_news.gemeinderat],
            "vereine": [item.__dict__ for item in structured_news.vereine],
            "kirchliche": [item.__dict__ for item in structured_news.kirchliche],
            "general": [item.__dict__ for item in structured_news.general],
        },
    }
    output_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Saved JSON output to: %s", output_path.resolve())


def configure_logging(level_name: str) -> None:
    resolved_level = getattr(logging, level_name.upper(), None)
    if not isinstance(resolved_level, int):
        raise ValueError(f"Unsupported log level: {level_name}")

    # Create logs directory
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)

    # Create logger
    logger_instance = logging.getLogger("albuchbot")
    logger_instance.setLevel(resolved_level)

    # Remove any existing handlers to avoid duplicates
    logger_instance.handlers = []

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(resolved_level)
    console_formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    console_handler.setFormatter(console_formatter)
    logger_instance.addHandler(console_handler)

    # File handler for debug logs
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    log_file = logs_dir / f"scraper_{timestamp}.log"
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)  # Always log DEBUG to file, regardless of console level
    file_formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    file_handler.setFormatter(file_formatter)
    logger_instance.addHandler(file_handler)

    logger_instance.info(f"Log file: {log_file.resolve()}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Step 1: Fetch Albuch Bote PDF, extract text, and build categorized news with Gemini."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output/news.json"),
        help="Path for JSON output file (default: output/news.json)",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite"),
        help="Gemini model name (default: GEMINI_MODEL or gemini-2.5-flash-lite)",
    )
    parser.add_argument(
        "--last-processed-document-title",
        default=os.getenv("LAST_PROCESSED_DOCUMENT_TITLE", ""),
        help=(
            "Title of the last processed PDF document. "
            "If it matches the currently selected document title, the run stops early."
        ),
    )
    parser.add_argument(
        "--run-state-output",
        type=Path,
        default=Path("output/run_state.json"),
        help="Path for run-state JSON file used by pipeline control (default: output/run_state.json)",
    )
    parser.add_argument(
        "--force-process",
        action="store_true",
        default=os.getenv("FORCE_PROCESS", "").strip().lower() in {"1", "true", "yes", "on"},
        help=(
            "Force analysis even if the selected document matches the last processed document title. "
            "Can also be enabled with FORCE_PROCESS=true."
        ),
    )
    parser.add_argument(
        "--log-level",
        default=os.getenv("LOG_LEVEL", "INFO"),
        help="Logging level (DEBUG, INFO, WARNING, ERROR); default INFO",
    )
    args = parser.parse_args()

    configure_logging(args.log_level)
    logger.info("AlbuchBot scraper started (Step 1)")
    configure_tls()

    html = fetch_page_html(SOURCE_URL)
    selected_candidate = find_current_pdf_candidate(html, SOURCE_URL)
    pdf_url = str(selected_candidate["url"])
    selected_title = str(selected_candidate["name"] or "").strip()

    last_processed_title = args.last_processed_document_title.strip()
    if args.force_process:
        logger.warning(
            "Force processing enabled. Duplicate-document protection is bypassed for title='%s'",
            selected_title,
        )
    elif last_processed_title:
        logger.info("Last processed title from pipeline: %s", last_processed_title)
        if _normalize_document_title(selected_title) == _normalize_document_title(last_processed_title):
            logger.warning(
                "Selected document already processed. Stopping run early. selected='%s'",
                selected_title,
            )
            write_run_state(
                run_state_path=args.run_state_output,
                should_process=False,
                selected_title=selected_title,
                selected_url=pdf_url,
                reason="already_processed",
            )
            logger.info("AlbuchBot scraper finished without processing (already processed document)")
            return

    with tempfile.TemporaryDirectory(prefix="albuchbot-") as temp_dir:
        pdf_path = Path(temp_dir) / "current_albuch_bote.pdf"
        download_pdf(pdf_url, pdf_path)
        text = extract_pdf_text(pdf_path)

    if not text:
        raise RuntimeError("PDF text extraction returned empty content.")

    structured_news = call_gemini_extract_news(text, model_name=args.model)
    save_output(structured_news, args.output, pdf_url, selected_title)
    write_run_state(
        run_state_path=args.run_state_output,
        should_process=True,
        selected_title=selected_title,
        selected_url=pdf_url,
        reason="processed",
    )

    logger.info("Source PDF: %s", pdf_url)
    logger.info("Source PDF title: %s", selected_title)
    logger.info("Saved extracted news JSON to: %s", args.output.resolve())
    logger.info("AlbuchBot scraper finished successfully")


if __name__ == "__main__":
    main()
