# graph-visualization 실험 레포에서 옮긴 것

`soma-gitops/graph-visualization` 은 그래프·타임라인·Diff 알고리즘을 시험한 레포다. 서비스 코드는 이 레포를 **패키지로 가져오지 않고 소스를 옮겨** 쓴다. 옮길 때 알고리즘과 설명 주석은 그대로 두고, 저장소(store)·픽스처 의존만 서비스의 모델로 바꿨다. ORV 픽스처 데이터는 가져오지 않았고 테스트는 mock 시드로 다시 돌린다.

## 대응표

| 실험 레포 | 서비스 | 바꾼 점 |
| --- | --- | --- |
| `graph/packed-layout.ts` (+test) | `features/graph/engine/packed-layout.ts` | 서비스 엣지는 방향이 있어 A→B·B→A 가 함께 올 수 있다. 이웃을 중복으로 넣으면 BFS 가 같은 노드를 두 번 줄 세워 자리가 모자라므로 중복을 걸렀다. 테스트의 "269개 노드" 는 시드 노드 수로 일반화 |
| `graph/physics.ts` (+test) | `engine/physics.ts` | 그대로 |
| `graph/reveal.ts` (+test) | `engine/reveal.ts` | 그대로 |
| `graph/view-graph.ts` (+test) | `engine/view-graph.ts` | 분류 이름만 문서 종류로(`Character` → `character`) |
| `graph/highlight.ts`, `label-layout.ts`, `canvas-grid.ts`, `adjacency.ts` | `engine/` 같은 이름 | 어항 타임라인용 점 격자(`drawDotGrid`)는 쓰지 않아 뺐다 |
| `graph/episode-filter.ts` | `engine/episode-filter.ts` | 에피소드 이름 대신 id, 스토어 대신 `ProjectGraph` 를 받는다. 캐시는 원본 객체 기준 WeakMap |
| `graph/render-graph.ts` | `engine/render-graph.ts` | 스토어 revision 대신 `ProjectGraph` 객체 identity 로 캐시. 링크에 관계 키를 싣는다 |
| `graph/palette.ts` | `engine/palette.ts` | 그래프 전용 CSS 변수 대신 `--lk-*` 토큰을 읽고, 선·격자처럼 토큰에 없는 옅은 색은 토큰 색에 투명도를 얹어 만든다 |
| `graph/kind-icon.ts` | `engine/kind-icon.ts` + `icon-shapes.ts` | lucide 내부 경로(`__iconNode`)를 깊이 import 하던 방식이 새 버전에서 깨져(이름 변경, 일부 아이콘은 별칭만 남음) 도형 데이터를 파일로 옮겼다. Path2D 는 처음 그릴 때 만든다(정적 export 빌드에서 모듈을 읽어도 안전) |
| `graph/graph-2d.tsx` | `engine/graph-2d.tsx` | 즐겨찾기·관계 이름을 스토어가 아니라 props 로 받는다. **방향 화살표**를 붙였다. 줌·화면 맞춤·가운데 맞추기 손잡이(`GraphControls`)를 냈다. 노드 기본 크기 4 → 6(시드 규모에서 아이콘이 보이도록) |
| `graph/types.ts` `DEFAULT_KINDS` | `engine/types.ts` | 노드가 150개를 넘을 때만 원고·캐릭터·이벤트로 시작하고 안내를 띄운다 |
| `timeline/timeline-rows.ts` (+test), `timeline-data.ts` | `features/timeline/` | 회차 순서를 에피소드 순서 + 미배정 원고로 만든다. 열 이름은 원고 제목의 " · " 앞부분("12화"). 테스트는 시드 기준으로 다시 썼다 |
| `diff/line-diff.ts` (+test) | `features/diff/line-diff.ts` | 그대로. 버전 비교와 최신화 모달이 함께 쓴다 |
| `diff/merge.ts` | `features/graph-refresh/merge.ts` | 문서 모양을 `DocumentDraft`(제목·Markdown 본문·속성 목록)로 바꿨다. 본문은 문단 하나를 한 줄로 보고 덩어리를 옮긴다. 직접 편집(`editSingle`·`editList`)은 옮기지 않았다 |

옮기지 않은 것: `store.ts`·`favorites.ts`(서비스 쿼리와 즐겨찾기 API 가 대신한다), `cluster.ts`(배치에서 쓰지 않음), 어항 타임라인·문서 뷰·툴바 등 실험용 UI, `diff/doc-snapshot.ts`·`incoming.ts`(실험 레포의 문서 모양 전용).

## 화면에서 다르게 한 것

- 노드를 누르면 실험 레포처럼 1-hop 으로 **걸러내지 않고** 오른쪽 "선택한 노드" 패널을 연다(와이어프레임 146). 패널의 카드를 누르면 그 문서를 연다.
- hover 1-hop 강조와 관계 이름 표시는 그대로다. 관계 이름은 대상 문서 종류의 관계 이름("관련 캐릭터")이다.
- 분류 필터·에피소드 선택은 작업공간 레이아웃의 `graphView` 로 프로젝트마다 저장된다.
- 타임라인은 실험 레포의 접기·펼치기 연출(cascade)은 옮기지 않고 와이어프레임의 정적 표로 그렸다.
