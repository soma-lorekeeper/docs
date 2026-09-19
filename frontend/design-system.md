# 디자인 시스템

와이어프레임(`loresentry-frontend/docs/design/lorekeeper.pen`, 컴포넌트 라이브러리 `lorekeeper.lib.pen`)이 단일 원천이다. 코드는 Pencil 변수와 화면 목록을 **동기화 스크립트로 가져와** 쓰고, 값을 손으로 옮겨 적지 않는다.

## Pencil → 토큰 파이프라인

```text
lorekeeper.lib.pen 변수(64개: 색 38·숫자 24·문자 2, 테마 축 mode = dark | light)
   │  Pencil 앱에서 스니펫 실행 → 결과를 붙여 넣기
   ▼
scripts/sync-pencil.mjs variables   →  src/design-system/tokens/pencil-variables.json
   │
   ▼
pnpm tokens (scripts/generate-tokens.mjs)
   ├─ src/design-system/tokens/tokens.css   :root / [data-theme="light"] 의 --lk-* 변수
   └─ src/design-system/tokens/tokens.ts    TokenName 타입, THEME_MODES, cssVar()
```

- `.pen` 파일은 암호화돼 있어 Pencil 앱(또는 Pencil MCP)으로만 읽는다. 앱 열기: `open -a /Applications/Pen.app docs/design/lorekeeper.lib.pen`.
- 스니펫은 `node scripts/sync-pencil.mjs snippet variables`(화면 목록은 `snippet screens`)로 출력해 Pencil 에서 실행한다.
- `pnpm tokens:check` 가 CI 에서 생성물이 원천과 같은지 확인한다. 토큰 파일을 손으로 고치면 CI 가 막는다.
- 색은 컴포넌트에서 반드시 `var(--lk-color-…)` 로 쓴다. 캔버스처럼 CSS 변수를 못 쓰는 곳(그래프)은 `getComputedStyle` 로 토큰 값을 읽는다(`features/graph/engine/palette.ts`).

## 테마 모드

- 모드 목록은 Pencil 테마 축에서 온다(`THEME_MODES`). 지금은 `dark`(기본)·`light` 두 개다.
- 사용자 선택은 `system | dark | light` 이고 `localStorage["loresentry.theme"]` 에 저장한다(`design-system/theme/theme-store.ts`).
- 첫 페인트 전에 인라인 스크립트(`theme-script.ts`)가 `<html data-theme>` 를 정해 깜빡임이 없다.
- `color-scheme` 은 모드별 캔버스 색의 밝기로 정해져 스크롤바·폼 컨트롤도 따라간다.
- 모드를 새로 더하려면 Pencil 에 테마 값을 추가하고 `pnpm tokens` 만 돌리면 된다. 메뉴(`theme-menu.ts`)는 `THEME_MODES` 를 그대로 나열하므로 코드 변경은 이름표 한 줄뿐이다.

## 프리미티브 (`src/design-system/primitives`)

| 컴포넌트 | 쓰임 |
| --- | --- |
| `Button`, `IconButton` | variant `outline \| primary \| ghost`, size `sm 28 \| md 32 \| lg 40`, `busy` 스피너 |
| `Menu` | 메뉴 항목·구분선·그룹·체크, 파괴 동작 색, 방향키·Esc 처리 |
| `Popover` | 앵커 기준 고정 위치, 바깥 클릭·Esc 닫기. 포털 안 이벤트가 뒤쪽 행으로 새지 않게 막는다 |
| `Modal`, `DialogCard`, `DialogDetail`, `DialogBullets` | 네이티브 `<dialog>`, 스크림 3단계, 크기 sm/md/lg, 대상 표시 줄 |
| `TextField`, `TextAreaField` | dialog·settings 두 밀도, 필수·힌트·오류·글자 수 |
| `InlineNotice`, `StatusNotice`, `ToastProvider`/`useToast` | 인라인 안내와 오른쪽 위 토스트 |
| `SaveBar` | unchanged · changed · saving · saved · error 다섯 상태(계정·프로젝트 설정) |
| `Segmented` | 라디오 그룹형 전환(메모 범위, 메모 위치) |
| `SidebarLink`, `SidebarButton`, `EmptyState` | 사이드바 항목, 빈·오류·로딩 상태 |

## 아이콘

- lucide-react 에서 쓰는 것만 레지스트리(`icons/icon-registry.ts`)에 등록한다. 번들에 쓰지 않는 아이콘이 들어가지 않는다.
- 새 아이콘은 `node scripts/add-icons.mjs <lucide 이름>...` 로 등록한다. 사용은 `<Icon name="..." size={14} />`, 의미가 있으면 `label` 을 준다.
- 그래프 캔버스는 DOM 아이콘을 못 쓰므로 같은 lucide 도형 데이터를 `features/graph/engine/icon-shapes.ts` 에 옮겨 Path2D 로 그린다.

## 화면 카탈로그

`src/design-system/screens/pencil-screen-catalog.json` 은 `.pen` 의 1440×900 화면 150개(다크 75·라이트 75)의 id·이름 목록이다(2026-09-18 동기화). 화면별 구현 대응은 [screens-and-decisions.md](screens-and-decisions.md).

## 폰트

Geist(`next/font`, 가변 굵기)를 기본으로 하고 한글은 Apple SD Gothic Neo → Noto Sans KR 순으로 대체된다. 에디터 글꼴·크기·줄 간격은 사용자가 툴바에서 바꾸며 기기별로 저장한다.
