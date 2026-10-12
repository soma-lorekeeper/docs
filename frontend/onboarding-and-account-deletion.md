# 온보딩과 계정 삭제

> 작성일: 2026-09-30
> 브랜치: 각 저장소의 `feat/terms-onboarding-account-deletion` (auth·gateway·content·frontend)
> 와이어프레임: `lorekeeper.pen` 175–179(계정 삭제), 184–188(온보딩), Motion Spec 보드

약관 동의(LOREKEEPER-612–624)는 이미 main 에 있다. 이 문서는 그 뒤에 붙는 **첫 사용 안내**와 **회원 탈퇴**를 다룬다.

## 흐름

```text
Google 로그인 → (신규·재동의) 약관 동의 모달 → /projects
   └─ SessionGate: onboardingCompleted === false → /welcome/
        1 작업공간 → 2 속성 표로 잇기 → 3 그래프 → 4 타임라인 → 5 그래프 최신화 → 시작 고르기
        ├─ 예시 프로젝트 둘러보기  PUT onboarding → POST /projects/sample → /workspace/?projectId=
        ├─ 새 프로젝트 만들기      PUT onboarding → /projects/?create=1 (만들기 대화상자가 열린다)
        └─ 둘 다 나중에            PUT onboarding → /projects/

계정 메뉴 → 계정 설정 → 계정 삭제 → 떠나는 이유(선택) → 이메일 입력 → POST /auth/users/me/deletion → /goodbye/
```

## 온보딩 상태

| 항목 | 내용 |
| --- | --- |
| 저장 위치 | auth `users.onboarding_completed_at`. 기존 회원은 마이그레이션에서 가입 시각으로 채워 안내를 보지 않는다 |
| 조회 | `GET /auth/users/me` 의 `onboarding_completed`. 필드가 없는 이전 BFF 는 완료로 본다(`services/api/auth.ts`) |
| 완료 | `PUT /auth/users/me/onboarding` (204, 멱등). 성공하면 세션 캐시의 사용자도 완료로 바꾼다 |
| 우회 | `SessionGate` 가 모든 보호 화면에서 `/welcome/` 로 보낸다. 온보딩 화면만 `onboarding` 으로 우회를 끈다 |
| 다시 보기 | 사용 가이드의 "처음 안내 다시 보기" → `/welcome/?replay=1`. 서버를 부르지 않고, 마치면 가이드로 돌아간다 |
| 실패 | 완료 저장이 실패하면 이동하지 않고 시작 고르기에 오류를 띄운다. 이동하면 SessionGate 가 다시 온보딩으로 돌려보내기 때문이다 |

## 무대: 실제 작업공간 창

무대는 따로 그린 카드가 아니라 **실제 작업공간 창을 줄여 옮긴 것**이다(`features/onboarding/scenes.tsx`). 사이드바(프로젝트 전환·그래프·타임라인·메모·그래프 최신화·즐겨찾기·파일 트리), 탭 막대, AI 챗 버튼이 늘 보이고, 단계마다 내용과 사이드바 선택만 바뀐다. 서버를 부르지 않는 정적 화면이라 운영에서도 같게 보인다.

| 단계 | 창 안의 화면 | 비추는 곳 |
| --- | --- | --- |
| 1 작업공간 | 새 탭(마지막 작업 파일·새로 만들기·최근 파일), 원고 폴더 펼침 | 프로젝트 전환과 파일 트리 |
| 2 속성 표로 잇기 | 설정 문서 "레나 아르벨"의 속성 표. 관련 장소에 "북쪽 온실" 칩이 새로 붙는다 | 속성 표 |
| 3 그래프 | 격자 배경, 문서 유형 아이콘 노드, 범례, 에피소드·확대 도구. 레나 아르벨과 이웃만 밝다 | 그래프 영역 |
| 4 타임라인 | 에피소드로 묶인 회차 열, 문서 유형 폴더로 묶인 행, 4화 열 선택 | 레나 아르벨 행 |
| 5 그래프 최신화 | 사이드바 항목이 "그래프 최신화 → 그래프 추출 중… → 변경 사항 반영"으로 바뀐 뒤 변경 사항 모달(현재 버전·신규 버전 비교)이 열린다 | 사이드바 항목, 이어서 모달 |

화면 구성이나 이름이 실제 화면에서 바뀌면 이 무대도 같이 고친다.

## 모션

전부 CSS 키프레임과 Web Animations API 다. 새 의존성은 없다(`features/onboarding/onboarding.module.css`).

- 곡선은 `cubic-bezier(0.2, 0.8, 0.2, 1)` 하나. 튕기는 곡선을 쓰지 않는다.
- 단계에 들어올 때 한 번만 재생하고, 멈춘 뒤 대기 애니메이션은 없다.
- 스포트라이트: 창 밖을 어둡게 하고 설명하는 영역에 테두리를 두른다. 단계가 바뀌면 다음 영역으로 560ms 미끄러진다. 자리는 변환의 영향을 받지 않게 offset 으로 잰다(`useSpotlight`).
- 장면별 등장: 새 탭 카드 순차 등장, 속성 표 행 페이드와 새 관계 칩 튀어나옴, 그래프 엣지 그리기·노드 튀어나옴, 타임라인 막대 자라기, 최신화 항목의 상태 전환과 검토 모달 열림.
- `prefers-reduced-motion` 이면 이동·크기 변화를 빼고 120ms 페이드만 남긴다. 스포트라이트는 미끄러지지 않고 바로 옮긴다.
- 키보드: ← → 이동, Enter 다음, Esc 시작 고르기로 건너뛰기. 터치 기기에서는 키 안내를 숨긴다.
- 무대는 1000×640 창을 화면에 맞춰 축소한다. 960px 이하에서는 설명을 위, 무대를 아래로 쌓는다.

## 계정 삭제

| 단계 | 처리 |
| --- | --- |
| 사유(선택) | 이유 여덟 개를 고르고 의견을 적는다. 답하지 않고 "다음" 을 눌러도 된다. 확인 단계의 "이전" 으로 돌아오면 답이 남아 있다 |
| 확인 | 계정 이메일을 똑같이 입력해야 버튼이 켜진다(앞뒤 공백·대소문자 무시). 서버도 같은 규칙으로 다시 대조한다 |
| 요청 | `POST /auth/users/me/deletion` `{confirmation_email, survey?}`. `survey` 는 답한 것이 있을 때만 싣는다. 인증 전환(`withAuthTransition`) 안에서 부른다 |
| 서버 순서 | gateway: 이메일 대조 → content `DELETE /users/me/data` → auth `DELETE /auth/users/me`(세션 폐기 후 계정·Google 연결·동의 기록 삭제) → (설문이 있으면) content `POST /withdrawal-surveys` → 세션 쿠키 삭제 |
| 실패 | `ACCOUNT_CONFIRMATION_MISMATCH` 는 입력란 오류, 그 밖은 "계정과 작업은 그대로" 안내와 다시 시도. 어느 단계에서 실패해도 다시 시도해도 안전하다 |
| 성공 | 쿼리 캐시를 비우고 `/goodbye/`. 같은 Google 계정으로 다시 로그인하면 새 계정·새 동의·새 온보딩으로 시작한다 |

정책 근거: `loresentry-authentication/docs/privacy/TERMS_OF_SERVICE.md` 제7조(유예 없이 즉시 삭제), `PRIVACY_POLICY.md` 보유·파기 표.

### 탈퇴 설문

> 2026-10-12 · content #20, gateway #12, frontend #53 (content #19 위에 쌓임). 머지 순서: content #19 → #20 → gateway #12 → frontend #53

| 코드 | 화면 |
| --- | --- |
| `RARELY_USED` | 자주 쓰지 않아요 |
| `MISSING_FEATURE` | 필요한 기능이 없어요 |
| `HARD_TO_USE` | 쓰기 어렵거나 불편해요 |
| `UNRELIABLE` | 오류가 잦거나 느려요 |
| `SWITCHED_SERVICE` | 다른 도구를 쓰기로 했어요 |
| `PRIVACY` | 원고와 개인정보가 걱정돼요 |
| `PAUSED_WRITING` | 연재를 마쳤거나 쉬고 있어요 |
| `OTHER` | 기타 |

- **어디에 남나.** content 의 `withdrawal_survey`(`reasons TEXT[]`, `comment`, `created_at`). **사용자 열이 없다.** 기존 `feedback` 표는 `user_id` 로 행을 묶고 탈퇴 정리(`DELETE /users/me/data`)가 함께 지우므로, 탈퇴할 때 받은 답을 거기 두면 같은 요청이 지운다.
- **언제 남나.** 계정 삭제가 성공한 **뒤에** gateway 가 보낸다. 중간에 실패한 탈퇴는 답을 남기지 않고, 계정이 이미 없는 재시도(`USER_NOT_FOUND`)는 두 번째 답을 남기지 않는다.
- **최선 노력.** 저장이 실패하거나 응답이 없어도 탈퇴는 성공이다. gateway 는 예외 종류만 로그에 남기고 답 내용은 남기지 않는다.
- **검증.** 정해진 코드만 받고(공백·대소문자 정리, 중복 제거), 의견은 1,000자까지, 둘 다 비면 400. 표의 CHECK 제약도 같은 규칙이다. `POST /withdrawal-surveys` 는 gateway 만 부르고 브라우저 경로는 없다.
- **읽기.** 운영자가 SQL 로 읽는다. 예: `select reason, count(*) from withdrawal_survey, unnest(reasons) reason group by reason order by 2 desc;`

## 예시 프로젝트

`POST /projects/sample` 이 content 에서 "유리 정원의 기록"을 원고 7개·설정 문서 14개·관계 46개로 한 트랜잭션에 만든다. 이름이 겹치면 "(2)" 를 붙인다. mock 은 시드 빌더를 새 id 로 한 벌 더 돌린다(`addSampleProject`).

## 와이어프레임과 다르게 한 점

- **약관 단계(180–183)는 구현하지 않았다.** 약관은 LOREKEEPER-621 의 로그인 화면 모달이 이미 서버 계약(필수 체크 하나, 처리방침은 열람 링크)대로 동작한다. 180 의 다중 체크박스 안은 법무 설계(`TERMS_CONSENT_DESIGN.md`)와 맞지 않는다.
- 온보딩 첫 장면에 "○○ 님, 환영해요." 를 더했다. 약관 화면이 인사를 맡지 않게 되어서다.
- 계정 삭제 대화상자는 설정 모달 위에 겹치지 않고 설정을 닫은 뒤 연다. 취소하면 설정으로 돌아간다.
- 176(입력 완료)·177(삭제 중)·178(실패)은 한 대화상자의 상태다.
- **탈퇴 사유 단계는 와이어프레임에 없다.** 175 앞에 같은 대화상자의 첫 단계로 넣었다. 이유는 2열 체크 목록(600px 이하 한 열), 의견은 선택 입력이다.

## 남은 결정

1. 새 토큰 `font-size-display`(28)·`line-height-display`(1.3) 를 `lorekeeper.lib.pen` 에 추가했다. 동기화 스니펫으로 `pencil-variables.json` 을 갱신하고 `pnpm tokens` 를 돌려야 CSS 의 대체값(28px, 1.3)이 토큰으로 바뀐다.
2. 예시 프로젝트의 S3 이미지는 없다. 이미지 시연이 필요하면 시드에 추가해야 한다.
3. ai-chat·graph-rag 는 아직 사용자 자료를 저장하지 않는다. 그 기능이 생기면 탈퇴 순서에 해당 서비스의 삭제를 더해야 한다.
4. **개인정보 처리방침에 탈퇴 설문을 더할지.** 설문은 계정과 연결하지 않지만 의견란에 이용자가 스스로 개인정보를 적을 수 있다. 법적 원문이라 고치지 않았다. 안:
   - 1. 처리 목적·항목 표: `| 탈퇴 사유 파악과 서비스 개선 | 탈퇴할 때 고른 사유와 직접 적은 의견(선택). 계정·이메일 등 이용자를 식별하는 정보와 연결하지 않음 | 탈퇴 화면의 선택 설문 |`
   - 보유·파기 표: `| 탈퇴 설문 응답 | 계정과 연결하지 않고 ○년 보관 후 파기 |` — **보관 기간을 정해야 한다.** 정하면 그 기간이 지난 행을 지우는 정리 작업도 필요하다.
