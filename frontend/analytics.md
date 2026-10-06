# 이용 통계 — Google Analytics 4

> 작성일: 2026-10-06
> 브랜치: `loresentry-frontend` `feat/google-analytics`
> 속성: 웹 스트림 `loresentry`, `https://loresentry.com`, 측정 ID `G-FV1Y05NZQW`

## 1. 언제 켜지는가

세 조건이 모두 맞을 때만 gtag.js 를 불러온다(`features/analytics/google-analytics.ts` `analyticsEnabled`).

| 조건 | 빠지는 트래픽 |
|---|---|
| `config.json` 의 `gaMeasurementId` 가 `G-` 형식 | 설정을 지우면 재빌드 없이 꺼진다 |
| `dataSource === "api"` (`?data=` 재정의 반영 후) | `?data=mock`, `?mock=` 으로 화면을 보는 탭 |
| 호스트가 `loresentry.com` | `localhost`, `out/` 을 로컬에서 띄운 것. `www` 는 CloudFront 가 apex 로 301 한다 |

측정 ID 를 `config.json` 에 둔 이유는 백엔드 주소와 같다 — 이 파일만 `no-store` 로 따로 올라가므로 바꾸는 데 빌드가 필요 없다(INFRA §17).

## 2. 페이지 조회는 앱이 직접 보낸다

GA 의 자동 page_view 를 끄고(`send_page_view: false`) 라우트가 바뀔 때마다 `GoogleAnalyticsTracker` 가 보낸다.

**자동 수집을 쓰지 않는 이유는 URL 이다.** 정적 export 라 식별자가 쿼리에 있다(`/workspace/?projectId=…&open=file:<fileId>`). 자동 수집은 이 URL 을 그대로 Google 로 보낸다. 앱은 보내기 전에 쿼리를 허용 목록으로 걸러 낸다.

| 쿼리 | 보내는 값 |
|---|---|
| `open` | `:` 앞만. `file:<fileId>` → `file`, `graph` → `graph` |
| `topic` | 그대로(사용 가이드 주제) |
| 그 밖 전부 | 버린다 — `projectId`, `returnTo`, `result`, `auth`, `data`, `mock`, `replay`, 해시 |

- 걸러 낸 주소는 `gtag("set", { page_location })` 으로 둔다. 같은 페이지에서 나는 스크롤·외부 링크 클릭 같은 향상된 측정 이벤트도 걸러 낸 주소를 쓴다.
- **`config` 에는 `page_location` 을 넣지 않는다.** `config` 의 값이 뒤의 `set` 보다 우선해서, 첫 페이지 뒤의 모든 page_view 가 첫 주소로 찍혔다(`loresentry-frontend#33`). 그래서 주소는 `set` 과 page_view 이벤트 자체에 싣는다.
- 걸러 낸 주소가 직전과 같으면 보내지 않는다. 프로젝트만 바꿔 작업공간을 다시 열면 같은 페이지로 본다. React Strict Mode 의 이중 실행도 여기서 걸린다.
- 두 번째 페이지부터 직전 주소를 `page_referrer` 로 싣는다.
- 작업공간 안의 탭 전환은 URL 이 바뀌지 않으므로 page_view 가 아니다. 필요하면 이벤트로 따로 보낸다(5절).

## 3. GA 콘솔 설정

| 위치 | 값 | 이유 |
|---|---|---|
| 스트림 → 향상된 측정 → 페이지 조회 → 고급 → **브라우저 기록 이벤트 기반 페이지 변경** | **끔** | 켜 두면 GA 가 원래 URL 로 page_view 를 한 번 더 보낸다 — 중복이고 `projectId` 가 샌다 |
| 태그 설정 → 원치 않는 추천 | `accounts.google.com`, `api.loresentry.com` | Google 로그인 왕복이 매번 새 세션·새 유입으로 잡힌다 |
| 태그 설정 → 내부 트래픽 정의 + 데이터 필터 | 팀 IP | 개발·확인 트래픽 제외 |
| 데이터 보존 | 14개월 | 처리방침 §6 과 맞춘다 |
| Google 신호 데이터, 광고 개인 최적화 | 끔 | 처리방침 §6 과 맞춘다 |

## 4. 개인정보 처리방침

`public/policies/privacy.html` 을 `초안 0.3` 으로 올렸다.

- §5 업체 표에 Google 애널리틱스(Google LLC)를 더했다.
- §6 의 "광고·행동 분석 도구는 도입하지 않았습니다" 를 바꿨다. 분석 쿠키 `_ga`·`_ga_<측정 ID>`, 수집 항목, 식별자 제거, 보존 기간, 거부 방법(쿠키 차단, GA 차단 부가 기능)을 적었다.

공개본 확정 전에 국외 이전 고지 범위를 법무 검토와 다시 대조한다. 동의 배너는 두지 않았다 — EU 이용자를 받게 되면 Consent Mode v2 와 배너가 필요하다.

## 5. 남은 것

- 커스텀 이벤트: `login`, `sign_up`(약관 동의 완료), `tutorial_complete`(온보딩 완료, 고른 시작), `create_project`, 작업공간 뷰 열기. `sign_up`·`create_project` 는 주요 이벤트로 표시한다.
- CloudFront 에 CSP 를 두게 되면 `script-src https://www.googletagmanager.com`, `connect-src https://*.google-analytics.com https://*.analytics.google.com https://www.googletagmanager.com` 을 허용한다.

## 6. 검증

- 단위 테스트(`google-analytics.test.tsx`): 켜지는 조건, 쿼리 걸러 내기, gtag.js 한 번만 불러오기, 페이지별 page_view 한 번과 referrer, 측정 ID 형식.
- 2026-10-06 운영 확인: 헤드리스 브라우저로 `/workspace/?projectId=…&open=file:…&mock=x` 에 접속해 `/g/collect` 요청을 잡았다. page_view 두 건(`/workspace/?open=file` → `/login/`, referrer 이어짐)과 scroll 한 건이 `G-FV1Y05NZQW` 로 나갔고, 식별자·테스트 쿼리는 없었다. 중복 page_view 도 없었다.
- 배포 후: Tag Assistant 로 `loresentry.com` 연결 → GA DebugView 와 실시간 보고서에서 page_view 의 `page_location` 에 `projectId` 가 없는지, Google 로그인 뒤 세션 소스가 `accounts.google.com` 으로 바뀌지 않는지 확인한다.
