# AI 챗 탐색 구현 설계

> 갱신일: 2026-10-21
> 상태: **구현 설계.** [AI_CHAT_NAVIGATION_DESIGN.md](AI_CHAT_NAVIGATION_DESIGN.md)(무엇을 하는가)를 지금 코드에 어떻게 넣는가를 정한다.
> 독자: ai-chat·content를 구현하는 사람.
> 기준 코드: `loresentry-ai-chat` `41ef19b`, `loresentry-content` `4aeec2e`, `loresentry-gateway` `12fe815`, `loresentry-frontend` `a3f4a86`

## 0. 요약

| 레포 | 바뀌는 것 | 규모 |
|---|---|---|
| **ai-chat** | 매 턴 전체 스냅샷 + 단일 호출을 **에이전트 루프**로 바꾼다. 루프는 프레임워크 없이 직접 짠다(§1). 보유 문서와 요약을 위한 테이블을 더한다 | 큼 |
| **content** | 내부 API 2개 추가: `chat-map`, `chat-documents`. 기존 API는 그대로 | 작음 |
| **frontend** | 대기 문구 하나("작품 전체를 읽는 중" → "필요한 문서를 읽는 중"). 계약은 바뀌지 않는다 | 아주 작음 |
| **gateway** | 바꾸지 않는다. 다만 ai-chat 요청 제한(`request-timeout: 180s`)이 루프 전체의 시간 상한이다(§1.6) | 없음 |

## 1. 에이전트 루프 구성

### 1.1 프레임워크를 쓰지 않는다

| 선택지 | 장점 | 단점 |
|---|---|---|
| **직접 구현** (Responses API + 반복문) | 루프는 100줄 남짓이다. 도구 실행이 서버 안의 함수라 담기 규칙·판정·강제 종료를 그대로 쓴다. 프롬프트 배치(캐시)를 완전히 통제한다. 지금 코드(`OpenAIModel`, `CitationStream`, `TurnRunner`의 취소·마감·지표)를 거의 그대로 재사용한다. 가짜 모델로 결정적으로 테스트할 수 있다 | 재시도, 추적을 직접 만든다 |
| OpenAI Agents SDK | 도구 루프와 추적을 제공한다 | 판정 게이트, 단계별 담기, 마지막 단계 강제를 hook으로 끼워야 한다. 입력 배치를 SDK가 만들어서 캐시 통제가 약해진다. 지금의 SSE와 근거 파서를 SDK 스트림에 다시 이어야 한다 |
| LangGraph | 상태 그래프, 체크포인트 | 노드 두세 개짜리 루프에 비해 무겁다. 요구사항 문서의 "에이전트 프레임워크를 쓰지 않는다" 결정과도 어긋난다 |

**직접 구현한다.** 루프가 단순하고(도구 2개, 종료 조건 2개), 이 설계에서 중요한 부분(문서 순서를 지키는 담기, 답 전에 판정, 캐시를 위한 입력 배치)이 모두 프레임워크 밖의 로직이다. 병렬 도구나 여러 에이전트가 필요해지면 그때 다시 본다.

### 1.2 구성 요소

```text
TurnRunner (turns.py, 기존)          턴의 수명: 취소 감시, 마감, 지표, 저장, SSE
 └ Agent (agent.py, 신규)            에이전트 루프
     ├ model.step()                   응답 모델의 도구 호출 한 번 (스트리밍 없음)
     ├ pack() + content.documents()   request_documents 실행
     ├ model.judge()                  판정 모델 호출
     └ 결과 → Outcome(level, items, missing, read)
 └ model.stream()                     마지막 답 스트리밍 (기존)
 └ CitationStream (citations.py)      근거 파싱·검증 (기존, 대상만 바뀜)
```

### 1.3 모델 호출 세 가지

| 호출 | 용도 | 출력 | 도구 | 추론 강도 | 출력 상한 | 스트리밍 |
|---|---|---|---|---|---|---|
| `step` | 다음 행동 결정 | 도구 호출 1개 | 2개, `tool_choice=required`. 마지막 단계는 `ready_to_answer`로 강제 | `CHAT_STEP_REASONING_EFFORT`(low) | 2,000 | 아니요 |
| `judge` | 충분한지 판정 | `{sufficient, missing}` JSON | 없음. `text.format=json_schema` | low | 1,000 | 아니요 |
| `stream` | 마지막 답 | 본문 + `<cite>` | 선언만 하고 `tool_choice=none` | 기존 `OPENAI_REASONING_EFFORT`(medium) | 기존 | 예 |

- 세 호출 모두 `store=False`, `prompt_cache_key=project_id`(기존과 같다).
- `step`은 `include=["reasoning.encrypted_content"]`로 받고, 응답의 `output` 항목(추론 + `function_call`)을 **그대로** 다음 입력에 덧붙인다. `store=False`에서 단계 사이에 추론을 이어 가는 방법이다.
- 마지막 답 호출에도 앞 단계의 `function_call` 항목이 입력에 있으므로 도구 정의를 함께 보내고 `tool_choice=none`으로 막는다.
- `judge`는 응답 모델과 다른 시스템 지시와 다른 입력을 쓰므로 캐시를 공유하지 않는다. 입력이 작아서(질문 + 대화 + 보유 문서) 괜찮다.

### 1.4 루프

```python
# app/agent.py (요지)
async def run(self, turn: TurnInput, phase: Callable[[str], None]) -> Outcome:
    items = turn.base_input()                    # 지도, 보유 문서, 대화, 이번 질문
    read: list[Material] = []                    # 이번 턴에 새로 읽은 것
    missing: list[str] = []
    for step in range(1, self.settings.max_steps + 1):
        if self.clock.remaining() < self.settings.final_reserve_seconds:
            break                                # 마지막 답 시간을 남긴다 (§1.6)
        force = step == self.settings.max_steps
        result = await self.model.step(NAVIGATE_INSTRUCTIONS, items, TOOLS, force)
        items += result.items
        call = result.call
        if call.name == "request_documents":
            picked = pack(call.ranking, turn.nodes, turn.held_ids, self.settings.step_max_chars)
            docs = await self.content.documents(turn.user_id, turn.project_id, picked.ids)
            read += docs
            turn.hold(docs)
            items.append(tool_output(call.id, context_xml(docs, picked)))
        else:                                    # ready_to_answer
            verdict = await self.model.judge(JUDGE_INSTRUCTIONS, judge_input(turn))
            if verdict.sufficient:
                items.append(tool_output(call.id, "<check sufficient=\"true\"/>"))
                return Outcome("SUPPORTED", items, [], read)
            missing = verdict.missing
            items.append(tool_output(call.id, check_xml(missing)))
    return Outcome("NOT_ENOUGH", items, missing, read)
```

- **`pack()`는 순수 함수**다. 순위, 노드의 `chars`, 보유 목록, 한도를 받아 읽을 id·건너뛴 id·모르는 id를 돌려준다. 담기 규칙(설계 §5)이 여기 다 있고 단위 테스트한다.
- 판정 입력은 `turn`에서 만든다. 질문, 직전 대화, **이번 턴까지의 보유 문서 본문**이다. 응답 모델의 `items`는 넣지 않는다.
- 루프가 끝나면 `TurnRunner`가 `items`에 `<final level=…>` user 메시지를 붙이고 `model.stream()`을 부른다. 그 뒤는 지금 코드와 같다(근거 파싱 → 저장 → `done`).

### 1.5 오류와 이상 동작

| 상황 | 처리 |
|---|---|
| 모델이 지도에 없는 id만 요청 | `pack()`이 `<unknown/>`로 알리고 단계를 소비한다 |
| 순위가 모두 보유 문서 | 읽을 게 없다고 알린다. 같은 일이 연속 두 번이면 다음 단계를 `ready_to_answer`로 강제한다 |
| 도구 인자가 스키마에 어긋남 | `strict: true`라 거의 없다. 생기면 `MODEL_INVALID_TOOL_CALL`로 턴 실패 |
| 판정 호출 실패 | 그 턴은 `NOT_ENOUGH`로 마감한다(답은 한다) |
| `step`·`stream` 호출 실패 | 지금과 같은 `ModelFailure` → `CHAT_MODEL_UNAVAILABLE` |
| content 실패 | 지금과 같은 `CONTENT_UNAVAILABLE` |
| 취소 | 지금 감시자(`watch`)가 태스크를 취소한다. 루프 어디서든 `CancelledError`로 빠진다. **보유 문서는 턴이 완료될 때만 저장하므로**(§2.6) 취소·실패한 턴은 아무것도 남기지 않는다 |

### 1.6 시간 예산

gateway가 ai-chat 요청 전체를 180초에서 끊는다(`application.yaml`의 `request-timeout: 180s`). 지금 `CHAT_TURN_DEADLINE_SECONDS`가 170초인 이유다. 루프도 그 안에 끝나야 한다.

| 구간 | 예상 |
|---|---|
| 지도·보유 문서 받기 | 1초 안팎 |
| `step` × 최대 4 (low) | 단계당 수 초~10여 초 |
| `judge` × 1~2 (low) | 수 초 |
| 마지막 답 (medium, 스트리밍) | 지금의 단일 호출과 비슷. 수십 초 |

- **마지막 답 몫을 먼저 떼어 둔다.** 남은 시간이 `CHAT_FINAL_RESERVE_SECONDS`(90초)보다 적으면 루프를 멈추고 그때까지의 결과로 답한다. 충분 판정을 받은 적이 없으면 `NOT_ENOUGH`다.
- 지표(`chat_turns`)에 단계 수와 전체 시간을 남긴다. 180초에 자주 닿으면 gateway 제한을 늘리거나 단계 수를 줄인다. gateway는 15초 heartbeat가 있어 중간 끊김(ALB 60초)은 걱정하지 않아도 된다.

## 2. ai-chat 변경

### 2.1 모듈별

| 파일 | 처리 | 내용 |
|---|---|---|
| `app/agent.py` | **신규** | 루프(§1.4), `pack()`, `Outcome`, 도구 정의 |
| `app/navigation.py` | **신규** | 지도·보유 문서·결과 XML, 짧은 id, 판정 입력, `NAVIGATE_INSTRUCTIONS`·`JUDGE_INSTRUCTIONS`·`<final>` 문구 |
| `app/summaries.py` | **신규** | 요약 작업 큐와 작업자(§2.7) |
| `app/content.py` | 수정 | `Node`·`ChatMap` 파싱, `ContentSource.map()`·`documents()` 추가. `snapshot()`은 지운다 |
| `app/prompt.py` | 수정 | `build_prompt`·`Aliases`·최근 회차 분리를 지운다. `document_xml`·`question_xml`·`history_answer`·`select_history`·`TASKS`·`Material`은 남겨 `navigation.py`가 쓴다. `materials()`를 스냅샷 대신 문서 목록을 받게 바꾼다 |
| `app/model.py` | 수정 | `AnswerModel`에 `step()`·`judge()`·`summarize()`를 더한다. `OpenAIModel`의 오류 변환은 공용 함수로 뺀다. `stream()`은 `Prompt` 대신 `(instructions, items, tools)`를 받는다 |
| `app/turns.py` | 수정 | `run()`의 "스냅샷 → `build_prompt` → `stream`"을 "턴 입력 만들기 → `Agent.run` → `<final>` → `stream`"으로 바꾼다. 취소·마감·지표·저장은 그대로 |
| `app/citations.py` | 그대로 | `CitationStream(materials)`에 넘기는 대상이 "보유 문서 + 이번 턴에 읽은 문서"로 바뀔 뿐이다. 지도에서만 본 노드는 `materials`에 없으니 지금 코드대로 근거에서 빠진다 |
| `app/store.py` | 수정 | 보유 문서 읽기·저장·정리, 요약 읽기·저장, `complete_turn`에 `grounding`·보유 문서·새 지표, `message_json`에 `grounding` |
| `app/routes.py` | 수정 | `send`의 분량 초과 검사를 지운다. `availability`는 항상 `available`(응답 모양은 유지) |
| `app/settings.py` | 수정 | §2.8 |
| `app/main.py` | 수정 | `Summarizer`를 lifespan에서 시작·정리 |
| `db/migration/V3__…sql` | **신규** | §2.6 |
| `tests/` | 수정·추가 | §2.9 |

### 2.2 턴 입력 만들기 (`turns.py`)

```text
1. phase gathering
2. map   = content.map(user, project)                     지도 (본문 없음)
3. held  = store.held(session) → content.documents(...)    보유 문서를 현재 판으로
           합이 CHAT_HELD_MAX_CHARS를 넘으면 먼저 읽은 것부터 빼고 DB에서도 지움
           content가 missing으로 돌려준 것(삭제됨)도 DB에서 지움
4. sums  = store.summaries(map의 문서 id) → 노드에 붙임
           summarizer.schedule(낡은 것)                     턴은 기다리지 않음
5. history = store.history(...) → select_history(…, CHAT_HISTORY_MAX_CHARS)
6. base input:
     user [ <project/><author/><map/>  ← cache breakpoint
            <held/>                      ← cache breakpoint ]
     이전 턴들 (질문 / 답)
     user <turn/>
7. phase reading → Agent.run()
8. <final level> 덧붙임 → stream() → 첫 토큰에서 phase writing
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

content의 id는 uuidv7이라 앞쪽은 생성 시각이다. 끝쪽은 무작위라 8자리면 거의 겹치지 않는다. 지도 전체(문서 + 메모)에 대해 한 번에 계산하므로 같은 자료면 같은 결과가 나온다.

### 2.4 `model.py`

```python
class AnswerModel(Protocol):
    name: str
    async def step(self, instructions, items, tools, force: bool) -> StepResult: ...
    async def judge(self, instructions, items) -> Verdict: ...
    async def summarize(self, instructions, text) -> str: ...
    def stream(self, instructions, items, tools, cache_key) -> AsyncIterator[TextDelta | Finished]: ...

@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str            # request_documents / ready_to_answer
    ranking: list[str]   # ready_to_answer면 빈 목록

@dataclass(frozen=True)
class StepResult:
    call: ToolCall
    items: list[dict]    # response.output 그대로
    usage: Usage | None

@dataclass(frozen=True)
class Verdict:
    sufficient: bool
    missing: list[str]
    usage: Usage | None
```

- 판정 모델 이름은 `CHAT_JUDGE_MODEL`(기본은 `OPENAI_MODEL`)이다. `OpenAIModel`이 `judge_name`을 따로 갖는다.
- 사용량은 `TurnRunner`가 호출마다 더해 `Metrics`에 남긴다. 지금 `with_usage()`가 덮어쓰는 것을 합산으로 바꾼다.

### 2.5 `routes.py`

- `send`: `size > context_limit`이면 422를 내던 검사를 지운다. 큰 작품도 답한다.
- `availability`: 항상 `{"status": "available", "size": size, "limit": size}`. 프론트의 `too-large` 화면은 닿지 않게 되고, 프론트 정리 때 함께 지운다. 응답 모양을 유지해 프론트를 먼저 바꾸지 않아도 된다.
- `create_session`의 `content.size()` 호출(프로젝트 접근 확인)은 그대로 둔다.

### 2.6 DB (`V3__navigation.sql`)

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
    document_id UUID         NOT NULL,
    project_id  UUID         NOT NULL,
    user_id     UUID         NOT NULL,
    revision_no BIGINT       NOT NULL,
    summary     TEXT         NOT NULL,
    model       VARCHAR(100),
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT pk_document_summaries PRIMARY KEY (document_id)
);
CREATE INDEX ix_document_summaries_project ON document_summaries (project_id);
CREATE INDEX ix_document_summaries_user ON document_summaries (user_id);

ALTER TABLE chat_messages ADD COLUMN grounding JSONB;

ALTER TABLE chat_turns
    ADD COLUMN steps       INTEGER,
    ADD COLUMN judge_calls INTEGER,
    ADD COLUMN chars_read  INTEGER,
    ADD COLUMN grounding   VARCHAR(16);
```

- **보유 문서는 `complete_turn` 트랜잭션에서만 넣는다.** 이번 턴에 읽은 것을 `ON CONFLICT DO NOTHING`으로 넣는다. 취소·실패한 턴은 보유를 늘리지 않으므로 따로 되돌릴 일이 없다.
- `replaces`(마지막 질문 고치기)는 지금처럼 메시지만 지운다. 그 턴이 남긴 보유 문서는 남겨 둔다. 다음 질문에도 쓸모 있을 가능성이 높고, 해롭지 않다.
- 요약 삭제: `delete_project_data`는 `project_id`로, `delete_user_data`는 `user_id`로 `document_summaries`도 지운다. 지도에 없는 문서(삭제됨)의 요약은 지도를 만들 때 지운다.
- `chat_turns.context_chars`는 이제 지도 + 보유 문서 글자 수를 남긴다.

### 2.7 요약 (`summaries.py`)

```text
Summarizer
  queue: asyncio.Queue[(user_id, project_id, document_id)]   중복은 set으로 거름
  worker 1개 (lifespan에서 시작, 종료 때 취소)
  처리: content.documents([id]) → model.summarize() → store.save_summary()
        저장할 때 더 새 revision의 요약이 이미 있으면 버림
schedule 조건: summary_rev != rev 이고 updated_at이 SUMMARY_IDLE_MINUTES보다 오래됨
```

- replica가 1개라 프로세스 안 큐로 충분하다. 재시작하면 큐는 사라지지만, 다음 턴의 지도에서 다시 찾아 넣는다.
- 요약 호출은 사용자 턴과 같은 OpenAI 키를 쓴다. 지표는 로그로만 남긴다(`summary finished`, 문서 id, 토큰).

### 2.8 설정값

| 이름 | 기본 | 비고 |
|---|---|---|
| `CHAT_STEP_MAX_CHARS` | 40,000 | 신규 |
| `CHAT_MAX_STEPS` | 4 | 신규 |
| `CHAT_HELD_MAX_CHARS` | 200,000 | 신규 |
| `CHAT_HISTORY_MAX_CHARS` | 40,000 | 신규. 지금은 "한도 − 스냅샷 크기"로 계산하던 대화 예산 |
| `CHAT_STEP_REASONING_EFFORT` | `low` | 신규. `step`·`judge` 공통 |
| `CHAT_JUDGE_MODEL` | `OPENAI_MODEL` | 신규 |
| `CHAT_FINAL_RESERVE_SECONDS` | 90 | 신규 |
| `SUMMARY_IDLE_MINUTES` | 10 | 신규 |
| `CHAT_CONTEXT_LIMIT`, `CHAT_RECENT_MANUSCRIPTS`, `CHAT_HISTORY_FLOOR` | — | **삭제**. gitops `deployment.yaml`의 `CHAT_CONTEXT_LIMIT`도 지운다 |

### 2.9 테스트

지금 테스트 구조(가짜 content, 가짜 모델, Testcontainers Postgres)를 그대로 쓴다. 가짜 모델은 **각본**을 받는다: "1단계에 `request_documents([A, B])`, 판정은 부족(`missing=[…]`), 2단계에 `request_documents([C])`, 판정은 충분, 답은 '…'".

| 테스트 | 확인하는 것 |
|---|---|
| `pack()` 단위 | 순서 유지, 한도 안 채우기, 1순위 큰 문서 예외, 보유·모르는 id 처리 |
| 짧은 id 단위 | 겹치면 늘어남, 같은 입력에 같은 결과 |
| 지도 XML 단위 | 같은 자료면 같은 바이트 |
| 루프: 바로 충분 | `SUPPORTED`, 읽은 문서가 보유로 저장됨, `grounding` 저장 |
| 루프: 부족 → 다시 찾음 → 충분 | 판정의 `missing`이 다음 `step` 입력에 들어감 |
| 루프: 끝까지 부족 | 마지막 단계가 `ready_to_answer`로 강제됨, `NOT_ENOUGH`와 `<final>` 문구 |
| 판정 입력 | 응답 모델의 도구 호출·추론이 들어가지 않음 |
| 보유 문서 갱신 | 다음 턴에 현재 판으로 다시 들어감, 삭제된 문서는 빠짐, 한도 넘으면 오래된 것부터 빠짐 |
| 근거 | 지도에서만 본 노드를 인용하면 근거에서 빠짐 |
| 시간 예산 | 남은 시간이 예비분보다 적으면 루프를 멈추고 답함 |
| 취소 | 루프 중 취소하면 답·보유 문서가 저장되지 않음 |
| 기존 API 테스트 | 분량 초과 거절 테스트 2개는 "큰 작품도 답한다"로 바꾼다. 나머지는 가짜 모델 각본만 바꿔 유지 |

## 3. content 변경

### 3.1 `chat` 패키지

| 파일 | 추가 |
|---|---|
| `ChatController` | `GET /internal/projects/{id}/chat-map`, `POST /internal/projects/{id}/chat-documents` |
| `ChatService` | `map()`, `documents()`. 둘 다 `readOnly`, `REPEATABLE_READ`(기존과 같다) |
| `ChatRepository` | 아래 쿼리 |
| `ChatResponses` | `MapManuscript`, `MapSetting`, `MapMemo`, `ChatMap`, `DocumentsRequest`, `Documents` |

### 3.2 `chat-map`

기존 `manuscripts`·`settings`·`memos` 쿼리에서 본문 대신 길이를 읽는다.

```sql
select d.id, d.title, e.name as episode_name, d.revision_no,
       char_length(d.body_text) as body_chars, d.updated_at
from document d left join episode_folders e on e.id = d.episode_id
where d.project_id = :project and d.folder_id = 4 and d.trashed_at is null
order by (e.id is null), e.rank, e.id, d.rank, d.id
```

- `chars` = 제목 + `body_chars` + 속성 값의 코드 포인트 수. 지금 `sizeOf()`가 문서 하나에 더하는 값과 같다. Postgres `char_length`는 코드 포인트를 센다.
- 속성(`properties`)과 관계(`relations`)는 기존 쿼리를 그대로 쓴다. 메모는 본문 대신 `char_length(body)`를 읽는다.

### 3.3 `chat-documents`

```json
요청 { "document_ids": ["…"], "memo_ids": ["…"] }
응답 { "manuscripts": [Manuscript…], "settings": [Setting…], "memos": [Memo…], "missing": ["…"] }
```

- 항목 모양은 `chat-snapshot`의 `Manuscript`·`Setting`·`Memo`를 그대로 쓴다.
- 원고의 `ordinal`은 프로젝트 전체 순서여야 하므로, 전체 순서에 `row_number()`를 매긴 뒤 id로 거른다.

```sql
select * from (
  select d.id, …, row_number() over (order by (e.id is null), e.rank, e.id, d.rank, d.id) as ordinal
  from document d left join episode_folders e on e.id = d.episode_id
  where d.project_id = :project and d.folder_id = 4 and d.trashed_at is null
) m where m.id = any(:ids)
```

- 한 번에 최대 100개. 넘으면 400 `INVALID_REQUEST`. 없거나 휴지통에 있거나 다른 프로젝트의 id는 `missing`에 넣는다.

### 3.4 테스트

`ChatApiTest`에 더한다: 지도에 본문이 없고 `chars`가 `chat-size`의 문서별 합과 맞음, 순서가 스냅샷과 같음, 문서 요청이 원고 `ordinal`을 프로젝트 전체 기준으로 줌, 다른 프로젝트·휴지통 id는 `missing`, 다른 사용자는 404, 100개 초과는 400.

`chat-snapshot`은 ai-chat이 더 이상 부르지 않게 된 뒤 정리 PR에서 지운다.

## 4. frontend

- `src/features/chat/waiting.tsx`의 `reading` 문구: "작품 전체를 읽는 중" → "필요한 문서를 읽는 중"(영어 번역 포함).
- 그 밖의 계약은 바뀌지 않는다. `phase`는 지금의 `gathering`·`reading`·`writing`만 쓴다. 답 메시지의 `grounding`은 지금은 쓰지 않는다(본문에 이미 단서가 들어간다).
- `too-large` 화면과 `availability` 호출은 닿지 않게 된다. 정리 PR에서 지운다.

## 5. 배포 순서

| 순서 | 레포 | 내용 | 되돌리기 |
|---|---|---|---|
| 1 | content | `chat-map`, `chat-documents` 추가 | 추가만 하므로 영향 없음 |
| 2 | ai-chat | 루프, V3 마이그레이션, 설정값. 요약 작업 없이 | gitops 이미지 태그 `git revert`. V3는 추가만 하므로 옛 이미지도 동작한다 |
| 3 | ai-chat | 요약 작업 | 같음 |
| 4 | frontend | 대기 문구 | |
| 5 | 정리 | content `chat-snapshot` 삭제, frontend `too-large` 삭제, gitops `CHAT_CONTEXT_LIMIT` 삭제, [AI_CHAT_REQUIREMENTS.md](AI_CHAT_REQUIREMENTS.md) §3.3·§3.4 갱신 | |

기능 플래그는 두지 않는다. 옛 경로를 함께 남기면 코드가 두 벌이 된다. 문제가 생기면 gitops의 이미지 태그를 되돌린다.

2단계는 요약 없이도 동작한다. 지도에 요약이 비어 있으면 모델이 제목·순번·관계로 고른다. 그 상태의 지표가 요약의 효과를 재는 기준선이 된다.

## 6. PR 단위

| PR | 레포 | 범위 |
|---|---|---|
| A | content | §3 전체 |
| B | ai-chat | `content.py`, `navigation.py`, `agent.py`, `model.py`, `turns.py`, `prompt.py`, `store.py`, `routes.py`, `settings.py`, V3, 테스트. **요약 제외** |
| C | ai-chat | `summaries.py`, `main.py`, 요약 저장·삭제, 테스트 |
| D | frontend | 대기 문구 |
| E | 정리 | §5의 5단계 |

B가 가장 크다. 리뷰를 쉽게 하려면 커밋을 "content 클라이언트 → 지도·XML → `pack()` → 모델 호출 → 루프 → `turns.py` 연결 → 저장 → 라우트" 순서로 나눈다.

## 7. 열린 점

- **시간:** 4단계 + 판정 + 답이 180초 안에 드는지는 실측해야 한다. 자주 넘으면 gateway `request-timeout`을 늘린다(ai-chat 클라이언트만).
- **`step` 추론 강도:** low로 순서 품질이 충분한지 본다. 부족하면 medium으로 올리고 단계 수를 줄인다.
- **판정 모델:** 같은 모델로 시작한다. 비용이 크면 작은 모델로 바꾼다.
- **추론 항목:** `store=False`에서 `reasoning.encrypted_content`를 돌려주는 방식이 `gpt-6-luna`에서 동작하는지 구현 첫날 확인한다. 안 되면 단계 사이 추론 없이 도구 호출과 결과만 넘긴다.
