# 로그 스키마 — ECS 필드·레벨·마스킹·data stream 규약

> 최신화: 2026-10-31
> 대상: 클러스터 안 모든 파드 로그와 AWS 관리형 리소스(ALB·CloudFront·RDS·Neptune·EKS) 로그
> 상태: **규약 확정.** 로그 수집 스택(ECK)은 dev부터 배포 중이다. 앱이 이 규약대로 로그를 내는 작업은 다음 단계에서 한다.
> 관련: 매니페스트와 index template은 `loresentry-gitops/logging/`(`base/elasticsearch/logs-custom.json`)

클러스터 안 모든 로그와 AWS 관리형 리소스 로그를 Elasticsearch에 넣을 때의 규약이다. 로그를 내보내는 앱, 가공하는
Logstash, 저장하는 ES index template이 모두 이 문서를 따른다.

- 기준: **ECS 8.11**. Spring Boot 4.1.1의 ECS 출력이 `ecs.version: 8.11`이고, 여기서 쓰는 ECS 필드는 8.11에 모두 있다.
- ECS에 없는 우리 필드는 `loresentry.*` 아래에 둔다. ECS는 자체 필드를 회사 이름 아래에 두라고 권한다.
- 이 문서를 바꿀 때는 gitops의 index template과 Logstash 설정도 같은 시점에 고친다. 맨 아래 [바꾸는 절차](#바꾸는-절차)를
  따른다.

## 1. 흐름과 책임

```text
앱·Kafka·Valkey 등 파드 stdout ─▶ Filebeat(DaemonSet) ─┐
AWS(ALB·CloudFront·RDS·Neptune·EKS) ─▶ S3·CloudWatch ─▶ Filebeat(aws-logs) ─┤
                                                         ▼
                              Logstash: 파싱 · 정규화 · 마스킹 · data stream 선택
                                     ├─▶ Elasticsearch (30일, 길이 상한 적용)
                                     └─▶ S3 보관 (1년, 마스킹된 전문)
```

| 단계 | 하는 일 | 하지 않는 일 |
| --- | --- | --- |
| 앱 | 아래 [앱 로그 규약](#5-앱-로그-규약)대로 ECS JSON 한 줄씩 stdout에 쓴다. 금지 항목을 찍지 않는다 | 마스킹을 Logstash에 기대지 않는다 |
| Filebeat | 줄 읽기, CRI 해제, 여러 줄 묶기, `kubernetes.*` 붙이기 | 파싱·가공 |
| Logstash | 파싱, `log.level` 정규화, 필수 필드 채우기, 마스킹, 소음 제거, data stream 선택 | — |
| ES | 필드 타입(`logs@custom`), 보존 기간, 거부된 문서 보관(failure store) | ingest pipeline을 두지 않는다 |

## 2. 공통 필드

### 2.1 모든 로그 (항상 있음)

출처와 관계없이 모든 로그 문서에 있다. 앱이 보내지 않은 값은 Logstash가 채운다.

| 필드 | 타입 | 값 | 비어 있을 때 Logstash가 채우는 값 |
| --- | --- | --- | --- |
| `@timestamp` | date | 이벤트 시각(UTC) | 컨테이너 런타임이 줄마다 붙인 시각(CRI, 나노초). AWS 로그는 원본 시각 |
| `message` | match_only_text | 사람이 읽는 본문 | 원래 줄 |
| `log.level` | keyword | [3절](#3-loglevel) 7개 값 중 하나 | `unknown` |
| `service.name` | keyword | Kubernetes 이름(`gateway-api` 등, [6절](#6-data-stream)) | 파드 label `app.kubernetes.io/name`. 없으면 컨테이너 이름. AWS 로그는 출처 이름(`alb` 등) |
| `service.environment` | keyword | `dev` · `prod` | 클러스터 값 |
| `event.dataset` | keyword | data stream의 dataset([6절](#6-data-stream)) | Logstash가 정한 값 |
| `ecs.version` | keyword | `8.11` | `8.11` |

### 2.2 클러스터 로그 (파드에서 나온 로그만)

Filebeat가 Kubernetes 파드를 읽을 때 붙이는 필드와, 클러스터를 구분하려고 Logstash가 붙이는 필드다. AWS 로그에는 없다.

| 필드 | 타입 | 값 | 누가 채우나 |
| --- | --- | --- | --- |
| `kubernetes.namespace` | keyword | `dev` · `prod` · `monitoring` · `kube-system` 등 | Filebeat |
| `kubernetes.pod.name` | keyword | 파드 이름 | Filebeat |
| `kubernetes.node.name` | keyword | 노드 이름 | Filebeat |
| `kubernetes.labels.*` | keyword | 파드 label (예: `kubernetes.labels.app_kubernetes_io/name`) | Filebeat |
| `container.name` | keyword | 컨테이너 이름 | Filebeat |
| `container.id` | keyword | 컨테이너 ID | Filebeat |
| `host.name` | keyword | 노드 호스트 이름 | Filebeat |
| `orchestrator.type` | keyword | `kubernetes` | Logstash |
| `orchestrator.cluster.name` | keyword | `lore-sentry-k8s` · `lore-sentry-dev-k8s` | Logstash |

- `kubernetes.*`는 ECS 핵심 필드가 아니라 Elastic 관례 필드다. ECS에는 파드 label 같은 세부 필드가 없어서 이 관례를 쓴다.
  `container.*`, `host.name`, `orchestrator.*`는 ECS 필드다.
- ECS의 `orchestrator.namespace`는 `kubernetes.namespace`와 값이 같아서 두지 않는다.
- `service.environment`(2.1)와 `kubernetes.namespace`는 뜻이 다르다. `monitoring`·`kube-system` 파드는 namespace가 다르지만
  환경은 `dev`·`prod`이고, AWS 로그에는 namespace가 없다.

### 2.3 있을 때만

| 필드 | 타입 | 언제 |
| --- | --- | --- |
| `event.original` | keyword (색인 안 함) | 파싱한 로그. 마스킹된 원래 줄 |
| `tags` | keyword | `truncated`(길이 상한), `_parse_failure`(파싱 실패) 등 |

## 3. `log.level`

**모든 출처에서 같은 값 체계를 쓰는 keyword 필드**다. 레벨로 거르고 집계하는 기준이다.

| 값 | 뜻 |
| --- | --- |
| `trace` | 가장 잘게 쪼갠 내부 진행 기록 |
| `debug` | 개발자 진단 정보 |
| `info` | 정상 동작의 주요 사건 |
| `warn` | 예상 밖이지만 처리된 상황 (재시도, 느린 쿼리, 잠금 대기) |
| `error` | 작업 하나의 실패 |
| `fatal` | 프로세스나 세션이 더 진행할 수 없음 |
| `unknown` | 레벨을 알 수 없음. 이 값이 보이면 파싱 규칙을 추가한다 |

ECS는 `log.level`에 출처의 원래 값을 넣으라고 권한다. 하지만 출처 사이에 통일된 레벨 필드가 ECS에 없어서 여기서는 정규화한
값을 넣고, 원래 표현은 `event.original`에 남긴다.

| 출처 | 원래 표현 | `log.level` |
| --- | --- | --- |
| Spring 3개 (ECS JSON, 평문 모두) | `TRACE`, `DEBUG`, `INFO`, `WARN`, `ERROR` | 소문자로. Logback에는 `FATAL`이 없다 |
| Python 앱 (logging, ai-chat JSON `level`) | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` | `WARNING`→`warn`, `CRITICAL`→`fatal`, 나머지 소문자 |
| uvicorn 접근 로그 | 레벨 대신 응답 상태 | 5xx `error`, 4xx `warn`, 그 외 `info` |
| graph-rag (로깅 설정 없음, 레벨 없이 메시지만 출력) | 없음 | `unknown`. 앱 로그 규약을 적용하면 사라진다 |
| Kafka 브로커, Strimzi operator (log4j) | `TRACE` … `FATAL` | 소문자로 |
| Valkey | `.` `-` `*` `#` | `debug`, `debug`, `info`, `warn` |
| PostgreSQL | `DEBUG1-5`, `LOG`, `INFO`, `NOTICE`, `WARNING`, `ERROR`, `FATAL`, `PANIC` | `debug`, `info`, `info`, `info`, `warn`, `error`, `fatal`, `fatal`. 느린 쿼리(`duration:`)와 잠금 대기(`still waiting for`)는 `warn`으로 올린다 |
| Neptune slowquery | 없음 | `warn`. `exceptionClass`가 있으면 `error` |
| ALB, CloudFront | 없음 | 응답 5xx `error`, 4xx `warn`, 그 외 `info` |
| EKS audit | 없음 | 응답 5xx `error`, 401·403 `warn`, 그 외 `info` |
| EKS authenticator | `level=` 값 | 소문자로 |
| 그 밖의 파드 | 형식마다 다름 | 알아볼 수 있으면 위 규칙, 아니면 `unknown` |

## 4. 금지 항목과 마스킹

### 로그에 남기지 않는 것

| 항목 | 이유 |
| --- | --- |
| 이메일, 이름(`display_name`), Google 계정 ID(`google_subject`) | 개인정보. DB에서도 평문이라 로그가 두 번째 사본이 된다 |
| 원고·요약·채팅 본문, 요청·응답 본문 전체 | 작품 데이터. DB·Kafka·S3에서는 암호화되어 있다([MANUSCRIPT_ENCRYPTION.md](MANUSCRIPT_ENCRYPTION.md)) |
| 세션·동의 쿠키(`__Host-ls_session`, `__Host-ls_consent`), 토큰, `Authorization` 헤더 | 인증 수단 |
| OAuth `code`·`state`, Google ID 토큰 | 로그인 과정의 비밀 값. ID 토큰(JWT) 안에는 이메일과 이름이 있다 |
| 비밀번호 (DB 역할, Valkey 계정) | 사용자 비밀번호는 없다(Google 로그인만). 인프라 비밀번호가 대상이다 |
| 광고 클릭 ID 값(`gclid`, `gbraid`, `wbraid`) | 클릭 하나를 특정한다. 이미 `user_acquisitions`와 GA로 수집한다 |

`user.id`(auth `users.id` UUID)는 남겨도 된다. UUID만으로는 사람을 특정할 수 없다.

### Logstash 마스킹 규칙

규칙의 원칙:

- 앱이 실수로 남긴 값을 막는 두 번째 방어선이다.
- **파싱이 성공했는지와 상관없이** `message`, `event.original`, `url.query`, `error.message`, `error.stack_trace`에 적용한다.
- ES나 S3로 갈라지기 전에 적용한다.

| 대상 | 찾는 것 | 바꾼 값 |
| --- | --- | --- |
| 이메일 | `로컬@도메인` 형태 | `[email]` |
| OAuth | 쿼리 `code=`, `state=`의 값 | `[redacted]` |
| 광고 클릭 ID | 쿼리 `gclid=`, `gbraid=`, `wbraid=`의 값 (이름과 `utm_*`는 남김) | `[redacted]` |
| 세션·동의 쿠키 | `ls_session=`, `ls_consent=`의 값 (`__Host-` 접두어 포함) | `[redacted]` |
| 인증 헤더 | `Cookie:`, `Set-Cookie:`, `Authorization:` 뒤의 값 | `[redacted]` |
| JWT | `eyJ`로 시작하고 점으로 나뉜 세 조각 | `[jwt]` |
| 비밀번호 | `PASSWORD '...'` | `PASSWORD '[redacted]'` |
| DB 키 값 | `Key (칼럼)=(값)` (고유 키·외래 키 위반의 `DETAIL`) | `Key (칼럼)=([redacted])` |

마스킹을 ES ingest pipeline에서 하지 않는 이유:

- S3 보관본은 ES를 거치지 않는다.
- ingest pipeline이 실패하면 failure store가 pipeline을 적용하기 전의 원본을 남긴다.
- 전용 처리기 `redact`는 Basic 라이선스에서 동작하지 않는다.

이름, 본문처럼 패턴으로 잡을 수 없는 것은 앱이 찍지 않는 것으로만 막는다. 대신 정기적으로 반대 검색(이메일 패턴,
`code=`, `PASSWORD` 등)을 돌려 ES와 S3에서 0건인지 확인한다.

### 길이 상한

- ES로 가는 쪽에서만, `message`·`error.stack_trace`·`event.original` 각각 **256KB**를 넘으면 앞부분만 남기고 `tags`에
  `truncated`를 붙인다.
- S3 보관본은 마스킹된 전문을 그대로 둔다.
- 개인정보 대책이 아니라 ES 저장·검색 비용 대책이다. 평소 로그는 2KB 이하이고 긴 예외도 수십 KB라 영향이 없다.
  Kafka payload(최대 6MB)를 통째로 찍는 사고만 걸린다.
- 참고로 Filebeat에도 메시지당 10MB 기본 상한(`message_max_bytes`)이 있다.

### 소음 제거

| 버리는 것 | 이유 |
| --- | --- |
| 응답이 2xx인 헬스체크(`GET /health`) 접근 로그 | kubelet probe가 파드마다 시간당 540줄을 남긴다. 성공 여부는 Prometheus와 파드 이벤트가 이미 기록한다. 실패(4xx·5xx·타임아웃)는 남긴다 |
| EKS audit 중 시스템 계정(노드, 컨트롤러, Argo CD 등)의 `get`·`list`·`watch` | 양이 대부분이고 감사 가치가 낮다. 쓰기 요청과 사람 계정의 요청은 모두 남긴다 |
| RDS `rdsadmin` 계정의 연결·해제 줄 | AWS 내부 관리 연결이다 |

## 5. 앱 로그 규약

다음 작업에서 각 서비스가 맞출 규약이다. 그 전까지는 Logstash가 지금의 평문 형식에서 레벨과 서비스를 뽑는다.

### 형식

- stdout에 **한 줄에 ECS JSON 하나**를 쓴다. 여러 줄 예외는 `error.stack_trace` 한 필드에 담는다.
- 필드 이름은 ECS 이름을 쓰고, ECS에 없는 것은 `loresentry.*` 아래에 snake_case로 둔다.
- Spring: `logging.structured.format.console=ecs`를 쓴다. `service.name`은 Kubernetes 이름으로 맞춘다
  (`logging.structured.ecs.service.name`). 지금 `spring.application.name`은 `gateway`·`authentication`·`content`라서 다르다.
- Python: 같은 필드를 내는 JSON formatter를 쓴다. ai-chat의 `time`·`level`·`logger`는 `@timestamp`·`log.level`·`log.logger`로
  바꾼다.

### 필드

| 구분 | 필드 | 타입 | 언제 |
| --- | --- | --- | --- |
| 필수 | `@timestamp`, `log.level`, `log.logger`, `message`, `service.name`, `service.version`(이미지 태그), `ecs.version` | | 항상 |
| 추적 | `trace.id`, `span.id` | keyword | 요청이나 이벤트를 처리하는 중일 때([7절](#7-trace)) |
| 에러 | `error.type`(예외 클래스), `error.message`, `error.stack_trace` | keyword, match_only_text, wildcard | 예외를 남길 때 |
| HTTP | `http.request.method`, `url.path`, `http.response.status_code`, `event.duration`(ns) | | 요청 처리를 남길 때 |
| 사용자 | `user.id` | keyword | auth `users.id` UUID만 |
| Kafka | `loresentry.kafka.topic`, `loresentry.kafka.partition`(long), `loresentry.kafka.offset`(long), `loresentry.kafka.key` | keyword | 메시지를 보내거나 받을 때 |
| CloudEvents | `loresentry.cloudevent.id`, `loresentry.cloudevent.type`, `loresentry.cloudevent.subject` | keyword | 이벤트를 다룰 때 |
| 도메인 ID | `loresentry.project_id`, `loresentry.document_id`, `loresentry.file_id` | keyword | 해당 대상을 다룰 때 |
| 채팅 | `loresentry.chat.session_id`, `loresentry.chat.turn_id` | keyword | ai-chat. 인증 세션과 헷갈리지 않게 `chat` 아래에 둔다 |
| 최신화 | `loresentry.refresh.request_id`, `loresentry.refresh.run_id` | keyword | content, graph-rag |
| 설정 검사 | `loresentry.setting_check.run_id` | keyword | ai-chat |

ECS 8.11에는 메시징 필드 묶음이 없어서 Kafka와 CloudEvents 값을 `loresentry.*` 아래에 둔다.
`loresentry.*`의 문자열은 모두 keyword로 저장한다. 새 도메인 필드는 이 표에 먼저 추가한 뒤 쓴다.

## 6. data stream

ES는 로그를 data stream(보관함)에 쌓는다. 로그 하나는 보관함 하나에만 들어가고, 보관함마다 보존 기간과 필드 규칙을 따로 줄
수 있다. 검색은 `logs-*`, `logs-loresentry.*`, `logs-aws.*`처럼 묶어서 할 수 있다.

```text
data stream 이름 = logs-<dataset>-<dev|prod>
dataset          = <묶음>.<이름>       소문자, '-'는 '_'로 (data stream 이름에서 '-'는 구분자다)
묶음             = loresentry(우리 서비스) | 제품 이름(kafka, strimzi, valkey) | kubernetes(그 밖) | aws(관리형)
```

Logstash는 이 순서로 dataset을 고른다.

1. aws-logs Filebeat에서 온 로그 → 출처별 `aws.*`
2. 파드 label `app.kubernetes.io/part-of: lore-sentry`이고 아래 우리 서비스 중 하나 → `loresentry.<이름>`
3. Kafka 브로커(label `strimzi.io/kind: Kafka`) → `kafka.log`
4. Strimzi operator → `strimzi.operator`
5. `auth-valkey` → `valkey.log`
6. 나머지 → `kubernetes.container`

| 출처 | `service.name` | dataset | data stream (dev) |
| --- | --- | --- | --- |
| gateway | `gateway-api` | `loresentry.gateway_api` | `logs-loresentry.gateway_api-dev` |
| authentication | `authentication-api` | `loresentry.authentication_api` | `logs-loresentry.authentication_api-dev` |
| content | `content-api` | `loresentry.content_api` | `logs-loresentry.content_api-dev` |
| ai-chat API | `ai-chat-api` | `loresentry.ai_chat_api` | `logs-loresentry.ai_chat_api-dev` |
| ai-chat 요약기 | `ai-chat-summarizer` | `loresentry.ai_chat_summarizer` | `logs-loresentry.ai_chat_summarizer-dev` |
| graph-rag | `graph-rag-api` | `loresentry.graph_rag_api` | `logs-loresentry.graph_rag_api-dev` |
| Kafka 브로커 | `kafka` | `kafka.log` | `logs-kafka.log-dev` |
| Strimzi operator | `strimzi-operator` | `strimzi.operator` | `logs-strimzi.operator-dev` |
| auth-valkey | `auth-valkey` | `valkey.log` | `logs-valkey.log-dev` |
| 그 밖의 파드 (openai-mock, kafka-exporter, 로깅·모니터링·Argo CD·kube-system 등) | label 또는 컨테이너 이름 | `kubernetes.container` | `logs-kubernetes.container-dev` |
| ALB | `alb` | `aws.alb` | `logs-aws.alb-dev` |
| CloudFront | `cloudfront` | `aws.cloudfront` | `logs-aws.cloudfront-dev` |
| RDS PostgreSQL | `rds-postgresql` | `aws.rds` | `logs-aws.rds-dev` |
| Neptune | `neptune` | `aws.neptune` | `logs-aws.neptune-dev` |
| EKS audit | `eks` | `aws.eks_audit` | `logs-aws.eks_audit-dev` |
| EKS authenticator | `eks` | `aws.eks_authenticator` | `logs-aws.eks_authenticator-dev` |

prod는 끝만 `-prod`로 바뀐다. 환경마다 16개다.

## 7. trace

요청이 서비스를 옮겨 가며 처리되는 흐름을 `trace.id` 하나로 이어 본다. 앱 쪽 구현은 다음 작업이다.

| 항목 | 규약 |
| --- | --- |
| 형식 | W3C Trace Context `traceparent: 00-<trace-id 32 hex>-<span-id 16 hex>-<flags>` |
| 헤더 이름 | HTTP와 Kafka 모두 `traceparent` (소문자 정확히. Kafka 헤더는 대소문자를 구분한다). [KAFKA_DESIGN.md](KAFKA_DESIGN.md)의 선택 헤더와 같다. 자체 `traceId` 헤더는 쓰지 않는다 |
| ID 생성 | 추적 라이브러리(Spring: Micrometer Tracing, Python: OpenTelemetry)가 만든다. 128비트 무작위라 번호 관리가 필요 없다 |
| 시작점 | gateway가 외부 요청마다 새로 시작한다. 인터넷에서 온 `traceparent`는 믿지 않는다. 스케줄·폴링 작업은 1회마다 새로 시작한다 |
| 서비스 간 HTTP | `traceparent`를 그대로 전파한다 |
| outbox | 요청 트랜잭션에서 그때의 `traceparent`를 행에 저장하고, relay가 그 값으로 헤더를 붙여 발행한다. relay 시점의 값을 쓰면 흐름이 끊긴다 |
| Kafka 소비 | 헤더의 값을 이어받는다 |
| span | 작업(요청 받기, 다른 서비스 호출, DB 쿼리, Kafka 발행) 단위로 새로 생긴다. 서비스 안에서는 스택처럼 쌓였다 빠지고, 헤더에는 현재 span 하나만 실린다 |
| 로그 필드 | `trace.id`, `span.id` |
| 응답 | 에러 응답 본문과 응답 헤더에 `trace_id`를 돌려준다. 문의 받은 값을 Kibana에서 바로 찾는다 |
| ALB와 잇기 | ALB가 붙이는 `X-Amzn-Trace-Id`를 gateway가 `http.request.id`로 남긴다. ALB 접근 로그의 `trace_id`도 같은 필드에 넣는다 |

## 8. DB 로그

### PostgreSQL (`aws.rds`)

로그 접두어 `%m:%r:%u@%d:[%p]:%l:%e:%s:%v:%x:%c:%q%a:`를 이렇게 나눈다. 파라미터 그룹을 바꾸기 전의 기본 접두어
(`%t:%r:%u@%d:[%p]:`)도 함께 받는다.

| 접두어 | 필드 | 예 |
| --- | --- | --- |
| `%m` | `@timestamp` | |
| `%r` | `source.address` | 클라이언트 주소 |
| `%u` | `postgresql.log.user` | `content_svc` (DB 역할로 서비스를 구분한다) |
| `%d` | `postgresql.log.database` | `content` |
| `%p` | `process.pid` | |
| `%e` | `error.code` | SQLSTATE |
| `%c` | `postgresql.log.session_id` | |
| 심각도 | `log.level` | [3절](#3-loglevel) |
| `duration:` | `event.duration` (ns) | 느린 쿼리 |

ECS에는 DB 필드 묶음이 없어서 Elastic PostgreSQL integration과 같은 `postgresql.log.*`를 쓴다. `user.*`는 앱 사용자만
가리키도록 남긴다.

SQLSTATE는 `error.code`, PostgreSQL 조건 이름은 `error.type`에 넣는다.

| 상황 | `error.code` | `error.type` |
| --- | --- | --- |
| update 충돌, SSI 충돌 | `40001` | `serialization_failure` |
| 데드락 | `40P01` | `deadlock_detected` |
| 고유 키 충돌 | `23505` | `unique_violation` |
| 외래 키 위반 | `23503` | `foreign_key_violation` |
| 잠금 대기 실패 (`NOWAIT`) | `55P03` | `lock_not_available` |
| 쿼리 취소·시간 초과 | `57014` | `query_canceled` |
| 관리자 종료 (재부팅 등) | `57P01` | `admin_shutdown` |

lost update(확인 없이 덮어쓰기)는 DB 입장에서 정상 UPDATE라 로그에 남지 않는다.

### Neptune (`aws.neptune`)

slowquery 로그의 쿼리 문자열, 소요 시간(`event.duration`), `statusCode`, `exceptionClass`를 남긴다. 쿼리 문자열에 원고에서
나온 그래프 데이터가 들어갈 수 있어서 **S3 보관에서는 뺀다**(ES 30일만).

## 9. index template (`logs@custom`)

ES 내장 `logs` index template(`logs-*-*`)에 ES가 열어 둔 확장 지점 `logs@custom`을 더한다. 내용은
`loresentry-gitops/logging/base/elasticsearch/logs-custom.json`이다.

| 설정 | 값 | 이유 |
| --- | --- | --- |
| 레플리카 | 0 | ES가 1노드다. 레플리카가 있으면 클러스터가 yellow가 된다 |
| 보존 | 30일 (data stream lifecycle), `prefer_ilm: false` | 내장 template의 ILM 정책 `logs`는 삭제 단계가 없다 |
| 필드 수 | 2000개, 넘는 새 필드는 저장만 하고 색인하지 않음 | 예상하지 못한 필드가 쏟아져 매핑이 끝없이 커지는 것을 막는다 |
| keyword 명시 | `log.level`, `log.logger`, `service.*`, `trace.id`, `span.id`, `http.request.id`, `error.code`, `error.type`, `user.id`, `orchestrator.*`, `postgresql.log.*`, `loresentry.*` | 동적 매핑은 처음 들어온 문서가 타입을 정한다. 걸러 보고 집계할 필드의 타입을 ES 내부 규칙이 아니라 이 파일로 보장한다 |
| long 명시 | `http.response.status_code`, `event.duration`, `loresentry.kafka.partition`, `loresentry.kafka.offset` | 범위 검색과 정렬 |

failure store는 ES 9.2부터 `logs-*-*`에 기본으로 켜져 있어서 따로 설정하지 않는다. 실제로 적용된 값은 dev에 처음 배포할 때
`GET _index_template/logs`와 data stream 설정으로 확인한다.

## 바꾸는 절차

1. 이 문서, gitops의 `logs-custom.json`, Logstash 설정을 함께 고친다. 문서는 docs PR, 나머지는 gitops PR이다.
2. dev에 먼저 반영해 확인한 뒤 prod에 반영한다.
3. 필드 타입 변경은 이미 만들어진 인덱스에는 적용되지 않고 **다음 rollover부터** 새 인덱스에 적용된다. 그 사이에는 같은
   필드가 인덱스마다 다른 타입이 되어 Kibana가 충돌로 표시한다. 타입은 처음부터 바르게 정하고, 바꿀 때는 새 이름으로 추가한다.
