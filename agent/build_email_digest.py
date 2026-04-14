"""
Build a readable email digest from AlbuchBot JSON output.

Outputs:
- subject text file
- body text file
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build email subject and digest text from news JSON")
    parser.add_argument("--input", default="output/news.json", help="Path to news JSON")
    parser.add_argument("--run-state", default="output/run_state.json", help="Path to run state JSON")
    parser.add_argument("--subject-output", default="output/email_subject.txt", help="Subject output path")
    parser.add_argument("--body-output", default="output/email_body.txt", help="Body output path")
    parser.add_argument("--max-items", type=int, default=7, help="Max number of items in digest")
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def category_label(category: str) -> str:
    mapping = {
        "gemeinderat": "Gemeinderat",
        "vereine": "Vereinsleben",
        "kirchliche": "Kirchliche Themen",
        "general": "Weitere Themen",
    }
    return mapping.get(category, category)


def build_items(news_payload: dict[str, Any]) -> list[tuple[str, str, str]]:
    news = news_payload.get("news", {})
    order = ["gemeinderat", "vereine", "kirchliche", "general"]
    items: list[tuple[str, str, str]] = []

    for category in order:
        entries = news.get(category, [])
        if not isinstance(entries, list):
            continue

        for entry in entries:
            title = str(entry.get("title", "")).strip()
            summary = str(entry.get("summary", "")).strip()
            if title or summary:
                items.append((category, title, summary))

    return items


def build_digest(news_payload: dict[str, Any], run_state: dict[str, Any], max_items: int) -> tuple[str, str]:
    source = news_payload.get("source", {})
    pdf_title = str(source.get("pdf_title") or source.get("pdf_link_name") or "Aktuelle Ausgabe").strip()
    pdf_url = str(source.get("pdf_url") or run_state.get("selected_pdf_url") or "").strip()
    created_at = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")

    subject = f"AlbuchBot Update: {pdf_title}"

    items = build_items(news_payload)[: max(1, max_items)]

    intro = (
        "Guten Tag,\n\n"
        "hier ist das aktuelle AlbuchBot-Update als kompakte Zusammenfassung. "
        f"Die Meldungen stammen aus: {pdf_title}."
    )

    lines = [intro, "", f"Stand: {created_at}", ""]
    lines.append("Meldungen:")
    lines.append("")

    if items:
        current_category = None
        for category, title, summary in items:
            # Add category header when it changes
            if category != current_category:
                if current_category is not None:
                    lines.append("")  # blank line between categories
                lines.append(category_label(category))
                lines.append("")
                current_category = category
            
            # Add title and summary
            if title:
                lines.append(title)
                if summary:
                    lines.append(summary)
                lines.append("")
    else:
        lines.append("Es konnten fuer diese Ausgabe keine verwertbaren Meldungen extrahiert werden.")

    if pdf_url:
        lines.extend(["", f"Quelle: {pdf_url}"])

    lines.extend(["", "Viele Gruesse", "AlbuchBot"])

    body = "\n".join(lines).strip()
    return subject, body


def main() -> None:
    args = parse_args()
    news_payload = read_json(Path(args.input))
    run_state = read_json(Path(args.run_state))

    subject, body = build_digest(news_payload, run_state, args.max_items)

    subject_path = Path(args.subject_output)
    body_path = Path(args.body_output)

    subject_path.parent.mkdir(parents=True, exist_ok=True)
    body_path.parent.mkdir(parents=True, exist_ok=True)

    subject_path.write_text(subject + "\n", encoding="utf-8")
    body_path.write_text(body + "\n", encoding="utf-8")

    print(f"Wrote subject to {subject_path}")
    print(f"Wrote body to {body_path}")


if __name__ == "__main__":
    main()
