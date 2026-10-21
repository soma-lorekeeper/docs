# AI 챗 탐색 설계 — 지도를 보고 필요한 문서부터 읽기

> 갱신일: 2026-10-21
> 상태: **설계.** 구현 전. 핵심 로직만 정한다. 세부는 필요할 때 고도화한다(§8).
> 독자: ai-chat·content를 구현하는 사람.
> 관련: [AI_CHAT_REQUIREMENTS.md](AI_CHAT_REQUIREMENTS.md). 이 문서의 방식이 그 문서 §3.3(매 턴 전체 스냅샷)과 §3.4(분량 초과면 챗을 열지 않음)를 대체한다. 나머지 계약(세션, 스트리밍, 근거 형식, 권한, 삭제)은 그대로다.

## 0. 요약

작품 크기와 상관없이 같은 방식으로 답한다. 모델은 먼저 **지도**(모든 문서의 노드·관계·요약)를 보고, **필요하다고 생각하는 순서대로** 문서를 요청한다. 서버는 한 단계에서 정해진 글자 수만큼 그 순서대로 읽어 준다. 모델이 답할 준비가 됐다고 하면 별도 판정 모델이 충분한지 본다. 끝까지 충분하지 않으면 그 사실을 답에 밝힌다.

```text
질문
 └ 지도 + 보유 문서 + 대화 기록 + 질문
    └ 반복 (최대 CHAT_MAX_STEPS)
       ├ request_documents(순위) → 서버가 순서대로 CHAT_STEP_MAX_CHARS까지 읽어 줌
       └ ready_to_answer()       → 판정 모델: { sufficient, missing }
            충분 → 답
            부족 → missing을 알려 주고 계속
    └ 최대 단계 도달 → "충분한 원고 내용을 찾지 못했다"로 시작해 답
```

| 항목 | 결정 |
|---|---|
| 적용 범위 | 모든 프로젝트. 작은 작품은 한두 단계에 다 읽고 끝난다 |
| 문서 순서 | **답변 모델이 정한다.** 매 단계 처음부터 다시 정렬한다 |
| 비용 상한 | 단계당 최대 글자 수 `CHAT_STEP_MAX_CHARS` 하나. 질문당 상한은 단계당 상한 × 최대 단계 수다 |
| 종료 | 판정 모델이 충분하다고 할 때, 또는 최대 단계 도달 |
| 답의 단서 | `SUPPORTED`(그대로) / `NOT_ENOUGH`(충분한 원고 내용을 찾지 못함) |
| 문서 편집 | 턴마다 보유 문서를 **현재 판으로 다시 넣는다.** 편집된 문서가 저절로 최신이 된다 |
| 관계 출처 | content의 `document_relations` |

## 1. 지도 (사전 컨텍스트)

턴마다 content에서 받아 만든다. 본문은 없다.

| 노드 필드 | 뜻 |
|---|---|
| `id` | 문서 UUID의 **끝** 8자리(겹치면 늘린다). content의 id는 uuidv7이라 앞자리는 생성 시각이어서 잘 겹친다 |
| `kind` | `manuscript`·`character`·`place`·… ·`memo` |
| `title` | 제목 |
| `number`, `episode` | 원고의 읽는 순서와 에피소드 |
| `chars` | 읽어 오는 비용. 본문 + 텍스트 속성의 글자 수(코드 포인트). 한국어는 UTF-8 바이트 수가 글자 수의 약 3배라 글자 수로 센다 |
| `rev` | 현재 revision |
| `<summary>`, `summary_rev` | 요약과 그 요약을 만든 판(§5). 메모 노드에는 없다 |

간선은 `<edge a="…" b="…">관계 설명</edge>`다.

```xml
<map>
<node id="5f0c9a21" kind="manuscript" number="12" episode="Episode 4. 새 원고" title="12화 · 균열의 밤" chars="9105" rev="25" summary_rev="20">
<summary>레나가 무너진 성당에서 처음으로 빛을 끌어낸다. … 인물: 레나 아르벨, 오렌 / 장소: 무너진 성당</summary>
</node>
<node id="a83e11d7" kind="character" title="레나 아르벨" chars="3120" rev="7" summary_rev="7">
<summary>…</summary>
</node>
<node id="c2b9047e" kind="memo" title="레나 각성 시점" chars="420"/>
<edge a="5f0c9a21" b="a83e11d7">처음 각성한 곳</edge>
</map>
```

순서는 지금 스냅샷 규칙(원고 → 설정 → 메모, 각각 정해진 순서)을 따른다. 같은 자료면 같은 바이트가 나와야 프롬프트 캐시가 맞는다.

## 2. 턴 루프

```text
held = 이 세션의 보유 문서 (현재 판으로 다시 읽음)
for step in 1..CHAT_MAX_STEPS:
    모델 호출 (도구 필수, 마지막 단계는 ready_to_answer 강제)

    request_documents(ranking):
        cap = CHAT_STEP_MAX_CHARS
        for id in ranking:
            if id in held:            건너뜀
            if chars(id) ≤ cap:       읽음, cap -= chars(id)
            elif 이번 단계에 읽은 게 없음: 이 문서 하나만 읽고 끝냄   (큰 문서 예외)
            else:                     건너뜀 (다음 단계에 다시 요청 가능)
        읽은 문서를 held에 넣고 도구 결과로 돌려줌

    ready_to_answer():
        판정 모델 호출 (§3) → { sufficient, missing }
        충분 → 종료(SUPPORTED)
        부족 → missing을 도구 결과로 돌려줌

최대 단계 도달 → 종료(NOT_ENOUGH, 마지막 판정의 missing을 씀)
종료 → <final> 지시를 붙이고 도구 없이 답을 스트리밍
```

- **순서대로 보면서 들어가는 것만 읽는다.** 1순위는 언제나 읽힌다. 남는 글자 수는 뒤 순위의 작은 문서로 채운다.
- 한 턴 안에서는 도구 호출과 결과가 계속 덧붙으므로, 단계마다 앞부분의 캐시가 맞는다.

## 3. 종료와 답의 단서

응답 모델이 `ready_to_answer`를 부르면 **별도 판정 모델**을 한 번 호출한다. 응답 모델이 스스로 "충분하다"고 하는 말은 믿지 않는다. 모델은 자료가 부족해도 그럴듯하게 지어내는 경향이 있다.

- **판정 모델에 주는 것:** 질문, 직전 대화(질문과 답 본문), 지금까지 읽은 문서 본문. 지도와 요약은 주지 않는다. 요약은 근거가 아니기 때문이다.
- **판정 모델이 내는 것:**

```json
{ "sufficient": false, "missing": ["그것이 레나의 첫 각성이라는 근거"] }
```

- **충분의 정의**는 Sufficient Context(ICLR 2025)를 따른다. 읽은 문서만으로 그럴듯한 답을 확정할 수 있으면 충분하다. 답이 맞는지가 아니라 근거가 있는지를 본다. 정답을 몰라도 판정할 수 있다.
- `missing`은 부족할 때 응답 모델에게 돌려준다. 응답 모델은 이를 보고 다음에 읽을 문서를 다시 정렬한다.

| 수준 | 조건 | 답 |
|---|---|---|
| `SUPPORTED` | 판정이 충분 | 그대로 답한다 |
| `NOT_ENOUGH` | 최대 단계까지 충분하지 않음 | **"원고에서 충분한 내용을 찾지 못했다"로 시작한다.** 읽은 문서가 보여 주는 것만 답하고, `missing`을 말한다. 추정하지 않는다 |

해석이 여러 갈래인 경우는 따로 수준을 두지 않는다. 응답 모델의 지시에 "해석이 갈리면 그렇다고 말하라"를 넣는다(§6.1).

수준은 답 메시지에 `grounding`으로 저장하고 `done`으로 내려보낸다.

## 4. 세션 구성

턴마다 프롬프트를 다시 만든다.

```text
instructions  시스템 지시 (§6.1, 고정)
input
  user       <project/> <author/> <map>…</map>
  user       <held> 보유 문서 전문 (처음 읽은 순서대로, 현재 판) </held>
  user/asst  이전 턴들의 질문과 답 본문
  user       <turn> <viewing/> <quote/> <task/> <question/> </turn>
  … 이번 턴의 도구 호출과 결과가 덧붙음
```

- **보유 문서**는 이 세션에서 읽은 문서다. 턴마다 content에서 **현재 판**으로 다시 받는다. 그래서 편집된 문서는 저절로 최신이 되고, 옛 판으로 답할 일이 없다. 따로 변경을 감지할 필요가 없다.
- 보유 문서의 합이 `CHAT_HELD_MAX_CHARS`를 넘으면 가장 먼저 읽은 것부터 뺀다. 빠진 문서는 필요하면 다시 요청하면 된다.
- 지도와 보유 문서는 턴 사이에 거의 바뀌지 않으므로 앞부분이 대부분 캐시에 맞는다. 문서가 편집되면 그 위치부터 캐시가 깨지는데, 지금은 감수한다.
- 이전 턴은 지금처럼 질문과 답 본문만 넣는다.

## 5. 요약

- 대상은 문서(원고·설정)다. 메모에는 만들지 않는다.
- 요약은 **무엇을 읽을지 고르는 데만** 쓴다. 답은 언제나 읽어 온 본문으로 한다. 그래서 조금 낡아도 된다.
- **규칙 하나:** `summary_rev`가 현재 `rev`와 다르고, 마지막 수정 뒤 `SUMMARY_IDLE_MINUTES`가 지났으면 백그라운드에서 다시 만든다. 지도를 만들 때 대상을 찾아 작업에 넣고, 턴은 기다리지 않는다.
- 요약이 아직 없으면 노드에 요약 없이 내보낸다. 모델은 제목과 관계로 고른다.

## 6. 프롬프트

영어로 쓴다(지금 프롬프트와 같다). 답은 질문의 언어로 한다. `# Format`과 `# Sources`는 지금 `INSTRUCTIONS`(`loresentry-ai-chat/app/prompt.py`)를 그대로 쓴다.

### 6.1 시스템 지시

```text
You are the story assistant inside Lore Sentry, a writing tool for web novel authors. You answer the author's questions about their own work.

# Materials
- <map>: every document and memo in the project. Each <node> has an id, kind, title, size in characters (`chars`) and revision (`rev`). `number` is a chapter's reading order; `episode` is its arc. <summary> was written from revision `summary_rev`; it helps you choose what to read and is never evidence. <edge> is a link the author made between two documents, with the author's description.
- <held>: full documents you have already read in this conversation, at their current revision.
- Memos are the author's working notes: plans and reminders, not facts of the story. When you rely on a memo, say so, and never present it as something that happens in the manuscript.

# Finding what you need
At each step, call one tool.
- request_documents(ranking): the documents to read next, most necessary first. The server reads them in your order until this step's size limit is reached; the rest can be requested at a later step. Re-rank from scratch at every step using what you have read. Do not list held documents.
- ready_to_answer(): when you think the documents you hold are enough. A reviewer checks them. If it reports missing facts, look for those next.
Rank by how likely a document is to contain the facts the answer still needs. Start from the quoted passage, the document the author is viewing, and nodes whose title or summary match the question. Follow edges from documents that turned out useful.

# How to answer
- Answer only from documents you have read. Never from summaries or general knowledge. If they do not contain the answer, say so and say what is missing. Do not invent events, traits or settings.
- Distinguish what the manuscript shows from what setting documents or memos say. Point out contradictions.
- If the documents support more than one reading, say so and give each reading with what it rests on.
- You cannot edit documents. You may offer wording, but the author has to apply it.
- Answer in the language of the author's question; if unclear, use the author's locale.
- Lead with the answer, then the supporting points. Prefer chapter titles and character names.
- Follow the <final> instruction on how to frame the answer.

# Format
(지금과 같다)

# Sources
(지금과 같다. 다음을 더한다)
- Cite only documents and memos you have read. Never cite a node you only saw in the map.
```

### 6.2 도구

```json
[
  { "type": "function", "name": "request_documents", "strict": true,
    "parameters": { "type": "object", "additionalProperties": false,
      "required": ["ranking"],
      "properties": {
        "ranking": { "type": "array", "items": { "type": "string" }, "minItems": 1, "maxItems": 20 } } } },
  { "type": "function", "name": "ready_to_answer", "strict": true,
    "parameters": { "type": "object", "additionalProperties": false, "properties": {} } }
]
```

### 6.3 판정 모델 (별도 호출)

시스템 지시:

```text
You review whether a set of documents from a web novel project is enough to answer the author's question.
The documents are enough if a definite, plausible answer can be built from them alone, without outside knowledge and without treating the author's memos as story facts. The answer does not have to be right, only supported by the documents. Every step of a multi-step answer must appear in the documents. If several answers are possible, the documents must give a reason to choose one.
If they are not enough, list what is missing as short facts to look for.

Example
Question: 레나가 처음 각성한 곳은 어디야?
Documents say "레나는 무너진 성당의 제단 앞에서 처음으로 빛을 끌어냈다." → {"sufficient": true, "missing": []}
Documents only say "성당에서의 각성 이후 레나는 달라졌다." → {"sufficient": false, "missing": ["그것이 레나의 첫 각성이라는 근거"]}
```

입력은 `<conversation>`(직전 대화), `<documents>`(읽은 문서 본문), `<question>`이다. 출력은 JSON 스키마로 받는다.

```json
{ "type": "object", "additionalProperties": false, "required": ["sufficient", "missing"],
  "properties": { "sufficient": { "type": "boolean" },
                  "missing": { "type": "array", "items": { "type": "string" }, "maxItems": 5 } } }
```

### 6.4 마지막 답 지시

```text
<final level="SUPPORTED">Answer the question now.</final>
<final level="NOT_ENOUGH">Answer now. Begin by saying you could not find enough in the manuscript. Answer only what the documents you read show, and name what is missing: {missing}. Do not guess.</final>
```

### 6.5 요약 생성

```text
Summarize this document from a web novel project so that someone can decide, without reading it, whether it answers a question about the story.
Write in the language of the document. 2 to 4 sentences on what it says or what happens, then the names of people, places and things that appear. Use names exactly as written. No evaluation, no guesses. At most 500 characters.
```

## 7. 필요한 것

**content 내부 API** (권한·404 규칙은 기존 내부 API와 같다)

- `GET /internal/projects/{id}/chat-map`: 지금 `chat-snapshot`에서 본문을 빼고 문서마다 `chars`를 더한 것. 관계와 메모 목록(본문 없이)을 포함한다.
- `POST /internal/projects/{id}/chat-documents` `{"ids": [...]}`: 고른 문서·메모의 본문. 모양은 `chat-snapshot`의 항목과 같다.

**ai_chat DB**

```text
chat_held_documents   session_id, document_id, first_read_at        PRIMARY KEY(session_id, document_id)
document_summaries    document_id, revision_no, summary, created_at  PRIMARY KEY(document_id)
chat_messages         + grounding JSONB   {"level": "...", "documents_read": n, "missing": [...]}
chat_turns            + steps, chars_read, grounding
```

요약은 원고에서 나온 글이라 프로젝트·계정 삭제 때 기존 삭제 경로에서 함께 지운다.

**설정값**

| 이름 | 초깃값 |
|---|---|
| `CHAT_STEP_MAX_CHARS` | 40,000 |
| `CHAT_MAX_STEPS` | 4 |
| `CHAT_HELD_MAX_CHARS` | 200,000 |
| `CHAT_JUDGE_MODEL` | 답변 모델과 같음 |
| `SUMMARY_IDLE_MINUTES` | 10 |

**SSE:** `phase`에 `{"phase":"navigating","step":n,"reading":[{"file_id","title"}]}`를 더한다. 프론트가 모르는 `phase`는 무시해도 동작한다.

## 8. 나중에 고도화할 것

지금은 하지 않는다. 지표를 보고 필요할 때 한다.

- **본문 검색 도구:** 지도로 찾기 어려운 질문("~한 적 있어?", "몇 화에 나왔지?")용. content 검색이 이미 있다.
- **캐시 최적화:** 보유 문서를 다시 넣지 않고 기록에 덧붙이기, 편집은 뒤에 덧붙이기, 기록 압축.
- **요약 갱신 정책:** 변경량 기준, 최근 회차 우선, 일괄 생성.
- **근거 수준 세분화:** `NOT_ENOUGH`를 일부만 찾음과 전혀 못 찾음으로 나누기. 해석이 갈림을 따로 두기.
- **모델 확신도:** 판정에 응답 모델의 답 후보와 확률을 더해 함께 보기(Sufficient Context 논문의 선택적 생성).
- **판정 근거 검증:** 판정 모델이 사실마다 인용을 달게 하고 서버가 본문과 대조하기.
- **판정 모델 교체:** 작은 모델로 바꿔 비용 줄이기.
- **아주 큰 작품의 지도:** 에피소드 단위로 접고 펼치기.
- **평가:** 같은 작품을 단계 상한을 크게 잡아 전부 읽게 한 답을 기준으로, 비용 대비 필요 문서 재현율과 수준별 정답률을 잰다.

## 9. 참고

- Joren 외, [Sufficient Context](https://arxiv.org/abs/2411.06037) (ICLR 2025): 충분도의 정의, 예시 하나를 준 판정 프롬프트(이진 라벨로 정확도 93%), 자료가 부족해도 모델이 지어낸다는 관찰
- Li 외, [GraphReader](https://arxiv.org/abs/2406.14550) (EMNLP Findings 2024): 그래프를 보고 노드를 골라 읽다가 충분하면 멈추는 구조
