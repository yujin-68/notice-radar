from __future__ import annotations

import re
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
from wsgiref.simple_server import make_server

from notice_radar import DEFAULT_CONFIG_PATH, DEFAULT_SOURCE_URL, collect_and_save, load_json


def load_base_config() -> dict:
    base = load_json(DEFAULT_CONFIG_PATH) or {}
    return {
        "keywords": list(base.get("keywords", [])),
        "limit": int(base.get("limit", 30)),
        "storage_dir": Path(base.get("storage_dir", "data")),
        "result_path": Path(base.get("result_path", "result.txt")),
        "timeout": int(base.get("timeout", 20)),
        "sources": base.get("sources") or [{"name": "기본 소스", "url": base.get("source_url", DEFAULT_SOURCE_URL)}],
    }


def split_keywords(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,\n|]+", text) if part.strip()]


def period_to_after(period: str) -> datetime | None:
    days_map = {"1d": 1, "7d": 7, "30d": 30}
    days = days_map.get(period)
    return datetime.now() - timedelta(days=days) if days else None


def parse_state(query: dict[str, list[str]]) -> dict:
    keyword_text = query.get("keywords", [""])[0].strip()
    keywords = split_keywords(keyword_text) if keyword_text else []
    source = query.get("source", ["all"])[0]
    period = query.get("period", ["all"])[0]
    new_only = query.get("new", ["all"])[0] == "only"
    return {
        "keyword_text": keyword_text,
        "keywords": keywords,
        "source": source,
        "period": period,
        "new_only": new_only,
    }


def apply_filters(data: dict, state: dict) -> dict:
    if state["source"] == "all" and state["period"] == "all" and not state["new_only"] and not state["keywords"]:
        return data

    after_date = period_to_after(state["period"])
    source_terms = [] if state["source"] == "all" else [state["source"]]
    keyword_list = state["keywords"]
    filtered = []

    for notice in data.get("all_notices", data.get("notices", [])):
        text = f"{notice.get('title', '')} {notice.get('context', '')}".casefold()
        matched = [keyword for keyword in keyword_list if keyword.casefold() in text]
        if keyword_list and not matched:
            continue
        if source_terms:
            source_text = f"{notice.get('source', '')} {notice.get('url', '')}".casefold()
            if not any(term.casefold() in source_text for term in source_terms):
                continue
        if after_date and notice.get("date") and notice["date"] < after_date.strftime("%Y-%m-%d"):
            continue
        if state["new_only"] and not notice.get("is_new"):
            continue

        item = dict(notice)
        item["matched_keywords"] = matched
        filtered.append(item)

    derived = dict(data)
    derived["notices"] = filtered
    derived["matched_notices"] = len(filtered)
    derived["applied_filters"] = {
        "source": state["source"],
        "period": state["period"],
        "new_only": state["new_only"],
    }
    derived["keywords"] = keyword_list
    return derived


def source_options(data: dict) -> list[dict[str, str]]:
    sources = data.get("sources") or []
    options = [{"value": "all", "label": "전체 출처"}]
    for source in sources:
        value = source.get("url") or source.get("name")
        label = source.get("name") or value
        if value:
            options.append({"value": value, "label": label})
    return options


def render_page(data: dict, state: dict, options: list[dict[str, str]]) -> str:
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
        [
            ("refresh", "1"),
            ("keywords", state["keyword_text"]),
            ("source", state["source"]),
            ("period", state["period"]),
            ("new", "only" if state["new_only"] else "all"),
        ]
    )
    source_label = next((option["label"] for option in options if option["value"] == state["source"]), state["source"])
    filter_text = " · ".join(
        part
        for part in [
            f"출처: {source_label}" if state["source"] != "all" else "",
            f"최근: {state['period']}" if state["period"] != "all" else "",
            "NEW만 표시" if state["new_only"] else "",
        ]
        if part
    ) or "없음"

    keyword_options = escape(state["keyword_text"])
    source_html = "".join(
        f'<option value="{escape(option["value"], quote=True)}"{" selected" if option["value"] == state["source"] else ""}>{escape(option["label"])}</option>'
        for option in options
    )
    period_options = {
        "all": "전체 기간",
        "1d": "1일 이내",
        "7d": "7일 이내",
        "30d": "30일 이내",
    }
    period_html = "".join(
        f'<option value="{value}"{" selected" if value == state["period"] else ""}>{label}</option>'
        for value, label in period_options.items()
    )
    new_html = "".join(
        f'<option value="{value}"{" selected" if ((value == "only") == state["new_only"]) else ""}>{label}</option>'
        for value, label in [("all", "전체"), ("only", "NEW만")]
    )

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
    form {{ display: grid; gap: 10px; margin: 16px 0; padding: 12px; background: #f8f8f8; border-radius: 8px; }}
    .row {{ display: grid; gap: 8px; grid-template-columns: repeat(3, minmax(0, 1fr)); }}
    label {{ display: grid; gap: 4px; font-size: 14px; color: #222; }}
    input, select {{ padding: 8px 10px; border: 1px solid #ccc; border-radius: 6px; font: inherit; }}
    .actions {{ display: flex; gap: 8px; }}
    .btn {{ display: inline-block; background: #111; color: #fff; text-decoration: none; padding: 8px 12px; border-radius: 6px; border: none; cursor: pointer; }}
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
    필터: {escape(filter_text)}
  </div>
  {"<div class='warning'>수집 경고: " + escape("; ".join(data.get('fetch_errors', []))) + "</div>" if data.get("fetch_errors") else ""}
  <form method="get" action="/">
    <label>
      키워드
      <input type="text" name="keywords" value="{keyword_options}" placeholder="예: 장학금, 공모전, AI" />
    </label>
    <div class="row">
      <label>
        출처
        <select name="source">{source_html}</select>
      </label>
      <label>
        기간
        <select name="period">{period_html}</select>
      </label>
      <label>
        NEW 표시
        <select name="new">{new_html}</select>
      </label>
    </div>
    <div class="actions">
      <button class="btn" type="submit">적용</button>
      <button class="btn" type="submit" name="refresh" value="1">새로고침</button>
    </div>
  </form>
  {table}
</body>
</html>
"""


def get_data(refresh: bool, query: dict[str, list[str]]) -> tuple[dict, dict]:
    base = load_base_config()
    latest_path = base["storage_dir"] / "latest.json"
    if refresh or not latest_path.exists():
        data = collect_and_save(base)
    else:
        data = load_json(latest_path) or collect_and_save(base)
    state = parse_state(query)
    return apply_filters(data, state), state


def app(environ, start_response):
    parsed = urlparse(environ.get("PATH_INFO", "/") + ("?" + environ.get("QUERY_STRING", "") if environ.get("QUERY_STRING") else ""))
    if parsed.path != "/":
        start_response("404 Not Found", [("Content-Type", "text/plain; charset=utf-8")])
        return [b"Not Found"]

    query = parse_qs(parsed.query)
    data, state = get_data(query.get("refresh", ["0"])[0] == "1", query)
    html = render_page(data, state, source_options(data)).encode("utf-8")
    start_response("200 OK", [("Content-Type", "text/html; charset=utf-8"), ("Content-Length", str(len(html)))])
    return [html]


if __name__ == "__main__":
    with make_server("127.0.0.1", 5000, app) as server:
        print("Web UI: http://127.0.0.1:5000")
        server.serve_forever()
