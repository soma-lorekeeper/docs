# 프론트엔드 (loresentry-frontend)

> 갱신일: 2026-09-20
> 브랜치: `loresentry-frontend` main (PR #4 로 병합, https://loresentry.com 에 배포)
> 상태: 와이어프레임 전 화면을 **mock 데이터**로 구현했다. 백엔드 API 어댑터는 아직 없다.

기존 프론트 코드를 모두 걷어내고 빈 상태에서 다시 만들었다. 보존한 것은 Pencil 와이어프레임(`docs/design/lorekeeper.pen`, `lorekeeper.lib.pen`), `.pen` 스크립트 노드가 참조하는 `graph-canvas.js`·`timeline-table.js`, 그리고 배포 파이프라인(`.github/workflows/ci-cd.yaml`, `docs/deploy/`, `public/config.json`, devcontainer)뿐이다.

## 문서

| 문서 | 내용 |
| --- | --- |
| [architecture.md](architecture.md) | 기술 스택, 폴더 구조, 라우팅, 작업공간 탭·분할 모델, 상태 관리, 문서 편집 세션 |
| [design-system.md](design-system.md) | Pencil → 토큰 파이프라인, 테마 모드, 프리미티브, 아이콘, 화면 카탈로그 |
| [mock-and-server-contract.md](mock-and-server-contract.md) | 서비스 포트와 mock 구현, `?mock=` 실패·지연 주입, 서버에 대한 잠정 가정 목록 |
| [graph-visualization-port.md](graph-visualization-port.md) | graph-visualization 실험 레포에서 옮겨 온 알고리즘과 바꾼 점 |
| [screens-and-decisions.md](screens-and-decisions.md) | 와이어프레임 화면별 구현 위치, 와이어프레임과 다르게 한 점, 남은 결정 사항 |

## 실행

```bash
cd loresentry-frontend
pnpm install
pnpm dev                 # http://localhost:3000 → /login 에서 "Google로 계속하기"
pnpm check:cdn           # CI 와 같은 검사: 포맷·토큰·테스트·린트·타입·빌드·정적 export 검증
```

- 로그인은 mock 이라 버튼만 누르면 된다. 데이터는 브라우저 `localStorage` 에 저장되고, 시드 프로젝트 "유리 정원의 기록"이 들어 있다.
- 실패·지연 화면을 보려면 URL 에 `?mock=` 을 붙인다(예: `/workspace/?projectId=glass-garden&mock=search.query:fail`). 자세한 규칙은 [mock-and-server-contract.md](mock-and-server-contract.md#mock-제어).
- 테마는 프로젝트 목록 왼쪽 위 계정 메뉴의 "화면 테마"에서 바꾼다(시스템·다크·라이트).

## 한눈에 보는 현황

- 와이어프레임 150장(다크 75 + 라이트 75) 가운데 화면 단위로 따지면 전 화면을 구현했다. 화면별 대응은 [screens-and-decisions.md](screens-and-decisions.md).
- 테스트 190개(vitest). graph-visualization 에서 옮긴 레이아웃·물리·펼침·필터·타임라인·줄 diff 테스트를 시드 데이터로 다시 돌린다.
- `pnpm check:cdn` 통과. 정적 export(`out/`)는 CloudFront 재작성 함수가 기대하는 `/<route>/index.html` 구조를 검증한다.
- **확인이 필요한 결정**이 남아 있다. 특히 2026-09-18 자 `TABLE_AND_LOGIC.md` 가 이전 `DOCUMENT_EDITING_PROPOSAL.md` 를 대체하면서 폴더 모델과 최신화 병합 방식이 달라졌다. [screens-and-decisions.md의 남은 결정](screens-and-decisions.md#남은-결정-사항)을 먼저 봐 주세요.
