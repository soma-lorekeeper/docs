# Authentication · Content · AI Chat 테이블과 문서 최신화 로직 제안

> 갱신일: 2026-09-20  
> 상태: **테이블 반영됨.** §3·§4·§5·§6의 테이블은 각 서비스의 Flyway 마이그레이션으로 운영 DB에 생성됐다(§9). §7의 최신화 로직과 API 계약은 아직 구현 전이다.  
> 범위: Authentication, Content, AI Chat의 최소 영속 데이터와 AI 기반 문서 최신화 흐름. 메모·즐겨찾기·이미지·검색 인덱스·실시간 협업 편집은 제외한다.

## 1. 결론 요약

문서 최신화는 세 시점의 문서를 다룬다.

```text
base
  최신화 버튼을 누른 시점의 설정 문서 버전.
  AI가 이 버전을 기준으로 제안을 만든다.
  document_versions의 REFRESH_BASE 행을 참조한다.

left
  AI 결과가 도착해 모달을 열기 직전, 실제 document에서 다시 읽은 최신 문서.
  AI가 분석 중인 사이 사용자가 한 일반 편집을 포함한다.

right
  AI가 base를 바탕으로 만든 제안 문서.
```

```text
Content 백엔드
  base version, left snapshot, right snapshot을 보관한다.
  실제 문서 반영 때 revision을 검사하고 트랜잭션으로 저장한다.

프론트엔드
  base / left / right 전체를 받아 2-way 및 3-way diff를 계산한다.
  문단·라인 차이를 표시하고, 좌↔우 복사와 양쪽 직접 편집을 제공한다.
```

`hunk`, `AI가 수정한 줄`, `사용자가 수정한 줄`은 DB의 원본 데이터가 아니다. 사용자가 양쪽을 편집할 때마다 위치와 경계가 달라지므로, 프론트가 세 snapshot에서 다시 계산하는 표시 데이터다.

---

# 2. 서비스별 소유 테이블

```text
Authentication DB
  users
  auth_sessions

Content DB
  projects
  base_folders
  episode_folders
  document
  document_properties
  document_relations
  document_versions
  refresh_runs
  refresh_document_drafts
  outbox_events

AI Chat DB
  chat_sessions
  chat_messages
```

서비스 간에는 DB foreign key를 만들지 않는다. 예를 들어 Content의 `owner_user_id`는 Authentication의 `users.id` 값을 저장하지만, 서로 다른 DB를 직접 JOIN하지 않는다.

---

# 3. Authentication 서비스

## 3.1 `users`

목적: Google 로그인 사용자와 서비스 표시 이름을 보관한다. 현재 로그인 수단이 Google 하나이므로 OAuth 공급자별 별도 테이블은 두지 않는다.

```text
컬럼                 타입              역할과 필요한 이유
──────────────────────────────────────────────────────────────────────────
id                   UUID              서비스 내부 사용자 ID. 다른 서비스가 참조하는 안정적인 값이다.
google_subject       VARCHAR           Google OIDC sub. 이메일 변경과 무관한 Google 계정 고유값이다.
email                VARCHAR           표시용 Google 이메일이다. 사용자는 직접 바꾸지 않는다.
display_name         VARCHAR           사용자가 수정 가능한 서비스 표시 이름이다.
locale               VARCHAR(5)        화면 언어(ko / en). NULL 이면 아직 적힌 적이 없다. 처음 로그인할 때 프론트가 그때 보던 언어를 적는다.
status               VARCHAR           ACTIVE / DELETED. 탈퇴·비활성 계정 로그인을 막는다.
created_at           TIMESTAMPTZ       계정 생성 시각이다.
updated_at           TIMESTAMPTZ       마지막 계정 수정 시각이다.
```

```text
users 레코드 예시

id             = u-suhyeon
google_subject = 109827364501928374650
email          = suhyeon@example.com
display_name   = 서윤
status         = ACTIVE
created_at     = 2026-09-01 10:00:00+09
updated_at     = 2026-09-18 11:24:12+09
```

제약:

```text
UNIQUE (google_subject)
CHECK (status IN ('ACTIVE', 'DELETED'))
CHECK (locale IN ('ko', 'en'))
```

약관 번역: `terms_version_translations (terms_version_id, locale, title, content)`. 동의는 언제나 원본
`terms_versions` 행에 기록하고, 번역은 그 행을 다른 언어로 보여 줄 뿐이다. `GET /auth/terms?locale=en` 이
번역이 있으면 번역을, 없으면 원본을 돌려준다([frontend/i18n.md](frontend/i18n.md)).

## 3.2 `auth_sessions`

목적: 브라우저 로그인 상태의 재발급·만료·로그아웃을 제어한다. refresh token 원문은 저장하지 않고 해시만 저장한다.

```text
컬럼                 타입              역할과 필요한 이유
──────────────────────────────────────────────────────────────────────────
id                   UUID              세션 ID다.
user_id              UUID              로그인한 users.id다.
refresh_token_hash   VARCHAR           refresh token 원문 대신 저장하는 해시다.
expires_at           TIMESTAMPTZ       이 시각 이후 재발급을 거부한다.
revoked_at           TIMESTAMPTZ       로그아웃·강제 로그아웃 시각. 유효하면 NULL이다.
created_at           TIMESTAMPTZ       로그인 성공 시각이다.
last_used_at         TIMESTAMPTZ       마지막 토큰 재발급 시각이다.
```

```text
auth_sessions 레코드 예시

id                 = s-4db2
user_id            = u-suhyeon
refresh_token_hash = argon2id:$argon2id$v=19$...
expires_at         = 2026-10-18 09:00:00+09
revoked_at         = null
created_at         = 2026-09-18 09:00:00+09
last_used_at       = 2026-09-18 15:06:21+09
```

```text
로그아웃 시:
  revoked_at = now()

중요:
  Content와 AI Chat에는 email, Google token, refresh token을 복제하지 않는다.
```

---

# 4. Content 서비스 — 실제 문서 원본

## 4.1 폴더 모델

폴더를 `document`의 한 종류로 저장하지 않는다.

```text
기본 분류 폴더
  base_folders 테이블의 전역 데이터다.
  세계관, 캐릭터, 장소, 원고, 조직, 아이템, 이벤트가 마이그레이션으로 한 번만 시드된다.
  프로젝트나 사용자마다 중복 생성하지 않는다.

에피소드 폴더
  원고(manuscript)에만 필요한 특수 폴더다.
  episode_folders 테이블의 프로젝트별 행이다.
  사용자가 만들 수 있는 유일한 폴더다.
```

문서는 항상 `folder_id`로 하나의 기본 폴더를 가리킨다. 원고가 에피소드에 속하면 `episode_id`도 함께 가진다. 폴더가 본문을 가지거나 문서와 같은 행에 섞이지 않는다.

## 4.2 `projects`

목적: 창작 자료를 분리하는 최상위 단위다.

```text
컬럼                 타입              역할과 필요한 이유
──────────────────────────────────────────────────────────────────────────
id                   UUID              프로젝트 ID다.
owner_user_id        UUID              만든 Authentication 사용자 ID다.
name                 VARCHAR           프로젝트 이름이다.
description          TEXT              프로젝트 설명이다.
trashed_at           TIMESTAMPTZ       프로젝트 휴지통 이동 시각이다.
created_at           TIMESTAMPTZ       생성 시각이다.
updated_at           TIMESTAMPTZ       마지막 변경 시각이다.
```

```text
projects 레코드 예시

id            = p-orv
owner_user_id = u-suhyeon
name          = 전지적 독자 시점
description   = 회귀와 시나리오를 다루는 장편 소설
trashed_at    = null
```

## 4.3 `base_folders`

목적: 모든 프로젝트가 공유하는 기본 분류 폴더 정의다. 애플리케이션 초기 마이그레이션이 한 번만 시드한다.

```text
컬럼                 타입              역할과 필요한 이유
──────────────────────────────────────────────────────────────────────────
id                   SMALLINT          고정 기본 폴더 ID다.
code                 VARCHAR           WORLDVIEW, CHARACTER, LOCATION, MANUSCRIPT 등 시스템 식별자다.
name                 VARCHAR           화면에 표시할 한국어 이름이다.
position             INTEGER           기본 폴더 표시 순서다.
```

```text
base_folders 레코드 예시

id       = 4
code     = MANUSCRIPT
name     = 원고
position = 40
```

```text
마이그레이션 예시:

INSERT INTO base_folders (id, code, name, position) VALUES
  (1, 'WORLDVIEW',     '세계관', 10),
  (2, 'CHARACTER',     '캐릭터', 20),
  (3, 'LOCATION',      '장소',   30),
  (4, 'MANUSCRIPT',    '원고',   40),
  (5, 'ORGANIZATION',  '조직',   50),
  (6, 'ITEM',          '아이템', 60),
  (7, 'EVENT',         '이벤트', 70);
```

## 4.4 `episode_folders`

목적: 사용자가 프로젝트별로 생성하는 원고 아래의 에피소드 폴더를 보관한다. 현재 사용자가 생성할 수 있는 유일한 폴더다.

```text
컬럼                 타입              역할과 필요한 이유
──────────────────────────────────────────────────────────────────────────
id                   UUID              에피소드 폴더 ID다.
project_id           UUID              소속 프로젝트다.
created_by_user_id   UUID              만든 사용자 ID다.
name                 VARCHAR           에피소드 폴더 이름이다.
rank                 VARCHAR           원고 사이드바에서의 순서다.
created_at           TIMESTAMPTZ       생성 시각이다.
updated_at           TIMESTAMPTZ       제목·순서 변경 시각이다.
```

```text
episode_folders 레코드 예시

id                 = ef-01
project_id         = p-orv
created_by_user_id = u-suhyeon
name               = 1부 멸망의 시작
rank               = a0
```

## 4.5 `document`

목적: 실제 창작 문서의 유일한 원본이다. 폴더 행은 넣지 않는다. 본문은 Markdown 문자열 하나로 통째로 저장한다.

```text
컬럼                 타입              역할과 필요한 이유
──────────────────────────────────────────────────────────────────────────
id                   UUID              문서 ID다.
project_id           UUID              소속 프로젝트다.
folder_id            SMALLINT          base_folders.id. 문서가 속한 기본 폴더다.
episode_id           UUID              episode_folders.id. 원고가 에피소드에 속하면 채운다.
title                VARCHAR           문서 제목이다.
body_json            JSONB             본문. 에디터 문서 구조를 담는다. 본문의 유일한 원본이다.
                                       {"schema_version":1,"doc":{...}} 모양이다.
body_text            TEXT              서버가 body_json 에서 뽑은 순수 텍스트다.
                                       검색·글자 수·AI 추출이 쓴다. 클라이언트가 보내지 않는다.
body_md              TEXT              변환 전 Markdown. body_json 이 NULL 인 옛 행만 쓴다.
                                       새로 쓰지 않으며 후속 마이그레이션에서 지운다.
body_sha             VARCHAR           본문 해시. 같은 저장을 생략하는 데 쓴다.
char_count           INTEGER           글자 수다. body_text 의 코드 포인트 수(줄바꿈 제외)다.
rank                 VARCHAR           같은 기본 폴더 또는 에피소드 안의 정렬 순서다.
revision_no          BIGINT            실제 문서 저장 번호다. 오래된 탭의 덮어쓰기를 막는다.
locked               BOOLEAN           문서 편집 잠금 여부다.
trashed_at           TIMESTAMPTZ       문서 휴지통 이동 시각이다.
created_at           TIMESTAMPTZ       생성 시각이다.
updated_at           TIMESTAMPTZ       마지막 실제 저장 시각이다.
```

핵심 제약:

```text
folder_id는 NOT NULL
episode_id가 있으면 folder_id는 base_folders.MANUSCRIPT여야 한다.
  이 교차 테이블 규칙은 서비스 계층 검증 또는 DB trigger로 보장한다.
UNIQUE (project_id, folder_id, lower(title))의 정확한 범위는
  휴지통·에피소드 이름 정책 확정 후 결정한다.
```

```text
document 레코드 예시 — 캐릭터 문서

id             = d-yjh
project_id     = p-orv
folder_id      = 2                 -- base_folders.CHARACTER
episode_id     = null
title          = 유중혁
body_json      =
  {"schema_version": 1,
   "doc": {"type": "doc", "content": [
     {"type": "paragraph", "content": [
       {"type": "text", "text": "유중혁은 회귀를 반복하는 인물이다."}]},
     {"type": "paragraph", "content": [
       {"type": "text", "text": "그는 매 회차의 결말을 알고 있다."}]}]}}
body_text      = "유중혁은 회귀를 반복하는 인물이다.\n그는 매 회차의 결말을 알고 있다."
body_sha       = sha256:7a82...
char_count     = 38
rank           = a0V
revision_no    = 22
locked         = false
trashed_at     = null
```

```text
document 레코드 예시 — 원고

id             = d-ch-01
project_id     = p-orv
folder_id      = 4                 -- base_folders.MANUSCRIPT
episode_id     = ef-01
title          = 1화 멸망의 시작
body_json      = {"schema_version":1,"doc":{...원고 전체...}}
revision_no    = 7
```

## 4.6 `document_properties`

목적: 설명·별칭처럼 대상 문서를 가리키지 않는 텍스트 속성을 저장한다.

```text
컬럼                 타입              역할과 필요한 이유
──────────────────────────────────────────────────────────────────────────
id                   UUID              속성 행 ID다.
document_id          UUID              속성을 가진 document.id다.
property_key         VARCHAR           description, alias 등 속성 이름이다.
text_value           TEXT              속성 값이다.
position             INTEGER           화면 표시 순서다.
```

```text
document_properties 레코드 예시

id           = dp-yjh-description
document_id  = d-yjh
property_key = description
text_value   = 회귀를 반복하는 세 번째 등장인물
position     = 10
```

## 4.7 `document_relations`

목적: 관계 칩을 저장한다. 그래프와 타임라인의 원천 데이터다.

**관계에는 방향이 없다. 그래서 한 쌍에 한 행이다.** 예전에는 A→B 와 B→A 를 각각 한 행으로 두고
저장할 때마다 반대쪽을 맞춰 주었는데, 같은 사실을 두 곳에 적는 구조라 어긋나면 한쪽 문서에서만
보이는 관계가 생겼다(실제로 그랬다). 두 id 를 크기 순으로 넣어 쌍을 한 가지 모양으로 고정한다 —
순서 제약이 없으면 `(A,B)` 와 `(B,A)` 가 서로 다른 행으로 들어간다.

```text
컬럼                 타입              역할과 필요한 이유
──────────────────────────────────────────────────────────────────────────
id                   UUID              관계 행 ID다.
low_document_id      UUID              두 끝 중 id 가 작은 쪽이다. "출발"이 아니다.
high_document_id     UUID              두 끝 중 id 가 큰 쪽이다.
description          TEXT              이 연결의 설명이다. 대상 문서가 아니라 연결의 것이라 쌍마다 하나다.
```

`relation_key` 와 `position` 은 행의 성질이 아니라서 두지 않는다.

- **키는 반대쪽 문서의 분류가 정한다.** 원고에서 캐릭터를 보면 `related_character`, 같은 행을
  캐릭터에서 보면 `related_manuscript` 다. 한 행에서 양쪽 키가 모두 나와야 하므로 분류 표
  (`base_folders.relation_key`)에서 꺼낸다. 행에 적어 두면 문서를 다른 분류로 옮겼을 때 어긋난다.
- **순서는 읽을 때 정한다**(키, 그다음 행 id = 만든 순서). 한 행이 두 문서의 것이라 문서마다 다른
  순서를 담을 자리가 없다.

```text
document_relations 레코드 예시 — 원고 d-ch1 과 캐릭터 d-yjh 의 관계 하나

id               = dr-ch1-yjh
low_document_id  = d-ch1          (두 id 중 작은 쪽일 뿐이다)
high_document_id = d-yjh
description      = 첫 등장

d-ch1 에서 읽으면 → relation_key = related_character,  target = d-yjh
d-yjh 에서 읽으면 → relation_key = related_manuscript, target = d-ch1
```

휴지통에 있는 문서와의 관계는 **읽을 때 빠지지만 지워지지 않는다.** 그 사이에 상대 문서를
저장했다고 행을 지우면, 되살렸을 때 관계가 돌아올 자리가 없다.

## 4.8 `document_versions`

목적: 실제 문서의 전체 스냅샷을 보관한다. 자동·수동 버전과, 문서 최신화의 공통 원본(base)을 같은 구조로 저장한다.

```text
컬럼                 타입              역할과 필요한 이유
──────────────────────────────────────────────────────────────────────────
id                   UUID              버전 ID다.
document_id          UUID              어느 문서의 버전인가.
source_revision_no   BIGINT            snapshot을 찍은 실제 revision 번호다.
kind                 VARCHAR           AUTO, NAMED, AI_APPLY, RESTORE, REFRESH_BASE다.
label                VARCHAR           NAMED 버전의 사용자 이름이다.
snapshot             JSONB             제목, 본문, 속성, 관계를 포함한 문서 전체 상태다.
expires_at           TIMESTAMPTZ       자동/내부 base 버전의 정리 시각. 영구 버전은 NULL이다.
created_at           TIMESTAMPTZ       버전 생성 시각이다.
```

```text
document_versions 레코드 예시 — 최신화의 base

id                 = dv-yjh-base-9001
document_id        = d-yjh
source_revision_no = 20
kind               = REFRESH_BASE
expires_at         = 2026-10-18 15:00:00+09

snapshot =
{
  "title": "유중혁",
  "folderId": 2,
  "bodyMd": "유중혁은 회귀를 반복하는 인물이다.",
  "properties": [
    { "key": "description", "value": "회귀를 반복하는 세 번째 등장인물" }
  ],
  "relations": [
    { "relationKey": "related_character", "targetDocumentId": "d-kdj" }
  ]
}
```

`REFRESH_BASE`는 사용자가 만든 버전이 아니라 내부 병합 기준이다. 일반 버전 목록에 표시하지 않으며, refresh run이 끝난 뒤 보관 기간 정책에 따라 정리한다.

---

# 5. Content 서비스 — 문서 최신화 작업본

## 5.1 `refresh_runs`

목적: 사용자가 [그래프 최신화]를 누른 1회 실행을 추적한다. 버튼 클릭 즉시 생성한다. 이 행이 있어야 같은 프로젝트에서 중복 분석을 막을 수 있다.

```text
컬럼                 타입              역할과 필요한 이유
──────────────────────────────────────────────────────────────────────────
id                   UUID              최신화 실행 ID다.
project_id           UUID              대상 프로젝트다.
requested_by         UUID              실행한 사용자 ID다.
status               VARCHAR           CAPTURING_BASE, GENERATING, READY, APPLYING, APPLIED, FAILED, CANCELLED다.
prompt_version       VARCHAR           사용한 추출 규칙 버전이다.
started_at           TIMESTAMPTZ       버튼을 누른 시각이다.
ready_at             TIMESTAMPTZ       좌·우 작업본이 준비된 시각이다.
completed_at         TIMESTAMPTZ       반영/실패/취소 종료 시각이다.
error_message        TEXT              실패 사유다.
```

```text
refresh_runs 레코드 예시

id             = rr-9001
project_id     = p-orv
requested_by   = u-suhyeon
status         = GENERATING
prompt_version = graph-refresh-v2
started_at     = 2026-09-18 15:00:00+09
ready_at       = null
completed_at   = null
error_message  = null
```

제약:

```text
프로젝트당 CAPTURING_BASE 또는 GENERATING 상태의 run은 하나만 허용한다.
```

## 5.2 `refresh_document_drafts`

목적: 최신화 모달에서 문서 하나를 비교·병합하기 위한 raw 작업본을 보관한다. 라인·문단 hunk는 저장하지 않는다.

```text
컬럼                         타입              역할과 필요한 이유
────────────────────────────────────────────────────────────────────────────
id                           UUID              문서별 최신화 작업 ID다.
refresh_run_id               UUID              어느 refresh run에 속하는가.
target_document_id           UUID              갱신 후보 기존 문서다.
base_version_id              UUID              document_versions의 REFRESH_BASE 행이다.
left_source_revision_no      BIGINT            left_snapshot을 읽은 당시 실제 document revision이다. 생성 전에는 NULL이다.
left_snapshot                JSONB             AI 결과 도착 직전의 실제 최신 문서 전체다. 생성 전에는 NULL이다.
right_snapshot               JSONB             AI가 base를 바탕으로 만든 제안 문서 전체다. 생성 전에는 NULL이다.
status                       VARCHAR           BASE_CAPTURED, OPEN, APPLIED, STALE, SKIPPED다.
draft_revision               BIGINT            모달 작업본 자동 저장의 충돌 방지 번호다.
updated_at                   TIMESTAMPTZ       마지막 모달 편집 시각이다.
```

```text
refresh_document_drafts 레코드 예시

id                      = rrd-yjh-9001
refresh_run_id          = rr-9001
target_document_id      = d-yjh
base_version_id         = dv-yjh-base-9001
left_source_revision_no = 22
status                  = OPEN
draft_revision          = 1
updated_at              = 2026-09-18 15:05:03+09
```

좌측 snapshot 예시:

```json
{
  "title": "유중혁",
  "folderId": 2,
  "bodyMd": "유중혁은 회귀를 반복하는 인물이다.\n\n그는 매 회차의 결말을 알고 있다.",
  "properties": [
    { "key": "description", "value": "회귀를 반복하는 세 번째 등장인물" }
  ],
  "relations": [
    { "relationKey": "related_character", "targetDocumentId": "d-kdj" }
  ]
}
```

우측 snapshot 예시:

```json
{
  "title": "유중혁",
  "folderId": 2,
  "bodyMd": "유중혁은 1863회차를 반복한 회귀자다.\n\n그는 매 회차의 결말을 알고 있으며, 김독자와 이지혜를 지킨다.",
  "properties": [
    { "key": "description", "value": "1863회차를 반복한 회귀자" }
  ],
  "relations": [
    { "relationKey": "related_character", "targetDocumentId": "d-kdj" },
    { "relationKey": "related_character", "targetDocumentId": "d-ljh" }
  ]
}
```

## 5.3 `outbox_events`

목적: 실제 문서 변경을 graph-rag에 비동기로 알린다. 모달 작업본 변경에는 만들지 않고, 최종 반영으로 실제 관계가 바뀔 때만 만든다.

```text
컬럼                 타입              역할과 필요한 이유
──────────────────────────────────────────────────────────────────────────
id                   UUID              이벤트 ID다.
event_type           VARCHAR           FileChanged 같은 이벤트 종류다.
aggregate_id         UUID              변경된 document.id다.
payload              JSONB             그래프 투영에 필요한 최신 문서·관계 상태다.
published_at         TIMESTAMPTZ       Kafka 발행 시각. 미발행이면 NULL이다.
created_at           TIMESTAMPTZ       실제 문서 저장 트랜잭션 시각이다.
```

```text
outbox_events 레코드 예시

id           = oe-yjh-23
event_type   = FileChanged
aggregate_id = d-yjh
published_at = null

payload =
{
  "documentId": "d-yjh",
  "revisionNo": 23,
  "relations": [
    { "relationKey": "related_character", "targetDocumentId": "d-kdj" },
    { "relationKey": "related_character", "targetDocumentId": "d-ljh" }
  ]
}
```

---

# 6. AI Chat 서비스

## 6.1 `chat_sessions`

목적: 일반 프로젝트별 AI 대화의 세션을 보관한다. 문서 최신화 task와 일반 대화는 별개다.

```text
컬럼                 타입              역할과 필요한 이유
──────────────────────────────────────────────────────────────────────────
id                   UUID              대화 세션 ID다.
project_id           UUID              소속 프로젝트다.
user_id              UUID              만든 사용자 ID다.
title                VARCHAR           사용자가 붙인 대화 제목이다.
created_at           TIMESTAMPTZ       생성 시각이다.
updated_at           TIMESTAMPTZ       마지막 메시지 시각이다.
deleted_at           TIMESTAMPTZ       삭제 시각이다.
```

```text
chat_sessions 레코드 예시

id         = cs-100
project_id = p-orv
user_id    = u-suhyeon
title      = 유중혁의 동기 분석
created_at = 2026-09-18 13:00:00+09
deleted_at = null
```

## 6.2 `chat_messages`

목적: 일반 AI 대화의 완성된 사용자 메시지와 AI 응답을 저장한다. 스트리밍 중간 조각은 저장하지 않고, 완성된 응답만 확정한다.

```text
컬럼                 타입              역할과 필요한 이유
──────────────────────────────────────────────────────────────────────────
id                   UUID              메시지 ID다.
session_id           UUID              chat_sessions.id다.
role                 VARCHAR           USER / ASSISTANT다.
content_md           TEXT              메시지 본문이다.
status               VARCHAR           COMPLETE / FAILED / CANCELLED다.
created_at           TIMESTAMPTZ       생성 시각이다.
```

```text
chat_messages 레코드 예시

id         = cm-201
session_id = cs-100
role       = ASSISTANT
content_md = 유중혁은 반복되는 회귀 속에서...
status     = COMPLETE
```

---

# 7. 문서 최신화 상세 구현

## 7.1 시작: 기준 버전(base)을 만든다

사용자가 [그래프 최신화]를 누르면 Content가 먼저 task를 만든다.

```text
Content 트랜잭션

1. refresh_runs(rr-9001, CAPTURING_BASE) INSERT

2. 갱신 대상 후보인 활성 설정 문서를 읽는다.
   예: character / location / organization / item / event / worldview

3. 각 후보 문서의 제목, 본문, 속성, 관계 전체를
   document_versions(kind = REFRESH_BASE)로 INSERT
   이 행들이 base다.

4. 후보 문서마다 refresh_document_drafts 행을 INSERT
   base_version_id = 방금 만든 document_versions.id
   left_snapshot, right_snapshot = NULL
   status = BASE_CAPTURED

5. refresh_runs.status = GENERATING

6. base version들과 선택 원고를 문서 최신화 생성 모듈에 전달한다.
   이 모듈은 현재 AI Chat의 일반 질의응답 기능과 별개다.
```

이때 생성되는 base 예시:

```text
15:00:00
document d-yjh의 실제 revision = 20

document_versions
  id                 = dv-yjh-base-9001
  kind               = REFRESH_BASE
  source_revision_no = 20

refresh_document_drafts
  id                 = rrd-yjh-9001
  refresh_run_id     = rr-9001
  target_document_id = d-yjh
  base_version_id    = dv-yjh-base-9001
  status             = BASE_CAPTURED
```

이후 사용자가 일반 편집기에서 유중혁을 수정해도 base는 바뀌지 않는다.

## 7.2 AI 생성 중 일반 편집은 계속 가능하다

```text
15:00  base 생성. 유중혁 실제 revision = 20
15:02  사용자가 일반 편집기로 저장. revision 20 → 21
15:04  사용자가 다시 저장. revision 21 → 22
15:05  AI가 base revision 20을 근거로 제안을 반환
```

일반 저장은 전체 본문을 조건부로 갱신한다.

```text
UPDATE document
SET body_json = :entireBody,
    body_text = :extractedText,
    body_sha = :hash,
    char_count = :count,
    revision_no = revision_no + 1,
    updated_at = now()
WHERE id = :documentId
  AND revision_no = :expectedRevision;
```

0행이 갱신되면 다른 탭/기기가 먼저 저장한 것이다. 서버는 오래된 전체 본문으로 최신 문서를 덮어쓰지 않고 충돌 응답을 보낸다.

## 7.3 AI 결과 도착: left와 right를 만든다

문서 최신화 생성 모듈이 제안을 반환하면 Content는 문서별로 다음을 한 트랜잭션에서 실행한다.

```text
1. AI 결과의 targetDocumentId와 baseVersionId가
   같은 프로젝트의 REFRESH_BASE인지 검증한다.

2. 현재 실제 document와 document_properties,
   document_relations를 다시 읽는다.
   → 이것이 left_snapshot이다.
   → 예시에서는 실제 revision 22다.

3. AI가 base를 기준으로 만든 전체 제안을 검증한다.
   → 이것이 right_snapshot이다.

4. 기존 refresh_document_drafts 행 UPDATE
   base_version_id = dv-yjh-base-9001  (이미 연결됨, revision 20)
   left_source_revision_no = 22
   left_snapshot = 실제 최신 문서
   right_snapshot = AI 제안 문서
   status = OPEN

5. 모든 대상 문서 작업본 생성 후 refresh_runs.status = READY
```

세 문서는 이렇게 달라질 수 있다.

```text
base (15:00, revision 20)
  설명: 회귀를 반복하는 세 번째 등장인물
  본문: 유중혁은 회귀를 반복하는 인물이다.

left (15:05, revision 22)
  설명: 회귀를 반복하는 세 번째 등장인물
  본문: 유중혁은 회귀를 반복하는 인물이다.
        그는 매 회차의 결말을 알고 있다.

right (AI 제안)
  설명: 1863회차를 반복한 회귀자
  본문: 유중혁은 1863회차를 반복한 회귀자다.
```

## 7.4 프론트엔드: 2-way와 3-way 계산

프론트가 받는 값:

```text
base  = document_versions[base_version_id].snapshot
left  = refresh_document_drafts.left_snapshot
right = refresh_document_drafts.right_snapshot
```

각 계산의 목적은 다르다.

```text
left ↔ right : 2-way diff
  현재 모달에서 어느 문단·라인·속성·관계가 다른지 표시한다.
  좌측/우측 영역과 화살표는 이 결과를 사용한다.

base → left, base → right : 3-way diff/merge
  공통 원본에서 좌측과 우측이 각각 무엇을 바꿨는지 판단한다.
  서로 다른 문단을 바꾼 독립 변경은 자동 병합 후보로 표시할 수 있다.
  같은 base 구간을 서로 다르게 바꾼 경우는 충돌 구간으로 표시한다.
```

본문의 권장 계산 단위:

```text
1. 최상위 블록 배열을 그대로 문단 단위로 쓴다(본문이 이미 블록 배열이다)
2. 블록 단위 diff3로 안정 구간 / 변경 구간 / 충돌 구간 판정.
   블록 동일성은 직렬화 비교다 — 같은 글자라도 서식이 다르면 다른 블록이다
3. 변경 블록 내부만 라인 diff
4. 필요하면 바뀐 라인 내부만 문자 diff로 강조
```

속성과 관계는 텍스트 diff가 아니다.

```text
텍스트 속성: property_key별 base / left / right 값 비교
관계: relation_key + target_document_id를 키로 집합 비교
```

## 7.5 모달에서의 양방향 병합과 저장

좌측과 우측 모두 편집 가능하다. 우측 AI안이 우선권을 갖지 않는다.

```text
사용자가 우측 hunk를 좌측으로 복사
  → left_snapshot의 대응 문단을 교체

사용자가 좌측 hunk를 우측으로 복사
  → right_snapshot의 대응 문단을 교체

사용자가 좌측 또는 우측을 직접 수정
  → 수정한 snapshot 전체를 변경
```

프론트는 변경 뒤 다시 diff를 계산한다. 서버에는 hunk가 아니라 바뀐 좌·우 snapshot 전체를 저장한다.

```text
PATCH /refresh-document-drafts/{draftId}

If-Match-Draft: "4"
{
  "leftSnapshot":  { ...문서 전체... },
  "rightSnapshot": { ...문서 전체... }
}
```

```text
Content 저장

UPDATE refresh_document_drafts
SET left_snapshot = :left,
    right_snapshot = :right,
    draft_revision = draft_revision + 1,
    updated_at = now()
WHERE id = :draftId
  AND draft_revision = :expectedDraftRevision;
```

모달을 닫았다 다시 열어도 좌·우 편집 상태가 보존된다.

## 7.6 최종 반영

사용자는 병합 결과가 있는 한쪽을 선택한다. 선택한 쪽은 반영 요청에만 담기고 DB 컬럼으로 저장하지 않는다. 반영 결과는 `document_versions(kind = AI_APPLY)`의 snapshot으로 남는다.

```text
POST /refresh-document-drafts/{draftId}/apply

{ "side": "LEFT" }     현재 버전 전체 반영
{ "side": "RIGHT" }    신규 버전 전체 반영
```

Content는 최종 반영 전에 left가 만들어진 뒤 실제 문서가 또 바뀌지 않았는지 확인한다.

```text
현재 document.revision_no == left_source_revision_no ?

같다
  1. 선택한 snapshot을 실제 document, properties, relations에 반영
  2. document.revision_no 증가
  3. document_versions(kind = AI_APPLY) 생성
  4. 관계가 바뀌면 outbox_events 생성
  5. refresh_document_drafts.status = APPLIED

다르다
  1. 실제 문서는 덮어쓰지 않음
  2. refresh_document_drafts.status = STALE
  3. 좌·우 작업본은 그대로 보존
```

`STALE` 처리에서 자동 3-way 재병합을 할지, 사용자가 새 최신 문서를 기준으로 다시 검토하게 할지는 후속 결정이다. 초기 구현은 사용자의 작업본을 보존한 채 재검토를 요구하는 편이 안전하다.

## 7.7 그래프 반영

```text
사용자가 최종 반영
  ↓
Content 트랜잭션
  document / properties / relations 갱신
  document_version(AI_APPLY) 생성
  outbox_event(FileChanged) 생성
  ↓
Outbox publisher → Kafka
  ↓
graph-rag → Neptune의 문서 노드와 관계 간선 갱신
```

AI 생성 중, diff 모달 편집 중에는 Neptune을 갱신하지 않는다. 실제 `document_relations`가 확정된 뒤에만 그래프를 갱신한다.

---

# 8. 이번 제안에서 의도적으로 제외한 것

```text
AI가 참고한 모든 원고의 별도 복사 테이블
  AI 작업 재현에는 유용하지만, base / left / right 병합과 최종 반영의 필수 데이터는 아니다.

refresh_changes, ai_hunks, user_hunks, line_changes
  편집하면 경계가 계속 달라진다. snapshot에서 프론트가 계산한다.

일반 폴더를 document 행으로 저장하는 모델
  기본 폴더는 base_folders, 사용자 생성 에피소드 폴더는 episode_folders로 분리한다.

실시간 공동 편집용 OT/CRDT
  현재 범위 밖이다. 다중 탭의 저장 충돌은 document.revision_no 조건부 저장으로 막는다.
```

---

# 9. 스키마 반영 현황

2026-09-19에 각 서비스 저장소의 Flyway 마이그레이션으로 운영 RDS의 논리 DB에 반영했다.

```text
서비스           마이그레이션 위치                          운영 DB 버전
─────────────────────────────────────────────────────────────────────────
authentication   src/main/resources/db/migration           v1  users, auth_sessions
content          src/main/resources/db/migration           v2  테이블 10개 + base_folders 시드
ai-chat          db/migration                              v1  chat_sessions, chat_messages
```

실행 방식:

```text
authentication, content
  Spring Boot 기동 시 Flyway가 자동 실행된다.

ai-chat
  이미지에 Flyway CLI와 JRE를 포함한다.
  docker-entrypoint.sh가 flyway migrate를 실행한 뒤 uvicorn을 시작한다.
```

스키마를 바꿀 때는 적용된 파일을 수정하지 않고 다음 번호의 `V{n}__설명.sql`을 추가한다. 이미 적용된 파일을 고치면 Flyway 체크섬 검증이 실패해 pod가 기동하지 않는다.

문서 설명에 더해 DB에서 강제하는 규칙:

```text
공통
  PK 기본값은 PostgreSQL 18 내장 uuidv7()
  같은 DB 안의 참조는 FK로 연결한다. 서비스 간 참조(user_id 등)는 값만 저장한다.

document
  episode_id가 있으면 folder_id = 4(MANUSCRIPT)             CHECK
  episode_id의 에피소드는 같은 project_id에 속해야 한다       복합 FK (episode_id, project_id)
  rank는 COLLATE "C"                                        분수 인덱스 문자열을 바이트 순으로 정렬

document_relations
  UNIQUE (document_id, relation_key, target_document_id)

refresh_runs
  프로젝트당 CAPTURING_BASE / GENERATING run은 하나          부분 UNIQUE 인덱스

refresh_document_drafts
  UNIQUE (refresh_run_id, target_document_id)
  OPEN / APPLIED / STALE이면 left_source_revision_no,
    left_snapshot, right_snapshot이 모두 NOT NULL           CHECK
```

문서 제목의 UNIQUE 범위(§4.5)는 휴지통·에피소드 이름 정책이 확정되지 않아 아직 걸지 않았다.
