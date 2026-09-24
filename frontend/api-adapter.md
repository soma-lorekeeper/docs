# 서버 연결 — API 어댑터

> 작성일: 2026-09-24
> 상태: **구현됨.** 프로젝트·파일·문서·버전·검색이 실제 API 로 동작한다. 나머지 포트는 mock 이다.
> 전제: [`../CONTENT_PROJECT_API.md`](../CONTENT_PROJECT_API.md), [`mock-and-server-contract.md`](mock-and-server-contract.md)

## 1. 포트 단위로 옮긴다

`createServices` 가 mock 위에 **서버에 있는 포트만** 겹친다.

```ts
return { ...mock, ...createApiServices(config.apiBaseUrl) };
```

서버는 한 번에 완성되지 않는다. 전체를 구현해야만 전환할 수 있다면 마지막 엔드포인트가 끝날 때까지 프론트는 계속 mock 으로 돈다. 포트 단위로 옮기면 끝난 것부터 실제 데이터를 쓰고 나머지 화면은 그대로 동작한다.

| 포트 | 지금 | 기다리는 것 |
|---|---|---|
| `projects` `files` `documents` `versions` `search` | **API** | — |
| `auth` `account` | mock | authentication 서비스 연동 + gateway 릴레이 |
| `memos` `workspaceState` | mock | 서버 테이블 결정 |
| `graph` `refresh` | mock | graph-rag · Neptune · AI |
| `chat` | mock | LLM provider, 스트리밍 타임아웃 |
| `help` | mock | 정적 번들로 갈지 결정 |

**포트를 하나 더 옮길 때 할 일**은 `services/api/<port>.ts` 를 쓰고 `services/api/index.ts` 에 한 줄 더하는 것뿐이다.

## 2. 파일 구조

```text
src/services/api/
  http.ts        ApiClient — base URL, 신원 헤더, 조건부 헤더, 오류 변환
  errors.ts      서버 code → ServiceErrorCode 표와 사용자 문구
  identity.ts    임시 신원(브라우저별 UUID). 로그인이 붙으면 사라진다
  mapping.ts     분류 코드 ↔ 문서 종류, 관계 키, 속성 합치기/나누기, 아이콘
  projects.ts    files.ts    documents.ts    search.ts    favorites.ts
  index.ts       createApiServices(): Partial<Services>
```

`http.ts` 하나가 base URL·신원·오류를 맡으므로 포트 어댑터는 경로와 본문만 안다.

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

## 5. 신원 — 임시

서버는 모든 요청에 `X-User-Id` 를 요구하고, gateway 는 아직 인증하지 않는다. 그래서 프론트가 브라우저마다 UUID 하나를 만들어 `localStorage["loresentry.devUserId"]` 에 둔다.

**즉 지금 배포된 API 는 누구나 읽고 쓸 수 있다.** Google 로그인이 붙으면 `identity.ts` 는 사라지고 gateway 가 검증한 신원을 넣는다.

## 6. 전환 방법

```text
?data=api     그 탭에서만 실제 API 를 쓴다
?data=mock    그 탭에서만 mock 을 쓴다
```

`public/config.json` 의 `dataSource` 를 `"api"` 로 바꾸면 기본값이 된다. **아직 바꾸지 않았다** — 바꾸면 배포된 사이트에서 시드 이야기("유리 정원의 기록")가 사라지고, 방문자마다 빈 작업공간을 받는다.

## 7. `dataSource=api` 에서 아직 안 되는 것

| 기능 | 왜 |
|---|---|
| 사용자 섹션 만들기·삭제 | 폴더 모델 결정이 끝나지 않아 서버에 없다. mock 으로 되돌리지 않고 **거절한다** — 새로 고침에 사라지는 자료를 만들지 않는다 |
| 에피소드 순서 바꾸기 | 서버에 에피소드 이동 엔드포인트가 없다 |
| 내보내기 | 서버 렌더링이 없다. PDF 는 화면 인쇄로 따로 처리한다 |
| 즐겨찾기 | `localStorage` 에 둔다. 원본 id 목록일 뿐이라 자료가 사라지지는 않는다. 서버 테이블이 생기면 `FavoriteStore` 구현만 바꾼다 |
| 메모·작업공간 복원·그래프·타임라인·AI 챗 | mock 그대로 동작한다 |

## 8. 검증

- 단위 테스트 28개(`api-services.test.ts`). `fetch` 를 가로채 경로·헤더·본문·오류 변환을 확인한다.
- **배포된 API 에 대한 계약 확인.** 단위 테스트는 `fetch` 를 가로채므로 필드 이름이 서버와 어긋나도 통과한다. 그래서 실제 `api.loresentry.com` 응답에 대해 어댑터가 읽는 필드가 전부 있는지 따로 확인했다(20개 항목, 어긋난 것 없음).
