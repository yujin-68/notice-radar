from __future__ import annotations

from html import escape
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from wsgiref.simple_server import make_server

from notice_radar import collect_and_save, load_json


def render_page(data: dict) -> str:
    keywords = ", ".join(data.get("keywords", [])) if data.get("keywords") else "없음"
    rows = []
    for notice in data.get("notices", []):
        date_text = escape(notice.get("date") or "-")
        title = escape(notice.get("title", ""))
        url = escape(notice.get("url", "#"), quote=True)
        matched = ", ".join(notice.get("matched_keywords", []))
        new_tag = '<span class="tag">NEW</span>' if notice.get("is_new") else ""
        rows.append(
            "<tr>"
            f"<td>{date_text}</td>"
            f"<td>{escape(notice.get('source', '-'))}</td>"
            f"<td><a href=\"{url}\" target=\"_blank\" rel=\"noreferrer\">{title}</a>{new_tag}</td>"
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
    .empty {{ margin-top: 20px; color: #666; }}
  </style>
</head>
<body>
  <h1>KNU Notice Radar</h1>
  <div class="meta">
    업데이트: {escape(data.get("updated_at", "-"))} · 수집 {data.get("total_notices", 0)}건 · 매칭 {data.get("matched_notices", 0)}건<br />
    키워드: {escape(keywords)}
  </div>
  <a class="btn" href="/?refresh=1">새로고침</a>
  {table_html}
</body>
</html>
"""


def get_data(refresh: bool) -> dict:
    latest_path = Path("data") / "latest.json"
    if refresh or not latest_path.exists():
        return collect_and_save(config_path=Path("config.json"))
    data = load_json(latest_path)
    if data is None:
        return collect_and_save(config_path=Path("config.json"))
    return data


def app(environ, start_response):
    parsed = urlparse(environ.get("PATH_INFO", "/") + ("?" + environ.get("QUERY_STRING", "") if environ.get("QUERY_STRING") else ""))
    if parsed.path != "/":
        start_response("404 Not Found", [("Content-Type", "text/plain; charset=utf-8")])
        return [b"Not Found"]

    query = parse_qs(parsed.query)
    refresh = query.get("refresh", ["0"])[0] == "1"
    data = get_data(refresh=refresh)
    html = render_page(data).encode("utf-8")
    start_response("200 OK", [("Content-Type", "text/html; charset=utf-8"), ("Content-Length", str(len(html)))])
    return [html]


if __name__ == "__main__":
    with make_server("127.0.0.1", 5000, app) as server:
        print("Web UI: http://127.0.0.1:5000")
        server.serve_forever()
