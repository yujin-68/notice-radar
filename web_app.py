from __future__ import annotations

from html import escape
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
from wsgiref.simple_server import make_server

from notice_radar import collect_and_save, filter_notices_by_criteria, load_json


# 결과 데이터를 받아 HTML 페이지로 렌더링
def render_page(data: dict) -> str:
    keywords = ", ".join(data.get("keywords", [])) if data.get("keywords") else "없음"
    applied_filters = data.get("applied_filters", {})
    filter_summary = []
    if applied_filters.get("source_filters"):
        filter_summary.append(f"출처: {', '.join(applied_filters['source_filters'])}")
    if applied_filters.get("after_date"):
        filter_summary.append(f"이후: {applied_filters['after_date']}")
    if applied_filters.get("before_date"):
        filter_summary.append(f"이전: {applied_filters['before_date']}")
    if applied_filters.get("new_only"):
        filter_summary.append("NEW만 표시")
    filter_text = " · ".join(filter_summary) if filter_summary else "없음"
    rows = []
    for notice in data.get("notices", []):
        date_text = escape(notice.get("date") or "-")
        title = escape(notice.get("title", ""))
        url = escape(notice.get("url", "#"), quote=True)
        matched = ", ".join(notice.get("matched_keywords", []))
        new_tag = '<span class="tag">NEW</span>' if notice.get("is_new") else ""
        context = escape((notice.get("context") or "")[:140])
        context_html = f"<div class='excerpt'>{context}</div>" if context else ""
        rows.append(
            "<tr>"
            f"<td>{date_text}</td>"
            f"<td>{escape(notice.get('source', '-'))}</td>"
            f"<td><a href=\"{url}\" target=\"_blank\" rel=\"noreferrer\">{title}</a>{new_tag}{context_html}</td>"
            f"<td>{escape(matched)}</td>"
            "</tr>"
        )
    table_html = (
        "<table><thead><tr><th style='width:120px;'>날짜</th><th style='width:190px;'>출처</th><th>제목</th><th style='width:140px;'>매칭 키워드</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )
    if not rows:
        table_html = "<div class='empty'>매칭된 공고가 없습니다.</div>"

    refresh_url = "/?refresh=1"
    if applied_filters:
        query_params: list[tuple[str, str]] = [("refresh", "1")]
        for source in applied_filters.get("source_filters", []):
            query_params.append(("source", source))
        if applied_filters.get("after_date"):
            query_params.append(("after", applied_filters["after_date"]))
        if applied_filters.get("before_date"):
            query_params.append(("before", applied_filters["before_date"]))
        if applied_filters.get("new_only"):
            query_params.append(("new", "1"))
        refresh_url = "/?" + urlencode(query_params)

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
  <a class="btn" href="{refresh_url}">새로고침</a>
  {table_html}
</body>
</html>
"""


# 최신 데이터가 없거나 새로고침 요청 시 수집 실행
def get_data(refresh: bool, filters: dict[str, list[str] | str | bool]) -> dict:
    latest_path = Path("data") / "latest.json"
    if refresh or not latest_path.exists():
        return collect_and_save(
            config_path=Path("config.json"),
            source_filters_override=filters.get("source") if isinstance(filters.get("source"), list) else None,
            after_date_override=filters.get("after") if isinstance(filters.get("after"), str) else None,
            before_date_override=filters.get("before") if isinstance(filters.get("before"), str) else None,
            new_only_override=bool(filters.get("new_only", False)),
        )
    data = load_json(latest_path)
    if data is None:
        return collect_and_save(config_path=Path("config.json"))

    source_filters = filters.get("source") if isinstance(filters.get("source"), list) else []
    after = filters.get("after") if isinstance(filters.get("after"), str) else None
    before = filters.get("before") if isinstance(filters.get("before"), str) else None
    new_only = bool(filters.get("new_only", False))

    if source_filters or after or before or new_only:
        all_notices = data.get("all_notices") or data.get("notices", [])
        filtered = filter_notices_by_criteria(
            all_notices,
            data.get("keywords", []),
            source_filters=source_filters,
            after_date=after,
            before_date=before,
            new_only=new_only,
        )
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

    return data


# WSGI 엔트리: 라우팅/쿼리 처리 후 HTML 응답 생성
def app(environ, start_response):
    parsed = urlparse(environ.get("PATH_INFO", "/") + ("?" + environ.get("QUERY_STRING", "") if environ.get("QUERY_STRING") else ""))
    if parsed.path != "/":
        start_response("404 Not Found", [("Content-Type", "text/plain; charset=utf-8")])
        return [b"Not Found"]

    query = parse_qs(parsed.query)
    refresh = query.get("refresh", ["0"])[0] == "1"
    filters = {
        "source": [item for raw in query.get("source", []) for item in raw.split(",") if item.strip()],
        "after": query.get("after", [None])[0],
        "before": query.get("before", [None])[0],
        "new_only": query.get("new", ["0"])[0] == "1" or query.get("new_only", ["0"])[0] == "1",
    }
    data = get_data(refresh=refresh, filters=filters)
    html = render_page(data).encode("utf-8")
    start_response("200 OK", [("Content-Type", "text/html; charset=utf-8"), ("Content-Length", str(len(html)))])
    return [html]


if __name__ == "__main__":
    # 로컬 개발용 간단 서버 실행
    with make_server("127.0.0.1", 5000, app) as server:
        print("Web UI: http://127.0.0.1:5000")
        server.serve_forever()
