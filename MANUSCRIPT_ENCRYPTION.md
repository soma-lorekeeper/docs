# 원고 암호화 — 프로젝트 키 봉투 암호화

> 최신화: 2026-10-31
> 대상: content · graph-rag · ai-chat이 저장하거나 서로 전달하는 원고 본문과 그 사본
> 상태: **구현·배포 완료.** dev 2026-10-31 01:46, prod 2026-10-31 02:29(KST)에 전환했고, 평문 칼럼·토픽·객체까지 지웠다(§8). 남은 것은 §10.

## 0. 요약

- 원고 **본문**을 **프로젝트마다 하나인 키**로 암호화한다. 서비스마다 키를 두지 않는다.
- 범위는 본문과 그 사본이다. 문서 본문, 버전·작업본 스냅샷, 최신화 입력·중간 결과·결과, FileChanged 이벤트의 본문, ai-chat 요약 큐와 요약 테이블이 여기에 들어간다.
- 제목, 속성, 관계, 메모, 채팅 메시지는 암호화하지 않는다(§10).
- content가 본문을 처음 저장할 때 암호화한다. 같은 암호문이 DB → outbox → Kafka → ai-chat으로 **다시 암호화하지 않고 그대로** 간다.
- S3 최신화 객체는 같은 프로젝트 키로 객체 전체를 암호화한다. 원문이 필요한 서비스만 그 자리에서 푼다.
- 키는 두 단계다. KMS 마스터 키 하나가 프로젝트 키를 감싸고, DB와 이벤트에는 **감싼 프로젝트 키만** 둔다. 마스터 키는 KMS 밖으로 나오지 않는다.
- 감싼 키를 풀 수 있는 것은 content · graph-rag · ai-chat의 Pod Identity 역할뿐이다. 키 관리자도 키를 쓸 수 없다.
- 프로젝트 키는 바꾸지 않는다. 마스터 키는 KMS 자동 교체(365일)를 켰다.

```text
                     ┌──────── KMS: 마스터 키 (밖으로 나오지 않음) ────────┐
                     │  GenerateDataKey (content만) · Decrypt (3개 역할만)  │
                     └──────────────────────────────────────────────────────┘
                                ▲ 감싼 키 W를 보내고 평문 키 K를 받는다
                                │
작가 ─저장─> content ─K로 암호화─> content DB (암호문 C, 감싼 키 W)
                │
                ├─ outbox → Kafka v2 { wrapped_key: W, file.body_text_enc: C } ─> graph-rag, ai-chat
                └─ S3 input.json = K로 암호화한 입력 전체 ─> graph-rag ─ context/batch/result도 K로 ─> content
```

---

## 1. 전환 전 상태

| 위치 | 원고가 있던 곳 | 전환 전 보호 수준 |
|---|---|---|
| content `document` | `body_json`, `body_text`, `body_md`, `body_sha`(평문 해시) | RDS 디스크 암호화(`alias/aws/rds`)뿐. SQL로 읽으면 평문 |
| content `document_versions` | `snapshot` | 〃 |
| content `refresh_document_drafts` | `left_snapshot`, `right_snapshot`, `origin_snapshot` | 〃 |
| content `refresh_results` | `payload` | 〃 |
| content `outbox_events` | FileChanged `file.body_text` | 〃 |
| Kafka `content.project.changed.v1`와 DLQ | `file.body_text` | `tls: false`, EBS 암호화 |
| S3 `refresh/{project}/{request}/` | `input.json`, `context.json`, `batch-NNNN.json`, `result.json` | SSE-S3 |
| ai-chat `summary_queue` | `body` | RDS 디스크 암호화뿐 |
| ai-chat `document_summaries` | `summary`(500자 이하 본문은 원문을 그대로 복사) | 〃 |

---

## 2. 키

| 이름 | 개수 | 어디에 있나 | 하는 일 |
|---|---|---|---|
| **마스터 키** (KMS 대칭 키) | 환경마다 1개: `alias/lore-sentry-manuscript`, `alias/lore-sentry-manuscript-dev` | KMS 안 | 프로젝트 키를 감싸고 푼다 |
| **프로젝트 키** K (AES-256) | 프로젝트마다 1개 | 각 서비스 **메모리 캐시에만**(10분, 1,000개) | 원고를 암호화하고 푼다 |
| **감싼 프로젝트 키** W (약 180바이트) | 프로젝트마다 1개 | content `project_keys`, FileChanged·GraphRefreshRequested의 `wrapped_key`, ai-chat 행마다(`wrapped_key`) | KMS만 풀 수 있다 |

- **만들기:** content가 프로젝트에 키가 없으면 처음 암호화할 때 만든다. 호출은 `GenerateDataKey(KeySpec=AES_256, EncryptionContext={"project_id": …})`이다.
  - 만드는 일은 호출한 트랜잭션 안에서 일어난다.
  - 키는 커밋된 뒤에만 캐시에 들어간다. 롤백되면 키 행과 데이터가 함께 사라지므로 어긋나지 않는다.
- **풀기:** `Decrypt(KeyId=alias, EncryptionContext={"project_id": …})`. KMS 오류는 일시 장애로 다룬다.
- **교체:** 프로젝트 키는 바꾸지 않는다. 마스터 키는 KMS가 옛 버전을 보관하므로 자동 교체해도 코드와 데이터가 바뀌지 않는다.
- **삭제:** 프로젝트를 영구 삭제하면 `project_keys` 행이 CASCADE로 지워진다. ai-chat 사본은 ProjectDeleted를 받으면 지운다.
  - RDS 자동 백업에는 W가 최대 7일 남는다.
  - 그래서 삭제한 프로젝트의 암호문은 최대 7일 뒤부터 어떤 경로로도 풀 수 없다.

---

## 3. 암호문 형식

세 서비스(Java 하나, Python 둘)가 같은 형식을 쓴다.

```text
offset 0        1바이트    형식 = 0x01  (zlib + AES-256-GCM)
offset 1        12바이트   nonce (매번 랜덤)
offset 13       n바이트    암호문
offset 13 + n   16바이트   GCM 태그
```

- **평문:** 값의 UTF-8 바이트를 zlib(RFC 1950)로 압축한 것이다. 풀 때 출력 상한은 32MiB다.
- **AAD:** `lore-sentry:v1:{project_id}:{subject_id}:{purpose}`이고, UUID는 소문자로 쓴다. AAD에 저장 위치(테이블·칼럼)를 넣지 않는다. 그래서 같은 암호문을 다시 암호화하지 않고 DB, Kafka, ai-chat으로 옮길 수 있다.
- **저장:** DB에는 `BYTEA`로, JSON에는 표준 base64로 넣는다.
- **형식 구분:** 첫 바이트 `0x01`은 JSON의 `{`(`0x7B`)와 겹치지 않는다. 그래서 전환 기간에 평문 JSON과 암호문을 첫 바이트로 가렸다.
- **언어 간 테스트 벡터:** `manuscript-envelope-vectors.json`을 세 저장소의 테스트에 같은 파일로 두었다. Java가 잠근 것을 Python이 푸는 것도 확인했다.
- **본문 변경 감지:** `mac_key = HMAC-SHA256(K, "lore-sentry:mac:v1")`를 만들고 `body_text_mac = HMAC-SHA256(mac_key, body_text)`로 계산한다. content만 쓴다.

| purpose | subject_id | 쓰이는 곳 |
|---|---|---|
| `body_json` | 문서 id | content `document.body_json_enc` |
| `body_md` | 문서 id | content `document.body_md_enc`(Markdown 레거시 행. 현재 dev·prod 모두 0건) |
| `body_text` | 문서 id | content `document.body_text_enc`, FileChanged `file.body_text_enc`, ai-chat `summary_queue.body_enc` |
| `version_snapshot` | 문서 id | content `document_versions.snapshot_enc` |
| `draft_left` · `draft_right` · `draft_origin` | 작업본 id | content `refresh_document_drafts.*_snapshot_enc` |
| `refresh_input` | 요청 id | S3 `input.json` |
| `refresh_context` | 요청 id | S3 `context.json`(graph-rag) |
| `refresh_batch` | 요청 id | S3 `batch-NNNN.json`(graph-rag 중간 결과) |
| `refresh_result` | 요청 id | S3 `result.json`, content `refresh_results.payload_enc` |
| `summary` | 문서 id | ai-chat `document_summaries.summary_enc` |

S3 `request.json`은 id, 위치, 설정만 담고 본문이 없어서 평문 JSON으로 둔다.

---

## 4. content

**마이그레이션**

- **V18:** `project_keys`와 `*_enc` 칼럼, `body_text_mac`, `body_text_length`, `is_matched`를 추가한다.
- **V19 (Java 마이그레이션):** 웹 서버가 뜨기 전에 기존 데이터를 암호화한다.
  - 모든 프로젝트에 키를 만든다.
  - 본문, 버전, 작업본, 결과, outbox FileChanged payload를 암호화한다.
  - 행마다 "암호화 후 다시 풀어 원문과 같은지" 확인하고, 하나라도 다르면 전체를 되돌린다.
  - 끝으로 NOT NULL 제약을 건다.
- **V20:** 평문 칼럼(`body_json`, `body_text`, `body_md`, `body_sha`, `snapshot`, 작업본 스냅샷 3개, `payload`)과 생성 칼럼 `matched`를 지운다. 작업본 CHECK 제약은 암호문 칼럼 기준으로 다시 만든다.

**코드**

- Repository가 넣기 직전에 암호화하고, 꺼낸 직후에 푼다(`manuscript` 패키지의 `Manuscripts`, `ProjectKeys`, `Envelope`).
- 문서와 작업본의 id는 `uuidv7()`로 먼저 받는다. AAD에 id가 들어가기 때문이다.

**DB가 평문을 읽던 SQL을 바꾼 곳**

| 기능 | 바뀐 방식 |
|---|---|
| 본문 검색 | 프로젝트 문서를 풀어 앱에서 거른다. 순위는 같다 |
| 챗 분량 | `body_text_length` 칼럼을 쓴다 |
| 본문 변경 감지 | `body_text_mac`끼리 비교한다 |
| 작업본 `matched` | 앱이 left·right JSON 트리를 비교해 `is_matched`에 넣는다 |

**파일 이벤트 재발행**

`POST /internal/admin/file-events/replay?include_trashed=true`는 휴지통 프로젝트의 문서까지 재발행한다. ai-chat 요약을 다시 암호화할 때 이 경로를 썼다(§6).

---

## 5. 서비스 사이 전달

### 5.1 Kafka

- FileChanged와 ProjectDeleted는 **`content.project.changed.v2`**(6 partition, `max.message.bytes` 6MiB)로만 나간다.
  - v2 payload는 v1에서 `file.body_text`를 빼고, `file.body_text_enc`와 맨 위 `wrapped_key`(파일이 없으면 null)를 더한 것이다.
- content는 DB의 `body_text_enc`를 풀지 않고 그대로 싣는다. 그래서 outbox에도 평문 본문이 남지 않는다.
- graph-rag는 본문을 쓰지 않으므로 v2를 받아도 KMS를 부르지 않는다. ai-chat 요약기는 암호문을 그대로 큐에 넣는다.
- DLQ는 `content.project.changed.v2.graph-rag.dlq` 하나다.
- **v1 토픽과 DLQ 2개는 지웠다**(gitops #22, #24). 그 안의 평문 본문도 함께 사라졌다.

### 5.2 S3 최신화 객체

- **content:** 문서를 풀어 입력 JSON을 만든 뒤 통째로 암호화해 올린다. GraphRefreshRequested에는 `wrapped_key`를 싣는다. `input_fingerprint`는 올린 객체 바이트(암호문)의 sha256이다.
- **graph-rag:** 지문을 확인한 뒤 풀어서 읽는다. context·batch·result는 같은 키로 암호화해 쓴다. 저장해 둔 것과 새것이 같은지는 푼 내용으로 비교한다.
- **content가 결과를 읽을 때:** 첫 바이트가 `0x01`이면 풀어서 파싱한다. DB에는 다시 암호화해 `payload_enc`로 남긴다.
- **버킷:** SSE-S3 그대로다. 전환 전 평문 객체는 지웠다(§8).

---

## 6. ai-chat

- **`summary_queue`:** 받은 `body_enc`와 `wrapped_key`를 그대로 저장하고, 요약 직전에만 푼다(V9). V11에서 평문 `body` 칼럼을 지웠다.
- **`document_summaries`:** 모델 요약과 500자 이하 원문 복사를 모두 `summary_enc` + `wrapped_key`로 저장한다(V10). V11에서 평문 `summary` 칼럼을 지웠다.
- **기존 요약 재암호화:** content 재발행(휴지통 포함)으로 문서마다 키가 실린 이벤트가 들어왔다. 본문이 그대로인 요약은 모델을 부르지 않고 그 키로 다시 암호화했다.
  - dev는 252개, prod는 573개다.
  - V11은 평문이 하나라도 남아 있으면 실행을 거부한다.
- **chat API:** 지도를 만들 때 요약을 푼다. KMS 장애나 복호화 실패가 생기면 그 요약만 빼고 답변은 계속한다.
- **구독:** 요약기는 v2만 구독한다. v2 계약을 따르지 않는 이벤트(암호문 본문이 없는 FileChanged)는 건너뛴다.

---

## 7. KMS와 IAM

`loresentry-gitops/bootstrap/aws/manuscript-key.sh <dev|prod>`가 아래를 한 번에 만든다(여러 번 실행해도 결과가 같다). 키 정책 원본은 같은 폴더의 `manuscript-key-policy.json`이다.

| 서비스 | ServiceAccount | IAM 역할(prod / dev) | KMS 권한 |
|---|---|---|---|
| content | `content-api` | `lore-sentry-content-role` / `lore-sentry-dev-content-role` | `GenerateDataKey`, `Decrypt` |
| graph-rag | `graph-rag-api` | `lore-sentry-graph-rag-role` / `lore-sentry-dev-graph-rag-role` | `Decrypt` |
| ai-chat API, 요약기 | `ai-chat-api`, `ai-chat-summarizer` | `lore-sentry-ai-chat-role` / `lore-sentry-dev-ai-chat-role` | `Decrypt` |

- **모든 허용에 조건이 붙는다.** 요청에 `kms:EncryptionContext:project_id`가 있어야 한다.
- **키 관리:** 계정 루트(`:root`)를 principal로 두되, `aws:PrincipalArn`이 SSO 역할 `AWSReservedSSO_myisb_IsbUsersPS_*`·`IsbAdminsPS_*`일 때만 관리 동작을 허용한다.
  - 관리 권한에는 `Decrypt`·`GenerateDataKey`가 없다. 실제로 SSO 역할로 `GenerateDataKey`를 부르면 `AccessDenied`가 난다.
  - `lorekeeper-owner`(PowerUser) IAM 사용자는 IAM 권한만으로는 이 키를 쓸 수 없다.
- **설정:** `manuscript-key` ConfigMap(`key-id`, `region`)을 base에 두고, dev overlay는 별칭만 바꾼다. 네 Deployment가 `MANUSCRIPT_KEY_ID`·`MANUSCRIPT_KEY_REGION`을 받는다.
- **감사 기록(CloudTrail, 전환 후):** 다른 주체의 호출은 없었다.

| 환경 | GenerateDataKey | Decrypt |
|---|---|---|
| dev | content 역할만 | ai-chat·graph-rag 역할만 |
| prod | content 역할만(38회) | ai-chat 역할 |

---

## 8. 전환 기록

| 순서 | dev | prod |
|---|---|---|
| 1. 키·역할·Pod Identity | 스크립트 실행 | 스크립트 실행 |
| 2. v2 토픽·설정·환경 변수 | gitops #20, #21(overlay) | gitops #23(base로 이동) |
| 3. 소비자(v1+v2 구독) | graph-rag #8, ai-chat #19 #20 | graph-rag #10, ai-chat #23 |
| 4. content 전환(V18·V19, `Recreate`) | content #30 #32 | content #33 |
| 5. 검증 | §9 | §9 |
| 6. 요약 재암호화(재발행) | 문서 274개 | 문서 588개(휴지통 포함) |
| 7. 평문 제거 | content #31(V20), graph-rag #9(v2만), ai-chat #22(V11), v1 토픽 삭제(#22) | content #34, graph-rag #11, ai-chat #24, v1 토픽 삭제·Recreate 해제(#24) |
| 8. `VACUUM FULL` | 본문 관련 테이블 7개 | 같은 7개 |
| 9. S3 전환 전 객체 | 없음(수명 규칙으로 이미 만료) | 20개 삭제(버전 관리 꺼짐) |

- **V19 처리량:**
  - dev: 프로젝트 12, 문서 252, 버전 252, outbox 504건, 3.4초
  - prod: 프로젝트 36, 문서 592, 버전 632, 작업본 5, 결과 23, outbox 216건, 7.1초
- **content 중단 시간:** `Recreate`로 배포했으므로 전환 배포와 V20 배포 때 각각 30~40초 내려갔다.

## 9. 검증 결과

**전수 대조**

평문 칼럼이 남아 있던 단계에서 문서마다 두 값을 비교했다.
- API가 복호화해 돌려준 본문 JSON과 분석용 평문
- DB에 남아 있던 원래 평문

| 환경 | 문서 | 버전 | 불일치 |
|---|---|---|---|
| dev | 252 / 252 일치 | 252 / 252 일치 | 0 |
| prod | 활성 문서 473 / 473 일치 | 628 / 628 일치 | 0 |

- prod 휴지통 문서 119개는 API로 읽을 수 없다.
  - 대신 V19의 행별 검사(암호화 후 다시 풀어 원문과 비교)를 통과했다.
  - 마이그레이션이 기록한 길이가 592개 모두 실제 평문과 일치했다.

**기능 검증**

- **dev:** 실제 API로 한 바퀴 돌렸다. 샘플 프로젝트 생성, 저장·재조회, 본문 검색, 버전, 그래프 최신화, AI 챗이 모두 통과했다. 평문 칼럼을 지운 뒤에도 다시 통과했다.
- **prod:** 가상 사용자로 테스트 프로젝트를 만들어 확인하고 지웠다. 저장·재조회, 검색, 버전, 분석용 평문, 챗 스냅샷이 통과했다. 이것도 평문 칼럼 삭제 전후에 각각 확인했다.

**암호문만 남았는지**

| 대상 | 결과 |
|---|---|
| Kafka v2 | dev 44건, prod 전부 `body_text_enc`와 `wrapped_key`. 평문 본문 0건 |
| outbox | FileChanged 평문 본문 0건 |
| S3 | input·context·result 첫 바이트 `0x01` |
| `refresh_results` | 암호문 |
| DB 칼럼 | 원고 관련 테이블에 text·jsonb 칼럼은 S3 위치(`result_bucket`, `result_key`), 요약 큐의 `title`, 요약 `fingerprint`만 남음 |

## 10. 남은 것과 범위 밖

- **RDS 자동 백업:** 전환 전 평문이 최대 7일(prod 약 2026-11-07까지) 남는다. 바로 지우려면 보존 기간을 0으로 내렸다 올려야 하고, 그동안 특정 시점 복구를 쓸 수 없다. 아직 하지 않았다.
- **암호화하지 않은 값:**
  - content: 제목, 속성 값, 관계 설명, 메모, 에피소드 이름
  - ai-chat: `chat_messages`(답변, 인용, 근거), `chat_sessions.preview`, `summary_queue.title`, 요약 `fingerprint`(문장별 SHA-1 앞부분)
- **전송 구간:** Kafka 리스너 TLS(`tls: false`), 클러스터 안 HTTP(graph-rag·ai-chat이 content에서 본문을 받는 경로), OpenAI로 보내는 평문
- **로그:** 본문이 로그(Loki)에 찍히는지는 점검하지 않았다.
- **막지 못하는 것:**
  - 실행 중인 pod 장악(메모리의 K, 역할 자격 증명)
  - GitOps 쓰기·Argo CD admin으로 해당 ServiceAccount를 쓰는 코드를 배포하는 것
  - IAM 권한으로 관리 역할의 신뢰 정책을 고쳐 키 정책을 바꾸는 것. 이 경우 CloudTrail에 남는다.
- **KMS 장애:**
  - content는 키가 캐시에 없는 프로젝트의 본문을 읽지 못한다.
  - ai-chat 요약기는 그 행을 1분 뒤로 미룬다.
  - chat API는 그 요약을 빼고 답한다. 단, 키가 캐시에 없으면 그 턴은 KMS 재시도 때문에 최대 약 21초 늦어진다.
- **새 서비스가 본문을 읽어야 할 때:** 그 서비스의 역할을 키 정책의 Decrypt 목록에 넣고 `manuscript-key.sh`를 다시 실행한다.
