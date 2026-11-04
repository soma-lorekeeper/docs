# APM 아키텍처 — span을 모아 Kibana APM에서 호출 그림 보기까지

> 최신화: 2026-11-05
> 대상: gateway, authentication, content, ai-chat(API·summarizer), graph-rag가 만드는 trace(span)
> 상태: **dev 운영 중(2026-11-04).** 서비스 6개가 span을 보내고 Kibana APM에서 본다. 2026-11-05부터 앱 쪽 추적 엔진을
> EDOT(Java agent, Python distro)로 옮기며, 로그와 APM이 같은 span을 가리키고 흐름에 빈 구간이 없게 고친다. 이 문서는 옮긴 뒤의
> 구성을 적고, 서비스별 진행은 [12절](#12-전환-상태와-남은-일)에 있다. prod는 prod 환경을 세팅할 때 같은 구성으로 옮긴다.
> 관련: trace 규약은 [LOG_SCHEMA.md 7절](LOG_SCHEMA.md#7-trace), span 마스킹은 [4절](LOG_SCHEMA.md#4-금지-항목과-마스킹),
> template은 [9절](LOG_SCHEMA.md#9-index-template-logscustom), 로그 흐름은 [LOGGING_ARCHITECTURE.md](LOGGING_ARCHITECTURE.md),
> 매니페스트는 `loresentry-gitops/logging/base/otel-collector/`(설명은 `logging/README.md`의 "APM")

로그와 trace는 같은 `trace.id`로 묶이지만 하는 일이 다르다.

- **로그**: 무슨 일이 있었나. 줄마다 메시지·상태·ID·오류가 있다. Filebeat → Logstash → ES `logs-*`로 간다.
- **trace**: 누가 누구를 불렀고 어디서 시간이 걸렸나. 작업 하나가 span 하나이고, span이 부모·자식으로 이어져 한 요청의 호출
  그림이 된다. 앱의 추적 엔진(EDOT) → **EDOT Collector** → ES `traces-*.otel-*`로 간다. Kibana APM이 이것으로 서비스 목록,
  지연 시간, trace 폭포 그림을 그린다.
- 로그 줄의 `trace.id`·`span.id`는 그 줄을 찍는 순간의 현재 span이다. 그래서 로그 줄 하나에서 APM의 그 span으로, span에서 그
  안에서 찍힌 줄로 오간다.
- span은 Logstash를 거치지 않는다. 그래서 개인정보 처리(지우기·가리기)는 collector가 한다.

## 1. 전체 구조

```mermaid
flowchart LR
  subgraph APPNS["EKS namespace dev (앱)"]
    GW["gateway-api"]
    AUTH["authentication-api"]
    CT["content-api"]
    AC["ai-chat-api"]
    SUM["ai-chat-summarizer"]
    GR["graph-rag-api"]
  end

  subgraph MON["namespace monitoring (로깅 노드)"]
    COL["EDOT Collector<br/>edot-collector:4317 gRPC · :4318 HTTP"]
    ES[("Elasticsearch 1노드")]
    KB["Kibana APM"]
    LS["Logstash"]
  end

  GW & AUTH & CT & AC & SUM & GR -- "OTLP span" --> COL
  COL -- "API key(쓰기 전용)" --> ES
  GW & AUTH & CT & AC & SUM & GR -.->|"stdout 로그 → Filebeat"| LS
  LS -- "logs-loresentry.*" --> ES
  ES --> KB
  USER(["사용자"]) -- "https://kibana-dev.loresentry.com" --> KB
```

- 서비스 사이에서는 W3C `traceparent` 헤더(HTTP·Kafka)로 trace가 이어진다. 그래서 서비스마다 따로 보낸 span이 ES에서
  한 trace로 모인다. 전파는 span 전송과 상관없이 지금도 동작한다.
- collector는 받은 span을 ES에 쓰기만 한다. 앱은 collector가 멈춰도 영향을 받지 않는다([10절](#10-장애가-나면)).

## 2. 구성요소

| 구성요소 | 종류 | 어디서 | 하는 일 |
|---|---|---|---|
| Java 서비스 3개 | EDOT Java agent `co.elastic.otel:elastic-otel-javaagent` 1.14.0(OpenTelemetry Java agent 2.32.0 기반). 이미지에 넣고 시작 스크립트가 `-javaagent`로 띄운다 | namespace dev | 요청·호출·쿼리·Kafka 소비와 발행·스케줄마다 span을 만들고 OTLP로 보낸다. 켜는 계측은 [LOG_SCHEMA.md 7절 계측 범위](LOG_SCHEMA.md#계측-범위), 전송 설정은 환경 변수 |
| Python 서비스 3개 | EDOT Python `elastic-opentelemetry` 1.19.0(SDK 1.45.2, 계측 0.66b2). `opentelemetry-instrument`로 띄운다 | namespace dev | 같은 일 |
| 로깅 팩토리 | 서비스마다 로그 패키지(Java `logging`, authentication은 `adapter/out/logging`도, Python `app/logs`) | 앱 안 | 엔진 위에서 우리 span(아래 [3절](#우리가-여는-span))을 열고, 로그 줄에 그 순간의 span ID를 넣는다. 서비스 코드는 추적 API를 쓰지 않는다 |
| gateway 전파기 확장 | gateway 저장소의 agent 확장(`otel.javaagent.extensions`) | gateway | `traceparent`를 나가는 호출에 넣기만 하고 들어온 값은 읽지 않는다. 외부 요청이 늘 새 trace로 시작한다 |
| EDOT Collector | Deployment 1대, 이미지 `elastic-agent:9.5.5`(`ELASTIC_AGENT_OTEL=true`) | 로깅 노드 | span 받기, 민감한 값 지우기·가리기, APM 필드 보강, 집계 지표 만들기, ES 쓰기 |
| template Job | Job(Argo CD Sync hook) | 로깅 노드 | collector보다 먼저 `traces-otel@custom`·`metrics-otel@custom`을 ES에 넣는다(로그용 `logs@custom`과 같은 Job) |
| 쓰기 계정 | ES API key `edot-collector`, Secret `monitoring/edot-collector-es` | ES | `*.otel-*` data stream 만들기·쓰기만 한다 |
| Kibana APM | Kibana 9.5.5 Observability → APM | 로깅 노드 | 서비스·트랜잭션·trace·오류 화면. 따로 설정할 것이 없다(기본 APM 인덱스에 `*.otel-*`가 들어 있다) |

## 3. span이 만들어지고 이어지는 길

규약은 [LOG_SCHEMA.md 7절](LOG_SCHEMA.md#7-trace)에 있다. 문서 최신화 요청을 예로 들면 이렇게 이어진다.

```mermaid
sequenceDiagram
  participant B as 브라우저
  participant GW as gateway
  participant CT as content
  participant K as Kafka
  participant GR as graph-rag
  participant COL as EDOT Collector

  B->>GW: POST 최신화 (외부 traceparent는 읽지 않는다)
  Note over GW: 새 trace 시작 (root span)
  GW->>CT: HTTP + traceparent (계측이 넣음)
  Note over CT: outbox 행에 traceparent 저장
  CT-->>GW: 응답
  Note over CT: relay가 행의 traceparent로 span을 열고 그 안에서 발행
  CT->>K: 발행 계측이 그 문맥을 헤더에 넣음
  K->>GR: 레코드 헤더의 traceparent를 이어받음
  Note over GR: InboxEvent에 traceparent 저장 → 처리 → 저장된 문맥 안에서 완료 이벤트 발행
  GR->>K: 완료 이벤트 (같은 trace)
  K->>CT: 소비 계측이 이어받아 완료 처리
  GW-->>COL: span (서비스마다 따로 보냄)
  CT-->>COL: span
  GR-->>COL: span
```

- **시작점**: gateway가 외부 요청마다 새 trace를 연다(전파기가 들어온 값을 읽지 않는다). 스케줄·폴링은 1회마다 새 trace다.
- **저장했다 나중에 처리**: outbox 행, ai-chat `summary_queue`, graph-rag `InboxEvent`, Kafka 에러 처리기가 저장된 `traceparent`를
  부모로 span을 열고 그 안에서 처리·발행한다. 그래서 나중에 처리해도 같은 trace에 붙고, 발행 헤더도 그 trace를 잇는다. 발행
  계측은 헤더를 현재 문맥으로 덮어쓰므로, 저장된 문맥을 현재 문맥으로 두는 것이 trace를 잇는 유일한 방법이다.
- **로그와 잇기**: 로그 줄의 `trace.id`·`span.id`는 줄을 찍는 순간의 현재 span이다. APM에서 느린 span을 찾아 그 `span.id`로
  Discover에서 그 안의 줄을 보고, 반대로 오류 줄의 `trace.id`로 APM의 호출 그림을 열어 같은 `span.id`의 span을 찾는다.

### 우리가 여는 span

계측이 만들지 않는 구간은 로깅 팩토리가 연다. 이름이 Kibana APM의 transaction·span 이름이 된다.

| API | span 이름 | 종류 | 부모 |
|---|---|---|---|
| `LogFlow.call(target, …)` | `<target> call`(예: `google call`, `s3 call`, `upstream call`) | INTERNAL. 계측이 없는 대상은 CLIENT | 현재 span. 계측의 CLIENT span이 이 span의 자식 |
| `LogFlow.call("kafka", …, headers=…)`(Python) | `<topic> publish` | PRODUCER | 현재 span |
| `LogFlow.task(name, …)` | `<name>` | INTERNAL | 현재 span. 흐름 밖에서 시작한 작업은 root |
| `LogFlow.polling`·`schedule`(Python) | `<스케줄 이름>` | INTERNAL | root |
| `LogFlow.kafka_record`(Python) | `<topic> process` | CONSUMER | 레코드 헤더 |
| `LogScope.outboxEvent`(Python `outbox_event`) | `outbox <event type>` | INTERNAL | 행에 저장된 `traceparent` |
| `LogScope.kafkaRecord` | `<topic> recover` | INTERNAL | 레코드 헤더 |
| `LogScope.stored`(Python) | `<작업 이름>` | INTERNAL | 행에 저장된 `traceparent` |

- Java의 요청, 스케줄 회차, Spring Kafka 소비, 발행, 쿼리 span은 agent가 연다. Python의 요청, HTTP·DB·AWS·OpenAI 호출 span은 계측이 연다.
- 부모가 다른 서비스나 다른 시점(헤더, 저장된 값)에 있는 span은 Kibana APM에서 transaction으로 보인다. Kafka 소비, outbox 발행,
  요약 작업이 그렇다.

## 4. collector 안의 처리

```mermaid
flowchart LR
  R["otlp receiver<br/>4317 · 4318"] --> M["memory_limiter<br/>한도 80%"] --> TS["tail_sampling<br/>빈 폴링·성공 헬스체크 trace 버리기"] --> T["transform/privacy<br/>지우기 · 가리기"] --> BT["batch<br/>1,000개 · 1초"] --> P["elasticapm processor<br/>APM 필드 보강"]
  P --> E[("elasticsearch exporter<br/>mapping otel")]
  P --> C["elasticapm connector<br/>집계 지표"] --> E
```

| 단계 | 하는 일 |
|---|---|
| `memory_limiter` | 컨테이너 메모리 한도의 80%에 가까워지면 받기를 잠시 거절한다. 앱의 추적 엔진이 다시 보낸다 |
| `tail_sampling` | 빈 폴링 회차와 성공한 헬스체크의 trace를 통째로 버리고 나머지는 모두 남긴다(아래 "버리는 trace"). 판단하려고 trace를 5초 모았다가 내보낸다 |
| `transform/privacy` | 로그에 남기지 않는 값을 지우고, 남은 값을 가린다(아래 표) |
| `batch` | 묶어서 ES에 보낸다 |
| `elasticapm` processor | span을 Kibana APM이 읽는 모양으로 보강한다(`transaction.*`·`span.*`, `processor.event`, 결과·성공 여부) |
| `elasticapm` connector | span으로 서비스·트랜잭션·호출 대상별 지연·건수·오류율을 1분·10분·60분 단위로 집계한다. APM 목록 화면이 이것을 읽는다 |
| `elasticsearch` exporter | OTel 원래 모양(`mapping.mode: otel`)으로 data stream에 쓴다 |

민감한 값 처리([LOG_SCHEMA.md 4절](LOG_SCHEMA.md#4-금지-항목과-마스킹)):

| 처리 | 대상 |
|---|---|
| 지우기 | URL 쿼리(`url.full`·`url.original`·`http.url`·`http.target`의 `?` 뒤, `url.query`), SQL과 Redis 명령(`db.statement`·`db.query.text`), 예외 event의 `exception.message`·`exception.stacktrace`, AWS 호출 span의 S3 객체 키(`url.full`·`url.path`·`aws.s3.key`·`aws.s3.copy_source`), GenAI 대화 내용(`gen_ai.prompt`·`gen_ai.completion`·`gen_ai.input.messages`·`gen_ai.output.messages`·`gen_ai.system_instructions`, `gen_ai.*` event) |
| 가리기 | 남은 모든 span·event 문자열 속성. 이메일, OAuth `code`·`state`, 광고 클릭 ID, 서명 URL, 세션 쿠키, 인증 헤더, JWT, 비밀번호, DB 키 값 |

- 예외는 `exception.type`만 남는다. 스택은 같은 `span.id`의 앱 로그 `error.stack_trace`(메시지 없는 스택)로 본다.
- HTTP 헤더, 요청·응답 본문, GenAI 대화 내용은 계측이 기본으로 담지 않는다. 그 설정을 켜지 않는다. 위 지우기는 잘못 켜졌을 때의 두 번째 방어선이다.
- 요청 span의 `client.address`·`user_agent.original`은 ALB 접근 로그와 같은 기준으로 남긴다.

버리는 trace([LOG_SCHEMA.md 7절](LOG_SCHEMA.md#7-trace)):

| 대상 | 기준 | 이유 |
|---|---|---|
| 빈 폴링 회차 | 회차 span에 `loresentry.polling.idle=true`가 있다 | 1초 relay의 빈 회차가 trace 대부분을 차지한다 |
| 성공한 헬스체크 | root span이 `/health`·`/health/**` 요청이고 `http.response.status_code`가 400 미만이다 | kubelet probe가 파드마다 시간당 수백 번 부른다(2026-11-04 하루 약 10만 transaction). 성공 여부는 Prometheus와 파드 이벤트에 있다. 실패한 헬스체크는 남는다 |

- 두 경우 모두 `tail_sampling`의 `drop` 정책이 trace를 자식 span까지 통째로 버린다. span 하나만 거르면 자식 span(조회, 내부 서비스 헬스체크)이 부모 없이 남기 때문이다.
- 헬스체크 기준은 root span만 본다. gateway 헬스체크가 부른 내부 서비스 헬스체크가 성공해도 gateway가 실패로 답하면 trace가 남는다.

빈 폴링 회차의 span을 여는 쪽:

| 서비스 | span을 여는 쪽 |
|---|---|
| content(1초 relay, 1분 최신화 timeout), auth(1초 relay) | agent의 Spring Scheduling 계측이 `@Scheduled` 실행마다 연다 |
| ai-chat summarizer tick, graph-rag relay | `LogFlow.polling`이 회차를 시작할 때 열고 현재 문맥으로 둔다 |

- 어느 쪽이든 `LogFlow.polling`이 줄 없이 끝난 회차의 span에 표시를 단다. 회차 안의 대기열 조회 span은 회차 span의 자식이라 함께 버려진다.
- 확인(2026-11-04 부하 테스트 중 4분): content relay가 남긴 회차 span 29건, 같은 시간 relay 종료 로그 28건. 빈 회차 표시가 붙은 span은 ES에 0건이다.

## 5. 저장

| data stream | 내용 | Kibana에서 |
|---|---|---|
| `traces-<dataset>.otel-<namespace>` | span. 서비스가 받은 요청·소비한 레코드는 transaction, 그 안의 호출·쿼리는 span | trace 폭포 그림, 트랜잭션 목록 |
| `logs-<dataset>.otel-<namespace>` | span의 예외 event | APM 오류 |
| `metrics-{service_summary,service_transaction,transaction,service_destination}.{1m,10m,60m}.otel-*` | connector가 만든 집계 | 서비스 목록, 지연·처리량·오류율 그래프, 호출 대상 |

| 설정 | 값 | 정하는 곳 |
|---|---|---|
| 레플리카 | 0(1노드 ES) | `traces-otel@custom`, `metrics-otel@custom`, 예외 event는 `logs@custom` |
| 보존 | 30일. 로그와 같다 | 같은 template. 내장 ILM 정책은 삭제 단계가 없어 `prefer_ilm: false`로 data stream lifecycle을 쓴다 |
| 디스크 | 로그와 같은 ES 128Gi를 나눠 쓴다(2026-11-04에 20Gi에서 늘림) | 부하 테스트 중 실측은 traces 하루 약 4GB, 로그 하루 약 1GB. 디스크가 95%를 넘으면 ES가 index를 읽기 전용으로 바꿔 로그 수집도 멈추므로 kps `KubePersistentVolumeFillingUp`이 마지막 경보다 |

## 6. 계정과 권한

- collector는 ES API key(이름 `edot-collector`)로 쓴다. 권한은 `traces-*.otel-*`·`metrics-*.otel-*`·`logs-*.otel-*`에
  `auto_configure`(data stream 만들기)와 `create_doc`(문서 넣기)뿐이다. 읽기와 앱 로그 data stream 쓰기는 거부된다(403).
- 키는 Git에 넣지 않는다. dev는 `loresentry-gitops/bootstrap/dev/09-apm-key.sh`가 `elastic` 계정으로 발급해 Secret
  `monitoring/edot-collector-es`에 넣는다. 비밀번호와 키는 stdin으로만 넘겨 명령줄·화면에 남지 않는다.
- 만료는 두지 않는다. 만료되면 span이 조용히 끊기기 때문이다. 바꿀 때는 Kibana(Stack Management → API keys)에서 키를 무효화하고
  Secret을 지운 뒤 스크립트를 다시 돌리고 collector를 재시작한다.

## 7. Kibana에서 보기

- 주소는 `https://kibana-dev.loresentry.com`, 메뉴는 Observability → APM이다.
- Basic 라이선스에서 쓸 수 있는 것과 없는 것:

| 기능 | Basic |
|---|---|
| 서비스 목록, 트랜잭션, trace 폭포 그림, 오류, 지연·처리량·오류율 그래프 | 된다 |
| service map, 이상 탐지(ML), correlations | 안 된다(Platinum) |
| custom links | 안 된다(Gold) |

- OTel 데이터의 제약: span의 스택 trace는 보이지 않는다(예외 스택은 지우고, EDOT Java의 span 스택은 끈다). 부모가 다른 서비스나
  다른 시점에 있는 span(Kafka 소비, outbox 발행, 저장 후 처리)은 transaction으로 보인다.
- 자주 쓰는 길: Discover의 앱 로그에서 `trace.id` 값을 누르면 그 trace의 폭포 그림이 새 탭으로 열린다(아래 링크 설정). 그 줄의
  `span.id`가 폭포 그림의 span ID와 같다. 반대로 폭포 그림에서 느린 span을 찾고, 그 `span.id`로 Discover에서 그 안의 줄을 본다.

편의 설정(2026-11-04, Kibana API로 만듦. 매니페스트가 아니라 Kibana 저장 객체다):

| 설정 | 내용 |
|---|---|
| `trace.id` 링크 | data view `loresentry-app-logs-dev`(앱 로그)·`dev-all-logs`(dev 전체)의 `trace.id`에 URL 형식 `/app/apm/link-to/trace/{{value}}` |
| APM 기본 환경 | 고급 설정 `observability:apmDefaultServiceEnvironment` = `dev`(서비스가 `deployment.environment.name=dev`를 보낸다) |
| data view | `loresentry-apm-traces` "APM trace (dev)"(`traces-*.otel-*`), `loresentry-apm-errors` "APM 오류 (dev)"(`logs-*.otel-*`) |

대시보드(위쪽 Links 패널로 서로 오가고 APM 서비스 목록으로 간다. 기본 범위 24시간, 패널은 ES|QL):

| 대시보드 | id | 패널 |
|---|---|---|
| LoreSentry 앱 (dev) | `loresentry-dash-app` | 서비스별 로그량, warn·error 추이, `request completed`의 p95(ms), 상태 클래스(2xx~5xx), 느린 `http.route`, 상위 `error.type`, `log.level: unknown` 수 |
| Kafka (dev) | `loresentry-dash-kafka` | 토픽별 이벤트 처리 추이와 결과(`loresentry.result.outcome`), 처리 실패 `error.type`, outbox relay 발행·격리·실패 건수, 브로커·Strimzi warn·error |
| DB (dev) | `loresentry-dash-db` | RDS 오류(SQLSTATE `error.code`), RDS·Neptune 느린 쿼리 수와 p95, 앱의 DB 관련 `error.type`(트랜잭션·JDBC·psycopg 등), Valkey warn·error |
| APM (dev) | `loresentry-dash-apm` | 서비스별 처리량·p95 지연·실패율, span 유형 분포, 느린 트랜잭션, 예외 유형 |

- 다시 만들 때 쓸 내보내기(NDJSON)와 생성 스크립트는 저장소에 두지 않았다. Kibana 저장 객체 내보내기(Stack Management → Saved objects)로 백업한다.

## 8. 자원과 배치

| 항목 | 값 |
|---|---|
| collector | 1대, 요청 100m·256Mi, 한도 500m·512Mi, `GOMEMLIMIT` 400MiB |
| 로깅 노드 | `r7i.xlarge`(4 vCPU·32GiB) 1대에 ES·Logstash·Kibana·Filebeat·collector가 함께 뜬다(2026-11-05 확인) |
| CPU·메모리 예약 | CPU 2,210m/3,920m(56%), 메모리 8,952Mi/29.8GiB(29%)(2026-11-05, Stack Monitoring sidecar 포함). `r7i.large` 때는 collector 전에 이미 CPU 1,610m/1,930m(83%)였고, collector 요청을 작게 둔 이유다 |
| 부하 테스트 때 최고치(2026-11-04, 당시 `r7i.large` 2 vCPU·16GiB) | 노드 CPU 1,950m(100%, Logstash 1,546m·ES 950m), ES 메모리 2,894Mi/3Gi, Kibana 1,506Mi/1,536Mi |
| span 전송 뒤 실측(2026-11-04 부하 테스트 중, 당시 `r7i.large`) | span 초당 약 155개. traces 저장량 시간당 약 165MB(병합 전 크기). collector CPU 38m·메모리 217Mi. 노드 CPU 2,000m(103%) |

EDOT로 옮기면 쿼리·Redis·AWS·OpenAI span이 늘고 성공한 헬스체크 trace는 줄어든다. 옮긴 뒤 부하 테스트로 span 수, 저장량,
collector·노드 자원을 다시 잰다.

로깅 노드는 부하 테스트 때 노드 CPU가 100%를 넘은 것과 Stack Monitoring sidecar 예약을 보고 2026-11-04 `r7i.xlarge`로 올렸다.
관리형 노드 그룹은 인스턴스 타입을 그 자리에서 바꿀 수 없어, 새 노드 그룹 `lore-sentry-dev-logging-pool-v2`(같은 2b·label·taint)를
만들고 이전 노드를 drain한 뒤 이전 그룹을 지웠다.

## 9. 배포

```mermaid
flowchart LR
  KEY["bootstrap/dev/09-apm-key.sh<br/>API key Secret"] -.-> W2
  subgraph LOGAPP["logging Application (logging/overlays/ENV)"]
    W0["wave 0<br/>Elasticsearch"] --> W1["wave 1<br/>template Job<br/>logs·traces-otel·metrics-otel @custom"] --> W2["wave 2<br/>Kibana · Logstash · Filebeat ·<br/>EDOT Collector"]
  end
```

- 첫 data stream이 생기기 전에 template이 들어가야 레플리카 0으로 만들어진다. 그래서 template Job(wave 1)이 collector(wave 2)보다 먼저다.
- Secret이 없으면 collector 파드는 만들어지지 않고 기다린다. 스크립트를 돌리면 저절로 뜬다.

| 바꾸는 것 | 위치 |
|---|---|
| 지우기·가리기 규칙, 파이프라인 | `logging/base/otel-collector/otel.yml`(바꾸면 ConfigMap 이름이 바뀌어 collector가 다시 뜬다). LOG_SCHEMA 4절도 같이 고친다 |
| 자원, 이미지 버전 | `logging/base/otel-collector/collector.yaml`. 버전은 ES·Kibana와 같게 둔다 |
| 보존·레플리카 | `logging/base/elasticsearch/{traces,metrics}-otel-custom.json`(새 data stream·다음 rollover부터 적용) |
| 앱 쪽 전송 | 환경 변수. `workload/base`는 `OTEL_TRACES_EXPORTER`·`OTEL_METRICS_EXPORTER`·`OTEL_LOGS_EXPORTER=none`과 `OTEL_SERVICE_NAME`을 둔다. `workload/overlays/dev`가 `OTEL_TRACES_EXPORTER=otlp`, `OTEL_EXPORTER_OTLP_ENDPOINT=http://edot-collector.monitoring.svc:4318`, `OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf`, `OTEL_TRACES_SAMPLER=parentbased_always_on`, `OTEL_SEMCONV_STABILITY_OPT_IN=http,database`, `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=false`, `OTEL_RESOURCE_ATTRIBUTES`(환경, namespace, 파드 이름은 Downward API)를 켠다. gateway는 `OTEL_PROPAGATORS`로 inject-only 전파기를 고른다. 옮기기 전 서비스는 이전 변수(Java `MANAGEMENT_TRACING_EXPORT_OTLP_ENABLED`·`MANAGEMENT_OPENTELEMETRY_TRACING_EXPORT_OTLP_ENDPOINT`, Python `OTEL_EXPORTER_OTLP_ENDPOINT`)를 쓴다 |
| 계측 범위 | 서비스 저장소. Java는 agent 설정 파일(이미지에 들어간다), Python은 requirements의 계측 패키지와 `OTEL_PYTHON_DISABLED_INSTRUMENTATIONS`. [LOG_SCHEMA.md 7절 계측 범위](LOG_SCHEMA.md#계측-범위)도 같이 고친다 |

설정을 바꾼 뒤에는 로컬에서 ES 9.5.5(보안 켬)와 같은 collector 이미지에 시험 span을 보내, ES 문서에 지운 값이 없는지 확인한다.

## 10. 장애가 나면

| 상황 | 동작 |
|---|---|
| collector가 멈춤·재시작 | 앱의 추적 엔진이 잠시 다시 보내다가 못 보낸 span은 버린다. 요청 처리와 로그는 영향이 없다. collector 메모리 안의 보내기 대기분은 사라진다 |
| Java agent를 읽지 못함 | `-javaagent`의 jar가 없거나 깨졌으면 JVM이 시작하지 않는다. 이미지 빌드에서 jar를 받으며 확인한다. collector에 닿지 못하는 것은 시작을 막지 않는다 |
| ES가 멈춤 | exporter가 메모리 큐에서 다시 시도하다가, 큐가 차면 span을 버린다. 로그는 Logstash 디스크 큐에 따로 쌓인다 |
| 메모리 한도 근처 | `memory_limiter`가 받기를 거절하고 앱의 추적 엔진이 다시 보낸다 |
| API key 무효·만료 | ES가 401을 돌려주고 span이 버려진다. collector 로그(`logs-kubernetes.container-*`의 `edot-collector` 파드)에 남는다 |
| Secret 없음 | collector 파드가 `CreateContainerConfigError`로 기다린다 |

## 11. 정합성 점검

로그와 APM이 같은 곳을 가리키는지, 흐름에 빈 구간이 없는지 dev에서 확인한다. 추적 엔진·계측·로깅 팩토리·collector 규칙을
바꿀 때마다 돌린다. ES를 읽는 점검(C1~C9, C11)은 `loresentry-gitops/logging/tools/apm_consistency.py`가 하고(ES 비밀번호는 stdin으로만
받는다), C10은 서비스 저장소의 빌드가 한다.

| # | 점검 | 합격 |
|---|---|---|
| C1 | 로그 줄(헬스체크 제외, 1분 넘게 지난 줄)의 `trace.id`·`span.id`가 APM에 같은 서비스의 span으로 있다 | 100% |
| C2 | 외부 호출 결과 줄마다 같은 `span.id`인 `<target> call` span이 있고, span 길이가 `event.duration`과 맞는다(±5ms). 계측이 있는 대상은 CLIENT 자식이 있다 | 100% |
| C3 | 흐름별 trace(파일 저장 → Kafka → graph-rag·summarizer, 최신화 요청 → 완료, 채팅 SSE → content·OpenAI, 로그인·로그아웃, 탈퇴 auth outbox → content)마다 root가 하나이고 부모 없는 span이 없다. 기대한 서비스와 span 종류(DB·Redis·AWS·Kafka·OpenAI)가 모두 있다 | 흐름마다 표본 20개 모두 |
| C4 | outbox·`summary_queue`·`InboxEvent` 행에 저장된 trace와 소비 span의 trace가 같고, relay 회차 trace와는 다르다 | 100% |
| C5 | 빈 폴링 회차 trace, 성공한 헬스체크 trace, 시작점이 아닌 부모 없는 span이 저장되지 않는다 | 0건 |
| C6 | 서비스마다 쓰는 의존 대상([LOG_SCHEMA.md 계측 범위](LOG_SCHEMA.md#계측-범위))에 span이 있고, span 종류별 표준 속성 보유율이 99% 이상이다. 한 작업에 같은 종류의 span이 둘 있는 곳이 없다 | 기준 충족 |
| C7 | 외부 `traceparent`를 붙여 보낸 gateway 요청이 새 trace로 시작한다 | 이어지지 않음 |
| C8 | resource에 `service.name`·`service.version`·`service.instance.id`·`k8s.pod.name`·`deployment.environment.name`이 있다 | 100% |
| C9 | traces·`logs-*.otel-*`에서 반대 검색(이메일, JWT, `code=`·`state=`, S3 객체 키 경로, SQL 문, 프롬프트)을 한다 | 0건 |
| C10 | 서비스 코드(로그 패키지 밖)에 추적 API import, `traceparent` 헤더 직접 설정이 없다(ArchUnit, ruff, grep) | 0건 |
| C11 | 앱 로그의 `warn`·`error` 줄(로그 클래스 줄과 프레임워크 줄)에 `error.stack_trace`가 있다. 예외 메시지 반대 검색은 0건이다 | 100% |

## 12. 전환 상태와 남은 일

EDOT로 옮기는 순서다. 각 단계는 dev에 배포한 뒤 11절 점검을 통과해야 다음으로 넘어간다.

| 순서 | 대상 | 바뀌는 것 | 상태 |
|---|---|---|---|
| 1 | 문서 | 이 문서와 LOG_SCHEMA 4·5·7절, KAFKA_DESIGN 헤더 규약 | 이 문서 |
| 2 | collector, 점검 스크립트 | 성공한 헬스체크 drop, S3 객체 키·GenAI 대화 내용 지우기, `apm_consistency.py` | 예정 |
| 3 | authentication | EDOT Java agent, 로깅 팩토리 span(호출 감싸기, 작업, outbox 저장 문맥), 로그 `span.id` 규칙, warn·error 스택 | 예정 |
| 4 | content | 같은 패턴과 Kafka 소비, 에러 처리기·DLQ 저장 문맥, S3·KMS | 예정 |
| 5 | gateway | inject-only 전파기 확장, RestClient interceptor 감싸는 span, 채팅 SSE, Valkey | 예정 |
| 6 | ai-chat | EDOT Python, summarizer 작업 저장 문맥(요청 밖에서 생기던 부모 없는 DB span이 없어진다), messaging 속성, OpenAI·KMS | 예정 |
| 7 | graph-rag | 폴링 회차 span을 시작할 때 열기, `InboxEvent` 저장 문맥과 발행 헤더, Neptune httpx 계측, 내부 ASGI span 빼기 | 예정 |
| 8 | Kibana, 부하 테스트 | APM 대시보드를 표준 속성으로 고치기, 전체 점검, 오버헤드와 로깅 노드 자원 재기 | 예정 |

| 남은 일 | 내용 |
|---|---|
| prod | prod 로깅 스택과 함께 collector·template·키를 만들고 prod overlay에 전송 환경 변수를 넣는다. sampling 비율은 prod 실측으로 정한다 |
