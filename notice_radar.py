from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

DEFAULT_CONFIG_PATH = Path("config.json")
DEFAULT_STORAGE_DIR = Path("data")
DEFAULT_SOURCES_FILE = Path("url.md")
DEFAULT_RESULT_PATH = Path("result.txt")
DEFAULT_TIMEOUT = 20
DEFAULT_LIMIT = 30
DEFAULT_SOURCE_URL = "https://home.knu.ac.kr/HOME/seeai/"
DEFAULT_HTML_TAGS = {"li", "tr", "div", "article", "section"}

DATE_PATTERN = re.compile(r"(20\d{2}[./-]\d{1,2}[./-]\d{1,2})")
NOTICE_HREF_PATTERNS = ("mode=view", "viewBtin.action", "pg=vv")


@dataclass(frozen=True)
class AppConfig:
    config_path: Path = DEFAULT_CONFIG_PATH
    storage_dir: Path = DEFAULT_STORAGE_DIR
    sources_file: Path = DEFAULT_SOURCES_FILE
    result_path: Path = DEFAULT_RESULT_PATH
    keywords: list[str] = field(default_factory=list)
    source_url: str | None = None
    source_url_override: str | None = None
    sources: list[dict[str, str]] = field(default_factory=list)
    source_filters: list[str] = field(default_factory=list)
    after_date: str | None = None
    before_date: str | None = None
    new_only: bool = False
    limit: int = DEFAULT_LIMIT
    timeout: int = DEFAULT_TIMEOUT
    offline_html_path: str | None = None


@dataclass(frozen=True)
class ResultPaths:
    latest: Path
    previous: Path
    history: Path
    result: Path


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def append_jsonl(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(data, ensure_ascii=False) + "\n")


def normalize_text(text: str) -> str:
    return " ".join(text.split())


def extract_date(anchor) -> str:
    for parent in anchor.parents:
        if parent.name in DEFAULT_HTML_TAGS:
            block_text = normalize_text(parent.get_text(" ", strip=True))
            match = DATE_PATTERN.search(block_text)
            if match:
                return match.group(1).replace(".", "-").replace("/", "-")
    return ""


def extract_context(anchor) -> str:
    for parent in anchor.parents:
        if parent.name in DEFAULT_HTML_TAGS:
            block_text = normalize_text(parent.get_text(" ", strip=True))
            if block_text:
                return block_text[:500]
    return ""


def parse_notices(html: str, base_url: str, source_name: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    notices: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for anchor in soup.select("a[href]"):
        href = anchor.get("href")
        if not href:
            continue
        if not any(pattern in href for pattern in NOTICE_HREF_PATTERNS):
            continue

        absolute_url = urljoin(base_url, href)
        if absolute_url in seen_urls:
            continue

        title = normalize_text(anchor.get_text(" ", strip=True))
        if not title:
            continue

        seen_urls.add(absolute_url)
        notices.append(
            {
                "id": hashlib.sha1(absolute_url.encode("utf-8")).hexdigest()[:16],
                "title": title,
                "url": absolute_url,
                "date": extract_date(anchor),
                "source": source_name,
                "context": extract_context(anchor),
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


def parse_notice_filter_date(date_text: str | None) -> datetime | None:
    if not date_text:
        return None
    parsed = parse_notice_date(date_text)
    if parsed is not None:
        return parsed
    try:
        return datetime.fromisoformat(date_text)
    except ValueError:
        return None


def build_notice_search_text(notice: dict[str, Any]) -> str:
    return normalize_text(
        " ".join(part for part in [str(notice.get("title", "")), str(notice.get("context", ""))] if part)
    ).casefold()


def sort_notices_latest(notices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        notices,
        key=lambda item: parse_notice_date(str(item.get("date", ""))) or datetime.min,
        reverse=True,
    )


def dedupe_notices(notices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    dedup: dict[str, dict[str, Any]] = {}
    for notice in notices:
        dedup[notice["url"]] = notice
    return list(dedup.values())


def filter_notices_by_keywords(notices: list[dict[str, Any]], keywords: list[str]) -> list[dict[str, Any]]:
    cleaned_keywords = [keyword.strip() for keyword in keywords if keyword.strip()]
    if not cleaned_keywords:
        return [dict(notice, matched_keywords=[]) for notice in notices]

    filtered: list[dict[str, Any]] = []
    for notice in notices:
        search_text = build_notice_search_text(notice)
        matched = [keyword for keyword in cleaned_keywords if keyword.casefold() in search_text]
        if matched:
            entry = dict(notice)
            entry["matched_keywords"] = matched
            filtered.append(entry)
    return filtered


def filter_notices_by_criteria(
    notices: list[dict[str, Any]],
    keywords: list[str],
    source_filters: list[str] | None = None,
    after_date: str | None = None,
    before_date: str | None = None,
    new_only: bool = False,
) -> list[dict[str, Any]]:
    filtered = filter_notices_by_keywords(notices, keywords)
    source_terms = [item.strip().casefold() for item in (source_filters or []) if item.strip()]
    after = parse_notice_filter_date(after_date)
    before = parse_notice_filter_date(before_date)

    result: list[dict[str, Any]] = []
    for notice in filtered:
        source_text = str(notice.get("source", "")).casefold()
        url_text = str(notice.get("url", "")).casefold()
        if source_terms and not any(term in source_text or term in url_text for term in source_terms):
            continue

        notice_date = parse_notice_date(str(notice.get("date", "")))
        if after is not None and (notice_date is None or notice_date < after):
            continue
        if before is not None and (notice_date is None or notice_date > before):
            continue
        if new_only and not notice.get("is_new"):
            continue

        result.append(notice)

    return result


def write_result_text(path: Path, filtered: list[dict[str, Any]], fetch_errors: list[str] | None = None) -> None:
    lines: list[str] = []
    if fetch_errors:
        lines.append("수집 경고:")
        for error in fetch_errors:
            lines.append(f"- {error}")
        lines.append("")

    if not filtered:
        lines.append("매칭된 공지사항이 없습니다.")
    else:
        for idx, notice in enumerate(filtered, start=1):
            new_tag = " [NEW]" if notice.get("is_new") else ""
            date = notice.get("date") or "날짜 없음"
            lines.append(f"{idx}. {notice['title']}{new_tag}")
            lines.append(f"   - 날짜: {date}")
            lines.append(f"   - 출처: {notice.get('source', '-')}")
            lines.append(f"   - 키워드: {', '.join(notice.get('matched_keywords', []))}")
            lines.append(f"   - 링크: {notice['url']}")
            context = notice.get("context")
            if context:
                lines.append(f"   - 요약: {context}")
            lines.append("")

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def fetch_html(source_url: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    candidates = [source_url]
    if source_url.endswith("/"):
        candidates.append(source_url.rstrip("/") + "/index.htm")
    elif not source_url.lower().endswith(".htm") and not source_url.lower().endswith(".html"):
        candidates.append(source_url.rstrip("/") + "/index.htm")

    last_error: Exception | None = None
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
        "Referer": "https://seeai.knu.ac.kr/",
    }

    for candidate in candidates:
        try:
            response = requests.get(candidate, timeout=timeout, headers=headers)
            response.raise_for_status()
            response.encoding = response.apparent_encoding
            html = response.text

            soup = BeautifulSoup(html, "html.parser")
            frame = soup.find("frame", attrs={"name": "bottom"}) or soup.find("frame")
            frame_src = frame.get("src") if frame else None
            if frame_src:
                frame_url = urljoin(candidate, frame_src)
                frame_response = requests.get(frame_url, timeout=timeout, headers=headers)
                frame_response.raise_for_status()
                frame_response.encoding = frame_response.apparent_encoding
                return frame_response.text
            return html
        except requests.RequestException as error:
            last_error = error

    if last_error is not None:
        raise last_error
    raise RuntimeError("HTML을 가져오지 못했습니다.")


def parse_sources_from_markdown(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []

    text = path.read_text(encoding="utf-8")
    sources: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if ":" not in line:
            continue
        match = re.search(r"(https?://\S+)", line)
        if not match:
            continue
        url = match.group(1).rstrip(")")
        name = line[: match.start()].rstrip(": ").strip()
        name = re.sub(r"^\d+\.\s*", "", name)
        if not name:
            name = url
        if url in seen_urls:
            continue
        seen_urls.add(url)
        sources.append({"name": name, "url": url})

    return sources


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="KNU Notice Radar")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="설정 파일 경로")
    parser.add_argument("--source-url", help="공지 수집 URL (설정 덮어쓰기)")
    parser.add_argument("--keyword", action="append", default=None, help="관심 키워드(여러 번 사용 가능)")
    parser.add_argument("--source", action="append", default=None, help="출처 필터(여러 번 사용 가능)")
    parser.add_argument("--after", help="이 날짜 이후 공지만 표시 (YYYY-MM-DD)")
    parser.add_argument("--before", help="이 날짜 이전 공지만 표시 (YYYY-MM-DD)")
    parser.add_argument("--new-only", action="store_true", help="새로 발견된 공지만 표시")
    parser.add_argument("--limit", type=int, help="최대 수집 건수")
    parser.add_argument("--offline-html", help="저장한 HTML 파일로 테스트")
    return parser.parse_args()


def load_config(
    config_path: Path = DEFAULT_CONFIG_PATH,
    *,
    source_url_override: str | None = None,
    keywords_override: list[str] | None = None,
    source_filters_override: list[str] | None = None,
    after_date_override: str | None = None,
    before_date_override: str | None = None,
    new_only_override: bool = False,
    limit_override: int | None = None,
    offline_html_path: str | None = None,
) -> AppConfig:
    config_data = load_json(config_path) or {}
    storage_dir = Path(config_data.get("storage_dir", DEFAULT_STORAGE_DIR))
    sources_file = Path(config_data.get("sources_file", DEFAULT_SOURCES_FILE))
    keywords = keywords_override if keywords_override is not None else list(config_data.get("keywords", []))
    limit = limit_override if limit_override is not None else int(config_data.get("limit", DEFAULT_LIMIT))
    source_url = config_data.get("source_url")
    sources = config_data.get("sources") or []

    return AppConfig(
        config_path=config_path,
        storage_dir=storage_dir,
        sources_file=sources_file,
        result_path=DEFAULT_RESULT_PATH,
        keywords=keywords,
        source_url=source_url,
        source_url_override=source_url_override,
        sources=sources,
        source_filters=source_filters_override or [],
        after_date=after_date_override,
        before_date=before_date_override,
        new_only=new_only_override,
        limit=limit,
        timeout=int(config_data.get("timeout", DEFAULT_TIMEOUT)),
        offline_html_path=offline_html_path,
    )


def resolve_sources(config: AppConfig) -> list[dict[str, str]]:
    if config.source_url_override:
        return [{"name": "사용자 지정 소스", "url": config.source_url_override}]

    if config.sources:
        return list(config.sources)

    parsed_sources = parse_sources_from_markdown(config.sources_file)
    if parsed_sources:
        return parsed_sources

    fallback_url = config.source_url or DEFAULT_SOURCE_URL
    return [{"name": "기본 소스", "url": fallback_url}]


def fetch_all_notices(config: AppConfig, sources: list[dict[str, str]]) -> tuple[list[dict[str, Any]], list[str]]:
    if config.offline_html_path:
        html = Path(config.offline_html_path).read_text(encoding="utf-8")
        base_url = sources[0]["url"]
        return parse_notices(html, base_url, sources[0]["name"]), []

    notices: list[dict[str, Any]] = []
    errors: list[str] = []
    for source in sources:
        try:
            html = fetch_html(source["url"], timeout=config.timeout)
            notices.extend(parse_notices(html, source["url"], source["name"]))
        except requests.RequestException as error:
            errors.append(f"{source['name']}: {error}")

    return notices, errors


def process_notices(
    notices: list[dict[str, Any]],
    previous_ids: set[str],
    config: AppConfig,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ordered = sort_notices_latest(dedupe_notices(notices))
    ordered = ordered[: max(config.limit, 0)]

    for item in ordered:
        item["is_new"] = item["id"] not in previous_ids

    filtered = filter_notices_by_criteria(
        ordered,
        config.keywords,
        source_filters=config.source_filters,
        after_date=config.after_date,
        before_date=config.before_date,
        new_only=config.new_only,
    )
    return ordered, sort_notices_latest(filtered)


def build_previous_ids(previous_data: dict[str, Any] | None) -> set[str]:
    if not previous_data:
        return set()
    previous_source = previous_data.get("all_notices") or previous_data.get("notices", [])
    return {item["id"] for item in previous_source}


def build_applied_filters(config: AppConfig) -> dict[str, Any]:
    return {
        "source_filters": config.source_filters,
        "after_date": config.after_date,
        "before_date": config.before_date,
        "new_only": config.new_only,
    }


def save_all_results(
    paths: ResultPaths,
    summary: dict[str, Any],
    fetch_errors: list[str],
) -> None:
    save_json(
        paths.latest,
        {
            "updated_at": summary["updated_at"],
            "sources": summary["sources"],
            "keywords": summary["keywords"],
            "applied_filters": summary["applied_filters"],
            "total_notices": summary["total_notices"],
            "matched_notices": summary["matched_notices"],
            "fetch_errors": fetch_errors,
            "all_notices": summary["all_notices"],
            "notices": summary["notices"],
        },
    )
    append_jsonl(
        paths.history,
        {
            "updated_at": summary["updated_at"],
            "sources": summary["sources"],
            "keywords": summary["keywords"],
            "applied_filters": summary["applied_filters"],
            "total_notices": summary["total_notices"],
            "matched_notices": summary["matched_notices"],
            "fetch_errors": fetch_errors,
        },
    )
    write_result_text(paths.result, summary["notices"], fetch_errors)


def collect_and_save(config: AppConfig | None = None, **overrides: Any) -> dict[str, Any]:
    runtime_config = config or load_config(**overrides)
    if config is not None and overrides:
        raise ValueError("config가 주어졌다면 추가 overrides를 함께 전달할 수 없습니다.")

    paths = ResultPaths(
        latest=runtime_config.storage_dir / "latest.json",
        previous=runtime_config.storage_dir / "previous.json",
        history=runtime_config.storage_dir / "history.jsonl",
        result=runtime_config.result_path,
    )

    sources = resolve_sources(runtime_config)
    notices, fetch_errors = fetch_all_notices(runtime_config, sources)
    previous_data = load_json(paths.latest)
    previous_ids = build_previous_ids(previous_data)

    all_notices, filtered = process_notices(notices, previous_ids, runtime_config)
    previous_snapshot = previous_data
    if previous_snapshot:
        save_json(paths.previous, previous_snapshot)

    now = datetime.now().isoformat(timespec="seconds")
    summary = {
        "updated_at": now,
        "sources": sources,
        "keywords": runtime_config.keywords,
        "applied_filters": build_applied_filters(runtime_config),
        "total_notices": len(all_notices),
        "matched_notices": len(filtered),
        "all_notices": all_notices,
        "notices": filtered,
        "fetch_errors": fetch_errors,
        "latest_path": str(paths.latest),
        "previous_path": str(paths.previous),
        "result_path": str(paths.result),
        "history_path": str(paths.history),
    }

    save_all_results(paths, summary, fetch_errors)
    return summary


def main() -> None:
    args = parse_args()
    config = load_config(
        Path(args.config),
        source_url_override=args.source_url,
        keywords_override=args.keyword,
        source_filters_override=args.source,
        after_date_override=args.after,
        before_date_override=args.before,
        new_only_override=args.new_only,
        limit_override=args.limit,
        offline_html_path=args.offline_html,
    )
    data = collect_and_save(config=config)

    print(f"수집: {data['total_notices']}건 / 키워드 매칭: {data['matched_notices']}건")
    print(f"- JSON: {data['latest_path']}")
    print(f"- TEXT: {data['result_path']}")
    print(f"- HISTORY: {data['history_path']}")
    if data.get("fetch_errors"):
        print(f"- WARN: {len(data['fetch_errors'])}개 소스 수집 실패")


if __name__ == "__main__":
    main()
