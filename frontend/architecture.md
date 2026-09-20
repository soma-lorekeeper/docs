# 프론트엔드 구조

## 기술 스택

| 영역 | 선택 | 이유 |
| --- | --- | --- |
| 프레임워크 | Next.js 16 App Router, `output: "export"`, `trailingSlash: true` | 기존 배포(S3 + CloudFront 정적 호스팅)를 그대로 쓴다. 서버 런타임이 없다. |
| 스타일 | CSS Modules + CSS 변수(`--lk-*`) | 토큰이 Pencil 변수에서 생성되고, 테마 전환이 변수 교체만으로 끝난다. |
| 서버 상태 | TanStack Query 5 | 쿼리 키를 한곳(`services/query-keys.ts`)에서 관리하고 무효화 규칙을 모은다. |
| 에디터 | Tiptap 3 + `@tiptap/markdown` | 본문을 Markdown 문자열 하나로 저장한다는 서버 가정에 맞춘다. |
| 그래프 | `react-force-graph-2d` + `d3-force-3d` | graph-visualization 실험 레포와 같은 렌더러. 브라우저 전용이라 `next/dynamic({ ssr: false })` 로 불러온다. |
| 테스트 | Vitest + Testing Library + jsdom | |

## 폴더 구조

```text
src/
  app/                 라우트(정적 페이지)와 전역 Provider
  config/              런타임 설정(public/config.json)
  design-system/       토큰·테마·아이콘·프리미티브·화면 카탈로그
  domain/              문서 종류, 도메인 모델 타입
  services/            서비스 포트(ports.ts), 쿼리 키, mock 구현
  features/            화면 단위 기능. 기능끼리는 서로의 공개 컴포넌트·훅만 쓴다
    auth, projects, account, help
    workspace/         셸, 탭·분할 레이아웃 모델, 사이드바, 뷰 레지스트리
    documents/         문서 뷰, 편집 세션, 에디터 툴바, 속성 표
    search, file-trash, project-settings, memos, chat, versions
    graph/engine/      graph-visualization 에서 옮긴 그래프 엔진
    graph, timeline, graph-refresh, diff
  shared/              포맷 함수, cx, 크기 관찰 훅
  test/                테스트 준비물(jsdom 보정, renderWithServices)
scripts/               Pencil 동기화, 토큰 생성, 아이콘 등록, 정적 export 검증
```

## 라우팅

정적 export 는 동적 세그먼트(`/projects/[id]`)를 빌드 시점에 알 수 없으므로 **쿼리 파라미터**를 쓴다. CloudFront 함수(`docs/deploy/cloudfront-rewrite.js`)는 `/path/` 를 `/path/index.html` 로 바꿔 준다.

| 경로 | 화면 |
| --- | --- |
| `/login/` | 로그인(`?auth=canceled\|failed\|expired` 로 실패 사유) |
| `/projects/` | 프로젝트 목록 |
| `/projects/trash/` | 프로젝트 휴지통 |
| `/projects/guide/?topic=<id>` | 사용 가이드 |
| `/logout/` | 로그아웃 완료 |
| `/workspace/?projectId=<id>&open=<tab>` | 작업공간. `open` 은 `file:<fileId>`, `search`, `graph`, `timeline`, `memo`, `trash`, `settings`, `help` |

`useSearchParams` 를 쓰는 페이지는 Suspense 로 감싼다(정적 export 요구 사항).

## 작업공간 모델

작업공간 안의 화면 전환은 URL 이 아니라 **탭 레이아웃 상태**로 한다(`features/workspace/model/layout.ts`).

```text
WorkspaceLayout
  panes[]            최대 2개(분할 보기, 와이어프레임 167)
    tabs[]           target = { kind: "file", fileId } | { kind: 뷰 종류 }
    activeTabId
  activePaneId
  sidebarOpen
  panels             memoOpen, memoDock(right|below), 메모 폭·높이, aiChatOpen
  graphView          그래프 분류 필터·에피소드 선택(프로젝트별 복원)
```

- 모든 변경은 `layoutReducer` 의 액션으로만 한다. 리듀서는 순수 함수라 단위 테스트가 있다.
- 레이아웃은 `WorkspaceStateService` 로 저장했다가 프로젝트를 다시 열 때 복원한다(요구사항 §3).
- 뷰 종류 → 화면은 `views/registry.tsx` 한곳에서 정한다. 새 뷰는 `WORKSPACE_VIEW_KINDS` 와 레지스트리에 한 줄씩 더하면 된다.
- 탭은 숨겨질 뿐 언마운트되지 않는다. 검색어·스크롤 같은 탭 안 상태가 유지된다.
- 탭 닫기는 `closeTab(paneId, tabId)` 을 거친다. 뷰는 `registerCloseGuard(paneId, tabId, guard)` 로 닫기를 가로챌 수 있다(프로젝트 설정의 "저장하지 않고 나갈까요?", 와이어프레임 75).
- 뷰가 탭 이름·아이콘을 바꿔야 할 때는 `setTabLabel(paneId, tabId, label)` 을 쓴다(도움말 → "사용 가이드").
- 닫기 가로채기와 탭 이름은 **창까지 합친 열쇠**(`tabKey(paneId, tabId)`)로 둔다. 같은 뷰를 두 창에 열면 탭 id 가 같아서, 창을 빼면 한쪽이 다른 쪽을 덮어쓴다.

## 서버 상태와 무효화

- 쿼리 키는 `services/query-keys.ts` 에만 둔다.
- 파일 트리·그래프·검색·프로젝트 목록에 영향을 주는 변경은 `invalidateProjectContent(queryClient, projectId)` 한 번으로 묶어 무효화한다.
- 전역 기본값: `retry: false`, `staleTime: 30s`, 창 포커스 재요청 끔. 실패 화면을 와이어프레임대로 바로 보여 주기 위해서다.

## 문서 편집 세션

`features/documents/document-session.ts` 는 React 밖의 작은 스토어이고, `useSyncExternalStore` 로 화면에 붙는다.

- 입력 후 0.8초 쉬면 저장, 계속 입력해도 5초마다 한 번은 저장한다.
- 저장 요청에 `If-Match`(revision)와 `X-Save-Id`(멱등 키)를 싣는다고 가정한다.
- 409 가 오면 서버가 준 현재 본문·공통 조상으로 **문단 단위 3-way 병합**(`merge-text.ts`)을 시도하고, 같은 문단이 겹치면 "내 변경 유지 / 최신 버전 불러오기"를 묻는다.
- 편집 중(또는 저장 요청이 떠 있는 동안)에 다른 곳의 저장이 캐시로 들어오면 **revision 을 올리지 않는다**. 올리면 다음 저장이 충돌 없이 남의 변경을 덮어쓰므로, 일부러 낡은 revision 으로 보내 409 를 받고 위 병합으로 넘어간다.
- 잠긴 문서는 편집을 막는다. 잠그기 전에 남은 변경을 먼저 저장한다.
- 에디터는 Tiptap 이고 본문은 Markdown 으로 오간다. 밑줄은 `++text++` 로 직렬화한다(Markdown 표준에 밑줄이 없어서).

## 접근성 원칙

- 메뉴는 WAI-ARIA menu 패턴(방향키 이동, Esc 로 닫고 트리거로 포커스 복귀).
- 파일 트리는 tree 패턴(방향키로 펼침·접기·부모 이동, Shift+F10 으로 행 메뉴).
- 탭은 tablist 패턴, 모달은 네이티브 `<dialog>` 로 포커스를 가둔다.
- 저장 상태·검색 결과 수 같은 변화는 `aria-live` 로 읽힌다.
- 애니메이션은 `prefers-reduced-motion` 을 따른다.
