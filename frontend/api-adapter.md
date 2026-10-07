# 서버 연결 — API 어댑터

> 작성일: 2026-09-24 · 최신화: 2026-09-28
> 상태: **배포 기본값이 `api` 다.** 포트 13개 중 9개가 실제 API 로 돌고, 서버가 없는 4개는 mock 이 아니라 **거절한다.**
> 전제: [`../CONTENT_PROJECT_API.md`](../CONTENT_PROJECT_API.md), [`mock-and-server-contract.md`](mock-and-server-contract.md)

## 1. 포트 단위로 옮긴다

`createServices` 가 mock 위에 **서버에 있는 포트만** 겹친다.

```ts
return { ...mock, ...createApiServices(config.apiBaseUrl) };
```

서버는 한 번에 완성되지 않는다. 전체를 구현해야만 전환할 수 있다면 마지막 엔드포인트가 끝날 때까지 프론트는 계속 mock 으로 돈다. 포트 단위로 옮기면 끝난 것부터 실제 데이터를 쓰고 나머지 화면은 그대로 동작한다.

| 포트 | 지금 | 기다리는 것 |
|---|---|---|
| `auth` `account` | **API** | — |
| `projects` `files` `documents` `versions` | **API** | — |
| `memos` `workspaceState` `favorites` `feedback` | **API** | — |
| `graph` | **API** (content RDB 의 `GET /projects/{id}/graph`) | — |
| `help` | **정적 글** (`services/api/help.ts`, 앱과 함께 배포) | — |
| `refresh` | **거절** (`unavailable`) | content 최신화 API · graph-rag HTTP · 이벤트 파이프라인 |
| `chat` | **거절** | ai-chat 서비스 |

**서버가 없는 포트는 mock 으로 덮지 않는다.** 실제 API 로 도는 사이트에서 mock 그래프·대화는 보는 사람이 자기 자료로 믿는다. 그래서 `unavailable` 로 거절하고 화면이 "준비 중" 이라고 말한다(`services/api/unavailable.ts`, `features/common/preparing-state.tsx`).

**포트를 하나 더 옮길 때 할 일**은 `services/api/<port>.ts` 를 쓰고 `services/api/index.ts` 에 한 줄 더하는 것뿐이다.

## 2. 파일 구조

```text
src/services/api/
  http.ts        ApiClient — base URL, 쿠키, CSRF·조건부 헤더, 오류 변환
  errors.ts      서버 code → ServiceErrorCode 표와 사용자 문구
  mapping.ts     분류 코드 ↔ 문서 종류, 관계 키, 속성 합치기/나누기, 아이콘
  unavailable.ts 서버가 없는 포트(refresh, chat)를 거절하는 구현
  auth.ts        projects.ts   files.ts   documents.ts   graph.ts
  memos.ts       workspace-state.ts   favorites.ts   feedback.ts   help.ts
  index.ts       createApiServices(): Partial<Services>
```

`http.ts` 하나가 base URL·쿠키·오류를 맡으므로 포트 어댑터는 경로와 본문만 안다.

## 3. 서버와 화면이 다른 곳 — 전부 어댑터가 흡수한다

| 서버 | 화면 | 어디서 |
|---|---|---|
| `name` | `title` | `projects.ts` |
| snake_case | camelCase | 각 어댑터 |
| `LOCATION` | `place` | `mapping.ts` 표 |
| `RESTORE` | `PRE_RESTORE` | `documents.ts` |
| 텍스트 속성 + 관계 두 목록 | 속성 한 목록 | `mapping.ts` |
| 분류 폴더에 행이 없음 | 트리 노드가 필요 | `mapping.ts` 의 `category:<CODE>` id |
| `icon` 없음 | `icon` 필수 | `mapping.ts` 가 id 에서 파생 |

**분류 코드는 대문자 변환으로 유추하지 않는다.** `place` 는 `LOCATION` 이다. 한 글자만 어긋나도 잘못된 폴더에 문서가 생기므로 표로 둔다.

**트리는 화면이 조립한다.** 서버는 폴더·에피소드·문서 세 목록을 주고, 분류 폴더는 전역 시드라 행이 없다. 그 노드 id(`category:CHARACTER`)는 어댑터가 만든 값이고 요청으로 돌아갈 때 다시 코드가 된다. 이 id 모양을 아는 곳은 `mapping.ts` 두 함수뿐이다.

## 4. 오류

서버는 `{ code, message, next_action }` 을 준다. `code` 가 계약이고 `message` 는 진단용 영어이므로 **화면에 그대로 띄우지 않는다** — 사용자 문구는 `errors.ts` 가 고른다.

표에 없는 코드는 상태 코드로 떨어진다(409 → `duplicate`, 404 → `not-found` …). 서버가 새 코드를 추가해도 화면이 깨지지 않고, 표에 한 줄 더하면 문구가 정확해진다.

**문서 저장 충돌만 예외다.** `409 DOCUMENT_CONFLICT` 는 `current` 와 `base` 를 함께 싣고, 어댑터는 이것을 `ConflictError` 로 바꿔 에디터의 3-way 병합에 넘긴다. 같은 409 인 `DOCUMENT_LOCKED` · `FILE_TITLE_TAKEN` 과 코드로 갈라야 화면이 맞는 안내를 낸다.

## 5. 신원 — 단일 세션 쿠키

브라우저는 **HttpOnly 세션 ID 쿠키 하나**만 쓴다. 프론트는 그것을 읽지 못하므로 "로그인했는가" 를 `GET /auth/users/me` 로 **서버에 물어서** 판단한다. `X-User-Id` 는 인증 수단이 아니고, gateway 가 검증한 값을 안쪽으로 **설정**한다.

| 규칙 | 이유 |
|---|---|
| 모든 요청에 `credentials: "include"` | 쿠키가 신원의 전부다 |
| 상태를 바꾸는 요청에 `X-LS-CSRF: 1` | 폼·본문·쿼리로 대신할 수 없어 헤더의 존재 자체가 방어가 된다. 로그아웃도 같다 |
| **재발급을 하지 않는다** | 보호 요청이 성공할 때마다 서버가 세션 수명을 연장한다. heartbeat 를 두지 않는다 |
| **401 을 자동 재전송하지 않는다** | 계약이 금한다. 저장·수정은 결과 유실 위험이 있어 각 도메인의 중복 방지(`X-Save-Id`)를 따른다 |
| 로그아웃은 `POST /auth/sessions/revoke` | 토큰 시절의 `/auth/tokens/revoke` 는 사라졌다. `session_revocation` 이 `unconfirmed` 면 완전한 성공으로 표시하지 않는다 |

온보딩 상태와 계정 삭제도 같은 계정 API 에 있다.

| 요청 | 어댑터 |
|---|---|
| `GET /auth/users/me` 의 `onboarding_completed` | `User.onboardingCompleted`. 필드가 없으면 완료로 본다 |
| `PUT /auth/users/me/onboarding` | `account.completeOnboarding()`, 204 |
| `POST /auth/users/me/deletion` `{confirmation_email}` | `account.deleteAccount()`, 204. 인증 전환 안에서 부르고 성공하면 BFF 가 세션 쿠키를 지운다 |
| `POST /projects/sample` | `projects.createSample()`, 201 과 프로젝트 |

흐름과 오류 처리: [onboarding-and-account-deletion.md](onboarding-and-account-deletion.md).

계약: `loresentry-gateway/docs/auth/SESSION_FLOW.md`, `docs/FRONTEND_AUTH_CONTRACT.md`.

## 6. 전환 방법

```text
?data=api     그 탭에서만 실제 API 를 쓴다
?data=mock    그 탭에서만 mock 을 쓴다
```

`public/config.json` 의 `dataSource` 는 **이미 `"api"` 다.** 그냥 접속하면 실제 API 로 돈다. `?data=mock` 은 그 탭에서만 화면을 따로 보는 데 쓴다.

재정의는 **탭 수명 동안 기억한다**(`sessionStorage`). Google 로그인은 사이트를 떠나 고정된 `/login?result=...` 로 돌아오므로, 쿼리에만 의존하면 그 왕복에서 재정의가 사라진다.

## 7. `dataSource=api` 에서 아직 안 되는 것

| 기능 | 왜 |
|---|---|
| 사용자 섹션 만들기·삭제 | 폴더 모델 결정이 끝나지 않아 서버에 없다. mock 으로 되돌리지 않고 **거절한다** — 새로 고침에 사라지는 자료를 만들지 않는다 |
| 에피소드 순서 바꾸기 | 서버에 에피소드 이동 엔드포인트가 없다 |
| 내보내기 | 서버 렌더링이 없다. PDF 는 화면 인쇄로 따로 처리한다 |
| 이미지 업로드 | **프론트에 포트도 UI 도 없다.** 서버 3개 엔드포인트와 BFF 라우트는 준비돼 있다 |
| AI 최신화·AI 챗 | 서버가 없다. mock 으로 덮지 않고 "준비 중" 으로 알린다. 첫 프로젝트 투어는 최신화가 없으면 그 단계를 뺀다 |

내보내기 `md`·`txt` 는 서버가 필요 없어 **브라우저에서 만든다.** PDF 는 화면 인쇄다. DOCX·HWP 만 서버를 기다린다.

## 8. 검증

- 프론트 테스트 **246개**. `api-services.test.ts` 는 `fetch` 를 가로채 경로·헤더·본문·오류 변환을 확인한다.
- **배포된 API 에 대한 계약 확인.** 단위 테스트는 `fetch` 를 가로채므로 필드 이름이 서버와 어긋나도 통과한다. 그래서 실제 `api.loresentry.com` 응답으로 따로 확인했다.
- **앱 자신의 배선도 테스트한다.** 어댑터가 옳아도 앱이 그것을 쓰지 않으면 소용이 없다 — 실제로 `config.json` 이 `api` 인데 앱이 mock 으로 굳어 있던 적이 있다(`CONTENT_PROJECT_API.md` §0.11). `providers.test.tsx` 가 그 배선을 고정한다.
