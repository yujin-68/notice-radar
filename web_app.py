from __future__ import annotations

from html import escape
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
from wsgiref.simple_server import make_server

from notice_radar import DEFAULT_CONFIG_PATH, collect_and_save, load_json


def build_config(filters: dict[str, list[str] | str | bool]) -> dict:
    base = load_json(DEFAULT_CONFIG_PATH) or {}
    storage_dir = base.get("storage_dir", "data")
    return {
        "keywords": list(base.get("keywords", [])),
        "source_filters": filters.get("source", []) if isinstance(filters.get("source"), list) else [],
        "after": filters.get("after") if isinstance(filters.get("after"), str) else None,
        "before": filters.get("before") if isinstance(filters.get("before"), str) else None,
        "new_only": bool(filters.get("new_only", False)),
        "limit": int(base.get("limit", 30)),
        "storage_dir": Path(storage_dir),
        "result_path": Path(base.get("result_path", "result.txt")),
        "timeout": int(base.get("timeout", 20)),
        "sources": base.get("sources") or ([{"name": "기본 소스", "url": base.get("source_url", "https://home.knu.ac.kr/HOME/seeai/")}]),
    }


def apply_filters(data: dict, filters: dict[str, list[str] | str | bool]) -> dict:
    source_filters = filters.get("source") if isinstance(filters.get("source"), list) else []
    after = filters.get("after") if isinstance(filters.get("after"), str) else None
    before = filters.get("before") if isinstance(filters.get("before"), str) else None
    new_only = bool(filters.get("new_only", False))

    if not (source_filters or after or before or new_only):
        return data

    filtered = []
    for notice in data.get("all_notices", data.get("notices", [])):
        text = f"{notice.get('title', '')} {notice.get('context', '')}".casefold()
        if data.get("keywords") and not any(keyword.casefold() in text for keyword in data["keywords"] if keyword.strip()):
            continue
        if source_filters:
            source_text = f"{notice.get('source', '')} {notice.get('url', '')}".casefold()
            if not any(term.casefold() in source_text for term in source_filters if term.strip()):
                continue
        if after and notice.get("date") and notice["date"] < after:
            continue
        if before and notice.get("date") and notice["date"] > before:
            continue
        if new_only and not notice.get("is_new"):
            continue
        filtered.append(notice)

    derived = dict(data)
    derived["notices"] = filtered
    derived["matched_notices"] = len(filtered)
    derived["applied_filters"] = {
        "source_filters": source_filters,
        "after_date": after,
        "before_date": before,
        "new_only": new_only,
    }
    return derived


def render_page(data: dict) -> str:
    keywords = ", ".join(data.get("keywords", [])) if data.get("keywords") else "없음"
    applied_filters = data.get("applied_filters", {})
    warnings = data.get("fetch_errors", [])
    rows = []
    for notice in data.get("notices", []):
        rows.append(
            "<tr>"
            f"<td>{escape(notice.get('date') or '-')}</td>"
            f"<td>{escape(notice.get('source', '-'))}</td>"
            f"<td><a href=\"{escape(notice.get('url', '#'), quote=True)}\" target=\"_blank\" rel=\"noreferrer\">{escape(notice.get('title', ''))}</a>"
            f"{'<span class=\"tag\">NEW</span>' if notice.get('is_new') else ''}"
            f"{'<div class=\"excerpt\">' + escape((notice.get('context') or '')[:140]) + '</div>' if notice.get('context') else ''}</td>"
            f"<td>{escape(', '.join(notice.get('matched_keywords', [])))}</td>"
            "</tr>"
        )

    table = (
        "<table><thead><tr><th style='width:120px;'>날짜</th><th style='width:180px;'>출처</th><th>제목</th><th style='width:140px;'>매칭 키워드</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
        if rows
        else "<div class='empty'>매칭된 공고가 없습니다.</div>"
    )
    refresh_query = urlencode(
        [("refresh", "1")]
        + [("source", item) for item in applied_filters.get("source_filters", [])]
        + ([("after", applied_filters["after_date"])] if applied_filters.get("after_date") else [])
        + ([("before", applied_filters["before_date"])] if applied_filters.get("before_date") else [])
        + ([("new", "1")] if applied_filters.get("new_only") else [])
    )
    filter_parts = []
    if applied_filters.get("source_filters"):
        filter_parts.append(f"출처: {', '.join(applied_filters['source_filters'])}")
    if applied_filters.get("after_date"):
        filter_parts.append(f"이후: {applied_filters['after_date']}")
    if applied_filters.get("before_date"):
        filter_parts.append(f"이전: {applied_filters['before_date']}")
    if applied_filters.get("new_only"):
        filter_parts.append("NEW만 표시")
    filter_text = " · ".join(filter_parts) if filter_parts else "없음"

    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>KNU Notice Radar</title>
  <style>
    body {{ font-family: Arial, sans-serif; max-width: 900px; margin: 24px auto; padding: 0 12px; }}
    h1 {{ margin-bottom: 8px; }}
    .meta {{ color: #444; margin-bottom: 16px; }}
    .btn {{ display: inline-block; background: #111; color: #fff; text-decoration: none; padding: 8px 12px; border-radius: 6px; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 16px; }}
    th, td {{ border-bottom: 1px solid #ddd; text-align: left; padding: 10px 8px; vertical-align: top; }}
    th {{ background: #f5f5f5; }}
    .tag {{ display: inline-block; background: #ffe08a; color: #5c4600; border-radius: 999px; padding: 2px 8px; font-size: 12px; margin-left: 6px; }}
    .excerpt {{ color: #666; font-size: 12px; margin-top: 6px; line-height: 1.4; }}
    .warning {{ margin: 12px 0; padding: 10px 12px; border-radius: 6px; background: #fff3cd; color: #6b5300; }}
    .empty {{ margin-top: 20px; color: #666; }}
  </style>
</head>
<body>
  <h1>KNU Notice Radar</h1>
  <div class="meta">
    업데이트: {escape(data.get("updated_at", "-"))} · 수집 {data.get("total_notices", 0)}건 · 매칭 {data.get("matched_notices", 0)}건<br />
    키워드: {escape(keywords)}<br />
    필터: {escape(filter_text)}
  </div>
  {"<div class='warning'>수집 경고: " + escape("; ".join(warnings)) + "</div>" if warnings else ""}
  <a class="btn" href="/?{refresh_query}">새로고침</a>
  {table}
</body>
</html>
"""


def get_data(refresh: bool, filters: dict[str, list[str] | str | bool]) -> dict:
    config = build_config(filters)
    latest_path = config["storage_dir"] / "latest.json"
    if refresh or not latest_path.exists():
        return collect_and_save(config)

    data = load_json(latest_path)
    if data is None:
        return collect_and_save(config)
    return apply_filters(data, filters)


def app(environ, start_response):
    parsed = urlparse(environ.get("PATH_INFO", "/") + ("?" + environ.get("QUERY_STRING", "") if environ.get("QUERY_STRING") else ""))
    if parsed.path != "/":
        start_response("404 Not Found", [("Content-Type", "text/plain; charset=utf-8")])
        return [b"Not Found"]

    query = parse_qs(parsed.query)
    filters = {
        "source": [item for raw in query.get("source", []) for item in raw.split(",") if item.strip()],
        "after": query.get("after", [None])[0],
        "before": query.get("before", [None])[0],
        "new_only": query.get("new", ["0"])[0] == "1" or query.get("new_only", ["0"])[0] == "1",
    }
    data = get_data(query.get("refresh", ["0"])[0] == "1", filters)
    html = render_page(data).encode("utf-8")
    start_response("200 OK", [("Content-Type", "text/html; charset=utf-8"), ("Content-Length", str(len(html)))])
    return [html]


if __name__ == "__main__":
    with make_server("127.0.0.1", 5000, app) as server:
        print("Web UI: http://127.0.0.1:5000")
        server.serve_forever()
