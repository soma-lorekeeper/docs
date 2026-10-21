# AI 챗 탐색 설계 — 지도를 보고 필요한 문서부터 읽기

> 갱신일: 2026-10-21
> 상태: **설계.** 구현 전. 핵심 구조만 정한다. 세부는 필요할 때 고도화한다(§8).
> 독자: ai-chat·content를 구현하는 사람.
> 관련: [AI_CHAT_REQUIREMENTS.md](AI_CHAT_REQUIREMENTS.md). 이 방식이 그 문서 §3.3(매 턴 전체 스냅샷)과 §3.4(분량 초과면 챗을 열지 않음)를 대체한다. 나머지 계약(세션, 스트리밍, 근거 형식, 권한, 삭제)은 그대로다.

## 0. 한 줄 요약

**응답 모델**이 지도를 보고 필요한 순서대로 문서를 읽다가 "이제 답할 수 있다"고 하면, **판정 모델**이 그 문서들이 정말 충분한지 검토한다. 충분하면 답하고, 부족하면 빠진 것을 알려 다시 찾게 한다. 작품 크기와 상관없이 모든 프로젝트에 같은 방식을 쓴다.

## 1. 역할

| 역할 | 하는 일 | 하지 않는 일 |
|---|---|---|
| **응답 모델** | 읽을 문서의 순서를 정한다. 답할 준비가 됐는지 판단한다. 답을 쓴다 | 충분한지 최종 판단하지 않는다 |
| **판정 모델** | 읽어 온 문서가 질문에 답하기에 충분한지 검토하고, 부족하면 빠진 것을 말한다 | 답을 쓰거나 채점하지 않는다. 응답 모델의 이번 턴 출력을 보지 않는다 |
| **서버** | 응답 모델이 정한 순서대로, 단계당 글자 수 한도 안에서 문서를 읽어 준다. 루프 횟수를 센다 | 순서를 바꾸지 않는다 |

## 2. 에이전트 루프

```text
질문
 │
 ▼
응답 모델 ◀──────────────────────────────────┐
 │                                           │
 ├─ request_documents(ranking)               │
 │     서버: 순서대로 CHAT_STEP_MAX_CHARS까지 읽음 ─▶ 읽은 문서
 │                                           │
 └─ ready_to_answer()                        │
       판정 모델: 읽은 문서로 충분한가?        │
         ├─ 부족 ─────────────────────────────▶ missing
         └─ 충분 ─▶ 응답 모델이 답 (SUPPORTED) ─▶ 끝

CHAT_MAX_STEPS 도달 ─▶ "원고에서 충분한 내용을 찾지 못했어요"로 시작해 답 (NOT_ENOUGH)
```

```text
held = 이 세션에서 읽은 문서 (현재 판)
for step in 1..CHAT_MAX_STEPS:
    응답 모델 호출 (도구 필수. 마지막 단계는 ready_to_answer 강제)

    request_documents(ranking):
        읽을 수 있는 만큼 순서대로 읽어 held에 넣고, 도구 결과로 돌려줌 (§5)

    ready_to_answer():
        r = 판정 모델(질문, 직전 대화, held 본문)
        if r.sufficient:  답(SUPPORTED); 끝
        else:             r.missing을 도구 결과로 돌려줌

답(NOT_ENOUGH, 마지막 missing)
```

- 응답 모델은 **매 단계 처음부터 다시 정렬**한다. 방금 읽은 내용과 판정이 돌려준 `missing`이 다음 순서를 바꾼다.
- 한 턴 안에서는 도구 호출과 결과가 계속 덧붙으므로, 단계마다 앞부분의 프롬프트 캐시가 맞는다.

## 3. 판정: 답이 아니라 컨텍스트를 검토한다

판정은 응답 모델이 `ready_to_answer`를 부른 시점, **답을 쓰기 전에** 한다. 검토 대상은 "이 질문에 이 문서들이면 답할 수 있는가"다. 응답 모델이 스스로 충분하다고 하는 말은 믿지 않는다. 모델은 자료가 부족해도 충분하다고 여기고 그럴듯하게 지어내는 경향이 있다.

| | 내용 |
|---|---|
| **입력** | 질문, 직전 대화(질문과 답 본문), 읽은 문서 본문 |
| **넣지 않는 것** | 응답 모델의 이번 턴 추론·초안, 지도, 요약 |
| **출력** | `{ "sufficient": bool, "missing": [빠진 사실] }` |
| **충분의 정의** | 읽은 문서만으로 그럴듯한 답을 확정할 수 있으면 충분. 답이 맞는지가 아니라 근거가 있는지를 본다(Sufficient Context, ICLR 2025). 정답을 몰라도 판정할 수 있다 |
| **직전 대화** | 질문이 무엇을 가리키는지("그때", "그 사람") 알기 위해서만 쓴다. **이전 답은 근거가 아니다.** 근거는 문서뿐이다. 이전 턴의 틀린 답이 다음 턴의 근거로 둔갑하는 것을 막는다 |

판정이 응답 모델에 의존하는 곳은 하나다. **어떤 문서를 읽었는지는 응답 모델이 정했다.** 엉뚱한 문서만 골랐다면 판정은 "부족"을 내고, `missing`이 다시 찾을 방향을 준다. 이것은 의도한 동작이다.

## 4. 응답 모델이 보는 입력

턴마다 다시 만든다.

```text
instructions  시스템 지시 (§7.1)
input
  <project/> <author/>
  <map>      모든 문서·메모의 노드와 관계. 본문 없음
  <held>     이 세션에서 읽은 문서 전문 (처음 읽은 순서, 현재 판)
  이전 턴들의 질문과 답 본문
  <turn>     <viewing/> <quote/> <task/> <question/>
  … 이번 턴의 도구 호출과 결과가 덧붙음
```

**지도의 노드**

| 필드 | 뜻 |
|---|---|
| `id` | 문서 UUID의 **끝** 8자리(겹치면 늘림). uuidv7이라 앞자리는 생성 시각이어서 잘 겹친다 |
| `kind`, `title` | 종류(`manuscript`·`character`·…·`memo`)와 제목 |
| `number`, `episode` | 원고의 읽는 순서와 에피소드 |
| `chars` | 읽어 오는 비용. 본문 + 텍스트 속성의 글자 수(코드 포인트) |
| `rev` | 현재 revision |
| `<summary>`, `summary_rev` | 요약과 그 요약을 만든 판. 무엇을 읽을지 고르는 데만 쓴다. 근거가 아니다. 메모에는 없다 |

간선은 `<edge a="…" b="…">관계 설명</edge>`(content `document_relations`)다.

```xml
<node id="5f0c9a21" kind="manuscript" number="12" episode="Episode 4" title="12화 · 균열의 밤" chars="9105" rev="25" summary_rev="20">
<summary>레나가 무너진 성당에서 처음으로 빛을 끌어낸다. … 인물: 레나 아르벨, 오렌 / 장소: 무너진 성당</summary>
</node>
<edge a="5f0c9a21" b="a83e11d7">처음 각성한 곳</edge>
```

**보유 문서(held)** 는 턴마다 content에서 **현재 판으로 다시 받는다.** 편집된 문서는 저절로 최신이 되므로 변경을 따로 감지하지 않는다. 합이 `CHAT_HELD_MAX_CHARS`를 넘으면 먼저 읽은 것부터 뺀다. 빠진 문서는 다시 요청하면 된다.

## 5. 문서 담기 규칙

```text
cap = CHAT_STEP_MAX_CHARS
for id in ranking:
    if id in held:       건너뜀
    if chars(id) ≤ cap:  읽음, cap -= chars(id)
    elif 아직 읽은 게 없음: 이 문서 하나만 읽고 끝냄   (한도보다 큰 문서 예외)
    else:                건너뜀 (다음 단계에 다시 요청 가능)
```

순서대로 보면서 들어가는 것만 읽는다. 1순위는 언제나 읽힌다. 질문당 상한은 `CHAT_STEP_MAX_CHARS × CHAT_MAX_STEPS`다.

## 6. 종료와 답

| 수준 | 조건 | 답 |
|---|---|---|
| `SUPPORTED` | 판정이 충분 | 그대로 답한다 |
| `NOT_ENOUGH` | 최대 단계까지 충분하지 않음 | **"원고에서 충분한 내용을 찾지 못했다"로 시작한다.** 읽은 문서가 보여 주는 것만 답하고, `missing`을 말한다. 추정하지 않는다 |

해석이 갈리는 경우는 응답 모델 지시의 한 줄로 처리한다(§7.1). 수준은 답 메시지의 `grounding`에 저장하고 `done`으로 내려보낸다.

## 7. 프롬프트

영어로 쓴다(지금 프롬프트와 같다). 답은 질문의 언어로 한다. `# Format`과 `# Sources`는 지금 `INSTRUCTIONS`(`loresentry-ai-chat/app/prompt.py`)를 그대로 쓴다.

### 7.1 응답 모델 시스템 지시

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

### 7.2 도구

```json
[
  { "type": "function", "name": "request_documents", "strict": true,
    "parameters": { "type": "object", "additionalProperties": false, "required": ["ranking"],
      "properties": { "ranking": { "type": "array", "items": { "type": "string" }, "minItems": 1, "maxItems": 20 } } } },
  { "type": "function", "name": "ready_to_answer", "strict": true,
    "parameters": { "type": "object", "additionalProperties": false, "properties": {} } }
]
```

### 7.3 판정 모델 시스템 지시

```text
You review whether a set of documents from a web novel project is enough to answer the author's question. You do not answer the question.
The documents are enough if a definite, plausible answer can be built from them alone, without outside knowledge and without treating the author's memos as story facts. The answer does not have to be right, only supported by the documents. Every step of a multi-step answer must appear in the documents. If several answers are possible, the documents must give a reason to choose one.
The conversation is only for understanding what the question refers to. Earlier answers are not evidence; only the documents are.
If the documents are not enough, list what is missing as short facts to look for.

Example
Question: 레나가 처음 각성한 곳은 어디야?
Documents say "레나는 무너진 성당의 제단 앞에서 처음으로 빛을 끌어냈다." → {"sufficient": true, "missing": []}
Documents only say "성당에서의 각성 이후 레나는 달라졌다." → {"sufficient": false, "missing": ["그것이 레나의 첫 각성이라는 근거"]}
```

입력은 `<conversation>`·`<documents>`·`<question>`이고, 출력은 JSON 스키마로 받는다.

```json
{ "type": "object", "additionalProperties": false, "required": ["sufficient", "missing"],
  "properties": { "sufficient": { "type": "boolean" },
                  "missing": { "type": "array", "items": { "type": "string" }, "maxItems": 5 } } }
```

### 7.4 마지막 답 지시

판정이 끝난 뒤 응답 모델에게 붙이고, 도구 없이 답을 스트리밍한다.

```text
<final level="SUPPORTED">Answer the question now.</final>
<final level="NOT_ENOUGH">Answer now. Begin by saying you could not find enough in the manuscript. Answer only what the documents you read show, and name what is missing: {missing}. Do not guess.</final>
```

### 7.5 요약 생성

```text
Summarize this document from a web novel project so that someone can decide, without reading it, whether it answers a question about the story.
Write in the language of the document. 2 to 4 sentences on what it says or what happens, then the names of people, places and things that appear. Use names exactly as written. No evaluation, no guesses. At most 500 characters.
```

요약은 문서(원고·설정)에만 만든다. `summary_rev`가 현재 `rev`와 다르고 마지막 수정 뒤 `SUMMARY_IDLE_MINUTES`가 지났으면 백그라운드에서 다시 만든다. 턴은 기다리지 않고, 요약이 없으면 노드를 요약 없이 내보낸다.

## 8. 필요한 것

**content 내부 API** (권한·404 규칙은 기존 내부 API와 같다)

- `GET /internal/projects/{id}/chat-map`: `chat-snapshot`에서 본문을 빼고 문서마다 `chars`를 더한 것. 관계와 메모 목록(본문 없이)을 포함한다.
- `POST /internal/projects/{id}/chat-documents` `{"document_ids": [...], "memo_ids": [...]}`: 고른 문서·메모의 본문. 모양은 `chat-snapshot`의 항목과 같다.

구현은 [AI_CHAT_NAVIGATION_IMPLEMENTATION.md](AI_CHAT_NAVIGATION_IMPLEMENTATION.md)에 있다.

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

**SSE:** 바꾸지 않는다. 지도를 받는 동안 `gathering`, 루프 동안 `reading`, 첫 답 토큰부터 `writing`이다. 읽는 문서를 보여 주는 `phase`는 나중에 더한다.

## 9. 나중에 고도화할 것

지금은 하지 않는다. 지표를 보고 필요할 때 한다.

- **본문 검색 도구:** 지도로 찾기 어려운 질문("~한 적 있어?", "몇 화에 나왔지?")용. content 검색이 이미 있다.
- **캐시 최적화:** 보유 문서를 매 턴 다시 넣지 않고 기록에 덧붙이기, 기록 압축.
- **요약 갱신 정책:** 변경량 기준, 최근 회차 우선, 일괄 생성.
- **판정 강화:** 사실마다 인용을 달게 하고 서버가 본문과 대조하기. 응답 모델의 답 후보와 확률을 함께 보기.
- **단서 세분화:** 일부만 찾음 / 전혀 못 찾음 / 해석이 갈림.
- **판정 모델 교체:** 작은 모델로 비용 줄이기.
- **아주 큰 작품의 지도:** 에피소드 단위로 접고 펼치기.
- **평가:** 단계 한도를 크게 잡아 전부 읽게 한 답을 기준으로, 비용 대비 필요 문서 재현율과 수준별 정답률을 잰다.

## 10. 참고

- Joren 외, [Sufficient Context](https://arxiv.org/abs/2411.06037) (ICLR 2025): 충분의 정의, 예시 하나를 준 판정 프롬프트(이진 라벨로 정확도 93%), 자료가 부족해도 모델이 지어낸다는 관찰
- Li 외, [GraphReader](https://arxiv.org/abs/2406.14550) (EMNLP Findings 2024): 그래프를 보고 노드를 골라 읽다가 충분하면 멈추는 구조
