# Content — 프로젝트 CRUD API 계획

> 작성일: 2026-09-22
> 상태: **계획, 결정 완료.** §9의 네 가지를 확정했고 구현 전이다. 구현이 끝나면 이 문서를 구현 기록으로 갱신한다.
> 범위: `projects` 테이블만 다루는 CRUD. 파일·문서·메모·그래프·Kafka는 제외한다.
> 전제: [`TABLE_AND_LOGIC.md`](TABLE_AND_LOGIC.md) §4.2, [`CORE_FEATURE_REQUIREMENTS.md`](CORE_FEATURE_REQUIREMENTS.md) §2.2, [`LORE_SENTRY_PROJECT_CONTEXT.md`](LORE_SENTRY_PROJECT_CONTEXT.md) §3.2

## 1. 이 작업의 경계

content 서비스의 첫 도메인 로직이다. 지금 content에는 `/health`·`/health/db`와 `MediaStorageService`뿐이고, Flyway로 만든 테이블 10개를 읽고 쓰는 코드가 없다.

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
gateway 공개 : /api/projects  (인증이 붙은 뒤에 추가, §4.3)
```

`/api/v1` 접두사를 두지 않는다. 이 API의 소비자는 우리 프론트엔드 하나이고, 두 버전을 동시에 운영할 계획이 없다. 쓰지 않을 계층을 미리 넣지 않는 것이 이 프로젝트의 기존 판단과 같다(`LORE_SENTRY_PROJECT_CONTEXT.md` §5.4의 NGINX 제외 논리). 나중에 버전이 필요하면 gateway에 경로 한 줄을 더하면 된다.

### 3.2 표현 — `Project`

```json
{
  "id": "0199a3f2-8c41-7c2a-9f3d-2b7e1c4a5d60",
  "name": "유리 정원의 기록",
  "description": "유리 온실에서 시작되는 장편",
  "lastWorkedAt": "2026-09-21T14:03:11+09:00",
  "trashedAt": null,
  "createdAt": "2026-09-01T10:00:00+09:00",
  "lastFile": null
}
```

- **`name`이다, `title`이 아니다.** 테이블 컬럼과 `TABLE_AND_LOGIC.md`가 `name`이다. 프론트 모델은 `title`이므로 어댑터(`services/api/projects.ts`) 한 곳에서 매핑한다(§9-1).
- **`lastWorkedAt`은 지금 `updated_at` 값을 싣는다.** 프로젝트 목록 정렬 기준이다. 문서 저장이 구현되면 같은 트랜잭션에서 `projects.updated_at`을 touch하고, 그때도 API 필드명은 바뀌지 않는다. 별도 컬럼이 필요해지면 그때 나눈다.
- **`lastFile`은 `null` 고정이다.** `document` 테이블이 이번 범위 밖이다. 프론트 모델이 이미 `| null`이므로 화면은 깨지지 않는다. 문서 CRUD가 붙으면 `project_id`의 최근 수정 활성 문서로 채운다.
- **`icon`은 서버에 두지 않는다.** 프론트 모델에 있지만 mock이 생성 순번으로 정하는 표시용 값이고, 사용자가 고르는 UI가 와이어프레임에 없다. 어댑터가 `id`에서 결정론적으로 고른다. 사용자가 아이콘을 고르게 되는 날 컬럼을 추가한다.
- `description`은 **항상 문자열**이다. 비어 있으면 `""`를 저장하고 `""`를 돌려준다. `null`을 쓰지 않으므로 어댑터에 `?? ""`가 필요 없다.
- 시각은 전부 ISO-8601 offset 문자열이다.

## 4. 신원 — 잠정 계약

### 4.1 헤더

`projects.owner_user_id`가 `NOT NULL`이므로 모든 요청은 사용자 신원을 필요로 한다. 인증이 구현될 때까지의 잠정 계약이다.

```text
X-Lore-User-Id: <authentication 서비스의 users.id (UUID)>
```

- content는 이 헤더가 없거나 UUID가 아니면 **`401 unauthenticated`**로 거절한다. 내부 서비스 입장에서 신원 없는 호출은 호출 규약 위반이다.
- content는 이 헤더를 **무조건 신뢰한다.** 검증은 gateway의 책임이다(`INFRA_AND_CICD.md` §1-A). 서비스마다 토큰을 검증하면 인증 로직이 4곳으로 복제된다.
- 인증이 붙으면 gateway는 **클라이언트가 보낸 동일 헤더를 먼저 제거하고** 자기가 검증한 값을 넣는다. 이 한 줄이 빠지면 누구나 남의 사용자 ID를 사칭할 수 있다.

### 4.2 소유권

모든 조회·변경은 `owner_user_id = <헤더 값>`으로 한정한다. 남의 프로젝트에 접근하면 **403이 아니라 404**를 돌려준다. 403은 "그 id는 존재한다"를 알려주고, 프론트에도 그 둘을 구분할 오류 코드가 없다(`ServiceError`는 `not-found` 하나뿐).

### 4.3 gateway 릴레이는 지금 붙이지 않는다

`api.loresentry.com`은 현재 전 구간 무인증 공개다. 지금 `/api/projects`를 릴레이하면 gateway가 어떤 사용자 ID를 넣든 **인터넷의 누구나 그 사용자의 프로젝트를 만들고 지울 수 있다.** ALB `inbound-cidrs`는 Argo CD와 공유하는 ALB 전체에 작용하므로 이 경로만 막을 수도 없다(`LORE_SENTRY_PROJECT_CONTEXT.md` §12.3).

그래서 이번 단계의 검증은 클러스터 안에서 한다. 팀원은 이미 두 경로를 쓸 수 있다(`ENVIRONMENT_SETTING.md` §5·§6).

```bash
kubectl port-forward -n prod svc/content-api 8080:80
curl -H 'X-Lore-User-Id: <uuid>' localhost:8080/projects

# 또는 Telepresence 연결 후
curl -H 'X-Lore-User-Id: <uuid>' http://content-api/projects
```

gateway 릴레이와 JWT 검증은 authentication 서비스의 Google OAuth와 함께 하나의 작업으로 묶는다. 프론트 `config.json`의 `dataSource`를 `api`로 바꾸는 것도 그때다.

## 5. 엔드포인트 스펙

공통: 요청·응답 `Content-Type: application/json`, 요청에 `X-Lore-User-Id` 필수.

### 5.1 `GET /projects` — 활성 프로젝트 목록

```text
200  { "projects": [ Project, ... ] }
```

- `trashed_at IS NULL`인 것만, `lastWorkedAt DESC` 정렬이다(요구사항 §2.2 "최근 작업 순").
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
400  validation    이름이 비었거나 255자를 넘음
409  duplicate     같은 사용자의 활성 프로젝트에 같은 이름이 있음
```

- `description`은 선택이다. 필드를 생략하면 `""`로 본다.
- 요구사항 §2.2의 "제목은 필수, 설명은 비어 있어도 생성 가능"이 그대로 규칙이다.

### 5.4 `GET /projects/{projectId}` — 단건 조회

```text
200  Project
404  not_found     없거나, 남의 것이거나, 휴지통에 있음
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
400  validation
404  not_found
409  duplicate
```

- **부분 갱신이다.** 보낸 필드만 바꾸고, 없는 필드는 그대로 둔다.
- `null`은 보내지 않는다. 설명을 지우려면 `""`를 보낸다. JSON `null`과 필드 부재를 구분하는 규칙을 만들지 않기 위해서다.
- 낙관적 잠금(`If-Match`)을 **두지 않는다.** 마지막 저장이 이긴다. 문서 본문과 달리 프로젝트 메타는 짧고, 충돌을 병합할 것이 없다. 와이어프레임 70–76도 저장 충돌 화면이 아니라 "저장하지 않고 나가기" 확인만 다룬다.
- 휴지통 프로젝트는 수정할 수 없다(404). 복원 후에 고친다.

### 5.6 `POST /projects/{projectId}/trash` — 휴지통 이동

```text
204
404  not_found
```

- 이미 휴지통에 있으면 **204를 그대로 돌려주고 `trashed_at`을 덮어쓰지 않는다.** 재시도가 안전해야 하고, 덮어쓰면 휴지통 정렬 순서가 흔들린다.
- `PATCH`의 필드가 아니라 별도 경로다. 상태 전이는 "무엇을 바꿨다"가 아니라 "무슨 일이 일어났다"이고, 이름 수정과 같은 요청에 섞이면 안 된다.

### 5.7 `POST /projects/{projectId}/restore` — 복원

```text
200  Project
404  not_found
409  duplicate     복원하려는 이름이 활성 프로젝트와 겹침
```

- 이미 활성이면 현재 상태를 200으로 돌려준다(멱등).
- **복원은 중복 이름을 만들 수 있다.** 휴지통에 넣은 뒤 같은 이름으로 새 프로젝트를 만들었다면, 복원 시점에 충돌한다. 409로 거절하고 프론트가 안내한다. 자동 개명(`(1)` 붙이기)은 하지 않는다 — 사용자가 모르는 사이에 이름이 바뀐다.

### 5.8 `DELETE /projects/{projectId}` — 영구 삭제

```text
204
404  not_found
409  invalid_state   휴지통에 있지 않음
```

- **휴지통 항목만 지울 수 있다.** 와이어프레임 111–122의 영구 삭제 진입점은 휴지통뿐이다. 서버도 같은 규칙을 강제한다.
- 프로젝트에 딸린 문서·에피소드·버전·최신화 작업본이 함께 사라진다. FK CASCADE가 처리한다(§7).
- `outbox_events`에는 FK가 없어 고아 행이 남는다. 이번 범위에서 outbox를 쓰지 않으므로 문제가 없지만, 이벤트 발행이 붙을 때 정리 규칙을 정해야 한다.

## 6. 오류 응답

```json
{ "error": "duplicate", "message": "project name already exists for this owner" }
```

`message`는 **진단용 영어 문자열이고 화면에 그대로 띄우지 않는다.** 사용자 문구는 프론트가 `error` 코드로 고른다. 서버 메시지를 그대로 노출하면 원인을 임의로 추측하지 않는다는 오류 UX 원칙(요구사항 §9)을 지킬 수 없다.

`validation`은 `field`를 덧붙인다.

```json
{ "error": "validation", "message": "name must be 1..255 characters", "field": "name" }
```

| HTTP | `error` | 프론트 `ServiceError` |
|---|---|---|
| 400 | `validation` | `validation` |
| 401 | `unauthenticated` | (세션 만료 처리로 보냄) |
| 404 | `not_found` | `not-found` |
| 409 | `duplicate` | `duplicate` |
| 409 | `invalid_state` | `validation` |
| 500 | `internal` | `unknown` |
| 연결 실패 | — | `network` |

gateway의 업스트림 장애 응답(`{"error":"upstream_unavailable","upstream":"content"}`)은 이미 같은 모양이다. 어댑터는 이것을 `network`로 본다.

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

## 8. 구현 계획

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
    ApiExceptionHandler      @RestControllerAdvice. 예외 → §6 오류 바디
    CurrentUserArgumentResolver   X-Lore-User-Id → UUID, 없으면 401
```

- 검증은 `ProjectService`에 둔다. 컨트롤러는 형식(JSON 파싱, UUID 모양)만 본다.
- 중복 이름은 **선검사하지 않고** 유니크 인덱스 위반(`23505`)을 잡아 `duplicate`로 바꾼다. 선검사는 동시 요청을 막지 못하고, 인덱스가 있으면 선검사는 중복 코드다.
- `trim()`은 저장 직전에 한 번만 한다. 앞뒤 공백을 제거한 값이 곧 저장값이고 중복 검사 대상이다(요구사항 §3.2).

### 8.2 순서

1. `V3` 마이그레이션 + `MigrationTest`에 제약 검증 추가 (§7)
2. `CurrentUserArgumentResolver`, `ApiExceptionHandler` — 이후 모든 도메인 엔드포인트가 공유한다
3. `ProjectRepository` + Testcontainers 통합 테스트
4. `ProjectService` — 검증과 상태 전이
5. `ProjectController` + `@WebMvcTest`
6. README의 엔드포인트 표·"Not implemented yet" 갱신
7. (별도 작업) gateway 릴레이 + 프론트 `services/api/projects.ts`

### 8.3 테스트

`MigrationTest`가 이미 Testcontainers `postgres:18`로 Flyway를 돌린다. repository 테스트는 같은 컨테이너 방식을 쓴다. Boot 4에서 `@WebMvcTest`는 `org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest`다(Boot 3 경로가 아니다).

반드시 덮을 것:

- 이름 trim, 1자·255자 경계, 256자 거절
- 같은 이름 대소문자만 다른 생성 → 409
- 휴지통에 넣은 이름과 같은 이름으로 생성 → **성공해야 한다**(부분 인덱스가 활성만 본다)
- 그 상태에서 복원 → 409
- 다른 `X-Lore-User-Id`로 조회·수정·삭제 → 404
- 휴지통 이동 두 번 → 204, `trashed_at` 불변
- 활성 프로젝트 `DELETE` → 409 `invalid_state`
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
