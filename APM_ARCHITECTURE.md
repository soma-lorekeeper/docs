# APM 아키텍처 — span을 모아 Kibana APM에서 호출 그림 보기까지

> 최신화: 2026-11-04
> 대상: gateway, authentication, content, ai-chat(API·summarizer), graph-rag가 만드는 trace(span)
> 상태: **dev 인프라 세팅 단계.** EDOT Collector·template·쓰기 계정을 세우고, 앱의 span 전송은 아직 꺼져 있다([11절](#11-남은-일)).
> prod는 prod 환경을 세팅할 때 같은 구성으로 옮긴다.
> 관련: trace 규약은 [LOG_SCHEMA.md 7절](LOG_SCHEMA.md#7-trace), span 마스킹은 [4절](LOG_SCHEMA.md#4-금지-항목과-마스킹),
> template은 [9절](LOG_SCHEMA.md#9-index-template-logscustom), 로그 흐름은 [LOGGING_ARCHITECTURE.md](LOGGING_ARCHITECTURE.md),
> 매니페스트는 `loresentry-gitops/logging/base/otel-collector/`(설명은 `logging/README.md`의 "APM")

로그와 trace는 같은 `trace.id`로 묶이지만 하는 일이 다르다.

- **로그**: 무슨 일이 있었나. 줄마다 메시지·상태·ID·오류가 있다. Filebeat → Logstash → ES `logs-*`로 간다.
- **trace**: 누가 누구를 불렀고 어디서 시간이 걸렸나. 작업 하나가 span 하나이고, span이 부모·자식으로 이어져 한 요청의 호출
  그림이 된다. 앱 SDK → **EDOT Collector** → ES `traces-*.otel-*`로 간다. Kibana APM이 이것으로 서비스 목록, 지연 시간,
  trace 폭포 그림을 그린다.
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
| Java 서비스 3개 | Spring Boot 4 `spring-boot-starter-opentelemetry`(Micrometer Tracing → OTel bridge) | namespace dev | 요청·호출·Kafka 소비마다 span을 만들고 OTLP로 보낸다(전송 설정은 환경 변수) |
| Python 서비스 3개 | OpenTelemetry SDK 1.45.1, 계측 0.66b1(FastAPI·httpx·psycopg) | namespace dev | 같은 일. Kafka 레코드는 헤더에서 직접 이어받는다 |
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

  B->>GW: POST 최신화 (외부 traceparent는 숨긴다)
  Note over GW: 새 trace 시작 (root span)
  GW->>CT: HTTP + traceparent
  Note over CT: outbox 행에 traceparent 저장
  CT-->>GW: 응답
  CT->>K: relay가 행의 traceparent로 발행
  K->>GR: 레코드 헤더의 traceparent를 이어받음
  Note over GR: InboxEvent에 traceparent 저장 → 처리 → 완료 이벤트
  GR->>K: 완료 이벤트 (같은 trace)
  K->>CT: listener가 이어받아 완료 처리
  GW-->>COL: span (서비스마다 따로 보냄)
  CT-->>COL: span
  GR-->>COL: span
```

- **시작점**: gateway가 외부 요청마다 새 trace를 연다. 스케줄·폴링은 1회마다 새 trace다.
- **저장했다 나중에 처리**: outbox 행, ai-chat `summary_queue`, graph-rag `InboxEvent`가 저장 시점의 `traceparent`를 갖고 있어,
  나중에 처리해도 같은 trace에 붙는다.
- **로그와 잇기**: 모든 로그 줄의 `trace.id`·`span.id`가 span의 ID와 같다. APM에서 느린 trace를 찾아 그 `trace.id`로 Discover에서
  줄을 보고, 반대로 오류 줄의 `trace.id`로 APM의 호출 그림을 연다.

## 4. collector 안의 처리

```mermaid
flowchart LR
  R["otlp receiver<br/>4317 · 4318"] --> M["memory_limiter<br/>한도 80%"] --> T["transform/privacy<br/>지우기 · 가리기"] --> BT["batch<br/>1,000개 · 1초"] --> P["elasticapm processor<br/>APM 필드 보강"]
  P --> E[("elasticsearch exporter<br/>mapping otel")]
  P --> C["elasticapm connector<br/>집계 지표"] --> E
```

| 단계 | 하는 일 |
|---|---|
| `memory_limiter` | 컨테이너 메모리 한도의 80%에 가까워지면 받기를 잠시 거절한다. 앱 SDK가 다시 보낸다 |
| `transform/privacy` | 로그에 남기지 않는 값을 지우고, 남은 값을 가린다(아래 표) |
| `batch` | 묶어서 ES에 보낸다 |
| `elasticapm` processor | span을 Kibana APM이 읽는 모양으로 보강한다(`transaction.*`·`span.*`, `processor.event`, 결과·성공 여부) |
| `elasticapm` connector | span으로 서비스·트랜잭션·호출 대상별 지연·건수·오류율을 1분·10분·60분 단위로 집계한다. APM 목록 화면이 이것을 읽는다 |
| `elasticsearch` exporter | OTel 원래 모양(`mapping.mode: otel`)으로 data stream에 쓴다 |

민감한 값 처리([LOG_SCHEMA.md 4절](LOG_SCHEMA.md#4-금지-항목과-마스킹)):

| 처리 | 대상 |
|---|---|
| 지우기 | URL 쿼리(`url.full`·`url.original`·`http.url`·`http.target`의 `?` 뒤, `url.query`), SQL(`db.statement`·`db.query.text`), 예외 event의 `exception.message`·`exception.stacktrace` |
| 가리기 | 남은 모든 span·event 문자열 속성. 이메일, OAuth `code`·`state`, 광고 클릭 ID, 서명 URL, 세션 쿠키, 인증 헤더, JWT, 비밀번호, DB 키 값 |

- 예외는 `exception.type`만 남는다. 스택은 같은 `trace.id`의 앱 로그 `error.stack_trace`(메시지 없는 스택)로 본다.
- HTTP 헤더와 요청·응답 본문은 계측 라이브러리가 기본으로 담지 않는다. 그 설정을 켜지 않는다.

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
| 디스크 | 로그와 같은 ES 20Gi를 나눠 쓴다 | span 전송을 켠 뒤 하루 저장량을 재고, 모자라면 디스크를 늘린다(gp3는 그 자리에서 늘어난다) |

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

- OTel 데이터의 제약: span의 스택 trace는 보이지 않는다(어차피 지운다). Kafka 소비 같은 messaging span은 transaction으로 보일 수 있다.
- 자주 쓰는 길: 오류 줄의 `trace.id` → APM 검색창에 `trace.id : "<값>"` → 폭포 그림에서 느린 span 확인 → 그 span의 서비스 로그를
  Discover에서 같은 `trace.id`로 본다.

## 8. 자원과 배치

| 항목 | 값 |
|---|---|
| collector | 1대, 요청 100m·256Mi, 한도 500m·512Mi, `GOMEMLIMIT` 400MiB |
| 로깅 노드 | `r7i.large`(2 vCPU·16GiB) 1대에 ES·Logstash·Kibana·Filebeat·collector가 함께 뜬다 |
| CPU 예약 | collector 전 1,610m/1,930m(83%). collector 요청을 작게 둔 이유다 |
| 부하 테스트 때 최고치(2026-11-04) | 노드 CPU 1,950m(100%, Logstash 1,546m·ES 950m), ES 메모리 2,894Mi/3Gi, Kibana 1,506Mi/1,536Mi |

노드는 그대로 두고 다음 부하 테스트 지표를 근거로 증설을 정한다. 관리형 노드 그룹은 인스턴스 타입을 그 자리에서 바꿀 수 없어,
새 노드 그룹(같은 2b·label·taint)을 만들고 이전 노드를 drain한다.

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
| 앱 쪽 전송 | workload overlay의 환경 변수(Java), 앱 코드의 exporter 연결(Python)([11절](#11-남은-일)) |

설정을 바꾼 뒤에는 로컬에서 ES 9.5.5(보안 켬)와 같은 collector 이미지에 시험 span을 보내, ES 문서에 지운 값이 없는지 확인한다.

## 10. 장애가 나면

| 상황 | 동작 |
|---|---|
| collector가 멈춤·재시작 | 앱 SDK가 잠시 다시 보내다가 못 보낸 span은 버린다. 요청 처리와 로그는 영향이 없다. collector 메모리 안의 보내기 대기분은 사라진다 |
| ES가 멈춤 | exporter가 메모리 큐에서 다시 시도하다가, 큐가 차면 span을 버린다. 로그는 Logstash 디스크 큐에 따로 쌓인다 |
| 메모리 한도 근처 | `memory_limiter`가 받기를 거절하고 SDK가 다시 보낸다 |
| API key 무효·만료 | ES가 401을 돌려주고 span이 버려진다. collector 로그(`logs-kubernetes.container-*`의 `edot-collector` 파드)에 남는다 |
| Secret 없음 | collector 파드가 `CreateContainerConfigError`로 기다린다 |

## 11. 남은 일

| 일 | 내용 |
|---|---|
| Java 전송 켜기 | 앱 코드는 그대로 두고 workload dev overlay에 환경 변수(`MANAGEMENT_TRACING_EXPORT_OTLP_ENABLED=true`, collector 주소)를 넣는다 |
| Python 전송 켜기 | ai-chat·graph-rag는 SDK `TracerProvider`만 있고 exporter가 없다. OTLP exporter 패키지를 더하고, 환경 변수가 있을 때 `setup_tracing`이 exporter를 붙이게 고친 뒤 환경 변수를 넣는다 |
| 빈 스케줄 span | Spring `@Scheduled`는 실행마다 span을 연다. 1초 relay도 빈 회차마다 span이 생겨, 전송을 켜면 하루 수십만 개가 쌓인다. LOG_SCHEMA 7절(빈 순회는 span도 남기지 않는다)과 맞추도록 끄거나 거른다 |
| 확인 | Kibana APM에 서비스 6개, 최신화 흐름의 폭포 그림, traces·`logs-*.otel-*` 반대 검색(이메일·JWT·`code=`·검색어) 0건, 하루 저장량과 로깅 노드 자원 |
| prod | prod 로깅 스택과 함께 collector·template·키를 만든다. sampling 비율은 prod 실측으로 정한다 |
