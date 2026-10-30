# 원고 암호화 — 프로젝트 키 봉투 암호화

> 최신화: 2026-10-31  
> 대상: content · graph-rag · ai-chat이 저장하거나 서로 전달하는 원고 본문  
> 상태: **설계. 구현 전.**

## 0. 요약

- 원고는 **프로젝트마다 하나인 키**로 암호화한다. 서비스마다 키를 두지 않는다.
- content가 원고를 처음 저장할 때 암호화하고, 그 암호문이 DB → outbox → Kafka → ai-chat으로 **그대로** 간다. S3 최신화 입력·결과는 같은 프로젝트 키로 객체 전체를 암호화한다. 원문이 필요한 서비스만 그 자리에서 푼다.
- 키는 두 단계다. KMS 마스터 키 하나가 프로젝트 키를 감싸고, DB·이벤트에는 **감싼 프로젝트 키만** 둔다. 마스터 키는 KMS 밖으로 나오지 않는다.
- 감싼 키를 풀 수 있는 것은 content · graph-rag · ai-chat의 Pod Identity 역할뿐이다. DB나 백업, Kafka 메시지, S3 객체가 유출되어도 원고를 읽을 수 없다.
- 프로젝트 키는 바꾸지 않는다. 마스터 키는 KMS 자동 교체를 켠다.

```text
                     ┌──────── KMS: 마스터 키 (밖으로 나오지 않음) ────────┐
                     │  GenerateDataKey (content만) · Decrypt (3개 역할만)  │
                     └──────────────────────────────────────────────────────┘
                                ▲ 감싼 키 W를 보내고 평문 키 K를 받는다
                                │
작가 ─저장─> content ─K로 암호화─> content DB (암호문 C, 감싼 키 W)
                │
                ├─ outbox → Kafka v2 { wrapped_key: W, body_text_enc: C } ─> graph-rag, ai-chat (W로 K를 받아 C를 푼다)
                └─ S3 input.enc = K로 암호화한 입력 전체 ─> graph-rag ─ result.enc = K로 암호화 ─> content
```

---

## 1. 지금 상태

| 위치 | 원고가 들어 있는 곳 | 지금 보호 수준 |
|---|---|---|
| content DB `document` | `body_json`, `body_text`, `body_md`(옛 행) | RDS 디스크 암호화(`alias/aws/rds`)뿐. SQL로 읽으면 평문 |
| content DB `document_versions` | `snapshot` | 〃 |
| content DB `refresh_document_drafts` | `left_snapshot`, `right_snapshot`, `origin_snapshot` | 〃 |
| content DB `refresh_results` | `payload`(result 원본. 제안 본문, 근거 인용) | 〃 |
| content DB `outbox_events` | `payload`의 `file.body_text` | 〃 (발행 3일 뒤 삭제) |
| Kafka `content.project.changed.v1`과 DLQ | `file.body_text` | `tls: false`, EBS 암호화. 3일·7일 보존 |
| S3 `refresh/{project}/{request}/input.json` | 최신화 대상 문서 본문 | SSE-S3. 3일 만료 |
| S3 `refresh/{project}/{request}/result.json` | 제안 본문, 근거 인용 | 〃 |
| ai-chat DB `summary_queue` | `body` (본문 전체 사본) | RDS 디스크 암호화뿐 |
| ai-chat DB `document_summaries`, `chat_messages`, `chat_sessions` | 요약, 답변, 드래그 인용, 근거, 미리보기 | 〃 |
| RDS 자동 백업 7일 | 위 DB 전부 | 디스크 암호화뿐 |

디스크 암호화는 볼륨이나 스냅샷 파일이 유출됐을 때만 막아 준다. DB에 접속하면 평문이 보이고, 지금은 `lorekeeper-owner` 팀원 모두가 Secrets Manager에서 DB 마스터 비밀번호를 꺼낼 수 있다.

Neptune에는 본문이 없다. 파일 id, 프로젝트 id, 관계 설명만 있다.

---

## 2. 키

### 2.1 두 종류

| 이름 | 개수 | 어디에 있나 | 하는 일 |
|---|---|---|---|
| **마스터 키** (KMS 대칭 키) | 환경마다 1개. `alias/lore-sentry-manuscript`, dev는 `alias/lore-sentry-manuscript-dev` | KMS 안. 값을 꺼낼 수 없음 | 프로젝트 키를 감싸고 푼다 |
| **프로젝트 키** K (AES-256, 32바이트) | 프로젝트마다 1개 | **서버 메모리에만.** DB·디스크·로그에 쓰지 않는다 | 원고를 암호화하고 푼다 |
| **감싼 프로젝트 키** W (약 180바이트) | 프로젝트마다 1개 | content `project_keys`, Kafka 메시지, ai-chat `project_keys` | K를 마스터 키로 잠근 것. KMS만 풀 수 있다 |

원고를 마스터 키로 직접 암호화하지 않는 이유는 두 가지다. KMS는 한 번에 4KB까지만 암호화하고, 조회마다 KMS를 부르면 지연과 요청 비용이 생긴다. KMS는 32바이트짜리 프로젝트 키만 다룬다.

### 2.2 만들기

프로젝트를 만드는 트랜잭션에서 content가 만든다. 기존 프로젝트는 전환 때 배치로 만든다(§8).

```text
KMS GenerateDataKey(KeyId=alias/lore-sentry-manuscript, KeySpec=AES_256,
                    EncryptionContext={"project_id": "<소문자 UUID>"})
  → Plaintext K (메모리 캐시에 넣는다), CiphertextBlob W (project_keys에 저장한다)
```

```sql
CREATE TABLE project_keys (
    project_id  UUID        PRIMARY KEY REFERENCES projects (id) ON DELETE CASCADE,
    wrapped_key BYTEA       NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

encryption context는 비밀값이 아니다. W를 다른 프로젝트의 키로 속여 쓰지 못하게 묶고, CloudTrail에 어느 프로젝트의 키를 풀었는지 남기는 용도다.

### 2.3 풀기

```text
메모리 캐시에 project_id → K가 있으면 그대로 쓴다
없으면 KMS Decrypt(CiphertextBlob=W, EncryptionContext={"project_id": …}) → K를 캐시에 넣는다
```

- 캐시는 pod마다 둔다. 만료 10분, 항목 상한 1,000개로 시작한다.
- KMS 호출은 캐시에 없을 때만 일어난다. 복호화 자체는 AES-GCM이라 100만 자 문서도 몇 ms다.
- KMS 오류는 일시 장애로 다룬다. API는 503을 돌려주고, Kafka 소비자는 DLQ로 보내지 않고 재시도한다.

### 2.4 교체

- **프로젝트 키는 바꾸지 않는다.** DB 유출에는 교체가 효과가 없다(어차피 W를 못 푼다). 메모리에서 K가 샌 경우는 옛 암호문까지 전부 다시 써야 의미가 있는데, 사본이 DB 여러 테이블·Kafka·ai-chat에 퍼져 있어 비용에 비해 얻는 것이 적다.
- **마스터 키는 KMS 자동 교체(365일)를 켠다.** KMS가 옛 키 버전을 보관하므로 이전에 감싼 W도 그대로 풀린다. 코드나 데이터는 바뀌지 않는다.

### 2.5 삭제

- 프로젝트를 영구 삭제하면 `project_keys` 행이 같이 지워진다(CASCADE).
- W의 사본은 다른 곳에도 남는다. Kafka(3일), outbox(발행 3일 뒤 삭제), RDS 자동 백업(7일)에 있고, ai-chat 사본은 ProjectDeleted를 받으면 지운다.
- 따라서 삭제한 프로젝트의 암호문은 **최대 7일 뒤** 어떤 경로로도 풀 수 없게 된다.

---

## 3. 암호문 형식

세 서비스(Java 하나, Python 둘)가 같은 형식을 쓴다.

```text
offset 0        1바이트    형식 = 0x01  (zlib 압축 + AES-256-GCM)
offset 1        12바이트   nonce (매번 랜덤)
offset 13       n바이트    암호문
offset 13 + n   16바이트   GCM 태그
```

- **평문:** 원래 값의 UTF-8 바이트를 zlib(RFC 1950)로 압축한 것이다. Java는 `Deflater`·`Inflater`, Python은 `zlib`을 쓴다. 풀 때 출력 상한은 32MiB로 둬서 압축 폭탄을 막는다.
- **AAD:** `lore-sentry:v1:{project_id}:{subject_id}:{purpose}`의 UTF-8 바이트다. UUID는 소문자로 쓴다. 암호문을 다른 프로젝트·문서·용도로 옮겨 붙이면 태그 검증에서 실패한다.
- **저장:** DB에는 `BYTEA`로, JSON에는 표준 base64(패딩 포함)로 넣는다.
- **압축하는 이유:** 암호문은 압축되지 않는다. 그래서 암호화하면 PostgreSQL TOAST 압축과 Kafka producer의 zstd 압축이 본문에는 더 이상 듣지 않는다. 앱에서 먼저 압축해야 크기가 지금과 비슷하게 유지된다.
- **형식 바이트로 구분:** 첫 바이트 `0x01`은 JSON의 `{`(`0x7B`)와 겹치지 않는다. 전환 기간에 소비자는 첫 바이트를 보고 암호문인지 평문 JSON인지 가린다.
- **라이브러리:** Java는 `Cipher.getInstance("AES/GCM/NoPadding")`과 `GCMParameterSpec(128, nonce)`, Python은 `cryptography`의 `AESGCM`이다. 둘 다 `암호문 ‖ 태그`를 이어서 돌려주므로 위 배치와 같다.
- **언어 간 테스트 벡터:** 고정된 K·nonce·AAD·평문과 기대 바이트를 `docs/events/crypto/test-vectors.json`에 두고, content와 graph-rag·ai-chat 테스트가 모두 이것으로 검증한다.

### 3.1 용도(purpose)와 subject

같은 암호문이 여러 곳을 지나가도 AAD가 같아야 다시 암호화하지 않고 옮길 수 있다. 그래서 AAD에는 저장 위치(테이블·컬럼 이름)를 넣지 않고, 값의 의미만 넣는다.

| purpose | subject_id | 쓰이는 곳 |
|---|---|---|
| `body_json` | document id | content `document.body_json_enc` |
| `body_text` | document id | content `document.body_text_enc`, outbox·Kafka `file.body_text_enc`, ai-chat `summary_queue.body_enc` |
| `version_snapshot` | document id | content `document_versions.snapshot_enc` |
| `draft_left` · `draft_right` · `draft_origin` | 작업본(`refresh_document_drafts`) id | content 작업본 스냅샷 |
| `refresh_input` | request id (`refresh_runs.id`) | S3 `input.enc` |
| `refresh_result` | request id | S3 `result.enc`, content `refresh_results.payload_enc` |
| `summary` | document id | ai-chat `document_summaries.summary_enc` |
| `chat_content` · `chat_quote` · `chat_citations` · `chat_grounding` | message id | ai-chat `chat_messages` |
| `chat_preview` | session id | ai-chat `chat_sessions.preview_enc` |

`version_snapshot`의 subject를 버전 id가 아니라 문서 id로 둔 이유는, 버전 id를 DB가 `returning id`로 만들기 때문이다. 그래서 같은 문서의 다른 버전끼리 암호문을 바꿔치기하는 것은 막지 못한다. 이것은 DB 쓰기 권한을 가진 공격자를 가정한 경우라 범위 밖으로 둔다.

---

## 4. content DB

### 4.1 바뀌는 컬럼

| 테이블 | 지금 | 바뀐 뒤 |
|---|---|---|
| `document` | `body_json JSONB`, `body_text TEXT` | `body_json_enc BYTEA`, `body_text_enc BYTEA` |
| `document` | `body_md TEXT` (`body_json`이 NULL인 옛 행) | 전환 배치가 `body_json`으로 바꾼 뒤 삭제 |
| `document` | `body_sha` (평문 SHA-256. 쓰기만 하고 읽는 곳이 없음) | 삭제 |
| `document` | — | `body_text_mac BYTEA` (본문 변경 감지), `body_text_length INTEGER` (분량 계산) |
| `document_versions` | `snapshot JSONB` | `snapshot_enc BYTEA` |
| `refresh_document_drafts` | `left_snapshot`, `right_snapshot`, `origin_snapshot` JSONB | 각각 `*_enc BYTEA` |
| `refresh_document_drafts` | `matched` (DB가 두 JSONB를 비교하는 생성 컬럼) | 앱이 계산해 넣는 일반 `BOOLEAN` |
| `refresh_results` | `payload JSONB` | `payload_enc BYTEA` (S3 객체 바이트 그대로) |
| `outbox_events` | `payload JSONB`, 안에 `file.body_text` | `payload JSONB` 그대로. 안의 본문만 `file.body_text_enc`, 맨 위에 `wrapped_key`(§5.1) |
| — | — | `project_keys` (§2.2) |

**암호화하지 않는 것:** 제목, `document_properties.text_value`, `document_relations.description`, `memo.body`, 에피소드 이름.

- 제목은 목록 정렬과 제목 검색이 SQL에서 쓴다.
- 나머지는 짧고 원고 본문이 아니다.
- 다만 `document_versions`와 작업본 스냅샷에는 제목·속성·관계까지 통째로 들어 있으므로, 스냅샷 안에서는 함께 암호화된다.
- 이 범위를 넓힐지는 후속으로 판단한다(§11).

### 4.2 MAC 키

본문이 바뀌었는지 판단할 때 암호문끼리는 비교할 수 없다. nonce가 매번 달라 같은 평문도 다른 암호문이 되기 때문이다. 그래서 평문의 MAC을 따로 둔다.

```text
mac_key        = HMAC-SHA256(key = K, msg = "lore-sentry:mac:v1")
body_text_mac  = HMAC-SHA256(key = mac_key, msg = body_text의 UTF-8 바이트)
```

키 없는 SHA-256을 쓰면 "이 문장이 들어 있나"를 추측해서 확인할 수 있다. HMAC은 K 없이는 계산할 수 없다. MAC은 content만 쓴다.

### 4.3 고칠 SQL

DB가 SQL 안에서 평문을 직접 읽는 곳이다.

| 위치 | 지금 | 바꾼 뒤 |
|---|---|---|
| `search/SearchRepository.java:25-37` | `position(lower(:query) in lower(d.body_text))` | 제목 조건은 SQL에 남긴다. 본문은 프로젝트의 활성 문서에서 `body_text_enc`를 읽어 앱에서 풀고 거른다. 순위(제목 일치 0, 제목 포함 1, 본문 포함)와 정렬은 앱에서 같은 규칙으로 다시 만든다 |
| `chat/ChatRepository.java:165`, `193`, `347` | `char_length(d.body_text)` | `body_text_length` |
| `document/DocumentRepository.java:400` | `old.body_text is distinct from new.body_text` | `old.body_text_mac is distinct from new.body_text_mac` |
| `document/DocumentRepository.java:393` | `body_sha = sha256(...)` | 삭제 |
| `V16__refresh_run_files.sql:109` | `matched` 생성 컬럼 | 작업본을 저장하는 코드가 left·right 평문을 비교해 넣는다 |

본문 검색 비용을 어림해 보면, 300화 × 5천 자 소설은 약 150만 자다. 압축한 암호문으로 2MB 남짓을 읽고 푸는 데 수십 ms다.

### 4.4 코드 경계

- `ProjectKeys`: W를 만들고 저장하고 풀며, K를 캐시한다. KMS를 부르는 유일한 곳이다.
- `ManuscriptCipher`: `encrypt(projectId, subjectId, purpose, bytes)`와 `decrypt(...)`. §3의 형식을 구현한다.
- Repository가 넣기 직전에 암호화하고, 꺼낸 직후에 푼다. 서비스와 컨트롤러는 평문만 다룬다.
- 의존성: `software.amazon.awssdk:kms`. 캐시는 만료 시각을 두는 맵이나 Caffeine을 쓴다. HMAC과 AES-GCM은 JDK 21 표준 API로 된다.

---

## 5. 서비스 사이 전달

### 5.1 Kafka FileChanged → `content.project.changed.v2`

content는 `document.body_text_enc`를 **풀지 않고** 그대로 싣는다. AAD가 같으므로(§3.1) 받는 쪽에서 그대로 풀린다.

```json
{
  "project_id": "…",
  "file_id": "…",
  "change_type": "UPDATED",
  "changed_fields": ["BODY"],
  "wrapped_key": "AQIDAHh…",
  "file": { "title": "도깨비 학살", "folder_code": "EVENT", "body_text_enc": "AYx3…" },
  "relations": [ { "relation_id": "…", "relation_key": "related_character", "other_file_id": "…" } ]
}
```

- **outbox도 해결된다.** outbox 행이 이 payload이므로 `outbox_events`에 평문 본문이 남지 않는다. relay는 payload를 파싱하지 않으므로 바뀌지 않는다.
- **받는 쪽은 필요할 때만 푼다.** id, `change_type`, `changed_fields`는 평문이라 건너뛸지는 복호화 없이 판단한다. graph-rag와 ai-chat-summarizer는 본문이 필요할 때만 W로 K를 받아 푼다.
- **DLQ도 암호문이다.** DLQ는 원본 메시지를 그대로 옮긴다.
- **새 topic이 필요하다.** `body_text`를 없애고 `body_text_enc`를 넣는 것은 필드 삭제와 타입 변경이라, `KAFKA_DESIGN.md` Schema versioning 규칙에 따라 v2 topic으로 옮긴다. 절차는 거기 적힌 "새 버전 topic으로 옮기는 순서"를 따른다. v1에서는 발행을 멈출 때까지 평문이 나가고, 멈춘 뒤 보존 기간(3일)이 지나면 topic을 지운다.
- **크기 한도를 올린다.** 100만 자 본문은 UTF-8로 약 3MB이고, zlib을 거치면 대개 그 절반 안팎, base64로 다시 1.33배가 된다. 그런데 압축이 거의 안 되는 최악의 경우에는 약 4MB가 되어 지금 한도(4MiB)에 걸린다. 그래서 v2 topic과 그 DLQ의 `max.message.bytes`, producer의 `max.request.size`를 6MiB(`6291456`)로 둔다. `retention.bytes`는 그대로라 broker 디스크 상한은 바뀌지 않는다.

### 5.2 S3 최신화 입력과 결과

입력과 결과는 객체 전체를 암호화한다. 결과 파일에는 본문이 여러 필드(제안, 사실, 근거 인용)에 흩어져 있어서, 필드 단위로 암호화하면 빠뜨리기 쉽기 때문이다.

| | 지금 | 바뀐 뒤 |
|---|---|---|
| 입력 | `input.json` (refresh-input-v2 JSON) | `input.enc` = 같은 JSON을 K로 암호화(purpose `refresh_input`) |
| 결과 | `result.json` (refresh-result-v3 JSON) | `result.enc` = 같은 JSON을 같은 K로 암호화(purpose `refresh_result`) |
| GraphRefreshRequested | `location`, `input_fingerprint` 등 | 선택 필드 `wrapped_key` 추가. 선택 필드 추가라 v1 그대로 쓴다 |
| `input_fingerprint` | 객체 바이트의 sha256 | 그대로. 객체 바이트(암호문)의 sha256 |
| `refresh_results` | 결과 JSON | 객체 바이트 그대로(`payload_enc`)와 그 sha256 |

- **content:** 문서 본문을 풀어서 지금처럼 입력 JSON을 만든 뒤, 통째로 암호화해 올린다.
- **graph-rag:** 객체를 받아 첫 바이트가 `0x01`이면 풀고, 그 뒤는 지금 코드로 파싱한다. 결과는 같은 K로 암호화해 올린다. 바뀌는 곳은 S3 어댑터(`adapters/refresh_artifacts.py`)의 읽기·쓰기다.
- **버킷:** SSE-S3 그대로 둔다. 본문은 이미 앱에서 암호화돼 있다.

### 5.3 content 내부 HTTP

graph-rag의 관계 문서 조회와 ai-chat의 `/internal/projects/{id}/chat-*`는 클러스터 안에서 평문으로 오간다. 저장이 아니라 전송 구간이라 이 설계의 범위 밖이다(§11). 다만 ai-chat이 자기 DB에 암호화해 저장하려면 K가 있어야 하므로, `chat-snapshot` 응답에 `wrapped_key`를 선택 필드로 더한다.

---

## 6. ai-chat

- **`project_keys` 사본을 둔다.** FileChanged v2와 `chat-snapshot` 응답에서 받은 W를 저장하고, ProjectDeleted를 받으면 지운다.
- **암호화하는 값:**
  - `summary_queue.body`: 받은 `body_text_enc`를 그대로 `body_enc BYTEA`에 저장하고, 요약할 때 푼다.
  - `document_summaries.summary`
  - `chat_messages`의 `content_md`, `quote`, `citations`, `grounding`
  - `chat_sessions.preview`
- **암호화하지 않는 값:** 채팅 세션 제목. content의 제목과 같은 이유다.
- **KMS 권한:** ai-chat은 K로 암호화만 하고 새 키는 만들지 않으므로 `Decrypt` 권한만 있으면 된다.
- **고칠 SQL:** `app/store.py:633-640`의 채팅 검색이 `content_md ilike`와 `quote->>'text' ilike`, `citations`의 `title ilike`를 쓴다. 세션 제목 조건만 SQL에 남기고, 나머지는 앱에서 풀어서 거른다.

---

## 7. KMS와 IAM

### 7.1 역할

| 서비스 | ServiceAccount | IAM 역할 | KMS 권한 |
|---|---|---|---|
| content | `content-api` | `lore-sentry-content-role` (있음) | `GenerateDataKey`, `Decrypt` |
| graph-rag | `graph-rag-api` | `lore-sentry-graph-rag-role` (있음) | `Decrypt` |
| ai-chat, ai-chat-summarizer | **새로 만든다** | `lore-sentry-ai-chat-role` (**새로 만든다**) | `Decrypt` |
| 관리자 | — | `lore-sentry-kms-admin` (**새로 만든다**) | 키 관리만. `Decrypt` 없음 |

키 정책이 역할 ARN을 직접 지정하므로, 서비스 역할에 IAM 정책을 따로 붙이지 않아도 된다.

### 7.2 키 정책

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ContentCreatesAndOpens",
      "Effect": "Allow",
      "Principal": { "AWS": "arn:aws:iam::<AWS_ACCOUNT_ID>:role/lore-sentry-content-role" },
      "Action": ["kms:GenerateDataKey", "kms:Decrypt"],
      "Resource": "*",
      "Condition": { "StringLike": { "kms:EncryptionContext:project_id": "*" } }
    },
    {
      "Sid": "ConsumersOpenOnly",
      "Effect": "Allow",
      "Principal": { "AWS": [
        "arn:aws:iam::<AWS_ACCOUNT_ID>:role/lore-sentry-graph-rag-role",
        "arn:aws:iam::<AWS_ACCOUNT_ID>:role/lore-sentry-ai-chat-role"
      ] },
      "Action": "kms:Decrypt",
      "Resource": "*",
      "Condition": { "StringLike": { "kms:EncryptionContext:project_id": "*" } }
    },
    {
      "Sid": "AdminManagesButCannotUse",
      "Effect": "Allow",
      "Principal": { "AWS": "arn:aws:iam::<AWS_ACCOUNT_ID>:role/lore-sentry-kms-admin" },
      "Action": [
        "kms:Describe*", "kms:Get*", "kms:List*",
        "kms:PutKeyPolicy", "kms:EnableKeyRotation", "kms:DisableKeyRotation",
        "kms:TagResource", "kms:UntagResource",
        "kms:ScheduleKeyDeletion", "kms:CancelKeyDeletion"
      ],
      "Resource": "*"
    }
  ]
}
```

- **계정 루트(`:root`)에 `kms:*`를 주는 기본 문구는 넣지 않는다.** 그 문구가 있으면 키 정책이 IAM에 권한을 위임하게 되어, `lorekeeper-owner`(PowerUser) 누구나 IAM 권한만으로 `Decrypt`를 부를 수 있다.
- **관리 역할이 없어지면 키를 관리할 수 없게 된다.** 이 경우 AWS Support를 거쳐야 하므로, 관리 역할은 지우지 않고 두 사람 이상이 맡을 수 있게 둔다.
- **남는 위험:** `IAMFullAccess`가 있으면 관리 역할의 신뢰 정책을 고쳐 그 역할을 맡은 뒤, 키 정책을 바꿔 자기에게 `Decrypt`를 줄 수 있다. 이 경로는 막히지 않고 기록으로만 남는다(§7.3).

### 7.3 기록과 비용

- **기록:** `Decrypt`와 `GenerateDataKey` 호출은 CloudTrail 이벤트 기록에 남는다. 호출 역할과 `project_id`가 함께 보인다. 세 역할이 아닌 주체의 `Decrypt`, `PutKeyPolicy`, 관리 역할의 신뢰 정책 변경에 알림을 거는 것은 trail 구성과 함께 정한다(§11).
- **네트워크:** pod는 이미 NAT로 외부(OpenAI)에 나가므로 KMS에도 닿는다. KMS VPC 엔드포인트는 선택이다.
- **비용:** 키 하나당 월 1달러에 교체분이 조금 더해진다. 요청은 1만 건당 0.03달러인데, 캐시가 있어 pod 재시작이나 캐시 만료 때만 호출하므로 무시할 수준이다.

---

## 8. 전환 순서

**모든 단계를 dev 클러스터에서 먼저 한다.**

1. **인프라.** 마스터 키와 별칭을 만들고, 키 정책과 자동 교체를 설정한다. 관리 역할, ai-chat ServiceAccount·역할·Pod Identity 연결도 만든다.
2. **content 확장.** Flyway로 `project_keys`, `*_enc`, `body_text_mac`, `body_text_length`를 추가한다(모두 NULL 허용). 새 프로젝트는 키를 만든다. 문서 저장은 평문과 암호문에 함께 쓴다.
3. **content 배치.**
   - 기존 프로젝트에 키를 만든다.
   - `body_md`만 있는 옛 행을 `body_json`으로 바꾼다.
   - 기존 `document`, `document_versions`, 작업본, `refresh_results` 행을 암호화해 채운다.
4. **content 읽기 전환.** 읽기를 `*_enc`로 바꾸고, §4.3의 SQL을 고친다.
5. **소비자 준비.** graph-rag와 ai-chat-summarizer가 v2 topic과 암호화된 S3 객체(`0x01`)를 둘 다 읽을 수 있게 배포한다. graph-rag는 이 시점부터 결과를 암호화해 올린다.
6. **content 발행 전환.**
   - S3 입력을 암호화해 올리고, GraphRefreshRequested에 `wrapped_key`를 싣는다.
   - FileChanged를 v2로 옮긴다(`KAFKA_DESIGN.md`의 절차).
   - v1 발행을 멈추고, 보존 기간이 지나면 v1 topic과 DLQ를 지운다.
7. **content 정리.**
   - 평문 쓰기를 멈추고 평문 컬럼과 `body_sha`, `body_md`를 지운다.
   - 해당 테이블에 `VACUUM FULL`(또는 pg_repack)을 돌려 옛 튜플을 없앤다. `VACUUM FULL`은 테이블 잠금을 잡으므로 사용량이 적은 시간에 한다.
   - 자동 백업은 7일 뒤 사라진다. 수동 스냅샷이 있으면 지운다.
8. **ai-chat 저장 암호화.** §6을 적용하고, 2~7과 같은 순서로 확장·배치·전환·정리한다.

---

## 9. 확인

- **DB:** `select body_text_enc from document limit 1`의 결과가 `\x01…`로 시작한다. 평문 컬럼은 없다.
- **덤프:** 예시 프로젝트(`SampleProjectService`)의 본문 한 문장을 카나리아로 쓴다. `pg_dump --data-only` 결과에서 그 문장이 0건이어야 한다. 사용자 원고는 열어 보지 않는다.
- **Kafka:** v2 메시지 하나를 읽었을 때 `body_text_enc`만 있다. DLQ도 확인한다.
- **S3:** `input.enc`, `result.enc`의 첫 바이트가 `0x01`이다.
- **KMS:** CloudTrail에서 `Decrypt`를 부른 주체가 세 역할뿐이다.
- **형식:** content, graph-rag, ai-chat 테스트가 같은 테스트 벡터를 통과한다.

---

## 10. 막는 것과 못 막는 것

| 막는 것 | 못 막는 것 |
|---|---|
| DB 직접 접속(psql, Telepresence) | 실행 중인 서비스 pod 장악(메모리의 K, 역할 자격 증명) |
| DB 덤프, RDS 백업·스냅샷 유출 | GitOps 쓰기나 Argo CD admin 권한으로 해당 ServiceAccount를 쓰는 코드를 배포하는 것 |
| SQL injection으로 행을 읽는 것 | `IAMFullAccess`로 관리 역할을 거쳐 키 정책을 바꾸는 것(기록은 남음) |
| Kafka 메시지, DLQ, broker 디스크 | OpenAI로 보내는 평문 |
| S3 최신화 객체 | 클러스터 안 HTTP 구간의 평문 |

---

## 11. 범위 밖과 후속

- **암호화 범위 확장:** 제목, 속성 값, 관계 설명, 메모, 에피소드 이름, 채팅 세션 제목. 넓히면 목록 정렬과 제목 검색도 앱으로 옮겨야 한다.
- **전송 구간:** Kafka 리스너 TLS(`tls: false`), 클러스터 안 HTTP, RDS 접속 SSL.
- **CloudTrail:** trail과 알림 규칙.
- **로그:** 본문, 프롬프트, LLM 응답을 로그에 찍지 않는다는 규칙과 그 점검.
- **프로젝트 키 교체:** 하지 않기로 했다(§2.4). 나중에 필요해지면 형식 바이트를 `0x02`로 올리고 키 버전을 넣는다.
