# 문서 저장·버전·Diff 설계 — 제안

> 작성일: 2026-09-18
> 상태: **제안이다. 확정된 사항이 아니다.** 검토 뒤 채택 여부를 정하고, 채택하면 `CORE_TABLE_ERD.md`로 옮긴다.
> 목적: 문서를 어떤 형태로 저장할지, 어떤 테이블이 필요한지, 저장과 그래프 최신화가 각각 어떤 순서로 도는지를 정한다.
> 전제: [`CORE_FEATURE_REQUIREMENTS.md`](CORE_FEATURE_REQUIREMENTS.md) §5·§6.2·§8.2, [`LORE_SENTRY_PROJECT_CONTEXT.md`](LORE_SENTRY_PROJECT_CONTEXT.md) §3.4·§3.5, [`GRAPH_INBOX_PATTERN.md`](GRAPH_INBOX_PATTERN.md), [`IMAGE_UPLOAD_S3.md`](IMAGE_UPLOAD_S3.md) §4.2

## 0. 확정이 아닌 이유

- 테이블명·컬럼은 `GRAPH_INBOX_PATTERN.md`가 이미 쓰고 있는 이름(`files`, `document_relations`, `file_versions`, `outbox_events`)에 맞춰 두었으나, `CORE_TABLE_ERD.md`가 현재 저장소에 없다. ERD가 확정되면 그쪽이 기준이다.
- §8의 미결 사항이 정해지기 전에는 일부 컬럼이 바뀔 수 있다.
- 편집기 라이브러리는 프론트엔드에서 아직 선택 전이다 (`package.json`에 에디터 의존성 없음).

---

## 1. 한 줄 요약

**본문은 Markdown 문자열 하나로 저장하고, 저장은 문서 전체를 덮어쓴다.** 블록 단위로 쪼개지 않는다. 버전은 그 문자열을 통째로 복사해 두고, 그래프 최신화의 제안은 문서를 건드리지 않는 별도 테이블에 쌓였다가 확정될 때 한 번에 반영된다.

```text
documents.body_md  ←── 원본. 검색·diff·AI·내보내기·버전이 전부 이 문자열 하나를 본다
```

---

## 2. 문서 저장

### 2.1 저장 형태

문서 하나가 `documents` 한 행이고, 본문 전체가 컬럼 하나에 들어간다.

```text
documents
┌──────────┬──────────────────────────────────────┐
│ file_id  │ body_md                              │
├──────────┼──────────────────────────────────────┤
│ 카이론    │ "# 카이론\n\n남부 출신이다.\n\n         │
│          │  나이는 스물셋이다."                    │
└──────────┴──────────────────────────────────────┘
```

저장은 UPDATE 한 번이다.

```sql
UPDATE documents SET body_md = ?, body_sha = ?, char_count = ?
 WHERE file_id = ?;
UPDATE files SET revision_no = revision_no + 1
 WHERE id = ? AND revision_no = ?;   -- 0행이면 409
```

`revision_no`는 저장할 때마다 1씩 오르는 숫자다. `WHERE revision_no = ?`가 붙어 있어서, 다른 탭이 먼저 저장해 이미 값이 올라갔으면 이 UPDATE가 0행을 고친다. 그때 서버가 409를 돌려준다. 이 조건이 없으면 나중에 저장한 쪽이 앞 내용을 조용히 지운다.

`revision_no`를 `documents`가 아니라 `files`에 둔 이유는 `GRAPH_INBOX_PATTERN.md` §4.3의 revision 가드가 파일 단위이기 때문이다. 제목·본문·속성·관계가 모두 이 하나의 번호를 공유한다.

### 2.2 왜 Markdown 문자열인가

| 후보 | 문제 |
|---|---|
| 블록 테이블 (Notion식) | 회차 하나에 문단 수백 개. 문서를 열 때마다 수백 행 JOIN·정렬. 버전 하나 뜰 때마다 수백 행 복사. 소설 본문에는 블록 ID가 필요 없다 |
| 에디터 JSON (ProseMirror doc) | 저장 포맷이 에디터 구현에 묶인다. 검색·AI·내보내기·diff가 전부 변환을 거치고, 변환이 손실적이면 경로마다 결과가 달라진다 |
| **Markdown 문자열** | 에디터 스키마를 Markdown이 표현 가능한 범위로 제한해야 한다 |

Markdown을 고르면 얻는 것이 크다.

- 검색은 `body_md` 컬럼을 그대로 인덱싱한다.
- diff는 텍스트 비교다. 트리 비교 알고리즘이 필요 없다.
- 409 이후 3-way 병합이 가능하다. `base`·`current`·`mine` 세 문자열에 표준 텍스트 병합이 그대로 먹는다.
- AI에 변환 없이 넘긴다.
- 버전은 문자열 복사다.

### 2.3 서식 처리 — 무엇을 문서에 넣고 무엇을 빼는가

`CORE_FEATURE_REQUIREMENTS.md` §5.1은 글꼴·크기·줄 간격을 편집 기능으로 요구한다. 이것을 본문 문자열에 넣으면 Markdown으로 표현이 안 된다.

**UI에는 그대로 두되, 값은 문서가 아니라 사용자 설정에 저장한다.**

| | 저장 위치 |
|---|---|
| 제목·문단·목록·인용·코드·굵게·기울임·취소선·링크 | `documents.body_md` |
| 글꼴·글자 크기·줄 간격·첫 줄 들여쓰기·문단 간격 | `user_editor_prefs` |

작품 전체에 일관되게 적용되는 값이지 문단마다 다를 값이 아니므로, 사용자·프로젝트 단위 설정이 오히려 맞다. 화면 렌더링과 PDF/DOCX 내보내기에 적용한다.

### 2.4 이미지

Markdown 문법은 URL만 담는다. 업로드는 이미 구축된 [`IMAGE_UPLOAD_S3.md`](IMAGE_UPLOAD_S3.md) 경로를 그대로 쓴다.

```text
① 붙여넣기/드래그
② POST /projects/{projectId}/images        → 티켓 발급, image 행 PENDING
③ PUT uploadUrl (브라우저 → S3 직접)
④ POST /projects/{projectId}/images/{id}/complete  → COMMITTED
⑤ 응답의 publicUrl을 본문에 삽입
     ![표지](https://media.loresentry.com/projects/{pid}/images/{uuid}.png)
```

업로드는 **문서 저장과 별개 요청**이다. 업로드 중에는 로컬 blob URL로 placeholder를 보여 주고 성공하면 교체한다. 실패해도 문서 저장은 정상적으로 돈다.

본문에는 URL만 들어가므로 이미지 추가·삭제가 텍스트 diff에서 한 줄 추가·삭제로 자연스럽게 잡힌다.

> **주의.** 사용자가 본문에서 이미지를 지워도 S3 객체를 지우면 안 된다. 과거 `file_versions`가 그 URL을 여전히 참조한다. 이미지는 프로젝트 영구 삭제 때만 함께 정리한다.

---

## 3. 편집기

### 3.1 무엇을 쓰는가

**ProseMirror 계열 WYSIWYG 편집기를 쓴다.** 후보는 Tiptap 또는 Milkdown이고, 현재는 Tiptap을 권한다.

요구사항 §5.1이 "제목과 **서식 있는 본문**", "문자 서식, 글머리·번호 목록"을 요구하므로 소스 편집기(CodeMirror에 `**굵게**`를 그대로 노출)는 맞지 않는다. 그리고 WYSIWYG Markdown 편집기는 사실상 전부 ProseMirror 위에 있다. "Markdown 전용 편집기를 쓴다"와 "ProseMirror를 쓴다"는 배타적이지 않다.

| 후보 | 성격 | 판단 |
|---|---|---|
| CodeMirror 6 | Markdown 원문 편집 | 저장은 가장 단순하나 "서식 있는 본문" 요구와 맞지 않음. **원문 보기 토글**로는 유용 |
| Milkdown | ProseMirror 기반. 문서 모델이 Markdown AST | 변환 배선이 덜 필요. 생태계가 얇음 |
| **Tiptap** | ProseMirror 래퍼 | 커스텀 노드·찾기바꾸기·글자수 확장이 필요하고 레퍼런스가 많다. 한국어 IME가 가장 오래 검증된 엔진이 ProseMirror |

### 3.2 스키마를 Markdown 크기로 잠근다

에디터가 만들 수 있는 요소를 Markdown으로 표현 가능한 것만으로 제한한다. 이것이 Markdown 저장이 성립하는 조건이다.

```text
블록    heading(1-6) · paragraph · blockquote · codeBlock · horizontalRule
        bulletList · orderedList · listItem · taskList · image
인라인   bold · italic · strike · code · link · hardBreak

제외    underline · fontSize · fontFamily · lineHeight · textAlign · color
        → §2.3의 user_editor_prefs로

확장 예약  fileRef — 본문 내 파일 참조. §8의 미결 사항 참조
```

변환기는 `prosemirror-markdown`의 `MarkdownParser`/`MarkdownSerializer`를 **같은 스키마에서 쌍으로** 정의한다. 파서와 직렬화기를 따로 만들면 한쪽에 노드를 추가했을 때 다른 쪽이 조용히 그것을 버린다.

**CI에 왕복 테스트를 넣는다.** 코퍼스의 각 문서에 대해 `serialize(parse(md)) === md`가 성립해야 한다. 한국어 문장, 중첩 목록, 코드블록 안의 백틱, 빈 줄 개수, 전각 문자를 케이스에 포함한다. 이 테스트가 통과하는 한 저장 포맷이 에디터를 배신하지 않는다.

---

## 4. 테이블

### 4.1 문서 본체

```text
projects
 └ files ─────────────── 파일과 폴더 (트리, 제목, 유형, 순서, 잠금, revision_no)
     ├ documents ─────── 본문 (body_md)
     ├ document_properties ─ 텍스트 속성
     └ document_relations ── 관계 속성. 그래프 투영의 원천
```

| 테이블 | 들어가는 것 | 쓰이는 곳 |
|---|---|---|
| `files` | 제목, `file_type`, `parent_id`, `rank`, `locked`, `revision_no`, `trashed_at` | 사이드바 트리, 이름 중복 검사, 휴지통, 동시성 토큰 |
| `documents` | `body_md`, `body_sha`, `char_count` | 편집기가 읽고 쓰는 곳. 검색 대상 |
| `document_properties` | `property_key`, `text_value`, `position` | 설정 문서 속성 표의 텍스트 항목 |
| `document_relations` | `relation_key`, `target_file_id`, `position` | 관계 칩. **그래프·타임라인의 원천** |

`document_relations`가 그래프의 유일한 원천이라는 것은 `GRAPH_INBOX_PATTERN.md` §3과 같다. 별도 엣지 테이블을 두지 않는다. 복제하면 둘을 동기화하는 코드가 생기고 그 코드가 언젠가 틀린다.

`files.rank`를 정수가 아니라 문자열 fractional index로 두는 이유는 드래그 이동 때문이다. 정수면 형제 사이에 끼울 때 뒤쪽 전부를 다시 번호 매겨야 한다.

### 4.2 버전

```text
file_versions ── 과거 시점의 제목 + 본문 + 속성 + 관계를 통째로 복사해 둔 것
```

| 컬럼 | 역할 |
|---|---|
| `title`, `body_md`, `properties jsonb`, `relations jsonb` | 그 시점의 전체 스냅샷 |
| `kind` | `AUTO` / `NAMED` / `PRE_RESTORE` / `RESTORE` / `AI_APPLY` |
| `label` | `NAMED`일 때 사용자가 붙인 이름 |
| `source_revision_no` | 어느 revision을 찍은 것인지 |
| `expires_at` | `AUTO`만 `+30일`. 나머지는 `NULL`(영구) |

한 행이 곧 "그때의 문서 전부"다. 복원은 이 행을 읽어 `documents`에 덮어쓰면 끝이고, 비교는 두 행을 읽어 문자열 비교하면 끝이다.

**델타 체인(직전 버전과의 차이만 저장)은 쓰지 않는다.** 회차 하나 15KB가 PG18 lz4로 3~4KB가 되고, 하루 4시간 집필 × 30일이면 프로젝트당 약 6MB다. 그걸 아끼려고 "중간 한 행이 깨지면 그 뒤로 전부 복구 불가"를 감수할 이유가 없다.

보관 정책은 컬럼 하나로 표현한다. 정리 배치가 `DELETE FROM file_versions WHERE expires_at < now()` 한 줄이 된다. 종류별 분기가 코드가 아니라 데이터에 있다.

속성·관계를 정규화하지 않고 `jsonb`로 찍는 이유는 버전이 **읽기 전용 과거**이기 때문이다. 과거 버전의 속성을 쿼리로 검색할 일이 없고, 통째로 꺼내 비교 화면에 뿌리기만 한다.

### 4.3 그래프 최신화

3층이다.

```text
refresh_runs ─────────── "최신화를 한 번 돌린 것"        (실행 단위)
 └ refresh_proposals ─── "이 문서를 이렇게 고치자"        (문서 단위)
     └ refresh_changes ─ "이 부분을 이렇게 고치자"        (변경 단위)
```

| 테이블 | 한 행의 의미 | 핵심 컬럼 |
|---|---|---|
| `refresh_runs` | 사용자가 [그래프 최신화]를 누른 1회 | `status` — `RUNNING`/`READY`/`APPLIED`/`CANCELLED`/`FAILED` |
| `refresh_proposals` | 고칠 설정 문서 1개 | `target_file_id`, **`base_revision_no`**, `status` |
| `refresh_changes` | 그 문서 안의 변경 1개 | **`decision`**, `anchor_old`, `anchor_new`, `edited_value` |

**이 테이블들에만 제안이 쌓이고, 확정 전까지 `documents`는 전혀 바뀌지 않는다.** 요구사항 §8.2의 *"확정 전에는 실제 설정 문서를 바꾸지 않는다"*가 이 구조로 보장된다.

중복 실행 방지는 DB 제약으로 건다.

```sql
CREATE UNIQUE INDEX refresh_one_running
  ON refresh_runs(project_id) WHERE status = 'RUNNING';
```

`base_revision_no`는 "제안을 만들 때 그 문서가 몇 번이었나"를 기억한다. 확정 시점에 문서가 그 사이 바뀌었으면 여기서 잡아낸다.

### 4.4 나머지

| 테이블 | 용도 |
|---|---|
| `sections`, `favorites` | 사용자 생성 섹션, 즐겨찾기(원본 복제가 아닌 바로가기) |
| `memos` | `scope` = `PROJECT`/`FILE`. §8의 미결 사항 참조 |
| `images` | [`IMAGE_UPLOAD_S3.md`](IMAGE_UPLOAD_S3.md) §4.2의 초안을 그대로 |
| `workspace_states` | 탭·활성 파일·스크롤·패널 배치·그래프 카메라를 `jsonb` 한 덩이로. 관계 쿼리 대상이 아니고 쓰기가 잦다 |
| `user_editor_prefs` | 글꼴·크기·줄 간격. **문서 데이터가 아니다** (§2.3) |
| `outbox_events` | Kafka 발행 대기. 문서 저장과 같은 트랜잭션 (`GRAPH_INBOX_PATTERN.md` §4.1) |
| `save_idempotency` | 최근 저장 응답 캐시. 재시도가 409로 오인되는 것을 막는다 |

---

## 5. 저장 플로우

사용자가 본문에 한 글자를 친다.

```text
① 타이핑
     화면만 바뀐다. 네트워크를 쓰지 않는다.

② 0.8초 무입력  또는  첫 입력으로부터 5초 경과
     둘 중 먼저 오는 쪽에 저장을 시작한다.

③ PATCH /projects/{pid}/documents/{fileId}
     If-Match: "17"
     X-Save-Id: 9f2c…
     { "bodyMd": "# 카이론\n\n남부 출신이다.\n\n나이는 스물셋이다." }

④ 서버 — 트랜잭션 하나
     ├ UPDATE files SET revision_no=18 WHERE id=? AND revision_no=17
     │     0행이면 → 409 반환하고 종료
     ├ UPDATE documents SET body_md=…, body_sha=…, char_count=…
     ├ 직전 AUTO 버전이 5분 이전인가?
     │     예   → INSERT file_versions (kind='AUTO', expires_at=now()+30d)
     │     아니오 → 건너뛴다
     └ 관계가 바뀌었는가? → INSERT outbox_events
     COMMIT

⑤ 200 OK, ETag: "18"
     클라이언트가 revision을 18로 갱신하고 IndexedDB 백업을 지운다.
```

### 5.1 maxWait 5초가 필요한 이유

debounce만 두면 계속 타이핑하는 동안 타이머가 매번 리셋되어 저장이 무한정 밀린다. Pensiv 실측에서 400ms 간격으로 치자 **8.2초 동안 저장 요청이 한 번도 나가지 않았다.** 빠르게 쓰는 사람일수록 유실 구간이 길어지는, 방향이 거꾸로 된 구조다. 상한을 두면 최악의 유실이 5초로 고정된다.

### 5.2 409를 받으면

서버가 현재 본문과 공통 조상을 함께 돌려준다.

```json
{ "currentRevisionNo": 19,
  "current": { "title": "…", "bodyMd": "…" },
  "base":    { "revisionNo": 17, "bodyMd": "…" } }
```

서로 다른 문단을 고쳤으면 자동으로 병합하고, 같은 문단이면 사용자에게 고르게 한다. 둘 다 Markdown 문자열이라 텍스트 병합이 그대로 먹는다.

### 5.3 멱등키

저장 요청이 타임아웃돼도 서버에는 반영됐을 수 있다. 그대로 재시도하면 `revision_no`가 이미 올라가 있어 409가 나고, 사용자에게는 자기 자신과 충돌했다는 이상한 화면이 뜬다. `X-Save-Id`를 최근 N건 기억해 두고 같은 키가 오면 같은 응답을 다시 돌려준다.

### 5.4 저장과 버전은 다른 일이다

```text
저장  →  documents·files 를 덮어쓴다        (1초 단위, 조용히)
버전  →  file_versions 에 한 줄 추가         (5분 간격, 또는 사용자가 누를 때)
```

수동 "버전 저장"은 본문을 쓰지 않는다. 본문은 이미 자동 저장돼 있으므로 스냅샷만 하나 찍는다.

### 5.5 복원

요구사항 §3.5는 *"이전 버전 복원은 기존 파일을 덮어쓰는 것이 아니라 새 변경으로 저장한다"*이다. 세 단계를 한 트랜잭션으로 처리한다.

```text
BEGIN
  INSERT file_versions (kind='PRE_RESTORE')   -- 지금 상태를 먼저 남긴다
  UPDATE documents / files                    -- 선택 버전 내용으로. revision_no+1
  INSERT file_versions (kind='RESTORE')
COMMIT
```

복원 요청에도 `If-Match`를 요구한다. 복원이야말로 다른 기기의 최신 편집을 통째로 날릴 수 있는 지점이다.

### 5.6 이벤트 발행 주기

저장은 초 단위로 일어난다. 매 저장마다 `outbox_events`에 넣으면 Kafka와 graph-rag가 의미 없는 중간 상태로 폭주한다.

**발행 조건을 좁힌다 — 버전이 생성됐을 때, 또는 `document_relations`가 바뀌었을 때.** 그래프가 필요로 하는 것은 문자 단위 변경이 아니라 관계 변경이다.

---

## 6. 그래프 최신화 — diff 계산과 결정 플로우

### 6.1 버튼을 누른다

```sql
INSERT INTO refresh_runs (project_id, status) VALUES (?, 'RUNNING');
-- UNIQUE(project_id) WHERE status='RUNNING' 때문에 이미 돌고 있으면 DB가 거부
```

요구사항 §8.2의 *"추출 중에는 같은 요청을 중복 실행할 수 없다"*가 제약으로 보장된다.

### 6.2 AI가 제안을 만든다

AI에 넘기는 것은 **`body_md` 그대로**다. 변환 계층이 없다.

AI가 돌려준 결과를 테이블에 쌓는다.

```text
refresh_proposals
  target_file_id    = 카이론
  base_revision_no  = 18          ← 지금 문서가 18번이다
  status            = PENDING

refresh_changes                                                      decision
  ① BODY       "나이는 스물셋이다." → "나이는 스물넷이다."             UNDECIDED
  ② BODY       (없음)              → "여동생 리아가 있다."            UNDECIDED
  ③ RELATION   관련 이벤트 [] → [도깨비 학살]                        UNDECIDED

refresh_runs.status = 'READY'
```

여기까지 `documents`는 전혀 건드리지 않았다.

### 6.3 용어 — hunk

**hunk는 "바뀐 덩어리 하나"다.** 두 텍스트를 비교했을 때 서로 다른 부분 한 조각을 가리키는 diff 용어다.

```text
현재 문서                    AI 제안
─────────────────           ─────────────────
남부 출신이다.                남부 출신이다.        ← 같음. hunk 아님
나이는 스물셋이다.            나이는 스물넷이다.     ← hunk ①
검술에 능하다.                검술에 능하다.        ← 같음
가족은 없다.                  가족은 없다.
                            여동생 리아가 있다.    ← hunk ②
```

`git add -p`가 "이 덩어리를 스테이징할까요?"라고 하나씩 물을 때의 그 덩어리다. **`refresh_changes`의 한 행이 곧 hunk 하나다.** 사용자가 선택하는 단위이기도 하다.

### 6.4 diff를 계산하는 경우와 아닌 경우

| 화면 | 입력 | hunk를 어떻게 얻나 |
|---|---|---|
| 그래프 최신화 비교 | AI가 준 변경 목록 | **계산하지 않는다.** 이미 hunk 형태로 온다 |
| 버전 비교 | 문자열 2개 | **계산한다** |

AI는 `anchor_old` → `anchor_new` 쌍을 준다. 그 자체가 hunk이므로, 현재 본문에서 `anchor_old`의 위치만 찾으면 화면에 그릴 수 있다. 새 본문을 합성해 다시 diff를 뜰 이유가 없다.

**버전 비교는 2단계로 계산한다.**

```text
1단계  빈 줄 기준으로 문단 배열로 쪼개 LCS
       → 추가된 문단 / 삭제된 문단 / 유지된 문단 / 변경된 문단
2단계  "변경된 문단" 쌍에 대해서만 문자 단위 diff
```

한국어는 공백으로 단어가 갈리지 않아 단어 단위 비교가 쓸모없다. 그리고 5,000자 × 5,000자를 통째로 문자 단위로 돌리면 느리다. 문단으로 먼저 걸러야 실용적인 속도가 나온다.

### 6.5 비교 화면과 결정

```text
┌─────────────────────┬─────────────────────┐
│ 현재 문서            │ AI 제안              │
├─────────────────────┼─────────────────────┤
│ 남부 출신이다.        │ 남부 출신이다.        │
│ 나이는 스물셋이다.    │ 나이는 스물넷이다. ●  │ ① [유지] [적용] [수정]
│ 가족은 없다.         │ 가족은 없다.         │
│                     │ 여동생 리아가 있다. ● │ ② [유지] [적용] [수정]
└─────────────────────┴─────────────────────┘
  속성  관련 이벤트: (없음) → 도깨비 학살 ●      ③ [유지] [적용]
```

버튼을 누르면 **그 행의 `decision`만** UPDATE된다. 문서는 그대로다.

```text
PATCH /refresh-changes/①  { decision: 'TAKE_NEW' }
PATCH /refresh-changes/②  { decision: 'KEEP_CURRENT' }
PATCH /refresh-changes/③  { decision: 'TAKE_EDITED',
                            editedValue: '도깨비 학살, 남부 전쟁' }
```

DB에 남으므로 브라우저를 닫았다 와도 선택이 그대로다.

### 6.6 좌·우 양쪽을 편집할 수 있게 하는 경우

비교 화면에서 현재 쪽과 제안 쪽 어느 것을 고쳐도 결과는 같다 — **"이 hunk의 최종 값은 이것"**이고, 그 값이 `edited_value`에 들어가며 `decision`이 `TAKE_EDITED`가 된다. 좌·우의 구분은 편집을 시작하는 순간 사라진다.

**병합 결과는 저장하지 않고 계산한다.**

```text
merged = 현재 body_md 에서
           decision = TAKE_NEW     인 hunk → anchor_new 적용
           decision = TAKE_EDITED  인 hunk → edited_value 적용
           decision = KEEP_CURRENT 인 hunk → 그대로
```

이렇게 두면 요구사항 §8.2의 *"되돌리기"*가 공짜다. `decision`을 `UNDECIDED`로 되돌리면 그 hunk만 원래대로 간다. 병합 결과 문자열을 따로 저장해 두었다면 되돌릴 방법이 없다.

### 6.7 반영 확정

```text
POST /refresh-runs/{runId}/apply

  decision='UNDECIDED' 인 행이 있는가?
      있으면 → 400. "아직 결정하지 않은 변경이 있습니다"
      (요구사항 §8.2 "모든 차이를 처리한 뒤에만 반영을 확정한다")

  문서별로 트랜잭션 하나:
      proposals.base_revision_no ≠ files.revision_no ?
          다르면 → status='STALE'. 건너뛰고 재검토를 요청한다

      선택된 hunk를 현재 body_md 에 순서대로 적용
      UPDATE documents SET body_md = '…결과 전체…'
      UPDATE files SET revision_no = revision_no + 1
      INSERT file_versions (kind='AI_APPLY')
      UPDATE refresh_proposals SET status='APPLIED'
      INSERT outbox_events            -- 관계가 바뀌었다면
  COMMIT
```

부분 적용인데도 결국 `body_md`를 통째로 다시 쓴다. **메모리에서 문자열을 조립한 뒤 한 번에 쓰는 것**이고 15KB짜리라 비용이 없다. 전체 저장 방식이 부분 선택을 막지 않는다.

결과가 `AI_APPLY` 버전으로 남으므로 마음에 들지 않으면 이전 버전으로 복원하면 된다.

---

## 7. 왜 블록 단위로 쪼개지 않는가

비교 화면에서 양쪽을 편집할 수 있게 하면 hunk의 위치가 계속 밀린다. 이 때문에 Notion식 블록 구조가 필요해 보이지만, 필요하지 않다.

### 7.1 hunk 추적은 위치 매핑으로 한다

```text
편집 전   hunk ① = 위치 45~58
사용자가 앞쪽에 10글자를 추가
편집 후   hunk ① = 위치 55~68     ← ProseMirror가 자동으로 옮긴다
```

ProseMirror의 position mapping은 협업 편집과 코멘트 앵커를 위해 만들어진 기능이다. hunk를 decoration으로 얹어 두면 편집할 때마다 따라간다. CodeMirror에도 `mapPos`로 같은 것이 있다. VS Code의 병합 충돌 화면과 `git mergetool`이 평문 텍스트 위에서 같은 일을 한다.

### 7.2 hunk는 문서 구조가 아니다

```text
병합 세션 동안만 존재   →  hunk id, 좌·우 범위
확정하면 버려진다       →  문서는 다시 그냥 문자열
```

`refresh_changes` 행은 제안을 처리하는 동안만 사는 데이터다. 문서 자체에 블록 ID를 박는 것과는 다른 얘기다.

### 7.3 구조가 필요한 부분은 이미 구조화되어 있다

```text
설정 문서 "카이론"
├─ 속성 표  →  document_properties / document_relations 행들   ← 이미 항목마다 독립
└─ 본문     →  documents.body_md 문자열 하나                   ← 자유 산문
```

"이 항목만 제안 수용"이 자연스러워야 하는 곳은 속성이고, 거기는 이미 행 단위다. 남은 본문은 정해진 구조가 없는 산문이라 블록 ID를 박아도 얻는 것이 없다.

### 7.4 블록으로 쪼갤 때 잃는 것

| | 문자열 하나 | 블록 테이블 |
|---|---|---|
| 문서 열기 | `SELECT body_md` 한 행 | 문단 수백 행 JOIN + 정렬 |
| 검색 | 컬럼 하나 인덱싱 | 블록별 검색 후 문서 단위 재조립 |
| diff | 텍스트 비교 | 블록 대응 관계를 먼저 풀어야 함 |
| AI에 전달 | 그대로 | 조립해서 Markdown 생성 |
| 버전 | 한 행 복사 | 수백 행 복사 + 순서 보존 |
| 문단 순서 변경 | 문자열 이동 | 뒤쪽 전부 재번호 |

특히 버전이 아프다. 30일치 자동 버전을 블록 단위로 뜨면 버전 하나당 수백 행이 생긴다.

> 블록 구조가 실제로 필요해지는 것은 **문단별 코멘트가 편집을 견뎌야 할 때**나 **실시간 공동 편집**을 할 때다. 둘 다 현재 범위 밖이고, 그때가 오면 블록이 아니라 CRDT를 검토해야 한다.

---

## 8. 미결 사항

### 8.1 요구사항 문서 간 불일치

| 항목 | `CORE_FEATURE_REQUIREMENTS` | `LORE_SENTRY_PROJECT_CONTEXT` | 이 제안의 선택 |
|---|---|---|---|
| 파일 메모 개수 | 여러 개 (§6.1) | 파일당 하나 (§3.6) | **여러 개.** CORE가 더 최신이고 "와이어프레임 보완"으로 변경을 명시 |
| 본문 내 파일 참조 | 범위 제외 (§5.1) | 삽입 가능 (§3.4) | **제외.** 단 Markdown 문법과 그래프 원천 확장 자리만 예약 |
| 글꼴·크기·줄 간격 | 편집 기능으로 필요 (§5.1) | — | **UI는 제공, 문서 데이터에서는 제외** (§2.3) |
| 자동 버전 주기 | 수치 없음 | 5분 / 30일 (§3.5) | **5분 / 30일** |

### 8.2 정해야 할 것

- **비교 화면에서 hunk 바깥 텍스트도 편집할 수 있게 할 것인가.** 허용하면 `edited_value`로 표현되지 않아 병합 중인 본문 전체를 별도로 들고 있어야 하고, 그러면 hunk별 되돌리기가 성립하지 않는다. 허용하지 않기를 권한다 — 자유 편집은 확정 후 일반 편집기에서 하면 된다.
- **설정 문서 속성의 자유도.** `CORE_FEATURE_REQUIREMENTS` §5.2가 미결로 남긴 항목이다. 이 제안은 사용자 정의 속성(상위 집합)으로 스키마를 잡고 프로젝트 생성 시 유형별 표준 속성을 시드하는 방식을 전제했다. 표준 항목만 허용하기로 정해져도 스키마 변경 없이 검증만 추가하면 된다.
- **자동 버전에 최소 변경량 게이트를 둘 것인가.** 5분 간격만 두면 한 글자만 고쳐도 버전이 쌓인다. 30일치 목록의 가독성 문제다.
- **그래프 최신화의 제안 범위.** 어떤 설정 유형까지, 속성만인지 본문까지인지. `refresh_changes.target`의 허용값이 여기서 갈린다.
- **`refresh_changes` 앵커 매칭 실패 처리.** LLM이 원문을 정확히 베끼지 못할 때를 위해 NFC 정규화 → 공백 유연 매칭 → 곡선따옴표·겹낫표·전각 공백 정규화 순의 단계적 매칭이 필요하다. 후보가 여럿이거나 없을 때 그 hunk만 실패로 두고 나머지를 진행할지 정해야 한다.
- **검색 인덱스.** 한국어 부분 일치는 `pg_bigm`이 더 정확하지만 RDS 확장 지원 여부 확인이 필요하다. `pg_trgm`은 확실히 되고 3음절 이상 질의에서 충분하다.
- **마이그레이션 도구.** `INFRA_AND_CICD.md` §11 체크리스트에 미선택으로 남아 있다. Spring Boot와의 마찰이 가장 적은 것은 Flyway다.

### 8.3 문서 정합성

`README.md`가 `CORE_TABLE_ERD.md`를 링크하고 `GRAPH_INBOX_PATTERN.md`가 그 문서를 전제로 참조하지만, 현재 저장소에 그 파일이 없다. ERD를 확정할 때 함께 정리한다.
