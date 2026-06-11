# KNU Notice Radar 아키텍처

이 문서는 프로젝트가 어떻게 동작하는지 기능 단위로 분해해 설명합니다. 본 프로젝트의 핵심은 **정적 웹페이지에서 공지사항 데이터를 견고하게 추출하는 크롤링 엔진**에 있으며, 코드는 `notice_radar.py`, `web_app.py`, `main.py`, `config.json`을 기준으로 합니다.

## 1. 전체 구조 한눈에 보기

* **핵심 크롤링/필터 로직**: `notice_radar.py` (웹 요청, DOM 탐색 파싱, 필터링)
* **CLI 엔트리**: `main.py` → `notice_radar.main()` 호출
* **간단 웹 UI**: `web_app.py` (WSGI 서버 기반 결과 뷰어)
* **설정 및 출력**: `config.json` (설정) / `data/latest.json`, `result.txt` (출력)

---

## 2. 핵심 모듈: notice_radar.py (크롤링 엔진)

가장 중요한 크롤링 파이프라인은 **1) 대상 패턴 정의**, **2) 방어 우회 및 HTML 수집**, **3) DOM 트리 기반의 유연한 파싱** 세 단계로 이루어집니다.

### 2.1 크롤링 대상 패턴 정의 (상수 및 정규식)

특정 웹사이트의 CSS 클래스나 ID에 의존하지 않고, 범용적으로 공지사항을 찾아내기 위한 기준점을 정의합니다.

```python
import re

# 공지사항의 제목, 작성일, 요약 본문이 포함될 가능성이 높은 논리적 부모 HTML 블록
HTML_TAGS = {"li", "tr", "div", "article", "section"}

# 텍스트 내에서 작성일(예: 2026.06.11, 2026-06-11)을 찾아내기 위한 정규표현식
DATE_PATTERN = re.compile(r"(20\d{2}[./-]\d{1,2}[./-]\d{1,2})")

# 단순 링크가 아닌, 실제 '공지사항 본문'으로 향하는 고유 URL 패턴
NOTICE_HREF_PATTERNS = ("mode=view", "viewBtin.action", "pg=vv")

```

### 2.2 방어 우회 및 HTML 수집 (`fetch_html`)

대상 서버에 접근하여 HTML 소스코드를 안전하게 다운로드하고, 오래된 웹 표준 구조(`<frame>`)를 처리합니다.

```python
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

DEFAULT_SOURCE_URL = "https://home.knu.ac.kr/HOME/seeai/"
DEFAULT_TIMEOUT = 20

def fetch_html(source_url: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    # 봇 차단 방지를 위한 브라우저 위장 헤더
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
        "Referer": DEFAULT_SOURCE_URL,
    }
    last_error: Exception | None = None
    
    # URL이 디렉터리로 끝날 경우 /index.htm을 추가한 버전을 함께 시도
    for candidate in (source_url, source_url.rstrip("/") + "/index.htm" if not source_url.endswith((".htm", ".html")) else source_url):
        try:
            response = requests.get(candidate, timeout=timeout, headers=headers)
            response.raise_for_status()
            response.encoding = response.apparent_encoding
            html = response.text
            
            # frameset 구조 대응: 껍데기 HTML 대신 실제 콘텐츠가 담긴 프레임(bottom 등)의 src를 추적
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

```

### 2.3 DOM 탐색 기반 HTML 파싱 (`extract_date`, `parse_notices`)

가져온 HTML에서 `BeautifulSoup`을 활용해 구조 변경에 강건한(Robust) 방식으로 데이터를 추출합니다. `<a>` 태그를 찾은 뒤, 그 링크를 감싸는 **상위 부모 요소로 거슬러 올라가며(Bottom-up)** 데이터를 수집하는 것이 핵심입니다.

```python
import hashlib

def normalize_text(text: str) -> str:
    return " ".join(text.split())

def extract_date(anchor) -> str:
    # <a> 태그 기준, 부모 요소(li, tr 등)로 거슬러 올라가며 정규식을 통해 날짜 추출
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
        
        # 공지사항 본문 링크 패턴 필터링
        if not href or not any(pattern in href for pattern in NOTICE_HREF_PATTERNS):
            continue

        # 상대경로를 절대경로로 변환
        url = urljoin(base_url, href)
        if url in seen_urls:
            continue

        title = normalize_text(anchor.get_text(" ", strip=True))
        if not title:
            continue

        seen_urls.add(url)
        context = ""
        
        # 문맥(Context) 추출: 부모 태그 영역의 텍스트를 가져와 최대 500자 요약본 생성
        for parent in anchor.parents:
            if parent.name in HTML_TAGS:
                context = normalize_text(parent.get_text(" ", strip=True))[:500]
                break

        notices.append(
            {
                # 고유 식별자(ID)를 위해 URL 해싱
                "id": hashlib.sha1(url.encode("utf-8")).hexdigest()[:16],
                "title": title,
                "url": url,
                "date": extract_date(anchor),
                "source": source_name,
                "context": context,
            }
        )

    return notices

```

### 2.4 데이터 필터링 및 정렬 (`filter_notices`, `sort_notices_latest`)

* 추출된 데이터 중 제목과 요약 문맥(`context`)을 병합한 문자열에서 사용자 정의 키워드를 탐색합니다. (OR 조건)
* 출처(Source) 및 날짜(`after`, `before`) 기준으로 필터링을 수행하며, 추출된 날짜를 기준으로 최신순 정렬을 보장합니다.

---

## 3. 웹 UI 및 로컬 서빙 (web_app.py)

* **WSGI 애플리케이션 (`app`)**: 내장 `wsgiref.simple_server`를 사용하여 경량 웹 서버를 띄웁니다. (`127.0.0.1:5000`)
* **데이터 로딩 및 렌더링**:
* `data/latest.json`이 없거나 URL 쿼리에 `refresh=1`이 있으면 즉시 크롤러를 재가동(`notice_radar.collect_and_save()`)합니다.
* 결과를 HTML 테이블로 렌더링하며, XSS 방지를 위해 `html.escape`를 적용합니다.



---

## 4. 데이터 흐름 (실행 파이프라인)

1. **실행**: `python main.py` (CLI) 또는 `python web_app.py` (웹)
2. **설정 로드**: `config.json` 및 CLI 인자 병합
3. **HTML 다운로드**: `fetch_html`이 우회 헤더와 프레임 탐색 기법을 사용해 소스 확보
4. **DOM 파싱**: `parse_notices`가 부모 노드를 탐색하며 링크, 제목, 날짜, 문맥을 정교하게 추출
5. **데이터 정제**: URL 기준 중복 제거 후 최신순 정렬 (최대 `limit` 개수 제한)
6. **필터링 적용**: 설정된 키워드, 기간, 출처 기준 필터 적용
7. **저장 및 출력**: 스냅샷(`latest.json`) 및 가독성 높은 텍스트(`result.txt`) 저장

---

## 5. 저장 파일 구조

* **`data/latest.json`**
* 최신 실행 스냅샷
* `all_notices`: 중복 제거 및 최신순 정렬된 원본 수집 데이터
* `notices`: 키워드/기간 필터가 적용된 최종 매칭 데이터


* **`result.txt`**: 사람이 읽기 쉽게 포맷팅된 요약 결과 보고서

---

## 6. 주요 동작 규칙 및 아키텍처 강점

* **구조적 강건함 (Robustness)**: 특정 CSS 선택자(Selector)에 의존하는 대신 `HTML_TAGS`와 앵커 기준의 상향식(Bottom-up) DOM 트리 탐색을 사용하여 웹사이트 개편 시 유지보수 비용을 최소화했습니다.
* **단일 페이지 제약**: 불필요한 트래픽 유발을 막기 위해 각 소스의 **첫 목록 페이지만 수집**하고 페이지네이션은 따라가지 않습니다.
* **유연한 확장성**: 새로운 공지사항 게시판을 추가하려면 `config.json`의 `sources`에 URL만 추가하면 되며, 필요시 `NOTICE_HREF_PATTERNS`만 업데이트하면 즉각 대응 가능합니다.