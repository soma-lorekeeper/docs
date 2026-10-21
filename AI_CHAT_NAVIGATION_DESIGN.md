# AI 챗 탐색 모드 설계 — 한 번에 읽을 수 없는 큰 작품

> 갱신일: 2026-10-21
> 상태: **설계.** 구현 전. 지금 운영 중인 방식(매 턴 프로젝트 전체를 넣는 단일 호출)은 이 문서에서 `FULL` 모드라 부르고 그대로 둔다.
> 독자: ai-chat·content·gateway·frontend를 구현하는 사람.
> 관련: [AI_CHAT_REQUIREMENTS.md](AI_CHAT_REQUIREMENTS.md)(FULL 모드 계약), [TABLE_AND_LOGIC.md §6](TABLE_AND_LOGIC.md), [GRAPH_INBOX_PATTERN.md](GRAPH_INBOX_PATTERN.md)

## 0. 요약

작품이 커서 전부를 한 번에 넣을 수 없으면, 모델에게 **지도**(모든 문서의 노드·관계·요약)를 먼저 주고, 모델이 **필요한 순서대로 문서를 요청**하게 한다. 서버는 그 순서를 지키며 비용 상한 안에서 문서를 담아 준다. 모델이 "충분하다"고 하면 별도 판정으로 충분도를 확인하고 답한다. 끝까지 충분하지 않으면 그 사실을 답에 밝힌다.

| 항목 | 결정 |
|---|---|
| 모드 | 작은 작품은 `FULL`(지금 방식). 한도를 넘으면 `NAVIGATE`(이 문서). 세션의 첫 질문에서 정하고, `FULL`이던 세션이 커지면 `NAVIGATE`로 바꾼다. 반대로는 바꾸지 않는다 |
| 사전 컨텍스트 | 지도: 문서·메모 노드(제목, 종류, 순번, 글자 수, revision, 작가 속성, 요약과 요약 revision), 관계 간선 |
| 문서 순서 | **답변 LLM이 정한다.** 매 단계 처음부터 다시 정렬한다. 서버는 그 순서대로 들어가는 만큼 담는다 |
| 비용 | 질문당 상한과 단계당 상한. 단위는 **새로 읽어 오는 글자 수**(유니코드 코드 포인트, 분량 판정과 같은 규칙) |
| 종료 | ① 모델이 `ready_to_answer`를 부르고, 충분도 판정과 모델 확신도가 모두 기준 이상일 때 ② 최대 단계 도달 ③ 예산 소진 |
| 충분도 | 별도 판정 호출. 정의는 Sufficient Context(ICLR 2025)를 따른다. 필요한 사실마다 근거 문서와 인용을 달게 하고 서버가 검증한다 |
| 확신도 | 숫자 하나를 직접 묻지 않는다. 가장 유력한 답 두 개와 각각의 확률을 받는다 |
| 답의 단서 | 종료 시 충분도와 확신도로 `SUPPORTED`·`AMBIGUOUS`·`PARTIAL`·`NOT_FOUND`를 정하고, 그에 맞는 지시를 마지막 호출에 넣는다 |
| 세션 구성 | 대화 기록을 **덧붙이기만 한다**(읽은 문서도 기록에 남는다). 프롬프트 캐시가 매 단계·매 턴 맞는다. 너무 길어지거나 캐시가 식으면 다시 만든다(압축) |
| 문서 변경 | 턴 시작마다 지도의 revision을 비교해 `<changes>`로 알린다. 옛 판으로는 근거를 인정하지 않는다 |
| 관계 출처 | content의 `document_relations`. Neptune을 쓰지 않는다(revision이 늦게 따라오는 사본이다) |

## 1. 전제

- 전체 컨텍스트는 너무 커서 한 번에 가져올 수 없다.
- 문서 하나는 반드시 가져올 수 있다. 단계 상한보다 큰 문서는 그 문서 하나만 담는 예외를 둔다(§7.3).
- 문서마다 비용(글자 수)을 미리 안다.
- 답의 품질은 **어떤 순서로 문서를 읽느냐**에 가장 크게 달려 있다. 순서를 정하는 일은 질문과 읽은 내용을 모두 아는 답변 LLM이 맡는다.

## 2. 용어

| 용어 | 뜻 |
|---|---|
| 지도 | 프로젝트의 모든 문서·메모를 노드로, 관계를 간선으로 나열한 사전 컨텍스트. 본문은 없다 |
| 노드 | 지도의 문서 하나 또는 메모 하나. 세션 별칭(`D12`, `M3`)으로 부른다 |
| 요약 | 원고 회차를 모델이 요약한 글. 무엇을 읽을지 고르는 데만 쓴다. **근거가 아니다** |
| 보유 문서 | 이 세션에서 읽어 온 문서와 그 revision. 기록(transcript)에 들어 있다 |
| 단계 | 탐색 루프에서 모델 호출 한 번 |
| 턴 | 질문 하나와 답 하나. 여러 단계로 이루어진다 |
| 충분도 S | 보유 문서만으로 답을 세울 수 있는지. 판정 호출이 정한다 |
| 확신도 C | 모델이 자기 답을 얼마나 믿는지. `ready_to_answer`의 두 답과 확률로 정한다 |
| 근거 수준 | 답에 붙는 단서의 종류. `SUPPORTED`·`AMBIGUOUS`·`PARTIAL`·`NOT_FOUND` |
| 압축 | 기록을 버리고 지도·최근 보유 문서·대화 글만으로 프롬프트를 다시 만드는 일 |

## 3. 모드 선택

| 조건 | 모드 |
|---|---|
| 분량(`chat-size`) ≤ `CHAT_FULL_LIMIT` | `FULL` |
| 분량 > `CHAT_FULL_LIMIT`, 지도 크기 ≤ `CHAT_MAP_LIMIT` | `NAVIGATE` |
| 지도 크기 > `CHAT_MAP_LIMIT` | 챗을 열지 않음(422 `CHAT_CONTEXT_TOO_LARGE`) |

- 모드는 세션의 첫 질문에서 정해 `chat_sessions.mode`에 남긴다. 턴마다 바꾸면 보유 문서와 캐시가 매번 깨진다.
- `FULL` 세션에서 분량이 한도를 넘으면 그 턴부터 `NAVIGATE`로 바꾸고 압축한다(§5.5). `NAVIGATE`에서 `FULL`로는 돌아가지 않는다.
- `CHAT_FULL_LIMIT`의 초깃값은 지금 한도인 1,400,000자다. 그러면 지금 챗을 쓰는 작품은 하나도 바뀌지 않고, **지금 막혀 있는 작품만** `NAVIGATE`를 쓴다. 평가(§14) 결과를 보고 비용을 위해 낮춘다.
- [AI_CHAT_REQUIREMENTS.md §3.4](AI_CHAT_REQUIREMENTS.md)의 "분량 초과면 챗을 열지 않는다"는 "지도가 한도를 넘으면 열지 않는다"로 바뀐다. 프론트의 안내 문구도 함께 바뀐다.

## 4. 페르소나

두 모드가 같은 조수다. 자료가 들어오는 방식만 다르다.

| 항목 | 내용 |
|---|---|
| 정체 | Lore Sentry 안의 이야기 조수. 작가가 자기 작품에 대해 묻는 것에 답한다 |
| 아는 것 | 이 프로젝트의 원고·설정·관계·메모뿐. 일반 지식으로 작품의 빈칸을 채우지 않는다 |
| 하는 것 | 원고가 보여 주는 것과 설정·메모가 말하는 것을 구분해 답한다. 모순을 짚는다. 근거를 단다 |
| 하지 않는 것 | 문서를 고치지 않는다. 고쳐 쓴 문장은 제안만 하고 적용은 작가 몫이라고 말한다. 작품을 평가하거나 줄거리를 대신 짓지 않는다 |
| 정직 | 못 찾았으면 못 찾았다고 말한다. 무엇을 읽었고 무엇이 빠졌는지 밝힌다 |
| 말투 | 질문의 언어로, 짧고 구체적으로. 답부터, 그다음 근거. 회차 제목과 인물 이름을 쓴다 |

## 5. 세션 구성

### 5.1 프롬프트 배치

```text
instructions   NAVIGATE_INSTRUCTIONS (§9.1, 모든 세션 공통·고정)

input
 [0] user      <project/> <author/> <map version="k">…</map>          ← 캐시 경계
 [턴 1]
   user        <turn n="1"> <changes/> <hints/> <viewing/> <quote/> <task/> <question/> <budget/> </turn>
   assistant   function_call request_context(...)
   tool        function_call_output <context> <document …/> … </context>
   assistant   function_call ready_to_answer(...)
   tool        function_call_output <check …/>
   user        <final level="…">…</final>
   assistant   답 본문                                                ← 캐시 경계
 [턴 2]
   user        <turn n="2"> …
   …
```

- **덧붙이기만 한다.** 앞선 턴의 도구 호출과 결과(읽은 문서 본문)를 지우지 않고 그대로 둔다. 그래서 매 단계와 매 턴의 앞부분이 바이트까지 같고, 프롬프트 캐시가 맞는다. 이미 읽은 문서를 다시 보내는 비용은 캐시 적중 비용뿐이다.
- [AI_CHAT_REQUIREMENTS.md §3.3](AI_CHAT_REQUIREMENTS.md)의 "대화 기록에 도구 결과를 넣지 않는다"는 `NAVIGATE`에서는 적용하지 않는다.
- 충분도 판정과 마지막 답 호출도 같은 `instructions`와 같은 앞부분을 쓴다. 판정 지시는 시스템 지시가 아니라 **마지막 user 메시지**로 넣는다(§9.4). 그래야 캐시가 공유된다.
- `store=False`를 유지한다. 한 턴 안의 단계 사이에는 추론 항목(`reasoning.encrypted_content`)을 함께 돌려준다. 턴 사이에 추론 항목을 남길지는 모델 동작을 확인해서 정한다(구현 메모).

### 5.2 별칭

- 별칭은 **세션 안에서 고정**한다. 지도를 처음 만들 때 지도 순서대로 `D1…`, `M1…`을 매기고 `chat_session_aliases`에 저장한다. 뒤에 생긴 문서는 다음 번호를 받는다.
- 지금 `FULL`의 별칭은 위치 기반이라 회차가 하나 늘면 뒤가 모두 밀린다. `NAVIGATE`에서는 그러면 안 된다. 앞선 턴의 기록과 근거가 별칭으로 문서를 가리키기 때문이다.

### 5.3 기록 캐시

- Content는 revision마다 본문을 남기지 않는다(`document_versions`는 만료되는 자동 스냅샷이다). 그래서 지난 단계에 보낸 본문을 나중에 똑같이 다시 만들 수 없다. 기록(입력 항목 목록)은 ai-chat이 보관한다.
- 보관 위치는 ai-chat 프로세스 메모리의 LRU다(키: 세션 id, TTL `CHAT_TRANSCRIPT_TTL_MINUTES`). 지금 replica가 1개라서 충분하다. replica를 늘리면 Valkey로 옮긴다.
- 원고 사본을 DB에 오래 남기지 않으려는 결정이다. 캐시를 놓치면(재시작, TTL 만료) 압축(§5.5)으로 다시 만든다. 그때는 모델 쪽 프롬프트 캐시도 이미 식었을 가능성이 높아서 손해가 작다.
- DB에는 **무엇을 언제 어느 revision으로 읽었는지**(원장, `chat_context_reads`)만 남긴다. 본문은 남기지 않는다.

### 5.4 문서 변경

턴을 시작할 때 `chat-map`을 받아 지도 판(version)과 원장을 비교하고, 차이를 `<changes>`에 적는다. 지도 자체는 고치지 않는다(고치면 캐시가 깨진다).

| 변화 | `<changes>` 표기 | 처리 |
|---|---|---|
| 보유 문서가 수정됨 | `<edited id="D12" read_rev="20" rev="25"/>` | 옛 판으로는 근거를 인정하지 않는다(§10). 모델이 다시 요청할 수 있고, 서버는 이 문서를 "보유"로 걸러 내지 않는다 |
| 보유하지 않은 문서가 수정됨 | `<edited id="D40" rev="9"/>` | 지도 노드의 revision·요약이 낡았음을 알린다 |
| 새 문서 | `<added id="D313" kind="manuscript" number="301" title="…" chars="8120" rev="1"/>` | 새 별칭을 매기고 노드 정보를 함께 준다 |
| 삭제·휴지통 | `<removed id="D7"/>` | 요청하면 서버가 빈 결과를 준다. 근거에서 지운다 |
| 관계가 바뀜 | `<edge-added …/>` `<edge-removed …/>` | 지도 간선의 변화 |

- 변경이 `CHAT_MAP_CHANGES_LIMIT`개를 넘으면 압축해서 지도를 새로 만든다.
- 편집기의 저장 대기 변경은 질문 전에 프론트가 먼저 저장한다(FULL과 같다).

### 5.5 압축

다음 중 하나면 기록을 버리고 다시 만든다. 지도 판(version)은 1 올라간다.

- 기록 크기 > `CHAT_TRANSCRIPT_LIMIT`
- 기록 캐시를 놓침(재시작·TTL 만료)
- 지도 변경 수 > `CHAT_MAP_CHANGES_LIMIT`
- `FULL`에서 `NAVIGATE`로 바뀜

다시 만든 프롬프트는 이렇다.

```text
[0] user   <project/> <author/> <map version="k+1">…현재 지도…</map>
[1] user   <carried> 최근 CHAT_CARRY_TURNS개 턴에서 근거로 쓰인 문서의 현재 판 </carried>
[2…]       이전 턴들: 질문 글과 답 본문만 (FULL의 대화 기록과 같은 형식)
```

- 별칭은 그대로 쓴다.
- 이어받는 문서도 예산을 쓴다고 보지 않는다. 다만 `CHAT_CARRY_LIMIT`를 넘으면 최근 턴 것부터 남긴다.
- 원장에 새 epoch를 시작하고, 이어받은 문서를 step 0으로 기록한다.

## 6. 사전 컨텍스트: 지도

### 6.1 노드

| 필드 | 원고 | 설정 문서 | 메모 | 출처 |
|---|---|---|---|---|
| `id` | ✓ | ✓ | ✓ | 세션 별칭 |
| `kind` | `manuscript` | `character`·`place`·… | `memo` | folder_code |
| `title` | ✓ | ✓ | 있으면 | content |
| `number`, `episode` | ✓ | | | 원고 순번, 에피소드 이름 |
| `about` | | | 파일 메모면 대상 문서 | content |
| `chars` | ✓ | ✓ | ✓ | **요청 비용.** 본문 + 텍스트 속성의 코드 포인트 수 |
| `rev` | ✓ | ✓ | | `revision_no` |
| `<property>` | `description`이 있으면 | `alias`, `description` | | 작가가 쓴 속성. 항상 최신이다 |
| `<excerpt>` | | 본문 앞 `CHAT_EXCERPT_CHARS`자 | 본문 앞 같은 길이 | content |
| `<summary>`, `summary_rev` | ✓ | | | §8. 요약을 만든 판 |
| `stale` | 요약이 낡았으면 `true` | | | §8.3 |
| `editing` | 최근 `SUMMARY_IDLE_MINUTES` 안에 수정됐으면 `true` | ✓ | ✓ | `updated_at` |

설정 문서와 메모에는 LLM 요약을 두지 않는다. 작가가 쓴 `description`·`alias`와 본문 앞부분으로 충분하고, 이 값들은 매 턴 content에서 오므로 항상 최신이다. **낡을 수 있는 것은 원고 요약뿐이다.**

### 6.2 간선

`<edge a="D12" b="D204" key="…">관계 설명</edge>`. content의 `document_relations` 그대로다. 관계 키는 지금처럼 ai-chat이 양쪽 분류로 계산한다. 관계 설명은 작가의 글이라 근거로 인용할 수 있다.

### 6.3 형식

```xml
<map version="3" documents="512" memos="37" edges="148">
<node id="D1" kind="manuscript" number="1" episode="Episode 1. 유리 정원" title="1화 · 초대장" chars="7820" rev="14" summary_rev="14">
<summary>레나가 유리 정원의 초대장을 받고 아르벨 저택을 떠난다. …
인물: 레나 아르벨, 집사 오렌 / 장소: 아르벨 저택 / 새로 드러난 사실: 초대장의 문장이 왕가의 것이다</summary>
</node>
<node id="D41" kind="manuscript" number="41" episode="Episode 4. 새 원고" title="41화 · 균열의 밤" chars="9105" rev="25" summary_rev="20" stale="true" editing="true">
<summary>…</summary>
</node>
<node id="D204" kind="character" title="레나 아르벨" chars="3120" rev="7">
<property key="alias">레나, 아르벨 가의 막내</property>
<property key="description">빛을 다루는 각성자. …</property>
<excerpt>…</excerpt>
</node>
<node id="M3" kind="memo" about="D204" title="레나 각성 시점" chars="420"><excerpt>…</excerpt></node>
<edge a="D12" b="D204" key="APPEARS">처음 각성한 곳</edge>
</map>
```

- 순서는 FULL 스냅샷과 같은 규칙(원고: 에피소드 → 문서 순서, 설정: 분류 → 문서 순서, 메모: 만든 순서)으로 고정한다. 같은 자료면 같은 바이트가 나와야 한다.
- **크기 추정:** 원고 노드 약 450자, 설정 노드 약 350자로 잡으면 원고 300화 + 설정 200개가 약 20만 자다. 매 호출 들어가지만 캐시에 맞는다.
- 지도가 `CHAT_MAP_LIMIT`를 넘을 때의 대안(에피소드 단위로 접고 펼치기)은 §16에 남긴다.

### 6.4 턴 머리말

```xml
<turn n="3">
<changes>…</changes>
<hints>
<match id="D204" on="레나"/>
<match id="D12" on="각성"/>
</hints>
<viewing id="D41" title="41화 · 균열의 밤"/>
<quote id="D41" title="41화 · 균열의 밤">…드래그한 구간…</quote>
<task>…의도별 지시(FULL과 같다)…</task>
<question>레나가 처음 각성한 곳이 어디였지?</question>
<budget chars="120000" step_chars="40000" steps="4"/>
</turn>
```

`<hints>`는 서버가 질문·인용의 낱말을 노드 제목·`alias`와 문자열로 맞춰 본 결과다(최대 `CHAT_HINTS`개). 계산이 싸고 결정적이어서, 첫 단계 정렬의 출발점이 된다. 모델이 따를 의무는 없다.

## 7. 턴 루프

### 7.1 흐름

```text
턴 시작
  manifest(chat-map) 비교 → <changes>, 압축 여부
  머리말 덧붙임
  for step in 1..R:
    LLM 호출 (tool_choice=required; 마지막 단계는 ready_to_answer로 강제)
    ├ request_context → 서버가 담기(§7.3) → <context> 덧붙임 → 다음 단계
    ├ search_text     → content 검색 → <hits> 덧붙임 → 다음 단계
    └ ready_to_answer → 충분도 판정(§7.4)
         S 충분 ∧ C 충분           → 마지막 답(SUPPORTED)
         S 충분 ∧ C 부족 ∧ 남은 단계 → <check>에 "해석이 갈림, 가를 문서를 읽어라" → 다음 단계
         S 부족 ∧ 남은 단계·예산    → <check>에 빠진 사실 → 다음 단계
         그 밖                      → 마지막 답(§7.5로 수준 결정)
  R 도달 또는 예산 소진 → 마지막 답(§7.5)
마지막 답: tool_choice=none, 스트리밍, 근거 파싱(§10)
```

### 7.2 도구

```json
[
  {
    "type": "function",
    "name": "request_context",
    "description": "Read documents or memos, most necessary first.",
    "strict": true,
    "parameters": {
      "type": "object",
      "properties": {
        "ranking": { "type": "array", "items": { "type": "string" }, "minItems": 1, "maxItems": 20,
                     "description": "Node ids in priority order." },
        "missing": { "type": "array", "items": { "type": "string" }, "maxItems": 8,
                     "description": "Facts the answer still needs." }
      },
      "required": ["ranking", "missing"],
      "additionalProperties": false
    }
  },
  {
    "type": "function",
    "name": "search_text",
    "description": "Find where words appear in the full text of the project.",
    "strict": true,
    "parameters": {
      "type": "object",
      "properties": { "query": { "type": "string", "minLength": 1, "maxLength": 100 } },
      "required": ["query"],
      "additionalProperties": false
    }
  },
  {
    "type": "function",
    "name": "ready_to_answer",
    "description": "Declare that the documents you hold are enough to answer.",
    "strict": true,
    "parameters": {
      "type": "object",
      "properties": {
        "facts": {
          "type": "array", "maxItems": 12,
          "items": {
            "type": "object",
            "properties": {
              "fact": { "type": "string" },
              "id": { "type": "string" },
              "quote": { "type": "string" }
            },
            "required": ["fact", "id", "quote"],
            "additionalProperties": false
          }
        },
        "answers": {
          "type": "array", "minItems": 1, "maxItems": 2,
          "items": {
            "type": "object",
            "properties": {
              "answer": { "type": "string" },
              "probability": { "type": "number" }
            },
            "required": ["answer", "probability"],
            "additionalProperties": false
          }
        }
      },
      "required": ["facts", "answers"],
      "additionalProperties": false
    }
  }
]
```

- 한 단계에 도구 하나만 부른다(`parallel_tool_calls=false`).
- 정렬은 상위 20개까지만 받는다. 노드가 수백 개라도 전부 정렬하게 하지 않는다.
- `search_text`는 content 검색을 그대로 쓴다(부분 일치, 결과 최대 `CHAT_SEARCH_HITS`개, 스니펫). 결과 글자 수도 예산에 넣는다. 형태소 분석이 없다는 한계는 도구 설명에 적지 않고 결과가 비면 "다른 표기로 찾아 보라"고 돌려준다.

### 7.3 담기 규칙

```text
cap = min(CHAT_STEP_BUDGET, 남은 CHAT_TURN_BUDGET)
eligible = ranking에서 아래 둘을 뺀 목록 (순서 유지)
    - 지도에 없거나 삭제된 id          → <unknown id/>로 알림
    - 현재 revision으로 이미 보유한 id → <held id rev/>로 알림

top = eligible[0]
if cost(top) > CHAT_STEP_BUDGET and cost(top) ≤ 남은 CHAT_TURN_BUDGET:
    top 하나만 담고 끝냄                     (큰 문서 예외)
for id in eligible:
    if cost(id) ≤ cap: 담음, cap -= cost(id)
    else:              <skipped id reason="budget"/>로 알리고 건너뜀
```

- 순서대로 보면서 **들어가는 것만 담는다.** 1순위는 언제나 담긴다. 남는 예산은 뒤 순위의 작은 문서로 채운다. 담지 못한 문서는 다음 단계에서 다시 요청할 수 있다.
- 비용은 새로 가져온 글자 수로 센다. 단계마다 앞부분 전체를 다시 보내는 비용은 캐시 덕분에 싸고, 최대 단계 수 R이 그 총량을 묶는다.
- 결과 형식:

```xml
<context step="2" used="38410" turn_left="61200" steps_left="2">
<document id="D12" kind="manuscript" number="12" title="12화 · 균열의 밤" rev="23">
<property key="description">…</property>
<body>
…본문…
</body>
</document>
<held id="D204" rev="7"/>
<skipped id="D88" reason="budget"/>
</context>
```

### 7.4 충분도 판정과 확신도

**충분도 S.** `ready_to_answer`를 받으면 판정 호출을 한다. 같은 모델, 같은 앞부분에 판정 지시(§9.4)를 user 메시지로 붙이고 JSON 스키마 출력으로 받는다.

```json
{
  "required_facts": [
    { "fact": "레나가 처음 각성한 장소", "status": "FOUND",
      "evidence": [ { "id": "D12", "quote": "레나는 무너진 성당의 제단 앞에서 처음으로 빛을 끌어냈다." } ] },
    { "fact": "그것이 첫 각성이라는 점", "status": "FOUND", "evidence": [ … ] }
  ],
  "sufficient": true,
  "missing": []
}
```

- 서버가 근거를 검증한다. `id`가 보유 문서(현재 revision)인지, `quote`가 그 본문·속성·관계 설명에 실제로 있는지 본다. 틀리면 그 사실을 `MISSING`으로 내린다.
- S = (검증된 `FOUND` 수) / (필요 사실 수). **충분** = `sufficient`가 true이고, 검증 뒤에도 `MISSING`·`CONFLICT`가 없을 때다.
- `CONFLICT`(문서끼리 어긋남)는 충분으로 칠 수 있다. 다만 답에서 모순을 짚게 한다.

**확신도 C.** `ready_to_answer.answers`를 쓴다.

- C = 1순위 답의 확률. 답이 하나면 그 확률.
- **충분** = C ≥ `CHAT_CONFIDENCE_MIN` 이고 (1순위 − 2순위) ≥ `CHAT_CONFIDENCE_MARGIN`.
- "0~100으로 확신도를 말하라"는 방식은 쓰지 않는다. RLHF로 다듬은 모델은 자기 평가 점수가 거의 항상 최고점에 몰린다(TASR, 2026).
- 한 턴에서 `ready_to_answer`가 두 번 이상 오면, 1순위 답이 직전과 같은지도 기록한다. 지금은 종료 조건에 넣지 않고, 평가(§14)에서 쓸모를 본다.

판정 호출은 `ready_to_answer`가 올 때만 한다. 그 전에는 모델 스스로 더 필요하다고 보고 있으니 확인할 이유가 없다.

### 7.5 근거 수준과 마지막 답

| 수준 | 조건 | 답 |
|---|---|---|
| `SUPPORTED` | S 충분 ∧ C 충분 | 그대로 답한다 |
| `AMBIGUOUS` | S 충분 ∧ C 부족 | "원고에 근거는 있지만 해석이 갈린다"고 말하고, 해석마다 무엇에 기대는지 보인다 |
| `PARTIAL` | S 부족, 검증된 `FOUND`가 1개 이상 | **"원고에서 충분한 내용을 찾지 못했다"로 시작한다.** 문서가 보여 주는 부분만 답하고, 그 너머는 추정이라고 밝히고, 빠진 사실을 말한다 |
| `NOT_FOUND` | S 부족, 검증된 `FOUND`가 없음 | 읽은 범위 안에서 찾지 못했다고 말한다. 무엇을 읽었는지 밝히고, 작가가 확인해 볼 곳을 제안한다. 추정하지 않는다 |

- 판정 없이 끝난 경우(R 도달 전에 `ready_to_answer`가 한 번도 없었음)에는 마지막 단계에서 `ready_to_answer`를 강제하므로 언제나 판정이 한 번은 있다. 판정 호출이 실패하면 `PARTIAL`로 본다.
- **충분도가 부족한데 확신도가 높은 경우는 확신도를 무시한다.** 자료가 부족한데 확신하는 것이 지어내기의 전형이다(Sufficient Context, ICLR 2025).
- 마지막 답은 `<final>`(§9.5)을 덧붙이고 도구 없이(`tool_choice=none`) 스트리밍한다.
- 수준과 범위는 답 메시지의 `grounding`에 저장하고 `done`으로 내려보낸다(§11.2).

## 8. 요약

### 8.1 대상과 내용

- 대상은 **원고 회차만**이다. 원고에 작가가 쓴 `description`이 있어도 요약은 따로 만든다. 길 찾기에는 등장 인물·장소·새 사실 목록이 더 쓸모 있다.
- 내용: 일어난 일 2~4문장, 그리고 `인물`·`장소`·`사물·개념`·`새로 드러난 사실` 목록. 최대 `SUMMARY_MAX_CHARS`자. 프롬프트는 §9.6.

### 8.2 요약은 길 찾기용이다

답은 언제나 읽어 온 본문으로 한다. 요약이 낡았을 때의 피해는 틀린 답이 아니라 "필요한 문서를 늦게 읽거나 놓치는 것"이다. 그래서 갱신을 느슨하게 하고, 놓침은 세 가지로 막는다.

- `search_text`는 언제나 최신 본문을 찾는다.
- 노드에 `summary_rev`·`rev`·`stale`을 보여 준다. 모델은 낡은 요약을 덜 믿는다.
- 지금 쓰는 중인 문서는 `editing="true"`로 표시한다.

### 8.3 갱신 정책

다음을 **모두** 만족하면 다시 만든다.

- 마지막 수정 뒤 `SUMMARY_IDLE_MINUTES`(10분)가 지났다.
- 요약 이후 충분히 바뀌었다: |현재 글자 수 − 요약 때 글자 수| / 요약 때 글자 수 ≥ `SUMMARY_CHANGE_RATIO`(0.1), 또는 revision 차이 ≥ `SUMMARY_REVISION_GAP`(20).

요약이 없으면 조건 없이 만든다. `stale`은 둘째 조건만 본다.

- 글자 수 차이는 대리 지표다. 길이가 같은 고쳐 쓰기는 놓친다. revision 차이 조건이 그 빈틈을 줄인다. 옛 본문이 없어서 정확한 diff는 계산할 수 없다.
- **지연 방식으로 만든다.** `NAVIGATE` 세션이 지도를 만들 때 갱신 대상을 찾아 백그라운드 작업에 넣는다. 그 턴은 기다리지 않고 지금 요약(또는 요약 없음)으로 진행한다. 챗을 쓰지 않는 작품에는 비용이 들지 않는다.
- 처음 `NAVIGATE`를 여는 큰 작품은 요약이 하나도 없다. 첫 몇 턴은 제목·순번·검색으로 길을 찾고, 요약은 최근 회차부터 채운다. 지난 회차는 거의 바뀌지 않으니 한 번 만들면 오래 간다.
- 모델은 `CHAT_SUMMARY_MODEL`로 따로 정한다. 기본은 답변 모델이고 추론 강도는 `low`다.
- 동시성: 같은 문서는 한 번에 하나만 만든다(`document_summaries`에 행 잠금 또는 진행 표시). 결과를 저장할 때 더 새 판의 요약이 이미 있으면 버린다.

## 9. 프롬프트

모든 프롬프트는 영어로 쓴다(FULL과 같다). 답은 질문의 언어로 한다. `# Format`·`# Sources`는 FULL의 `INSTRUCTIONS`(`loresentry-ai-chat/app/prompt.py`)와 같은 내용을 쓰고, 다른 줄만 여기 적는다.

### 9.1 시스템 지시 `NAVIGATE_INSTRUCTIONS`

```text
You are the story assistant inside Lore Sentry, a writing tool for web novel authors. You answer the author's questions about their own work.

# Materials
This project is too large to read at once. You work from a map and read documents on request.
- <project> and <author>: the work and the person you are talking to.
- <map>: every document and memo in the project.
  - <node>: id, kind, title, size in characters (`chars`) and current revision (`rev`). `number` is the reading order of a manuscript chapter (1 is the first chapter); `episode` is the arc it belongs to.
  - <property> and <excerpt> come from the document itself and are current.
  - <summary> was written by a model from an earlier text of the chapter (`summary_rev`). It helps you decide what to read. It is never evidence.
  - `stale="true"`: the chapter changed a lot after its summary was written. `editing="true"`: the author is editing it now.
  - <edge>: a link the author made between two documents, with the author's description of the link.
- <document>: the full text of a document you requested. It stays in this conversation; do not request it again unless <changes> lists it as edited.
- <memo> and memo nodes: the author's working notes. Notes are plans and reminders, not facts of the story. When you rely on a note, say that it comes from the author's notes, and never present a note as something that happens in the manuscript.
- <changes> at the start of a turn lists documents added, removed or edited since the map was made. If a document you read earlier is listed as edited, its old text is out of date: do not rely on it, and request it again if you need it.

# Finding what you need
At each step, call exactly one tool.
- request_context(ranking, missing): the documents to read next, most necessary first. The server reads them in your order until this step's budget runs out. Anything that does not fit can be requested at a later step.
- search_text(query): find where a name, phrase or event appears in the full text when the map does not tell you. Results are short snippets: use them to decide what to read, not as evidence. Search has no stemming, so try the exact form used in the text, or a shorter form.
- ready_to_answer(facts, answers): call this when the documents you hold are enough. List each fact the answer needs with the document that shows it and an exact quote. Give your one or two most likely answers with probabilities that reflect how likely each is to be right.

How to rank:
- Rank again from scratch at every step, using what you have read so far.
- Put first the documents most likely to contain the facts still missing, and write those facts in `missing`.
- Start from the quoted passage, the document the author is viewing, the <hints>, and nodes whose title, aliases or summary match names in the question.
- Follow edges from documents that turned out to be useful. Drop directions that turned out to be irrelevant.
- For questions about when something happened, the first time, or the order of events, read chapters around the likely range in reading order.
- To claim that something never happens, search first and read the chapters the search points to. A summary that does not mention it proves nothing.
- Do not list documents you already hold at their current revision.
- Spend the budget on what this question needs, not on reading everything related.

# How to answer
- Answer only from documents you have read in this conversation at their current revision. Do not answer from summaries, excerpts of unread documents, search snippets or general knowledge. If the documents do not contain the answer, say so plainly and say what is missing. Do not invent events, traits or settings.
- Distinguish what the manuscript shows from what setting documents or notes say, especially when they disagree. Point out contradictions you notice.
- You cannot edit documents. If asked to rewrite, you may offer wording in your answer, but say that the author has to apply it.
- Answer in the language of the author's question. If that is unclear, use the author's locale.
- Be concise and specific: lead with the answer, then the supporting points. Prefer chapter titles and character names over vague references.
- Follow the <final> instruction on how to frame the answer.

# Format
(FULL과 같다)

# Sources
(FULL과 같다. 다음 한 줄을 더한다)
- Cite only documents and memos you have read in this conversation at their current revision. Never cite a node you only saw in the map.
```

### 9.2 턴 머리말

§6.4의 형식. `<task>`는 FULL의 `TASKS`를 그대로 쓴다.

### 9.3 단계 사이 알림

도구 결과 끝에 서버가 붙이는 한 줄이다.

| 상황 | 문구 |
|---|---|
| 일반 | `<budget turn_left="61200" step_chars="40000" steps_left="2"/>` |
| 마지막 단계 직전 | `<note>Next is the last step. Call ready_to_answer with what you hold.</note>` |
| 예산 소진 | `<note>The reading budget for this question is used up. Call ready_to_answer with what you hold.</note>` |
| 판정 결과: 부족 | `<check sufficient="false" found="1" required="3"><missing>각성 직후 만난 인물</missing>…</check>` + `<note>Find the missing facts, or call ready_to_answer if they cannot be found.</note>` |
| 판정 결과: 해석이 갈림 | `<check sufficient="true" confident="false"/>` + `<note>The documents support more than one answer. Read what would decide between them, or call ready_to_answer again.</note>` |
| 검색 결과 없음 | `<hits query="…" count="0"/>` + `<note>No match. Try another spelling or a shorter form.</note>` |

### 9.4 충분도 판정 지시 (user 메시지)

```text
<review>
Stop acting as the assistant for this message. Review the documents gathered in this conversation for the latest question, strictly.

The documents are sufficient if a definite, plausible answer to the question can be built from the full documents read in this conversation, at their current revision, without summaries, excerpts, search snippets, notes presented as story facts, or outside knowledge. The answer does not have to be right, only supported by the documents.
- Questions with several steps: every link must appear in the documents. Do not bridge a gap with inference.
- Ambiguous questions: sufficient only if the documents resolve the ambiguity and answer the resolved question.
- Several candidate answers: sufficient only if the documents give a reason to choose one.
- Claims that something never or not yet happens: sufficient only if a search was run and the chapters it points to were read.

List the facts the answer needs. For each, give status FOUND with the document id and an exact quote, MISSING, or CONFLICT when documents disagree (quote both).

Example
Question: 레나가 처음 각성한 곳은 어디야?
Read: D12 contains "레나는 무너진 성당의 제단 앞에서 처음으로 빛을 끌어냈다."
Result: the place and the fact that it was the first time are both FOUND in D12. sufficient = true.
If only D30 were read, containing "성당에서의 각성 이후 레나는 달라졌다", the place is FOUND but "first time" is MISSING. sufficient = false.
</review>
```

출력은 §7.4의 JSON 스키마(`text.format: json_schema`, `strict: true`)로 받는다. Sufficient Context 논문에서는 예시 하나를 준 프롬프트가 예시 없는 것보다 판정 정확도가 높았다(0.930 vs 0.870).

### 9.5 마지막 답 지시 `<final>`

```text
<final level="SUPPORTED">Answer the question now.</final>

<final level="AMBIGUOUS">Answer the question now. The documents support more than one reading. Say so, give each reading and what it rests on, and do not pick one as certain.</final>

<final level="PARTIAL" read="6" chars="84120">Answer the question now. Begin by saying that you could not find enough in the manuscript to answer fully. Then answer only what the documents you read show. Mark anything beyond them as a guess. Name what is still missing: {missing}.</final>

<final level="NOT_FOUND" read="6" chars="84120">Answer the question now. Say that you could not find this in the documents you read, and name them briefly. Suggest where the author might look. Do not guess.</final>
```

### 9.6 요약 생성 (별도 호출)

```text
Summarize one chapter of a web novel so that a reader of the summary can decide, without reading the chapter, whether it holds the answer to a question about the story.
Write in the language of the chapter.
- First, 2 to 4 sentences on what happens, in order.
- Then these lines, leaving out any that would be empty:
  인물: names that appear, with the other names or titles used for them
  장소: places
  사물·개념: objects, powers, organizations, terms
  새로 드러난 사실: facts revealed for the first time (relationships, abilities, secrets, deaths)
- Use names exactly as written in the chapter. No evaluation, no guesses, nothing from other chapters.
- At most 600 characters.
```

입력은 회차 제목·순번·에피소드와 본문이다. 줄 이름(`인물` 등)은 작품 언어에 맞춰 바꾼다.

## 10. 근거 검증

- 검증 대상(`materials`)은 턴 시작 때 고정되지 않는다. **이 세션에서 현재 revision으로 보유한 문서와 메모**가 대상이다. 단계가 지날 때마다 늘어난다.
- `<changes>`에서 수정된 것으로 표시된 문서를 옛 판으로 인용하면 `quote`를 null로 바꾼다. 다시 읽었다면 새 판으로 검증한다.
- 지도에서만 본 노드(읽지 않음)를 인용하면 그 근거를 지우고 본문 표시도 지운다.
- 관계 설명 인용은 지금처럼 관계 데이터와 대조한다.

## 11. API 계약

### 11.1 ai-chat → content (내부)

기존 `chat-snapshot`·`chat-size`는 FULL용으로 그대로 둔다. 다음을 더한다. 권한·404·`no-store` 규칙은 기존 내부 API와 같다.

**`GET /internal/projects/{id}/chat-map`** — 지도와 manifest. 본문은 없다.

```json
{
  "project": { "id": "…", "name": "…", "description": "…" },
  "manuscripts": [
    { "id": "…", "title": "12화 · 균열의 밤", "ordinal": 12, "episode_name": "Episode 4. 새 원고",
      "properties": [ { "key": "description", "value": "…" } ],
      "revision_no": 23, "chars": 9105, "updated_at": "…" }
  ],
  "settings": [
    { "id": "…", "folder_code": "CHARACTER", "title": "레나 아르벨",
      "properties": [ { "key": "alias", "value": "…" }, { "key": "description", "value": "…" } ],
      "excerpt": "…본문 앞 200자…", "revision_no": 7, "chars": 3120, "updated_at": "…" }
  ],
  "relations": [ { "low_document_id": "…", "high_document_id": "…", "description": "…" } ],
  "memos": [ { "id": "…", "scope": "FILE", "document_id": "…", "title": "…", "excerpt": "…", "chars": 420, "updated_at": "…" } ],
  "size": 2318734
}
```

- 순서·필터 규칙은 `chat-snapshot`과 같다. `chars`는 그 문서가 분량(`size`)에 더하는 값과 같아야 한다.
- 원고 요약은 ai-chat이 가진다. content는 요약을 모른다.

**`POST /internal/projects/{id}/chat-documents`** — 고른 문서·메모의 본문.

```json
요청 { "document_ids": ["…"], "memo_ids": ["…"] }
응답 { "documents": [ { …chat-snapshot의 문서 항목과 같은 모양… } ],
       "memos": [ { …chat-snapshot의 메모 항목과 같은 모양… } ],
       "missing": ["…"] }
```

- 한 번에 최대 50개. 이 프로젝트의 활성 항목만 돌려주고, 없거나 휴지통에 있는 id는 `missing`에 넣는다.

**`GET /internal/projects/{id}/search?q=`** — 공개 검색(`GET /projects/{id}/search`)과 같은 동작의 내부 경로. `X-User-Id`로 권한을 본다.

### 11.2 gateway → 브라우저 (SSE 추가)

| 이벤트 | 데이터 | 시점 |
|---|---|---|
| `phase` | `{"phase":"navigating","step":2,"reading":[{"file_id":"…","title":"12화 · 균열의 밤"}]}` | 문서를 읽어 올 때. 검색이면 `"searching":"레나"` |
| `phase` | `{"phase":"checking"}` | 충분도 판정 중 |

- 기존 `gathering`·`reading`·`writing`은 그대로 쓴다. 프론트가 모르는 `phase`는 무시해도 동작한다.
- 답 메시지에 `grounding`을 더한다.

```json
"grounding": {
  "mode": "NAVIGATE",
  "level": "PARTIAL",
  "documents_read": 6,
  "chars_read": 84120,
  "missing": ["각성 직후 만난 인물"]
}
```

`FULL`의 답은 `{"mode":"FULL","level":"SUPPORTED"}`로 둔다. 화면에 근거 수준을 어떻게 보일지는 §16.

## 12. 데이터 모델 (ai_chat DB)

```text
chat_sessions
  + mode               VARCHAR(16)  FULL / NAVIGATE. 첫 질문에서 정함
  + map_version        INTEGER      압축할 때 1씩 오름 (= 원장 epoch)

chat_session_aliases (신규)
  session_id, alias, target_kind(DOCUMENT/MEMO), target_id
  UNIQUE(session_id, alias), UNIQUE(session_id, target_id)

chat_context_reads (신규)  — 원장. 본문은 남기지 않는다
  id, session_id, turn_id, map_version, step, target_kind, target_id, alias,
  revision_no, chars, rank_position, reason(REQUESTED/CARRIED), created_at
  INDEX(session_id, map_version)

document_summaries (신규)
  project_id, document_id, revision_no, source_chars, summary, model, prompt_version,
  created_at
  PRIMARY KEY(document_id)

chat_turns
  + mode, steps, fetched_chars, searches, judge_calls,
  + sufficiency NUMERIC, confidence NUMERIC, confidence_margin NUMERIC,
  + grounding VARCHAR(16), stop_reason VARCHAR(16)  SUFFICIENT / MAX_STEPS / BUDGET / JUDGE_FAILED

chat_messages
  + grounding JSONB    답만
```

- 보유 문서 = 현재 `map_version`의 `chat_context_reads`에서 대상별 최신 revision.
- 삭제: `document_summaries`는 원고에서 나온 글이라 원고와 같이 지운다. 프로젝트 영구 삭제·계정 삭제 때 ai-chat의 기존 삭제 경로(`DELETE /projects/{id}/chat/data`, `DELETE /users/me/data`)가 함께 지운다. 문서 하나가 삭제될 때는 지금 신호가 없으므로, 지도를 만들 때 지도에 없는 문서의 요약을 지운다. Kafka `FileChanged`를 받게 되면 이벤트로 바꾼다.
- 로그에는 원고·질문·요약 원문을 남기지 않는다(기존 규칙).

## 13. 설정값

| 이름 | 초깃값 | 뜻 |
|---|---|---|
| `CHAT_NAVIGATE_ENABLED` | `false` | 탐색 모드 사용 여부 |
| `CHAT_FULL_LIMIT` | 1,400,000 | 이 분량 이하면 FULL |
| `CHAT_MAP_LIMIT` | 400,000 | 지도 글자 수 한도 |
| `CHAT_TURN_BUDGET` | 120,000 | 질문당 새로 읽는 글자 수 상한 |
| `CHAT_STEP_BUDGET` | 40,000 | 단계당 상한 |
| `CHAT_MAX_STEPS` | 4 | 최대 단계 수 R |
| `CHAT_RANK_TOP_K` | 20 | 정렬 목록 최대 길이 |
| `CHAT_SEARCH_HITS` | 20 | 검색 결과 최대 개수 |
| `CHAT_HINTS` | 10 | 머리말 힌트 최대 개수 |
| `CHAT_EXCERPT_CHARS` | 200 | 설정·메모 노드의 본문 앞부분 길이 |
| `CHAT_CONFIDENCE_MIN` | 0.6 | 확신도 기준 |
| `CHAT_CONFIDENCE_MARGIN` | 0.2 | 1·2순위 확률 차이 기준 |
| `CHAT_TRANSCRIPT_LIMIT` | 600,000 | 기록 글자 수가 이걸 넘으면 압축 |
| `CHAT_TRANSCRIPT_TTL_MINUTES` | 60 | 기록 캐시 TTL |
| `CHAT_MAP_CHANGES_LIMIT` | 30 | 지도 변경이 이만큼 쌓이면 압축 |
| `CHAT_CARRY_TURNS` / `CHAT_CARRY_LIMIT` | 3 / 120,000 | 압축 때 이어받을 문서의 범위와 상한 |
| `CHAT_NAVIGATE_DEADLINE_SECONDS` | 300 | 탐색 턴 전체 마감. FULL의 턴 마감과 따로 둔다 |
| `CHAT_SUMMARY_MODEL` | 답변 모델 | 요약 모델 |
| `SUMMARY_MAX_CHARS` | 600 | 요약 최대 길이 |
| `SUMMARY_IDLE_MINUTES` | 10 | 이 시간 동안 수정이 없어야 다시 만듦 |
| `SUMMARY_CHANGE_RATIO` | 0.1 | 글자 수 변화 비율 기준 |
| `SUMMARY_REVISION_GAP` | 20 | revision 차이 기준 |

예산은 처음에 넉넉하게 잡는다. 평가(§14)에서 비용 대비 재현율 곡선을 보고 줄인다. 검색 깊이는 약 3회부터 이득이 줄어든다는 보고가 있어(BCAS, 2026) R을 4로 시작한다.

## 14. 지표와 평가

### 14.1 턴 지표

`chat_turns`에 남긴다: 모드, 단계 수, 읽은 글자 수, 검색 수, 판정 수, S, C, 근거 수준, 종료 이유, 입력·캐시·출력 토큰(단계 합), 첫 토큰까지 시간, 전체 시간.

### 14.2 오프라인 평가 — FULL을 기준으로

작은 작품은 FULL로 답할 수 있다. 같은 작품에 `CHAT_FULL_LIMIT`를 낮춰 NAVIGATE를 강제하면, **정답 데이터 없이** 비교할 수 있다.

- 기준: FULL 답과 그 답이 인용한 문서 = "필요 문서"
- 질문 세트: 사실 확인, 처음·순서, "~한 적 있어?"(부재 증명), 인물 관계, 드래그 의도 4종

| 지표 | 보는 것 |
|---|---|
| **비용 대비 재현율** | 처음 B글자 안에 필요 문서가 몇 % 들어왔나. 정렬 품질의 핵심 지표 |
| 1단계 적중률 | 첫 정렬 상위 k개 중 필요 문서 비율. 지도·요약·힌트의 품질 |
| 충분해질 때까지의 단계·글자 수 | 비용 |
| 답 일치율 | FULL 답과 같은 내용인가(LLM 판정) |
| 근거 수준의 적절성 | `SUPPORTED`인데 틀린 비율(과신), `PARTIAL`·`NOT_FOUND`인데 FULL은 답한 비율(과소) |
| S·C의 보정 | 값의 구간별 실제 정답률. 기준값(`CHAT_CONFIDENCE_MIN` 등)을 여기서 정한다 |
| 요약 신선도의 영향 | `stale` 노드가 필요 문서였을 때 늦게 읽힌 정도 |

## 15. 실패 처리

| 상황 | 처리 |
|---|---|
| 모델이 지도에 없는 id를 요청 | `<unknown id/>`로 알리고 계속 |
| 정렬 목록이 모두 보유 문서 | 아무것도 담지 않고 `<held …/>`만 돌려줌. 같은 일이 두 번이면 `ready_to_answer`를 강제 |
| 도구 인자 형식 오류 | 한 번 다시 요청. 또 틀리면 `ready_to_answer`를 강제 |
| content 오류 | FULL과 같이 503 `CONTENT_UNAVAILABLE` 계열로 턴 실패 |
| 판정 호출 실패 | 근거 수준 `PARTIAL`로 마지막 답 |
| 턴 마감 초과 | 마지막 답을 시작하지 않았으면 그때까지 보유한 것으로 `PARTIAL` 답을 시도. 이미 스트리밍 중이면 FULL과 같은 실패 처리 |
| 멈추기 | 진행 중 단계를 취소하고 답을 저장하지 않는다. 이번 턴에 읽은 문서는 기록 캐시에서 되돌린다(턴 시작 시점으로). 원장 행은 지운다 |
| 마지막 질문 고치기(`replaces`) | 그 턴의 기록 항목과 원장 행을 지우고 다시 실행 |
| 기록 캐시 놓침 | 압축(§5.5) 후 진행 |

## 16. 열린 질문

1. **판정 모델.** 같은 모델로 하면 캐시를 공유해 싸지만, 답변 모델과 같은 편향을 가질 수 있다. 작은 모델로 따로 하면 독립적이지만 앞부분 캐시를 못 쓴다. 평가에서 비교한다.
2. **비용 단위.** 글자 수로 시작한다. 실제 청구액(캐시 할인 포함 토큰)으로 바꿀지는 지표를 보고 정한다.
3. **근거 수준을 화면에 보일지.** 본문에 이미 단서가 들어가므로 필수는 아니다. `PARTIAL`·`NOT_FOUND`에 "읽은 범위" 칩을 보이는 안을 프론트와 정한다.
4. **지도가 한도를 넘는 작품.** 에피소드 노드(`E3`)로 접고, `request_context`로 에피소드를 요청하면 그 안의 회차 노드를 펼쳐 주는 계층 지도. 요약 트리를 따라 내려가는 MemWalker와 같은 구조다.
5. **요약 일괄 생성.** 큰 작품이 처음 챗을 열 때 회차 전체를 요약할지, 최근부터 천천히 채울지(지금 안). 비용 상한이 필요하다.
6. **프로젝트 메모.** 짧은 프로젝트 메모는 노드 대신 지도에 전문으로 넣을지.
7. **추론 항목.** `store=False`에서 턴 사이 추론 항목을 남겨야 캐시와 품질이 유지되는지 확인한다.

## 17. 도입 순서

1. content: `chat-map`, `chat-documents`, 내부 검색.
2. ai-chat: 별칭 고정, 원장, 기록 캐시, 모드 선택. `CHAT_NAVIGATE_ENABLED=false`로 배포.
3. ai-chat: 탐색 루프와 도구, `ready_to_answer` 자기 확신도만으로 종료(판정 없이). 지금 막혀 있는 큰 작품에만 켠다. 기존 사용자에게는 영향이 없다.
4. 충분도 판정과 근거 수준, `grounding` 계약, 프론트 `phase` 문구.
5. 요약 작업.
6. 평가 도구(§14.2)로 기준값을 보정하고, `CHAT_FULL_LIMIT`를 낮출지 정한다.

## 18. 참고 연구

| 연구 | 이 설계에서 가져온 것 |
|---|---|
| Joren 외, [Sufficient Context](https://arxiv.org/abs/2411.06037) (ICLR 2025) | 충분도의 정의(정답 없이 판정), 판정 프롬프트의 예시 하나, "부족한데 확신하면 지어낸다", 충분도와 확신도를 함께 쓰는 선택적 생성 |
| Kieback 외, [TASR](https://arxiv.org/abs/2606.13814) (KDD 2026 Agent4IR 워크숍) | 말로 하는 확신도 점수는 쓸모없다는 근거, 라운드 사이 답의 안정성 |
| Wu 외, [When Knowledge Is Not Free](https://arxiv.org/abs/2606.02245) (2026) | 고정된 예산 선택기보다 에이전트가 획득을 조절하는 편이 유망하다는 근거 |
| [BCAS](https://arxiv.org/abs/2603.08877) (2026) | 검색 깊이가 약 3회부터 이득이 줄어든다는 보고 → R=4 |
| Li 외, [GraphReader](https://arxiv.org/abs/2406.14550) (EMNLP Findings 2024) | 그래프 + 노드 읽기 함수 + 충분하면 멈추는 에이전트 구조 |
| Chen 외, [MemWalker](https://arxiv.org/abs/2310.05029) (2023) | 요약으로 길을 찾고 본문으로 답하기, 계층 지도(§16-4) |
| Sun 외, Think-on-Graph (ICLR 2024) | 단계마다 충분성을 평가하는 그래프 탐색 |
