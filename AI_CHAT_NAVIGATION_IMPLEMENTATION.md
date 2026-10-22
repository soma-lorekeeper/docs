# AI 챗 탐색 구현 설계

> 갱신일: 2026-10-22
> 상태: **구현 완료, 로컬 검증 완료(§10).** [AI_CHAT_NAVIGATION_DESIGN.md](AI_CHAT_NAVIGATION_DESIGN.md)(무엇을 하는가)를 지금 코드에 어떻게 넣는가를 정한다.
> 독자: ai-chat·content를 구현하는 사람.
> 기준 코드: `loresentry-ai-chat` `41ef19b`, `loresentry-content` `4aeec2e`, `loresentry-gateway` `12fe815`, `loresentry-frontend` `a3f4a86`, `loresentry-gitops` `bdb1cd3`

## 0. 요약

| 레포 | 바뀌는 것 | 규모 |
|---|---|---|
| **ai-chat** | ① 답변 서버: 매 턴 전체 스냅샷 + 단일 호출을 **에이전트 루프**(답 → 판정 → 전달)로 바꾼다. 프레임워크 없이 직접 짠다. ② **요약 스케줄러**: 같은 레포·같은 이미지의 별도 프로세스. Kafka를 받아 요약을 만든다 | 큼 |
| **content** | 내부 API 2개(`chat-map`, `chat-documents`)와 백필용 이벤트 재발행 1개 추가. 기존 API와 이벤트는 그대로 | 작음 |
| **gitops** | `ai-chat-summarizer` Deployment, 요약용 OpenAI 키 Secret, 설정값 | 작음 |
| **frontend** | 대기 문구 하나. 계약은 그대로 | 아주 작음 |
| **gateway** | 바꾸지 않는다. ai-chat 요청 제한 `request-timeout: 180s`가 루프 전체의 시간 상한이다(§1.6) | 없음 |

## 1. 에이전트 루프

### 1.1 프레임워크를 쓰지 않는다

| 선택지 | 판단 |
|---|---|
| **직접 구현** (Responses API + 반복문) | **채택.** 루프는 100줄 남짓이다. 이 설계의 핵심(문서 순서를 지키는 담기, 답을 내보내기 전 판정, 캐시를 위한 입력 배치)은 프레임워크 밖의 로직이다. 지금 코드(`OpenAIModel`, `CitationStream`, `TurnRunner`의 취소·마감·지표)를 거의 그대로 재사용한다. 가짜 모델로 결정적으로 테스트할 수 있다 |
| OpenAI Agents SDK | 판정 게이트와 마지막 단계 강제를 hook으로 끼워야 하고, 입력 배치를 SDK가 만들어 캐시 통제가 약해진다 |
| LangGraph | 노드 두세 개짜리 루프에 비해 무겁다. 요구사항 문서의 "에이전트 프레임워크를 쓰지 않는다" 결정과 어긋난다 |

### 1.2 구성 요소

```text
TurnRunner (turns.py, 기존)          턴의 수명: 취소 감시, 마감, 지표, 저장, SSE
 ├ TurnInput (navigation.py, 신규)    지도·보유 문서·대화·질문으로 입력을 만든다
 ├ Agent (agent.py, 신규)             루프
 │   ├ model.step()                   응답 모델 호출 한 번 → 도구 호출 또는 답
 │   ├ pack() + content.documents()   request_documents 실행
 │   ├ model.sufficiency()            충분도 판정 (AI가 쓴 글 없이)
│   └ model.check_answer()           답 판정 (둘을 동시에)
 └ deliver()                          통과한 답을 CitationStream에 통과시켜 SSE로 보냄
```

### 1.3 모델 호출

| 호출 | 용도 | 출력 | 설정 | 스트리밍 |
|---|---|---|---|---|
| `step`(탐색) | 다음 행동 결정 | 도구 호출 1개 또는 답 초안 | 도구 `request_documents`, `tool_choice=auto`, 추론 강도 `CHAT_NAVIGATION_REASONING_EFFORT`(low) | 아니요 |
| `step`(답) | 답 쓰기 | 답 본문 | `tool_choice=none`, 추론 강도 `OPENAI_REASONING_EFFORT`(medium), 출력 상한 `OPENAI_MAX_OUTPUT_TOKENS`. 탐색 단계가 답을 쓰기로 하면 그 초안은 버리고 이 호출로 다시 쓴다. 마지막 단계·마지막 답도 이 호출 | 아니요 |
| `sufficiency` | 충분도 판정 | `{sufficient, missing}` | 모델 `CHAT_JUDGE_MODEL`, `text.format=json_schema`, 추론 강도 `CHAT_JUDGE_REASONING_EFFORT`(low), 출력 상한 2,000 | 아니요 |
| `check_answer` | 답 판정 | `{answer_ok, problems}` | 위와 같다 | 아니요 |
| `summarize` | 요약 스케줄러 전용(§3) | 요약 본문 | 모델 `SUMMARY_MODEL`, low, 1,000 | 아니요 |

- 모든 호출이 `store=False`이고, OpenAI 클라이언트는 일시 오류(5xx·429·연결)를 2회 재시도한다(`max_retries=2`). 턴당 호출이 여러 번이라 한 번의 오류가 턴을 깨지 않게 하려는 것이다. 호출마다 `model call` 로그(강도, 시간, 토큰)를 남긴다. `step`은 `prompt_cache_key=project_id`(기존과 같다).
- `step`은 `include=["reasoning.encrypted_content"]`로 받고, 응답의 `output` 항목(추론 + `function_call` 또는 `message`)을 **그대로** 다음 입력에 덧붙인다. `store=False`에서 단계 사이에 추론을 이어 가는 방법이다.
- 도구가 한 번이라도 쓰인 입력에는 `function_call` 항목이 있으므로, `tool_choice=none`인 호출에도 도구 정의는 함께 보낸다.
- 두 판정은 응답 모델과 다른 지시·입력을 쓰므로 응답 모델과 캐시를 공유하지 않는다. 충분도 입력은 질문 + 이전 질문들 + 보유 문서, 답 판정 입력은 질문 + 대화 + 보유 문서 + 답이다. 보유 문서 블록을 입력 맨 앞에 두어 두 판정끼리는 앞부분이 같게 한다.

### 1.4 루프

```python
# app/agent.py (요지)
async def run(self, turn: TurnInput) -> Outcome:
    items = turn.base_input()                      # 지도, 보유 문서, 대화, 이번 질문
    sufficiency: Sufficiency | None = None
    reviews: list[Review] = []
    for step in range(1, self.settings.max_steps + 1):
        if self.clock.remaining() < self.settings.answer_reserve_seconds:
            break                                  # 마지막 답 시간을 남긴다 (§1.6)
        last = step == self.settings.max_steps
        out = await self.model.step(NAVIGATE_INSTRUCTIONS, items, TOOLS,
                                    tool_choice="none" if last else "auto")
        items += out.items
        if out.call:                               # request_documents
            picked = pack(out.call.ranking, turn.nodes, turn.held_ids,
                          self.settings.step_max_chars)
            docs = await self.content.documents(turn.user_id, turn.project_id, picked.ids)
            turn.hold(docs)
            items.append(tool_output(out.call.id, context_xml(docs, picked)))
            continue
        if sufficiency is None or turn.read_since_check:
            sufficiency_call = self.model.sufficiency(SUFFICIENCY_INSTRUCTIONS, sufficiency_input(turn))
        else:
            sufficiency_call = done(sufficiency)    # 읽은 문서가 그대로면 지난 결과
        sufficiency, check = await asyncio.gather(
            sufficiency_call,
            self.model.check_answer(ANSWER_CHECK_INSTRUCTIONS, answer_input(turn, out.text)))
        turn.read_since_check = False
        if sufficiency.sufficient and check.answer_ok:
            return Outcome("SUPPORTED", out.text, turn.read, reviews)
        review = Review(len(reviews) + 1, sufficiency, check)
        reviews.append(review)
        items.append(user_message(review_xml(review)))
    final = await self.model.step(NAVIGATE_INSTRUCTIONS, items + [user_message(final_xml(reviews))],
                                  TOOLS, tool_choice="none")
    return Outcome("NOT_ENOUGH", final.text, turn.read, reviews)
```

- **`pack()`는 순수 함수**다. 순위, 노드의 `chars`, 보유 목록, 한도를 받아 읽을 id·건너뛴 id·모르는 id를 돌려준다. 담기 규칙(설계 §4)이 여기 다 있다.
- `sufficiency_input()`은 질문, **이전 질문들**, 이번 턴까지의 보유 문서 본문으로 만든다. **AI가 쓴 글(이번 답, 이전 답, 추론)은 하나도 넣지 않는다.**
- `answer_input()`은 질문, 직전 대화, 보유 문서 본문, 답으로 만든다. 응답 모델의 `items`(추론·도구 호출)는 넣지 않는다.
- 거절된 답(`out.items`의 assistant 메시지)은 입력에서 지우지 않고, 그 뒤에 `<review>`를 붙인다. 한 턴의 `<review>`는 모두 누적된다.
- 판정이 `problems`만 지적했는데 다음 단계가 마지막이면, 마지막 단계의 `tool_choice=none`이 곧 고쳐 쓰기다. 그 답도 판정을 받는다.
- `Outcome`을 받은 `TurnRunner`는 `deliver()`로 답을 내보내고(§2.5), 지금처럼 저장하고 `done`을 보낸다.

### 1.5 오류와 이상 동작

| 상황 | 처리 |
|---|---|
| 모델이 지도에 없는 id만 요청 | `pack()`이 `<unknown/>`로 알리고 단계를 소비한다 |
| 순위가 모두 보유 문서 | 읽을 게 없다고 알린다. 연속 두 번이면 다음 단계를 `tool_choice=none`으로 강제한다 |
| 답이 비어 있음 | 지금처럼 `MODEL_EMPTY_RESPONSE`로 턴 실패 |
| 판정 호출 실패(둘 중 하나라도) | 그 답을 `NOT_ENOUGH`로 내보낸다. 다시 쓰지 않는다 |
| `step` 호출 실패 | 지금과 같은 `ModelFailure` → `CHAT_MODEL_UNAVAILABLE` |
| content 실패 | 지금과 같은 `CONTENT_UNAVAILABLE` |
| 취소 | 지금 감시자(`watch`)가 태스크를 취소한다. 루프 어디서든 `CancelledError`로 빠진다. **보유 문서는 턴이 완료될 때만 저장하므로**(§4) 취소·실패한 턴은 아무것도 남기지 않는다 |

### 1.6 시간 예산

gateway가 ai-chat 요청 전체를 180초에서 끊는다(`application.yaml`의 `request-timeout: 180s`). 그래서 `CHAT_TURN_DEADLINE_SECONDS`가 170초다.

| 구간 | 예상 |
|---|---|
| 지도·보유 문서 받기 | 1초 안팎 |
| `step` (medium) | 도구 호출이면 짧고, 답이면 지금의 단일 호출과 비슷(수십 초) |
| `judge` (low) | 수 초 |
| 내보내기 | 1초 안팎 |

- 단계를 시작하기 전 남은 시간이 `CHAT_ANSWER_RESERVE_SECONDS`(75초)보다 적으면 루프를 멈추고 마지막 답(`NOT_ENOUGH`)으로 간다. 답 한 번 + 여유다.
- 지표(`chat_turns`)에 단계 수와 시간을 남긴다. 180초에 자주 닿으면 gateway의 ai-chat 제한을 늘리거나 단계 수를 줄인다. 15초 heartbeat가 있어 중간 끊김(ALB 60초)은 걱정하지 않는다.

## 2. ai-chat 답변 서버

### 2.1 모듈별

| 파일 | 처리 | 내용 |
|---|---|---|
| `app/agent.py` | **신규** | 루프(§1.4), `pack()`, `Outcome`, 도구 정의 |
| `app/navigation.py` | **신규** | `TurnInput`, 지도·보유 문서·결과·review XML, 짧은 id, 판정 입력, 지시문 |
| `app/content.py` | 수정 | `Node`·`ChatMap` 파싱, `ContentSource.map()`·`documents()` 추가. `snapshot()`은 지운다 |
| `app/prompt.py` | 수정 | `build_prompt`·`Aliases`·최근 회차 분리를 지운다. `document_xml`·`question_xml`·`history_answer`·`select_history`·`TASKS`·`Material`은 남긴다. `materials()`는 문서 목록을 받게 바꾼다 |
| `app/model.py` | 수정 | `AnswerModel`을 `step()`·`sufficiency()`·`check_answer()`·`summarize()`로 바꾼다. 오류 변환(`status_code`)은 그대로 공용 |
| `app/turns.py` | 수정 | `run()`을 "턴 입력 → `Agent.run` → `deliver()`"로 바꾼다. 취소·마감·지표·저장은 그대로. 사용량은 호출마다 합산 |
| `app/citations.py` | 그대로 | `CitationStream(materials)`의 대상이 "보유 문서 + 이번 턴에 읽은 문서"가 될 뿐이다. 지도에서만 본 노드는 `materials`에 없으니 근거에서 빠진다 |
| `app/store.py` | 수정 | 보유 문서 읽기·저장·정리, 요약 읽기, `complete_turn`에 `grounding`·보유 문서·새 지표, `message_json`에 `grounding` |
| `app/routes.py` | 수정 | `send`의 분량 초과 검사를 지운다. `availability`는 항상 `available` |
| `app/settings.py` | 수정 | §2.7 |
| `db/migration/V3__navigation.sql` | **신규** | §4 |

### 2.2 턴 입력 (`navigation.py`)

```text
1. phase gathering
2. map     = content.map(user, project)
3. held    = store.held(session) → content.documents(...)        현재 판으로
             합이 CHAT_HELD_MAX_CHARS를 넘으면 먼저 읽은 것부터 빼고 DB에서도 지움
             content가 missing으로 돌려준 것(삭제됨)은 DB에서 지움
4. 요약     = store.summaries(map의 문서 id) → 노드에 붙임
5. history = store.history(...) → select_history(…, CHAT_HISTORY_MAX_CHARS)
6. base input
     user [ <project/><author/><map/>  ← cache breakpoint
            <held/>                      ← cache breakpoint ]
     이전 턴들 (질문 / 답)
     user <turn/>
7. phase reading → Agent.run()
```

지금의 `text_block(..., breakpoint=True)`를 그대로 써서 지도 뒤와 보유 문서 뒤에 캐시 경계를 둔다.

### 2.3 짧은 id

```python
def short_ids(ids: list[str]) -> dict[str, str]:
    for length in (8, 12, 16, 32):
        tails = {i: i.replace("-", "")[-length:] for i in ids}
        if len(set(tails.values())) == len(ids):
            return tails
```

uuidv7은 앞쪽이 생성 시각이라 끝쪽을 쓴다. 지도 전체에 대해 한 번에 계산하므로 같은 자료면 같은 결과다.

### 2.4 `model.py`

```python
class AnswerModel(Protocol):
    name: str
    async def step(self, instructions, items, tools, tool_choice) -> StepResult: ...
    async def sufficiency(self, instructions, text) -> Sufficiency: ...
    async def check_answer(self, instructions, text) -> AnswerCheck: ...
    async def summarize(self, instructions, text) -> SummaryResult: ...

@dataclass(frozen=True)
class StepResult:
    call: ToolCall | None    # request_documents면 채워짐
    text: str                # 답이면 본문
    items: list[dict]        # response.output 그대로
    usage: Usage | None

@dataclass(frozen=True)
class Sufficiency:
    sufficient: bool
    missing: list[str]
    usage: Usage | None

@dataclass(frozen=True)
class AnswerCheck:
    answer_ok: bool
    problems: list[str]
    usage: Usage | None
```

지금의 `stream()`은 지운다. 답을 판정 뒤에 내보내므로 모델 쪽 스트리밍이 필요 없다.

### 2.5 내보내기 (`turns.py`)

```python
def deliver(outcome: Outcome, materials, channel) -> CitationStream:
    parser = CitationStream(materials)
    channel.put("phase", {"phase": "writing"})
    for piece in chunks(outcome.text, 60):         # 지금 delta와 비슷한 크기
        forward(parser.feed(piece), channel)
    forward(parser.finish(), channel)
    return parser
```

`CitationStream`과 `forward()`를 그대로 쓴다. `citation`·`delta` 이벤트 모양이 지금과 같아서 프론트는 바뀌지 않는다. `first_token_ms`는 `writing`을 보낸 시점이다.

### 2.6 `routes.py`

- `send`: `size > context_limit`이면 422를 내던 검사를 지운다.
- `availability`: 항상 `{"status": "available", "size": size, "limit": size}`. 응답 모양을 유지해 프론트를 먼저 바꾸지 않아도 된다.
- `create_session`의 `content.size()`(프로젝트 접근 확인)는 그대로 둔다.

### 2.7 설정값

| 이름 | 기본 | 비고 |
|---|---|---|
| `CHAT_STEP_MAX_CHARS` | 40,000 | 신규 |
| `CHAT_MAX_STEPS` | 4 | 신규 |
| `CHAT_HELD_MAX_CHARS` | 200,000 | 신규 |
| `CHAT_HISTORY_MAX_CHARS` | 40,000 | 신규. 지금은 "한도 − 스냅샷 크기"로 계산하던 대화 예산 |
| `CHAT_JUDGE_MODEL` / `CHAT_JUDGE_REASONING_EFFORT` | `OPENAI_MODEL` / `low` | 신규. medium은 거절만 늘고 느려서 low(평가 §5) |
| `CHAT_NAVIGATION_REASONING_EFFORT` | `low` | 신규. 탐색 단계의 추론 강도(평가 §4-8) |
| `CHAT_ANSWER_RESERVE_SECONDS` | 75 | 신규 |
| `CHAT_CONTEXT_LIMIT`, `CHAT_RECENT_MANUSCRIPTS`, `CHAT_HISTORY_FLOOR` | — | **삭제**. gitops의 `CHAT_CONTEXT_LIMIT`도 지운다 |

### 2.8 테스트

지금 구조(가짜 content, 가짜 모델, Testcontainers Postgres)를 그대로 쓴다. 가짜 모델은 **각본**을 받는다: "1단계 `request_documents([A, B])` → 2단계 답 'X' → 판정 부족(`missing`) → 3단계 `request_documents([C])` → 4단계 답 'Y' → 판정 통과".

| 테스트 | 확인하는 것 |
|---|---|
| `pack()` 단위 | 순서 유지, 한도 안 채우기, 1순위 큰 문서 예외, 보유·모르는 id |
| 짧은 id·지도 XML 단위 | 겹치면 늘어남, 같은 자료면 같은 바이트 |
| 루프: 바로 통과 | `SUPPORTED`, 읽은 문서가 보유로 저장, `grounding` 저장 |
| 루프: 부족 → 더 읽음 → 통과 | `missing`이 다음 `step` 입력에 들어감 |
| 루프: 답에 문제 → 고쳐 씀 → 통과 | `problems`가 다음 `step` 입력에 들어감 |
| 루프: 끝까지 실패 | 마지막 단계 `tool_choice=none`, 마지막 답에 `<final>`, `NOT_ENOUGH` |
| 판정 입력 | 충분도 입력에 AI가 쓴 글(이번 답·이전 답·추론)이 없음. 답 판정 입력에 답과 보유 문서가 있음 |
| 충분도 재사용 | 답 판정만 거절된 뒤 더 읽지 않고 고쳐 쓰면 충분도를 다시 부르지 않음 |
| 거절 누적 | 두 번째 `step` 입력에 첫 답과 첫 `<review>`가 그대로 있음 |
| 보유 문서 갱신 | 다음 턴에 현재 판으로, 삭제된 문서는 빠짐, 한도 넘으면 오래된 것부터 |
| 근거 | 지도에서만 본 노드를 인용하면 근거에서 빠짐 |
| 내보내기 | `phase writing` → `citation`·`delta` 순서가 지금과 같음 |
| 시간 예산 | 남은 시간이 예비분보다 적으면 루프를 멈추고 마지막 답 |
| 취소 | 루프 중 취소하면 답·보유 문서가 저장되지 않음 |
| 기존 API 테스트 | 분량 초과 거절 테스트 2개는 "큰 작품도 답한다"로 바꾼다. 나머지는 각본만 바꿔 유지 |

## 3. 요약 스케줄러

### 3.1 프로세스

| 항목 | 내용 |
|---|---|
| 위치 | ai-chat 레포 `app/summarizer/`. 같은 이미지, 실행 명령만 `python -m app.summarizer` |
| 배포 | gitops `workload/base/ai-chat-summarizer/deployment.yaml`, replicas 1. HTTP를 열지 않는다. 생존 확인은 하트비트 파일을 보는 exec probe |
| 의존 | ai_chat DB(같은 Flyway 스키마), Kafka, OpenAI(지금은 챗 키 `ai-chat-openai`를 같이 쓴다). **content를 부르지 않는다** |
| Kafka 라이브러리 | `confluent-kafka`(graph-rag와 같다). 설정도 graph-rag의 `consumer_config`와 같은 원칙: 수동 커밋, `auto.offset.reset=earliest`, `group.protocol=classic` |

### 3.2 안에서 도는 것

```text
① 컨슈머 (스레드 1)
   토픽 content.project.changed.v1, 그룹 ai-chat-summarizer
   header ce_type으로 거름 (value를 열기 전에)
   FileChanged CREATED / UPDATED / RESTORED
       → summary_queue upsert (revision_no가 더 클 때만):
         document_id, project_id, revision_no, kind, title, body, changed_at=now()
         메모는 FileChanged가 없어 오지 않는다
   FileChanged TRASHED / DELETED
       → summary_queue, document_summaries에서 그 문서 삭제
   ProjectDeleted
       → 그 project_id의 행을 모두 삭제
   DB 반영이 끝난 뒤 오프셋 커밋

② 틱 (asyncio, 1분마다)
   select … from summary_queue
   where changed_at <= now() - SUMMARY_IDLE_MINUTES and next_attempt_at <= now()
   order by changed_at desc
   limit SUMMARY_MAX_PER_MINUTE
   for update skip locked
   → 작업자에게 넘김

③ 작업자 (SUMMARY_WORKERS개)
   fp_now = fingerprint(body)
   s = document_summaries[document_id]
   if s is None or changed(s.fingerprint, fp_now) ≥ threshold(len(body)):
       summary = body if len(body) ≤ 500 else model.summarize(body)
       upsert document_summaries (summary, revision_no, fingerprint=fp_now, model, prompt_version)
   큐 행 삭제 (단, 처리 중 revision이 더 올라갔으면 남겨 둠)
   실패 → attempts += 1, next_attempt_at = now() + [1, 5, 30]분, 3회 넘으면 삭제하고 로그
```

- 변화량이 기준에 못 미치면 요약과 **지문을 둘 다 그대로** 둔다. 작은 수정이 쌓여 기준을 넘으면 다시 요약된다.
- 큐의 `body`는 처리될 때까지만 있는 임시 사본이다. 오래 남는 것은 요약과 지문뿐이다.
- `FOR UPDATE SKIP LOCKED` 덕분에 나중에 replica를 늘려도 같은 문서를 두 번 요약하지 않는다.

### 3.3 지문과 변화량

```python
SENTENCE = re.compile(r"[^.!?。…\n]+[.!?。…]*")

def fingerprint(text: str) -> list[tuple[str, int]]:
    return [(sha1(s.strip()), len(s.strip())) for s in SENTENCE.findall(text) if s.strip()]

def changed(old, new) -> int:
    a, b = Counter(h for h, _ in old), Counter(h for h, _ in new)
    size = dict(old) | dict(new)
    return sum(size[h] * n for h, n in ((a - b) + (b - a)).items())

def threshold(length: int) -> int:
    return min(SUMMARY_MIN_CHANGE_CHARS, int(length * SUMMARY_MIN_CHANGE_RATIO))
```

문장 하나에서 한 글자를 고치면 그 문장 길이의 두 배로 센다. 실제보다 크게 잡는 쪽이라 요약을 놓치지 않는다. 지문은 `JSONB`로 저장한다. 회차 하나에 문장 수백 개, 행 하나에 수십 KB다.

### 3.4 설정값

| 이름 | 기본 |
|---|---|
| `KAFKA_BOOTSTRAP_SERVERS` | configmap `kafka`의 `bootstrap-servers`(graph-rag와 같다) |
| `SUMMARY_TOPIC` / `SUMMARY_GROUP_ID` | `content.project.changed.v1` / `ai-chat-summarizer` |
| `SUMMARY_OPENAI_API_KEY` | 비면 `OPENAI_API_KEY`. 운영은 Secret `ai-chat-openai`를 같이 쓴다 |
| `SUMMARY_MODEL` / `SUMMARY_REASONING_EFFORT` | `gpt-6-luna` / `low` |
| `SUMMARY_IDLE_MINUTES` | 10 |
| `SUMMARY_MIN_CHANGE_CHARS` / `SUMMARY_MIN_CHANGE_RATIO` | 1,000 / 0.3 |
| `SUMMARY_MAX_PER_MINUTE` / `SUMMARY_WORKERS` | 30 / 2 |
| `SUMMARY_PROMPT_VERSION` | `summary-v1` |

프롬프트 버전이 바뀌어도 일괄 재요약하지 않는다. 다음 변경 때 새 버전으로 바뀐다. 일괄로 바꾸려면 백필(§5.3)을 다시 돌린다.

### 3.5 테스트

| 테스트 | 확인하는 것 |
|---|---|
| 지문·변화량 단위 | 같은 글은 0, 문장 추가·삭제·수정, 기준값(짧은 문서는 30%) |
| 이벤트 처리 | 새 revision만 큐에 반영, 옛 revision·중복은 무시, TRASHED·DELETED·ProjectDeleted는 삭제 |
| 틱 | 10분 안 지난 행은 건너뜀, 최근 수정 순, 상한 개수 |
| 작업자 | 요약 없음 → 생성, 변화 작음 → 건너뛰고 지문 유지, 500자 이하는 LLM 없이, 실패 재시도, 처리 중 새 revision이면 큐 유지 |

Kafka는 가짜 메시지 목록으로 컨슈머 처리 함수만 테스트한다. 실제 브로커 연결은 graph-rag처럼 운영 배포에서 확인한다.

## 4. DB (`V3__navigation.sql`)

```sql
CREATE TABLE chat_held_documents (
    session_id    UUID        NOT NULL,
    target_id     UUID        NOT NULL,
    kind          VARCHAR(10) NOT NULL,
    first_read_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pk_chat_held_documents PRIMARY KEY (session_id, target_id),
    CONSTRAINT fk_chat_held_documents_session FOREIGN KEY (session_id)
        REFERENCES chat_sessions (id) ON DELETE CASCADE,
    CONSTRAINT ck_chat_held_documents_kind CHECK (kind IN ('DOCUMENT', 'MEMO'))
);

CREATE TABLE document_summaries (
    document_id    UUID         NOT NULL,
    project_id     UUID         NOT NULL,
    revision_no    BIGINT       NOT NULL,
    summary        TEXT         NOT NULL,
    fingerprint    JSONB        NOT NULL,
    model          VARCHAR(100),
    prompt_version VARCHAR(40)  NOT NULL,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT pk_document_summaries PRIMARY KEY (document_id)
);
CREATE INDEX ix_document_summaries_project ON document_summaries (project_id);

CREATE TABLE summary_queue (
    document_id     UUID         NOT NULL,
    project_id      UUID         NOT NULL,
    revision_no     BIGINT       NOT NULL,
    folder_code     VARCHAR(40)  NOT NULL,
    title           TEXT         NOT NULL,
    body            TEXT         NOT NULL,
    changed_at      TIMESTAMPTZ  NOT NULL,
    attempts        INTEGER      NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT pk_summary_queue PRIMARY KEY (document_id)
);
CREATE INDEX ix_summary_queue_due ON summary_queue (changed_at);
CREATE INDEX ix_summary_queue_project ON summary_queue (project_id);

ALTER TABLE chat_messages ADD COLUMN grounding JSONB;

ALTER TABLE chat_turns
    ADD COLUMN steps       INTEGER,
    ADD COLUMN judge_calls INTEGER,
    ADD COLUMN rejections  INTEGER,
    ADD COLUMN reviews     JSONB,
    ADD COLUMN chars_read  INTEGER,
    ADD COLUMN grounding   VARCHAR(16);
```

- `chat_turns.reviews`에는 그 턴의 거절 이유(`missing`·`problems`)를 남긴다. 어떤 질문에서 자주 거절되는지 보는 지표다. 다음 턴에는 넘기지 않는다.
- **보유 문서는 `complete_turn` 트랜잭션에서만 넣는다**(`ON CONFLICT DO NOTHING`). 취소·실패한 턴은 보유를 늘리지 않는다.
- `replaces`(마지막 질문 고치기)는 지금처럼 메시지만 지운다. 그 턴이 남긴 보유 문서는 남긴다.
- 요약·큐는 사용자와 무관한 문서 단위 데이터라 `user_id`가 없다. 삭제는 요약 스케줄러가 이벤트로 한다(계정 삭제도 content가 프로젝트마다 `ProjectDeleted`를 낸다). 답변 서버의 `delete_user_data`·`delete_project_data`는 지금처럼 챗 데이터만 지운다.
- 마이그레이션은 답변 서버 컨테이너가 시작할 때 돌린다(지금과 같다). 요약 스케줄러는 Flyway를 돌리지 않고, 테이블이 없으면 기다렸다 다시 시도한다. 배포 순서(§7)로 답변 서버가 먼저 올라간다.

## 5. content 변경

### 5.1 `chat-map`

기존 `manuscripts`·`settings`·`memos` 쿼리에서 본문 대신 길이를 읽는다.

```sql
select d.id, d.title, e.name as episode_name, d.revision_no,
       char_length(d.body_text) as body_chars, d.updated_at
from document d left join episode_folders e on e.id = d.episode_id
where d.project_id = :project and d.folder_id = 4 and d.trashed_at is null
order by (e.id is null), e.rank, e.id, d.rank, d.id
```

- `chars` = 제목 + `body_chars` + 속성 값의 코드 포인트 수. 지금 `sizeOf()`가 문서 하나에 더하는 값과 같다. Postgres `char_length`는 코드 포인트를 센다.
- 속성·관계는 기존 쿼리를 그대로 쓴다. 메모는 본문 대신 `char_length(body)`를 읽는다.

### 5.2 `chat-documents`

```json
요청 { "document_ids": ["…"], "memo_ids": ["…"] }
응답 { "manuscripts": [Manuscript…], "settings": [Setting…], "memos": [Memo…], "missing": ["…"] }
```

- 항목 모양은 `chat-snapshot`의 `Manuscript`·`Setting`·`Memo`와 같다.
- 원고의 `ordinal`은 프로젝트 전체 순서여야 하므로, 전체 순서에 `row_number()`를 매긴 뒤 id로 거른다.
- 한 번에 최대 100개. 넘으면 400 `INVALID_REQUEST`. 없거나 휴지통에 있거나 다른 프로젝트의 id는 `missing`.

### 5.3 백필: `POST /internal/admin/file-events/replay`

- 휴지통에 없는 모든 문서에 대해 `FileChanged(UPDATED)`를 outbox에 다시 쓴다. 프로젝트 하나를 트랜잭션 하나로 묶고, 지금 `ContentEvents`의 규칙대로 소유자 잠금(`lock(userId)`) 뒤에 쓴다.
- 쿼리 파라미터 `after`(project id)와 `limit`(프로젝트 수)으로 나눠 돌린다. 응답은 처리한 마지막 project id다.
- gateway에 노출하지 않는다(ClusterIP 내부 경로). 운영자가 `kubectl port-forward`로 한 번 부른다.
- graph-rag도 이 이벤트를 받는다. 같은 revision은 revision 가드에서 무시하므로 결과는 바뀌지 않고, 처리 부하만 생긴다. 그래서 `limit`으로 나눠 돌린다.

### 5.4 테스트

`ChatApiTest`: 지도에 본문이 없고 `chars`가 `chat-size`의 문서별 합과 맞음, 순서가 스냅샷과 같음, 문서 요청의 원고 `ordinal`이 프로젝트 전체 기준, 다른 프로젝트·휴지통 id는 `missing`, 다른 사용자는 404, 100개 초과는 400.
백필: 모든 활성 문서에 outbox 행이 생김, 휴지통 문서는 빠짐, `after`·`limit`으로 나뉨.

`chat-snapshot`은 ai-chat이 더 이상 부르지 않게 된 뒤 정리 PR에서 지운다.

## 6. gitops · frontend

**gitops**

- `workload/base/ai-chat-summarizer/`: Deployment(replicas 1, 이미지는 ai-chat과 같은 태그, `command: ["python", "-m", "app.summarizer"]`), kustomization. 환경 변수는 §3.4와 DB 연결(ai-chat과 같은 Secret).
- OpenAI 키는 지금 챗 Secret `ai-chat-openai`를 같이 쓴다. 요약 비용을 따로 보려면 전용 Secret을 만들고 그 Deployment의 참조만 바꾼다(`bootstrap/secrets/README.md`).
- CI의 이미지 태그 갱신 Lambda가 ai-chat 이미지 태그를 두 Deployment에 함께 반영하도록 overlay를 맞춘다.
- ai-chat Deployment의 `CHAT_CONTEXT_LIMIT`을 지운다.

**frontend**

- `src/features/chat/waiting.tsx`의 `reading` 문구: "작품 전체를 읽는 중" → "필요한 문서를 읽는 중"(영어 포함).
- 그 밖의 계약은 그대로다. `grounding`은 지금은 쓰지 않는다(본문에 이미 단서가 들어간다). `too-large` 화면은 닿지 않게 되고 정리 PR에서 지운다.

## 7. 배포 순서

| 순서 | 레포 | 내용 | 되돌리기 |
|---|---|---|---|
| 1 | content | `chat-map`, `chat-documents`, 백필 API | 추가만 하므로 영향 없음 |
| 2 | ai-chat | 답변 서버 루프 + V3 마이그레이션 | gitops 이미지 태그 `git revert`. V3는 추가만 하므로 옛 이미지도 동작한다 |
| 3 | ai-chat + gitops | 요약 스케줄러 Deployment, Secret | Deployment 삭제. 요약이 없어도 답변 서버는 동작한다 |
| 4 | 운영 | 백필 API를 나눠 호출 | |
| 5 | frontend | 대기 문구 | |
| 6 | 정리 | content `chat-snapshot`, frontend `too-large`, gitops `CHAT_CONTEXT_LIMIT` 삭제, [AI_CHAT_REQUIREMENTS.md](AI_CHAT_REQUIREMENTS.md) §3.3·§3.4·§3.5 갱신 | |

기능 플래그는 두지 않는다. 옛 경로를 함께 남기면 코드가 두 벌이 된다. 문제가 생기면 gitops의 이미지 태그를 되돌린다.

2단계는 요약 없이도 동작한다. 지도에 요약이 비어 있으면 모델이 제목·순번·관계로 고른다. 그 상태의 지표가 요약의 효과를 재는 기준선이 된다.

## 8. PR 단위

| PR | 레포 | 범위 |
|---|---|---|
| A | content | §5 전체 |
| B | ai-chat | 답변 서버(§2)와 V3의 챗 부분. 커밋은 "content 클라이언트 → 지도·XML → `pack()` → 모델 호출 → 루프 → 내보내기 → `turns.py` 연결 → 저장 → 라우트" 순서 |
| C | ai-chat | 요약 스케줄러(§3)와 V3의 요약 부분 |
| D | gitops | 요약 스케줄러 Deployment, Secret 절차, 설정값 |
| E | frontend | 대기 문구 |
| F | 정리 | §7의 6단계 |

V3를 B와 C에 나눠 넣지 않고 B에서 한 번에 만든다. 마이그레이션을 두 번 내지 않으려는 것이다.

## 9. 열린 점

- **시간:** 단계 수 × medium 호출 + 판정이 180초 안에 드는지는 실측한다. 자주 넘으면 gateway의 ai-chat `request-timeout`을 늘린다.
- **추론 항목:** `store=False`에서 `reasoning.encrypted_content`를 돌려주는 방식이 `gpt-6-luna`에서 동작하는지 구현 첫날 확인한다. 안 되면 단계 사이 추론 없이 도구 호출과 결과만 넘긴다.
- **판정 통과율:** 첫 답이 통과하는 비율을 보고 스트리밍 복원(설계 §10)을 판단한다.
- **처리방침:** 요약 스케줄러가 저장된 원고를 자동으로 OpenAI에 보내는 것은 지금 처리방침과 어긋난다. 이 설계에서는 고려하지 않기로 했고(2026-10-21), 출시 전에 처리방침을 맞춘다.

## 10. 로컬 검증 (2026-10-22)

Docker의 Postgres 18 · Kafka 4.1(KRaft 단일 브로커) 위에 content(`bootRun`), ai-chat 답변 서버(`uvicorn`), 요약 스케줄러(`python -m app.summarizer`)를 띄우고 실제 `gpt-6-luna`로 돌렸다.

**자료.** 원고 24화(3부, 회차당 약 4,000자)와 설정 문서 8개(인물 4, 장소 2, 사물 1, 세계관 1), 관계 5개, 메모 2개. 분량 107,838자. 회차마다 확인할 사실을 하나씩 심었다(7화 첫 각성, 12화 유리 열쇠 발견, 15화 오렌의 거래, 21화 카엘이 문을 엶, 24화 봉인). 단계 한도 40,000자라 한 단계에 다 읽을 수 없다.

**요약 스케줄러**

| 확인 | 결과 |
|---|---|
| 생성 | 이벤트 32건이 큐에 들어가고 15초 디바운스 뒤 두 틱에 걸쳐 처리됨. 원고 24개는 모델 요약(132~302자), 짧은 설정 문서 8개는 본문 그대로 |
| 요약 품질 | 7화 요약에 "무너진 성당의 제단 앞에서 레나가 처음 빛을 끌어내고"가 들어감 |
| 작은 수정 | 한 문장 추가(rev 2) → `unchanged`, 요약은 rev 1 그대로 |
| 큰 수정 | 장소를 바꾼 고쳐 쓰기(rev 3) → 새 요약 rev 3, 바뀐 장소 반영 |
| 백필 | 재발행 API로 32건 → 컨슈머가 모두 받았고, 이미 같은 판이 요약돼 있어 큐에 하나도 들어가지 않음 |
| 휴지통·복원 | 휴지통 → 요약 삭제, 복원 → 다시 요약 |
| 프로젝트 삭제 | `ProjectDeleted` → 그 프로젝트의 요약·큐 모두 삭제 |

**AI 챗**

| 질문 | 결과 |
|---|---|
| 레나가 처음 각성한 곳과 곁에 있던 사람 | 7화 하나만 읽고 정답(무너진 성당 제단, 서윤). 2단계, 판정 2회, `SUPPORTED`, 6.3초. 두 번째 단계에서 캐시 7,592토큰 |
| 유리 열쇠의 처음부터 끝까지(같은 세션) | 12·15·21·24화와 설정 문서를 읽고 순서대로 답함. 원고와 설정을 구분하고 원고가 밝히지 않은 부분도 말함. `SUPPORTED`, 11.4초 |
| 원고에 없는 사실(카엘의 고향) | 충분도 판정이 두 번 거절(`missing` 기록), 최대 단계 뒤 "원고에서 확실한 답을 찾지 못했어"로 시작하는 답. `NOT_ENOUGH`, 28.2초 |
| 드래그 인용 `explain`(15화) | 인용 장면을 12·21화와 설정 문서로 이어 설명. `SUPPORTED`, 22.9초 |
| 7화를 고쳐 쓴 뒤 같은 세션에서 다시 질문 | 새로 읽은 문서 0개. 보유 문서를 현재 판으로 다시 받아 바뀐 장소로 답함("정정할게") |

**DB 정합성.** 완료된 턴 5개 모두 `answer_id`가 있고 답마다 `grounding`이 저장됨. 근거 인용은 모두 원문 대조를 통과(`quote` null 없음). 보유 문서 고아 행 없음. 거절된 턴의 `reviews`에 이유가 남음. 두 프로세스 로그에 오류 없음.

**구현 중 확인한 것.** `store=False`에서 `include=["reasoning.encrypted_content"]`로 받은 출력 항목을 다음 단계 입력에 그대로 넣는 방식이 `gpt-6-luna`에서 동작한다(§9의 확인 항목).

## 11. 평가 후 바뀐 것 (2026-10-22)

[AI_CHAT_EVALUATION.md](AI_CHAT_EVALUATION.md)의 세 데이터셋 평가로 고친 내용: 충분도 판정 규칙(모순, 정밀도, 드래그 질문, 시간순의 공백, 설정만으로는 부족), 답 판정의 인용 문구 규칙, 문서가 충분했을 때의 마지막 답, 탐색·답의 추론 강도 분리, 답 길이 기준, 일시 오류 재시도, 호출별 로그. 평가 도구는 `loresentry-ai-chat/evaluation/`.
