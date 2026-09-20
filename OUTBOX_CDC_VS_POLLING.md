# Outbox 발행 방식 — CDC vs 폴링

> 갱신일: 2026-09-20
> 목적: `outbox_events`의 행을 Kafka로 내보내는 두 방식(폴링 퍼블리셔 / CDC)을 비교하고, 현재 Lore Sentry 구성에 어느 쪽이 맞는지 정한다.
> 전제: [`GRAPH_INBOX_PATTERN.md`](GRAPH_INBOX_PATTERN.md) §4.1, [`TABLE_AND_LOGIC.md`](TABLE_AND_LOGIC.md) §5.3, [`LORE_SENTRY_PROJECT_CONTEXT.md`](LORE_SENTRY_PROJECT_CONTEXT.md) §4.4·§16.2, [`INFRA_AND_CICD.md`](INFRA_AND_CICD.md) §3.4·§19·§19-B

## 1. 무엇을 비교하는가

비교 대상은 **Outbox 패턴을 쓸지 말지가 아니다.** Content가 문서 변경과 `outbox_events` INSERT를 한 트랜잭션으로 커밋하는 것은 이미 확정이고, 그 뒤 **테이블의 행을 Kafka 토픽으로 옮기는 릴레이(relay)를 무엇으로 구현하는가**만 다르다.

```text
[공통]  Content 트랜잭션: files/relations/versions + outbox_events INSERT
                                   │
                    ┌──────────────┴──────────────┐
                    ▼                             ▼
         (A) 폴링 퍼블리셔                  (B) CDC (Debezium)
     published_at IS NULL 조회          PostgreSQL WAL → 논리 복제 슬롯
     → produce → published_at 갱신      → Kafka Connect → produce
                    └──────────────┬──────────────┘
                                   ▼
                     Kafka → graph-rag Consumer → Neptune
```

소비자 쪽(Inbox·revision 가드·Neptune 트랜잭션)은 두 방식에서 **완전히 동일하다.** 어느 쪽도 exactly-once를 주지 않고 at-least-once만 주기 때문이다.

## 2. 핵심 비교

| 기준 | (A) 폴링 퍼블리셔 | (B) CDC / Debezium |
|---|---|---|
| 동작 | 앱 워커가 주기적으로 `SELECT ... WHERE published_at IS NULL` | Connect 워커가 WAL을 논리 복제 슬롯으로 스트리밍 |
| 발행 지연 | 폴링 주기에 비례. 1초 주기면 평균 0.5초, 최대 1초 | 커밋 직후 수십 ms |
| DB 부하 | 변경이 없어도 쿼리가 계속 돈다. `UPDATE published_at`으로 쓰기·vacuum도 늘어난다 | 조회 쿼리 0. WAL을 읽을 뿐이라 테이블에 손대지 않는다 |
| 필요한 인프라 | 없음. Content 서비스 안의 스케줄러 스레드 | Kafka Connect 클러스터(Pod·JVM) + Debezium 플러그인 이미지 |
| 필요한 DB 권한·설정 | 일반 애플리케이션 권한 | `rds.logical_replication=1`, 파라미터 그룹 변경 후 **재부팅**, `REPLICATION` 권한 역할 |
| 순서 보장 | 조회 정렬로 직접 만든다. 잘못 만들면 유실 가능(§4) | 커밋 순서 그대로. 가장 강한 지점 |
| 전달 보장 | at-least-once (발행 후 `published_at` 갱신 전 크래시 → 중복) | at-least-once (오프셋 커밋 전 크래시 → 중복) |
| 실패 시 눈에 띄는 방식 | 워커가 죽으면 `published_at IS NULL` 행이 쌓인다. 쿼리 한 번으로 보인다 | Connector가 `FAILED`가 되고, **복제 슬롯이 WAL을 계속 붙잡아 디스크가 찬다** |
| 최악의 장애 | 이벤트 지연. DB는 멀쩡하다 | 슬롯 방치 → WAL 누적 → **DB 스토리지 full로 쓰기 정지** |
| 운영 복잡도 | 코드 100줄 수준. 디버깅이 SQL 한 줄 | Connect 워커 운영, 커넥터 설정, 스냅샷 모드, 슬롯 감시, 플러그인 이미지 빌드 |
| 이벤트 스키마 | 애플리케이션이 payload를 만든다. 완전히 통제 가능 | Outbox Event Router SMT로 payload를 꺼내야 한다. 기본은 DB 행 구조가 새어 나온다 |
| 멀티 인스턴스 | `FOR UPDATE SKIP LOCKED`가 필요 | Connect가 알아서 단일 태스크로 처리 |
| 비용 | 사실상 0 | Connect 워커 CPU/메모리 + 이미지 관리 + 슬롯 감시 |

한 줄로 줄이면 이렇다.

- **폴링은 싸고 단순한 대신 지연이 주기에 묶이고 DB를 계속 긁는다.**
- **CDC는 빠르고 DB를 긁지 않는 대신, 릴레이를 운영해야 할 인프라가 하나 더 생기고 실패 모드가 DB 쪽으로 번진다.**

## 3. 우리 서비스에 무엇이 맞는가 — 폴링

**결론: 폴링 퍼블리셔로 시작한다.** 근거는 네 가지다.

### 3.1 지연 요구가 느슨하다

이 흐름의 사용자 시나리오는 "속성 표에서 칩을 추가하고, 잠시 뒤 그래프 화면을 열면 간선이 보인다"다 (`GRAPH_INBOX_PATTERN.md` §2). 자동 저장 응답은 이미 Content 커밋에서 끝나고 Neptune 반영을 기다리지 않는다. 초 단위 지연이 화면에서 구분되지 않는데 CDC의 수십 ms를 살 이유가 없다.

### 3.2 중복·순서 역전을 이미 소비자가 흡수한다

폴링의 대표 약점인 중복 발행은 Inbox 1단계(`V('inbox:<eventId>')`)에서, 순서 문제는 2단계 revision 가드에서 이미 걸러진다. 게다가 이벤트가 델타가 아니라 **상태 전체 스냅샷**이라 마지막 revision으로 수렴한다. CDC가 주는 커밋 순서 보장은 **우리가 이미 돈 주고 산 방어를 한 번 더 사는 것**에 가깝다.

### 3.3 인프라 여유가 없다

| 제약 | 수치 | CDC에 미치는 영향 |
|---|---|---|
| 노드 CPU | `r7i.large` 3대, allocatable 5790m 중 약 3700m 사용 | Connect 워커(통상 500m~1000m request, JVM 1~2GB)가 한 노드의 여유를 대부분 먹는다 |
| RDS | `db.t4g.micro`, gp3 20GB, Single-AZ | 버스터블 2 vCPU에 복제 슬롯이 붙는다. **슬롯이 밀리면 20GB가 WAL로 찬다** |
| 파라미터 변경 | `rds.logical_replication=1`은 정적 파라미터 | 운영 DB **재부팅**이 필요하다 |

`INFRA_AND_CICD.md` §3.4가 기록한 대로 현재 스케줄링 병목은 CPU request다. 노드가 3대인 이유부터가 Kafka 브로커였다. 여기에 Connect 워커를 얹으면 **노드를 한 대 더 사거나, 다른 서비스가 `Pending`이 된다.**

### 3.4 아직 도메인 로직이 없다

`outbox_events` 테이블은 만들어졌지만 producer도 consumer도 없고, 토픽·파티션 키도 미확정이다 (§19.3). 이 단계에서 먼저 확인해야 할 것은 "커밋 순서가 ms 단위로 보존되는가"가 아니라 **"이벤트가 한 번은 확실히 가고, 두 번 와도 그래프가 안 깨지는가"**다. 폴링이 그 검증에 충분하고, 더 빨리 만들 수 있다.

## 4. 폴링을 제대로 구현하는 법

폴링이 위험해지는 경우는 거의 전부 **구현을 잘못해서**다. 아래는 그 함정과 대응이다.

### 4.1 워터마크로 조회하지 않는다

```sql
-- 위험: 늦게 커밋된 과거 시각 행을 영구히 건너뛴다
SELECT * FROM outbox_events WHERE created_at > :last_seen ORDER BY created_at;
```

`created_at`은 트랜잭션 시작 시점에 찍히지만 **가시성은 커밋 시점**에 생긴다. 먼저 시작해 늦게 커밋된 트랜잭션의 행은 커서가 이미 지나가 버린 뒤에 나타나고, 그대로 유실된다. CDC는 이 문제가 구조적으로 없지만, 폴링도 **플래그 방식이면 안전하다.**

```sql
-- 안전: 아직 안 보낸 행만 본다. 늦게 나타나도 다음 주기에 잡힌다
SELECT id, event_type, aggregate_id, payload
FROM outbox_events
WHERE published_at IS NULL
ORDER BY created_at, id
LIMIT 100
FOR UPDATE SKIP LOCKED;
```

### 4.2 필요한 것들

| 항목 | 내용 |
|---|---|
| 인덱스 | `CREATE INDEX ... ON outbox_events (created_at, id) WHERE published_at IS NULL` — 부분 인덱스라 발행된 행이 쌓여도 인덱스가 커지지 않는다 |
| 잠금 | `FOR UPDATE SKIP LOCKED`. Content Pod가 2개 이상으로 늘어도 같은 행을 두 번 보내지 않는다 |
| 주기 | 1초 시작. 빈 배치가 연속되면 백오프(최대 5초), 행이 꽉 차면 즉시 다음 배치 |
| 파티션 키 | `projectId`. 같은 프로젝트 이벤트가 한 파티션에 순서대로 들어가야 revision 가드 가정이 성립한다 (§4.2) |
| 프로듀서 설정 | `acks=all`, `enable.idempotence=true`. 브로커 `min.insync.replicas=2`와 짝이다 |
| 정리 | 발행된 행을 지우지 않으면 테이블이 무한정 커진다. `published_at < now() - 7 days` 행을 주기적으로 삭제 |
| 모니터링 | `published_at IS NULL`인 가장 오래된 행의 나이(초). 이 값 하나가 릴레이 헬스체크다 |

### 4.3 위치

Content 서비스 안의 스케줄러로 둔다. 별도 Deployment로 빼면 Pod가 하나 늘어나는데, 현재 CPU 예산에서 굳이 지불할 이유가 없다. 단, `@Scheduled` 하나로 충분하다는 뜻이지 **발행 로직을 도메인 트랜잭션 안에 넣으라는 뜻은 아니다.** 릴레이는 반드시 커밋 이후 별도 트랜잭션이다.

## 5. 언제 CDC로 옮기는가

다음 중 하나라도 실제로 관측되면 그때 바꾼다. 미리 바꾸지 않는다.

| 신호 | 의미 |
|---|---|
| 미발행 행의 최대 나이가 목표치를 상시 초과 | 폴링 주기를 줄여도 안 되면 릴레이 처리량 한계다 |
| `outbox_events`의 `UPDATE`/vacuum이 RDS CPU 크레딧을 갉아먹음 | 폴링의 쓰기 부하가 실제 비용이 됐다 |
| Outbox가 필요한 서비스가 3개 이상으로 늘어남 | 서비스마다 릴레이 코드를 복제하느니 Connect 하나로 모으는 편이 싸진다 |
| 이벤트 순서를 ms 단위로 보장해야 하는 요구가 생김 | revision 가드로 흡수되지 않는 도메인이 등장한 경우 |

### 마이그레이션 경로

지금 폴링을 고른다고 CDC가 막히지 않는다. **테이블 구조가 그대로 재사용된다.**

1. `outbox_events`는 그대로 둔다. Debezium Outbox Event Router SMT가 기대하는 컬럼 형태(`id`, `aggregate_id`, `event_type`, `payload`)와 이미 일치한다.
2. RDS 파라미터 그룹에 `rds.logical_replication=1`을 넣고 재부팅한다.
3. Strimzi의 `KafkaConnect` + `KafkaConnector` CRD로 선언한다. **토픽과 마찬가지로 커넥터도 Git에 선언되는 것**이 이 구성의 장점이다 (`INFRA_AND_CICD.md` §19).
4. 폴링 퍼블리셔를 끄고, `published_at` 갱신 대신 삭제 기반 정리로 바꾼다.
5. **소비자(graph-rag)는 바꾸지 않는다.** Inbox와 revision 가드가 그대로 유효하다.

전환 시 추가로 감시해야 할 것은 하나다. **복제 슬롯의 `confirmed_flush_lsn` 지연.** 커넥터가 멈춘 채 방치되면 WAL이 쌓여 20GB 스토리지를 채우고, 그 순간 Content DB 전체의 쓰기가 멈춘다. 폴링에는 없던 실패 모드다.

## 6. 요약

| 질문 | 답 |
|---|---|
| 지금 무엇을 쓰는가 | 폴링 퍼블리셔 |
| 왜 | 지연 요구가 느슨하고, 중복·순서를 Inbox가 이미 흡수하며, Connect 워커를 얹을 CPU 예산이 없다 |
| CDC가 더 나은 점 | 커밋 순서 보존, 수십 ms 지연, DB 조회 부하 0, 서비스가 많아질수록 유리 |
| CDC의 대가 | Connect 클러스터 운영, RDS 재부팅, 복제 슬롯이 DB 스토리지를 볼모로 잡는 실패 모드 |
| 나중에 바꿀 수 있는가 | 있다. 테이블과 소비자를 그대로 두고 릴레이만 교체한다 |
