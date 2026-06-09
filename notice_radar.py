from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

DEFAULT_CONFIG_PATH = Path("config.json")
DEFAULT_STORAGE_DIR = Path("data")
DEFAULT_RESULT_PATH = Path("result.txt")
DEFAULT_SOURCE_URL = "https://home.knu.ac.kr/HOME/seeai/"
DEFAULT_TIMEOUT = 20
DEFAULT_LIMIT = 30
HTML_TAGS = {"li", "tr", "div", "article", "section"}
DATE_PATTERN = re.compile(r"(20\d{2}[./-]\d{1,2}[./-]\d{1,2})")
NOTICE_HREF_PATTERNS = ("mode=view", "viewBtin.action", "pg=vv")


def load_json(path: Path) -> dict[str, Any] | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_text(text: str) -> str:
    return " ".join(text.split())


def extract_date(anchor) -> str:
    for parent in anchor.parents:
        if parent.name in HTML_TAGS:
            match = DATE_PATTERN.search(normalize_text(parent.get_text(" ", strip=True)))
            if match:
                return match.group(1).replace(".", "-").replace("/", "-")
    return ""


def parse_notices(html: str, base_url: str, source_name: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    notices: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for anchor in soup.select("a[href]"):
        href = anchor.get("href")
        if not href or not any(pattern in href for pattern in NOTICE_HREF_PATTERNS):
            continue

        url = urljoin(base_url, href)
        if url in seen_urls:
            continue

        title = normalize_text(anchor.get_text(" ", strip=True))
        if not title:
            continue

        seen_urls.add(url)
        context = ""
        for parent in anchor.parents:
            if parent.name in HTML_TAGS:
                context = normalize_text(parent.get_text(" ", strip=True))[:500]
                break

        notices.append(
            {
                "id": hashlib.sha1(url.encode("utf-8")).hexdigest()[:16],
                "title": title,
                "url": url,
                "date": extract_date(anchor),
                "source": source_name,
                "context": context,
            }
        )

    return notices


def parse_notice_date(date_text: str) -> datetime | None:
    if not date_text:
        return None
    normalized = date_text.replace(".", "-").replace("/", "-")
    parts = normalized.split("-")
    if len(parts) != 3:
        return None
    try:
        year, month, day = (int(part) for part in parts)
        return datetime(year, month, day)
    except ValueError:
        return None


def sort_notices_latest(notices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(notices, key=lambda item: parse_notice_date(str(item.get("date", ""))) or datetime.min, reverse=True)


def filter_notices(notices: list[dict[str, Any]], keywords: list[str], source_filters: list[str], after: str | None, before: str | None, new_only: bool) -> list[dict[str, Any]]:
    keyword_list = [keyword.strip() for keyword in keywords if keyword.strip()]
    source_terms = [source.strip().casefold() for source in source_filters if source.strip()]
    after_date = parse_notice_date(after or "")
    before_date = parse_notice_date(before or "")
    filtered: list[dict[str, Any]] = []

    for notice in notices:
        text = normalize_text(f"{notice.get('title', '')} {notice.get('context', '')}").casefold()
        matched = [keyword for keyword in keyword_list if keyword.casefold() in text]
        if keyword_list and not matched:
            continue
        if source_terms:
            source_text = f"{notice.get('source', '')} {notice.get('url', '')}".casefold()
            if not any(term in source_text for term in source_terms):
                continue
        notice_date = parse_notice_date(str(notice.get("date", "")))
        if after_date and (notice_date is None or notice_date < after_date):
            continue
        if before_date and (notice_date is None or notice_date > before_date):
            continue
        if new_only and not notice.get("is_new"):
            continue

        item = dict(notice)
        item["matched_keywords"] = matched
        filtered.append(item)

    return filtered


def write_result_text(path: Path, notices: list[dict[str, Any]], warnings: list[str]) -> None:
    lines: list[str] = []
    if warnings:
        lines.append("수집 경고:")
        lines.extend(f"- {warning}" for warning in warnings)
        lines.append("")
    if not notices:
        lines.append("매칭된 공지사항이 없습니다.")
    else:
        for idx, notice in enumerate(notices, 1):
            lines.append(f"{idx}. {notice['title']}{' [NEW]' if notice.get('is_new') else ''}")
            lines.append(f"   - 날짜: {notice.get('date') or '날짜 없음'}")
            lines.append(f"   - 출처: {notice.get('source', '-')}")
            lines.append(f"   - 키워드: {', '.join(notice.get('matched_keywords', []))}")
            lines.append(f"   - 링크: {notice['url']}")
            if notice.get("context"):
                lines.append(f"   - 요약: {notice['context']}")
            lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def fetch_html(source_url: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
        "Referer": DEFAULT_SOURCE_URL,
    }
    last_error: Exception | None = None
    for candidate in (source_url, source_url.rstrip("/") + "/index.htm" if not source_url.endswith((".htm", ".html")) else source_url):
        try:
            response = requests.get(candidate, timeout=timeout, headers=headers)
            response.raise_for_status()
            response.encoding = response.apparent_encoding
            html = response.text
            soup = BeautifulSoup(html, "html.parser")
            frame = soup.find("frame", attrs={"name": "bottom"}) or soup.find("frame")
            if frame and frame.get("src"):
                frame_response = requests.get(urljoin(candidate, frame["src"]), timeout=timeout, headers=headers)
                frame_response.raise_for_status()
                frame_response.encoding = frame_response.apparent_encoding
                return frame_response.text
            return html
        except requests.RequestException as error:
            last_error = error
    if last_error is not None:
        raise last_error
    raise RuntimeError("HTML을 가져오지 못했습니다.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="KNU Notice Radar")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--source-url")
    parser.add_argument("--keyword", action="append", default=None)
    parser.add_argument("--source", action="append", default=None)
    parser.add_argument("--after")
    parser.add_argument("--before")
    parser.add_argument("--new-only", action="store_true")
    parser.add_argument("--limit", type=int)
    return parser.parse_args()


def load_config(args: argparse.Namespace) -> dict[str, Any]:
    config = load_json(Path(args.config)) or {}
    sources = args.source_url and [{"name": "사용자 지정 소스", "url": args.source_url}] or config.get("sources") or [
        {"name": "기본 소스", "url": config.get("source_url") or DEFAULT_SOURCE_URL}
    ]
    return {
        "keywords": args.keyword if args.keyword is not None else list(config.get("keywords", [])),
        "source_filters": args.source or [],
        "after": args.after,
        "before": args.before,
        "new_only": args.new_only,
        "limit": args.limit if args.limit is not None else int(config.get("limit", DEFAULT_LIMIT)),
        "storage_dir": Path(config.get("storage_dir", DEFAULT_STORAGE_DIR)),
        "result_path": Path(config.get("result_path", DEFAULT_RESULT_PATH)),
        "timeout": int(config.get("timeout", DEFAULT_TIMEOUT)),
        "sources": sources,
    }


def collect_and_save(config: dict[str, Any]) -> dict[str, Any]:
    storage_dir = config["storage_dir"]
    latest_path = storage_dir / "latest.json"
    previous_path = storage_dir / "previous.json"
    result_path = config["result_path"]

    sources = config["sources"]
    notices: list[dict[str, Any]] = []
    warnings: list[str] = []

    for source in sources:
        try:
            html = fetch_html(source["url"], timeout=config["timeout"])
            notices.extend(parse_notices(html, source["url"], source["name"]))
        except requests.RequestException as error:
            warnings.append(f"{source['name']}: {error}")

    previous_data = load_json(latest_path) or {}
    previous_ids = {item["id"] for item in previous_data.get("all_notices", previous_data.get("notices", []))}

    all_notices = sort_notices_latest({notice["url"]: notice for notice in notices}.values())[: max(config["limit"], 0)]
    for notice in all_notices:
        notice["is_new"] = notice["id"] not in previous_ids

    filtered = sort_notices_latest(
        filter_notices(
            all_notices,
            config["keywords"],
            config["source_filters"],
            config["after"],
            config["before"],
            config["new_only"],
        )
    )

    if previous_data:
        save_json(previous_path, previous_data)

    updated_at = datetime.now().isoformat(timespec="seconds")
    summary = {
        "updated_at": updated_at,
        "sources": sources,
        "keywords": config["keywords"],
        "applied_filters": {
            "source_filters": config["source_filters"],
            "after_date": config["after"],
            "before_date": config["before"],
            "new_only": config["new_only"],
        },
        "total_notices": len(all_notices),
        "matched_notices": len(filtered),
        "all_notices": all_notices,
        "notices": filtered,
        "fetch_errors": warnings,
        "latest_path": str(latest_path),
        "previous_path": str(previous_path),
        "result_path": str(result_path),
    }

    save_json(
        latest_path,
        {
            "updated_at": updated_at,
            "sources": sources,
            "keywords": config["keywords"],
            "applied_filters": summary["applied_filters"],
            "total_notices": len(all_notices),
            "matched_notices": len(filtered),
            "fetch_errors": warnings,
            "all_notices": all_notices,
            "notices": filtered,
        },
    )
    write_result_text(result_path, filtered, warnings)
    return summary


def main() -> None:
    args = parse_args()
    config = load_config(args)
    data = collect_and_save(config)
    print(f"수집: {data['total_notices']}건 / 키워드 매칭: {data['matched_notices']}건")
    print(f"- JSON: {data['latest_path']}")
    print(f"- TEXT: {data['result_path']}")
    if data["fetch_errors"]:
        print(f"- WARN: {len(data['fetch_errors'])}개 소스 수집 실패")


if __name__ == "__main__":
    main()
