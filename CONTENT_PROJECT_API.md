# Content API — 구현 현황과 계약

> 작성일: 2026-09-22 · 최신화: 2026-09-26
> 상태: **서버 세 층은 검증됐고, 프론트는 방금 처음으로 서버와 말하기 시작했다.** `config.json` 이 `api` 여도 앱이 mock 으로 굳어 있던 버그를 브라우저로 잡아 고쳤다(§0.11). Google 로그인 화면까지 실제로 도달하는 것을 확인했고, 계정 승인 한 걸음이 남았다.
> 범위: content 서비스의 HTTP API 전부와, 그것을 외부로 내보내는 gateway 중계. 처음에는 `projects` CRUD 계획서로 시작해 실제 구현 기록으로 자랐다.
> 전제: [`TABLE_AND_LOGIC.md`](TABLE_AND_LOGIC.md) §4, [`CORE_FEATURE_REQUIREMENTS.md`](CORE_FEATURE_REQUIREMENTS.md), [`LORE_SENTRY_PROJECT_CONTEXT.md`](LORE_SENTRY_PROJECT_CONTEXT.md) §3

---

# 0. 현재 상태 — 한눈에

```text
브라우저 → Cloudflare → ALB → gateway(BFF) ──▶ content ─▶ RDS(content)
                                 │  쿠키·CSRF·AT 검증      38개 엔드포인트
                                 ├──▶ authentication ─▶ RDS(auth) · auth-valkey(세션)
                                 └── X-User-Id 는 gateway 가 **설정**한다. 클라이언트 값은 버려진다
     프론트 포트 13개 중 9개가 실제 API · 4개는 서버 없어 mock (§0.10)
```

| 저장소 | 배포 | 테스트 |
|---|---|---|
| `loresentry-content` | Flyway `V7` | 140 |
| `loresentry-gateway` | BFF 배포됨, prod 프로필 기동 중 | 214 |
| `loresentry-frontend` | 배포됨, **기본값 `api`** (`?data=mock` 으로 되돌림) | 234 |

## 0.1 구현된 엔드포인트 38개

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

**메모 5개 · 즐겨찾기 3개 · 작업공간 상태 2개** — `loresentry-content#9`, `V7`

| Method | Path | 비고 |
|---|---|---|
| `GET` | `/projects/{id}/memos?scope=project\|file[&document_id=]` | 최근 수정 순 |
| `POST` | `/projects/{id}/memos` | 빈 본문 허용 — 카드를 먼저 만들고 쓴다 |
| `PATCH DELETE` | `/memos/{mid}` | 보낸 필드만 갱신 |
| `GET` | `/projects/{id}/favorites` | `{file_ids}`, 휴지통 제외 |
| `PUT DELETE` | `/projects/{id}/favorites/{fid}` | 양방향 멱등, 매번 전체 목록 |
| `GET PUT` | `/projects/{id}/workspace-state` | 불투명 JSON. 처음이면 `layout: null` |

메모는 **한 테이블에 `scope` 로** 나눴다. 프로젝트 메모와 파일 메모는 붙는 대상만 다르고 본문·수정 시각·삭제가 같아서, 나누면 목록 질의만 두 벌이 된다. `CHECK` 가 둘을 섞이지 않게 한다.

즐겨찾기는 휴지통 파일을 목록에서 빼지만 **행은 남긴다** — 복원하면 별이 돌아온다.

작업공간 상태는 서버가 해석하지 않는다. 해석하면 탭 모델이 바뀔 때마다 마이그레이션이 필요하다.

**이미지 업로드 3개** — `loresentry-content#6`

| Method | Path | 비고 |
|---|---|---|
| `POST` | `/projects/{id}/images` | presigned 티켓 발급 + `image` 행 `PENDING` |
| `POST` | `/projects/{id}/images/{iid}/complete` | `HeadObject` 로 확인 후 `COMMITTED`. 멱등 |
| `GET` | `/projects/{id}/images/{iid}` | `public_url` 은 `COMMITTED` 이후에만 |

브라우저가 S3 에 직접 올리므로 서버는 바이트가 도착했는지 알 수 없다. 그래서 `complete` 가 클라이언트의 말을 믿지 않고 `HeadObject` 로 존재와 크기를 확인한다. `PENDING` 상태에서 `public_url` 을 주지 않는 이유는, 그 주소를 문서에 넣은 뒤 업로드가 실패하면 깨진 이미지가 남기 때문이다.

이미지는 **프로젝트와 id 를 함께** 조회한다. id 만으로 찾으면 같은 소유자의 다른 프로젝트를 통해서도 보인다.

## 0.2 구현되지 않은 엔드포인트 — **main 기준**

> **2026-09-24 정정.** 앞선 판에서 authentication 을 "브랜치에만 있다"고 적었는데 **틀렸다.** 그 사이 main 으로 머지됐다(PR 없이 main 직접 푸시, `e9d5b5b`). 저장소를 다시 읽고 이 표를 고쳤다. 아래는 **각 저장소 main 기준**이다.

### 서버 코드가 아예 없는 것

| 포트 | 없는 엔드포인트 | 어디에 | 막는 것 |
|---|---|---|---|
| `GraphService` (1) | 프로젝트 관계 그래프 | graph-rag | **스켈레톤이다**(`/`·`/health`·`/health/db` 뿐). Neptune 투영본을 만들 Outbox publisher·Inbox consumer 가 없다 |
| `ChatService` (6) | 세션 CRUD·메시지·스트리밍 | ai-chat | **스켈레톤이다.** LLM provider 미정. ALB `idle_timeout` 60초 + gateway `read-timeout` 10초를 올려야 SSE 가 산다 |
| `RefreshService` (4) | 최신화 실행·조회·반영·폐기 | content | AI 추출 모듈. 반영이 관계를 바꿔 `outbox_events` 를 만든다 |
| `FileService` 섹션 (2) | 생성·삭제 | content | **만들지 않기로 했다.** 에피소드만 사용자 생성 폴더다 |
| `DocumentService.export` (1) | PDF·DOCX·HWP | content | 서버 렌더링 없음. PDF 는 화면 인쇄로 대체 |
| `HelpService` (1) | 사용 가이드 | 미정 | 정적 번들로 갈지 서버에서 받을지 |

### 서버에는 있으나 브라우저가 쓸 수 없는 것

`AuthService`(3)·`AccountService`(2)가 여기 속한다. **authentication main 에 엔드포인트 6개가 다 있고, BFF 가 브라우저용으로 감싸는 것도 머지됐고, 프론트 어댑터도 붙었다.** 남은 것은 운영 설정뿐이다(§0.7).

```text
POST /auth/oauth/google/prepare      POST /auth/tokens/refresh    GET   /auth/users/me
POST /auth/oauth/google/callback     POST /auth/tokens/revoke     PATCH /auth/users/me
```

두 가지가 막고 있다.

1. **운영에서 기동하지 못한다.** `build-5-1` pod 가 `CrashLoopBackOff`(재시작 14회)이고 `build-4-1` 구버전이 트래픽을 받는다. 그래서 `/auth/**` 가 전부 404다. 원인은 설정 누락이다.

   ```text
   APPLICATION FAILED TO START
     auth.google.clientId / clientSecret / redirectUri  must not be blank
   ```

   GitOps 의 `authentication` Deployment 에 `AUTH_GOOGLE_*` 3개가 없다. `AUTH_JWT_*` 3개도 같은 이유로 곧 걸린다. **CI·Argo CD·`/health` 가 모두 초록인데 구버전이 도는 형태**여서 아무도 알려주지 않는다 — `readinessProbe` 가 깨진 pod 를 트래픽에서 빼 준 것은 설계대로 동작한 결과다.

2. **브라우저가 부를 수 있는 형태가 아니다.** auth 의 `/auth/**` 는 BFF 전용 내부 API 다. 브라우저는 쿠키·CSRF·리다이렉트가 필요하고, 그것은 gateway 의 일이다(§0.3-A).

### content 안에서 코드가 없는 것

| 항목 | 상태 |
|---|---|
| ~~`last_file`~~ | **[해소됨]** `loresentry-content#6`. 가장 최근에 수정된 활성 문서다(§0.4) |
| `outbox_events` 쓰기 | 테이블만 있다. 토픽 설계 미확정(`LORE_SENTRY_PROJECT_CONTEXT.md` §19.3) |
| ~~이미지 업로드 엔드포인트~~ | **[해소됨]** `loresentry-content#6`. `V6` 이 `image` 테이블을 만든다 |
| 남은 `PENDING` 이미지 정리 | 배치가 없다. 행은 `ix_image_pending` 으로 찾을 수 있게 해 뒀다 |
| `refresh_runs` · `refresh_document_drafts` | 테이블만 있다. 설계는 `TABLE_AND_LOGIC.md` §7 |

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
| **JWT 검증** | ❌ **없다.** `ClientHeaderIdentityResolver` 가 클라이언트의 `X-User-Id` 를 그대로 믿는다 → §0.6 |
| auth 경로 중계 | `/auth/**` 릴레이가 없다. 프로브 `GET /auth` 뿐이다 |
| composition | 없다. **프론트가 요구하는 것도 없다** — 54개 포트 메서드 중 두 서비스를 합쳐야 하는 것이 하나도 없다 |
| AI 스트리밍 패스스루 | 없다. 타임아웃 두 개를 올려야 한다 |
| retry · circuit breaking · 관측성 | 없다 |

## 0.3-A gateway BFF 가 내 중계를 대체했다 — **[해소됨]**

`loresentry-gateway` 의 `deliverable/LOREKEEPER-555` 브랜치(2026-09-24)가 브라우저용 BFF 계층을 만들고 있다. main 에는 문서만 들어왔고(`docs/EXTERNAL_API.md`, `BROWSER_SECURITY.md`, `auth/*`) **코드는 아직 브랜치에 있다.**

| 그 브랜치가 더하는 것 | |
|---|---|
| `security/CsrfFilter` | 인증보다 먼저 CSRF 검증 |
| `web/auth/AuthCookies` · `SensitiveResponseFilter` | AT·RT 쿠키 정책, 민감 응답 처리 |
| `config/EnvironmentConfiguration` | 환경 분리 + CORS 제한 |
| `web/content/ContentApiController` · `ContentDtos` | **라우트마다 타입 있는 컨트롤러와 DTO** |

**그 브랜치는 `web/ContentRelayController` 를 삭제한다.** 즉 §11의 설계 판단이 뒤집힌다.

| | 내가 넣은 것 (main) | 그 브랜치 |
|---|---|---|
| 선언 단위 | 네임스페이스 3개 | 라우트마다 하나 |
| 본문 | `byte[]` 무가공 통과 | DTO 로 파싱·재직렬화 |
| content 계약 변경 시 | gateway 무수정 | gateway DTO 도 고쳐야 한다 |
| 계약 위반 감지 | content 가 답한 대로 통과 | gateway 가 검증해 거절 |

둘 다 근거가 있다. 내 쪽은 content 에 엔드포인트를 더할 때 gateway 를 고치지 않아도 되고(실제로 18개를 추가하며 한 줄도 안 고쳤다), 저쪽은 외부 API 계약을 gateway 가 문서이자 코드로 들고 있어 content 가 몰래 모양을 바꾸면 잡힌다.

**[결정됨] 라우트별 DTO 로 갔다.** 그 브랜치가 main 에 머지되면서 `ContentRelayController` 가 사라졌다. §11 은 이제 **역사 기록**이고 현재 구조가 아니다.

그리고 그 대가가 바로 나타났다. 내가 나중에 추가한 이미지 3개와 메모·즐겨찾기·작업공간 10개가 **BFF 에 선언되지 않아 404 였다.** `loresentry-gateway#4` 에서 13개를 같은 방식으로 선언하고 `docs/CONTENT_API.md` 계약에도 적어, 같은 누락이 조용히 반복되지 않게 했다(총 39개).

## 0.4 프로젝트 "최근 작업" 은 버그였다 — `loresentry-content#6`

`last_worked_at` 이 **프로젝트 자기 행을 고칠 때만** 움직였다. 그래서 원고를 한 시간 써도, 이름만 바꾼 다른 프로젝트가 목록 위에 남았다. 목록은 최근 작업 순이라고 정해 두었으므로(요구사항 §2.2) 이것은 정렬이 요구사항과 달랐던 것이다.

이제 파일·문서 변경이 같은 트랜잭션에서 프로젝트 시각을 올린다. `ProjectActivity` 를 따로 둔 이유는 문서·파일·버전 세 곳이 같은 일을 해야 하고, 그때마다 프로젝트 저장소를 끌어오면 패키지 사이에 순환이 생기기 때문이다.

`last_file` 은 **가장 최근에 수정된 활성 문서**다. 열람 기록 테이블을 두지 않았다 — 사용자가 "그 파일을 작업했다"고 말할 때 뜻하는 것은 편집이다. 휴지통 문서는 제외한다. 열 수 없으므로 "마지막으로 작업한 파일"로 내놓으면 막힌 링크가 된다.

목록 질의는 행마다 질의하지 않고 `LATERAL` 조인을 쓴다. 프로젝트 목록은 여전히 한 문장이다.

## 0.5 검증이 잡은 S3 동작 — `loresentry-content#7`, `#8`

올리지 않고 `complete` 를 부르면 `OBJECT_NOT_UPLOADED`(409)여야 하는데 **`INTERNAL_ERROR`(500)** 였다. 기대된 결과에 스택 트레이스까지 남겼다.

두 단계로 틀렸고, 둘 다 **배포된 서비스에 실제로 호출해서** 찾았다. mock 단위 테스트는 두 번 다 통과했다.

1. **`HeadObject` 는 없는 키에 `NoSuchKeyException` 을 던지지 않는다.** 본문 없는 404 라서 SDK 가 매핑할 것이 없고 평범한 `S3Exception` 이 나온다. 기존 테스트는 **그 경로가 실제로는 내지 않는 예외**를 mock 해서 통과했다.
2. **그 상태 코드가 404 도 아니었다.** `s3:ListBucket` 이 없으면 S3 는 없는 객체를 404 대신 **403** 으로 감춘다 — 볼 수 없는 버킷에 무엇이 있는지 알려 주지 않기 위해서다. 미디어 정책에 객체 권한만 있었으므로 모든 miss 가 403 이었다. 1번을 고칠 때 쓴 테스트가 "403 은 권한 오류이므로 통과시킨다"고 **정반대로 못 박아** 통과했다.

지금은 404 와 403 을 모두 "올라오지 않았다"로 본다. 500 같은 실제 장애는 그대로 올린다 — 장애를 "아직 안 올라왔다"로 바꾸면 클라이언트가 재시도로 고칠 수 없는 것을 영원히 재시도한다.

**남은 조치 하나:** `iam-content-media-policy.json` 에 `s3:ListBucket` 을 넣었지만 **AWS 의 실제 정책에는 아직 반영되지 않았다.** 적용하면 S3 가 제대로 404 를 주고 "없음"과 "권한 없음"의 구분이 다시 살아난다. 지금은 403 분기가 그것을 가리고 있다.

## 0.6 무인증이었던 구간 — **[해소됨, §0.7]**

> 아래는 **2026-09-26 이전**의 상태다. 지금은 AT 검증이 붙어 위조 헤더가 `401 ACCESS_TOKEN_MISSING` 으로 막힌다(§0.7). 어떤 위험을 어떻게 닫았는지 남겨 둔다.

당시 `api.loresentry.com` 의 프로젝트·파일·문서 엔드포인트는 **누구나 읽고 쓸 수 있었다.** 헤더에 아무 UUID나 넣으면 그 사용자의 자료가 됐다.

프론트엔드를 실제 API에 붙이기 위해 의도적으로 택한 단계이고, 되돌리는 방법은 정해져 있다 — gateway의 `IdentityResolver` 구현 하나를 JWT 검증으로 바꾸면 된다. 중계는 그 결과만 읽고, 업스트림 헤더를 **복사가 아니라 설정**하므로 클라이언트가 보낸 값은 자동으로 무력화된다.

그때 반드시 함께 들어가야 하는 것: **클라이언트가 보낸 `X-User-Id` 제거.** 이미 `forward` 가 허용 목록에서 그 헤더를 걸러내지만, 새 경로를 추가할 때 같은 실수를 반복하지 않도록 테스트로 고정해 뒀다.

**검증에 필요한 조각은 이미 다 있다.** authentication 이 RS256 토큰을 발급하고(main), gateway BFF 브랜치가 쿠키·CSRF 를 만들고 있다. 남은 것은 **AT 검증을 `IdentityResolver` 에 꽂는 것**과, 그 앞에 auth 를 실제로 기동시키는 설정이다(§0.2).

---

## 0.7 인증 경계가 살아났다 — 2026-09-26

Secret 을 넣고 `loresentry-gitops#4` 를 머지하자 세 서비스가 처음으로 함께 기동했다.

```text
auth-valkey           1/1  ACL 두 계정으로 기동
authentication-api    1/1  Flyway v2 확인, 처음으로 기동 성공
gateway-api           2/2  prod 프로필 활성
```

공개 API 로 확인한 것.

| 요청 | 결과 |
|---|---|
| `X-User-Id` 헤더 위조 | **`401 ACCESS_TOKEN_MISSING`** — 더 이상 통하지 않는다 |
| 쿠키 없는 `GET /auth/users/me` | `401` |
| `GET /auth/oauth/google/prepare` | `302` → `accounts.google.com`, PKCE·state·nonce 포함 |
| `redirect_uri` | `https://api.loresentry.com/auth/oauth/google/callback` ✅ |
| CSRF 없는 `POST` | `403 CSRF_REJECTED` |
| `loresentry.com` Origin preflight | `200`, `allow-credentials: true`, `x-ls-csrf` 허용 |

만든 리소스는 네 개다. Argo CD 가 추적하지 않으므로 `prune` 에도 지워지지 않는다.

```text
ConfigMap authentication-jwt-public      auth-public.pem
Secret    authentication-runtime         AUTH_GOOGLE_* · AUTH_JWT_* · SPRING_DATA_REDIS_*
Secret    gateway-runtime                BFF_JWT_KEY_ID · BFF_SESSION_REDIS_*
Secret    authentication-valkey-acl      users.acl
```

DB 자격 증명은 **기존 `authentication-db` Secret 을 그대로 쓴다.** 인계 문서의 추천 비밀번호는 새로 생성된 값이고 실제 DB 에 적용되지 않았으므로, 잘 도는 비밀번호를 바꿀 이유가 없었다.

### valkey 설정에서 심각한 버그를 잡았다 — 배포 전에

`aclfile` 과 `requirepass` 를 함께 두면 **valkey 가 `requirepass` 를 무시하고 `default` 를 `nopass +@all` 로 둔다.** 로컬 valkey 9.0.6 에서 확인했다.

```text
비밀번호 없이 ping   → PONG
비밀번호 없이 SET    → OK
기존 readiness 프로브 → AUTH failed
```

즉 그대로 배포하면 **세션 저장소가 무인증 개방되고, 프로브가 실패해 pod 가 Ready 가 되지 않았을 것이다.** 비밀번호가 걸린 것처럼 보이는 설정이라 배포 후에는 알아차리기 어려웠을 형태다.

- `--requirepass` 를 넘기지 않고 접근 통제를 ACL 파일 하나에 맡긴다.
- ACL 에 `user default off` 를 넣는다.
- `default` 를 끄면 프로브도 붙을 수 없으므로 **PING 만 되는 `probe` 계정**을 둔다. 비밀 값이 없어 프로브에 주입할 자격 증명이 없다.
- **ACL 파일은 주석을 허용하지 않는다.** `#` 로 시작하는 줄이 있으면 `Aborting Valkey startup because of ACL errors` 로 기동이 거부된다. 이것도 로컬에서 먼저 걸렸다.

세션이 여기 있으므로 `emptyDir` 도 PVC 로 바꾸고 `maxmemory-policy` 를 `volatile-ttl` 로 바꿨다 — eviction 은 예고 없는 로그아웃이다.

### Argo CD 가 새 커밋을 집지 않았다

자동 동기화가 켜져 있는데도 낡은 리비전에서 `Synced` 로 남아 있었다. `argocd.argoproj.io/refresh=hard` 애노테이션으로 당겼다. 폴링 간격 문제로 보이지만, **자동 동기화를 믿고 기다리는 것만으로는 배포가 반영됐다고 단정할 수 없다**는 점은 기록해 둔다.

### 계정을 잘못 보고 작업할 위험이 있었다

`aws` 기본 프로필이 **다른 계정의 root**(`230361947714`)를 가리킨다. lorekeeper 는 `197179613039` 다.

```text
aws                        → 230361947714 root      ❌ 쓰지 않는다
aws --profile lorekeeper   → 197179613039 SSO       ✅
kubectl --context lore-sentry → exec 에 AWS_PROFILE=lorekeeper 고정  ✅
```

클러스터 접근은 컨텍스트가 프로필을 스스로 고정하므로 안전하지만, **`aws` 를 직접 부를 때는 반드시 `--profile lorekeeper` 를 붙인다.**

---

## 0.8 브라우저 이전 3층 검증 — 2026-09-26

브라우저로 들어가기 전에, 이미 구현했다고 적어 둔 것들을 **실제로 도는 서비스에 대고** 다시 확인했다. 단위 테스트는 내 가정을 확인할 뿐이므로, 세 층을 각각 실물로 통과시켰다.

| 층 | 방법 | 결과 |
|---|---|---|
| content 38개 엔드포인트 | 운영 pod 로 port-forward, 실제 S3 업로드 + CloudFront 읽기 포함 | **64/64** |
| BFF 경유 | gateway 를 로컬 기동, 실제 RS256 AT + 로컬 세션 레코드, 상대는 port-forward 한 운영 content | **29/29** |
| 프론트 어댑터 계약 | 어댑터가 보내는 요청·기대하는 응답 필드를 BFF 응답과 맞춤 | **11/11** |

**고칠 것은 나오지 않았다.** 앞선 절들에서 잡아 고친 것들(§0.4 프로젝트 활동, §0.5 `HeadObject`, §0.6 무인증, valkey ACL)이 마지막 결함이었다.

검증하면서 지킨 것.

- 운영 세션 저장소에는 **아무것도 쓰지 않았다.** 세션 레코드는 로컬 valkey 에 만들었다.
- 비밀 값은 `--from-env-file`·`--from-file` 로만 넘겨 명령줄과 출력에 남지 않게 했다.
- 임시 개인키·AT·kid 파일은 검증 후 지웠다. 로컬 gateway 프로세스와 valkey 컨테이너, port-forward 도 정리했다.
- 클러스터 쓰기는 모두 `197179613039` 계정에서만 일어났음을 확인했다.

### gateway 통합 하네스는 여기서 돌지 않는다

`loresentry-gateway/integration/session/run.py` 는 **Linux 전용**이다. macOS 에서는 auth 컨테이너가 host 네트워크로 `127.0.0.1` 의 PostgreSQL 에 닿지 못해 기동에서 멈춘다. 코드 결함이 아니다. 그리고 그 하네스의 `content_checks.py` 는 **원래 26개 라우트만** 본다 — 내가 더한 13개는 아직 들어 있지 않다(§12-19).

---

## 0.9 문서 편집 경로 점검 — 내보내기에서 결함 하나

편집 경로는 포트 단위로 전부 실제 API 에 붙어 있다.

| 조각 | 상태 |
|---|---|
| 본문·제목·속성·관계 저장 | ✅ `PUT /files/{id}/content`, `If-Match` + `X-Save-Id` |
| 자동 저장 | ✅ 유휴 + 최대 대기 두 타이머, 진행 중이면 이어서 한 번 더 |
| 충돌 | ✅ `409 DOCUMENT_CONFLICT` → 단락 3-way 병합, 실패 시 사용자 선택 |
| 잠금 | ✅ `PUT /files/{id}/lock`, 저장 중 잠김은 `locked` 상태로 분리 |
| 버전 목록·이름 저장·복원·삭제 | ✅ 4개 모두 |
| 내보내기 PDF | ✅ 화면 인쇄 |
| 내보내기 `md`·`txt` | ✅ 브라우저에서 생성 — **이번에 고쳤다** |
| 내보내기 DOCX·HWP | ☐ 서버 필요 (§12-30) |
| 그래프·AI 최신화·챗 | ☐ graph-rag·Kafka 대기 |

**찾은 결함.** API 어댑터의 `export` 가 **모든 형식을 거절했다.** `?data=api` 로 전환하면 mock 에서 되던 `md`·`txt` 내보내기가 오류 토스트로 바뀌고, DOCX·HWP 는 화면의 `catch` 가 "잠시 후 다시 시도해 주세요" 를 띄웠다 — 서버가 만들 수 없는 형식이므로 **영원히 성공하지 않는 재시도다.**

`md`·`txt` 는 서버가 필요 없다(문서 자체가 Markdown 이다). 브라우저에서 만들고, DOCX·HWP 는 거절 대신 **빈 `url`** 을 준다 — 포트 계약에서 "아직 준비되지 않았다"는 뜻이고 화면이 이미 그걸 안내로 바꾼다. `loresentry-frontend#7`.

`md` 의 관계 줄은 대상 문서 **제목**이 필요한데 문서 응답에는 대상 id 만 온다. 관계가 하나라도 있을 때만 파일 목록을 한 번 더 불러 대응을 만든다.

---

## 0.10 배포 기본값이 api 가 됐다 — 남은 미연결 목록

`config.json` 에 `dataSource` 가 없어 **배포된 사이트가 모두 mock 으로 돌고 있었다.** mock 은 씨앗 사용자 `서윤주 / seoyunju@lore.kr` 를 로그인된 것으로 보고하므로, 접속하면 **남의 계정으로 로그인된 것처럼** 보였다. Google 로그인이 안 된 게 아니라 화면이 서버를 본 적이 없었다.

같이 나온 버그: **`?data=api` 가 Google 로그인 왕복에서 사라졌다.** 재정의가 쿼리에만 있었고 BFF 는 고정된 `/login?result=success` 로 돌려보낸다. 하필 가장 중요한 이동이 재정의를 지웠으니, `?data=api` 로는 로그인 E2E 를 끝낼 수 없었다. 이제 탭 수명 동안 기억한다. `loresentry-frontend#8`, `#9`.

### 실제 API 로 도는 포트 9개

| 포트 | 메서드 | 상태 |
|---|---|---|
| `auth` | 세션·Google 로그인·로그아웃 | ✅ |
| `account` | 조회·표시 이름 변경 | ✅ |
| `projects` | 10개 전부 | ✅ |
| `files` | 트리·생성·이름·이동·휴지통 4개·즐겨찾기 2개·에피소드 삭제 | ✅ |
| `documents` | 조회·저장·잠금 | ✅ |
| `versions` | 목록·이름 저장·복원·삭제 | ✅ |
| `memos` | 목록·생성·수정·삭제 | ✅ |
| `search` | 프로젝트 내 검색 | ✅ |
| `workspaceState` | 적재·저장 | ✅ |

### 아직 연결되지 않은 것 — 전부

**서버가 없다 (포트 4개가 mock 으로 남는다).** 화면은 **그럴듯한 가짜 데이터를 보여 준다** — E2E 에서 이 네 화면이 도는 것처럼 보이는 것은 실제 동작이 아니다.

| 포트 | 화면 | 막고 있는 것 |
|---|---|---|
| `graph` | 관계 그래프 | graph-rag · Neptune 투영본 (§12-27) |
| `refresh` | AI 최신화 | Kafka · `RefreshService` (§12-28) |
| `chat` | AI 챗 | ai-chat · 스트리밍 (§12-29) |
| `help` | 사용 가이드 | 가이드 출처 미정 |

**연결된 포트 안의 빈 칸 4개.**

| 기능 | 무엇이 없나 | 사용자가 보는 것 |
|---|---|---|
| 사용자 섹션 추가·삭제 | 서버에 폴더 모델이 없다(§9-1 에서 **UI 제거**로 결정, 아직 안 함) | "섹션 추가" 를 누르면 실패 토스트 — 메뉴 3곳 (§12-21) |
| 에피소드 순서 바꾸기 | content 에 `PATCH /episodes/{id}/position` 이 없다. 이름 변경·삭제만 있다 | 에피소드를 끌어 옮기면 실패 토스트 |
| DOCX·HWP 내보내기 | 서버가 파일을 만들어야 한다 | "준비하고 있어요" 안내 (§0.9, §12-30) |
| 이미지 업로드 | **프론트에 포트도 UI 도 없다.** 서버 3개 엔드포인트와 BFF 라우트는 있다 | 본문에 이미지를 넣을 방법이 없다 (§12-22) |

즉 **문서 작업의 본류는 전부 실제 서버로 돈다.** 브라우저 E2E 에서 위 8가지만 "여기는 아직" 으로 알고 보면 된다.

---

## 0.11 앱은 한 번도 서버와 말한 적이 없었다 — 브라우저로 잡았다

`config.json` 을 `api` 로 바꿨는데도 화면은 계속 mock 씨앗 사용자 `서윤주` 로 로그인돼 보였다. **실제 브라우저를 띄워** 보니 `loresentry.com/projects/` 를 열어도 `api.loresentry.com` 으로 요청이 **한 건도** 나가지 않았다.

원인은 `app/providers.tsx` 한 줄이다.

```ts
const [config, setConfig] = useState(DEFAULT_RUNTIME_CONFIG);
const [services] = useState(() => createServices(DEFAULT_RUNTIME_CONFIG));
//                                              ^^^^^^^^^^^^^^^^^^^^^^ 영원히 이 값
```

서비스는 **기본 설정으로 한 번** 만들어지고, `loadRuntimeConfig()` 의 결과는 링크 URL 용 컨텍스트에만 들어갔다. 서비스는 다시 만들어지지 않는다. 그래서

- `config.json` 의 `dataSource: "api"` 가 **아무 효과도 없었다.**
- `?data=api` 도 **아무 효과도 없었다.** §0.10 에서 고친 왕복 유실은 별개의 버그였고, 고쳐도 소용이 없었다.
- mock 이 씨앗 사용자를 로그인된 것으로 보고하므로 **남의 계정으로 로그인된 화면**이 됐다.

설정이 도착한 뒤 그 값으로 서비스를 만들고, 그때까지는 아무것도 그리지 않는다 — 그 값이 첫 질의가 어디로 나갈지 정한다. `loresentry-frontend#10`.

### 왜 §0.8 검증이 이것을 놓쳤나

§0.8 은 세 층을 **각각** 실제 서비스에 대고 확인했다. content 는 port-forward 로, BFF 는 로컬 기동으로, 어댑터 계약은 **직접 만든 스크립트**로 어댑터 함수를 불러서. 전부 통과했고 그 결과 자체는 여전히 맞다.

**빠진 것은 앱 자신의 배선이었다.** 어댑터가 옳은지는 봤지만 **앱이 그 어댑터를 쓰는지**는 아무도 보지 않았다. 기존 `wiring` 테스트도 `createServices` 를 직접 불러 검사했을 뿐, 앱이 적재한 설정을 그 함수에 넘기는지는 확인하지 않았다.

교훈: **층별 검증은 층 사이를 증명하지 않는다.** 그래서 프로바이더 층에 회귀 테스트를 두고 예전 코드에서 실제로 실패하는지 확인했다(2개 모두 실패 → 수정 후 통과). 그리고 배포 파이프라인이 `prettier --check` 에서 멈춰 **#10 이 배포되지 않은 채로 "고쳤다"고 믿을 뻔했다**(`#11` 로 복구). 배포 성공까지 확인해야 고친 것이다.

### 브라우저로 확인한 현재 상태

```text
GET  loresentry.com/projects/          → config.json 읽음
GET  api.loresentry.com/auth/users/me  → 401           ← 실제로 서버에 묻는다
     /login?returnTo=%2Fprojects%2F 로 이동
클릭 Google로 계속하기
GET  api.loresentry.com/auth/oauth/google/prepare → 302
GET  accounts.google.com/o/oauth2/v2/auth...      → 302
     accounts.google.com 실제 로그인 화면 (Sign in - Google Accounts)
```

여기까지가 자격 증명 없이 갈 수 있는 끝이다. **실제 Google 계정으로 승인하는 마지막 한 걸음은 사람이 해야 한다.**

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

- ~~`auth/INTERNAL_API.md`가 없다~~ — **[해소됨]** 생겼다. 다만 이 저장소가 아니라 **authentication 저장소 안**(`docs/INTERNAL_API.md`)이다. gateway 도 같은 방식으로 `docs/EXTERNAL_API.md` 를 자기 저장소에 뒀다. 계약 문서가 코드와 같은 저장소에 있으면 함께 고쳐진다는 점에서 이쪽이 낫고, 대신 **이 docs 저장소는 서비스 간 계약의 단일 색인 역할을 잃었다.**
- **`auth_sessions` 테이블을 쓰지 않는다.** [`TABLE_AND_LOGIC.md`](TABLE_AND_LOGIC.md) §3.2는 refresh token 해시를 PostgreSQL에 두는데, 구현은 refresh token과 OAuth state를 **Redis(`auth-valkey`)**에 둔다. 마이그레이션 이름도 `V1__create_auth_accounts.sql`로 달라졌다. `TABLE_AND_LOGIC.md` §3을 구현에 맞춰 고쳐야 한다.
- **`auth-valkey`가 "순수 캐시"가 아니다 — 더 뾰족해졌다.** 구현이 `RedisRefreshTokenStore` 에서 `RedisSessionStore` 로 바뀌어 **사용자당 단일 활성 세션**을 거기 둔다. [`INFRA_AND_CICD.md`](INFRA_AND_CICD.md) §18과 [`LORE_SENTRY_PROJECT_CONTEXT.md`](LORE_SENTRY_PROJECT_CONTEXT.md) §14.1은 `auth-valkey`를 영속성 없는 캐시(`emptyDir`)로 기록한다. 그런데 refresh token과 OAuth state가 거기 있으면 **pod가 재시작되면 전원이 로그아웃된다.** 영속성을 줄지, 그 동작을 받아들일지 결정이 필요하다.

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

**2026-09-26 갱신.** 끝난 것도 남겨 둔다 — 무엇이 어떤 순서로 풀렸는지가 다음 순서를 정하는 근거다.

| # | 작업 | 저장소 | 상태 |
|---|---|---|---|
| 1 | 프로젝트 CRUD 8개 + `V3` | content | ✅ `#3` |
| 2 | authentication 계약 정렬 | content | ✅ `#4` |
| 3 | 파일·문서·버전·검색 18개 + `V4` `V5` | content | ✅ `#5` |
| 4 | gateway 중계 (네임스페이스) | gateway | ✅ `#2` `#3` — §0.3-A 가 대체함 |
| 5 | 프론트 API 어댑터 (포트 5개) | frontend | ✅ `#5` |
| 6 | Google OAuth·JWT·계정·세션 | auth | ✅ main |
| 7 | 이미지 3개 + `V6`, `last_file`, 프로젝트 활동 | content | ✅ `#6` |
| 8 | `HeadObject` 404·403 처리 | content | ✅ `#7` `#8` |
| 9 | 메모·즐겨찾기·작업공간 10개 + `V7` | content | ✅ `#9` |
| 10 | BFF 라우트별 선언 + 보안·쿠키·CSRF | gateway | ✅ main (팀원) |
| 11 | BFF 에 누락된 13개 선언 (§0.3-A) | gateway | ✅ `#4` |
| 12 | GitOps env·공개키 마운트·valkey ACL·PVC (§0.7) | gitops | ✅ `#4` |
| 13 | 프론트 인증·메모·즐겨찾기·작업공간 어댑터, 글자 수 한도 | frontend | ✅ `#6` |
| 14 | Secret 3개 + ConfigMap 1개 생성 (§0.7) | 클러스터 | ✅ 적용됨 |
| 15 | valkey `requirepass`·ACL 버그 수정 (§0.7) | gitops | ✅ `#4` |
| 16 | Google Cloud 운영·테스트 프로젝트 분리 | 사람 | ☐ 클라이언트 ID 는 다른데 프로젝트 ID 가 같다 |
| 17 | 브라우저 이전 3층 검증 (§0.8) | 전체 | ✅ 64 + 29 + 11 통과 |
| **18** | **브라우저 E2E — Google 로그인부터 문서·메모·즐겨찾기까지** | 전체 | ☐ **다음** |
| 19 | `integration/session/content_checks.py` 를 38개로 확장 (Linux 에서) | gateway | ☐ |
| 20 | 프론트 명시적 재발급·탭 조율 (`FRONTEND_AUTH_CONTRACT.md`) | frontend | ☐ |
| 21 | 프론트 사용자 섹션 메뉴 제거 (§9-1 결정) — **api 에서는 실패 토스트가 난다** | frontend | ☐ |
| 22 | 프론트 이미지 업로드 UI — 포트도 없다. 서버·BFF 는 준비됨 | frontend | ☐ |
| 23 | `s3:ListBucket` 을 AWS 실제 정책에 반영 (§0.5) | AWS | ☐ |
| 24 | NetworkPolicy — vpc-cni `ENABLE_NETWORK_POLICY` 가 꺼져 지금은 무시된다 | gitops | ☐ |
| 25 | 남은 `PENDING` 이미지·만료 자동 버전 정리 배치 | content | ☐ |
| 26 | `outbox_events` 쓰기 + 토픽 설계 | docs · content | ☐ |
| 27 | Outbox publisher · graph-rag Inbox → `GraphService` | content · graph-rag | ☐ |
| 28 | AI 최신화 (`RefreshService`) | content · ai-chat | ☐ |
| 29 | AI 챗 + 스트리밍 패스스루 | ai-chat · gateway | ☐ |
| 30 | 내보내기 DOCX·HWP — `md`·`txt`·PDF 는 브라우저가 처리한다 (§0.9) | content | ☐ |
| 31 | `config.json` 기본값을 `api` 로 전환 (§0.10) | frontend | ✅ `#9` |
| 32 | `?data=api` 가 로그인 왕복에서 사라지던 문제 (§0.10) | frontend | ✅ `#8` |
| 33 | 에피소드 순서 바꾸기 — `PATCH /episodes/{id}/position` 없음 (§0.10) | content · gateway · frontend | ☐ |
| 34 | 앱이 적재한 설정으로 서비스를 만들지 않던 버그 (§0.11) | frontend | ✅ `#10` `#11` |

**18번 브라우저 E2E 는 시작됐다.** 헤드리스 브라우저로 Google 로그인 화면까지 도달하는 것을 확인했고(§0.11), 그 과정에서 앱이 서버와 말하지 못하던 버그를 잡았다. 남은 것은 **실제 Google 계정으로 승인하는 한 걸음**이고, 그건 사람이 해야 한다.

graphRAG·Kafka·AI 를 뺀 content 엔드포인트는 **전부 구현됐다**(38개). 남은 content 작업은 정리 배치(25)와 내보내기(30)뿐이다. 프론트 편집 경로도 내보내기 두 형식만 남았다(§0.9).
