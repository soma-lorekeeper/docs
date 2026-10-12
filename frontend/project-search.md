# 프로젝트 검색 다시 붙이기

> 작성일: 2026-10-12
> 상태: **배포됨.** `loresentry-frontend` #49(2026-10-12). content RDB 검색 그대로, 서버 변경 없음.
> 전제: 요구사항 §7.1, 와이어프레임 038–042(`Workspace · Search`), [`../CONTENT_PROJECT_API.md`](../CONTENT_PROJECT_API.md) §0.1(검색 1개)·§0.17(지운 것), [`api-adapter.md`](api-adapter.md)

## 0. 시작할 때 상태 (2026-10-12)

| 층 | 상태 |
|---|---|
| content | `GET /projects/{id}/search?q=` **살아 있다.** RDB 부분 일치, 제목 완전 일치 → 제목 부분 일치 → 본문 순, 동률이면 최근 수정 순. 50개 상한, 첫 일치 앞뒤 40자 스니펫, 휴지통·다른 프로젝트·남의 프로젝트 제외. `SearchApiTest` 10개 |
| gateway | 전용 라우트(`ContentApiController#search`)가 있고 `If-None-Match` 를 중계한다. 응답 모양은 content 와 같다 |
| frontend | `fb13d2c`(#18, 2026-09-30)에서 **포트·어댑터·mock·화면·탭 종류·사이드바 항목·테스트를 모두 걷어냈다.** 이유는 "Elasticsearch 로 갈 것이므로". 에디터 찾기·바꾸기와 그래프 노드 검색은 남았다 |
| Elasticsearch | 어느 레포에도 없다(gitops·content·gateway·graph-rag) |
| 남은 흔적 | 사용 가이드 "검색과 그래프" 주제가 아직 "사이드바의 검색에서 제목과 본문을 함께 찾습니다" 라고 말한다(지금은 사실이 아니다). `PRODUCT.md` 는 검색을 "준비 중(Elasticsearch로 대체 예정)" 에 둔다 |

그 사이 앱은 "회색 책상과 흰 시트" 세계로 다시 만들어졌다(`2c8231e`). 옛 검색 화면과 와이어프레임 038–042 는 1px 테두리 상자·36px 아이콘 타일·청록 종류 라벨을 쓰는데, 지금 `DESIGN.md` 는 셋 모두 금지한다. **옛 코드를 되살리는 게 아니라, 옛 구조를 근거로 지금 세계에서 다시 짓는다.**

## 1. 방향 — 지금 있는 RDB 검색으로 붙이고, ES 는 포트 뒤에서 갈아 끼운다

- 서버가 이미 있다. "서버가 없는 포트는 mock 으로 덮지 않는다" 원칙과도 부딪히지 않는다.
- 화면은 `SearchService` 포트와 `SearchHit` 모양에만 기댄다. ES 로 바꿀 때 content 응답 모양만 같으면 프론트는 고칠 것이 없다.
- 지금 검색의 한계는 화면에서 숨기지 않는다: 형태소 분석이 없어 "레나의" 는 그 글자가 그대로 든 곳만 찾고, 결과는 50개까지다.

## 2. 화면

모드는 Operate. 작가가 "그 이름이 몇 화에 나왔더라"를 찾아 바로 그 문서로 가는 것이 할 일의 전부다.

### 진입

- 사이드바 주 내비게이션 **맨 위**에 "검색"(와이어프레임 순서: 검색 · 그래프 · 타임라인 · 메모). 다른 보기와 같은 `SidebarButton`, 고르면 pill.
- 탭 하나(`kind: "search"`). 이미 열려 있으면 그 탭으로 가서 입력칸에 포커스한다. `?open=search` 딥링크는 `parseTabId` 가 kind 목록을 보므로 kind 만 더하면 열린다.

### 옛 화면 → 이번

| 자리 | 와이어프레임·옛 구현 | 이번 | 근거 |
|---|---|---|---|
| 머리 | 프로젝트 이름 맥락 줄 + "검색" + 설명 문장 + 개수 | "검색" + 개수(`aria-live`) | Quiet Copy Rule. `ViewPage` 도 이제 제목·meta 만 받는다 |
| 입력칸 | 46px, 1px 테두리 | sunken 44px · 10px, 포커스에서 `surface-default` + focus ring. 사용 가이드 검색칸과 같은 결 | Inputs |
| 목록 | 1px 테두리 상자, 행 사이 1px 선 | sunken 판(14px, 안쪽 4px, 행 사이 2px), 행 10px · 64px 안팎, hover 에서 `surface-default` 로 떠오름 | Lists, Elevation Not Lines |
| 행 아이콘 | 36px 회색 타일 위 아이콘 | 받침 없는 종류 아이콘, `data-kind` 원색 | 아이콘 타일 금지, Kind Tint |
| 종류 표시 | 청록 글자 | 12px `text-secondary` 종류 이름 | Two Voices — 청록은 행동·포커스·선택에만 |
| 오른쪽 끝 | ↗ 아이콘 | 없음 | 행 전체가 단추다 |
| 일치 표시 | 굵게 | 문서 안 찾기와 같은 Change Teal 바탕(`diff-modify-bg`) + 600 | "찾은 글자" 가 어디서든 같은 모양 — **결정 3** |

행 구성: 종류 아이콘 · 제목(600, 말줄임) · 종류 이름 / 스니펫 한 줄(말줄임) / 경로 12px `text-secondary`(예: `원고 › 1부`).

### 상태

| 상태 | 보이는 것 |
|---|---|
| 빈 입력 | 빈 상태 원 안 `search` 아이콘 + "원고와 설정 문서의 제목·본문에서 찾아요" |
| 첫 검색 중 | 입력칸 끝 스피너, meta "검색 중" |
| 다음 검색 중 | **이전 결과를 그대로 두고** 입력칸 끝 스피너만. 글자를 칠 때마다 목록이 사라졌다 나타나지 않게 — 와이어프레임 039 와 다르다(**결정 2**) |
| 결과 | 목록. 정확히 50개면 목록 아래 한 줄 "처음 50개만 보여요. 검색어를 더 구체적으로 적어 보세요." |
| 결과 없음 | `search-x` + "일치하는 문서가 없어요". 지우기는 입력칸의 × 가 맡는다 — 단추를 또 두면 같은 이름의 단추가 둘이 된다 |
| 오류 | `role="alert"`, 중립 면 + 아이콘, "검색하지 못했어요" + [다시 시도]. 검색어는 남는다 |

### 조작

- 입력: 250ms 디바운스. **끝에 붙은 낱자모는 떼고 보낸다** — "유ㄹ" 는 "유" 로, "ㄹ" 만 남으면 보내지 않는다. 조합 중(`isComposing`)을 통째로 막지 않는 이유: macOS 한글 입력기는 마지막 글자를 다음 키를 칠 때까지 조합 상태로 둬서, 막으면 "유리" 를 치고 기다려도 검색이 나가지 않는다.
- Esc: 검색어가 있으면 지운다. Enter: 첫 결과를 연다. 결과가 아직 오지 않았으면 기다림을 건너뛰고 바로 요청한다. ↓: 첫 결과로, 결과 사이 ↑/↓, 첫 결과에서 ↑ 는 입력칸으로.
- 결과를 누르면 그 파일 탭을 연다.
- 900px 이하 서랍 사이드바, 600px 이하 여백(32/16/48)은 다른 보기와 같이 따른다. 라이트·다크 두 테마.

## 3. 작업 단계

### 1단계 — 데이터 층 (화면 없이 테스트로 닫는다)

| 파일 | 할 일 |
|---|---|
| `domain/models.ts` | `SearchHit` 복원 |
| `services/ports.ts` | `SearchService`, `Services.search` |
| `services/api/search.ts`, `api/index.ts` | `fb13d2c` 의 어댑터 복원. 경로 이름표는 지금 `t()` 를 지나는 `DOCUMENT_TYPE_META` 라벨을 쓴다 |
| `services/mock/search.ts`, `mock/index.ts` | 옛 `bodyMd` 대신 `bodyToPlainText(body)` 로 다시 쓴다(본문이 에디터 JSON 이 됐다). 영어 시드에서도 돈다 |
| `services/query-keys.ts` | `search` 키, `invalidateProjectContent` 에 검색 무효화 |
| `documents/use-document-session.ts` | 본문만 바뀐 저장에도 검색 결과를 무효화(제목·관계가 바뀐 저장은 위 묶음 무효화가 이미 덮는다) |

테스트: 어댑터(경로·한글 인코딩·분류 코드 매핑·스니펫 보존), mock(제목 일치 우선·휴지통 제외·빈 검색어), 배선(`wired.search` 가 mock 이 아님).

### 2단계 — 화면

- `features/search/` 에 `queries.ts`, `search-view.tsx`, `search-view.module.css` 를 새로 쓴다. 옛 파일은 구조 참고만 한다.
- 검색칸이 세 번째 복사본이 된다(작업공간 도움말, 사용 가이드, 검색). `design-system/primitives` 에 `SearchField` 로 뽑아 셋이 같이 쓴다.
- `workspace/model/layout.ts` 에 `"search"`, `views/registry.tsx` 에 한 줄, 사이드바 `PRIMARY_NAV` 맨 앞에 한 줄.
- 문구는 `src/i18n/en/workspace.ts` 에 영어를 함께 넣는다(`coverage.test.ts` 가 강제한다). 개수는 `plural` 헬퍼.
- 테스트: 결과 목록, 실패 뒤 검색어 유지와 다시 시도, 조합 중 요청 없음, Esc·Enter·방향키, 영어판, 사이드바 항목, `?open=search`.

### 3단계 — 둘레

- **온보딩 무대·랜딩 장면**의 사이드바 사본(`onboarding/scenes.tsx`)에 검색 줄을 더한다 — "실제 작업공간 화면을 줄여 옮긴 것" 원칙. 줄이 하나 늘면 아래 자리가 34px 내려가므로 단계마다 카메라 구도를 다시 본다(**결정 5**).
- 작업공간 투어는 대상을 DOM 에서 재므로 영향이 없을 것이다. 확인만 한다.
- 사용 가이드 "검색과 그래프" 주제(ko·en)가 실제 동작과 맞는지 대조한다.
- `PRODUCT.md`: 준비 중 목록에서 빼고 동작 목록과 사이드바 목록에 넣는다. `DESIGN.md`: Navigation 의 보기 항목, 검색 결과 행과 일치 표시.

### 4단계 — 선택

- **결과에서 문서 안 찾기로 이어 주기.** 결과를 열면 에디터 찾기 막대가 그 검색어로 열려 첫 일치로 간다. 에디터에 `setFindQuery` 명령이 이미 있고, 찾기 막대 열림 상태가 툴바 안에 있어 "열 때 넘길 검색어" 를 받을 자리만 만들면 된다.
- **단축키** ⌘⇧F / Ctrl+Shift+F 로 검색 탭 열기. ⌘F(문서 안 찾기)와 짝이 맞는다.

### 5단계 — 검증

- `pnpm check:cdn`.
- `?data=mock` 과 로그인한 실제 API 둘 다. 단위 테스트는 `fetch` 를 가로채므로 배포된 응답으로 따로 확인한다(`api-adapter.md` §8 관례).
- 1440×900 라이트·다크, 900px 서랍, 600px 을 한 번에 찍고, 한 번 고치고, 한 번 더 확인한다.
- macOS 한글 입력기로 조합 중 요청이 나가지 않는지 손으로 확인한다.

### 6단계 — 문서 (`soma-gitops/docs`)

- `frontend/api-adapter.md` 포트 표에 `search` **API**.
- `frontend/screens-and-decisions.md` 038–042 줄과 "와이어프레임과 다르게 한 점".
- `frontend/mock-and-server-contract.md` 의 `SearchService`·`?mock=search.query:fail` 이 다시 사실이 되는지 대조.
- `frontend/README.md` 테스트 수, `CONTENT_PROJECT_API.md` 작업 표에 "검색 다시 붙임 — RDB 부분 일치, ES 전까지".
- 이 문서의 상태 줄.

PR 은 frontend 하나(`feat/project-search`), docs 하나. 서버는 고치지 않는다.

## 4. 서버 쪽에 알고 가는 것 (이번 범위 밖)

- 스니펫이 앞뒤로 잘렸는지 알려 주지 않는다. 화면은 끝은 말줄임으로 처리하지만 앞의 "…" 는 추측할 수 없다. 필요하면 `snippet` 에 잘림 여부를 더한다.
- 문서마다 첫 일치 하나만 스니펫이 된다. 제목만 일치하면 스니펫이 없다.
- `LIKE` 가 아니라 `position(lower(q) in lower(...))` 다. 결과는 `ILIKE '%q%'` 와 같고, 검색어의 `%`·`_` 가 와일드카드로 읽히지 않는다.
- 텍스트 인덱스 없이 프로젝트 인덱스로 좁힌 뒤 제목·본문을 훑는다. 프로젝트당 문서 수백 개를 전제로 한 선택이다. 빠르게 하려면 `pg_trgm` GIN 인덱스가 정석이지만 3글자 단위라 "레나" 같은 두 글자 검색어에는 거의 도움이 안 된다. 형태소까지 원하면 Elasticsearch(nori)다.
- 스니펫 위치를 Java `toLowerCase` 로 찾고 SQL 은 `lower()` 로 거른다. 소문자로 바꿀 때 길이가 달라지는 드문 글자에서 강조 구간이 어긋날 수 있다.
- 전체 개수가 없다. 50개에서 끊긴 것인지 정확히 50개인지 화면이 구분하지 못한다.

## 5. 정한 것

1. 지금 RDB 검색으로 붙였다(2026-10-12 사용자 결정).
2. 다음 검색 중에는 이전 결과를 흐리게(불투명도 0.72) 남긴다.
3. 일치 표시는 문서 안 찾기와 같다 — `diff-modify-bg` 바탕 + 1px `diff-modify` 고리 + 600.
4. 4단계(문서 안 찾기 이어 주기, 단축키)는 넣지 않았다. 다음 작업 후보다.
5. 온보딩·랜딩 무대에도 검색 줄을 더했다. 무대 창은 높이가 640px 로 고정이라 줄이 하나 늘면 아래가 밀린다. "작업공간"·"관계" 장면은 맨 아래 "도움말" 줄이 15px 잘린다. 에디터·파일 장면은 전부터 56px·145px 잘려 있었다.

## 6. 구현 기록

| 자리 | 파일 |
|---|---|
| 포트·모델 | `services/ports.ts` `SearchService`, `domain/models.ts` `SearchHit` |
| 어댑터 | `services/api/search.ts` — 분류 코드를 종류로 바꾸고 경로를 `[종류 이름, 에피소드]` 로 만든다 |
| mock | `services/mock/search.ts` — 서버와 같은 순서·50개 상한·앞뒤 40자 스니펫 |
| 화면 | `features/search/search-view.tsx`, `queries.ts` |
| 검색칸 | `design-system/primitives/search-field.tsx` — 작업공간 도움말과 같이 쓴다 |
| 배선 | `workspace/model/layout.ts` `"search"`, `views/registry.tsx`, 사이드바 `PRIMARY_NAV` 맨 앞, 온보딩 `scenes.tsx` |
| 무효화 | `invalidateProjectContent` 에 검색, 본문만 바뀐 저장에도 검색 무효화(`use-document-session.ts`) |

- **검색칸 폭.** 입력칸을 `size={글자 수}` 로 늘리던 방식은 한글 한 글자가 그 단위보다 넓어서 첫 글자가 잘려 보였다("레나" → "ᆌ나"). 작업공간 도움말에도 있던 문제다. 보이지 않는 같은 글자를 겹쳐 놓아 실제 글자 폭만큼 늘어나게 바꿨다.
- **스니펫.** 서버는 앞뒤 40자를 주지만 화면은 앞을 24자로 줄여 일치 구간이 좁은 창에서도 보이게 한다. 잘린 쪽에는 "…" 을 붙인다.
- **검증.** `pnpm check:cdn`(테스트 435개, 한국어·영어 빌드, 정적 export 검증). mock 데이터로 1440×900 라이트·다크와 600px 화면을 확인했다. 배포 뒤 `loresentry.com` 의 `/ko/`·`/en/` 작업공간 번들에 검색 화면이 들어 있는 것을 확인했다.
- **남은 확인.** 로그인한 계정으로 배포된 API 를 검색해 보는 것은 실제 Google 로그인이 필요해 하지 못했다.
