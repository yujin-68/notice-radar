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

# 공지 블록 텍스트에서 날짜를 찾기 위한 정규식 패턴
DATE_PATTERN = re.compile(r"(20\d{2}[./-]\d{1,2}[./-]\d{1,2})")
# 공지 상세 페이지 링크로 판단할 때 사용하는 href 단서들
NOTICE_HREF_PATTERNS = ("mode=view", "viewBtin.action", "pg=vv")


# JSON 파일을 읽어 dict로 반환 (없으면 None)
def load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# JSON 파일 저장 (폴더가 없으면 생성)
def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def append_jsonl(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(data, ensure_ascii=False) + "\n")


# 공백 정규화: 여러 공백/개행을 하나의 공백으로 합침
def normalize_text(text: str) -> str:
    return " ".join(text.split())


# 링크 앵커 주변 블록에서 날짜 패턴을 찾아 반환
def extract_date(anchor) -> str:
    for parent in anchor.parents:
        if parent.name in {"li", "tr", "div", "article", "section"}:
            block_text = normalize_text(parent.get_text(" ", strip=True))
            match = DATE_PATTERN.search(block_text)
            if match:
                return match.group(1).replace(".", "-").replace("/", "-")
    return ""


def extract_context(anchor) -> str:
    for parent in anchor.parents:
        if parent.name in {"li", "tr", "div", "article", "section"}:
            block_text = normalize_text(parent.get_text(" ", strip=True))
            if block_text:
                return block_text[:500]
    return ""


# HTML에서 공지 링크/제목/날짜/출처를 추출
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
        context = extract_context(anchor)
        notices.append(
            {
                "id": hashlib.sha1(absolute_url.encode("utf-8")).hexdigest()[:16],
                "title": title,
                "url": absolute_url,
                "date": extract_date(anchor),
                "source": source_name,
                "context": context,
            }
        )

    return notices


# 문자열 날짜를 datetime으로 변환 (파싱 실패 시 None)
def parse_notice_date(date_text: str) -> datetime | None:
    if not date_text:
        return None
    normalized = date_text.replace(".", "-").replace("/", "-")
    parts = normalized.split("-")
    if len(parts) != 3:
        return None
    try:
        year, month, day = (int(p) for p in parts)
        return datetime(year, month, day)
    except ValueError:
        return None


def build_notice_search_text(notice: dict[str, Any]) -> str:
    return normalize_text(" ".join(part for part in [str(notice.get("title", "")), str(notice.get("context", ""))] if part)).casefold()


# 공지 목록을 날짜 기준 최신순 정렬
def sort_notices_latest(notices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        notices,
        key=lambda item: parse_notice_date(str(item.get("date", ""))) or datetime.min,
        reverse=True,
    )


def filter_notices_by_keywords(notices: list[dict[str, Any]], keywords: list[str]) -> list[dict[str, Any]]:
    cleaned_keywords = [k.strip() for k in keywords if k.strip()]
    if not cleaned_keywords:
        return [dict(notice, matched_keywords=[]) for notice in notices]

    filtered: list[dict[str, Any]] = []
    for notice in notices:
        search_text = build_notice_search_text(notice)
        matched = [k for k in cleaned_keywords if k.casefold() in search_text]
        if matched:
            entry = dict(notice)
            entry["matched_keywords"] = matched
            filtered.append(entry)

    return filtered


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


# 결과를 사람이 읽기 쉬운 텍스트 파일로 저장
def write_result_text(path: Path, filtered: list[dict[str, Any]]) -> None:
    lines: list[str] = []
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


# 공지 HTML을 가져오되 frameset 하위 frame도 처리
def fetch_html(source_url: str, timeout: int = 20) -> str:
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

            # 일부 KNU 사이트는 frameset 구조이며 실제 내용은 bottom frame에 있음
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


# 마크다운 목록에서 소스 이름/URL 추출 (예: url.md)
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


# CLI 인자 파싱
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="KNU Notice Radar")
    parser.add_argument("--config", default="config.json", help="설정 파일 경로")
    parser.add_argument("--source-url", help="공지 수집 URL (설정 덮어쓰기)")
    parser.add_argument("--keyword", action="append", default=None, help="관심 키워드(여러 번 사용 가능)")
    parser.add_argument("--source", action="append", default=None, help="출처 필터(여러 번 사용 가능)")
    parser.add_argument("--after", help="이 날짜 이후 공지만 표시 (YYYY-MM-DD)")
    parser.add_argument("--before", help="이 날짜 이전 공지만 표시 (YYYY-MM-DD)")
    parser.add_argument("--new-only", action="store_true", help="새로 발견된 공지만 표시")
    parser.add_argument("--limit", type=int, help="최대 수집 건수")
    parser.add_argument("--offline-html", help="저장한 HTML 파일로 테스트")
    return parser.parse_args()


# 공지 수집→필터링→저장까지의 전체 파이프라인
def collect_and_save(
    config_path: Path = Path("config.json"),
    source_url_override: str | None = None,
    keywords_override: list[str] | None = None,
    source_filters_override: list[str] | None = None,
    after_date_override: str | None = None,
    before_date_override: str | None = None,
    new_only_override: bool = False,
    limit_override: int | None = None,
    offline_html_path: str | None = None,
) -> dict[str, Any]:
    config = load_json(config_path) or {}
    keywords = keywords_override if keywords_override is not None else config.get("keywords", [])
    limit = limit_override if limit_override is not None else int(config.get("limit", 30))
    storage_dir = Path(config.get("storage_dir", "data"))
    sources_file = Path(config.get("sources_file", "url.md"))
    latest_path = storage_dir / "latest.json"
    previous_path = storage_dir / "previous.json"
    history_path = storage_dir / "history.jsonl"
    result_path = Path("result.txt")

    if source_url_override:
        sources = [{"name": "사용자 지정 소스", "url": source_url_override}]
    else:
        sources = config.get("sources") or parse_sources_from_markdown(sources_file)
        if not sources:
            fallback_url = config.get("source_url") or "https://home.knu.ac.kr/HOME/seeai/"
            sources = [{"name": "기본 소스", "url": fallback_url}]

    if offline_html_path:
        # 오프라인 테스트 모드에서는 단일 소스로 처리
        html = Path(offline_html_path).read_text(encoding="utf-8")
        base_url = sources[0]["url"]
        notices = parse_notices(html, base_url, sources[0]["name"])
    else:
        notices = []
        for source in sources:
            html = fetch_html(source["url"])
            notices.extend(parse_notices(html, source["url"], source["name"]))

    # 전체 소스 기준 URL 중복 제거
    dedup: dict[str, dict[str, Any]] = {}
    for notice in notices:
        dedup[notice["url"]] = notice
    notices = list(dedup.values())
    notices = sort_notices_latest(notices)
    notices = notices[: max(limit, 0)]

    previous_data = load_json(latest_path)
    previous_ids = set()
    if previous_data:
        previous_source = previous_data.get("all_notices") or previous_data.get("notices", [])
        previous_ids = {item["id"] for item in previous_source}
        save_json(previous_path, previous_data)

    for item in notices:
        item["is_new"] = item["id"] not in previous_ids

    filtered = filter_notices_by_criteria(
        notices,
        keywords,
        source_filters=source_filters_override,
        after_date=after_date_override,
        before_date=before_date_override,
        new_only=new_only_override,
    )
    filtered = sort_notices_latest(filtered)

    now = datetime.now().isoformat(timespec="seconds")
    applied_filters = {
        "source_filters": source_filters_override or [],
        "after_date": after_date_override,
        "before_date": before_date_override,
        "new_only": new_only_override,
    }
    save_json(
        latest_path,
        {
            "updated_at": now,
            "sources": sources,
            "keywords": keywords,
            "applied_filters": applied_filters,
            "total_notices": len(notices),
            "matched_notices": len(filtered),
            "all_notices": notices,
            "notices": filtered,
        },
    )
    append_jsonl(
        history_path,
        {
            "updated_at": now,
            "sources": sources,
            "keywords": keywords,
            "applied_filters": applied_filters,
            "total_notices": len(notices),
            "matched_notices": len(filtered),
        },
    )
    write_result_text(result_path, filtered)

    return {
        "updated_at": now,
        "sources": sources,
        "keywords": keywords,
        "applied_filters": applied_filters,
        "total_notices": len(notices),
        "matched_notices": len(filtered),
        "notices": filtered,
        "all_notices": notices,
        "latest_path": str(latest_path),
        "result_path": str(result_path),
        "history_path": str(history_path),
    }


# 엔트리포인트: 인자 처리 후 수집 실행 및 요약 출력
def main() -> None:
    args = parse_args()
    data = collect_and_save(
        config_path=Path(args.config),
        source_url_override=args.source_url,
        keywords_override=args.keyword,
        source_filters_override=args.source,
        after_date_override=args.after,
        before_date_override=args.before,
        new_only_override=args.new_only,
        limit_override=args.limit,
        offline_html_path=args.offline_html,
    )

    print(f"수집: {data['total_notices']}건 / 키워드 매칭: {data['matched_notices']}건")
    print(f"- JSON: {data['latest_path']}")
    print(f"- TEXT: {data['result_path']}")
    print(f"- HISTORY: {data['history_path']}")


if __name__ == "__main__":
    main()
