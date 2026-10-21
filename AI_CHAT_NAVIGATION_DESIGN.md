# AI 챗 탐색 설계 — 지도를 보고 필요한 문서부터 읽기

> 갱신일: 2026-10-21
> 상태: **설계.** 구현 전. 핵심 구조만 정한다. 세부는 필요할 때 고도화한다(§10).
> 독자: ai-chat·content를 구현하는 사람.
> 관련: [AI_CHAT_REQUIREMENTS.md](AI_CHAT_REQUIREMENTS.md). 이 방식이 그 문서 §3.3(매 턴 전체 스냅샷)·§3.4(분량 초과면 챗을 열지 않음)·§3.5의 실시간 스트리밍을 대체한다. 나머지 계약(세션, 근거 형식, SSE 이벤트 모양, 권한, 삭제)은 그대로다. 구현은 [AI_CHAT_NAVIGATION_IMPLEMENTATION.md](AI_CHAT_NAVIGATION_IMPLEMENTATION.md).

## 0. 한 줄 요약

**응답 모델**이 지도를 보고 필요한 순서대로 문서를 읽다가, 충분하다고 보면 도구 대신 **답**을 쓴다. 그 답을 두 판정이 동시에 검토한다. **충분도 판정**은 AI가 쓴 글을 보지 않고 읽은 문서만으로 답할 수 있는지 보고, **답 판정**은 답이 그 문서에 근거해 합리적인지 본다. 통과한 답만 작가에게 내보낸다. 통과하지 못하면 판정의 지적을 받아 더 읽거나 다시 쓴다. 답 → 판정 → 전달이 직렬로 이어진다. 작품 크기와 상관없이 모든 프로젝트에 같은 방식을 쓴다. 지도의 요약은 별도 **요약 스케줄러**가 미리 만들어 둔다.

## 1. 역할

| 역할 | 하는 일 | 하지 않는 일 |
|---|---|---|
| **응답 모델** | 읽을 문서의 순서를 정한다. 충분하다고 보면 답을 쓰고, 판정의 지적을 받으면 더 읽거나 고쳐 쓴다 | 자기 답을 최종 승인하지 않는다 |
| **충분도 판정** | 읽은 문서만으로 질문에 답할 수 있는지 본다. 부족하면 무엇이 빠졌는지 말한다 | **AI가 쓴 글을 하나도 보지 않는다**(이번 답, 이전 턴의 답, 추론) |
| **답 판정** | 답이 읽은 문서에 근거해 질문에 합리적으로 답하는지 본다. 틀린 곳을 말한다 | 답을 대신 쓰지 않는다. 응답 모델의 추론이나 도구 호출은 보지 않는다 |
| **서버** | 응답 모델이 정한 순서대로 단계당 글자 수 한도 안에서 문서를 읽어 준다. 단계와 시간을 센다. 통과한 답을 근거 검증을 거쳐 내보낸다 | 순서를 바꾸지 않는다 |
| **요약 스케줄러** | 모든 문서의 요약을 미리 만들고, 많이 바뀐 문서는 다시 만든다(§5) | 챗 턴에 끼어들지 않는다 |

## 2. 에이전트 루프

```text
질문
 │
 ▼
응답 모델 ◀───────────────────────────────────────────┐
 │                                                     │
 ├─ 도구 호출 request_documents(ranking)                 │
 │     서버: 순서대로 CHAT_STEP_MAX_CHARS까지 읽음 ──────▶ 읽은 문서
 │                                                     │
 └─ 일반 메시지 = 답                                     │
       충분도 판정 ∥ 답 판정 (동시에)                     │
         ├─ 하나라도 아니오 ─────────────────────────────▶ <review> missing, problems
         └─ 예 ─▶ 답을 내보냄 (SUPPORTED) ─▶ 끝

CHAT_MAX_STEPS 또는 시간 한도 도달 ─▶ 지적을 반영한 마지막 답을 쓰고 판정 없이 내보냄 (NOT_ENOUGH)
```

```text
held = 이 세션에서 읽은 문서 (현재 판)
for step in 1..CHAT_MAX_STEPS:
    out = 응답 모델 호출 (tool_choice=auto. 마지막 단계는 none으로 답 강제)

    if out이 request_documents:
        읽을 수 있는 만큼 순서대로 읽어 held에 넣고 돌려줌 (§4)
        continue

    answer = out의 본문
    s = 충분도 판정(질문, 이전 질문들, held 본문)        held가 지난 판정 뒤 그대로면 지난 결과 재사용
    a = 답 판정(질문, 직전 대화, held 본문, answer)      s와 동시에 실행
    if s.sufficient and a.answer_ok:  answer를 내보냄 (SUPPORTED); 끝
    <review missing=s.missing problems=a.problems>를 덧붙임

answer = 응답 모델 호출 (tool_choice=none, <final> 지시 + 마지막 review); 판정 없이 내보냄 (NOT_ENOUGH)
```

- **"준비됨"을 알리는 도구는 없다.** 응답 모델이 도구를 부르지 않고 일반 메시지를 쓰면 그것이 답이다. 답을 도구 인자에 넣으면 근거 표시의 따옴표를 JSON 안에서 이스케이프해야 해서 모델이 자주 틀린다.
- 응답 모델은 **매 단계 처음부터 다시 정렬**한다. 방금 읽은 내용과 판정의 `missing`이 다음 순서를 바꾼다.
- 답 판정만 거절하면(컨텍스트는 충분) 응답 모델은 보통 바로 고쳐 쓴다. 더 읽을지는 응답 모델이 정한다. 이때 읽은 문서가 그대로라 충분도는 다시 묻지 않는다.
- 거절된 답은 입력에서 지우지 않는다. 응답 모델이 "무엇을 썼고 왜 거절됐는지"를 함께 봐야 같은 실수를 반복하지 않는다. 한 턴의 `<review>`는 모두 누적된다.
- 어느 호출이 답이 될지 모르므로 모든 단계를 답 품질에 맞는 추론 강도로 부른다.
- 한 턴 안에서는 도구 호출과 결과, 답과 판정 지적이 계속 덧붙으므로 단계마다 앞부분의 프롬프트 캐시가 맞는다.

## 3. 판정: 충분도와 답을 따로 본다

판정은 답이 나온 뒤, **작가에게 보내기 전에** 한다. 응답 모델이 스스로 충분하다고 하거나 자기 답이 맞다고 하는 말은 믿지 않는다. 모델은 자료가 부족해도 충분하다고 여기고 그럴듯하게 지어내는 경향이 있다.

두 판정으로 나눈다. 한 번에 답과 문서를 같이 보면 판정이 **답을 보고 거꾸로 추론**할 수 있다("답이 그럴듯하니 자료도 충분하겠지"). 충분도는 원래 답이 필요 없는 질문이다.

| | 충분도 판정 | 답 판정 |
|---|---|---|
| 묻는 것 | 이 문서들만으로 답을 확정할 수 있는가 | 이 답이 문서에 근거해 질문에 합리적으로 답하는가 |
| 입력 | 질문, **이전 질문들(작가가 쓴 글만)**, 읽은 문서 본문 | 질문, 직전 대화(질문과 답 본문), 읽은 문서 본문, 응답 모델의 답(근거 표시 포함) |
| 넣지 않는 것 | **AI가 쓴 글 전부**(이번 답, 이전 턴의 답, 추론), 지도, 요약 | 응답 모델의 추론·도구 호출, 지도, 요약 |
| 출력 | `{ "sufficient": bool, "missing": [...] }` | `{ "answer_ok": bool, "problems": [...] }` |

- 둘은 **동시에** 실행한다. 지연은 판정 한 번과 같다.
- **통과**는 `sufficient`와 `answer_ok`가 모두 true일 때다.
- 답 판정만 거절되어 응답 모델이 더 읽지 않고 고쳐 쓰면, 읽은 문서가 그대로이므로 충분도 결과를 다시 쓴다.

**충분(`sufficient`)** — 읽은 문서만으로 그럴듯한 답을 확정할 수 있으면 충분하다. 답이 맞는지가 아니라 근거가 있는지를 본다(Sufficient Context, ICLR 2025). `missing`은 더 찾아야 할 사실이다.

이전 턴의 답도 AI가 쓴 글이라 충분도 판정에서 뺀다. 그래서 "그때 누구랑 있었어?"처럼 이전 답의 내용을 가리키는 후속 질문에서는 충분도 판정이 "부족"으로 기울 수 있다. 틀린 답을 통과시키는 쪽이 아니라 한 단계를 더 쓰는 쪽으로 실패하므로 감수한다.

**합리적(`answer_ok`)** — 답이 다음을 모두 지키면 합리적이다. 어긋난 점은 `problems`에 적는다.

1. 질문에 실제로 답한다(딴 이야기, 질문 바꿔치기 없음).
2. 모든 주장이 읽은 문서에 근거한다. 문서에 없는 사건·성격·설정을 지어내지 않는다.
3. 근거 표시가 가리키는 문서가 그 주장을 실제로 담고 있다.
4. 문서와 모순되지 않고, 원고가 보여 주는 것과 설정·메모가 말하는 것을 섞지 않는다.
5. 해석이 갈리거나 빠진 것이 있으면 그렇다고 밝힌다.

답 판정의 **직전 대화**는 질문이 무엇을 가리키는지("그때", "그 사람") 알기 위해서만 쓴다. **이전 답은 근거가 아니다.** 근거는 문서뿐이다.

**거절의 이유는 응답 모델에게 돌려준다.** `missing`은 다음에 읽을 문서를 고르는 데, `problems`는 고쳐 쓰는 데 쓴다. 무엇이 빠졌는지는 판정이, 어디서 찾을지는 응답 모델이 정한다. 판정은 지도를 보지 않으므로 문서를 추천하지 않는다.

남는 의존은 **어떤 문서를 읽었는지는 응답 모델이 정한다**는 것 하나다. 충분도 판정이 검토하는 대상 자체가 그것이다.

## 4. 응답 모델이 보는 입력과 문서 담기

턴마다 다시 만든다.

```text
instructions  응답 모델 시스템 지시 (§7.1)
input
  <project/> <author/>
  <map>      모든 문서·메모의 노드와 관계. 본문 없음
  <held>     이 세션에서 읽은 문서 전문 (처음 읽은 순서, 현재 판)
  이전 턴들의 질문과 답 본문
  <turn>     <viewing/> <quote/> <task/> <question/>
  … 이번 턴의 도구 호출과 결과, 답과 판정 지적이 덧붙음
```

**지도의 노드**

| 필드 | 뜻 |
|---|---|
| `id` | 문서 UUID의 **끝** 8자리(겹치면 늘림). uuidv7이라 앞자리는 생성 시각이어서 잘 겹친다 |
| `kind`, `title` | 종류(`manuscript`·`character`·…·`memo`)와 제목 |
| `number`, `episode` | 원고의 읽는 순서와 에피소드 |
| `chars` | 읽어 오는 비용. 본문 + 텍스트 속성의 글자 수(코드 포인트) |
| `rev` | 현재 revision |
| `<property>` | 작가가 쓴 속성(별칭, 설명). 항상 최신이다 |
| `<summary>`, `summary_rev` | 요약과 그 요약을 만든 판(§5). 무엇을 읽을지 고르는 데만 쓴다. 근거가 아니다. 메모에는 없다 |

간선은 `<edge a="…" b="…">관계 설명</edge>`(content `document_relations`)다.

```xml
<node id="5f0c9a21" kind="manuscript" number="12" episode="Episode 4" title="12화 · 균열의 밤" chars="9105" rev="25" summary_rev="20">
<summary>레나가 무너진 성당에서 처음으로 빛을 끌어낸다. … 인물: 레나 아르벨, 오렌 / 장소: 무너진 성당</summary>
</node>
<edge a="5f0c9a21" b="a83e11d7">처음 각성한 곳</edge>
```

**보유 문서(held)** 는 턴마다 content에서 **현재 판으로 다시 받는다.** 편집된 문서는 저절로 최신이 되므로 변경을 따로 감지하지 않는다. 합이 `CHAT_HELD_MAX_CHARS`를 넘으면 먼저 읽은 것부터 뺀다.

**담기 규칙**

```text
cap = CHAT_STEP_MAX_CHARS
for id in ranking:
    if id in held:       건너뜀
    if chars(id) ≤ cap:  읽음, cap -= chars(id)
    elif 아직 읽은 게 없음: 이 문서 하나만 읽고 끝냄   (한도보다 큰 문서 예외)
    else:                건너뜀 (다음 단계에 다시 요청 가능)
```

순서대로 보면서 들어가는 것만 읽는다. 1순위는 언제나 읽힌다.

## 5. 요약 스케줄러

| 항목 | 결정 |
|---|---|
| 대상 | 모든 프로젝트의 원고와 설정 문서. 메모는 제외(변경 이벤트가 없다) |
| 실행 | ai-chat 레포의 별도 프로세스(`ai-chat-summarizer`). 답변 서버와 다른 Pod |
| 트리거 | content가 발행하는 Kafka `content.project.changed.v1`의 `FileChanged`·`ProjectDeleted`를 별도 컨슈머 그룹으로 받는다. 이벤트에 본문이 들어 있어 content를 다시 부르지 않는다 |
| 시점 | 마지막 수정 뒤 **10분** 동안 편집이 없을 때 |
| 조건 | 요약이 없거나, 요약 시점과 비교한 변화량이 **min(1,000자, 문서 길이의 30%)** 이상일 때 |
| 변화량 | 요약할 때 문장 단위 지문 `(해시, 글자 수)`만 저장한다. 지금 본문과 비교해 사라진 문장 + 새로 생긴 문장의 글자 수 합을 변화량으로 본다. 원고 사본을 남기지 않는다 |
| 짧은 문서 | 본문이 500자 이하면 LLM 없이 본문을 그대로 요약으로 쓴다 |
| 저장 | ai_chat DB `document_summaries`. 답변 서버가 지도를 만들 때 읽는다. content에 돌려주지 않는다(요약은 AI 챗을 위한 증강 정보이고 다른 용도가 없다) |
| 삭제 | `TRASHED`·`DELETED`·`ProjectDeleted` 이벤트로 지운다. 계정 삭제는 content가 프로젝트마다 `ProjectDeleted`를 내므로 함께 처리된다 |
| 백필 | 한 번도 편집되지 않은 기존 문서는 content가 `FileChanged`를 한 번 다시 내보내 채운다 |

변화량이 기준에 못 미치면 요약은 그대로 두고 **지문도 요약 시점 것을 유지**한다. 작은 수정이 쌓여 기준을 넘는 순간 다시 요약된다. 요약이 조금 낡아도 괜찮다. 요약은 고르는 데만 쓰고 답은 본문으로 한다.

## 6. 종료와 답

| 수준 | 조건 | 답 |
|---|---|---|
| `SUPPORTED` | 판정 통과 | 통과한 답을 그대로 내보낸다 |
| `NOT_ENOUGH` | 최대 단계 또는 시간 한도까지 통과하지 못함 | 마지막 판정의 `missing`·`problems`를 반영해 다시 쓴 답. **"원고에서 확실한 답을 찾지 못했다"로 시작하고**, 문서가 보여 주는 것만 답하며 빠진 것을 말한다. 판정 없이 내보낸다 |

**내보내기.** 답은 판정 뒤에 내보내므로 실시간 스트리밍이 아니다. 서버는 답을 지금의 근거 파서(`CitationStream`)에 통과시켜 `citation`·`delta` 이벤트로 나눠 보낸다. SSE 이벤트 모양은 그대로이고, 작가는 확인이 끝난 답을 한 번에 빠르게 받는다. 검증되지 않은 답이 화면에 나갔다가 뒤집히는 일이 없고, 답을 두 번 생성하지 않는다.

수준은 답 메시지의 `grounding`에 저장하고 `done`으로 내려보낸다.

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
At each step, either call request_documents or write your answer.
- request_documents(ranking): the documents to read next, most necessary first. The server reads them in your order until this step's size limit is reached; the rest can be requested at a later step. Re-rank from scratch at every step using what you have read. Do not list held documents.
- When the documents you hold are enough, write your answer as a normal message instead of calling the tool. Reviewers check it against the documents.
- After a <review>, rank first the documents most likely to contain each <missing> fact. Do not request again documents that turned out not to help.
- When rewriting after a <review>, fix every <problem> and keep the parts of your previous answer that were not criticized.
Rank by how likely a document is to contain the facts the answer still needs. Start from the quoted passage, the document the author is viewing, and nodes whose title or summary match the question. Follow edges from documents that turned out useful.

# How to answer
- Answer only from documents you have read. Never from summaries or general knowledge. If they do not contain the answer, say so and say what is missing. Do not invent events, traits or settings.
- Distinguish what the manuscript shows from what setting documents or memos say. Point out contradictions.
- If the documents support more than one reading, say so and give each reading with what it rests on.
- You cannot edit documents. You may offer wording, but the author has to apply it.
- Answer in the language of the author's question; if unclear, use the author's locale.
- Lead with the answer, then the supporting points. Prefer chapter titles and character names.

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
      "properties": { "ranking": { "type": "array", "items": { "type": "string" }, "minItems": 1, "maxItems": 20 } } } }
]
```

### 7.3 판정 지적과 마지막 답 지시

판정을 통과하지 못하면 다음을 user 메시지로 덧붙이고 루프를 이어 간다.

```text
<review n="2" sufficient="false" answer_ok="false">
<missing>그것이 레나의 첫 각성이라는 근거</missing>
<problem>답은 12화라고 하지만 인용한 문서는 30화다</problem>
</review>
```

통과하지 못한 채 끝날 때(`tool_choice=none`으로 부른다):

```text
<final>Write your answer now. Begin by saying you could not find a certain answer in the manuscript. Answer only what the documents you read show. Still missing: {missing}. Problems to avoid: {problems}. Do not guess.</final>
```

### 7.4 충분도 판정 시스템 지시

```text
You decide whether a set of documents from a web novel project is enough to answer the author's question. You do not answer it.
The documents are enough if a definite, plausible answer can be built from them alone, without outside knowledge and without treating the author's memos as story facts. The answer does not have to be right, only supported by the documents. Every step of a multi-step answer must appear in the documents. If several answers are possible, the documents must give a reason to choose one.
The author's earlier questions are only for understanding what the question refers to.
If the documents are not enough, list in `missing` the facts still to look for, as short phrases.

Example
Question: 레나가 처음 각성한 곳은 어디야?
Documents say "레나는 무너진 성당의 제단 앞에서 처음으로 빛을 끌어냈다." → {"sufficient": true, "missing": []}
Documents only say "성당에서의 각성 이후 레나는 달라졌다." → {"sufficient": false, "missing": ["그것이 레나의 첫 각성이라는 근거"]}
```

입력은 `<earlier_questions>`·`<documents>`·`<question>`이다. 출력 스키마:

```json
{ "type": "object", "additionalProperties": false, "required": ["sufficient", "missing"],
  "properties": { "sufficient": { "type": "boolean" },
                  "missing": { "type": "array", "items": { "type": "string" }, "maxItems": 5 } } }
```

### 7.5 답 판정 시스템 지시

```text
You review an answer to an author's question about their web novel project against the documents it was based on. You do not rewrite it.
The answer is ok if it addresses the question that was asked; every claim is supported by the documents; each <cite> points to a document that actually contains what it supports; it does not contradict the documents or present setting documents or memos as events of the manuscript; and it says so where the documents are ambiguous or silent. List each failure in `problems`, briefly and specifically.
The conversation is only for understanding what the question refers to. Earlier answers are not evidence; only the documents are.

Example
Question: 레나가 처음 각성한 곳은 어디야?
Documents: 5f0c9a21 says "레나는 무너진 성당의 제단 앞에서 처음으로 빛을 끌어냈다."
Answer: "레나는 12화에서 무너진 성당의 제단 앞에서 처음 각성했어요.<cite id="5f0c9a21" .../>" → {"answer_ok": true, "problems": []}
Answer: "레나는 북쪽 온실에서 처음 각성했어요." → {"answer_ok": false, "problems": ["문서는 무너진 성당이라고 하는데 답은 북쪽 온실이라고 한다"]}
```

입력은 `<conversation>`·`<documents>`·`<question>`·`<answer>`이다. 출력 스키마:

```json
{ "type": "object", "additionalProperties": false, "required": ["answer_ok", "problems"],
  "properties": { "answer_ok": { "type": "boolean" },
                  "problems": { "type": "array", "items": { "type": "string" }, "maxItems": 5 } } }
```

### 7.6 요약 생성

```text
Summarize this document from a web novel project so that someone can decide, without reading it, whether it answers a question about the story.
Write in the language of the document. 2 to 4 sentences on what it says or what happens, then the names of people, places and things that appear. Use names exactly as written. No evaluation, no guesses. At most 500 characters.
```

## 8. 필요한 것

**content**

- `GET /internal/projects/{id}/chat-map`: `chat-snapshot`에서 본문을 빼고 문서마다 `chars`를 더한 것. 관계와 메모 목록(본문 없이)을 포함한다.
- `POST /internal/projects/{id}/chat-documents` `{"document_ids": [...], "memo_ids": [...]}`: 고른 문서·메모의 본문. 모양은 `chat-snapshot`의 항목과 같다.
- 요약 백필용 이벤트 재발행(한 번 쓰는 관리 기능).

**ai_chat DB:** 보유 문서, 요약과 지문, 요약 큐, 답의 `grounding`, 턴 지표.

**설정값 (주요)**

| 이름 | 초깃값 |
|---|---|
| `CHAT_STEP_MAX_CHARS` | 40,000 |
| `CHAT_MAX_STEPS` | 4 |
| `CHAT_HELD_MAX_CHARS` | 200,000 |
| `CHAT_JUDGE_MODEL` | 답변 모델과 같음 |
| `SUMMARY_IDLE_MINUTES` | 10 |
| `SUMMARY_MIN_CHANGE_CHARS` / `SUMMARY_MIN_CHANGE_RATIO` | 1,000 / 0.3 |

**SSE:** 이벤트 모양은 바꾸지 않는다. 지도를 받는 동안 `gathering`, 루프·판정 동안 `reading`, 통과한 답을 내보낼 때 `writing`이다.

## 9. 처리방침

요약 스케줄러는 작가가 요청하지 않아도 저장된 원고를 OpenAI로 보낸다. 지금 개인정보 처리방침 제4조("원고 저장만으로 자동 분석을 실행하지 않습니다")와 어긋난다. 이 설계에서는 고려하지 않기로 했다(2026-10-21). 출시 전에 처리방침을 맞춘다.

## 10. 나중에 고도화할 것

- **본문 검색 도구:** 지도로 찾기 어려운 질문("~한 적 있어?", "몇 화에 나왔지?")용. content 검색이 이미 있다.
- **캐시 최적화:** 보유 문서를 매 턴 다시 넣지 않고 기록에 덧붙이기, 기록 압축.
- **스트리밍 복원:** 첫 답이 판정을 통과하는 비율이 높으면, 답을 스트리밍하면서 판정하고 실패할 때만 고쳐 쓰기.
- **판정 강화:** 사실마다 인용을 달게 하고 서버가 본문과 대조하기. 응답 모델의 답 후보와 확률을 함께 보기.
- **단서 세분화:** 일부만 찾음 / 전혀 못 찾음 / 해석이 갈림.
- **판정·요약 모델 교체:** 작은 모델로 비용 줄이기.
- **요약을 다른 서비스와 공유:** 그래프 화면·검색이 쓰게 되면 `DocumentSummarized` 이벤트로 내보내기.
- **아주 큰 작품의 지도:** 에피소드 단위로 접고 펼치기.
- **평가:** 단계 한도를 크게 잡아 전부 읽게 한 답을 기준으로, 비용 대비 필요 문서 재현율과 수준별 정답률, 판정의 통과율을 잰다.

## 11. 참고

- Joren 외, [Sufficient Context](https://arxiv.org/abs/2411.06037) (ICLR 2025): 충분의 정의, 예시 하나를 준 판정 프롬프트(이진 라벨로 정확도 93%), 자료가 부족해도 모델이 지어낸다는 관찰
- Li 외, [GraphReader](https://arxiv.org/abs/2406.14550) (EMNLP Findings 2024): 그래프를 보고 노드를 골라 읽다가 충분하면 멈추는 구조
