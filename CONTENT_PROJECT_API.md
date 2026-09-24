# Content API — 구현 현황과 계약

> 작성일: 2026-09-22 · 최신화: 2026-09-24
> 상태: **26개 엔드포인트가 운영에서 동작한다.** graph-rag·Kafka·AI가 필요한 것만 남았다.
> 범위: content 서비스의 HTTP API 전부와, 그것을 외부로 내보내는 gateway 중계. 처음에는 `projects` CRUD 계획서로 시작해 실제 구현 기록으로 자랐다.
> 전제: [`TABLE_AND_LOGIC.md`](TABLE_AND_LOGIC.md) §4, [`CORE_FEATURE_REQUIREMENTS.md`](CORE_FEATURE_REQUIREMENTS.md), [`LORE_SENTRY_PROJECT_CONTEXT.md`](LORE_SENTRY_PROJECT_CONTEXT.md) §3

---

# 0. 현재 상태 — 한눈에

```text
브라우저 → Cloudflare → ALB → gateway ──네임스페이스 3개 중계──▶ content ─▶ RDS(content)
                                 │                                    26개 엔드포인트
                                 └── 무인증. X-User-Id 를 클라이언트가 고른다
```

| 저장소 | 배포 | 테스트 |
|---|---|---|
| `loresentry-content` | `build-8-1`, Flyway `V5` | 92 |
| `loresentry-gateway` | `build-7-1` | 55 |
| `loresentry-frontend` | 배포됨, 기본값은 mock (`?data=api` 로 전환) | 215 |

## 0.1 구현된 엔드포인트 26개

전부 `X-User-Id` 가 필요하고, 호출자 소유 프로젝트로만 한정된다.

**프로젝트 8개** — `loresentry-content#3`, `#4`

| Method | Path | 비고 |
|---|---|---|
| `GET` | `/projects` | 활성 목록, 최근 작업 순 |
| `GET` | `/projects/trash` | 휴지통 목록 |
| `POST` | `/projects` | `201` + `Location` |
| `GET` | `/projects/{id}` | 휴지통 프로젝트는 `404` |
| `PATCH` | `/projects/{id}` | 이름·설명 부분 갱신 |
| `POST` | `/projects/{id}/trash` · `/restore` | 멱등 |
| `DELETE` | `/projects/{id}` | 휴지통에서만 |

**파일 트리 10개** — `loresentry-content#5`

| Method | Path | 비고 |
|---|---|---|
| `GET` | `/projects/{id}/files` | 폴더·에피소드·문서 세 목록. 트리는 화면이 조립(§3.2) |
| `GET` | `/projects/{id}/files/trash` | 원래 분류·에피소드 이름 포함 |
| `POST` | `/projects/{id}/files` | `kind: document \| episode` |
| `PATCH` | `/files/{id}` | 이름 변경 |
| `PATCH` | `/files/{id}/position` | 이동·순서. 앞에 둘 형제를 지정하면 서버가 rank 계산 |
| `POST` | `/files/{id}/trash` · `/restore` | 복원 시 에피소드가 없으면 원고 폴더로 |
| `DELETE` | `/files/{id}` | 휴지통에서만 |
| `PATCH DELETE` | `/episodes/{id}` | 삭제해도 회차는 남아 원고 폴더로 |

**문서 편집 3개 + 버전 4개** — `loresentry-content#5`

| Method | Path | 비고 |
|---|---|---|
| `GET` | `/files/{id}/content` | 본문·텍스트 속성·관계 칩 |
| `PUT` | `/files/{id}/content` | **`If-Match` 필수**, `X-Save-Id` 로 재시도 멱등 |
| `PUT` | `/files/{id}/lock` | 잠금 |
| `GET` | `/files/{id}/versions` | 스냅샷 포함 |
| `POST` | `/files/{id}/versions` | 이름 붙인 버전 |
| `POST` | `/files/{id}/versions/{vid}/restore` | **새 revision** 으로 저장. `If-Match` 필수 |
| `DELETE` | `/files/{id}/versions/{vid}` | |

**검색 1개** — `GET /projects/{id}/search?q=`. 활성 문서의 제목·본문, 관련도 순, 스니펫 포함.

## 0.2 구현되지 않은 엔드포인트

프론트 `ports.ts` 의 13개 서비스 기준이다. **막는 것이 무엇인지**가 이 표의 요점이다.

| 포트 | 없는 엔드포인트 | 막는 것 |
|---|---|---|
| `AuthService` (3) | Google 로그인 시작·세션·로그아웃 | authentication 서비스가 `deliverable/LOREKEEPER-506` 브랜치에 있고 main 에 없다. gateway 릴레이도 없다 |
| `AccountService` (2) | 계정 조회·표시 이름 | 같음. auth 는 `/auth/users/me` 로 이미 구현돼 있다 |
| `GraphService` (1) | `GET /projects/{id}/graph` | **graph-rag + Neptune.** 투영본을 만들 Outbox publisher 와 Inbox consumer 가 없다 |
| `RefreshService` (4) | 최신화 실행·조회·반영·폐기 | **AI 추출 모듈**, 그리고 반영이 관계를 바꿔 `outbox_events` 를 만든다 |
| `ChatService` (6) | 세션 CRUD, 메시지, 스트리밍 | LLM provider 미정. ALB `idle_timeout` 60초와 gateway `read-timeout` 10초를 올려야 SSE 가 산다 |
| `MemoService` (4) | 프로젝트·파일 메모 | **테이블이 없다.** `TABLE_AND_LOGIC.md` 가 의도적으로 제외했고, 서버에 둘지 자체가 미결(§9) |
| `WorkspaceStateService` (2) | 작업공간 저장·복원 | 같음. 테이블 없음 |
| `FileService` 일부 (4) | 즐겨찾기 2개, 사용자 섹션 2개 | 즐겨찾기는 테이블 없음. 섹션은 **폴더 모델 결정**이 안 났다(§9-1) |
| `DocumentService` 일부 (1) | `export` (PDF·DOCX·HWP) | 서버 렌더링 미구현. PDF 는 화면 인쇄로 대체 |
| `HelpService` (1) | 사용 가이드 | 정적 번들로 갈지 서버에서 받을지 미결 |

content 안에서 코드가 없는 것도 함께 적는다.

| 항목 | 상태 |
|---|---|
| `last_file` (프로젝트 응답) | 항상 `null`. 문서는 이제 있으므로 질의 한 번이면 채운다 |
| `outbox_events` 쓰기 | 테이블만 있고 쓰는 코드가 없다. 토픽 설계 미확정(`LORE_SENTRY_PROJECT_CONTEXT.md` §19.3) |
| 이미지 업로드 엔드포인트 | `MediaStorageService` 까지만. `image` 테이블과 공개 경로가 없다(`IMAGE_UPLOAD_S3.md`) |
| `refresh_runs` · `refresh_document_drafts` | 테이블만 있고 로직이 없다(§7 이하 설계는 `TABLE_AND_LOGIC.md` §7) |

## 0.3 gateway 쪽에서 한 일

**`loresentry-gateway#2`** — content 중계. **`#3`** — 계약 헤더 전달.

| 한 것 | 내용 |
|---|---|
| 네임스페이스 3개 중계 | `/projects/**` · `/files/**` · `/episodes/**` → content. 경로를 **다시 쓰지 않는다** |
| 신원 주입 | `IdentityResolver` 가 정한 값을 `X-User-Id` 로 싣는다 |
| 계약 헤더 전달 | `If-Match` · `If-None-Match` · `X-Save-Id` 허용 목록 |
| 응답 무가공 통과 | 상태 코드와 **오류 본문까지** 그대로. `Location` · `Cache-Control` 유지, hop-by-hop 제거 |
| 오류 계약 통일 | gateway 자신의 실패도 `{code, message, next_action}`. 업스트림 불통은 `UPSTREAM_UNAVAILABLE` + `upstream` |
| URI 인코딩 끔 | 서블릿이 이미 인코딩한 경로·쿼리를 다시 인코딩해 `%EC` → `%25EC` 가 되던 것 수정 |

**gateway에서 하지 않은 것**

| 항목 | 상태 |
|---|---|
| **JWT 검증** | ❌ **없다.** `ClientHeaderIdentityResolver` 가 클라이언트의 `X-User-Id` 를 그대로 믿는다 → §0.4 |
| auth 경로 중계 | `/auth/**` 릴레이가 없다. 프로브 `GET /auth` 뿐이다 |
| composition | 없다. **프론트가 요구하는 것도 없다** — 54개 포트 메서드 중 두 서비스를 합쳐야 하는 것이 하나도 없다 |
| AI 스트리밍 패스스루 | 없다. 타임아웃 두 개를 올려야 한다 |
| retry · circuit breaking · 관측성 | 없다 |

## 0.4 지금 가장 급한 것 — 무인증

`api.loresentry.com` 의 프로젝트·파일·문서 엔드포인트는 **누구나 읽고 쓸 수 있다.** 헤더에 아무 UUID나 넣으면 그 사용자의 자료가 된다.

프론트엔드를 실제 API에 붙이기 위해 의도적으로 택한 단계이고, 되돌리는 방법은 정해져 있다 — gateway의 `IdentityResolver` 구현 하나를 JWT 검증으로 바꾸면 된다. 중계는 그 결과만 읽고, 업스트림 헤더를 **복사가 아니라 설정**하므로 클라이언트가 보낸 값은 자동으로 무력화된다.

그때 반드시 함께 들어가야 하는 것: **클라이언트가 보낸 `X-User-Id` 제거.** 이미 `forward` 가 허용 목록에서 그 헤더를 걸러내지만, 새 경로를 추가할 때 같은 실수를 반복하지 않도록 테스트로 고정해 뒀다.

---

# 1. 첫 작업의 경계 — 프로젝트 CRUD

아래 §1~§11은 **프로젝트 CRUD를 처음 설계할 때의 기록**이다. 그때 정한 신원 계약·오류 모양·검증 방식이 이후 파일·문서·검색에 그대로 쓰였으므로 남겨 둔다. 이후 작업으로 달라진 곳은 그 자리에 표시했다.

당시 content에는 `/health`·`/health/db`와 `MediaStorageService`뿐이었고, Flyway로 만든 테이블 10개를 읽고 쓰는 코드가 없었다.

**포함한다**

- 프로젝트 목록·단건 조회, 생성, 이름·설명 수정, 휴지통 이동·복원·영구 삭제
- 위를 위한 영속 계층(`JdbcClient` 기반 repository), 오류 응답 규약, 신원 헤더 계약
- 필요한 스키마 보정(`V3` 마이그레이션, §7)

**제외한다 — 그리고 그 이유**

| 제외 | 이유 |
|---|---|
| Kafka·`outbox_events` | 토픽 설계가 미확정이다(`LORE_SENTRY_PROJECT_CONTEXT.md` §19.3). 프로젝트 CRUD는 graph-rag가 볼 관계를 만들지 않으므로 이벤트 없이 완결된다 |
| 파일·폴더·문서 | `document`·`episode_folders`는 프로젝트가 있어야 만들 수 있다. 다음 단계다 |
| 인증 | gateway에 JWT 검증이 없다. 신원은 헤더 계약으로 **가정만** 하고, 실제 검증은 별도 작업이다(§4) |
| gateway 릴레이 | 지금 릴레이를 붙이면 무인증 공개 API가 된다(§4.3). content에만 구현하고 Telepresence로 검증한다 |
| 휴지통 보존 기간·자동 영구 삭제 | 요구사항에 기한이 없다. 무기한 보관한다(§9) |
| 프로젝트 삭제 시 ai_chat 데이터 정리 | 다른 서비스의 DB다. 이벤트가 생긴 뒤의 일이다 |

핵심 판단은 하나다. **프로젝트 CRUD는 `projects` 테이블 한 개로 닫힌다.** 영구 삭제만 예외적으로 다른 테이블을 건드리는데, 그것도 FK CASCADE로 DB에 맡긴다(§7).

## 2. 프론트엔드 계약이 이미 있다

`loresentry-frontend`가 `ProjectService` 포트 10개를 mock으로 구현해 전 화면을 돌리고 있다(`docs/frontend/mock-and-server-contract.md`). 이 API는 **그 포트를 채우는 것**이 목적이므로, 포트에서 거꾸로 설계한다.

| 프론트 포트 | 엔드포인트 |
|---|---|
| `list()` | `GET /projects` |
| `listTrash()` | `GET /projects/trash` |
| `create({title, description})` | `POST /projects` |
| `get(projectId)` | `GET /projects/{projectId}` |
| `getSettings(projectId)` | `GET /projects/{projectId}` — 같은 리소스다 |
| `rename(projectId, title)` | `PATCH /projects/{projectId}` |
| `saveSettings(projectId, settings)` | `PATCH /projects/{projectId}` |
| `moveToTrash(projectId)` | `POST /projects/{projectId}/trash` |
| `restore(projectId)` | `POST /projects/{projectId}/restore` |
| `deletePermanently(projectId)` | `DELETE /projects/{projectId}` |

`getSettings`는 `{title, description}`만 돌려주는데, 이는 `Project`의 부분집합이다. **별도 엔드포인트를 만들지 않는다.** 설정 화면 전용 리소스를 두면 같은 데이터에 진실의 원천이 둘이 된다.

`rename`과 `saveSettings`도 한 엔드포인트다. 둘 다 "프로젝트 메타의 일부를 고친다"이고, 차이는 보내는 필드 수뿐이다.

## 3. 경로와 표현

### 3.1 경로

```text
content 내부 : /projects, /projects/{projectId}, ...
gateway 공개 : /projects       (그대로 중계. 당시 계획은 /api/projects 였다 — §11.1)
```

**[변경됨] 결국 `/api` 접두사도 두지 않았다(§11.1).** 아래는 당시의 판단이다.

`/api/v1` 접두사를 두지 않는다. 이 API의 소비자는 우리 프론트엔드 하나이고, 두 버전을 동시에 운영할 계획이 없다. 쓰지 않을 계층을 미리 넣지 않는 것이 이 프로젝트의 기존 판단과 같다(`LORE_SENTRY_PROJECT_CONTEXT.md` §5.4의 NGINX 제외 논리). 나중에 버전이 필요하면 gateway에 경로 한 줄을 더하면 된다.

### 3.2 표현 — `Project`

```json
{
  "id": "0199a3f2-8c41-7c2a-9f3d-2b7e1c4a5d60",
  "name": "유리 정원의 기록",
  "description": "유리 온실에서 시작되는 장편",
  "last_worked_at": "2026-09-21T05:03:11Z",
  "trashed_at": null,
  "created_at": "2026-09-01T01:00:00Z",
  "last_file": null
}
```

필드 이름은 **snake case**다. authentication 서비스가 이미 그렇게 답하고 있고, 두 서비스가 같은 gateway를 지나 같은 프론트엔드로 간다(§10).

- **`name`이다, `title`이 아니다.** 테이블 컬럼과 `TABLE_AND_LOGIC.md`가 `name`이다. 프론트 모델은 `title`이므로 어댑터(`services/api/projects.ts`) 한 곳에서 매핑한다(§9-1).
- **`last_worked_at`은 지금 `updated_at` 값을 싣는다.** 프로젝트 목록 정렬 기준이다. 문서 저장이 구현되면 같은 트랜잭션에서 `projects.updated_at`을 touch하고, 그때도 API 필드명은 바뀌지 않는다. 별도 컬럼이 필요해지면 그때 나눈다.
- **`last_file`은 `null` 고정이다.** `document` 테이블이 이번 범위 밖이다. 프론트 모델이 이미 `| null`이므로 화면은 깨지지 않는다. 문서 CRUD가 붙으면 `project_id`의 최근 수정 활성 문서로 채운다.
- **`icon`은 서버에 두지 않는다.** 프론트 모델에 있지만 mock이 생성 순번으로 정하는 표시용 값이고, 사용자가 고르는 UI가 와이어프레임에 없다. 어댑터가 `id`에서 결정론적으로 고른다. 사용자가 아이콘을 고르게 되는 날 컬럼을 추가한다.
- `description`은 **항상 문자열**이다. 비어 있으면 `""`를 저장하고 `""`를 돌려준다. `null`을 쓰지 않으므로 어댑터에 `?? ""`가 필요 없다.
- 시각은 전부 ISO-8601 offset 문자열이다.

## 4. 신원 — 잠정 계약

### 4.1 헤더

`projects.owner_user_id`가 `NOT NULL`이므로 모든 요청은 사용자 신원을 필요로 한다. 인증이 구현될 때까지의 잠정 계약이다.

```text
X-User-Id: <authentication 서비스의 사용자 id (정규 UUID)>
```

헤더 이름과 아래 거절 규칙은 **authentication 서비스의 `AccountController`와 같다.** gateway가 서비스마다 다른 이름으로 신원을 실을 이유가 없다.

| 헤더 상태 | 응답 | 이유 |
|---|---|---|
| 없음 | `401 USER_CONTEXT_REQUIRED` | 신원이 아예 없다 |
| 정규 UUID가 아님 | `400 INVALID_REQUEST` | 신원을 읽었는데 값이 틀렸다 |
| 두 번 이상 실림 | `400 INVALID_REQUEST` | 어느 것이 gateway의 것인지 알 수 없다 |

"신원이 없다"와 "신원을 읽었는데 틀렸다"를 구분하는 것이 요점이다. 전자는 로그인이 필요하고, 후자는 호출자가 잘못 만든 요청이다.

- content는 이 헤더를 **무조건 신뢰한다.** 검증은 gateway의 책임이다(`INFRA_AND_CICD.md` §1-A). 서비스마다 토큰을 검증하면 인증 로직이 4곳으로 복제된다.
- 인증이 붙으면 gateway는 **클라이언트가 보낸 동일 헤더를 먼저 제거하고** 자기가 검증한 값을 넣는다. 이 한 줄이 빠지면 누구나 남의 사용자 ID를 사칭할 수 있다.
- `UUID.fromString`은 `1-2-3-4-5` 같은 비정규 표기도 받아들인다. 그래서 왕복 비교로 정규형만 통과시킨다 — authentication 서비스가 하는 것과 같다.

### 4.2 소유권

모든 조회·변경은 `owner_user_id = <헤더 값>`으로 한정한다. 남의 프로젝트에 접근하면 **403이 아니라 `PROJECT_NOT_FOUND`(404)**를 돌려준다. 403은 "그 id는 존재한다"를 알려주고, 프론트에도 그 둘을 구분할 오류 코드가 없다(`ServiceError`는 `not-found` 하나뿐).

### 4.3 gateway 릴레이 — 당시의 판단과 실제

**당시 판단:** 붙이지 않는다. `api.loresentry.com`이 전 구간 무인증 공개이므로, 릴레이하면 인터넷의 누구나 남의 프로젝트를 만들고 지울 수 있다. ALB `inbound-cidrs`는 Argo CD와 공유하는 ALB 전체에 작용하므로 이 경로만 막을 수도 없다(`LORE_SENTRY_PROJECT_CONTEXT.md` §12.3).

그래서 첫 검증은 클러스터 안에서 했다(`ENVIRONMENT_SETTING.md` §5·§6).

```bash
kubectl port-forward -n prod svc/content-api 8080:80
curl -H 'X-User-Id: <uuid>' localhost:8080/projects

# 또는 Telepresence 연결 후
curl -H 'X-User-Id: <uuid>' http://content-api/projects
```

**[변경됨] 이후 프론트엔드를 실제 API에 붙이기 위해 릴레이를 먼저 넣었다**(`loresentry-gateway#2`, §11). 무인증 공개를 감수한 결정이고, 그 대가와 되돌리는 방법은 §0.4에 적었다. JWT 검증은 여전히 남은 작업이다.

이 port-forward 경로는 지금도 유효하고, **gateway와 content를 갈라 보는 유일한 방법**이다. 실제로 `If-Match` 가 중계되지 않던 버그를 이 비교가 잡았다(§11.3).

## 5. 엔드포인트 스펙 — 프로젝트

**이 절은 프로젝트 8개만 다룬다.** 파일·문서·버전·검색 18개의 스펙은 구현과 함께 `loresentry-content` README 에 있다 — 스펙이 코드와 같은 저장소에 있으면 함께 고쳐진다. 목록은 §0.1이다.

공통: 요청·응답 `Content-Type: application/json`, 요청에 `X-User-Id` 필수.

### 5.1 `GET /projects` — 활성 프로젝트 목록

```text
200  { "projects": [ Project, ... ] }
```

- `trashed_at IS NULL`인 것만, `last_worked_at DESC` 정렬이다(요구사항 §2.2 "최근 작업 순").
- 빈 목록은 오류가 아니라 `{"projects": []}`다. 프론트에 빈 상태 화면이 있다(와이어프레임 093).
- 페이지네이션 없음. 한 사용자의 프로젝트 수가 수십 개를 넘을 근거가 없다(§9).
- 배열을 최상위로 돌려주지 않고 객체로 감싼다. 나중에 `total` 같은 필드를 더할 자리가 필요하다.

### 5.2 `GET /projects/trash` — 휴지통 목록

```text
200  { "projects": [ Project, ... ] }
```

- `trashed_at IS NOT NULL`인 것만, `trashedAt DESC` 정렬이다.
- 경로가 `/projects/{projectId}`와 겹치지 않는다. `trash`는 UUID가 아니므로 라우팅이 모호해지지 않지만, 컨트롤러에서 `/projects/trash`를 먼저 선언한다.

### 5.3 `POST /projects` — 생성

```json
{ "name": "유리 정원의 기록", "description": "" }
```

```text
201  Location: /projects/{id}
     Project
400  INVALID_PROJECT_NAME          이름이 비었거나 255자를 넘음
400  INVALID_PROJECT_DESCRIPTION   설명이 500자를 넘음
400  INVALID_REQUEST               모르는 필드, 타입이 어긋난 값, 읽을 수 없는 본문
409  PROJECT_NAME_TAKEN            같은 사용자의 활성 프로젝트에 같은 이름이 있음
```

- `description`은 선택이다. 필드를 생략하면 `""`로 본다.
- 요구사항 §2.2의 "제목은 필수, 설명은 비어 있어도 생성 가능"이 그대로 규칙이다.

### 5.4 `GET /projects/{projectId}` — 단건 조회

```text
200  Project
404  PROJECT_NOT_FOUND   없거나, 남의 것이거나, 휴지통에 있음
```

- **휴지통 프로젝트는 404다.** 요구사항 §2.2가 "휴지통 프로젝트는 열 수 없다"이고, 프론트 mock도 같다. 휴지통 항목의 정보는 §5.2 목록으로 충분하다.
- `/workspace`가 `projectId` 접근을 검증하는 근거가 이 엔드포인트다(`LORE_SENTRY_PROJECT_CONTEXT.md` §3.10: URL query 자체를 권한 근거로 쓰지 않는다).

### 5.5 `PATCH /projects/{projectId}` — 이름·설명 수정

```json
{ "name": "새 이름" }
{ "name": "새 이름", "description": "새 설명" }
```

```text
200  Project
400  INVALID_PROJECT_NAME / INVALID_PROJECT_DESCRIPTION / INVALID_REQUEST
404  PROJECT_NOT_FOUND
409  PROJECT_NAME_TAKEN
```

- **부분 갱신이다.** 보낸 필드만 바꾸고, 없는 필드는 그대로 둔다.
- `null`은 보내지 않는다. 설명을 지우려면 `""`를 보낸다. JSON `null`과 필드 부재를 구분하는 규칙을 만들지 않기 위해서다.
- 낙관적 잠금(`If-Match`)을 **두지 않는다.** 마지막 저장이 이긴다. 문서 본문과 달리 프로젝트 메타는 짧고, 충돌을 병합할 것이 없다. 와이어프레임 70–76도 저장 충돌 화면이 아니라 "저장하지 않고 나가기" 확인만 다룬다.
- 휴지통 프로젝트는 수정할 수 없다(404). 복원 후에 고친다.

### 5.6 `POST /projects/{projectId}/trash` — 휴지통 이동

```text
204
404  PROJECT_NOT_FOUND
```

- 이미 휴지통에 있으면 **204를 그대로 돌려주고 `trashed_at`을 덮어쓰지 않는다.** 재시도가 안전해야 하고, 덮어쓰면 휴지통 정렬 순서가 흔들린다.
- `PATCH`의 필드가 아니라 별도 경로다. 상태 전이는 "무엇을 바꿨다"가 아니라 "무슨 일이 일어났다"이고, 이름 수정과 같은 요청에 섞이면 안 된다.

### 5.7 `POST /projects/{projectId}/restore` — 복원

```text
200  Project
404  PROJECT_NOT_FOUND
409  PROJECT_NAME_TAKEN   복원하려는 이름이 활성 프로젝트와 겹침
```

- 이미 활성이면 현재 상태를 200으로 돌려준다(멱등).
- **복원은 중복 이름을 만들 수 있다.** 휴지통에 넣은 뒤 같은 이름으로 새 프로젝트를 만들었다면, 복원 시점에 충돌한다. 409로 거절하고 프론트가 안내한다. 자동 개명(`(1)` 붙이기)은 하지 않는다 — 사용자가 모르는 사이에 이름이 바뀐다.

### 5.8 `DELETE /projects/{projectId}` — 영구 삭제

```text
204
404  PROJECT_NOT_FOUND
409  PROJECT_NOT_TRASHED   휴지통에 있지 않음
```

- **휴지통 항목만 지울 수 있다.** 와이어프레임 111–122의 영구 삭제 진입점은 휴지통뿐이다. 서버도 같은 규칙을 강제한다.
- 프로젝트에 딸린 문서·에피소드·버전·최신화 작업본이 함께 사라진다. FK CASCADE가 처리한다(§7).
- `outbox_events`에는 FK가 없어 고아 행이 남는다. 이번 범위에서 outbox를 쓰지 않으므로 문제가 없지만, 이벤트 발행이 붙을 때 정리 규칙을 정해야 한다.

## 6. 오류 응답

authentication 서비스와 **같은 세 필드**다. 두 서비스가 같은 gateway를 지나 같은 프론트엔드로 답하므로, 오류 표현이 갈리면 클라이언트가 서비스별 분기를 갖게 된다.

```json
{ "code": "PROJECT_NAME_TAKEN", "message": "Project name is already in use.", "next_action": "NONE" }
```

`code`가 계약이다. `message`는 **진단용 영어 문자열이고 화면에 그대로 띄우지 않는다.** 사용자 문구는 프론트가 `code`로 고른다. 서버 메시지를 그대로 노출하면 원인을 임의로 추측하지 않는다는 오류 UX 원칙(요구사항 §9)을 지킬 수 없다.

`field` 힌트를 두지 않는다. authentication 서비스의 관례가 **잘못된 항목마다 고유 코드**를 주는 것이고(`INVALID_DISPLAY_NAME`), 프론트가 분기할 값이 하나면 충분하다.

| HTTP | `code` | 언제 | 프론트 `ServiceError` |
|---|---|---|---|
| 400 | `INVALID_REQUEST` | 읽을 수 없는 본문, 모르는 필드, 수정 필드 없음, 신원 헤더 이상 | `validation` |
| 400 | `INVALID_PROJECT_NAME` | 빈 이름, 255자 초과 | `validation` |
| 400 | `INVALID_PROJECT_DESCRIPTION` | 500자 초과 | `validation` |
| 401 | `USER_CONTEXT_REQUIRED` | 신원 헤더 없음 | (세션 만료 처리로 보냄) |
| 404 | `PROJECT_NOT_FOUND` | 없음·남의 것·휴지통·UUID 아닌 경로 | `not-found` |
| 409 | `PROJECT_NAME_TAKEN` | 활성 프로젝트에 같은 이름 | `duplicate` |
| 409 | `PROJECT_NOT_TRASHED` | 휴지통 밖에서 영구 삭제 시도 | `validation` |
| 500 | `INTERNAL_ERROR` | 그 외 | `unknown` |
| 연결 실패 | — | | `network` |

`next_action`은 `NONE` 또는 `RELOGIN`만 쓴다. authentication의 `RESTART_LOGIN`·`RETRY_LATER`는 로그인·토큰 흐름의 것이라 content에 해당이 없다.

**요청 JSON은 엄격하다.** 모르는 필드와 타입이 어긋난 값은 버리지 않고 `INVALID_REQUEST`로 거절한다. 조용히 버리면 클라이언트의 오타가 "저장은 됐는데 값이 안 바뀐다"로 나타난다. 이것도 authentication 서비스와 같다.

gateway의 업스트림 장애 응답(`{"error":"upstream_unavailable","upstream":"content"}`)은 **모양이 다르다.** gateway 자신의 오류이고 도메인 실패가 아니므로 어댑터는 이것을 `network`로 본다.

## 7. 스키마 보정 — `V3__project_constraints.sql`

`V1`의 `projects`는 이 API를 그대로 받기에 세 군데가 모자란다.

```sql
-- (1) 제목 상한. 요구사항 §2.2는 1~255자인데 컬럼이 VARCHAR(200)이다.
ALTER TABLE projects ALTER COLUMN name TYPE VARCHAR(255);

-- (2) 이름 중복 방지. "같은 사용자의 활성 프로젝트 안에서 대소문자 무시 중복 불가"를
--     애플리케이션 선검사만으로 막으면 동시 요청 두 개가 통과한다.
CREATE UNIQUE INDEX uq_projects_owner_active_name
    ON projects (owner_user_id, lower(name))
    WHERE trashed_at IS NULL;

-- (3) 영구 삭제. projects를 참조하는 FK 셋에 CASCADE가 없어
--     DELETE FROM projects 가 외래 키 위반으로 실패한다.
ALTER TABLE document          DROP CONSTRAINT fk_document_project;
ALTER TABLE document          ADD  CONSTRAINT fk_document_project
    FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE CASCADE;
ALTER TABLE episode_folders   DROP CONSTRAINT fk_episode_folders_project;
ALTER TABLE episode_folders   ADD  CONSTRAINT fk_episode_folders_project
    FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE CASCADE;
ALTER TABLE refresh_runs      DROP CONSTRAINT fk_refresh_runs_project;
ALTER TABLE refresh_runs      ADD  CONSTRAINT fk_refresh_runs_project
    FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE CASCADE;
```

`document`·`refresh_runs` 아래의 테이블(`document_properties`, `document_relations`, `document_versions`, `refresh_document_drafts`)은 이미 `ON DELETE CASCADE`이므로 연쇄가 끝까지 간다.

삭제를 애플리케이션에서 순서대로 수행하는 대안도 있으나, 테이블이 늘어날 때마다 삭제 코드를 고쳐야 하고 빠뜨리면 조용히 고아 행이 남는다. 제약은 DB에 두는 편이 낫다.

`V1`·`V2`는 이미 운영 DB에 적용됐으므로 **고치지 않는다.** 고치면 Flyway 체크섬 검증이 실패해 pod가 뜨지 않는다(`TABLE_AND_LOGIC.md` §9).

## 8. 구현 — `loresentry-content#3`

계획대로 구현했고, 아래 §8.1~§8.3은 실제 들어간 코드를 기술한다. 계획과 달라진 점은 하나뿐이다.
`@WebMvcTest`로 컨트롤러를 따로 떼어 검증하려던 것을 **통합 테스트 하나로 합쳤다.** 어차피 Testcontainers가
필요한 빌드이고, 신원 헤더·오류 매핑·상태 전이는 실제 HTTP와 실제 DB를 함께 지나야 의미가 있다.

| | |
|---|---|
| 신규 | `project/` 8개 파일, `web/` 6개 파일, `V3__project_constraints.sql` |
| 테스트 | `ProjectApiTest` 16개, `MigrationTest` 4개 추가 |
| 미포함 | gateway 릴레이, 프론트 어댑터 |

## 8-A. 계획

### 8.1 계층

JPA를 넣지 않는다. content의 `build.gradle`에는 `spring-boot-starter-jdbc`만 있고, `DbHealthController`가 이미 `JdbcClient`를 쓴다. 테이블 하나를 다루는 첫 도메인 작업에 ORM과 그 매핑 규칙을 한꺼번에 들이는 것은 이르다. JPA 도입은 문서 본문·관계처럼 그래프형 쓰기가 생길 때 다시 판단한다.

```text
com.loresentry.content.project
    ProjectController        HTTP 경계. 요청 DTO → 서비스, 도메인 → 응답 DTO
    ProjectService           검증, 트랜잭션, 상태 전이 규칙
    ProjectRepository        JdbcClient. SQL과 RowMapper
    Project                  record. 도메인 표현
    ProjectRequests          CreateProjectRequest, UpdateProjectRequest
    ProjectResponse          JSON 표현(§3.2)

com.loresentry.content.web
    ContentFailure           Reason enum. HTTP 상태를 모른다
    ErrorResponses           Reason → (status, message, next_action)
    ContentExceptionHandler  @RestControllerAdvice. 예외 → §6 오류 바디
    CurrentUserArgumentResolver   X-User-Id → UUID. 없으면 401, 값이 이상하면 400
```

- 검증은 `ProjectService`에 둔다. 컨트롤러는 형식(JSON 파싱, UUID 모양)만 본다.
- 중복 이름은 **선검사하지 않고** 유니크 인덱스 위반(`23505`)을 잡아 `PROJECT_NAME_TAKEN`으로 바꾼다. 선검사는 동시 요청을 막지 못하고, 인덱스가 있으면 선검사는 중복 코드다.
- `trim()`은 저장 직전에 한 번만 한다. 앞뒤 공백을 제거한 값이 곧 저장값이고 중복 검사 대상이다(요구사항 §3.2).

### 8.2 순서

1. `V3` 마이그레이션 + `MigrationTest`에 제약 검증 추가 (§7)
2. `CurrentUserArgumentResolver`, `ContentExceptionHandler` — 이후 모든 도메인 엔드포인트가 공유한다
3. `ProjectRepository` + Testcontainers 통합 테스트
4. `ProjectService` — 검증과 상태 전이
5. `ProjectController` + `@WebMvcTest`
6. README의 엔드포인트 표·"Not implemented yet" 갱신
7. (별도 작업) gateway 릴레이(§11) + 프론트 어댑터 — 둘 다 이후에 끝났다

### 8.3 테스트

`MigrationTest`가 이미 Testcontainers `postgres:18`로 Flyway를 돌린다. repository 테스트는 같은 컨테이너 방식을 쓴다. Boot 4에서 `@WebMvcTest`는 `org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest`다(Boot 3 경로가 아니다).

반드시 덮을 것:

- 이름 trim, 1자·255자 경계, 256자 거절
- 같은 이름 대소문자만 다른 생성 → 409
- 휴지통에 넣은 이름과 같은 이름으로 생성 → **성공해야 한다**(부분 인덱스가 활성만 본다)
- 그 상태에서 복원 → 409
- 다른 `X-User-Id`로 조회·수정·삭제 → 404
- 휴지통 이동 두 번 → 204, `trashed_at` 불변
- 활성 프로젝트 `DELETE` → 409 `PROJECT_NOT_TRASHED`
- 문서·에피소드가 딸린 프로젝트 영구 삭제 → 연쇄 삭제 확인

## 9. 결정 사항

2026-09-22에 네 가지를 확정했다. 본문은 모두 이 결정을 반영한 상태다.

| # | 항목 | 결정 |
|---|---|---|
| 1 | API 필드명 | **`name`.** 테이블과 `TABLE_AND_LOGIC.md`의 용어를 따른다. 프론트 모델은 `title`이므로 어댑터 한 곳에서 `name ↔ title`을 매핑한다 |
| 2 | 제목·설명 상한 | **제목 255자, 설명 500자.** 요구사항 §2.2가 기준이다. `V3`에서 `name`을 `VARCHAR(255)`로 확장하고, 프론트 프로젝트 설정 화면의 40/200을 255/500으로 맞춘다(§9.1) |
| 3 | `icon` | **서버에 두지 않는다.** 사용자가 고르는 UI가 없으므로 프론트 어댑터가 `id`에서 결정론적으로 고른다. 선택 기능이 생기면 그때 컬럼을 추가한다 |
| 4 | gateway 노출 시점 | **인증과 같은 작업으로 묶는다.** 이번에는 content에만 구현하고 port-forward/Telepresence로 검증한다(§4.3) |

아직 정하지 않았고, 이번 범위를 막지도 않는 것.

| 항목 | 지금의 처리 | 나중에 |
|---|---|---|
| 휴지통 보존 기간 | 무기한 보관 | 기한을 두면 자동 정리 배치와 "N일 후 삭제" 표시가 함께 필요하다 |
| 영구 삭제와 다른 서비스 | ai_chat의 세션은 남는다 | 요구사항 §3.2는 함께 삭제를 요구한다. `ProjectDeleted` 이벤트가 생길 때 해결한다 |
| 목록 페이지네이션 | 두지 않는다 | `{"projects": [...], "nextCursor": ...}`로 확장한다. 응답을 객체로 감싼 이유다 |

### 9.1 이 결정이 프론트엔드에 남기는 일

`loresentry-frontend`에서 함께 고쳐야 하는 것이다. 백엔드 구현과 독립적으로 진행할 수 있다.

- `features/project-settings/project-settings-view.tsx`의 `TITLE_MAX = 40`, `DESCRIPTION_MAX = 200` → **255 / 500**. 지금은 같은 값에 대해 생성 다이얼로그(255/500)와 설정 화면(40/200)이 서로 다른 한도를 강제한다. 설정 화면에서 기존 제목이 잘려 보이는 버그이기도 하다.
- `services/mock/projects.ts`에 설명 길이 검증이 없다. 서버가 400을 주게 되므로 mock도 같은 규칙을 넣는다.
- `services/api/projects.ts`(신규 어댑터)에서 `name → title` 매핑, `icon`을 `id` 해시로 파생, `lastFile`은 서버 값(`null`)을 그대로 쓴다.

---

## 10. authentication 서비스와의 정렬 — `loresentry-content#4`

`loresentry-authentication`의 `deliverable/LOREKEEPER-506` 브랜치에 Google OAuth·토큰·계정 API가 전부 구현되어 있다. 그 서비스가 이미 같은 gateway를 지나 같은 프론트엔드로 답하므로, **두 서비스가 어긋난 지점은 content가 옮겼다.** 나중에 합치는 비용이 지금 옮기는 비용보다 크다.

| 항목 | authentication (구현됨) | content (이전) | 조치 |
|---|---|---|---|
| 신원 헤더 | `X-User-Id` | `X-Lore-User-Id` | content 변경 |
| 헤더 없음 | `401 USER_CONTEXT_REQUIRED` | `401 unauthenticated` | content 변경 |
| 헤더 값 이상 | `400 INVALID_REQUEST` | `401` | content 변경 |
| 헤더 중복 | `400 INVALID_REQUEST` | 첫 값을 씀 | content 변경 |
| 비정규 UUID | 왕복 비교로 거절 | `trim()` 후 허용 | content 변경 |
| 오류 본문 | `{code, message, next_action}` | `{error, message, field}` | content 변경 |
| 오류 코드 표기 | `SCREAMING_SNAKE` | `lowercase` | content 변경 |
| 항목별 오류 | 고유 코드(`INVALID_DISPLAY_NAME`) | `field` 힌트 | content 변경 |
| JSON 필드 | snake case | camelCase | content 변경 |
| 모르는 필드 | 거절 | 무시 | content 변경 |

### 10.1 아직 정하지 않은 것

- **경로 접두사.** authentication은 자기 경로를 `/auth/**`로 스스로 접두사를 붙인다(`/auth/users/me`). content는 `/projects`로 접두사가 없다. gateway가 경로를 하나씩 명시 선언하므로 지금 당장 깨지지는 않지만, 규약이 서비스마다 다르면 gateway 라우팅 표에 특례가 생긴다. gateway 구현 시 한쪽으로 정한다.
- **`next_action` 어휘.** authentication은 `NONE`·`RESTART_LOGIN`·`RELOGIN`·`RETRY_LATER`를 쓰고 content는 `NONE`·`RELOGIN`만 쓴다. 프로젝트 목록이 낡았을 때 쓸 `REFRESH_LIST` 같은 값이 필요할지는 프론트가 이 필드를 실제로 분기에 쓸 때 정한다.
- **`Cache-Control: no-store`.** authentication은 계정·토큰 응답에 붙인다. 프로젝트 데이터도 사용자 데이터이므로 붙일지, 브라우저 캐시는 BFF가 맡을지 정해야 한다.

### 10.2 docs에 남은 불일치 — content 범위 밖

읽는 과정에서 확인했고, 이 문서가 고칠 것은 아니다.

- **`auth/INTERNAL_API.md`가 없다.** authentication README가 "The API contract is maintained in the Loresentry docs repository at `auth/INTERNAL_API.md`"라고 가리키는데 이 저장소에 그 파일이 없다. 위 표의 근거가 지금은 코드뿐이다.
- **`auth_sessions` 테이블을 쓰지 않는다.** [`TABLE_AND_LOGIC.md`](TABLE_AND_LOGIC.md) §3.2는 refresh token 해시를 PostgreSQL에 두는데, 구현은 refresh token과 OAuth state를 **Redis(`auth-valkey`)**에 둔다. 마이그레이션 이름도 `V1__create_auth_accounts.sql`로 달라졌다. `TABLE_AND_LOGIC.md` §3을 구현에 맞춰 고쳐야 한다.
- **`auth-valkey`가 "순수 캐시"가 아니다.** [`INFRA_AND_CICD.md`](INFRA_AND_CICD.md) §18과 [`LORE_SENTRY_PROJECT_CONTEXT.md`](LORE_SENTRY_PROJECT_CONTEXT.md) §14.1은 `auth-valkey`를 영속성 없는 캐시(`emptyDir`)로 기록한다. 그런데 refresh token과 OAuth state가 거기 있으면 **pod가 재시작되면 전원이 로그아웃된다.** 영속성을 줄지, 그 동작을 받아들일지 결정이 필요하다.

---

## 11. gateway 중계 — `loresentry-gateway#2`, `#3`

### 11.1 경로를 다시 쓰지 않는다

공개 `/projects`가 content의 `/projects`다. 매핑 표를 유지할 일이 없고, 프론트엔드가 호출하는 경로와 이 문서의 경로가 같다.

`/api` 접두사를 붙이지 않았다(§3.1의 판단을 뒤집었다). 실제로 붙여 보니 `api.loresentry.com/api/projects`가 되고, gateway는 경로를 다시 쓰는 코드를 갖게 되는데 그 코드가 사는 이유가 접두사 하나뿐이었다.

### 11.2 라우트 단위가 아니라 네임스페이스 단위로 선언한다

```java
@RequestMapping(path = {"/projects", "/projects/**"}, method = {...})
@RequestMapping(path = {"/files", "/files/**"},       method = {...})
@RequestMapping(path = {"/episodes", "/episodes/**"}, method = {...})
```

content가 프론트에 빚진 엔드포인트가 스무 개를 넘고, **그중 두 서비스의 응답을 합쳐야 하는 것이 하나도 없다.** 라우트마다 메서드를 하나씩 두면 content에 엔드포인트를 더할 때마다 gateway에도 옮겨 적어야 하고, 빠뜨리면 조용히 404가 된다. 실제로 §0.1의 파일·문서·검색 18개를 추가할 때 gateway는 **한 줄도 고치지 않았다.**

그래도 `/**` 하나로 아무 경로나 흘리지는 않는다. 각 매핑이 **어느 서비스가 어느 네임스페이스를 소유하는지 명시**하므로 content의 `/health`·`/health/db`·`/`는 공개되지 않고, 신원을 싣는 자리도 남는다. 조합이 필요한 경로가 생기면 그 경로만 더 구체적인 매핑으로 선언하면 된다 — Spring이 더 구체적인 패턴을 먼저 고른다.

### 11.3 요청 헤더는 허용 목록이다

```text
If-Match · If-None-Match · X-Save-Id     전달한다 (API 계약의 일부)
X-User-Id                                 거른 뒤 gateway 가 자기 값을 넣는다
Authorization · Cookie · 그 외             전달하지 않는다
```

**처음에는 `X-User-Id`만 싣고 아무것도 전달하지 않았다.** 그래서 `If-Match`가 content에 닿지 않아 **모든 문서 저장이 400이었다.** 같은 `PUT`이 port-forward로는 200이고 gateway로는 400인 것을 비교해 찾았다(§4.3).

허용 목록인 이유는 통째로 넘기면 클라이언트가 `X-User-Id`를 끼워 넣을 수 있고, 인증이 붙은 뒤에는 gateway가 소비해야 할 `Authorization`·`Cookie`까지 도메인 서비스로 새어 나가기 때문이다.

### 11.4 응답은 손대지 않는다

상태 코드와 본문을 그대로 통과시킨다. **오류 본문도 그렇다** — 이유를 아는 쪽은 업스트림이고, gateway가 다시 포장하면 그 이유가 지워지고 클라이언트는 두 겹을 벗겨야 한다.

`Location`과 `Cache-Control`은 유지하고, `Transfer-Encoding`·`Content-Length`처럼 서블릿 컨테이너가 다시 계산하는 헤더는 버린다.

업스트림에 **닿지 못한** 경우만 gateway의 실패다.

```json
{ "code": "UPSTREAM_UNAVAILABLE", "message": "...", "next_action": "RETRY_LATER", "upstream": "content" }
```

### 11.5 구현이 잡아낸 것 두 개

- **URI 이중 인코딩.** 서블릿이 주는 경로·쿼리는 이미 인코딩된 값인데 `UriBuilder`가 한 번 더 인코딩해 `%EC`가 `%25EC`가 됐다. 한글 검색어가 content에 깨져서 도착했을 것이다. 업스트림 클라이언트의 인코딩을 끄고, 설정과 테스트가 같은 헬퍼로 클라이언트를 만들게 했다.
- **`RestClient.header()`는 덮어쓰지 않고 추가한다.** "신원을 마지막에 실어 이긴다"는 방식이 값을 두 개 보냈다. content가 중복 헤더를 거절하므로 안전하게 실패했지만, 불변식이 순서에 의존하면 안 된다. 이제 `forward` 안에서 신원 헤더를 걸러낸다.

---

## 12. 남은 작업

이 문서가 계속 추적하는 목록이다. 끝난 것도 남겨 둔다 — 무엇이 어떤 순서로 풀렸는지가 다음 순서를 정하는 근거다.

| # | 작업 | 저장소 | 상태 |
|---|---|---|---|
| 1 | 프로젝트 CRUD 8개 + `V3` | content | ✅ `#3` |
| 2 | authentication 계약 정렬 (헤더·오류·snake case) | content | ✅ `#4` |
| 3 | 파일·문서·버전·검색 18개 + `V4` `V5` | content | ✅ `#5` |
| 4 | gateway 중계 + 신원 주입 (§11) | gateway | ✅ `#2` `#3` |
| 5 | 프론트 API 어댑터 (포트 5개) | frontend | ✅ `#5` |
| 6 | **gateway JWT 검증** (§0.4) | gateway | ☐ **다음** |
| 7 | authentication `deliverable/LOREKEEPER-506` 을 main 으로 + `/auth/**` 중계 | auth · gateway | ☐ |
| 8 | 프론트 설정 화면 글자 수 40/200 → 255/500 (§9.1) | frontend | ☐ |
| 9 | 즐겨찾기·메모·작업공간 상태를 서버에 둘지 결정, 테이블 (§0.2) | docs · content | ☐ |
| 10 | 폴더 모델 확정 → 사용자 섹션 (§9-1) | docs · content | ☐ |
| 11 | `outbox_events` 쓰기 + 토픽 설계 | docs · content | ☐ |
| 12 | Outbox publisher · graph-rag Inbox consumer → `GraphService` | content · graph-rag | ☐ |
| 13 | AI 최신화 (`RefreshService`) | content · ai-chat | ☐ |
| 14 | AI 챗 + 스트리밍 패스스루 (타임아웃 2개 상향) | ai-chat · gateway | ☐ |
| 15 | 내보내기 DOCX·HWP | content | ☐ |
| 16 | `last_file` 채우기 | content | ☐ |
| 17 | `auth/INTERNAL_API.md`, `TABLE_AND_LOGIC.md` §3 구현 반영 (§10.2) | docs | ☐ |
| 18 | `auth-valkey` 영속성 결정 — refresh token 이 거기 있으면 재시작 시 전원 로그아웃 (§10.2) | gitops | ☐ |
| 19 | `config.json` 의 `dataSource` 를 `api` 로 할지 결정 | frontend | ☐ |

**6번이 다음이다.** 그것 하나가 나머지 전부의 전제다 — 인증 없이 기능을 더 얹으면 공개 쓰기 표면만 넓어진다.
