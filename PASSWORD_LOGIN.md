# 아이디·비밀번호 로그인

> 작성일: 2026-11-07
> 브랜치: 각 저장소의 `feat/password-login` (authentication PR #20, gateway PR #36, frontend PR)
> 목적: 최종 발표 평가관처럼 Google 계정 없이도 들어올 수 있는 로그인 수단

지금까지 로그인은 Google 하나였다. 평가·시연용 계정을 미리 만들어 나눠 주려면 아이디와 비밀번호로 가입하고
로그인하는 길이 필요하다. 기존 계정 모델·세션·약관 동의·탈퇴를 그대로 쓰도록 가장 작은 모양으로 붙였다.

## 흐름

```text
로그인 화면
  ├─ Google로 계속하기            기존 OAuth 흐름 그대로
  └─ 아이디 · 비밀번호 폼
       ├─ 로그인      POST /auth/password/login   {login_id, password}
       └─ 계정 만들기 POST /auth/password/signup  {login_id, password}  (화면은 비밀번호 확인 칸을 더 받는다)
            → 200 {status: AUTHENTICATED}   세션 쿠키 → GET /auth/users/me → /projects (신규는 /welcome/)
            → 200 {status: TERMS_REQUIRED}  동의 쿠키 → /login?result=terms_required → 약관 동의 모달 → 세션
```

약관 동의는 Google과 똑같이 Auth의 동의 게이트가 판단한다. 처음 가입하면 `TERMS_REQUIRED`가 오고,
동의를 마치면 그 뒤 로그인은 바로 `AUTHENTICATED`다.

## 저장 모양 (Auth)

| 테이블 | 쓰임 |
|---|---|
| `oauth_identities` | `provider = 'password'`, `provider_id = 아이디(소문자)`, `email = NULL`. 기존 조회·세션·탈퇴 경로가 이 행으로 동작한다 |
| `password_credentials` (V12) | `user_id` PK·FK(ON DELETE CASCADE), `password_hash`(bcrypt cost 12), `created_at` |

아이디 규칙은 영문 소문자·숫자·`._-` 3~32자(대문자는 소문자로 정규화), 비밀번호는 공백 없이 8자 이상 72바이트 이하다.
이름은 받지 않고 아이디로 시작해 온보딩에서 정한다. 이메일은 받지 않으므로 계정 설정에서 이메일 칸이 숨고, 이메일로 확인하는 탈퇴는 아이디 계정에서 쓸 수 없다.

## API

| 계층 | 경로 | 응답 |
|---|---|---|
| Auth | `POST /auth/password/signup`, `POST /auth/password/login` | `{status, session_id \| consent_request_id, expires_at}` |
| Gateway | 같은 경로 (CSRF 헤더 필요, 세션 필터 제외) | `{status}`만. 쿠키는 BFF가 설정 |

오류 코드: `INVALID_LOGIN_ID`·`INVALID_PASSWORD`(400), `LOGIN_ID_TAKEN`(409), `INVALID_CREDENTIALS`(401), `LOGIN_UNAVAILABLE`(503).
자세한 계약은 `loresentry-authentication/docs/API.md`, `loresentry-gateway/docs/API.md`·`FRONTEND_AUTH_CONTRACT.md`에 있다.

## 배포 순서

1. authentication PR 병합 → `main` CI → Argo CD 동기화. Flyway V12가 기동 시 적용된다.
2. gateway PR 병합. 프런트보다 먼저 올라가야 폼 제출이 "서버에 연결할 수 없어요"로 떨어지지 않는다.
3. frontend PR 병합 → S3·CloudFront 배포.

확인:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://api.loresentry.com/auth/password/login \
  -H 'Origin: https://loresentry.com' -H 'X-LS-CSRF: 1' -H 'Content-Type: application/json' \
  -d '{"login_id":"nobody","password":"not-a-password"}'
# 401 이면 세 계층이 다 올라온 것. 404 면 gateway, 503 이면 auth 가 아직이다.
```

## 평가관 계정 만들기

가입 API를 두 번 부르고 약관에 동의해 두면 평가관은 로그인만 하면 된다. 아래 스크립트는 쿠키 통을 써서
가입 → 약관 조회 → 동의까지 한 번에 끝낸다. 비밀번호는 이 문서에 적지 않고 따로 전달한다.

```bash
API=https://api.loresentry.com
create() {
  local id="$1" pw="$2" jar
  jar=$(mktemp)
  curl -s -c "$jar" -X POST "$API/auth/password/signup" \
    -H 'Origin: https://loresentry.com' -H 'X-LS-CSRF: 1' -H 'Content-Type: application/json' \
    -d "{\"login_id\":\"$id\",\"password\":\"$pw\"}"; echo
  local version
  version=$(curl -s -b "$jar" "$API/auth/terms?locale=ko" | sed -E 's/.*"terms_version_id":"([^"]+)".*/\1/')
  curl -s -b "$jar" -c "$jar" -o /dev/null -w 'accept %{http_code}\n' -X POST "$API/auth/terms/accept" \
    -H 'Origin: https://loresentry.com' -H 'X-LS-CSRF: 1' -H 'Content-Type: application/json' \
    -d "{\"terms_version_id\":\"$version\"}"
  curl -s -b "$jar" "$API/auth/users/me"; echo
  rm -f "$jar"
}
create judge1 '<비밀번호1>'
create judge2 '<비밀번호2>'
```

| 아이디 | 상태 |
|---|---|
| `judge1` | 배포 뒤 생성 예정 |
| `judge2` | 배포 뒤 생성 예정 |

처음 로그인하면 온보딩(`/welcome/`)이 뜨고, 예시 프로젝트 "별이 없는 세자"를 만들어 볼 수 있다([SAMPLE_PROJECT.md](SAMPLE_PROJECT.md)).
발표 전에 한 번 로그인해 온보딩을 넘기고 예시 프로젝트를 만들어 두면 평가관은 바로 작업공간을 본다.
