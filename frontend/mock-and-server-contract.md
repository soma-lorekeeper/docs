# 서비스 계약과 mock

백엔드 API 가 아직 없어서 화면은 **서비스 포트**(인터페이스)에만 기대고, 지금은 브라우저 안의 mock 구현이 그 포트를 채운다. API 가 정해지면 `src/services/api/` 에 같은 포트를 구현한 HTTP 어댑터를 더하고 `public/config.json` 의 `dataSource` 를 `"api"` 로 바꾸면 된다(`src/services/create-services.ts`).

## 포트 (`src/services/ports.ts`)

| 포트 | 주요 동작 |
| --- | --- |
| `AuthService` | 세션 조회, Google 로그인 시작, 로그아웃 |
| `AccountService` | 계정 조회, 표시 이름 변경 |
| `ProjectService` | 목록·생성·이름 변경·휴지통·복원·영구 삭제·설정 |
| `FileService` | 파일 트리, 생성·이름 변경·이동, 휴지통·복원·영구 삭제, 즐겨찾기, 섹션·에피소드 삭제 |
| `DocumentService` | 문서 읽기, 저장(If-Match·X-Save-Id), 잠금, 내보내기 |
| `VersionService` | 버전 목록·저장·복원·삭제 |
| `MemoService` | 프로젝트·파일 메모 목록·생성·수정·삭제 |
| `SearchService` | 프로젝트 검색 |
| `GraphService` | 프로젝트 그래프(노드·엣지·에피소드) |
| `RefreshService` | 그래프 최신화 실행·조회·반영·폐기 |
| `ChatService` | 세션 목록·생성·이름 변경·삭제, 메시지, 스트리밍 전송(AbortSignal) |
| `WorkspaceStateService` | 작업공간 레이아웃 저장·복원 |
| `HelpService` | 사용 가이드 목록 |

오류는 `ServiceError(code)` 로 통일한다: `network`, `not-found`, `validation`, `duplicate`, `locked`, `busy`, `unknown`. 문서 저장 충돌만 `ConflictError(current, base)` 로 따로 던진다.

## mock 구현 (`src/services/mock/`)

- 데이터는 `MockDb` 한 덩어리이고 `localStorage` 에 저장된다. 스키마가 바뀌면 `MOCK_DB_VERSION` 을 올려 시드로 초기화한다(현재 7).
- 시드는 새로 쓴 이야기 "유리 정원의 기록"이다. graph-visualization 의 ORV 픽스처는 저작권 문제로 가져오지 않았다.
- 모든 호출은 `simulate(operation, fn)` 을 지나며 지연(기본 280ms, ±30%)과 실패 규칙을 적용하고, 결과를 `structuredClone` 해서 돌려준다(화면이 mock 데이터를 직접 고치지 못하게).

### mock 제어

URL 의 `?mock=` 로 실패·지연을 넣는다. 쉼표로 여러 규칙을 잇는다.

```text
?mock=search.query:fail            검색 요청이 항상 실패
?mock=memos.update:once            메모 저장이 한 번만 실패(재시도하면 성공)
?mock=projects.list:hang           목록이 끝없이 로딩
?mock=files.*:fail                 files 네임스페이스 전체 실패
?mock=latency:1500                 모든 요청 지연 1.5초
?mock=help.guides:fail,latency:0   조합
```

모드는 `fail`(항상), `once`(한 번), `hang`(응답 없음) 세 가지다. 연산 이름은 각 mock 파일의 `simulate("…")` 첫 인자다.

## 서버에 대한 잠정 가정

코드에서는 `서버 가정` 또는 `요구사항 §` 주석으로 표시했다. 확정되면 주석과 이 표를 함께 고친다.

| 가정 | 근거 | 코드 위치 |
| --- | --- | --- |
| Google OAuth 는 Gateway/BFF 가 리다이렉트로 처리하고, 실패 사유는 복귀 URL 의 `?auth=canceled\|failed\|expired` 로 온다 | 미확정 | `services/ports.ts` AuthService |
| 파일·폴더는 한 트리이고 `rank` 는 문자열 fractional index 다 | DOCUMENT_EDITING_PROPOSAL §4.1 | `domain/models.ts` |
| 사용자 섹션은 최상위 폴더이고, 섹션을 지우면 같은 이름의 일반 폴더로 파일 영역에 남는다 | 와이어프레임 27·86 (요구사항 §4.1보다 넓다) | `domain/models.ts`, `ports.ts` |
| 에피소드 폴더를 지우면 회차는 원고 폴더로 돌아간다 | 와이어프레임 166 | `ports.ts` deleteEpisode |
| 본문은 Markdown 문자열 하나, `revisionNo` 가 If-Match 토큰이다 | DOCUMENT_EDITING_PROPOSAL §2·§5 | `domain/models.ts`, `ports.ts` |
| 저장 충돌(409)은 현재 본문과 공통 조상을 함께 돌려준다 | DOCUMENT_EDITING_PROPOSAL §5 | `ports.ts` ConflictError |
| 버전은 제목·본문·속성 전체 스냅샷이고, 복원 전에 PRE_RESTORE 를 남긴다 | DOCUMENT_EDITING_PROPOSAL §4.2, 요구사항 §6.2 | `domain/models.ts`, `ports.ts` |
| 내보내기(PDF·DOCX·HWP)는 서버가 파일을 만들어 URL 을 준다. mock 은 MD·TXT 만 실제로 만든다 | 미확정 | `ports.ts`, `mock/documents.ts` |
| 에디터 글꼴·크기·줄 간격·정렬은 문서가 아니라 기기별 사용자 설정이다 | DOCUMENT_EDITING_PROPOSAL §2.3 | `documents/editor-prefs.ts` |
| 검색은 활성 원고·설정 문서의 제목·본문만, 관련도 순·동률이면 최근 수정 순 | 요구사항 §7.1 | `ports.ts` SearchService |
| "최근에 연 파일"은 서버의 사용자별 열람 기록이다. mock 은 최근 수정 순으로 대신한다 | 미확정 | `workspace/views/new-tab-view.tsx` |
| 그래프는 graph-rag 가 Neptune 투영본을 프로젝트 단위로 준다. 엣지는 관계 속성을 가진 문서 → 대상 문서 방향이다 | GRAPH_INBOX_PATTERN §3 | `domain/models.ts`, `ports.ts` |
| 최신화는 run → proposal 구조이고 확정 전에는 문서를 바꾸지 않는다. 확정 시 **제안 id 별** 최종값(삭제는 null)을 한 번에 보낸다 — 새로 생길 문서는 아직 파일 id 가 없기 때문이다 | DOCUMENT_EDITING_PROPOSAL §4.3 | `domain/models.ts`, `graph-refresh/merge.ts` |
| 추출 뒤 실제 문서가 또 바뀌었으면 반영을 거절한다(STALE). mock 은 제안의 `baseRevisionNo` 와 문서 revision 을 견준다 | TABLE_AND_LOGIC §7.6 | `mock/refresh.ts`, `graph-refresh/graph-diff-modal.tsx` |
| 최신화 추출 중에는 같은 요청을 다시 실행할 수 없다 | 요구사항 §8.2 | `ports.ts` RefreshService |
| AI 답변은 스트리밍하고, 중단된 미완성 답변은 기록에 남지 않는다 | 요구사항 §8.1 | `ports.ts` ChatService |
| 메모는 본문 전체를 덮어쓰고 버전 충돌을 검사하지 않는다(마지막 저장 우선). 그래서 편집기는 자기가 편집 중이 아닐 때 다른 편집기의 저장으로 다시 맞춘다 | 미확정 | `memos/use-memo-autosave.ts` |
| 작업공간(탭·패널·그래프 보기)은 서버에 저장해 프로젝트를 다시 열 때 복원한다 | 요구사항 §3 | `ports.ts` WorkspaceStateService |
| 사용 가이드는 서버·CMS 에서 받아온다(불러오기 실패 화면이 있어서) | 와이어프레임 90, 미확정 | `ports.ts` HelpService |
| 피드백은 외부 폼 URL(`config.json` 의 `feedbackUrl`)로 연다 | 미확정 | `help/workspace-help-view.tsx` |
| 가져오기는 TXT·MD 만 받는다. DOCX 등은 범위 미확정 | 요구사항 §4.2 | `workspace/use-import-document.ts` |

## `TABLE_AND_LOGIC.md`(2026-09-18) 와 어긋나는 점

위 가정의 상당수는 `DOCUMENT_EDITING_PROPOSAL.md` 를 근거로 했는데, 이 문서는 `TABLE_AND_LOGIC.md` 로 대체되는 중이다. 새 제안과 현재 프론트가 다른 곳:

| 주제 | TABLE_AND_LOGIC | 현재 프론트 | 영향 |
| --- | --- | --- | --- |
| 폴더 | 기본 분류 폴더는 전역 시드, 사용자가 만드는 폴더는 에피소드뿐(§4.1) | 와이어프레임대로 사용자 섹션·일반 폴더를 만들고 옮길 수 있다 | 사이드바의 섹션·폴더 기능을 뺄지 결정 필요 |
| 최신화 비교 | base·left·right 세 스냅샷, 3-way 로 충돌 구간 표시(§7.4) | left·right 2-way 만 비교 | 충돌 표시를 더하려면 run 응답에 base 가 필요 |
| 모달 편집 | 양쪽 직접 편집 가능, 편집마다 좌·우 스냅샷을 `PATCH` 로 저장해 다시 열어도 유지(§7.5) | 화살표로만 옮기고 직접 편집은 막았다(사용자 결정). 모달을 닫으면 작업이 사라진다 | 결정 충돌. 초안 저장 API 가 생기면 `MergeState` 를 그대로 보내면 된다 |
| 최종 반영 | 문서별로 `side: LEFT \| RIGHT` 를 보낸다. 반영 전 실제 문서가 바뀌었으면 STALE(§7.6) | 전체 문서를 모아 한 번에 확정하고 제안 id 별 왼쪽 값을 보낸다. mock 은 추출 뒤 문서가 바뀌었으면 반영을 거절하고 모달에 이유를 띄운다 | 어댑터에서 문서별 요청으로 풀면 된다. 전용 STALE 화면은 아직 없다 |
| 메모·즐겨찾기 | 이번 범위에서 제외 | 와이어프레임에 있어 mock 으로 구현 | 서버 테이블이 없으니 로컬 저장으로 둘지 결정 필요 |
