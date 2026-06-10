# Earnings Extractor — 처음부터 끝까지 (End-to-End Essay)

실제 사용자가 웹에서 PDF를 올리는 순간부터, 검증된 Excel을 받고, 그 정확도가 어떻게 측정되는지까지. 어떤 파일과 어떤 함수를 지나가는지 코드 기준으로 따라간다. 추측이 아니라 레포에 있는 코드 그대로다.

---

## 0. 한 문장 요약

> 사용자가 브라우저에서 earnings PDF를 올리면, 얇은 웹 핸들러(`api/extract.py`)가 파일을 받아 **하나의 고정 파이프라인**(`pipeline.py → process_single_pdf`)에 넘긴다. 그 안에서 PDF는 페이지 텍스트가 되고, 결정론적 코드가 추출할 페이지를 고르고, **LLM이 딱 한 번** 숫자를 읽어 인용문과 함께 돌려주고, 그 뒤 모든 단계는 결정론적 코드가 단위를 고치고 검증하고 review 플래그를 단다. 사용자가 화면에서 값을 확인·수정하면 `api/export.py`가 검증된 값만 Excel에 쓴다. 정확도는 별도의 `evaluation/` 하베스가 정답 키와 대조해 측정하는데, 이 정답은 추출 런타임이 절대 import하지 못하게 격리돼 있다.

핵심 명제 세 개가 이 흐름 전체에 박혀 있다. **(1) 흐름의 순서는 코드가 정한다 — agent가 아니다. (2) LLM은 읽기만, 코드가 검증한다. (3) 사람이 승인하기 전엔 아무 값도 클라이언트 시트에 들어가지 않는다.**

---

## 1. 사용자가 보는 것 — 웹 프런트엔드 (`app/page.tsx`)

화면은 두 단계다. **업로드 화면**과 **리뷰 화면.**

업로드 화면에서 사용자는 두 모드 중 하나를 고른다. **Samples**는 번들된 Tesla·Citi PDF를 `recorded` 모드로 돌린다 — API 키가 필요 없고 저장된 모델 응답을 재생한다. **Upload**은 자기 PDF를 드래그앤드롭하는 `live` 모드다. 여러 개를 한 번에 넣을 수 있고, 같은 파일은 이름+크기로 중복 제거된다. 호스팅된 업로드 경로는 4MB로 제한돼 있다(`MAX_HOSTED_UPLOAD_BYTES`).

사용자가 "Extract"를 누르면 `extractAll()`이 문서를 **하나씩** 순회한다. 각 문서마다 `/api/extract`로 POST를 보내는데, live면 `{mode, filename, fileBase64}`, 샘플이면 `{mode:"recorded", demoDocument:"tesla"|"citi"}`를 보낸다. 이 루프의 핵심 성질 하나: **한 파일이 실패해도 배치 전체가 죽지 않는다.** 실패는 모아서 건너뛰고, 성공한 것만으로 리뷰를 진행한다. 20개를 올렸는데 3개가 깨져도 17개는 정상적으로 검토 화면으로 넘어간다는 뜻이다.

서버가 추출 결과를 돌려주면 화면은 리뷰 화면으로 바뀐다. 왼쪽엔 9개 메트릭이 행으로 나열되고, 각 행은 편집 가능한 값 + (필요시) "check" 또는 "optional" 플래그를 단다. 오른쪽엔 **실제 PDF가 렌더링되고, 그 값이 나온 인용문 위치가 하이라이트**된다(`PdfViewer`가 pdf.js로 해당 페이지를 그리고 `metric.evidence.rects`로 박스를 친다). 사용자는 숫자가 인용문과 맞는지 눈으로 바로 대조하고, 틀렸으면 그 자리에서 고친다. 이 "값 옆에 근거를 붙여 사람이 확인" 구조가 제품의 review-first 철학이다.

마지막으로 "Export Excel"을 누르면 `exportWorkbook()`이 `/api/export`로 POST한다. 이때 **결정(decisions)은 값의 존재 여부로 자동 도출**된다 — 값이 있으면 `approved`, 비어 있으면 `not_applicable`. 서버가 base64 xlsx를 돌려주면 브라우저가 그걸 디코딩해 파일로 다운로드시킨다.

> 정리하면 웹 흐름은 **두 번의 서버 호출**이다: 추출(`/api/extract`) → 사용자 검토·수정 → 내보내기(`/api/export`). (`api/process.py`라는 올인원 엔드포인트도 레포에 있지만, 현재 프런트엔드는 이 둘로 쪼개 호출한다.)

---

## 2. 업로드가 서버로 들어오는 길 (`api/extract.py`)

`/api/extract`는 `BaseHTTPRequestHandler`다. `do_POST` → `_read_request`가 요청 본문을 읽고, `multipart/form-data`면 `_read_multipart`로, 아니면 JSON으로 파싱한다. 프런트엔드는 JSON(`fileBase64`)으로 보내므로 후자다.

`_process()`가 본체다. 먼저 `mode`를 확인하고(`live`/`recorded`만 허용), `os.chdir(ROOT)`로 작업 디렉터리를 레포 루트에 고정한다(템플릿·에셋을 상대경로로 읽기 때문). 그리고 `TemporaryDirectory` 안에서:

1. `_write_input_pdf()`가 업로드 바이트를 실제 `.pdf` 파일로 쓴다. 파일명은 `_safe_filename`으로 정제하고, base64면 디코딩한다. recorded 모드면 대신 번들된 골든 PDF(`assesment_info/`)를 복사한다.
2. **`extract(pdf_path, run_dir, mode)`** 를 호출한다 — 여기서부터는 CLI가 로컬 파일을 처리하는 것과 **완전히 동일한 경로**다.
3. 돌아온 `draft_metrics.json`을 다시 읽어 `DraftRun` 객체로 만들고, `template_metric_payloads()`로 프런트엔드용 메트릭 JSON으로 변환해 응답한다.

핵심: **웹 레이어는 얇은 어댑터다.** 파일을 풀고, 임시로 저장하고, 같은 엔진에 넘기고, 결과를 포장할 뿐이다. 모든 로직은 하나의 파이프라인에 있어서 CLI와 웹이 서로 어긋날 수가 없다.

---

## 3. 엔진의 바깥 루프 (`pipeline.py · extract`)

`extract()`는 모드를 검증하고, live면 OpenAI 설정을 로드하고, `find_pdf_inputs()`로 PDF 경로 리스트(웹에선 1개)를 만든다. 그리고 각 PDF에 대해 **`process_single_pdf()`** 를 부른다. 결과들을 모아 `build_draft_run()`으로 `DraftRun` 하나를 만들고 `draft_metrics.json`에 직렬화해 그 경로를 돌려준다.

`DraftRun`은 이 시점의 모든 것을 담는다 — 정규화된 메트릭 행, 문서 메타데이터, 분류 결과(감사용), 선택된 페이지 번호, 토큰 사용량. 이건 **리뷰의 입력**이지 최종 Excel이 아니다.

---

## 4. 심장 — 문서 하나의 고정 시퀀스 (`process_single_pdf`)

여기가 전부다. 14단계가 **고정된 순서**로 돈다. 순서가 중요한 이유는, 단계들이 새 리스트를 만드는 게 아니라 **같은 `list[MetricRow]`를 제자리(in-place)에서 계속 수정**하기 때문이다. 즉 normalize가 고친 값을 그 다음 validate가 그대로 본다.

### 1단계 — Ingest (`ingest.py`)
`read_pdf_pages(pdf_path)`가 pdfplumber로 **모든** 페이지를 읽어 `list[PageText]`(페이지번호 1-base, 텍스트, 글자수)로 만든다. `read_pdf_metadata()`는 PDF 메타데이터를 딕셔너리로 뽑는다. 이때부터 모든 게 raw 파일이 아니라 텍스트 위에서 돈다.

### 2단계 — Classify & Select (`classify.py`)
`classify_document(pages)`가 문서를 report / earnings-call transcript / unknown으로 분류한다 — 키워드와 구조(화자 턴, 표 마커)로. **그런데 바로 다음 줄에서 `document_type`을 무조건 `"earnings_report"`로 덮어쓴다.** 분류 결과는 감사 메타데이터와, 과제 안내문 같은 비-소스 PDF를 거르는 가드로만 쓰이고, 추출 동작 자체는 바꾸지 않는다. 제품 목표가 "문서 타입과 무관하게 같은 9필드 추출"이라 일부러 동작에서 분리한 것이다.

이어 `select_extraction_pages(pages, max_pages=6)`가 **모델에 줄 페이지를 최대 6장 고른다.** 항상 1페이지(표지=회사명)를 넣고, 나머지는 `_page_relevance`(숫자 밀도 + 재무 키워드 히트×2) 점수가 높은 순으로 채운다. "초반 6장"이 아니라 "관련성 높은 6장"이다 — 실제로 Tesla 정답 데이터를 보면 4·28·29페이지처럼 멀리 떨어진 페이지를 골라낸다. 이게 전체 filing을 모델에 안 먹이는 비용·집중 장치다.

> **왜 6장으로 충분한가:** 필드 9개가 9페이지에 흩어진 게 아니다. earnings의 손익계산서는 revenue·net income·EPS·operating income·operating expenses·gross margin을 **한 표 안에 나란히** 찍는다. 저장된 정답을 보면 Tesla는 6개 필드가 전부 4페이지에서, Citi는 5개가 3페이지에서 나왔다. 9필드를 다 채우는 데 보통 페이지 2~3장이면 되고, 6장은 여유 버퍼다.

### 3단계 — LLM 추출, 유일한 모델 읽기 (`extractor.py` / `recorded.py`)
`_extract_document_metrics()`가 분기한다. live면 `extract_metrics_live_with_usage()`가 OpenAI `responses.parse`를 호출하는데, `text_format=MetricsBatch`로 **구조화 출력**을 강제한다. 즉 모델은 자유 서술이 아니라 정해진 스키마의 칸을 채운다. 고른 6페이지 텍스트를 `--- PAGE n ---` 블록으로 이어 붙이고(`_build_user_prompt`), "현재 분기 값만 가져와라 / 숫자는 쉼표까지 그대로 복사하고 변환하지 마라(계산은 코드가 한다) / 없으면 null + needs_review" 같은 규칙을 준다. 반환은 각 메트릭마다 `{value, source_page, source_quote, confidence}`. recorded면 `extract_metrics_recorded()`가 `recorded_responses/`의 저장된 JSON을 재생한다 — API 키 없이 바이트 단위로 재현 가능하게.

`repair_metric_batch()`가 모델이 뱉은 JSON을 스키마로 검증·복구한다. 망가진 출력은 다운스트림으로 못 흐른다. 결과는 `list[MetricRow]` — 이후 모든 단계가 손대는 그 리스트다.

### 4~11단계 — 결정론적 보정과 검증 (모델 안 읽음)
여기서부터 신뢰성을 만든다. 전부 코드다.

- **`complete_template_rows`** (validation.py): 모델이 안 채운 필수 템플릿 필드를 빈 행 + review 플래그로 만든다. 절대 추측해 채우지 않는다.
- **`resolve_company_identity` + `apply_company_identity`** (identity.py): 회사명·티커를 텍스트·메타데이터에서 결정론적으로 채운다. 모델이 자주 비워두는 부분.
- **`enrich_capital_return_text`** (validation.py) + **`_apply_capital_return_narrative`** (pipeline.py): 자사주/배당 결합 필드를 만든다. 결정론적으로 먼저 시도하고, 셀이 약할 때만(live) 모델이 인용 문장을 한 문장으로 정리한다 — 숫자는 소스와 대조하고, 행은 review 상태로 남는다.
- **`repair_table_scale` + `normalize_metrics`** (normalize.py): 모든 값을 한 형태로 — **USD 백만, 마진은 퍼센트포인트, EPS는 평문 숫자**. Citi의 "$21.6 billion"을 올바른 백만 단위로 바꾸는 곳이다. 이 1000× 오류는 모델을 믿으면 안 되는 대표 사례고, 결정론적 레이어가 raw 15/18 → 18/18로 끌어올리는 가장 큰 이유다.
- **`apply_line_item_selection`** (line_item_selector.py, *live만*): 모델이 의미상 옆 라인(예: total revenue 대신 revenue 소계)을 집었으면, 인용된 페이지에서 정의에 맞는 라인을 다시 고른다. 별도 모델 호출이고, 값이 근거를 벗어나지 않게 가둔다.
- **`repair_source_pages`** (validation.py, *live만*): live 추출이 페이지를 잘못 인용했으면, 인용문이 실제로 있는 페이지로 스냅한다.
- **`validate_metrics`** (validation.py): 신뢰성 게이트. 인용문이 그 페이지에 실제로 있는지, 숫자가 인용문 안에 나오는지, 일관성이 맞는지(영업현금흐름 − capex = 자유현금흐름), 크기가 말이 되는지(순이익 ≤ 매출). 어긋나면 **`needs_review = true` + 평문 이유**를 단다. **값은 절대 안 고친다 — 사람에게 넘길 뿐.**
- **`verify_template_metrics`** (verifier.py, *live만*): 모델이 각 값과 인용문을 다시 대조해, 결정론 규칙이 못 잡는 의미 오류(잘못된 라인, 연간 vs 분기)를 **플래그만** 단다. 값은 안 바꾼다. recorded에선 건너뛰어 재현성을 지킨다.

### 12단계 — 조립
`build_draft_run`이 위 결과를 `DraftRun`으로 묶어 `draft_metrics.json`에 쓴다. 정규화된 값, 근거, 검증 플래그, 토큰 사용량이 다 들어간다.

> **live 모드 호출 수:** 추출(3) + 라인아이템(8) + 검증자(11) + 가끔 capital-return(6) = PDF당 **약 3~4번** 모델 호출. 나머지는 전부 결정론적. recorded 모드는 추출 한 번(재생)뿐이라 데모가 완전히 재현 가능하다.

---

## 5. 사람의 게이트와 내보내기 (`review.py`, `export.py`)

웹에선 4단계 리뷰 화면이 곧 사람 게이트다. 사용자가 본 값·페이지·인용문·플래그 이유를 바탕으로 승인/수정한다. (CLI에는 같은 일을 하는 `review.py`가 있어 `review.html`·큐·근거 리포트를 만들고 approve/reject 결정을 기록한다.)

내보내기는 `/api/export` → `export_reviewed_run` (export.py)이다. 워크북을 쓰는데 **첫 시트는 클라이언트 템플릿**이고, 이어 Metrics / Review / Evidence 탭이 붙는다. 각 셀은 그들 양식에 맞게 포맷한다($\<n\>B 문자열, 마진은 소수). 결정적 규칙: **승인된 값만 클라이언트 시트에 들어가고, 근거 없는/미승인 셀은 빈칸으로 남는다.** 그래서 틀린 값이 조용히 박히는 일이 구조적으로 막힌다.

---

## 6. 정확도는 어떻게 측정되나 (`evaluation/`)

이게 "eval-first"의 실체다. 추출이 잘됐다고 **주장**하는 게 아니라 **측정**한다.

`cli.py eval` → `_eval_bridge.run_eval` → `evaluation/runner.py` → `score_draft`(scoring.py)가 `draft_metrics.json`을 정답과 대조한다. 정답은 `evaluation/golden_metrics.py`의 `PRIMARY_FIELDS`에 산다 — Tesla·Citi 각 9필드의 기대값, 단위, 기대 소스 페이지·인용문까지. 예를 들어 Tesla Total revenue = 22,496(USD 백만, p4), Citi 자사주 = "$2.8B capital returned, including $1.75B buybacks"(p2).

**채점 규칙** (`score_field`):
- 숫자 필드는 타입별 **허용오차** 안이면 통과 — 통화는 max(1.0, 0.5%), EPS는 0.01, 퍼센트포인트는 0.1(tolerances.py). 그래서 반올림·표기 차이는 봐주되 진짜 오류는 잡는다.
- 텍스트 필드는 공백·대소문자 정규화 후 일치.
- **`expected_blank_review`** 필드(예: 은행 Citi의 gross margin, Tesla의 자사주)는 값이 비어 있고 + `needs_review` + 이유가 있을 때만 통과한다. 즉 "없는 값을 안 채우고, 사람에게 올바르게 넘겼는가"도 점수에 든다. 그냥 비워두는 것과 "근거 있게 비워 플래그한 것"을 구분한다.

`accuracy = passed / total`. 9필드 × 2문서 = 18이 만점이고, 결과는 Tesla 9/9, Citi 9/9 → **18/18**. 결정론 레이어 없이 모델 raw만으로는 15/18이었던 걸 normalize·validation이 18/18로 끌어올렸다 — 이게 "LLM 읽기 / 코드 검증"의 정량 증거다.

**부정행위 방지가 설계의 핵심.** 정답값은 `evaluation/` 패키지 밖으로 절대 안 나가고, 채점 코드에서만 import된다. `tests/test_no_cheat_imports.py`가 `earnings_extractor`(런타임)가 `evaluation`을 import하면 **테스트를 깨뜨린다.** 그래서 정확도 숫자는 정답이 추출 코드로 새어 들어가 만든 게 아니라, 모델 + 결정론 파이프라인이 **실제로 만든** 출력을 측정한다는 게 보장된다. recorded 모드로 돌리니 이 측정은 매번 동일하게 재현된다.

---

## 7. 전체 흐름 한 장 요약

```
[브라우저] app/page.tsx
   업로드(Samples=recorded / Upload=live) → 문서별로
        │  POST /api/extract  {mode, filename, fileBase64}
        ▼
[서버] api/extract.py · _read_multipart → _write_input_pdf
        │  extract(pdf_path, run_dir, mode)
        ▼
[엔진] pipeline.py · process_single_pdf  (고정 14단계)
   1 ingest(전체페이지)  ─ ingest.py
   2 classify(감사용) + select(6페이지)  ─ classify.py
   3 LLM 추출 1회  ─ extractor.py / recorded.py   ← 유일한 모델 읽기
   4 complete  5 identity  6 capital-return
   7 normalize(1000× 보정)  ─ normalize.py
   8 line-item(live)  9 repair pages(live)
   10 validate(needs_review)  ─ validation.py
   11 verifier(live, 플래그만)  12 build_draft_run → draft_metrics.json
        │
        ▼
[브라우저] 리뷰 화면: 값 + 하이라이트된 인용문, 사용자가 확인·수정
        │  POST /api/export  {draft, decisions(approved/not_applicable)}
        ▼
[서버] api/export.py · export_reviewed_run  ─ export.py
        승인된 값만 클라이언트 시트에 → base64 xlsx → 브라우저 다운로드

[평가, 별도] cli eval → evaluation/runner → scoring vs golden_metrics
        허용오차 채점 · 부정행위 import 금지 · Tesla 9/9 · Citi 9/9 · 15/18→18/18
```

---

## 8. 면접에서 이 흐름으로 방어할 세 가지

1. **agent가 아니다.** 순서는 `process_single_pdf`의 코드가 고정한다. 모델은 3단계 한 칸을 채울 뿐 다음 행동을 못 정한다.
2. **LLM 읽기 / 코드 검증.** 모델은 6페이지에서 숫자+인용문을 읽고, normalize가 단위를, validate가 일관성·크기를 잡는다. 15/18 → 18/18이 그 증거.
3. **승인 전엔 아무것도 안 나간다.** validate는 값을 안 고치고 플래그만 달고, export는 승인된 값만 시트에 쓴다. 사람이 인용문을 보고 확인한 값만 클라이언트에 도달한다.

그리고 솔직하게 먼저 꺼낼 한계: **6페이지 캡**은 고신호 페이지가 6장 넘는 비표준 문서에서 필드를 놓칠 수 있다 — 다만 그땐 틀린 값이 아니라 플래그된 빈칸으로 드러난다. 스케일에선 캡 상향이나 "필수 필드가 비면 페이지 넓혀 재추출"하는 2-pass가 다음 스텝이다.
