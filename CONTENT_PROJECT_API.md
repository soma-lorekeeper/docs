# Content API — 구현 현황과 계약

> 작성일: 2026-09-22 · 최신화: 2026-09-30
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
| `loresentry-content` | Flyway `V9` | 160 |
| `loresentry-gateway` | BFF 배포됨, prod 프로필 기동 중 | 186 |
| `loresentry-frontend` | 배포됨, **기본값 `api`** (`?data=mock` 으로 되돌림) | 239 |

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

`md`·`txt` 는 서버가 필요 없다 — 본문이 에디터 JSON 이므로 브라우저가 그것을 Markdown·텍스트로 바꾼다(§0.18). 브라우저에서 만들고, DOCX·HWP 는 거절 대신 **빈 `url`** 을 준다 — 포트 계약에서 "아직 준비되지 않았다"는 뜻이고 화면이 이미 그걸 안내로 바꾼다. `loresentry-frontend#7`.

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

## 0.12 프론트 코드 재검증 — 2026-09-26

`?data=api` 가 처음으로 실제로 동작하게 됐으니, 그 경로를 지나는 코드를 다시 읽었다. 결함 네 개를 찾아 고쳤다.

### 1. 15분 뒤 앱이 죽는다 — 재발급이 없었다

AT 수명은 **15분**(`RsaJwtTokens`: `Duration.ofMinutes(15)`), RT 는 14일이다. 프론트에는 **재발급이 없었다.** `isRefreshable` 은 작성돼 있고 **아무도 부르지 않았다.**

15분이 지나면 모든 요청이 401 이고 화면은 "다시 로그인해 주세요" 를 띄운다. **에디터의 자동 저장도 포함이다** — 글 쓰는 사람이 저장되지 않는 문서에 계속 입력한다. 소설 편집기에서 낼 수 있는 최악의 실패다.

고친 방식:

- `ACCESS_TOKEN_MISSING`·`ACCESS_TOKEN_EXPIRED` 만 재발급 대상. 원래 요청은 **한 번만** 재시도.
- `SESSION_INVALID` 는 끝난 세션이므로 건드리지 않는다. 모든 401 에 재발급하면 끝난 세션을 계속 두드린다.
- **재발급은 한 번에 하나.** 같은 탭의 동시 실패는 하나의 프로미스를 기다리고, 탭들은 **Web Lock** 으로 줄을 선다. 두 번째 탭은 "내가 거절당한 뒤 이미 갱신됐다"를 보고 재시도만 한다. 조율이 없으면 열린 탭 수만큼 재발급이 나가고, RT 를 회전시키는 서버에서는 늦은 요청이 이미 쓰인 RT 를 들고 가 실패한다.

운영 엔드포인트 확인: 쿠키 없이 `POST /auth/tokens/refresh` → `401 REFRESH_REJECTED`, `next_action: RELOGIN`, CORS 허용·자격 증명 허용.

### 2. 화면이 모르는 관계를 저장이 지운다

`toProperties` 는 `RELATION_KEY_BY_TYPE` 에 없는 `relation_key` 를 **버린다.** 저장은 화면이 든 목록을 그대로 서버에 쓰므로, **다음 저장이 그 관계를 서버에서 지운다.**

지금은 프론트만 관계를 쓰므로 터지지 않지만, AI 최신화가 관계를 쓰기 시작하면 사용자가 문서를 한 번 저장하는 것으로 그것이 사라진다. **화면은 자기가 표시할 수 없는 것을 지울 권한이 없다.** 읽을 때 본 미지의 행을 붙들어 두고 저장할 때 다시 실어 보낸다.

### 3. 새로 고침 직후 휴지통 복원이 실패한다

복원·이름 변경은 `projectOf(fileId)` 로 프로젝트를 찾는데, 그 대응은 **트리를 읽을 때만** 기록됐다. 휴지통 문서는 트리에 없다. 그래서 **새로 고침 → 휴지통 → 복원**이 "파일을 찾을 수 없어요" 로 실패했다. 휴지통 목록도 기록하게 했다.

### 4. 버전 목록이 문서 전체를 다시 받아왔다

버전 스냅샷에는 문서 종류가 없어 `documents.get` 으로 알아냈다 — **본문까지 포함된 전체 문서**를, 방금 열어 본 그 문서를. 읽을 때 종류를 기억하고 없을 때만 받아온다.

### 확인했고 문제가 없던 것

| 본 것 | 결론 |
|---|---|
| 어댑터 39개 경로 대 BFF 라우트 | 전부 일치 |
| 문서 저장 `If-Match`·`X-Save-Id`·충돌 3-way 병합 | 정상 |
| `hydrate` 가 편집 중 draft 를 덮는가 | 안 덮는다. dirty·inFlight 면 revision 도 올리지 않아 다음 저장이 409 로 병합을 탄다 |
| 탭 전환 시 세션이 옛 `fileId` 를 붙든 채 저장하는가 | `tabIdFor` 가 `file:<id>` 라 키가 달라 remount 된다. 안전 |
| 검색이 입력마다 요청하는가 | 디바운스됨 |
| 작업공간 배치 저장 | 디바운스됨. **적재 직후 같은 값을 다시 쓰던 한 번은 제거했다** |
| 로그아웃 후 이전 사용자 자료가 남는가 | `queryClient.clear()` 로 비운다 |
| 쿠키가 `api.` 하위 도메인으로 가는가 | 같은 사이트(등록 도메인 동일)라 문제없음. 운영에서 확인됨 |

### 남긴 것

재발급이 끝내 실패했을 때 **자동으로 로그인 화면으로 보내지 않는다.** 편집 중이면 저장 안 된 원고를 들고 화면을 떠나게 된다. 안내만 띄우고, 이 처리는 따로 다룬다(§12-20).

---

## 0.13 로그인이 자기 이동을 취소하고 있었다 · mock 을 걷어냈다

### "Google 로그인으로 이동 중…" 에서 멈추는 이유 — `loresentry-frontend#13`

`login-page.tsx` 의 `start()`:

```ts
await services.auth.startGoogleLogin(returnTo ?? "/projects");
await queryClient.invalidateQueries({ queryKey: queryKeys.session });
router.replace(returnTo ?? "/projects");   // ← 예약된 외부 이동을 취소한다
```

`window.location.assign` 은 **이동을 예약할 뿐** 즉시 떠나지 않는다. api 어댑터의 `startGoogleLogin` 은 곧바로 값을 돌려주므로 다음 두 줄이 실행되고, **클라이언트 라우팅이 그 이동을 취소한다.** 뒤 두 줄은 즉시 돌아오는 mock 을 위한 것이었다.

헤드리스 브라우저 추적에 경합이 그대로 남았다.

```text
고치기 전:  클릭 → /projects/                    (router.replace)
                 → /login/?returnTo=%2Fprojects%2F  (세션 게이트가 되돌림)
                 → accounts.google.com              (예약된 이동이 겨우 이김)

고친 뒤:    클릭 → accounts.google.com              (곧바로)
```

기계가 빠르면 이기고 느리면 진다 — 그래서 "가끔 된다" 로 보였다. 떠나는 중이므로 **끝나지 않는 프로미스**를 돌려주게 했다. 호출자의 다음 줄이 실행되지 않으니 이동이 취소되지 않고, 화면은 실제로 떠날 때까지 "이동 중" 에 머문다. 그것이 사실이다.

### 서버 없는 기능은 이제 mock 을 보여 주지 않는다 — `loresentry-frontend#12`

`graph`·`refresh`·`chat`·`help` 네 포트가 `?data=api` 에서도 mock 으로 답하고 있었다. **그럴듯한 가짜 그래프·대화·가이드를 자기 자료처럼** 보여 주므로 E2E 에서 가장 헷갈리는 지점이었다.

- 네 포트를 `unavailable` 로 거절한다. 요청도 보내지 않는다 — 서버에 그 경로가 없다.
- 각 화면은 "불러오지 못했어요 · 다시 시도" 대신 **준비 중**이라고 말한다. 연결 문제가 아니고 다시 시도해도 같기 때문이다.
- 사이드바의 "그래프 최신화" 는 비활성이 된다. 성공할 수 없는 호출을 누를 수 있게 두지 않는다.

---

## 0.14 관계를 양방향으로 만들었다 — `loresentry-content#10`

> **이 처방은 §0.20 이 대체했다.** 여기서 넣은 "반대쪽 행을 함께 맞춘다"는 구조가 바로 그 다음
> 문제의 원인이었다. 아래는 그때의 판단 기록으로 남긴다.

A 에서 B 를 연결해도 **B 문서의 "관련 ○○" 은 비어 있었다.** `document_relations` 에 한쪽 행만 넣고, 읽을 때도 `where document_id = :id` 로 나가는 행만 봤기 때문이다. 사용자는 한 번 이어 놓고 반대쪽 문서를 열어 그 관계를 찾는다.

### 왜 서버에서 고쳤나

읽을 때 들어오는 행을 합쳐 보여 주는 방법도 있다. 그런데 그러면 **한쪽에서 관계를 끊을 수 없다** — A 에서 칩을 지워도 B 가 가진 행이 남아 다음에 다시 나타난다. 그래서 저장할 때 반대쪽 행을 함께 넣고 함께 지운다.

| 결정 | 이유 |
|---|---|
| 역방향 키는 **저장하는 문서의 분류**가 정한다 | B 에서 A 를 볼 때 A 는 A 의 종류로 보인다. 캐릭터 A → 장소 B 면 A 에 `related_place`, B 에 `related_character` |
| 그 표를 서버가 갖는다 (`RelationKeys`) | 서버가 관계 어휘를 알아야 역방향 키를 만들 수 있다. 프론트의 같은 표와 한 글자도 어긋나면 화면이 읽지 못해 **조용히 사라진다.** `LOCATION` → `related_place` 가 유일하게 이름이 다른 자리 |
| 역방향 행은 상대의 `revision_no` 를 **올리지 않는다** | 관계는 본문이 아니다. 올리면 그 문서를 열어 둔 편집기가 다음 저장에서 충돌로 떨어진다 |
| 역방향 행을 따로 표시하지 않는다 | 관계는 대칭이다. "내가 만든 것"과 "상대가 만든 것"을 구분하면 한쪽에서 끊을 수 없는 관계가 생긴다 |
| 모르는 분류 코드면 역방향을 만들지 않는다 | 키를 짐작하면 화면이 읽지 못하는 관계가 쌓인다 |

### 딸려 온 두 가지

- **프론트**: 관계가 바뀐 저장 뒤에 문서 질의를 무효화한다. 반대쪽 문서를 열어 둔 탭이 링크 이전 목록을 들고 저장하면 방금 만든 관계를 지운다. `loresentry-frontend#16`
- **시드 스크립트**: 관계를 덮어쓰지 않고 더한다. 덮어쓰면 서버가 만든 역방향 행이 지워진다.

### 검증

`./gradlew test` **143개 통과**(140 → 143). 새 테스트 셋: 반대쪽에서 보이는지(키와 `revision_no` 까지), 한쪽에서 지우면 반대쪽에서도 사라지는지, **반대쪽에서도 끊을 수 있는지.**

배포는 `build-13-1` 로 올라가 Ready 다. Argo 가 이미지 갱신 커밋을 또 늦게 집어 `refresh=hard` 로 당겼다 — **자동 동기화를 믿고 기다리는 것만으로는 배포됐다고 단정할 수 없다**는 §0.7 의 기록이 두 번째로 맞았다.

---

## 0.15 인증이 불투명 세션으로 바뀌었다 — 내 재발급 구현은 폐기했다

2026-09-28 점검에서 gateway 를 27커밋 받아 보니 **인증 모델이 교체돼 있었다.** 팀원의 `LOREKEEPER-584~599` 작업이다.

| | 전 (§0.12 당시) | 지금 |
|---|---|---|
| 신원 | RS256 AT(15분) + RT(14일) | **단일 세션 ID 쿠키** |
| 수명 연장 | 프론트가 `POST /auth/tokens/refresh` 를 명시적으로 호출 | **보호 요청이 성공할 때마다 서버가 연장**(마지막 활동 +14일) |
| 로그아웃 | `POST /auth/tokens/revoke` | **`POST /auth/sessions/revoke`** |
| 401 코드 | `ACCESS_TOKEN_MISSING`·`ACCESS_TOKEN_EXPIRED` | **`SESSION_REQUIRED`**·`SESSION_INVALID`·`SESSION_UNAVAILABLE`(503) |

운영에 이미 그 버전이 떠 있다. 라이브로 확인했다.

```text
GET  /auth/users/me      (쿠키 없이) → 401 SESSION_REQUIRED
POST /auth/tokens/revoke              → 401 SESSION_REQUIRED   ← 경로가 없다
POST /auth/tokens/refresh             → 401 SESSION_REQUIRED   ← 경로가 없다
POST /auth/sessions/revoke            → 200 {"session_revocation":"not_requested"}
```

### 그래서 프론트에 실제 결함이 있었다

**로그아웃이 항상 실패했다.** 없는 경로(`/auth/tokens/revoke`)로 불러 세션 검사에서 401 로 막혔고, 화면은 "로그아웃하지 못했어요" 를 띄웠다. 새 경로로 옮기고, `session_revocation` 이 `unconfirmed` 면 완전한 성공으로 표시하지 않는다.

**§0.12 에서 만든 재발급 기계는 전부 지웠다.** 단일 흐름·Web Lock·1회 재시도까지 계약에 맞춰 만든 것이었지만, 지금 계약은 **재발급을 두지 말라고 명시한다** — 서버가 스스로 연장하고, "401 의 원래 요청을 일괄 자동 재전송하지 않는다" 가 계약 문구다. AT 15분이라는 전제 자체가 사라졌으니 그 코드가 풀던 문제도 없다. 코드는 남겨 두는 것보다 지우는 편이 정직하다 — 남아 있으면 다음 사람이 그것이 도는 줄 안다.

오류 표도 세션 코드로 맞췄다: `SESSION_REQUIRED`·`INVALID_SESSION_ID` → 재로그인, `SESSION_UNAVAILABLE`·`REVOCATION_UNCONFIRMED` → 일시 장애(로그아웃시키지 않는다).

### 배운 것

§0.11 은 "층별 검증은 층 사이를 증명하지 않는다" 였다. 여기서 하나 더 붙는다 — **다른 서비스의 계약 문서는 내가 읽은 그 시점의 것이다.** 팀원이 인증 방식을 바꾸는 동안 나는 이전 계약에 맞춰 기계를 만들고 있었다. 정기적으로 `git pull` 하고 **계약 문서의 diff 를 보는 것**이 구현보다 먼저다.

---

## 0.16 로그인이 재로그인부터 깨졌다 — valkey ACL 에 `PTTL` 이 없었다

증상은 `/login?result=unavailable` 이었다. 그 값은 BFF 가 auth 에 닿지 못했거나 auth 가 `LOGIN_UNAVAILABLE` 을 돌려줬을 때 나온다.

연결은 문제가 없었다 — gateway 파드에서 `http://authentication-api/health` 가 `{"status":"ok"}` 를 주고, auth 파드에서 Google 에도 닿았다. 남은 것은 세션 저장이었다.

### 원인

새 세션 설계는 Lua 스크립트로 두 키를 원자적으로 쓴다. **스크립트 안에서 부르는 명령도 ACL 검사를 받는다.** 내가 §0.7 에서 만든 ACL 은 `+eval` 만 주고 `+pttl` 을 빠뜨렸다.

**로컬 valkey 9.0.6 에 운영과 같은 규칙과 실제 스크립트를 넣어 재현했다.**

```text
1차 로그인          → 1791788834597   (성공)
2차 로그인(재로그인) → ERR ACL failure in script:
                      User authentication has no permissions to run the 'pttl' command
```

로그인 스크립트는 **그 계정에 기존 세션 기록이 있을 때만** `PTTL` 을 부른다.

```lua
if old and (not current(old) or redis.call('PTTL', KEYS[2]) <= 0) then return -2 end
```

그래서 **첫 로그인은 되고 그 뒤 모든 로그인이 실패한다.** §0.11 에서 로그인 성공을 확인했을 때 기록이 생겼고, 그때부터 깨져 있었다. 배포 당일에는 정상으로 보이는 모양이다.

**같이 찾은 두 번째 결함.** BFF 계정에는 `+get` 밖에 없어 세션 검증 스크립트가 `NOPERM ... 'eval'` 로 막힌다 — 로그인이 됐더라도 **모든 보호 요청이 실패했을 것이다.**

### 조치

```text
user authentication ... +get +set +del +getdel +eval +pttl +time
user bff            ... +get +eval +pttl +pexpireat +time
```

수정안을 로컬에서 먼저 통과시킨 뒤(1차·2차 로그인·BFF 검증 모두 정상) Secret 을 갱신하고 valkey 를 재시작했다. **ACL 파일만 바꾸면 valkey 가 다시 읽지 않고, `ACL LOAD` 는 관리 권한이 필요한데 그런 계정을 두지 않았다.** 세션은 PVC + AOF 로 재시작을 견딘다.

`+@all`·`+@read`·`+@write` 로 넓히지 않았다. BFF 는 여전히 `SET`·`DEL`·`GETDEL`·OAuth 키에 닿지 못한다.

### 팀원 인계 문서와 대조 — 요구사항과 정확히 일치했다

`AUTH_BFF_SECRETS_NEW.md`(2026-09-28, Auth `eeb8465`, BFF `ca3e3f2`)가 요구한 명령 집합이 위와 같다. 그 문서는 **원인을 "아직 확정하지 못했다"** 고 적었고, 위 재현이 그것을 확정했다.

문서의 "유지" 목록은 운영에 전부 들어가 있다(Auth 11개, BFF 6개). 논리 DB 도 양쪽 0 으로 같다.

**아직 정리하지 않은 것:** 이제 읽지 않는 JWT 설정이다 — `AUTH_JWT_PRIVATE_KEY_BASE64`·`AUTH_JWT_KEY_ID`·`AUTH_JWT_PUBLIC_KEY_PATH`, `BFF_JWT_PUBLIC_KEY`·`BFF_JWT_KEY_ID`, ConfigMap `authentication-jwt-public` 과 양쪽 볼륨 마운트. 로그인 실패의 원인은 아니고, 롤백 대상 버전이 그 키를 쓰는지 확인한 뒤 지워야 한다.

---

## 0.17 QA 1차 — 8건 반영 (2026-09-30)

브라우저로 쓰면서 나온 8건이다. 원인이 서버인 것과 화면인 것이 섞여 있었다.

### 서버 — `loresentry-content#11`, `loresentry-gateway#5`

**타임라인이 "불러오지 못했어요" 였다.** 타임라인은 `ProjectGraph` 로 그리는데 그 포트가 graph-rag 를 기다리고 있었다. 그런데 타임라인이 필요한 것 — 회차, 그 회차에 나온 엔티티, 에피소드 순서 — 은 **이미 RDB 세 표에 있고 한 홉이다.** `GET /projects/{id}/graph` 를 더해 `document` + `document_properties` + `document_relations` + `episode_folders` 를 질의 세 번으로 모아 준다. graph-rag 가 만드는 것(AI 가 본문에서 찾아낸 관계, 여러 홉)은 나중에 여기에 출처를 더하면 된다.

**메모 화면의 파일 메모가 400 이었다.** `scope=file` 이 `document_id` 를 요구해서, 프로젝트의 파일 메모를 한 목록으로 보려는 화면이 `INVALID_MEMO` 를 받았다. `document_id` 가 없으면 그 프로젝트의 파일 메모 전부로 읽는다.

**관계마다 설명.** `document_relations.description`(V8). 설명은 대상 문서의 것이 아니라 **연결의 것**이다. 그때는 역방향 행에도 같은 값을 넣어야 했는데, 이제 쌍이 한 행이라 적을 자리가 하나뿐이다(§0.20).

**"마지막으로 작업한 파일" 이 실제와 달랐다.** `document.updated_at` 이 가장 늦은 문서를 골랐는데 그 칸은 **이름 변경·이동·잠금·복원·생성에도 움직인다.** 그래서 한 시간 쓴 원고 대신 방금 이름만 바꾼 문서가 올라왔다. `projects.last_file_id` 를 두고 **본문 저장 때만** 적는다. 비어 있거나 그 문서가 휴지통에 갔으면 예전 방식으로 물러선다.

### 화면 — `loresentry-frontend#18`

**메모를 다시 만들었다.** 같은 자료가 두 모양이었다 — 작품 메모는 카드 목록, 문서 메모는 자동 저장되는 큰 입력창 하나(삭제도 없었다).

| 요구 | 조치 |
|---|---|
| 문서 메모가 먼저 | 패널 탭을 문서 → 작품 순으로. 패널은 문서를 열어 둔 채 쓰는 것이다 |
| 수동 저장 | ✓ 로 남기고 ✕ 로 버린다(`⌘Enter`·`Esc`). **자동 저장 기계는 지웠다** — 메모는 적다 지우는 곳이고, 자동 저장은 "쓰는 중"과 "남기기로 한 것"을 구분하지 못한다 |
| 삭제 | 모든 카드에서 바로 |
| 디자인 통일 | 두 범위가 **같은 카드 하나**를 쓴다 |
| 메모 전용 화면도 | 같은 카드. 문서 메모 탭은 프로젝트의 모든 문서 메모를 한 목록으로 본다 |

**관계.** 속성 추가 메뉴가 `캐릭터` 라고만 적어 "캐릭터 문서를 만든다"로 읽혔다 → **`관련 캐릭터`**. 칩마다 관계 설명을 그 자리에서 고친다.

**PDF 양식.** 편집 화면을 그대로 인쇄해 **제목 입력창·속성표·툴바·스크롤 영역이 종이에 찍혔다.** 인쇄 전용 지면에 제목과 본문만 담아 그것만 인쇄한다 — A4 여백, 세리프, 단락 첫 줄 들여쓰기, 외톨이 줄 방지. **DOCX·HWP** 는 "받을 수 있다" 고 안내한 뒤 아무 일도 없었다 → 메뉴에서 비활성 + "준비 중".

**지운 것.** 검색은 Elasticsearch 로 갈 것이므로 포트·어댑터·화면·탭·사이드바 항목을 걷어냈다(에디터의 찾기·바꾸기와 그래프 노드 검색은 화면 안 텍스트를 찾는 별개 기능이라 남겼다). 디렉터리의 **새 폴더·가져오기·섹션 추가** 도 지웠다 — 에피소드가 아닌 폴더는 서버 모델이 없고, 섹션은 누르면 실패 토스트였고, 에피소드는 원고 행 메뉴에서 이미 만든다. 가져오기는 동작하는 새 탭 화면에만 남겼다.

### 배포 파이프라인이 한 번 조용히 멈춰 있었다

content PR 을 머지했는데 **CI 가 트리거되지 않았다**(`push` 트리거인데 실행 기록이 없다). 머지만 보고 배포됐다고 믿을 뻔했다. `workflow_dispatch` 로 직접 돌려 `build-14-1` 을 올렸다. §0.14 의 Argo 지연에 이어, **배포는 "머지했다"가 아니라 "파드가 그 이미지로 Ready 다"로 확인해야 한다**는 사례가 하나 더 늘었다.

### 검증

```text
content    150 passed (143 → 150)   새 테스트가 "이름만 바꿨을 때 마지막 작업 파일이 안 바뀐다"를 고정
gateway    186 passed
frontend   240 passed               메모 테스트를 자동 저장 → 수동 저장·취소로 다시 씀
```

---

## 0.18 본문 저장 형식을 Markdown → 에디터 JSON 으로 바꿨다

`TEMP_BODY_JSON_MIGRATION.md` 의 작업이다. 에디터·UI 는 그대로 두고 **저장 형식만** 바꿨다.

### 왜 — 실제로 확인된 데이터 손상

Markdown 문자열은 글자와 서식 기호를 한 곳에 섞는다. 그래서 사용자가 쓴 **일반 문단**이 다시 열 때 다른 것이 됐다.

| 쓴 것 | 다시 열면 |
|---|---|
| `# 해시로 시작하는 문장` | 제목(H1) |
| `1. 번호처럼 보이는 문장` | 번호 목록 |
| `++더하기로 감싼 문장++` | 밑줄 |

문단별 정렬·들여쓰기(요구사항 §5.1)를 담을 자리도 없었다.

### 값의 모양

API·DB·버전 스냅샷이 같은 모양을 쓴다. 프론트 타입은 `DocumentBody`, API 필드는 `body`.

```json
{ "schema_version": 1, "doc": { "type": "doc", "content": [ ... ] } }
```

### 서버 — `loresentry-content#12`

| 칸 | 무엇 |
|---|---|
| `document.body_json` JSONB | 본문의 **유일한 원본**. `NULL` 이면 변환 전 레거시 행 |
| `document.body_text` TEXT | 서버가 `body_json` 에서 뽑은 순수 텍스트. 검색·글자 수·앞으로의 AI 추출이 쓴다. **클라이언트가 보내지 않는다** |
| `document.body_md` | 레거시 읽기용으로만 남는다. 새로 쓰지 않고, 모든 행이 변환된 뒤 후속 마이그레이션에서 지운다 |

- 저장 검증: `doc` 여부·`schema_version`·**노드/마크 허용 목록**·JSON 4MB·추출 텍스트 1,000,000자. 모르는 노드는 **거절한다** — 저장하면 다른 클라이언트가 못 여는 문서가 되고, 조용히 지우면 사용자는 글이 사라진 것으로 본다.
- `char_count` 는 추출 텍스트의 코드 포인트 수(줄바꿈 제외)다. 예전에는 Markdown 기호까지 세는 `String.length()` 였다.
- 새 문서는 삽입 시점에 빈 JSON 본문을 갖는다. 비워 두면 레거시 행과 구분되지 않는다.

### 프론트 — `loresentry-frontend#19`

- `domain/document-body.ts`(에디터 의존 없음)와 `features/documents/editor/body-markdown.ts`(헤드리스 에디터).
- **Markdown 은 입출력 형식일 뿐이다.** 가져올 때 한 번 들어오고 내보낼 때 한 번 나가며, 왕복시키지 않는다 — 그 왕복이 손상의 원인이었다.
- 3-way 병합이 **최상위 블록 배열** 기준이 됐다. 블록 동일성은 직렬화 비교라 같은 글자라도 마크가 다르면 다른 블록이다. 블록 추가·삭제는 충돌로 본다.
- `bodyToPlainText` 는 서버 `BodyText.extract` 와 **같은 규칙**이어야 한다. 다르면 같은 문서의 글자 수가 화면과 서버에서 다르게 보인다.

### 레거시 호환

옛 모양이 두 곳에 남아 있다 — `body_json` 이 `NULL` 인 문서 행, 그리고 버전·최신화 스냅샷 JSONB 안의 `body_md`. 응답은 `body`(nullable)와 `legacy_body_md`(nullable)를 함께 갖고, 저장 요청의 `legacy_body_md` 는 무시한다.

**Markdown → JSON 변환은 프론트가 한다.** 서버는 Markdown 을 해석하지 않는다. 어댑터가 `legacy_body_md` 를 받으면 바꿔서 넘기므로 화면 모델에는 언제나 `DocumentBody` 만 올라가고, 그 문서를 다음에 저장하면 변환이 끝난다.

### 검증

```text
content    160 passed (150 → 160)
frontend   239 passed · pnpm check:cdn 통과
```

회귀 테스트로 `# …`, `1. …`, `++…++`, `*…*` 인 일반 문단이 저장·재조회 후 그대로인지 **mock 경로와 API 어댑터 경로 둘 다** 확인한다. `md` 내보내기는 여전히 단방향이고(내보낸 `# …` 을 다시 읽으면 제목이 된다) 그 사실도 테스트로 못 박았다 — 그것이 저장 형식을 바꾼 이유다.

### 아직 아닌 것

에디터 UI 변경, 문단별 정렬·들여쓰기(`schema_version` 2 에서), `body_md` 컬럼 삭제(모든 행 변환 확인 후).

---

## 0.19 타임라인에 즐겨찾기 폴더만 보였다

표가 **회차에 연결된 엔티티만** 줄로 세웠다. 그래서 별을 켠 문서 몇 개만 회차와 이어져 있으면 그것들만 줄이 되고, 그 줄이 전부 즐겨찾기 폴더로 옮겨져 분류 폴더가 하나도 남지 않았다.

디자인(`loresentry-frontend/docs/design/timeline-table.js`)을 확인하니 다르다.

```text
즐겨찾기   김독자 · 유중혁 · 12화 · 균열의 밤
캐릭터     김독자 · 유중혁 · 유상아 · 정희원 · 이지혜     ← 별을 켠 둘이 여기에도 있다
장소       유리 산맥 · 3807호차 · 충무로역
조직 · 아이템 · 이벤트 · 세계관  …
```

두 가지가 어긋나 있었다.

| 디자인 | 구현 (전) |
|---|---|
| 분류마다 폴더를 두고 **그 분류의 문서를** 늘어놓는다 | 회차에 등장한 문서만 줄이 된다 |
| 즐겨찾기 항목이 **원래 분류에도 남는다** | 원래 분류에서 빠진다 |

### 고친 것

- **회차에 한 번도 나오지 않은 설정 문서도 줄을 갖는다.** 막대가 없는 줄이 곧 "이 설정을 깔아놓고 몇 화째 안 건드렸나" 의 답이다 — 타임라인에서만 답할 수 있는 질문이고, `timeline-rows.ts` 머리주석이 스스로 그렇게 적어 놓았는데 정작 그 줄을 만들지 않고 있었다. 원고는 여전히 줄이 되지 않는다(회차가 이미 열이다).
- **즐겨찾기는 원래 분류 폴더에도 남긴다.** 별은 분류를 가로지르는 표시일 뿐이라, 별을 켰다고 캐릭터 폴더에서 사라지면 그 폴더가 캐릭터 전부가 아니게 된다. 예전 주석은 "같은 막대가 두 줄 그려져 등장 횟수를 잘못 읽는다"를 이유로 뺐는데, 디자인이 양쪽에 두기로 정한 것이었다.
- 막대 없는 줄은 폴더 아래쪽으로 모으고, `rowCount` 는 그려지는 줄을 센다(즐겨찾기는 두 번 그려진다).

`loresentry-frontend#20`. 타임라인 테스트 36개 통과 — 옛 규칙을 고정하던 둘을 디자인에 맞게 고쳤고, 연결 없는 문서가 제 분류 폴더에 나오는지 확인하는 테스트를 넣었다.

### 남는 것

디자인의 즐겨찾기에는 **원고**(`12화 · 균열의 밤`)도 들어 있다. 지금 구현은 설정 문서만 줄로 만들므로 원고를 즐겨찾기해도 타임라인에 나오지 않는다. 회차는 열이므로 그 줄이 무엇을 뜻하는지 정해야 해서 이번에는 건드리지 않았다(§12-56).

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

## 0.20 관계를 한 쌍에 한 행으로 바꿨다

§0.14 는 A→B 와 B→A 를 **각각 한 행**으로 두고 저장할 때마다 반대쪽을 맞추는 방식이었다. 같은 사실을 두 곳에 적는 구조라, 두 행이 어긋나면 한쪽 문서에서만 보이는 관계가 생긴다. 실제로 **미러링이 들어온 2026-09-26 이전에 만들어진 관계는 전부 한쪽 행만 있고**, 코드만 고쳤기 때문에 그 행들은 영영 그대로였다.

관계에는 방향이 없다. 그러면 저장도 하나여야 한다.

```text
전                                        후
document_id   relation_key   target      low_document_id  high_document_id  description
d-ch1         related_character  d-yjh    d-ch1            d-yjh             첫 등장
d-yjh         related_manuscript d-ch1   (한 행)
(두 행이 서로를 설명한다)
```

### 무엇이 사라졌나

| 칸 | 왜 없앴나 |
|---|---|
| `relation_key` | 키는 **반대쪽 문서의 분류**가 정한다. 한 행에서 양쪽 키가 모두 나와야 하므로 분류 표(`base_folders.relation_key`, V10 에서 추가)에서 꺼낸다. 행에 적어 두면 문서를 다른 분류로 옮겼을 때 어긋난다 |
| `position` | 한 행이 두 문서의 것이라 문서마다 다른 순서를 담을 자리가 없다. 읽을 때 키·행 id(만든 순서)로 정한다 |
| `RelationKeys.java` | 분류 표가 DB 에 생겨 Java 사본이 필요 없어졌다. 프론트도 흩어져 있던 세 벌을 `domain/document-types.ts` 한 벌로 모았다 |

### 바뀌지 않은 것

**API 모양은 그대로다.** 응답은 여전히 `relations: [{relation_key, target_document_id, description}]` 이고, 키만 저장 대신 유도된다. 그래서 gateway 와 프론트는 손대지 않았다 — 저장 구조를 바꿀 때 바깥 계약까지 흔들면 되돌릴 수 없다.

### 휴지통

휴지통 문서와의 관계는 **읽을 때 빠지지만 지워지지 않는다.** 화면이 열 수 없는 칩을 그리지 않으면서도, 그 사이에 상대 문서를 저장했다고 관계가 사라지지는 않는다. 되살리면 돌아온다. (전에는 읽을 때 그대로 나가서, 휴지통 대상을 돌려보낸 저장이 `INVALID_RELATION_TARGET` 으로 막혔다.)

### 검증

마이그레이션이 칸을 **지우므로** 합치는 규칙이 틀리면 되돌릴 자리가 없다. 그래서 V9 까지만 적용한 DB 에 옛 모양 그대로 행을 넣고 V10 을 돌려 본다(`RelationCollapseTest`) — 양쪽 행이 한 행이 되는지, 설명이 한쪽에만 있어도 남는지, 한쪽만 있던 옛 관계가 살아남는지. API 쪽은 `DocumentApiTest` 가 한 쌍에 한 행인지, 분류를 옮기면 반대쪽 키가 따라오는지, 휴지통을 거쳐도 관계가 돌아오는지 본다. mock 도 같은 결과를 내도록 고치고 테스트를 붙였다.

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
| 20 | ~~프론트 명시적 재발급·탭 조율~~ | frontend | ⬛ **폐기.** 인증이 불투명 세션으로 바뀌어 재발급이 없다 (§0.15) |
| 21 | 프론트 사용자 섹션 메뉴 제거 (§9-1 결정) | frontend | ✅ `#18` — 새 폴더·가져오기도 함께 (§0.17) |
| 22 | 프론트 이미지 업로드 UI — 포트도 없다. 서버·BFF 는 준비됨 | frontend | ☐ |
| 23 | `s3:ListBucket` 을 AWS 실제 정책에 반영 (§0.5) | AWS | ☐ |
| 24 | NetworkPolicy — vpc-cni `ENABLE_NETWORK_POLICY` 가 꺼져 지금은 무시된다 | gitops | ☐ |
| 25 | 남은 `PENDING` 이미지·만료 자동 버전 정리 배치 | content | ☐ |
| 26 | `outbox_events` 쓰기 + 토픽 설계 | docs · content | ☐ |
| 27 | Outbox publisher · graph-rag Inbox → `GraphService` | content · graph-rag | ☐ |
| 28 | AI 최신화 (`RefreshService`) | content · ai-chat | ☐ |
| 29 | AI 챗 + 스트리밍 패스스루 | ai-chat · gateway | ☐ |
| 30 | 내보내기 DOCX·HWP — `md`·`txt`·PDF 는 브라우저가 처리한다 (§0.9·§0.17) | content | ☐ 메뉴에서는 비활성 |
| 31 | `config.json` 기본값을 `api` 로 전환 (§0.10) | frontend | ✅ `#9` |
| 32 | `?data=api` 가 로그인 왕복에서 사라지던 문제 (§0.10) | frontend | ✅ `#8` |
| 33 | 에피소드 순서 바꾸기 — `PATCH /episodes/{id}/position` 없음 (§0.10) | content · gateway · frontend | ☐ |
| 34 | 앱이 적재한 설정으로 서비스를 만들지 않던 버그 (§0.11) | frontend | ✅ `#10` `#11` |
| 35 | 모르는 관계 키가 저장으로 지워지던 문제 (§0.12) | frontend | ✅ |
| 36 | 새로 고침 직후 휴지통 복원 실패 (§0.12) | frontend | ✅ |
| 37 | 버전 목록이 문서 전체를 다시 받던 낭비 (§0.12) | frontend | ✅ |
| 38 | 서버 없는 4개 포트를 mock 대신 "준비 중" 으로 (§0.13) | frontend | ✅ `#12` |
| 39 | 로그인이 자기 리다이렉트를 취소하던 경합 (§0.13) | frontend | ✅ `#13` |
| 40 | 로그인 성공 뒤 아무도 앞으로 보내지 않던 문제 | frontend | ✅ `#14` |
| 41 | 관계 양방향 저장 (§0.14) | content · frontend | ✅ `#10` `#16` |
| 42 | 로그인 계정용 시드 스크립트 | frontend | ✅ `#15` |
| 43 | 세션 인증으로 전환 — 로그아웃 경로·오류 코드·재발급 제거 (§0.15) | frontend | ✅ |
| 44 | 탭 간 인증 전환 조율 (`FRONTEND_AUTH_CONTRACT.md` "인증 전환과 늦은 응답") | frontend | ☐ |
| 45 | valkey ACL 에 `PTTL`·BFF 스크립트 권한 추가 (§0.16) | 클러스터 | ✅ 적용됨 |
| 46 | 쓰지 않는 JWT env·ConfigMap·볼륨 정리 (§0.16) | gitops · 클러스터 | ☐ |
| 47 | 타임라인·관계도를 RDB 투영본으로 (§0.17) | content · gateway · frontend | ✅ `#11` `#5` |
| 48 | 메모 수동 저장·삭제·순서·디자인 통일 (§0.17) | frontend | ✅ `#18` |
| 49 | 관계 설명 + "관련 …" 라벨 (§0.17) | content · frontend | ✅ |
| 50 | 인쇄 양식 + DOCX·HWP 비활성 (§0.17) | frontend | ✅ `#18` |
| 51 | 검색 기능 제거 (Elasticsearch 로 대체 예정) | frontend | ✅ `#18` |
| 52 | content CI 가 push 에 트리거되지 않은 원인 확인 (§0.17) | content | ☐ |
| 53 | 본문 저장 형식 Markdown → 에디터 JSON (§0.18) | content · frontend · docs | ✅ `#12` `#19` |
| 54 | `body_md` 컬럼 삭제 — 모든 행의 `body_json` 확인 후 | content | ☐ |
| 55 | 문단별 정렬·들여쓰기 (`schema_version` 2) | content · frontend | ☐ |
| 56 | 타임라인 즐겨찾기에 원고 줄을 넣을지 결정 (§0.19) | frontend | ☐ |
| 57 | BFF 가 본문 JSON 을 중계하지 못해 문서가 안 열리던 문제 | gateway | ✅ `#6` |
| 58 | 타임라인이 분류 폴더를 다 보여주지 않던 문제 (§0.19) | frontend | ✅ `#20` |
| 59 | 관계를 한 쌍에 한 행으로 (§0.20) | content · frontend(mock) | ✅ |

**18번 브라우저 E2E 는 시작됐다.** 헤드리스 브라우저로 Google 로그인 화면까지 도달하는 것을 확인했고(§0.11), 그 과정에서 앱이 서버와 말하지 못하던 버그를 잡았다. 남은 것은 **실제 Google 계정으로 승인하는 한 걸음**이고, 그건 사람이 해야 한다.

graphRAG·Kafka·AI 를 뺀 content 엔드포인트는 **전부 구현됐다**(38개). 남은 content 작업은 정리 배치(25)와 내보내기(30)뿐이다. 프론트 편집 경로도 내보내기 두 형식만 남았다(§0.9).
