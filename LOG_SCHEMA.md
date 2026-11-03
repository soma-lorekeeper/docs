# 로그 스키마 — ECS 필드·레벨·마스킹·data stream 규약

> 최신화: 2026-11-03
> 대상: 클러스터 안 모든 파드 로그와 AWS 관리형 리소스(ALB·CloudFront·RDS·Neptune·EKS) 로그, 앱이 보내는 trace(span)
> 상태: **규약 확정.** 로그 수집 스택(ECK)은 dev에 배포했다. 앱이 이 규약대로 로그·trace를 내는 작업은 dev부터 적용 중이다.
> 관련: 매니페스트와 index template은 `loresentry-gitops/logging/`(`base/elasticsearch/logs-custom.json`)

클러스터 안 모든 로그와 AWS 관리형 리소스 로그를 Elasticsearch에 넣을 때의 규약이다. 로그를 내보내는 앱, 가공하는
Logstash, 저장하는 ES index template이 모두 이 문서를 따른다.

- 기준: **ECS 8.11**. 앱의 로그 formatter와 Logstash가 `ecs.version: 8.11`을 쓰고, 여기서 쓰는 ECS 필드는 8.11에 모두 있다.
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

앱 span (OTLP) ─▶ EDOT Collector: APM 가공 · 마스킹 ─▶ Elasticsearch traces·metrics (30일) ─▶ Kibana APM
```

| 단계 | 하는 일 | 하지 않는 일 |
| --- | --- | --- |
| 앱 | 아래 [앱 로그 규약](#5-앱-로그-규약)대로 로그 클래스로만 ECS JSON 한 줄씩 stdout에 쓴다. 금지 항목을 찍지 않는다. [7절](#7-trace)대로 span을 만들고 전파한다 | 마스킹을 Logstash·collector에 기대지 않는다 |
| Filebeat | 줄 읽기, CRI 해제, 여러 줄 묶기, `kubernetes.*` 붙이기 | 파싱·가공 |
| Logstash | 파싱, `log.level` 정규화, 필수 필드 채우기, 마스킹, 소음 제거, data stream 선택 | — |
| EDOT Collector | span 받기(OTLP), APM 화면용 지표 만들기, span 속성 마스킹 | 로그를 받지 않는다 |
| ES | 필드 타입(`logs@custom`, traces·metrics `@custom`), 보존 기간, 거부된 문서 보관(failure store) | ingest pipeline을 두지 않는다 |

## 2. 공통 필드

### 2.1 모든 로그 (항상 있음)

출처와 관계없이 모든 로그 문서에 있다. 앱이 보내지 않은 값은 Logstash가 채운다.

| 필드 | 타입 | 값 | 비어 있을 때 Logstash가 채우는 값 |
| --- | --- | --- | --- |
| `@timestamp` | date_nanos | 이벤트 시각(UTC, 나노초까지) | 컨테이너 런타임이 줄마다 붙인 시각(CRI, 나노초). AWS 로그는 원본 시각 |
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
| `container.name` | keyword | 컨테이너 이름 | Logstash (Filebeat가 붙인 `kubernetes.container.name`을 복사) |
| `container.id` | keyword | 컨테이너 ID | Filebeat |
| `host.name` | keyword | 노드 호스트 이름 | Logstash (`kubernetes.node.name`. Filebeat가 붙이는 값은 Filebeat 파드 이름이다) |
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
| `tags` | keyword | 문서가 거친 처리나 앱이 정한 분류. 아래 규칙을 따른다 |

`tags`는 Logstash와 앱이 함께 쓰는 칸이라 이름으로 출처를 나눈다.

| 시작 | 붙이는 쪽 | 값 |
| --- | --- | --- |
| `_`(밑줄) | 파이프라인 | `_truncated`(길이 상한). 파싱 실패는 Logstash 기본 태그를 그대로 쓴다: `_grokparsefailure`, `_dissectfailure`, `_jsonparsefailure`, `_dateparsefailure`, `_rubyexception`. 어느 단계에서 실패했는지가 이름에 드러난다 |
| 그 밖 | 앱 | 로그 종류 분류. 정해 둔 이름만 쓴다. `health`: 헬스체크 요청(`/health`, `/health/**`) 흐름의 모든 줄([4절 소음 제거](#소음-제거)). Java는 SLF4J marker로 내보낸다 |

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
| Spring 3개 (ECS JSON `log.level`) | `TRACE`, `DEBUG`, `INFO`, `WARN`, `ERROR` | 소문자로. Logback에는 `FATAL`이 없다 |
| Python 앱 (ECS JSON `log.level`) | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` | `WARNING`→`warn`, `CRITICAL`→`fatal`, 나머지 소문자 |
| 레벨 없이 응답 상태만 있는 접근 로그 | `http.response.status_code` | 5xx `error`, 4xx `warn`, 그 외 `info` |
| graph-rag (로깅 설정 없음, 레벨 없이 메시지만 출력) | 없음 | `unknown`. 앱 로그 규약을 적용하면 사라진다 |
| Kafka 브로커, Strimzi operator (log4j) | `TRACE` … `FATAL` | 소문자로 |
| Valkey | `.` `-` `*` `#` | `debug`, `debug`, `info`, `warn` |
| PostgreSQL | `DEBUG1-5`, `LOG`, `INFO`, `NOTICE`, `WARNING`, `ERROR`, `FATAL`, `PANIC` | `debug`, `info`, `info`, `info`, `warn`, `error`, `fatal`, `fatal`. 느린 쿼리(`duration:`)와 잠금 대기(`still waiting for`)는 `warn`으로 올린다 |
| Neptune slowquery | 없음 | `warn`. `exceptionClass`가 있으면 `error` |
| ALB, CloudFront | 없음 | 응답 5xx `error`, 4xx `warn`, 그 외 `info` |
| EKS authenticator | `level=` 값 | 소문자로 |
| 그 밖의 파드 | JSON의 `level`·`log.level` 값, logfmt `level=`, klog 첫 글자 `I`·`W`·`E`·`F`, Redis 형식 기호(argocd-redis), Go `panic:`, 줄 앞 40자 안의 대문자 레벨 단어 | 위 단어·기호 규칙에 더해 `notice`→`info`, `critical`→`fatal`, zap `dpanic`→`error`·`panic`→`fatal`. 못 찾으면 `unknown` |

- 우리 서비스(6절의 `loresentry.*`)는 `"ecs":{...}`가 든 ECS JSON 줄만 파싱한다. 지금의 평문 줄(Spring 기본 형식, uvicorn)과
  ECS가 아닌 JSON(summarizer)은 그대로 들어가고 `log.level`은 `unknown`이다. [앱 로그 규약](#5-앱-로그-규약)을 적용하면 사라진다.
- 2026-11-01 dev의 Loki 표본(84개 컨테이너, 2559줄)으로 확인한 `unknown` 비율: `argocd` 1%, `kube-system` 5%,
  `monitoring` 2%, `ambassador` 0%. 남은 것은 레벨이 없는 줄(시작 스크립트 출력, Go 기본 `log` 형식, Python traceback)이다.
- 그 밖의 파드는 줄을 바꾸지 않고 레벨만 찾는다. JSON이어도 필드로 펼치지 않는다. 남의 JSON이 `service.name`·`event.dataset`
  같은 필드를 덮어쓰거나 필드 수를 늘리지 않게 하기 위해서다.

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

`user.id`(auth `users.id` UUID)와 도메인 ID(프로젝트·문서·세션 대화·설정 검사 등의 UUID, 문서 저장의 `X-Save-Id`)는 남겨도 된다.
UUID만으로는 사람이나 내용을 특정할 수 없다. 다만 이름에 ID가 붙어도 인증에 쓰는 값(세션 ID인 `ls_session` 쿠키 값, 동의 요청 ID,
OAuth `login_request_id`·`state`)은 그 값만으로 로그인 과정을 이어 갈 수 있으므로 남기지 않는다.

### Logstash 마스킹 규칙

규칙의 원칙:

- 앱이 실수로 남긴 값을 막는 두 번째 방어선이다. 첫 번째 방어선은 로그 클래스다. `LogField`가 문자열 입력값·결과를
  기본으로 가린다([5절 입력값과 결과](#입력값과-결과-param-result)).
- **파싱이 성공했는지와 상관없이** `message`, `event.original`, `url.query`, `url.original`, `http.request.referrer`,
  `error.message`, `error.stack_trace`에 적용한다. 앱 필드 `loresentry` 아래의 모든 문자열(`loresentry.param.*`,
  `loresentry.result.*` 포함, 배열 안 포함)에도 적용한다.
- ES나 S3로 갈라지기 전에 적용한다.

| 대상 | 찾는 것 | 바꾼 값 |
| --- | --- | --- |
| 이메일 | `로컬@도메인` 형태 | `[email]` |
| OAuth | 쿼리 `code=`, `state=`의 값 | `[redacted]` |
| 광고 클릭 ID | 쿼리 `gclid=`, `gbraid=`, `wbraid=`의 값 (이름과 `utm_*`는 남김) | `[redacted]` |
| 서명 URL | 쿼리 `X-Amz-Signature=`, `X-Amz-Credential=`, `X-Amz-Security-Token=`, `Signature=`의 값 (대소문자 무시, `X-Amz-Expires` 등은 남김). S3 presigned URL, CloudFront 서명 URL | `[redacted]` |
| 세션·동의 쿠키 | `ls_session=`, `ls_consent=`의 값 (`__Host-` 접두어 포함) | `[redacted]` |
| 인증 헤더 | `Cookie:`, `Set-Cookie:`, `Authorization:` 뒤의 값 (줄 끝까지, JSON의 `"authorization": "..."` 포함) | `[redacted]` |
| JWT | `eyJ`로 시작하고 점으로 나뉜 세 조각 | `[jwt]` |
| 비밀번호 | `PASSWORD '...'` | `PASSWORD '[redacted]'` |
| DB 키 값 | `Key (칼럼)=(값)` (고유 키·외래 키 위반의 `DETAIL`) | `Key (칼럼)=([redacted])` |

마스킹을 ES ingest pipeline에서 하지 않는 이유:

- S3 보관본은 ES를 거치지 않는다.
- ingest pipeline이 실패하면 failure store가 pipeline을 적용하기 전의 원본을 남긴다.
- 전용 처리기 `redact`는 Basic 라이선스에서 동작하지 않는다.

이름, 본문처럼 패턴으로 잡을 수 없는 것은 앱이 찍지 않는 것으로만 막는다. 대신 정기적으로 반대 검색(이메일 패턴,
`code=`, `PASSWORD` 등)을 돌려 ES와 S3에서 0건인지 확인한다.

### trace(span) 마스킹

로그는 Logstash가, trace는 EDOT Collector가 마스킹한다. span은 Logstash를 거치지 않고 collector에서 ES로 바로 가기 때문이다.

- collector가 ES에 쓰기 전에 위 표의 규칙(이메일, OAuth, 광고 클릭 ID, 서명 URL, JWT, 비밀번호, DB 키 값)을 적용한다.
- 대상 span 속성: `url.full`, `url.query`, `exception.message`, `exception.stacktrace`, `db.query.text`·`db.statement`
- HTTP 헤더와 요청·응답 본문은 계측 라이브러리가 기본으로 담지 않는다. 그 설정을 켜지 않는다.
- traces에도 같은 반대 검색을 돌린다.

### 길이 상한

- ES로 가는 쪽에서만, `message`·`error.stack_trace`·`event.original` 각각 **256KB**를 넘으면 앞부분만 남기고 `tags`에
  `_truncated`를 붙인다.
- S3 보관본은 마스킹된 전문을 그대로 둔다.
- 개인정보 대책이 아니라 ES 저장·검색 비용 대책이다. 평소 로그는 2KB 이하이고 긴 예외도 수십 KB라 영향이 없다.
  Kafka payload(최대 6MB)를 통째로 찍는 사고만 걸린다.
- 참고로 Filebeat에도 메시지당 10MB 기본 상한(`message_max_bytes`)이 있다.

### 소음 제거

| 버리는 것 | 이유 |
| --- | --- |
| 헬스체크 흐름의 `info` 줄(`tags: health`) | kubelet probe가 파드마다 시간당 수백 번 부른다. 성공 여부는 Prometheus와 파드 이벤트가 이미 기록한다. 앱은 헬스체크 요청의 모든 줄(접근 로그, 계층 진입 로그, 내부 서비스 호출)에 `health` 태그를 붙인다. 실패한 헬스체크는 결과 줄과 원인 줄이 `warn`·`error`가 되므로 남는다. span은 버리지 않는다 |
| RDS `rdsadmin` 계정의 연결·해제 줄 | AWS 내부 관리 연결이다 |

## 5. 앱 로그 규약

각 서비스가 맞추는 규약이다. 이 규약을 적용하기 전의 평문 줄은 그대로 들어가고 `log.level`은 `unknown`이다.
dataset은 파드 label로 정하고, `service.name`은 앱이 보낸 값(없으면 파드 label)을 쓴다.

### 로그 클래스

- 서비스마다 로그 클래스 `AppLogger`를 하나 둔다.
  - Java: `com.loresentry.<서비스>.logging.AppLogger`
  - Python: `app/logs.py`의 `AppLogger`
  - 서비스 코드는 이 객체의 메서드로만 로그를 찍는다. SLF4J·`logging`·`print`·`System.out`을 직접 쓰지 않는다. Java는 ArchUnit, Python은 ruff 테스트로 막는다.
- 레벨은 `info`·`warn`·`error`만 쓴다. 프레임워크 로그는 원래 레벨 그대로 들어간다.
- **만들기**: 요청·flow 시작점에서 그때 아는 값(`trace.id`, `span.id`, `user.id`)을 생성자 인자로 넘겨 만든다.
  - 시작점은 HTTP 필터·미들웨어, Kafka 레코드 하나, 스케줄 1회다.
  - 만든 객체는 요청 범위 저장소(Java ThreadLocal, Python contextvars)에 넣고, 아래 계층은 `AppLogger.current()`로 꺼낸다.
  - 스레드를 옮기는 곳(비동기 실행기, 처리 스레드)은 객체를 함께 옮긴다.
- **필드**: 정해 둔 필드만 받는다(Java `LogField` 팩토리, Python 허용 목록).
  - 임의의 이름·값을 받지 않으므로 이메일·이름·본문을 필드로 넣을 자리가 없다.
  - 새 필드는 아래 [필드](#필드) 표에 먼저 추가한다.
- **자동으로 채우는 필드**: 그 줄을 찍은 위치. `log.logger`(클래스·모듈), `log.origin.function`(메서드·함수),
  `log.layer`(레이어), `log.package`(패키지). 아래 [레이어와 위치](#레이어와-위치)를 따른다.
- **무엇을 남기나**
  - `info`: 정상 흐름을 자주 남긴다. 대상은 시작점(요청·레코드 받음, 스케줄 시작), 계층 진입(controller → service → client·repository·publisher), 상태 변화(저장, 발행, 상태 전이), 완료(결과·건수·걸린 시간)다. 지금 그 서버가 무엇을 하고 있는지 보이도록 도메인 ID를 함께 남긴다.
  - **레이어 클래스의 모든 public 메서드**는 첫 줄에 진입 로그를 남긴다. 한 줄로 넘기기만 하는 메서드도 남긴다. 그래서 trace 하나를
    시간 순으로 놓으면 controller → service → client·repository 흐름이 줄 단위로 보인다. 분기·대체·결과가 있는 곳에는 결과 줄을 더한다.
  - 예외는 값을 돌려주기만 하면서 한 요청 안에서 여러 번 불리는 getter뿐이다. 이유를 적어 표시한다(Java `@SkipEntryLog("...")`).
    줄을 남겨도 흐름에 새 정보가 없고 같은 줄만 쌓이기 때문이다.
  - `warn`: 처리했지만 예상 밖인 상황. 재시도, 충돌(409), 검증 실패, 건너뛴 이벤트, 느린 외부 호출이 해당한다.
  - `error`: 예외로 작업 하나가 실패했을 때.
- **문구**: 짧은 영어 소문자 문장으로 쓰고, 값은 문구가 아니라 필드에 넣는다. 검색·알림 키로 쓰는 기존 문구는 바꾸지 않는다.

  | 레이어 | 문구 | 예 |
  | --- | --- | --- |
  | controller | `<대상> <동작> requested` | `project update requested` |
  | service·repository | `<대상> <동작>` | `chat usage get` |
  | client | `<상대> <대상> <동작> call` | `ai-chat usage get call` |
  | 결과 줄 | `<대상> <동작> completed` 또는 상태 이름 | `document save conflict` |
- **접근 로그**: 시작점이 `request received`와 `request completed`를 남긴다. 완료 줄(결과 줄)에는 `http.response.status_code`, `event.duration`이 들어간다.
  - 요청 하나의 **모든 줄**에 브라우저가 부른 실제 경로 `url.path`와 경로 템플릿 `http.route`(예: `/projects/{projectId}`)가 붙는다.
    시작점이 요청을 받을 때 Spring이 고를 템플릿을 미리 찾아 로그 클래스에 넣기 때문에, 세션 확인처럼 컨트롤러 앞의 줄과 거절된 요청에도 붙는다.
    맞는 경로가 없는 요청(404)에는 `http.route`가 없다. `url.path`는 실제 경로라 ID가 섞여 엔드포인트별로 묶을 때는 `http.route`를 쓴다.
  - 레벨은 5xx `error`, 4xx `warn`, 그 외 `info`다.
  - 헬스체크도 다른 요청과 같은 줄을 남기고 span도 만든다. 모든 줄에 `tags: health`가 붙고, `info` 줄은 Logstash가 버린다.
  - **실패의 결과는 이 한 줄이다.** 예외 처리기·보안 응답은 실패를 따로 찍지 않고 이유(`error.type` 우리 오류 코드, `error.code` 상대가 준 코드)를 로그 클래스에 맡긴다. 결과 줄이 그 필드를 붙인다. 그래서 `log.level: error` 건수가 실패 요청 수와 같다.
  - 실패의 원인(어느 호출·단계에서 무엇이 잘못됐나)은 그 자리에서 `warn`으로 따로 남긴다. 예: `upstream call failed`, `account deletion stopped: …`
  - 처리되지 않은 예외는 결과 줄 대신 `request failed with an unhandled exception`을 예외와 함께 한 줄 남긴다.
  - uvicorn 기본 접근 로그는 끈다.
  - 다른 내부 서비스를 부른 결과도 한 줄 남긴다(`upstream call completed`). 대상 호스트는 `url.domain`, 대상 주소(쿼리 제외)는
    `url.full`이다(ECS·OpenTelemetry HTTP client 이름). 이 줄의 `url.path`·`http.route`는 다른 줄과 같은 브라우저 요청의 값이라,
    같은 줄의 `url.*`가 두 URL을 가리킨다. 내부 서비스 쪽 경로는 `url.full`로 본다.

### 레이어와 위치

| 필드 | 값 | 정하는 방법 |
| --- | --- | --- |
| `log.logger` | 그 줄을 찍은 클래스(Python은 모듈) | 로그 클래스가 호출 위치에서 읽는다. 프레임워크 줄은 Logback logger 이름. 내부 서비스 호출 결과 줄은 실제로 HTTP를 보낸 HTTP client 클래스다(예: Spring `DefaultRestClient$DefaultResponseSpec`). 그 호출을 부른 우리 client 메서드는 바로 앞 진입 줄에 있다 |
| `log.origin.function` | 그 줄을 찍은 메서드·함수 | 로그 클래스가 호출 위치에서 읽는다. 람다 안에서 찍은 줄은 람다를 감싼 메서드 이름이다(Java가 만든 `lambda$send$3`이 아니라 `send`). 내부 서비스 호출 결과 줄은 우리 코드가 부른 HTTP client 메서드다(예: `body`) |
| `log.layer` | `controller`·`service`·`repository`·`client`·`consumer`·`publisher`·`filter`·`handler`·`interceptor`·`security`·`other` | 클래스 이름 끝으로 정한다(아래 표). 맞지 않는 클래스는 직접 지정한다(Java `@LogLayer("...")`) |
| `log.package` | `log.logger` 클래스의 패키지(예: `com.loresentry.gateway.web.chat`) | 로그 클래스가 읽는다 |

- 위치는 우리 코드가 부른 곳이다. 접근 로그(`request received`·`request completed`)는 우리 코드가 아니라 웹 서버가 부르는 필터 줄이라
  위치가 필터 그대로다(`log.layer: filter`).
- 내부 서비스 호출 결과 줄의 `log.layer`는 `client`다. 로그 레벨 설정은 그 줄을 찍은 로그 클래스(우리 패키지) 기준이라, 프레임워크 패키지의
  로그 레벨을 낮춰도 이 줄은 사라지지 않는다.

| 클래스 이름 끝 | `log.layer` |
| --- | --- |
| `Controller` | controller |
| `Service` | service |
| `Repository` | repository |
| `Client` | client |
| `Listener`, `Consumer` | consumer |
| `Publisher` | publisher |
| `Filter` | filter |
| `Handler` | handler |
| `Interceptor` | interceptor |
| 그 밖 | other(직접 지정하지 않았을 때) |

위치 정보(클래스·메서드·레이어·패키지)는 모두 ECS `log` 묶음 아래에 둔다. `service.*`는 서비스 자체(이름·버전·환경)를 뜻하므로 쓰지 않는다.

### 입력값과 결과 (param, result)

| 필드 | 무엇 |
| --- | --- |
| `loresentry.param.<이름>` | 메서드에 들어온 값과 처리 중의 중간값 |
| `loresentry.result.<이름>` | 처리 결과 |

값은 로그 클래스(`LogField.param`·`result`)가 종류별로 이렇게 바꾼다. 서비스 코드는 값을 넘기기만 하고 가리는 일은 빠뜨릴 수 없다.

| 넘긴 값 | 남는 모양 | 예 |
| --- | --- | --- |
| 숫자·불리언·enum·UUID | 그대로 | `param.base_revision: 12`, `result.status: CONFLICT` |
| 문자열 | 늘 `[redacted]`와 길이 | `param.title: "[redacted]"`, `param.title_length: 7` |
| 허용 목록이 있는 코드 문자열(`LogField.code`) | 목록 안이면 그대로, 밖이면 `other` | `param.verdict: DISMISSED` |
| 컬렉션·Map | 개수 | `param.drafts_count: 3` |
| JSON 본문 | 바이트 수 | `param.body_bytes: 48213` |
| 그 밖의 객체(DTO 등) | 받지 않는다(Java는 컴파일 오류) | 필요한 숫자·코드만 따로 넘긴다 |

- 이름은 파라미터 이름을 snake_case로 쓴다.
- 같은 이름은 늘 같은 타입으로 쓴다. ES는 처음 들어온 값으로 타입을 정하고, 서비스마다 타입이 다르면 Kibana에 충돌로 보인다.
- 값으로 검색할 수 있다. 예: `loresentry.param.verdict : "DISMISSED"`, `loresentry.param.body_bytes > 100000`.
  가린 값은 내용으로 찾을 수 없다.

### ID를 나누는 기준

한 줄의 ID는 "이 줄이 무엇에 대한 로그인가"로 나눈다.

| 순서 | 질문 | 그렇다면 |
| --- | --- | --- |
| 1 | 이 줄이 다루는 **대상**인가(읽기·저장·삭제·생성하는 바로 그것) | 전용 필드 `loresentry.<종류>_id` |
| 2 | 대상이 속한 **범위**인가(대상이 들어 있는 프로젝트, 메모가 붙은 문서) | 전용 필드 |
| 3 | 처리하면서 **새로 만든** 대상인가 | 결과 줄에서 전용 필드 |
| 4 | 대상이 아니라 **참조하는 다른 것**인가(이동할 위치, 복사 원본, 연결 상대, 보고 있던 문서) | `loresentry.param.<역할>_<종류>_id` (예: `param.target_folder_id`) |
| 5 | 같은 종류가 한 줄에 둘 이상인가 | 대상 하나만 전용 필드, 나머지는 역할 이름을 붙여 param |

- 같은 값을 두 곳에 넣지 않는다.
- 전용 필드는 [필드](#필드) 표의 종류만 쓴다. `loresentry.project_id : X` 하나로 그 대상의 줄이 서비스·레이어와 관계없이 모인다.
- `user.id`는 늘 ECS 필드다.

| 요청·작업 | 전용 필드 | param |
| --- | --- | --- |
| 문서 내용 저장 `PUT /files/{fileId}/content` | `file_id`, `save_id` | |
| 버전 복원 `/files/{fileId}/versions/{versionId}/restore` | `file_id`(범위), `version_id`(대상) | |
| 파일 이동 | `file_id` | `target_folder_id` 또는 `target_folder_code` |
| 채팅 메시지 보내기 | `chat.session_id` | `focus_file_id` |
| 파일 만들기의 결과 줄 | `project_id`, `file_id`(새로 만든 것) | |
| 문서 관계 저장(A와 B 연결) | `project_id`, `document_id`(A) | `related_document_id`(B) |

### 형식

- stdout에 **한 줄에 ECS JSON 하나**를 쓴다. 여러 줄 예외는 `error.stack_trace` 한 필드에 담는다.
- JSON 객체는 **중첩**해서 쓴다(`{"log":{"level":"INFO"}}`). 점이 든 키(`{"log.level":"INFO"}`)는 Logstash가 `log.level`
  필드로 읽지 못한다. Spring Boot 4의 ECS 출력은 MDC 값까지 중첩 형식이다.
- 모든 줄에 `"ecs":{"version":"8.11"}`을 넣는다. Logstash는 이 표시가 있는 JSON 줄만 필드로 펼친다.
- ECS 객체 이름(`host`, `service`, `event`, `log`, `error`, `http`, `url`, `user` 등)을 다른 뜻의 문자열 키로 쓰지 않는다.
  예를 들어 `"host": "kafka:9092"`는 `host.name`과 부딪혀 공통 필드 채우기가 실패한다(`_mutate_error`).
- 필드 이름은 ECS 이름을 쓰고, ECS에 없는 것은 `loresentry.*` 아래에 snake_case로 둔다. 예외는 두 가지다.
  - OpenTelemetry에 같은 뜻의 이름이 있으면 그 이름을 쓴다: `http.route`. APM span 속성과 이름이 같아진다.
  - 로그를 찍은 위치를 설명하는 값은 ECS `log` 묶음 아래에 둔다: `log.layer`, `log.package`.
- Spring: 서비스의 ECS formatter 클래스(`logging.structured.format.console`에 클래스 이름)를 쓴다.
  - Boot 내장 `ecs` 형식은 key-value·MDC 이름이 자기가 쓰는 객체(`log`, `error`)와 겹치면 그 줄을 통째로 버린다. 그래서 `log.origin.function`, `error.type`을 함께 낼 수 없다.
  - formatter는 모든 값을 하나의 중첩 객체에 합치고, 먼저 들어간 필수 필드를 key-value가 바꾸지 못하게 한다.
  - 프레임워크 로그(Spring, Tomcat, Kafka client, Flyway)도 같은 formatter를 거친다.
  - `service.name`은 Kubernetes 이름으로 맞춘다(`logging.structured.ecs.service.name`). `spring.application.name`(`gateway`·`authentication`·`content`)은 Kafka client-id 등이 쓰므로 그대로 둔다.
- Python: 같은 필드를 내는 formatter를 root·uvicorn 로거에 단다. ai-chat의 `time`·`level`·`logger`는 `@timestamp`·`log.level`·`log.logger`로 바꾼다.
- `service.version`은 이미지 태그(`build-<run>-<attempt>`)다. CI가 `docker build --build-arg APP_VERSION=<태그>`로 넘기고, Dockerfile이 `ENV APP_VERSION`으로 둔다.

### 필드

| 구분 | 필드 | 타입 | 언제 |
| --- | --- | --- | --- |
| 필수 | `@timestamp`, `log.level`, `log.logger`, `message`, `service.name`, `service.version`(이미지 태그), `ecs.version` | | 항상 |
| 호출 위치 | `log.origin.function`, `log.layer`, `log.package`, `process.thread.name` | keyword | 함수·레이어·패키지는 로그 클래스로 찍은 줄, 스레드 이름은 모든 줄 |
| 추적 | `trace.id`, `span.id` | keyword | 요청이나 이벤트를 처리하는 중일 때([7절](#7-trace)) |
| 에러 | `error.type`(예외 클래스), `error.message`, `error.stack_trace` | keyword, match_only_text, wildcard | 예외를 남길 때 |
| HTTP | `http.request.method`, `url.path`(브라우저가 부른 실제 경로, 쿼리 제외), `http.route`(경로 템플릿, keyword), `http.response.status_code`, `event.duration`(ns) | | 요청 처리를 남길 때. `url.path`·`http.route`는 요청의 모든 줄 |
| HTTP 연결 | `http.request.id`(ALB `X-Amzn-Trace-Id`), `url.domain`(다른 서비스를 부를 때의 대상 호스트), `url.full`(그 호출의 대상 주소, 쿼리 제외) | keyword | gateway 요청의 모든 줄, 나가는 호출 |
| 사용자 | `user.id` | keyword | auth `users.id` UUID만 |
| Kafka | `loresentry.kafka.topic`, `loresentry.kafka.partition`(long), `loresentry.kafka.offset`(long), `loresentry.kafka.key` | keyword | 메시지를 보내거나 받을 때 |
| CloudEvents | `loresentry.cloudevent.id`, `loresentry.cloudevent.type`, `loresentry.cloudevent.subject` | keyword | 이벤트를 다룰 때 |
| 도메인 ID | `loresentry.project_id`, `loresentry.document_id`, `loresentry.file_id`, `loresentry.episode_id`, `loresentry.memo_id`, `loresentry.version_id`(문서 버전), `loresentry.image_id`, `loresentry.save_id`(문서 저장의 `X-Save-Id`) | keyword | 해당 대상을 다룰 때 |
| 채팅 | `loresentry.chat.session_id`, `loresentry.chat.turn_id` | keyword | ai-chat. 인증 세션과 헷갈리지 않게 `chat` 아래에 둔다 |
| 최신화 | `loresentry.refresh.request_id`, `loresentry.refresh.run_id`, `loresentry.refresh.draft_id` | keyword | content, graph-rag, gateway |
| 설정 검사 | `loresentry.setting_check.run_id`, `loresentry.setting_check.finding_id` | keyword | ai-chat, gateway |
| 입력값·결과 | `loresentry.param.*`, `loresentry.result.*` | 문자열 keyword, 숫자 long·double | [입력값과 결과](#입력값과-결과-param-result) 규칙대로 |

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
3. Kafka 브로커(label `strimzi.io/component-type: kafka`) → `kafka.log`. entity-operator·kafka-exporter 파드에도
   `strimzi.io/kind: Kafka`가 붙어 있어 kind로는 고르지 않는다
4. Strimzi operator(label `strimzi.io/component-type: entity-operator` 또는 `strimzi.io/kind: cluster-operator`) →
   `strimzi.operator`. topic-operator·user-operator·cluster-operator는 `container.name`으로 나뉜다
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
| EKS authenticator | `eks` | `aws.eks_authenticator` | `logs-aws.eks_authenticator-dev` |

prod는 끝만 `-prod`로 바뀐다. 환경마다 15개다. 출처를 알 수 없는 AWS 줄(설정 실수, 켜지 않은 EKS 로그 종류)은 버리지 않고
`logs-aws.unknown-<env>`에 모은다. 여기에 쌓이면 규칙을 더한다.

AWS 로그의 주요 필드는 아래와 같다. ECS에 있는 필드는 ECS 이름을 쓰고, 출처에만 있는 값은 출처 이름 아래에 둔다.

| 출처 | ECS 필드 | 출처 필드 |
| --- | --- | --- |
| ALB | `http.request.method`, `url.original`·`url.path`·`url.query`·`url.domain`, `http.response.status_code`, `source.ip`, `destination.ip`, `user_agent.original`, `tls.version`, `event.duration`(처리 시간 합), `http.request.id`(`trace_id`) | `aws.alb.*` (대상 상태 코드, 오류 사유, 대상 그룹 등) |
| CloudFront (v2 JSON) | 위와 같은 HTTP 필드, `http.request.referrer`, `source.geo.country_iso_code`(`c-country`) | `aws.cloudfront.*` (엣지 위치, 결과 유형, 요청 ID, 캐시 동작 경로, 오리진 첫·마지막 바이트 지연 등). `cs(Cookie)`는 저장하지 않는다 |
| EKS authenticator | | `aws.eks.authenticator.arn`·`username` |
| Neptune | `event.duration`, `http.response.status_code`, `error.type`(`exceptionClass`) | `aws.neptune.*` (쿼리 fingerprint, 실행 통계) |

## 7. trace

요청이 서비스를 옮겨 가며 처리되는 흐름을 `trace.id` 하나로 이어 본다. 로그에서는 `trace.id`로 한 요청의 모든 줄을
모으고, 호출 그림(누가 누구를 불렀나, 어디서 시간이 걸렸나)은 Kibana APM에서 본다. dev부터 적용 중이다.

| 항목 | 규약 |
| --- | --- |
| 형식 | W3C Trace Context `traceparent: 00-<trace-id 32 hex>-<span-id 16 hex>-<flags>` |
| 헤더 이름 | HTTP와 Kafka 모두 `traceparent` (소문자 정확히. Kafka 헤더는 대소문자를 구분한다). [KAFKA_DESIGN.md](KAFKA_DESIGN.md)의 필수 헤더다. 자체 `traceId` 헤더는 쓰지 않는다. `tracestate`·`baggage`는 쓰지 않는다 |
| ID 생성 | 추적 라이브러리가 만든다. 128비트 무작위라 번호 관리가 필요 없다. Spring은 `spring-boot-starter-opentelemetry`(Micrometer Tracing의 OpenTelemetry bridge), Python은 OpenTelemetry SDK다. W3C 형식만 읽고 쓴다 |
| 시작점 | gateway가 외부 요청마다 새로 시작한다. 인터넷에서 온 `traceparent`·`tracestate`·`baggage`는 추적 라이브러리가 읽기 전에 숨긴다. 스케줄·폴링 작업은 1회마다 새로 시작한다. 1초 relay처럼 자주 도는 폴링은 처리할 일이 있을 때만 시작해, 빈 순회는 span도 로그도 남기지 않는다 |
| 서비스 간 HTTP | 계측 라이브러리(Spring RestClient observation, Python httpx 계측)가 `traceparent`를 붙인다. 라이브러리를 거치지 않는 호출(gateway의 채팅 SSE)은 코드가 직접 넣는다 |
| outbox | 요청 트랜잭션에서 그때의 `traceparent`를 행(`outbox_events.traceparent`, content·authentication)에 저장하고, relay가 그 값으로 헤더를 붙여 발행한다. relay 시점의 값을 쓰면 흐름이 끊긴다. 그래서 KafkaTemplate observation은 켜지 않는다(헤더가 두 번 붙는다) |
| 저장했다 나중에 처리 | 저장한 시점의 trace를 잇는다. ai-chat `summary_queue.traceparent`, graph-rag Neptune `InboxEvent`의 `traceparent`가 해당한다. 행 단위 로그는 행에 저장된 trace로 남긴다 |
| Kafka 소비 | 헤더의 값을 이어받는다. Spring은 listener observation, Python은 레코드마다 헤더에서 추출한다. 헤더가 없거나 형식이 틀리면 새 trace를 시작한다 |
| span | 작업(요청 받기, 다른 서비스 호출, DB 쿼리, Kafka 소비) 단위로 새로 생긴다. 서비스 안에서는 스택처럼 쌓였다 빠지고, 헤더에는 현재 span 하나만 실린다. 헬스체크도 span을 만든다(로그는 Logstash가 `info` 줄을 버린다) |
| 로그 필드 | `trace.id`, `span.id`. 로그 클래스로 찍은 줄은 시작점에서 받은 값, 프레임워크 로그는 그때 열려 있는 span의 값이다 |
| 응답 | 모든 서비스의 에러 응답 본문에 `trace_id`를 넣는다. gateway는 모든 응답에 헤더 `X-Trace-Id`를 붙이고 CORS로 브라우저에 노출한다. 문의 받은 값을 Kibana에서 바로 찾는다 |
| ALB와 잇기 | ALB가 붙이는 `X-Amzn-Trace-Id`를 gateway가 모든 로그 줄의 `http.request.id`로 남긴다. ALB 접근 로그의 `trace_id`도 같은 필드에 넣는다 |
| span 저장 | 앱이 OTLP로 EDOT Collector에 보내고, collector가 마스킹([4절](#4-금지-항목과-마스킹))과 APM 가공을 거쳐 ES traces·metrics data stream에 쓴다. 보존 30일. 전송은 collector를 세운 뒤 환경 변수로 켜고, 그 전에도 trace ID는 만들어지고 전파된다. sampling은 dev 100%, prod는 실측 후 정한다. span의 OTLP 지표·로그 전송은 끈다(Prometheus·Filebeat가 수집) |

## 8. DB 로그

### PostgreSQL (`aws.rds`)

로그 접두어 `%m:%r:%u@%d:[%p]:%l:%e:%s:%v:%x:%c:%q%a:`를 이렇게 나눈다. 파라미터 그룹을 바꾸기 전의 기본 접두어
(`%t:%r:%u@%d:[%p]:`)도 함께 받는다.

| 접두어 | 필드 | 예 |
| --- | --- | --- |
| `%m` | `@timestamp` | |
| `%r` | `source.ip`, `source.port` | 클라이언트 주소. 백그라운드 프로세스는 비어 있다 |
| `%u` | `postgresql.log.user` | `content_svc` (DB 역할로 서비스를 구분한다) |
| `%d` | `postgresql.log.database` | `content` |
| `%p` | `process.pid` | |
| `%l` `%s` `%v` `%x` | `postgresql.log.session_line_num`, `session_start_time`, `virtual_transaction_id`, `transaction_id` | |
| `%e` | `error.code` | SQLSTATE. `00000`(성공)이면 두지 않는다 |
| `%c` | `postgresql.log.session_id` | |
| `%a` | `postgresql.log.application_name` | `%q` 뒤라 백그라운드 프로세스 줄에는 없다 |
| 심각도 | `log.level` | [3절](#3-loglevel). `DETAIL`·`HINT`·`STATEMENT` 같은 보조 줄은 `info` |
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

slowquery 로그의 소요 시간(`event.duration`), 응답 코드, `exceptionClass`(`error.type`), 실행 통계(`aws.neptune.*`)와
**값을 지운 쿼리**(`message`)를 남긴다. 쿼리 원문은 저장하지 않는다.

- 쿼리를 보내는 곳은 graph-rag 하나이고, 모든 값을 작은따옴표 리터럴(`'...'`)로 쿼리에 넣는다(Gremlin, 바인딩 없음).
  리터럴에는 문서 제목·설명처럼 원고에서 나온 내용이 들어간다.
- 리터럴 가운데 **스키마 낱말 허용 목록**(라벨, 속성 키, 결과·오류 코드, consumer 이름, 폴더 코드), **UUID 식별자**
  (`file:<uuid>`, `inbox:<consumer>:<uuid>:<uuid>` 처럼 소문자 접두어와 이은 것 포함), sha256, ISO 시각만 남기고 나머지는
  `'[redacted]'`로 바꾼다. 쿼리 모양과 "어느 프로젝트·문서였나"는 남고 내용은 지워진다.
- 허용 목록에 없는 낱말은 지워지기만 하므로 내용이 새지 않는다. graph-rag에 새 라벨·키·코드를 더하면 gitops
  `logging/base/logstash/pipelines/aws-neptune.rb`의 목록에도 더한다.
- 값을 지운 뒤라 ES와 **S3 보관 모두**에 남긴다. Neptune이 만드는 `queryFingerprint`(값을 자리표시자로 바꾼 쿼리)도
  쿼리 모양별로 묶어 보는 용도로 함께 남긴다.

## 9. index template (`logs@custom`)

ES 내장 `logs` index template(`logs-*-*`)에 ES가 열어 둔 확장 지점 `logs@custom`을 더한다. 내용은
`loresentry-gitops/logging/base/elasticsearch/logs-custom.json`이다.

| 설정 | 값 | 이유 |
| --- | --- | --- |
| 레플리카 | 0 | ES가 1노드다. 레플리카가 있으면 클러스터가 yellow가 된다 |
| 보존 | 30일 (data stream lifecycle), `prefer_ilm: false` | 내장 template의 ILM 정책 `logs`는 삭제 단계가 없다 |
| 시각 | `@timestamp`는 date_nanos | 앱 줄과 CRI 시각은 나노초까지 있다. date(밀리초)로 색인하면 같은 밀리초에 찍힌 한 요청의 줄(service → client)이 정렬에서 섞인다 |
| 필드 수 | 2000개, 넘는 새 필드는 저장만 하고 색인하지 않음 | 예상하지 못한 필드가 쏟아져 매핑이 끝없이 커지는 것을 막는다 |
| keyword 명시 | `log.logger`, `log.layer`, `log.package`, `service.name`, `service.version`, `http.request.id`, `http.request.method`, `http.route`, `url.full`, `error.code`, `error.type`, `user.id`, `orchestrator.type`, `postgresql.log.*`, `loresentry.*` | 동적 매핑은 처음 들어온 문서가 타입을 정한다. 걸러 보고 집계할 필드의 타입을 ES 내부 규칙이 아니라 이 파일로 보장한다 |
| long 명시 | `http.response.status_code`, `event.duration`, `loresentry.kafka.partition`, `loresentry.kafka.offset` | 범위 검색과 정렬 |

- `logs@custom`은 ES 내장 OTel template(`logs-otel@template`)에도 합쳐진다. 그쪽에서 field alias인 필드는 여기에 지정할 수
  없다(ES가 template을 거부한다). 3·7절의 `log.level`, `trace.id`, `span.id`와 2절의 `service.environment`,
  `orchestrator.cluster.name`이 여기에 해당하고, 내장 `ecs@mappings`가 문자열을 keyword로 만든다.
- 로컬 ES 9.5.5에서 확인한 결과: 위 필드는 모두 keyword, long 필드는 long, data stream은 logsdb 모드, failure store 켜짐,
  보존 30일, 레플리카 0. dev에 배포한 뒤 `GET _data_stream/logs-*`로 다시 확인한다.
- 새 `loresentry.*` 문자열 필드는 dynamic template(`loresentry_strings_as_keyword`)이 keyword로 만든다. 숫자 필드는 위 long
  목록에 더한다.
- `loresentry.param.*`·`result.*`의 숫자는 처음 들어온 값으로 타입이 정해진다(정수 long, 소수 double). 같은 이름에 정수와 소수를
  섞지 않는다.

### traces·metrics (APM)

EDOT Collector가 쓰는 traces·metrics data stream에도 같은 이유로 `@custom` component template을 둔다. gitops APM 단계에서
첫 데이터보다 먼저 넣는다.

| 설정 | 값 | 이유 |
| --- | --- | --- |
| 레플리카 | 0 | 1노드 ES |
| 보존 | 30일 | 로그와 같은 기간. 호출 그림을 로그와 같은 기간 동안 본다 |

## 바꾸는 절차

1. 이 문서, gitops의 `logs-custom.json`, Logstash 설정을 함께 고친다. 문서는 docs PR, 나머지는 gitops PR이다.
2. dev에 먼저 반영해 확인한 뒤 prod에 반영한다.
3. 필드 타입 변경은 이미 만들어진 인덱스에는 적용되지 않고 **다음 rollover부터** 새 인덱스에 적용된다. 그 사이에는 같은
   필드가 인덱스마다 다른 타입이 되어 Kibana가 충돌로 표시한다. 타입은 처음부터 바르게 정하고, 바꿀 때는 새 이름으로 추가한다.
