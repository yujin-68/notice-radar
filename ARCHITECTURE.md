# KNU Notice Radar 아키텍처

이 문서는 프로젝트가 어떻게 동작하는지 기능 단위로 분해해 설명합니다. 코드 기준은 `notice_radar.py`, `web_app.py`, `main.py`, `config.json`입니다.

## 1. 전체 구조 한눈에 보기

- **CLI 엔트리**: `main.py` → `notice_radar.main()` 호출
- **핵심 수집/필터 로직**: `notice_radar.py`
- **간단 웹 UI**: `web_app.py` (WSGI 서버)
- **설정**: `config.json`
- **출력**: `data/latest.json`, `data/previous.json`, `result.txt`

## 2. 핵심 모듈: notice_radar.py

### 2.1 입출력 유틸

- **`load_json(path)`**
  - JSON 파일이 있으면 dict로 로드, 없으면 `None` 반환
- **`save_json(path, data)`**
  - 저장 경로 상위 폴더를 생성한 뒤 JSON 저장
- **`write_result_text(path, filtered)`**
  - 사람이 읽기 쉬운 텍스트 형식으로 결과를 출력 (`NEW` 태그 포함)

### 2.2 텍스트 정규화 및 날짜 추출

- **`normalize_text(text)`**
  - 여러 공백/개행을 하나의 공백으로 정리
- **`extract_date(anchor)`**
  - 링크(앵커) 주변 부모 블록에서 `YYYY-MM-DD` 형태 날짜를 탐색
  - `li/tr/div/article/section` 범위 내 텍스트에서 정규식 매칭

### 2.3 공지 파싱

- **`parse_notices(html, base_url, source_name)`**
  - HTML 내 `<a href>`를 순회하며 공지 링크 후보를 수집
  - `NOTICE_HREF_PATTERNS`에 해당하는 링크만 “공지 상세”로 간주
  - 링크 중복 제거(절대 URL 기준)
  - 결과 항목 구조:
    - `id`: URL 해시 기반 식별자
    - `title`, `url`, `date`, `source`

### 2.4 날짜 파싱 및 정렬

- **`parse_notice_date(date_text)`**
  - 문자열 날짜를 `datetime`으로 변환 (실패 시 `None`)
- **`sort_notices_latest(notices)`**
  - 날짜 최신순 정렬 (`datetime.min`을 기본값으로 사용)

### 2.5 키워드 필터링

- **`filter_notices_by_title(notices, keywords)`**
  - 제목에 포함된 키워드가 하나라도 있으면 매칭
  - 매칭된 키워드를 `matched_keywords`로 기록

### 2.6 HTML 수집

- **`fetch_html(source_url, timeout=20)`**
  - 요청 URL이 디렉터리 형태면 `/index.htm`도 시도
  - `User-Agent`, `Accept-Language`, `Referer` 헤더 지정
  - frameset 구조일 경우 `frame`의 `src`를 따라가 실제 콘텐츠 HTML을 반환

### 2.7 소스 목록 파싱

- **`parse_sources_from_markdown(path)`**
  - 마크다운 텍스트에서 URL을 추출해 소스 목록 생성
  - `url.md`가 존재할 경우 설정 소스로 사용됨

### 2.8 전체 파이프라인

- **`collect_and_save(...)`**
  1. `config.json` 로드 및 오버라이드 적용
  2. 소스 URL 결정(직접 입력 → 설정 → 마크다운 → 기본 URL)
  3. HTML 수집 및 공지 파싱
  4. URL 중복 제거 후 최신순 정렬
  5. 키워드 필터링 및 최신순 정렬
  6. 이전 실행 결과(`latest.json`)를 불러 NEW 여부 표시
  7. `latest.json`, `previous.json`, `result.txt` 저장
  8. 요약 dict 반환

### 2.9 CLI 엔트리

- **`main()`**
  - `argparse`로 인자 파싱 후 `collect_and_save()` 호출
  - 결과 요약을 표준 출력으로 표시

## 3. CLI 엔트리: main.py

- `notice_radar.main()`만 호출하는 얇은 래퍼
- 실제 로직은 `notice_radar.py`에 집중됨

## 4. Web UI: web_app.py

### 4.1 렌더링

- **`render_page(data)`**
  - JSON 결과를 HTML 테이블로 렌더링
  - `NEW` 태그 표시
  - 안전한 출력 위해 `html.escape` 사용

### 4.2 데이터 로딩

- **`get_data(refresh)`**
  - `data/latest.json`이 없거나 `refresh=1`이면 수집 실행
  - 그렇지 않으면 저장된 최신 결과 사용

### 4.3 WSGI 애플리케이션

- **`app(environ, start_response)`**
  - `/` 경로만 허용
  - 쿼리 스트링에서 `refresh` 처리
  - HTML 응답 반환

### 4.4 로컬 서버 실행

- `python web_app.py` 실행 시 `127.0.0.1:5000`에서 서비스

## 5. 데이터 흐름(실행 순서)

1. 사용자 실행 (`python main.py` 또는 `python web_app.py`)
2. 설정 로드 및 오버라이드 적용
3. 소스 URL별 HTML 다운로드
4. 공지 링크/제목/날짜 추출
5. 중복 제거 및 최신순 정렬
6. 키워드 필터링
7. 이전 실행 결과와 비교해 `NEW` 표시
8. 파일 저장 및 출력

## 6. 저장 파일 구조

- **`data/latest.json`**
  - 최신 실행 스냅샷 (sources/keywords/매칭 결과 포함)
- **`data/previous.json`**
  - 직전 `latest.json`의 백업 (NEW 판단 기준)
- **`result.txt`**
  - 사람이 읽기 쉬운 요약 결과

## 7. 설정 파일(`config.json`)

| 키 | 의미 |
|---|---|
| `sources_file` | 소스 목록 마크다운 파일 경로 (예: `url.md`) |
| `keywords` | 관심 키워드 목록 |
| `limit` | 최대 수집 건수 |
| `storage_dir` | 결과 저장 폴더 (기본 `data`) |

## 8. 주요 동작 규칙 / 제약

- **키워드 매칭**: 제목에 포함된 키워드가 하나라도 있으면 매칭(OR 조건)
- **중복 제거**: URL 기준으로 dedup
- **NEW 표시**: 이전 실행 결과와 비교하여 `id`가 새로 등장한 경우
- **날짜 추출**: 링크 주변 블록 텍스트에서 정규식으로 탐색

## 9. 확장 포인트

- 새로운 공지 사이트는 `NOTICE_HREF_PATTERNS`와 소스 목록만 추가하면 확장 가능
- 제목 외에 본문 파싱을 추가하면 키워드 매칭 정확도를 높일 수 있음
- `url.md`를 만들면 여러 소스를 손쉽게 관리 가능
