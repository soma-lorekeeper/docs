# 경보 규약 — severity·Slack 전달·라벨·규칙 목록

> 최신화: 2026-11-04
> 대상: 클러스터 안 Prometheus 경보(kps 기본·Kafka·우리 규칙), Grafana가 Elasticsearch 로그와 로그 스택 모니터링 데이터로
> 평가하는 경보, 앱이 내보내는 지표
> 상태: **규약 확정, dev 구현 중.** prod는 prod 환경을 세팅할 때 같은 규약으로 붙인다.
> 관련: 구성과 흐름 그림은 [ALERTING_ARCHITECTURE.md](ALERTING_ARCHITECTURE.md), 로그 필드는 [LOG_SCHEMA.md](LOG_SCHEMA.md),
> 매니페스트는 `loresentry-gitops/alerting/`(규칙)과 `argocd-apps-dev/kube-prometheus-stack.yaml`(Alertmanager·Grafana 설정)

경보를 만들고 보내는 모든 곳이 따르는 규약이다. Prometheus 규칙, Grafana 로그 규칙, Alertmanager 설정, 앱의 지표 코드가 이 문서를
기준으로 한다.

- **출구는 하나다.** 모든 경보는 kube-prometheus-stack(kps)의 Alertmanager로 모이고, Alertmanager만 Slack에 보낸다. Grafana도
  직접 보내지 않고 Alertmanager로 넘긴다. 묶기·억제·silence를 한 곳에서 다루기 위해서다.
- **알림은 LLM에 의존하지 않는다.** 나중에 붙일 AI 진단은 Alertmanager가 따로 부르는 webhook이고, 진단이 늦거나 실패해도
  Slack 알림은 그대로 나간다.
- 이 문서를 바꿀 때는 gitops의 규칙 파일도 같은 시점에 고친다. 맨 아래 [바꾸는 절차](#바꾸는-절차)를 따른다.

## 1. 경로와 책임

```text
앱 /metrics · kube-state-metrics · node-exporter · kubelet · Kafka ─▶ Prometheus ─(PrometheusRule)─┐
앱·AWS 로그 ─▶ Filebeat ─▶ Logstash ─▶ Elasticsearch logs-* ◀─┐                                    │
ES·Kibana·Logstash·Filebeat 상태 ─▶ Metricbeat sidecar ─▶ Elasticsearch .monitoring-* ◀─┤            │
                                                       (ES 쿼리, 1분마다) Grafana 경보 규칙 ─────────┤
                                                                                                    ▼
                                                     kps Alertmanager: 묶기 · 억제 · silence · 경로
                                                                 ├─▶ Slack #alerts-dev (bot)
                                                                 └─▶ (예정) AI 진단 webhook
```

| 단계 | 하는 일 | 하지 않는 일 |
| --- | --- | --- |
| 앱 | [7절](#7-앱-지표-규약)대로 요청 지표를 `:9464/metrics`에 내보낸다 | 경보를 직접 보내지 않는다 |
| Prometheus | 지표 수집(30초), 규칙 평가(30초), firing 경보를 Alertmanager로 보낸다 | Slack에 보내지 않는다 |
| Metricbeat sidecar | ECK가 ES·Kibana·Logstash·Filebeat 파드마다 붙인다. 각 구성요소의 상태 API를 10초마다 읽어 ES `.monitoring-*`에 쓴다(Elastic Stack Monitoring) | 경보를 평가하지 않는다. 구성요소의 로그는 모으지 않는다(이미 Filebeat가 모은다) |
| Grafana | ES 로그 규칙과 로그 스택 규칙 평가(1분), firing 경보를 contact point로 Alertmanager에 넘긴다 | 묶기·반복·Slack 전송(Grafana 내장 Alertmanager는 쓰지 않는다) |
| Alertmanager | 묶기, 억제, silence, severity별 경로, Slack 전송과 메시지 수정 | 조회·평가 |
| Slack | 사람이 받는 곳. 경보 메시지는 나중에 진단 답글이 달리는 자리다 | — |

Kibana는 로그 탐색과 Stack Monitoring 화면에만 쓴다. Basic 라이선스의 Kibana 규칙은 Slack·webhook connector가 없어(Gold 이상)
경보에 쓰지 않는다. Stack Monitoring 화면을 처음 열 때 Kibana가 기본 규칙을 만들자고 묻는데 만들지 않는다. 같은 내용을 Grafana
규칙으로 둔다([6.5](#65-로그-스택-규칙-grafana-stack-monitoring)).

로그 스택은 세 겹으로 본다. 감시 데이터가 감시 대상(ES) 안에 있는 약점은 앞의 두 겹이 덮는다.

| 겹 | 무엇을 | 어디서 |
| --- | --- | --- |
| 파드·노드·디스크 | ES·Kibana·Logstash·Filebeat 파드의 Ready·재시작·메모리·OOM, PVC 사용률, 로깅 노드 자원 | Prometheus(kps 기본 규칙, [6.2](#62-인프라-규칙-loresentry-infra)) |
| 끝에서 끝 | 로그가 ES에 들어오는지, 색인이 거부되는지. ES에 못 닿으면 오류로 울린다 | Grafana `logs-*`(LogIngestionStopped·LogIndexFailures) |
| 안쪽 상태 | 클러스터 상태, heap, 거부, 디스크 watermark, Logstash 큐 적체, Filebeat 전송 실패 | Grafana `.monitoring-*`(Stack Monitoring) |

## 2. severity

규칙마다 `severity` 라벨을 반드시 단다. 값은 네 가지뿐이다.

| severity | 뜻 | 받는 곳 | 반복 |
| --- | --- | --- | --- |
| `critical` | 사용자 영향이 지금 있거나 곧 생긴다. 서비스 불능, 5xx 급증, 데이터 유실 위험 | Slack + `@channel` | 4시간 |
| `warning` | 확인이 필요하지만 즉시 장애는 아니다. 재시작, 지연 증가, 디스크 증가 | Slack | 12시간 |
| `info` | 참고용. 사람이 바로 볼 필요는 없다 | 보내지 않는다. Grafana 경보 화면에서만 본다 | — |
| `none` | `Watchdog`(경보 경로가 살아 있다는 신호)·`InfoInhibitor`(kps 내부용) | `null` | — |

- severity를 고르는 기준은 "지금 사람이 움직여야 하나"다. 원인이 아니라 영향으로 고른다.
- 같은 현상의 단계를 나눌 때는 같은 경보 이름에 severity만 다르게 둔다(예: AppErrorLogs 1건 이상 warning, 20건 이상 critical).
  두 단계가 함께 울리면 critical이 warning을 가린다([5절](#5-억제)).

## 3. Slack 전달

### 3.1 채널과 멘션

| 환경 | 채널 | critical | warning |
| --- | --- | --- | --- |
| dev | `#alerts-dev` 하나 | `@channel` | 멘션 없음 |
| prod | `#alerts-prod` (prod 세팅 때) | prod 때 다시 정한다 | — |

Slack app 하나(bot token, scope `chat:write`·`channels:history`)가 경보 메시지를 쓰고, 나중에 진단 답글도 단다. token은 Git 밖
Secret `monitoring/alertmanager-slack`(key `bot-token`)에 있다.

### 3.2 묶기와 반복

| 설정 | 값 | 이유 |
| --- | --- | --- |
| `group_by` | `alertname`, `namespace`, `service` | 서로 다른 문제가 한 메시지에 섞이지 않게 한다. 진단도 이 묶음 단위로 한다 |
| `group_wait` | 30초 | 첫 메시지 전에 같은 묶음의 경보를 모은다. 첫 알림이 이만큼 늦어진다 |
| `group_interval` | 5분 | 같은 묶음에 경보가 더 붙거나 해소될 때 메시지를 고치는 간격 |
| `repeat_interval` | critical 4시간, warning 12시간 | 계속 울리는 경보를 다시 알리는 간격 |

### 3.3 메시지는 처음 것을 고친다

Alertmanager `update_message: true`로, 같은 묶음의 후속 알림(반복, 새로 붙은 경보, 해소)은 **새 메시지를 쓰지 않고 처음
메시지를 고친다.** 채널이 같은 경보로 도배되지 않고, 진단 답글이 그 메시지의 스레드에 붙는다.

- 고칠 때는 `@channel`이 다시 울리지 않는다. 반복 알림도 소리 없이 메시지만 바뀐다.
- 묶음이 모두 해소된 뒤 다시 울리면 새 메시지로 나간다.
- Alertmanager가 재시작하면(저장소가 emptyDir) 어느 메시지를 고칠지 잊으므로, 그 뒤 첫 알림은 새 메시지로 나간다.
- **경보 메시지를 지우지 않는다.** 지우면 그 묶음이 해소될 때까지 고치기가 `message_not_found`로 실패해 알림이 멈춘다.

### 3.4 메시지 형식

```text
@channel [CRITICAL] AppDeploymentUnavailable · dev/content-api          ← message_text (critical만 @channel)
┃ content-api에 준비된 파드가 없어요.                                    ← 빨강(critical)·노랑(warning)·초록(해소)
┃ • 시작 2026-11-04 16:02 KST
┃ • deployment content-api · replicas 0/1
┃ Grafana 경보 · silence 만들기 · Kibana 로그
┃ fp: 3f2a9c1b7e0d4a55
```

해소되면 같은 메시지가 이렇게 바뀐다.

```text
[RESOLVED] AppDeploymentUnavailable · dev/content-api
┃ content-api에 준비된 파드가 없어요.                                    ← 초록
┃ • 시작 16:02 · 해소 16:11 KST (9분)
┃ …
```

| 부분 | 규칙 |
| --- | --- |
| 첫 줄 | `[CRITICAL]`·`[WARNING]`·`[RESOLVED]`, 경보 이름, `namespace/service`. critical이 울리는 중일 때만 앞에 `@channel` |
| 색 | 묶음 상태(`.Status`)와 severity로 정한다. firing critical 빨강, firing warning 노랑, resolved 초록 |
| 본문 | 경보마다 `summary`(우리 규칙은 한국어 한 문장), 시작 시각(KST), 핵심 라벨(pod·deployment·route 등 있는 것만) |
| 링크 | Grafana 경보 목록과 silence 만들기, 로그 경보는 Kibana. Alertmanager·Prometheus 주소는 클러스터 안에서만 열리므로 링크로 쓰지 않는다 |
| 끝줄 | `fp:` 뒤에 묶음 안 경보들의 fingerprint. 나중에 진단 서비스가 webhook으로 받은 fingerprint로 이 메시지를 찾아 답글을 단다 |

## 4. 라벨

우리 규칙(Prometheus·Grafana 모두)의 경보에는 아래 라벨이 반드시 있다. `alertname`은 자동으로 붙고, 나머지 셋은 규칙에서 단다.

| 라벨 | 값 | 비고 |
| --- | --- | --- |
| `alertname` | 규칙 이름(UpperCamelCase, 예: `AppHigh5xxRatio`) | **자동으로 붙는다.** Prometheus는 `alert:` 값, Grafana는 규칙 제목이다. 직접 달거나 바꾸지 않는다. 같은 현상의 단계는 같은 이름에 severity만 다르게 둔다 |
| `severity` | [2절](#2-severity) 네 값 중 하나 | |
| `namespace` | 경보가 가리키는 리소스의 namespace(`dev`·`monitoring` 등) | Grafana 로그 규칙은 고정값(dev overlay에서 `dev`)을 단다 |
| `service` | Kubernetes 이름(`gateway-api`·`content-api`·`ai-chat-summarizer` 등). 로그의 `service.name`과 같은 값 | 아래 주의 참고 |

- **`service` 라벨이 이미 다른 뜻으로 붙어 있다.** kube-state-metrics·kubelet·Argo CD 지표에는 수집기 Service 이름
  (`kps-kube-state-metrics` 등)이 `service`로 붙어 온다. 우리 규칙은 `label_replace`로 `deployment`나 `container` 값을 `service`에
  덮어써서 앱 이름이 되게 한다.
- Grafana 로그 규칙은 ES의 `service.name`별로 결과가 나뉘고, 라벨 이름이 `service.name`으로 온다. 규칙에서
  `service: '{{ index $labels "service.name" }}'`로 옮긴다.
- 로그 스택 규칙([6.5](#65-로그-스택-규칙-grafana-stack-monitoring))은 `namespace: monitoring`, `service`는 구성요소 이름
  (`elasticsearch`·`kibana`·`logstash`·`filebeat`)이다. 로그의 `service.name`과 같은 값이다. Filebeat처럼 여러 대인 것은
  `pod` 라벨로 어느 것인지 구분한다.
- kps 기본 규칙과 Kafka 규칙은 원래 라벨 그대로 둔다.
- 우리 규칙의 `summary`·`description`은 한국어로 쓴다. kps 기본 규칙의 영어 설명은 그대로 둔다.

## 5. 억제

| 원천 | 가리는 대상 | 같아야 하는 라벨 | 출처 |
| --- | --- | --- | --- |
| severity `critical` | `warning`·`info` | `namespace`, `alertname` | kps 기본 |
| severity `warning` | `info` | `namespace`, `alertname` | kps 기본 |
| `InfoInhibitor` | `info` | `namespace` | kps 기본 |
| **`AppDeploymentUnavailable`** | `warning` | `namespace`, `service` | 우리 규칙. 파드가 하나도 없을 때 따라 울리는 지연·오류율·지표 수집 실패 경보를 가린다 |

Helm values에서 `inhibit_rules`를 쓰면 kps 기본값이 통째로 바뀌므로, 위 기본 3개도 values에 그대로 옮겨 적는다(`null` 받는 곳,
`Watchdog` 경로도 같다).

## 6. 규칙 목록

평가 주기: Prometheus 30초, Grafana 1분. `for`는 조건이 이만큼 끊기지 않고 이어져야 firing이 된다는 뜻이다. 그동안은
pending이고 Alertmanager로 보내지 않으며, 중간에 한 번이라도 조건이 거짓이면 처음부터 다시 센다. firing이 되면 Alertmanager의
`group_wait`(30초)를 거쳐 Slack에 도착한다. 이미 일어난 사건(OOMKilled 등)은 `for` 0으로 바로 알린다.

### 6.1 kps 기본 규칙 (142개, 그대로 둔다)

kps가 넣는 노드·쿠버네티스·Prometheus·Alertmanager 규칙이다. 클러스터에 없는 구성요소(etcd·controller-manager·scheduler·
kube-proxy·Windows) 규칙은 이미 꺼져 있다.

| 알아 둘 것 | 처리 |
| --- | --- |
| `KubeCPUOvercommit`(warning)가 dev에서 계속 울린다. CPU 요청 합(약 6.0코어)이 노드 하나를 뺀 용량(약 5.8코어)을 넘어 노드 하나가 죽으면 파드를 다 못 옮긴다는 뜻이다 | 끄지 않는다. 사실이므로 warning으로 받는다. 메시지는 하나가 계속 고쳐진다 |
| `CPUThrottlingHigh`(info)가 JVM 파드가 뜰 때마다 잠깐 pending이 된다 | info라 보내지 않는다 |
| `KubePodCrashLooping`·`KubePodNotReady`·`KubeDeploymentReplicasMismatch`(warning, 15분) | 그대로 받는다. 앱이 완전히 내려간 경우는 2분 만에 [AppDeploymentUnavailable](#62-인프라-규칙-loresentry-infra)이 먼저 알린다 |
| `KubePersistentVolumeFillingUp`(warning·critical) | ES·Logstash·Kafka·Prometheus 디스크를 함께 지킨다 |
| `AlertmanagerFailedToSendAlerts` | Slack 전송이 실패하면 울린다. 이 경보도 Slack으로 못 갈 수 있으므로 Grafana 경보 화면에서도 본다 |

### 6.2 인프라 규칙 (`loresentry-infra`)

| 규칙 | 조건 | for | severity | 근거 |
| --- | --- | --- | --- | --- |
| AppDeploymentUnavailable | 앱 Deployment(gateway·content·authentication·ai-chat·ai-chat-summarizer·graph-rag)의 준비된 파드 0 | 2m | critical | 기본 규칙은 15분 뒤에야 울린다. 롤링 업데이트는 새 파드가 뜰 때까지 옛 파드를 남겨 오탐이 없다 |
| ContainerMemoryNearLimit | 컨테이너 메모리 사용량이 limit의 90% 초과 | 10m | warning | kps 기본에 없다. dev에서 Logstash OOMKilled가 있었다. 2026-11-04 부하 테스트 중 Kibana 97%·ES 89%였다. 부하 테스트가 끝난 뒤 평소 값을 보고, ES처럼 heap과 파일 캐시로 원래 높은 것은 빼거나 기준을 따로 둔다 |
| ContainerOOMKilled | 최근 10분 안에 재시작했고 마지막 종료 이유가 `OOMKilled` | 0 | warning | 재시작 반복(15분)보다 먼저, 원인과 함께 알린다 |
| ArgoCDAppUnhealthy | Argo CD Application이 Healthy·Progressing이 아님 | 15m | warning | 배포가 깨진 상태 |
| ArgoCDAppOutOfSync | OutOfSync가 이어짐. `strimzi-kafka-operator`는 원래 OutOfSync로 표시되어 뺀다 | 30m | warning | selfHeal이 켜져 있어 오래 OutOfSync면 sync가 실패하고 있다 |

### 6.3 Kafka 규칙 (`lore-sentry-kafka`, 이미 있음)

| 규칙 | 조건 | for | severity |
| --- | --- | --- | --- |
| KafkaUnderMinIsrPartitions | min ISR 미만 파티션 있음 | 1m | critical |
| KafkaOfflinePartitions | offline 파티션 있음 | 1m | critical |
| KafkaActiveControllerCount | 활성 컨트롤러가 1이 아님 | 2m | critical |
| KafkaBrokerScrapeDown | 브로커 지표 수집 실패 | 3m | critical |
| KafkaUnderReplicatedPartitions | 복제 부족 파티션 있음 | 5m | warning |
| KafkaBrokerDiskFilling | 브로커 디스크 여유 15% 미만 | 5m | warning |
| KafkaConsumerGroupLagHigh | consumer lag 1,000 초과 | 10m | warning |
| KafkaDeadLetterMessages | 최근 15분 DLQ(`*.dlq`) 메시지 증가 | 0 | warning |

매니페스트는 `workload/base/kafka/prometheusrule.yaml`이다. dev overlay 패치가 규칙 순서(배열 위치)로 namespace를 바꾸므로,
순서를 바꾸지 않는다.

### 6.4 로그 규칙 (Grafana, ES 쿼리)

모두 ES 문서 개수로 판단하고, 앱 규칙은 `service.name`별로 나뉜다. kube-system 잡음(csi-snapshotter error 하루 약 9천 건)은 앱
data stream만 보는 규칙이라 걸리지 않는다.

| 규칙 | 조건 | 창 | severity | 근거(dev 3일 실측) |
| --- | --- | --- | --- | --- |
| AppErrorLogs | `logs-loresentry.*`의 `log.level: error` 1건 이상 / 20건 이상 | 5m | warning / critical | 앱 error는 3일에 4건뿐이고 모두 실제 문제였다(upstream 시간 초과 등) |
| AppWarnLogSpike | 같은 data stream의 `log.level: warn` 50건 이상 | 5m | warning | 평소 5분당 최대 5건. warn에는 4xx 응답이 들어 있다 |
| DependencyUnavailable | `error.type: *_UNAVAILABLE` 3건 이상 | 5m | critical | 의존 서비스·저장소에 못 닿아 사용자가 503을 받는 상태. 평소 각 2건 이하 |
| AlbServerErrors | `logs-aws.alb-*`의 `log.level: error`(5xx) 5건 이상 / 50건 이상 | 15m | warning / critical | 3일 7건. ALB가 5분마다 파일을 써서 창을 길게 둔다 |
| RdsErrorLogs | `logs-aws.rds-*`의 `log.level: error` 1건 이상 | 15m | warning | 3일 0건. 데드락·직렬화 실패·연결 거부가 여기 남는다 |
| RdsSlowQueries | `logs-aws.rds-*`의 느린 쿼리(`warn` + `event.duration`) 1건 이상 | 15m | warning | 파라미터 그룹 기준 1초 이상. 1초 넘는 쿼리는 한 건이라도 본다 |
| NeptuneErrorLogs | `logs-aws.neptune-*`의 `log.level: error`(쿼리 실패) 1건 이상 | 15m | warning | |
| NeptuneSlowQueries | `logs-aws.neptune-*`의 느린 쿼리 1건 이상 | 15m | warning | slowquery 기준 1초. RDS와 같이 한 건이라도 본다 |
| LogIngestionStopped | `logs-*` 전체 0건 | 10m | critical | 로그 수집 경로(Filebeat·Logstash·ES)가 멈췄다. 평소 0이 될 수 없다 |
| AwsLogIngestionStopped | `logs-aws.*` 0건 | 30m | warning | aws-logs Filebeat나 AWS 쪽 전달이 멈췄다. EKS authenticator가 몇 분마다 쓴다 |
| LogIndexFailures | failure store(`logs-*::failures`) 1건 이상 | 15m | warning | 필드 타입 충돌 등으로 색인이 거부된 문서. 스키마가 깨진 신호 |
| UnknownAppLogLines | `logs-loresentry.*`의 `log.level: unknown` 10건 이상 | 15m | warning | 앱이 ECS가 아닌 줄을 낸다. Logstash가 이 값을 넣는다 |

규칙 공통 설정:

| 설정 | 값 | 이유 |
| --- | --- | --- |
| 평가 주기 | 1분 | |
| pending 기간(Prometheus의 `for`) | 0. 첫 평가에서 기준을 넘으면 바로 firing | 조건 자체가 5~30분 창으로 센 값이라 순간적인 흔들림은 이미 걸러진다 |
| 결과 없음(NoData) | OK. LogIngestionStopped·AwsLogIngestionStopped만 Alerting | 오류가 없는 평소에는 개수 쿼리 결과가 비어 있다 |
| 쿼리 오류(Error) | 직전 상태 유지. LogIngestionStopped만 Error | ES가 잠깐 끊겨도 로그·로그 스택 규칙이 한꺼번에 울리지 않게 하고, 끊김 자체는 한 규칙으로 알린다 |
| severity가 두 단계인 규칙 | 한 규칙에서 값에 따라 `severity` 라벨을 정한다 | Grafana는 같은 폴더에 같은 이름의 규칙을 둘 수 없다 |
| Alertmanager로 넘기기 | contact point(prometheus-alertmanager) + 루트 policy `group_by ['...']`, `group_wait 0s`, `group_interval 1m`, `repeat_interval 1m` | Grafana는 경보 끝 시각을 약 4분 뒤로 찍어 보내므로 1분마다 다시 보내야 Alertmanager가 해소로 오해하지 않는다. 묶기·반복은 Alertmanager가 한다 |

### 6.5 로그 스택 규칙 (Grafana, Stack Monitoring)

ECK가 ES·Kibana·Logstash·Filebeat 파드마다 붙인 Metricbeat sidecar가 10초마다 쓰는 `.monitoring-*`를 Grafana가 읽는다. 공통
설정은 [6.4](#64-로그-규칙-grafana-es-쿼리)의 표와 같다. 기준값은 켜기 전에 dev 실측(부하 테스트가 끝난 평소 값)으로 확정하고
근거 열에 적는다.

| 규칙 | 조건 | 창 | severity | 근거 |
| --- | --- | --- | --- | --- |
| EsClusterNotGreen | ES 클러스터 상태 yellow / red | 5m | warning / critical | dev는 1노드·레플리카 0이라 평소 green이다. red는 색인·검색이 안 되는 shard가 있다는 뜻 |
| EsJvmHeapHigh | ES 노드 heap 사용률 85% 초과 | 10m | warning | 오래 높으면 GC가 길어지고 색인·검색이 느려진다 |
| EsThreadPoolRejections | write·search thread pool 거부 수가 늘어남 | 10m | warning | ES가 감당하지 못해 요청을 버리고 있다. Logstash는 다시 보내므로 큐가 쌓인다 |
| EsDiskHigh | ES 노드 디스크 사용률 80% 초과 / 90% 초과 | 10m | warning / critical | ES 기본 watermark는 85%(새 shard 배치 중단)·90%·95%(index 읽기 전용)다. PVC 경보보다 ES 기준에 맞춘다 |
| LogstashQueueBacklog | pipeline별 persistent queue에 쌓인 이벤트가 계속 늘어남 | 15m | warning | 출력(ES·S3)이 막힌 신호. `to-s3`(2GB)·`to-es`(1GB) 큐가 차면 Logstash가 입력을 늦춘다 |
| FilebeatOutputFailures | Filebeat 전송 실패·버린 이벤트 수가 늘어남(파드별) | 10m | warning | Logstash 연결 문제나 거부. 로그가 늦거나 빠진다 |
| StackMonitoringDataMissing | 구성요소별 `.monitoring-*` 문서 0건 | 5m | warning | sidecar나 모니터링 수집이 멈췄다. 이 데이터가 없으면 위 규칙이 모두 눈을 감는다 |

결과가 없을 때(NoData)는 위 규칙 모두 OK, StackMonitoringDataMissing만 Alerting이다.

### 6.6 앱 지표 규칙 (`loresentry-app`)

[7절](#7-앱-지표-규약)의 지표로 계산한다. 대상은 HTTP API 5개(gateway·content·authentication·ai-chat·graph-rag)다.
ai-chat-summarizer는 HTTP가 없어 Kafka lag 규칙과 AppDeploymentUnavailable이 덮는다.

| 규칙 | 조건 | for | severity | 근거 |
| --- | --- | --- | --- | --- |
| AppMetricsDown | 지표 수집 실패(`up == 0`) | 3m | warning | 파드는 있는데 지표가 안 나오면 아래 규칙이 모두 눈을 감는다 |
| AppHigh5xxRatio | 5xx 비율 5% 초과이면서 5분 5xx 5건 이상 / 20% 초과이면서 20건 이상 | 5m | warning / critical | 트래픽이 적은 dev에서 1건이 비율을 튀게 하지 않도록 건수 하한을 둔다 |
| AppLatencyP95High | 경로별 p95 1초 초과. 초당 0.05건 이상일 때만 | 10m | warning | dev 4일 실측 일반 경로 p99 1.4초 이하 |
| AppLatencyP99High | 같은 조건에서 p99 2.5초 초과 | 10m | warning | |
| JvmHeapHigh | JVM heap 사용량이 최대의 90% 초과(Spring 3개) | 10m | warning | |
| DbPoolPending | HikariCP 연결을 기다리는 요청이 있음(content·authentication) | 5m | warning | 연결 풀이 모자라 요청이 줄 서 있다 |

지연 규칙에서 빼는 경로: LLM 답을 기다리는 채팅 메시지 경로(`/chat/sessions/{sessionId}/messages` 등, dev p95 8.8초)와 샘플 프로젝트
생성(`/projects/sample`, p99 2.0초). 정확한 목록은 규칙 파일에 둔다.

## 7. 앱 지표 규약

| 항목 | 규약 |
| --- | --- |
| 노출 | 전용 포트 **9464**의 `GET /metrics`(Prometheus 텍스트 형식). Kubernetes Service의 `metrics` 포트로만 열고 Ingress에는 걸지 않는다 |
| 수집 | ServiceMonitor 하나가 `app.kubernetes.io/part-of: lore-sentry`이면서 `metrics` 포트가 있는 Service를 모두 수집한다(30초). `service`·`namespace`·`pod` 라벨은 Prometheus Operator가 단다 |
| 요청 지표 | `http_server_requests_seconds` histogram(`_bucket`·`_count`·`_sum`) |
| 라벨 | `method`, `uri`(경로 템플릿. 매칭되는 경로가 없으면 고정값), `status`(숫자), `outcome`(`SUCCESS`·`CLIENT_ERROR`·`SERVER_ERROR` 등) |
| 버킷 | 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60초. Spring 설정은 `50ms,100ms,250ms,500ms,1s,2500ms,5s,10s,30s,60s`로 쓴다. 숫자만 쓰면 ms로 읽히고, `2.5s`는 기동이 실패한다 |
| 빼는 요청 | `/health`·`/health/db`, 그리고 `/metrics` 수집 요청 자체 |
| 로그 | 수집 요청은 로그를 남기지 않는다(30초마다 파드당 한 줄씩 쌓이지 않게) |
| Spring | `spring-boot-starter-actuator`와 `micrometer-registry-prometheus`. `management.server.port: 9464`, 경로 `/metrics`, 노출은 `prometheus`만. JVM·HikariCP 지표는 함께 나온다 |
| Python | `prometheus-client`. histogram은 모듈 수준에서 한 번 정의하고 ASGI 미들웨어로 기록하며, 9464 서버는 진입점에서 한 번만 띄운다 |

라벨에 원문 값(사용자 입력, ID가 들어간 실제 경로)을 넣지 않는다. 경로는 템플릿(`/projects/{projectId}`)만 쓴다.

## 8. 로그 스택 모니터링 규약 (ECK Stack Monitoring)

| 항목 | 규약 |
| --- | --- |
| 켜는 곳 | ECK CR 5개(Elasticsearch `logs`, Kibana, Logstash, Beat `filebeat`, Beat `filebeat-aws`)의 `spec.monitoring.metrics.elasticsearchRefs: [{name: logs}]`. ECK가 파드마다 Metricbeat sidecar를 붙이고 설정한다 |
| 켜지 않는 것 | `spec.monitoring.logs`. 구성요소 로그는 이미 Filebeat가 모아 `logs-*`에 넣는다 |
| 저장 | 같은 ES의 `.monitoring-*`. dev는 감시 대상과 같은 ES를 쓴다. ES 자신이 죽은 경우는 Prometheus 파드 규칙과 LogIngestionStopped가 맡는다. prod는 prod 세팅 때 별도 모니터링 ES를 검토한다 |
| 보존 | 7일. 기본 보존 기간은 구현 때 확인해 맞춘다 |
| sidecar 자원 | ECK 기본값을 그대로 쓴다(Beat 컨테이너 기본은 메모리 요청·limit 300Mi). 줄이지 않는다. 켠 뒤 파드 spec에 실제로 들어간 값을 이 표에 적는다. 노드 CPU 요청이 이미 65~83%(로깅 노드 83%)라 자리가 모자라면 sidecar를 줄이지 않고 노드를 늘린다 |
| Grafana 읽기 | 데이터 소스 `es-monitoring`(index `.monitoring-*`). ES 역할 `grafana_reader`가 `.monitoring-*`도 읽는다 |
| 화면 | Kibana → Stack Monitoring. 클러스터·노드 상태, 색인 속도, Logstash pipeline 흐름도, Beats 전송량 |

## 9. 운영

- **경보를 잠시 끄기**: Grafana(`https://grafana-dev.loresentry.com`) → Alerting → Silences에서 Alertmanager 데이터 소스를 골라
  silence를 만든다. Slack 메시지의 "silence 만들기" 링크가 같은 화면을 연다. Alertmanager가 재시작하면 silence가 사라진다(emptyDir).
- **경보 메시지는 지우지 않는다**([3.3](#33-메시지는-처음-것을-고친다)).
- **소음이 생기면** 규칙을 끄기 전에 조건(창·건수·`for`)을 먼저 고친다. 바꾼 이유는 이 문서의 근거 열에 적는다.
- **Grafana 화면에서 규칙을 고치지 않는다.** 규칙은 provisioning으로만 넣으므로 화면에서는 읽기 전용이고, Grafana DB가
  emptyDir이라 화면에서 바꾼 것은 재시작하면 사라진다.
- **Kibana Stack Monitoring의 기본 규칙을 만들지 않는다.** Slack으로 못 보내는 데다 Grafana 규칙과 겹친다.

## 10. 아직 없는 것

| 항목 | 상태 |
| --- | --- |
| APM 경보 | APM(EDOT Collector → ES `traces-apm*`)을 세팅한 뒤, 같은 방식으로 Grafana 규칙을 붙인다 |
| AI 진단 | Alertmanager webhook으로 진단 서비스를 부르고, 결과를 경보 메시지의 스레드 답글로 단다. 알림 경로와 별도로 붙인다 |
| `Watchdog` 외부 감시 | Alertmanager나 Prometheus 자체가 죽으면 알릴 곳이 없다. 외부 dead man's switch를 붙이기 전까지는 빈 곳이다 |
| prod | prod 환경을 세팅할 때 `#alerts-prod`, prod kps values, prod ELK 이후의 로그 규칙, 앱 지표 prod overlay를 붙인다 |

## 바꾸는 절차

1. 이 문서의 표(규칙·조건·severity·근거)를 먼저 고친다.
2. gitops의 규칙 파일을 같은 내용으로 고친다. Prometheus 규칙은 `alerting/base/prometheus/`, Grafana 규칙은
   `alerting/base/grafana/`, Slack·묶기·억제는 `argocd-apps-dev/kube-prometheus-stack.yaml`의 `alertmanager.config`, 로그 스택
   모니터링 sidecar는 `logging/base/`의 각 CR이다.
3. 확인한다. Prometheus 규칙은 `promtool test rules`, Alertmanager 설정은 `amtool check-config`·`amtool config routes test`,
   Grafana 규칙은 규칙 미리보기로 쿼리 결과를 본다.
4. docs PR과 gitops PR을 함께 올린다.
