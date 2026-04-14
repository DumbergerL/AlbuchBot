"""
Step 2: Processor - reads extracted news JSON and pushes to Google Sheets.
Input: news JSON file produced by scraper.py
"""
import argparse
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

# Load env from repo root and from agent/.env for local development.
load_dotenv()
load_dotenv(dotenv_path=Path(__file__).with_name(".env"))

logger = logging.getLogger("albuchbot.processor")


def read_news_json(json_path: Path) -> dict[str, Any]:
    logger.info("Reading news JSON from: %s", json_path)
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    logger.info("News JSON loaded successfully")
    return data


def flatten_news_rows(news_data: dict[str, Any]) -> list[list[str]]:
    """Flatten the nested news structure into sheet rows."""
    generated_at = datetime.now(timezone.utc).isoformat()
    rows: list[list[str]] = []

    source_info = news_data.get("source", {})
    pdf_url = source_info.get("pdf_url", "")
    pdf_link_name = source_info.get("pdf_link_name", "")
    listing_url = source_info.get("listing_url", "")

    news_sections = news_data.get("news", {})
    for category in ["gemeinderat", "vereine", "kirchliche", "general"]:
        for item in news_sections.get(category, []):
            rows.append(
                [
                    generated_at,
                    category,
                    item.get("title", ""),
                    item.get("summary", ""),
                    item.get("source_excerpt", ""),
                    pdf_link_name,
                    listing_url,
                    pdf_url,
                ]
            )

    logger.info("Flattened %d news items into rows", len(rows))
    return rows


def push_to_google_sheet(
    json_path: Path,
    spreadsheet_id: str,
    worksheet_name: str,
    service_account_json: str,
) -> None:
    logger.info(
        "Pushing news to Google Sheets (spreadsheet=%s, worksheet=%s)",
        spreadsheet_id,
        worksheet_name,
    )

    news_data = read_news_json(json_path)
    rows = flatten_news_rows(news_data)

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    credentials = Credentials.from_service_account_file(service_account_json, scopes=scopes)
    client = gspread.authorize(credentials)
    spreadsheet = client.open_by_key(spreadsheet_id)

    try:
        worksheet = spreadsheet.worksheet(worksheet_name)
    except gspread.WorksheetNotFound:
        logger.info("Worksheet '%s' not found, creating it", worksheet_name)
        worksheet = spreadsheet.add_worksheet(title=worksheet_name, rows=200, cols=10)

    header = [
        "generated_at_utc",
        "category",
        "title",
        "summary",
        "source_excerpt",
        "pdf_link_name",
        "listing_url",
        "pdf_url",
    ]

    existing_header = worksheet.row_values(1)
    if existing_header != header:
        logger.info("Worksheet header missing or different, appending header row")
        worksheet.append_row(header, value_input_option="RAW")

    worksheet.append_rows(rows, value_input_option="RAW")
    logger.info("Google Sheet updated successfully, appended %d rows", len(rows))


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
    log_file = logs_dir / f"processor_{timestamp}.log"
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)  # Always log DEBUG to file, regardless of console level
    file_formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    file_handler.setFormatter(file_formatter)
    logger_instance.addHandler(file_handler)

    logger_instance.info(f"Log file: {log_file.resolve()}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Step 2: Process extracted news JSON and push to Google Sheets."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("output/news.json"),
        help="Path to news JSON file produced by scraper (default: output/news.json)",
    )
    parser.add_argument(
        "--spreadsheet-id",
        default=os.getenv("GOOGLE_SPREADSHEET_ID", ""),
        help="Google Spreadsheet ID (default: GOOGLE_SPREADSHEET_ID env var)",
    )
    parser.add_argument(
        "--worksheet-name",
        default=os.getenv("GOOGLE_WORKSHEET_NAME", "AlbuchBot News"),
        help="Google worksheet name (default: GOOGLE_WORKSHEET_NAME or 'AlbuchBot News')",
    )
    parser.add_argument(
        "--service-account-json",
        default=os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", ""),
        help="Path to service-account JSON file (default: GOOGLE_SERVICE_ACCOUNT_JSON env var)",
    )
    parser.add_argument(
        "--log-level",
        default=os.getenv("LOG_LEVEL", "INFO"),
        help="Logging level (DEBUG, INFO, WARNING, ERROR); default INFO",
    )
    args = parser.parse_args()

    configure_logging(args.log_level)
    logger.info("AlbuchBot processor started (Step 2)")

    if not args.input.exists():
        raise RuntimeError(f"News JSON file not found: {args.input.resolve()}")

    if not args.spreadsheet_id.strip():
        raise RuntimeError(
            "Missing Google Spreadsheet ID. Set GOOGLE_SPREADSHEET_ID in env/.env or pass --spreadsheet-id."
        )

    if not args.service_account_json.strip():
        raise RuntimeError(
            "Missing service account JSON path. Set GOOGLE_SERVICE_ACCOUNT_JSON in env/.env or pass --service-account-json."
        )

    service_account_path = Path(args.service_account_json)
    if not service_account_path.exists():
        raise RuntimeError(
            f"Service account JSON file not found: {service_account_path.resolve()}"
        )

    push_to_google_sheet(
        json_path=args.input,
        spreadsheet_id=args.spreadsheet_id,
        worksheet_name=args.worksheet_name,
        service_account_json=str(service_account_path),
    )

    logger.info("AlbuchBot processor finished successfully")


if __name__ == "__main__":
    main()
