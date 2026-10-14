# AI 챗 기능 요구사항 — 백엔드

> 갱신일: 2026-10-14
> 상태: **프론트엔드는 mock으로 완성, 서버는 미구현.** 화면과 서비스 계약은 `loresentry-frontend/src/features/chat`, `src/services/ports.ts`(`ChatService`), `src/services/mock/chat.ts`가 기준이다.
> 독자: ai-chat·gateway·content 서버를 구현하는 사람.
> 관련: [CORE_FEATURE_REQUIREMENTS.md §8.1](CORE_FEATURE_REQUIREMENTS.md), [TABLE_AND_LOGIC.md §6](TABLE_AND_LOGIC.md), [frontend/ai-chat.md](frontend/ai-chat.md)

## 0. 요약

작가는 작업공간 오른쪽 아래 버튼으로 AI 챗을 연다. 질문하면 AI가 **그 프로젝트의 원고·설정 문서·관계·메모 전체를 그 자리에서 읽고**, 근거가 된 문서를 표시한 답을 스트리밍으로 돌려준다. 원고나 설정 문서의 문장을 드래그해서 그 부분을 바로 물을 수도 있다. AI는 문서를 고치지 않는다.

이미 정한 것:

| 항목 | 결정 |
|---|---|
| 검색 방식 | 벡터 DB를 쓰지 않는다. 매 턴 프로젝트 전체를 컨텍스트로 넘긴다(성능 상한 측정 단계). 비용 최적화는 다음 단계 |
| 모델 | OpenAI `gpt-6-luna`(컨텍스트 1.05M 토큰). 모델 이름은 설정값 |
| 오케스트레이션 | 단일 호출. LangGraph 같은 에이전트 프레임워크는 쓰지 않는다 |
| 읽는 자료 | 원고, 설정 문서(속성·본문), 관계와 관계 설명, 프로젝트 메모·파일 메모, 사용자 프로필, 지금 보는 문서, 드래그한 인용 |
| 메모 | 넣되 **원고·설정 근거와 구분**한다. 답에서도 메모 근거는 따로 표시한다 |
| 세션 | 프로젝트 단위로 여러 개. 문서에 묶이지 않는다. 제목은 첫 질문 앞부분 |
| 답 동작 | 스트리밍, 멈추기, 답 복사(프론트), 마지막 질문 고쳐서 다시 생성 |
| 분량 초과 | 규칙 기반 글자 수 판정. 넘으면 챗을 열지 않고 이유를 안내 |
| 드래그 질의 | 챗 창에 열린 대화가 있으면 그 대화에, 없으면 새 대화로 |
| 사용량 제한 | 두지 않는다(초기) |

## 1. 용어

| 용어 | 뜻 |
|---|---|
| 세션(대화) | 한 프로젝트 안의 대화 하나. `chat_sessions` 한 행 |
| 턴 | 질문 하나와 그에 대한 답 하나 |
| 질문 | 사용자 메시지. 글, 의도(intent), 인용(quote)을 가진다 |
| 답 | AI 메시지. 제한된 마크다운 본문과 근거 목록(citations)을 가진다 |
| 근거 | 답이 기댄 자료. 문서 근거와 메모 근거 두 종류 |
| 인용 | 작가가 문서 본문(원고·설정)에서 드래그해 붙인 구간 |
| 의도 | 질문의 종류. `ask`(직접 질문), `explain`, `related`, `flow`(드래그 빠른 질문) |
| 컨텍스트 스냅샷 | 한 턴에서 모델에 넘기는 프로젝트 자료 전체 |

## 2. 사용자 흐름

```text
작업공간 → 오른쪽 아래 AI 챗 버튼
  ├─ 분량 초과        → 버튼 비활성 + "작품이 커서 AI 챗을 열 수 없어요" 안내
  └─ 열림 → 홈
        ├─ 인사, 새 대화 입력칸, 대화 목록(검색), 추천 질문
        └─ 대화
              질문 → [모으는 중 → 읽는 중] → 답 스트리밍(근거 칩) → 저장
              ├─ 멈추기 → 미완성 답 버림, "다시 묻기"
              ├─ 마지막 질문 고치기 → 그 질문과 답을 지우고 새로 생성
              └─ 이름 바꾸기 / 삭제

문서 본문에서 문장 드래그 → [AI에게 묻기 | 설명해 줘 | 관련 설정 | 앞뒤 흐름]
  ├─ 챗 창에 대화가 열려 있음 → 그 대화에 붙임
  └─ 아니면                   → 새 대화를 만들어 붙임
```

## 3. 기능 요구사항

### 3.1 세션

- 세션은 프로젝트에 속한다. 한 사용자가 한 프로젝트에 여러 세션을 둔다. 다른 사용자·다른 프로젝트의 세션은 보이지 않는다.
- 목록은 `updated_at` 내림차순. `updated_at`은 질문을 받거나 답을 저장할 때 갱신한다.
- 새 세션의 제목은 언어별 기본값("새 대화" / "New conversation")이다. **첫 질문을 받으면** 그 글(글이 비었으면 인용 글)의 공백을 하나로 접고 24자를 넘으면 23자 + "…"로 잘라 제목으로 쓴다. 사용자가 이름을 바꾼 뒤에는 건드리지 않는다.
- 목록 응답은 각 세션의 **미리보기**를 함께 준다. 마지막 답의 본문에서 근거 표시·굵게·목록 기호를 지우고 공백을 접은 앞 80자(넘으면 "…")다. 답이 없으면 빈 문자열이다.
- 이름은 앞뒤 공백을 지운 1~200자. 비면 400 `CHAT_TITLE_REQUIRED`.
- 삭제는 즉시 영구 삭제이고 그 세션의 메시지도 함께 지운다. 그 세션에서 생성 중인 답이 있으면 취소한다.

### 3.2 질문과 답

요청 하나가 턴 하나다.

| 입력 | 뜻 |
|---|---|
| `content` | 질문 글. 0~4,000자. 인용이 있으면 비어도 된다. 둘 다 비면 400 |
| `intent` | `ask` · `explain` · `related` · `flow`. 드래그 빠른 질문은 프론트가 기본 문구("이 부분을 설명해 줘" 등)를 `content`에 함께 보낸다 |
| `quote` | 드래그 인용. `{file_id, title, doc_type, text}`. `text`는 최대 400자(프론트가 자른다). `file_id`는 같은 프로젝트의 활성 문서여야 한다 |
| `focus_file_id` | 작가가 지금 보는 문서. 작가가 끄면 null. 답의 초점을 잡는 힌트일 뿐 자료 범위를 줄이지 않는다 |
| `replaces` | 마지막 질문을 고쳐 다시 물을 때 그 질문의 id. 아니면 null |
| `client_message_id` | 프론트가 만드는 UUID. 같은 값의 재요청은 새로 생성하지 않는다(§3.6) |

처리 순서:

1. 권한과 입력을 검사한다(§3.9).
2. 분량을 다시 판정한다. 넘으면 422 `CHAT_CONTEXT_TOO_LARGE`(프론트 버튼 상태와 서버 사이의 경합 대비).
3. `replaces`가 있으면 그것이 **이 세션의 마지막 사용자 질문인지** 확인하고(아니면 409 `CHAT_NOT_LAST_QUESTION`), 그 질문과 뒤의 메시지를 지운다.
4. 질문을 저장한다. 첫 질문이면 제목을 정한다.
5. 컨텍스트 스냅샷을 만든다(§3.3). 이 시점의 문서 상태로 답한다.
6. 모델을 호출하고 스트리밍한다(§3.5).
7. 완성된 답만 저장하고 `updated_at`·미리보기를 갱신한다.

- **멈추기:** 사용자가 멈추면 생성을 중단하고 답을 저장하지 않는다. 질문은 남는다. 프론트는 "응답을 멈췄어요. 끝나지 않은 답은 남기지 않아요."와 "다시 묻기"를 보인다. 다시 묻기는 그 질문을 `replaces`로 지정해 같은 입력으로 보낸다.
- **실패:** 모델·저장 실패도 답을 남기지 않는다. 생성은 끝났는데 저장만 실패했다면 **같은 답을 저장 재시도**한다(요구사항 §3.9). 다시 생성하지 않는다.
- **동시성:** 한 세션에서는 진행 중인 생성이 하나뿐이다. 진행 중에 같은 세션으로 새 질문이 오면 409 `CHAT_ANSWER_IN_PROGRESS`. 다른 세션끼리는 동시에 생성할 수 있다. 프론트는 창을 닫아도, 다른 대화로 옮겨도 각 생성을 이어서 보여 준다.
- **언어:** 질문의 언어로 답한다. 판단이 어려우면 사용자 `locale`.

### 3.3 컨텍스트 스냅샷

매 턴 프로젝트 전체를 새로 읽어 모델에 넘긴다. 채팅 중에도 문서가 바뀌므로 캐시된 스냅샷을 재사용하지 않는다.

| 자료 | 내용 | 비고 |
|---|---|---|
| 프로젝트 | 이름, 설명 | |
| 사용자 프로필 | 표시 이름(작가명), `locale` | gateway가 auth `/auth/users/me`에서 붙여 넘긴다 |
| 원고 | 에피소드·회차 순서대로 제목과 본문(`body_text`), 회차 순번 | 순번은 `episode rank → document rank`로 계산. 에피소드 밖 원고는 맨 뒤 |
| 설정 문서 | 분류, 제목, 속성(설명·별칭), 본문 | |
| 관계 | 문서 쌍, 관계 키, 관계 설명 | content의 명시적 관계. 추론 관계 없음 |
| 메모 | 프로젝트 메모, 파일 메모(어느 문서의 메모인지) | **근거와 구분**: 프롬프트에서 "작가의 작업 메모, 원고의 사실이 아님"으로 표시 |
| 지금 보는 문서 | `focus_file_id`의 제목 | 초점 힌트 |
| 인용 | `quote` | 질문의 대상 |
| 대화 기록 | 이 세션의 앞선 질문과 답(본문만) | 근거 목록·도구 결과는 넣지 않는다 |

- 휴지통의 문서·메모, 다른 프로젝트 자료는 넣지 않는다.
- **순서를 고정한다.** 같은 자료면 바이트까지 같은 프롬프트가 나오게 직렬화해야 OpenAI 프롬프트 캐시가 맞는다. 권장 순서: 시스템 지시 → 프로필 → 오래된 회차 → 설정·관계 → 메모 → 최근 회차 → 대화 기록 → 질문. 작가는 주로 최근 회차를 고치므로 수정이 캐시를 뒤쪽에서만 깨뜨린다.
- 편집기의 저장 대기 중인 변경은 질문을 보내기 전에 프론트가 먼저 저장한다(프론트 후속 작업, §6).
- content가 스냅샷을 한 번에 돌려주는 내부 API를 둔다(§4.3). 문서마다 따로 부르지 않는다.

### 3.4 분량 판정

- 규칙 기반이다. **원고·설정 문서의 제목·본문·텍스트 속성 + 메모의 제목·본문 글자 수 합**이 한도를 넘으면 `too-large`다.
- 한도는 서버 설정값이다. 초기값 **1,400,000자**(한국어 1자 ≈ 0.62토큰 실측 → 약 87만 토큰, 출력 128K를 뺀 입력 한도 약 92만 토큰 안). 대략 웹소설 240화 분량이다.
- 판정 결과에는 크기와 한도를 함께 준다. 프론트는 "지금 작품은 약 {size}자로, 한 번에 읽을 수 있는 {limit}자를 넘었어요."로 보인다.
- 프론트는 작업공간에 들어올 때와 문서가 바뀐 뒤(파일 트리 갱신 시) 다시 묻는다. 서버는 `send`에서도 다시 판정한다(§3.2).

### 3.5 답 형식과 스트리밍

**본문**은 제한된 마크다운이다.

- 줄바꿈 하나가 문단 하나. `- `로 시작하는 줄은 목록 항목. `**굵게**`만 허용한다. 제목·표·링크·코드 블록은 쓰지 않는다.
- 근거 표시는 `[[n]]`(n은 1부터 시작하는 `citations` 순번)이다. 근거를 뒷받침하는 문장 끝에 둔다.

**근거(citations)**:

```json
[
  { "kind": "document", "file_id": "…", "title": "12화 · 균열의 밤", "doc_type": "manuscript",
    "quote": "어둠 너머에서 익숙한 목소리가 이름을 불렀다." },
  { "kind": "memo", "memo_id": "…", "title": "균열의 방향",
    "quote": "균열은 문이 아니라 기억의 방향이다." }
]
```

- `quote`는 원문 그대로의 문장이거나 관계 설명이다. **서버가 검증한다.** 문서 근거는 그 문서 `body_text`·속성에 실제로 있는지, 관계 설명은 관계 데이터와 같은지 확인한다. 맞지 않으면 `quote`를 null로 바꾼다. 존재하지 않거나 휴지통에 있는 문서를 가리키는 근거는 지우고 본문의 표시도 지운다.
- `title`·`doc_type`은 답을 만든 시점의 값이다. 프론트는 칩을 눌러 `file_id` 문서를 연다(메모는 메모 화면).
- 모델에는 문서·메모를 id가 붙은 블록으로 넘기고, 근거를 구조화된 표시(예: `<cite doc="id" quote="…"/>`)로 내게 한 뒤 서버가 `[[n]]`과 `citations`로 바꾼다. 프롬프트 형식은 구현 몫이다.

**스트리밍**: `POST …/messages`의 응답을 SSE로 보낸다.

| 이벤트 | 데이터 | 시점 |
|---|---|---|
| `phase` | `{"phase":"gathering"}` | 스냅샷을 모으기 시작 |
| `phase` | `{"phase":"reading"}` | 모델 호출 시작, 첫 토큰 전 |
| `phase` | `{"phase":"writing"}` | 첫 본문 토큰 |
| `citation` | `{"index":1,"citation":{…}}` | 그 근거의 `[[n]]` 표시를 보내기 **전에** |
| `delta` | `{"text":"…"}` | 본문 조각. `[[n]]` 표시는 쪼개지 않고 한 조각에 담는다 |
| `done` | `{"question":Message,"answer":Message}` | 답 저장 후 |
| `error` | `{"code":"…","message":"…"}` | 실패. 답은 저장되지 않음 |

- 15초마다 heartbeat(`: ping`)를 보낸다. ALB idle timeout 60초와 gateway read-timeout에 걸리지 않게 하려는 것이다. 첫 토큰까지 수십 초 걸릴 수 있다.
- 프론트는 `phase`로 대기 문구("원고와 설정을 모으는 중" → "작품 전체를 읽는 중")와 경과 초를 보인다.
- 스트림이 끊겨도 서버는 생성을 끝까지 하고 저장한다. 멈춤은 명시적 취소 요청으로만 한다(§4.1).

### 3.6 끊김과 중복 방지

- `client_message_id`가 같은 재요청은 새로 생성하지 않는다. 생성이 끝났으면 `done`과 같은 결과를, 진행 중이면 409 `CHAT_ANSWER_IN_PROGRESS`를 준다.
- 메시지 목록은 진행 중인 턴을 알 수 있어야 한다. 세션에 `generating: true`(또는 진행 중 질문의 상태)를 실어, 새로 고친 화면이 "답을 쓰는 중"을 보이고 끝날 때까지 다시 물을 수 있게 한다.

### 3.7 대화 검색

- 범위는 그 프로젝트의 세션이다. 세션 제목, 질문 글, 인용 글, 답 본문, 근거 제목에서 찾는다. 대소문자를 무시한 부분 일치(형태소 분석 없음).
- 정렬: 제목 일치 → 최근 갱신 순.
- 결과마다 본문에서 처음 맞은 곳의 앞 16자·일치·뒤 40자 조각(`snippet`)을 준다. 제목만 맞으면 `snippet`은 null이고 프론트는 미리보기를 보인다.
- 검색어는 1~100자.

### 3.8 드래그 질의

- 프론트가 라우팅을 정한다. 챗 창에 대화가 열려 있으면 그 세션에, 아니면 새 세션을 만들어 보낸다. 서버 쪽에서는 일반 `send`와 같다.
- "AI에게 묻기"는 인용만 입력칸에 붙이고 작가가 질문을 적어 보낸다(`intent: ask`). "설명해 줘 / 관련 설정 / 앞뒤 흐름"은 바로 보낸다(`explain` / `related` / `flow`).
- 의도별 지시는 서버 프롬프트 템플릿이다.
  - `explain`: 인용 구간의 장면·인물·행동이 무엇이고 왜 중요한지.
  - `related`: 인용 구간과 이어진 설정 문서(명시적 관계 우선)와 원고 안의 같은 대상.
  - `flow`: 인용 구간의 앞뒤 회차·장면에서 이어지는 흐름.
- 열린 세션이 이미 생성 중이면 프론트는 보내지 않고 인용과 질문을 입력칸에 채워 둔다.

### 3.9 권한과 데이터 범위

- gateway는 세션 쿠키를 검증하고 `X-User-Id`를 붙인다(기존 규약). ai-chat은 세션의 `user_id`가 요청자와 같은지 확인하고, 다르면 404 `CHAT_SESSION_NOT_FOUND`로 존재를 숨긴다.
- 프로젝트 접근은 content가 판정한다. 스냅샷 요청이 404면 ai-chat도 404 `PROJECT_NOT_FOUND`.
- ai-chat은 auth를 직접 부르지 않는다. 프로필은 gateway가 조합해 넘긴다.

### 3.10 삭제와 보존

| 사건 | 처리 |
|---|---|
| 세션 삭제 | 즉시 영구 삭제, 진행 중 생성 취소 |
| 프로젝트 휴지통 이동 | 세션을 보이지 않게 한다(프로젝트 접근이 막히므로 자연히 404). 복원하면 다시 보인다 |
| 프로젝트 영구 삭제 | 그 프로젝트의 세션·메시지 삭제. content의 `ProjectDeleted` 이벤트를 구독하거나 gateway가 순서대로 호출 |
| 계정 삭제 | 그 사용자의 모든 세션·메시지 삭제. gateway의 탈퇴 순서에 ai-chat `DELETE /users/me/data`를 더한다 |
| 문서 삭제 | 과거 답의 근거는 남는다. 프론트는 열 수 없는 근거를 비활성 칩으로 보인다 |

### 3.11 비기능

- **지표**: 턴마다 입력·캐시·출력 토큰, 첫 토큰까지 시간, 전체 시간, 스냅샷 글자 수, 모델 이름을 남긴다. 비용 대비 성능을 판단할 다음 단계의 근거다. 원문은 남기지 않는다.
- **로그**: 원고·질문 원문을 로그에 남기지 않는다(개인정보 처리방침).
- **타임아웃**: 모델 호출 120초. gateway의 ai-chat 클라이언트는 일반 10초 클라이언트와 분리하고 연결 풀을 늘린다(현재 라우트당 5개).
- **사용량 제한**: 없음(초기). 지표를 보고 다음 단계에 정한다.

## 4. API 계약

### 4.1 gateway(BFF) 제공 — 브라우저용

JSON 이름은 snake_case. 쓰기 요청은 기존 규약대로 `X-LS-CSRF: 1`.

| 메서드·경로 | 요청 | 성공 |
|---|---|---|
| GET /projects/{id}/chat/availability | — | 200 Availability |
| GET /projects/{id}/chat/sessions | — | 200 `{sessions: Session[]}` |
| POST /projects/{id}/chat/sessions | — | 201 Session |
| GET /projects/{id}/chat/search | `q` 쿼리 | 200 `{hits: SearchHit[]}` |
| PATCH /chat/sessions/{sid} | title | 200 Session |
| DELETE /chat/sessions/{sid} | — | 204 |
| GET /chat/sessions/{sid}/messages | — | 200 `{messages: Message[], generating: boolean}` |
| POST /chat/sessions/{sid}/messages | content, intent, quote?, focus_file_id?, replaces?, client_message_id | 200 `text/event-stream`(§3.5) |
| POST /chat/sessions/{sid}/cancel | — | 204. 진행 중 생성이 없으면 204 그대로 |

```json
// Availability
{ "status": "available", "size": 128734, "limit": 1400000 }

// Session
{ "id": "…", "project_id": "…", "title": "북쪽 문 장면 확인",
  "updated_at": "2026-10-14T03:00:00.000Z", "preview": "본문이 있는 가장 최근 회차는 12화예요. …" }

// Message
{ "id": "…", "session_id": "…", "role": "user" | "assistant",
  "content": "…", "intent": "ask" | "explain" | "related" | "flow" | null,
  "quote": { "file_id": "…", "title": "…", "doc_type": "manuscript", "text": "…" } | null,
  "citations": [ … ], "created_at": "…" }

// SearchHit
{ "session": Session, "snippet": { "before": "…", "match": "항해", "after": "…" } | null }
```

오류 코드:

| 코드 | 상태 | 뜻 |
|---|---|---|
| `CHAT_SESSION_NOT_FOUND` | 404 | 없거나 다른 사용자의 세션 |
| `PROJECT_NOT_FOUND` | 404 | 없거나 접근할 수 없는 프로젝트 |
| `CHAT_TITLE_REQUIRED` | 400 | 빈 이름 |
| `CHAT_QUESTION_REQUIRED` | 400 | 글도 인용도 없음 |
| `CHAT_NOT_LAST_QUESTION` | 409 | `replaces`가 마지막 질문이 아님 |
| `CHAT_ANSWER_IN_PROGRESS` | 409 | 그 세션에서 이미 생성 중 |
| `CHAT_CONTEXT_TOO_LARGE` | 422 | 분량 초과 |
| `CHAT_MODEL_UNAVAILABLE` | 503 | 모델 호출 실패(재시도 가능) |

### 4.2 gateway → ai-chat

- 위 경로를 같은 이름으로 중계하되, `X-User-Id`와 함께 프로필을 붙인다: `X-User-Display-Name`(퍼센트 인코딩), `X-User-Locale`. 또는 내부 요청 본문의 `profile` 필드. 둘 중 하나로 정한다.
- SSE는 버퍼링 없이 그대로 흘린다(`StreamingResponseBody`). 일반 RestClient의 10초 read-timeout을 쓰지 않는다.

### 4.3 ai-chat → content (내부)

`GET /internal/projects/{id}/chat-snapshot` (gateway에 노출하지 않음, `X-User-Id` 필요)

```json
{
  "project": { "id": "…", "name": "…", "description": "…" },
  "manuscripts": [ { "id": "…", "title": "12화 · 균열의 밤", "ordinal": 12, "episode": "Episode 4. 새 원고",
                     "revision_no": 23, "body_text": "…" } ],
  "settings": [ { "id": "…", "folder_code": "CHARACTER", "title": "레나 아르벨",
                  "properties": [ { "key": "description", "value": "…" }, { "key": "alias", "value": "…" } ],
                  "revision_no": 7, "body_text": "…" } ],
  "relations": [ { "a": "…", "b": "…", "relation_key": "related_place", "description": "처음 각성한 곳" } ],
  "memos": [ { "id": "…", "scope": "PROJECT" | "FILE", "file_id": "…" | null, "title": "…", "body": "…" } ],
  "size": 128734
}
```

- 한 트랜잭션에서 읽어 시점을 맞춘다. 휴지통 자료는 뺀다. `size`는 §3.4의 규칙으로 content가 계산한다(분량 판정도 이 값을 쓴다).
- 분량 판정만 필요할 때를 위해 `GET /internal/projects/{id}/chat-size`도 둔다(본문을 읽지 않는 합계 쿼리).

## 5. 데이터 모델 (ai_chat DB)

[TABLE_AND_LOGIC.md §6](TABLE_AND_LOGIC.md)의 두 테이블을 바꾼다.

```text
chat_sessions
  + preview            TEXT      마지막 답 미리보기(80자). 답 저장 때 갱신
  + title_set_by_user  BOOLEAN   첫 질문 제목 규칙을 건너뛰기 위해

chat_messages
  + intent             VARCHAR   ask / explain / related / flow (질문만)
  + quote              JSONB     드래그 인용 (질문만)
  + citations          JSONB     근거 목록 (답만)
  + client_message_id  UUID      UNIQUE (session_id, client_message_id)
  status               COMPLETE 만 저장한다. 진행 중 상태는 chat_turns 로 분리

chat_turns (신규)
  id, session_id, question_id, status(GENERATING / COMPLETE / CANCELLED / FAILED),
  model, input_tokens, cached_tokens, output_tokens, first_token_ms, total_ms,
  context_chars, created_at, finished_at
```

## 6. 프론트엔드 대응과 남은 프론트 작업

| 서버 | 프론트 |
|---|---|
| API 전체 | `ChatService`(`src/services/ports.ts`). 지금은 `services/api/unavailable.ts`가 거절하고 화면은 "AI 챗은 아직 준비 중이에요"를 보인다. 서버가 생기면 `services/api/chat.ts`를 더하고 그 한 줄을 지운다 |
| SSE | `ApiClient`에 fetch 스트림 읽기를 더해야 한다(지금은 JSON만) |
| 출시 표시 | `src/features/chat/launch.tsx`의 `CHAT_LAUNCHED`를 true로 바꾸면 랜딩·온보딩·가이드·둘러보기의 "곧 열려요"가 사라진다 |

서버 연결 때 함께 할 프론트 작업:

- 질문을 보내기 전에 편집기의 저장 대기 변경을 먼저 저장한다(§3.3).
- `generating`이 true인 세션을 새로 고친 뒤에도 "답을 쓰는 중"으로 보이고 완료를 기다린다(§3.6).
- 멈추기 버튼이 `POST …/cancel`을 부른다.

## 7. 확인이 필요한 것

| # | 질문 | 지금 가정 |
|---|---|---|
| 1 | 분량 한도 1,400,000자를 그대로 쓸지, 실측 지연(첫 토큰 시간)을 보고 낮출지 | 1,400,000자로 시작, 지표 보고 조정 |
| 2 | 대화 기록을 몇 턴까지 넣을지 | 그 세션 전체(한도 안). 넘치면 오래된 턴부터 뺀다 |
| 3 | 프로필을 헤더로 넘길지 본문으로 넘길지 | gateway 구현 때 정한다 |
| 4 | 프로젝트 영구 삭제 시 ai-chat 정리를 이벤트로 할지 gateway 호출로 할지 | 계정 탈퇴와 같은 gateway 순서 호출 |
| 5 | 질문 글 최대 길이 | 4,000자 |
