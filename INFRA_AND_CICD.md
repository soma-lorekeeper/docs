# Lore Sentry EKS · GitOps · CI/CD 구축 기록

> 최신화: 2026-09-20  
> 대상 환경: AWS `ap-northeast-2` / EKS `lore-sentry-k8s`  
> 상태: **파이프라인 전 구간이 실제로 동작하는 것을 확인했다.** 서비스 5개가 `prod` namespace에서 Running이고, `https://api.loresentry.com`으로 공개 응답한다.

이전 판에는 "예정/확인 필요"로 남아 있던 항목이 많았다. 지금은 대부분 구축·검증이 끝났으므로, 이 문서는 **설계 의도**와 **실제 확인된 상태**를 함께 기록한다. 설계만 있고 아직 만들지 않은 것은 **[미구현]**으로 표시한다.

## 0. 한눈에 보는 현재 상태

| 항목 | 상태 |
|---|---|
| EKS `lore-sentry-k8s` | 동작 중 (노드 `r7i.large` 3대, `2a` 1 / `2b` 2) |
| Argo CD (App-of-Apps) | 동작 중, `argocd.loresentry.com` 공개, GitHub SSO 연동 |
| AWS Load Balancer Controller | 동작 중, ALB 1개를 IngressGroup `lore-sentry`로 공유 |
| GitOps 저장소 | `soma-lorekeeper/loresentry-gitops`, **Kustomize base + overlay** |
| 배포 대상 namespace | `prod` (dev는 디렉터리만 존재) |
| 서비스 | gateway · ai-chat · authentication · content · graph-rag (5개, 전부 Running) |
| 캐시 | `auth-valkey` (Valkey 9.0.6, 순수 캐시, 영속성 없음) |
| 관계형 DB | RDS PostgreSQL 18.6 `lore-sentry-postgres` (`db.t4g.micro`, 인스턴스 1개 / 논리 DB 3개) |
| 그래프 DB | Amazon Neptune 1.4.8.0 `lore-sentry-neptune` (`db.t4g.medium` 1노드) |
| 메시징 | Strimzi 1.2.0 / Kafka 4.3.1, 브로커 3대 KRaft, 잠정 토픽 2개 (토픽 설계 미확정) |
| CI/CD | 5개 저장소 전부 `main` push → ECR → Lambda → Argo CD 검증 완료 |
| 공개 진입점 | `https://api.loresentry.com` → gateway only. **전 구간 무인증** |
| 프론트엔드 | `https://loresentry.com` → CloudFront `<CF_FRONTEND_ID>` + S3 (클러스터 밖) |
| Cloudflare | **DNS 전용, 프록시 off.** 데이터 경로에 없으므로 WAF·캐싱·DDoS 완화는 없다 |
| 스토리지 | `gp3` 기본 StorageClass, `reclaimPolicy: Retain` |
| DB 연결 | 4개 서비스 전부 `/health/db` 200. gateway가 집계 (§1-A, §19-B) |
| 알려진 드리프트 | 클러스터의 `root` Application이 옛 저장소 URL 사용, Lambda 코드 미버전관리, `strimzi` Application `OutOfSync` (§19-A) |

## 1. 최종 목표

프로덕션 EKS 환경을 다음 흐름으로 구성한다.

```text
개발자 Git push
    ↓
GitHub Actions: 테스트 및 이미지 빌드
    ↓
Amazon ECR: 불변 이미지 저장
    ↓
AWS Lambda: GitOps 저장소의 이미지 태그 변경
    ↓
Argo CD: Git 변경 감지 및 EKS 동기화
    ↓
Deployment RollingUpdate
    ↓
AWS Load Balancer Controller가 ALB Target Group의 Pod IP 갱신
    ↓
Cloudflare DNS → ALB → Service → Pod
```

CI는 EKS를 직접 수정하지 않는다. **GitOps 저장소만 배포 상태를 변경하고, 실제 클러스터 변경은 Argo CD가 담당한다.**

## 1-A. 누가 무엇을 하는가 — 현재 구성의 책임 분담

이 절은 **지금 실제로 떠 있는 것**을 기준으로, 각 계층이 무엇을 하고 **무엇을 하지 않는지** 적는다. 책임이 겹치면 장애 시 어디를 봐야 할지 알 수 없게 되므로, 경계를 명시하는 것이 이 절의 목적이다.

### 요청 경로 한 줄 요약

```text
브라우저
  │  https://loresentry.com          정적 자산
  │  https://api.loresentry.com      API
  ▼
Cloudflare                DNS 전용. 프록시 off (CNAME → ALB)
  ▼
AWS ALB                   k8s-loresentry-<REDACTED> (internet-facing)
  │                       :443 HTTPS + ACM 인증서, :80 HTTP
  ▼
Target Group              k8s-prod-gatewaya-<REDACTED> (target-type: ip)
  ▼
gateway-api Pod           replicas 2, containerPort 8000
  │
  ├─ HTTP ─→ authentication-api ─→ RDS 논리 DB authentication
  ├─ HTTP ─→ content-api        ─→ RDS 논리 DB content
  ├─ HTTP ─→ ai-chat-api        ─→ RDS 논리 DB ai_chat
  └─ HTTP ─→ graph-rag-api      ─→ Neptune (8182)
```

ALB는 **하나뿐이고** `argocd.loresentry.com`과 공유한다. IngressGroup `lore-sentry` 덕분이다 (§6).

### 계층별 책임

| 계층 | 하는 일 | **하지 않는 일** |
|---|---|---|
| Cloudflare | **DNS 이름 해석만.** `api`·`argocd` → ALB, `loresentry.com`/`www` → CloudFront | TLS 종료, WAF, 라우팅, 캐싱, DDoS 완화 — **세 호스트 모두 프록시가 꺼져 있어 Cloudflare는 데이터 경로에 없다** |
| ALB | TLS 종료(ACM), HTTP→HTTPS, gateway Pod 간 부하 분산, health check(`/health`) | 마이크로서비스 호출, **API Composition**, 도메인 로직, 인증 |
| AWS LB Controller | Ingress/Service 감시 → ALB·Listener·Rule·Target Group 생성, EndpointSlice 변화를 Target Group에 반영 | 트래픽 처리 자체 (데이터 경로에 없다) |
| Ingress `lore-sentry-public` | `api.loresentry.com/` 전부를 `gateway-api:80`으로 | 서비스별 경로 분기 (의도적으로 gateway에 위임) |
| **gateway-api** | **단일 API 진입점, 내부 라우팅, API Composition, CORS, 업스트림 장애 격리** | 도메인 로직, DB 직접 접근 |
| 도메인 서비스 4종 | 자기 도메인 로직, **자기 DB만** 소유·접근 | 남의 DB 접근, CORS, 외부 노출 |
| RDS / Neptune | 영속화 | — (프라이빗 전용, 클러스터 밖에서 접근 불가) |
| Kafka (Strimzi) | 서비스 간 비동기 도메인 이벤트 | 요청 경로에 끼어들지 않는다. gateway의 대체재가 아니다 |
| auth-valkey | authentication 캐시 | 세션 저장소 (영속성 없음, §18) |
| Argo CD | **클러스터 desired state 적용의 유일한 주체** | 이미지 빌드, 태그 결정 |
| GitHub Actions | 테스트, 이미지 빌드, ECR push, Lambda 호출 | **`kubectl`로 클러스터 수정** |
| Lambda | GitOps 저장소의 이미지 태그 커밋 | 클러스터 접근 (VPC 연결조차 없다) |

### API Composition은 gateway가 한다 — 그리고 아직 하지 않고 있다

**책임 소재는 확정이다: API Composition은 `gateway-api`의 일이다.** ALB도, Kafka도, 도메인 서비스도 아니다. 이유는 셋이다.

1. **ALB에 둘 수 없다.** ALB는 L7이지만 응답 본문을 조합할 수 없다. host/path 분기까지가 한계다.
2. **도메인 서비스에 둘 수 없다.** content가 authentication을 호출해 응답을 합치기 시작하면 서비스 간 의존이 그래프처럼 얽히고, Database per Service의 경계가 호출 경계로 새어나간다.
3. **클라이언트에 둘 수 없다.** 화면 하나에 4개 서비스를 호출하게 하면 내부 MSA 토폴로지가 그대로 노출되고, 서비스를 쪼개거나 합칠 때마다 프론트엔드가 깨진다.

그래서 gateway가 **내부 토폴로지를 은닉하는 대가로 조합 책임을 진다.**

**다만 현재 gateway가 실제로 하는 조합은 없다.** 지금 있는 것은 업스트림 1:1 릴레이 5개와 헬스 집계 1개다.

| 엔드포인트 | 성격 |
|---|---|
| `GET /health` | gateway 자신만. **DB를 건드리지 않는다** |
| `GET /health/db` | **집계** — 4개 업스트림의 `/health/db`를 묶는다 |
| `GET /` | gateway 자신만 |
| `GET /graph` `GET /ai-chat` `GET /auth` `GET /content` | 업스트림 1:1 릴레이 |

`/health/db`가 **현재 유일하게 fan-out + 조합을 하는 엔드포인트**다. 4개를 순차 호출해 하나의 문서로 합치고, 전부 ok면 200, 하나라도 실패하면 503 + `status: degraded`를 준다. 구조적으로는 앞으로 만들 composition 엔드포인트의 축소판이다.

도메인 로직이 붙으면 이런 모양이 된다.

```text
GET /projects/{projectId}/workspace
        │
        ▼
   gateway-api
    ├──→ content-api        파일 트리, 문서
    ├──→ authentication-api 사용자 표시 이름
    └──→ graph-rag-api      관계 그래프
        │
        ▼
   하나의 응답으로 조합
```

**의도적으로 하지 않은 것:** catch-all `/**` 프록시. 그러면 gateway가 ALB가 이미 하는 일을 중복하는 리버스 프록시로 격하되고, composition·신원 전달·응답 재구성을 둘 자리가 사라진다. 경로는 하나씩 명시 선언한다.

### 인증은 아직 아무도 하지 않는다 — **[미구현]**

```text
현재:  브라우저 → ALB → gateway → 서비스        전 구간 무인증
목표:  브라우저 → ALB → gateway(JWT 검증) → 서비스(신원 헤더 수신)
```

확정된 것은 **검증 지점이 gateway라는 것**뿐이다. 도메인 서비스마다 토큰을 검증하면 인증 로직이 4곳으로 복제되고, ALB는 JWT를 이해하지 못한다. 남은 결정은 gateway가 자체 검증할지 authentication 서비스에 introspection을 요청할지다 (`LORE_SENTRY_PROJECT_CONTEXT.md` §19.2).

**지금은 `api.loresentry.com`의 모든 엔드포인트가 무인증으로 공개되어 있다.** `/health/db`는 DB 이름과 로그인 역할명을 그대로 노출한다 — 비밀번호는 없지만 내부 구조 정보이므로, 인증을 붙일 때 이 엔드포인트의 노출 범위도 같이 정해야 한다.

### 장애 격리 경계

| 무엇이 죽으면 | 어디까지 영향 |
|---|---|
| 도메인 서비스 1개 | 그 경로만 502. 다른 경로 정상 (실제 확인) |
| RDS | `/health/db` 503. **`/health`는 200이고 Pod는 살아 있다** (§19-B) |
| Neptune | graph-rag의 `/health/db`만 실패 |
| gateway 전체 | API 전면 중단. 그래서 replicas 2 + PDB `minAvailable: 1` |
| 노드 1대 | gateway는 AZ spread(`ScheduleAnyway`)로 분산되어 있어 생존 |
| `2b` AZ 전체 | 노드 3대 중 2대가 `2b`다. **Kafka가 `min.insync.replicas=2`를 못 채워 쓰기가 멈춘다** |
| Argo CD | 배포만 멈춘다. 실행 중 워크로드는 무영향 |
| Lambda / Actions | 배포 파이프라인만 멈춘다 |

### 현재 살아 있는 리소스 식별자

```text
EKS            lore-sentry-k8s (v1.36), 노드그룹 lore-sentry-pool
노드           r7i.large ×3 — 2a:1, 2b:2
VPC            vpc-<REDACTED> (10.20.0.0/16)
  public       subnet-<PUBLIC_2A> (2a), subnet-<PUBLIC_2B> (2b)
  private      subnet-<PRIVATE_2A> (2a), subnet-<PRIVATE_2B> (2b)
클러스터 SG    sg-<CLUSTER>   ← DB SG 규칙의 유일한 소스
ALB            k8s-loresentry-<REDACTED>, idle_timeout 60s, HTTP/2 on
Target Group   k8s-prod-gatewaya-<REDACTED> (ip, HTTP, HC /health)
RDS            lore-sentry-postgres (db.t4g.micro, PG 18.6)
Neptune        lore-sentry-neptune / lore-sentry-neptune-1 (db.t4g.medium)
Kafka          브로커 3대, 잠정 토픽 content.file.changed.v1 (6p/3r) + .dlq (3p/3r) — 토픽 설계 미확정
CloudFront     <CF_FRONTEND_ID> → loresentry.com, www.loresentry.com
Lambda         loresentry-update-gitops (python3.13, 256MB, 30s, VPC 없음)
ECR            gateway|ai-chat|authentication|content|graph-rag /api (IMMUTABLE)
```

`idle_timeout 60s`는 **AI 스트리밍의 제약**이다. gateway의 `spring.http.clients.read-timeout`이 10초이므로 둘 다 올려야 SSE/WebSocket 응답이 중간에 끊기지 않는다.

계정에 CloudFront 배포가 하나 더 있다 — `<CF_UNKNOWN_ID>` (`<CF_UNKNOWN_DOMAIN>`, alias 없음). 용도가 확인되지 않았으므로 쓰지 않는 것이면 정리 대상이다.

---

## 2. 인프라 아키텍처

### VPC와 서브넷

- VPC: `lore-sentry-vpc`
- VPC ID: `vpc-<REDACTED>`
- 리전: 서울 `ap-northeast-2`
- 구성: 퍼블릭 서브넷 2개 + 프라이빗 서브넷 2개, 2개 AZ
- 워커 노드와 Pod: 프라이빗 서브넷
- 인터넷 공개 ALB: 퍼블릭 서브넷
- NAT Gateway: 프라이빗 노드와 Pod의 인터넷 아웃바운드
- Internet Gateway: 공인 주소를 사용하는 리소스 및 NAT Gateway와 인터넷 사이 연결
- IGW는 VPC 단위로 하나를 공유할 수 있다.

```text
Internet
   │
   ▼
Internet Gateway
   │
   ├── Public subnet A ── ALB
   ├── Public subnet B ── ALB
   └── Public subnet의 NAT Gateway
                         │
                         ▼
              Private subnet A/B
              ├── EKS worker nodes
              └── Pods
```

퍼블릭 서브넷은 라우팅 테이블에 `0.0.0.0/0 → IGW`가 있다는 뜻이다. 해당 리소스에 공인 IPv4가 없다면 IGW만으로 인터넷 아웃바운드를 할 수 없다. 프라이빗 IP만 가진 노드는 NAT Gateway가 필요하다.

### EKS 네트워크 핵심

- EKS Control Plane은 AWS 관리 영역에 존재하며 워커 노드 목록에 나타나지 않는다.
- EKS가 고객 VPC에 만드는 ENI는 Control Plane과 고객 VPC 사이의 통신용 ENI다. Control Plane EC2 노드 자체는 아니다.
- VPC CNI가 Pod에 VPC 대역의 IP를 할당한다.
- 노드 커널이 실제 패킷을 처리하며 CNI가 필요한 인터페이스와 라우팅 구성을 준비한다.
- `kube-proxy`는 ClusterIP/NodePort Service 트래픽을 위해 노드 커널 규칙을 구성한다.
- CoreDNS는 Service 이름을 클러스터 DNS 이름으로 해석한다. CoreDNS는 일반적으로 Deployment로 동작한다.

## 3. 확인된 구축 상태

### 3.1 클러스터 기반

- EKS 클러스터 `lore-sentry-k8s`, 일반 Managed Node Group
- 노드 타입 `r7i.large` (2 vCPU / 약 16 GiB), **현재 1대**
- 노드가 프라이빗 서브넷에서 Ready
- 초기 `NodeCreationFailure`는 프라이빗 서브넷 NAT 경로 누락이 원인이었고 연결 후 해결
- VPC CNI 초기 장애는 Pod Identity/IAM 구성으로 해결
- EBS CSI Driver `ACTIVE`, Pod Identity association 생성
- ACM 인증서 Cloudflare DNS 검증 성공

### 3.2 GitOps / 배포

- Argo CD Root Application 적용
- Application 4개와 실제 sync wave: `platform`(-10) → `strimzi-kafka-operator`(-5) → `aws-load-balancer-controller`(**애노테이션 없음 = 0**) · `workload-prod`(0)
- **정정:** 이전 판은 `aws-load-balancer-controller`를 wave -20으로 적었으나, 이 Application에는 `sync-wave` 애노테이션이 아예 없다. 결과적으로 `workload-prod`와 같은 wave 0에 있다. 실무상 문제가 되지 않는 이유는 컨트롤러가 없는 Ingress는 그냥 처리되지 않고 대기하다가 컨트롤러가 뜨면 reconcile되기 때문이다. 순서를 우연이 아니라 보장으로 만들려면 음수 wave를 주면 된다.
- **GitOps 저장소는 Kustomize를 사용한다.** 이전 판의 "Directory 모드 + `recurse: true`" 방향은 **폐기**했다 (§7 참고)
- AWS Load Balancer Controller 동작, `IngressClass`/`IngressClassParams` 적용 확인
- ALB 1개 생성, IngressGroup `lore-sentry`로 API와 Argo CD 대시보드가 공유
- Lambda `loresentry-update-gitops`, Secrets Manager PAT, GitHub Actions workflow 5개 동작
- ECR 이미지 push → Lambda commit → Argo CD sync → RollingUpdate 전 구간 검증

### 3.3 검증된 배포 결과

```text
$ kubectl -n prod get pods
ai-chat-api-...                  1/1  Running
auth-valkey-...                  1/1  Running
authentication-api-...           1/1  Running
content-api-...                  1/1  Running
gateway-api-...                  1/1  Running   (2 replicas)
graph-rag-api-...                1/1  Running
lore-sentry-broker-0/1/2         1/1  Running   (노드당 1개)
lore-sentry-entity-operator-...  2/2  Running

$ kubectl -n prod get kafkatopic
NAME                          PARTITIONS  REPLICAS  READY
content.file.changed.v1       6           3         True
content.file.changed.v1.dlq   3           3         True

$ kubectl -n prod get pvc
data-0-lore-sentry-broker-0/1/2   Bound   10Gi   gp3

$ kubectl -n argocd get applications
platform                       Synced      Healthy
strimzi-kafka-operator         OutOfSync   Healthy   ← §17-A 참고
aws-load-balancer-controller   Synced      Healthy
workload-prod                  Synced      Healthy
```

```text
$ curl https://api.loresentry.com/content
{"service":"gateway-api",
 "thread":"VirtualThread[#51,tomcat-handler-15]/runnable@ForkJoinPool-1-worker-1",
 "upstream":{"content":{"service":"content-api"}}}
```

`thread` 값이 `VirtualThread[...]`인 것으로 gateway가 실제로 가상 스레드에서 요청을 처리하는 것을 확인했다.

### 3.4 노드 용량

노드 그룹은 `r7i.large` **3대**(노드당 allocatable 1930m, 합계 5790m), 2 AZ에 걸쳐 있다.

```text
ip-10-20-3-103  (2a)   1210m / 1930m   62%
ip-10-20-4-14   (2b)   1340m / 1930m   69%
ip-10-20-4-37   (2b)   1190m / 1930m   61%
```

여전히 **CPU request가 스케줄링의 병목**이고 메모리는 아니다. 애플리케이션 서비스는 `100m`, Kafka 브로커가 각 `500m`으로 가장 큰 소비자다.

request는 사용량 상한이 아니라 **스케줄링 예약**이다. 한 서비스의 request를 올리면 노드가 한가해 보이는데도 나중에 뜨는 Pod가 `Insufficient cpu`로 Pending이 될 수 있다.

**노드가 3대인 이유가 Kafka다.** 브로커 pool이 노드당 1개를 강제하므로 세 번째 브로커는 세 번째 노드 없이는 뜨지 못한다. (초기에는 1대였고, 그때는 여유 CPU가 470m뿐이어서 신규 서비스를 `100m`으로 잡았다.)

limits 합계는 노드별로 189~194%까지 오버커밋되어 있다. limits는 예약이 아니므로 스케줄링에는 영향이 없지만, 여러 Pod가 동시에 스파이크하면 서로 throttle된다.

### 3.5 두 개의 spread 제약이 같지 않다

같은 기능처럼 보이지만 결과가 정반대다. 이게 이번에 가장 배울 점이었다.

| | gateway-api | Kafka broker pool |
|---|---|---|
| topologyKey | `topology.kubernetes.io/zone` | `kubernetes.io/hostname` |
| whenUnsatisfiable | `ScheduleAnyway` | `DoNotSchedule` |
| 실제 결과 | 두 replica가 **같은 노드**에 떴다 | 노드당 정확히 1개 |

노드 3대 중 2대가 같은 zone(2b)이라 zone 기준 분산은 느슨하게 만족되고, `ScheduleAnyway`는 애초에 배치를 막지 않는다 — 권고일 뿐이다.

Kafka가 엄격한 이유는 **내구성 주장이 그 배치에 의존**하기 때문이다. 브로커 3개 중 2개가 한 노드에 있으면 노드 하나를 잃는 순간 `min.insync.replicas: 2`를 만족할 수 없고, `acks=all` 프로듀서가 멈춘다. 그래서 브로커가 기존 노드에 끼어 조용히 가정을 무너뜨리는 것보다 `Pending`으로 눈에 보이는 게 낫다.

게이트웨이도 진짜로 노드 하나를 견디게 하려면 zone이 아니라 hostname topologyKey가 필요하다. 현재의 PDB(`minAvailable: 1`)도 두 replica가 한 노드에 있으면 노드 드레인에 대해 보호해 주지 않는다.

## 4. Kubernetes Service 복습

Service는 IP가 계속 바뀌는 Pod 집합 앞에 고정된 이름과 접근점을 제공한다.

```text
Ingress → Service → Ready Pod 집합
```

### 주요 타입

| 타입 | 의미 | 주요 사용처 |
|---|---|---|
| `ClusterIP` | 클러스터 내부에서만 접근하는 기본 Service | ALB Ingress 뒤의 API, 내부 서비스 |
| `NodePort` | 모든 노드의 고정 포트를 개방 | `target-type: instance`, 특수한 외부 연결 |
| `LoadBalancer` | 클라우드 Load Balancer 생성을 요청 | 주로 NLB 기반 TCP/UDP 서비스 |
| `ExternalName` | 외부 DNS 이름에 대한 DNS 별칭 | 외부 서비스 연결 |
| Headless (`clusterIP: None`) | ClusterIP 없이 Pod IP를 DNS로 반환 | StatefulSet, 분산 DB |

현재 HTTP/HTTPS 애플리케이션의 표준 선택:

```text
ALB Ingress + ClusterIP Service + target-type: ip
```

`target-type: ip`에서는 ALB Target Group에 Pod IP가 직접 등록된다. AWS Load Balancer Controller가 Kubernetes Service/EndpointSlice 변경을 감지하여 Target Group 등록 정보를 갱신한다.

## 5. ALB, Ingress, Controller의 역할

### Ingress

Ingress는 실제 Load Balancer가 아니라 HTTP/HTTPS 라우팅 규칙이다.

```text
api.loresentry.com 요청
→ prod Namespace의 gateway-api Service 80번 포트
```

게이트웨이만 Ingress 뒤에 있고, 나머지 서비스로의 분기는 ALB가 아니라 게이트웨이 코드가 한다.

### AWS Load Balancer Controller

EKS 내부에서 실행되는 Controller로 다음 작업을 수행한다.

- Kubernetes Ingress와 Service 감시
- AWS API를 호출해 ALB/NLB 생성
- Listener와 Listener Rule 생성
- Target Group 생성
- EndpointSlice를 참고하여 Pod IP 등록/해제
- 필요한 Security Group 규칙 관리

### ALB와 NLB

- ALB: L7 HTTP/HTTPS, host/path 기반 라우팅, TLS 종료 가능
- NLB: L4 TCP/UDP/TLS, 고성능 연결 전달
- 현재 Istio를 사용하지 않는 표준 웹/API 구성은 ALB를 사용한다.

## 6. ALB 한 개를 공유하는 방식

Cloudflare에 `*.loresentry.com → ALB DNS`를 설정해도 DNS 자체가 ALB 수를 결정하지는 않는다. Kubernetes Ingress 구성이 ALB 수를 결정한다.

최종 결정은 **IngressClassParams로 공통 ALB 설정을 한 번 선언하고, 각 Namespace의 Ingress가 같은 IngressClass를 사용하도록 하는 것**이다.

```text
하나의 alb IngressClass (group.name: lore-sentry)
                  ↓
             Public ALB 1개
             ├── api.loresentry.com    → prod/gateway-api
             └── argocd.loresentry.com → argocd/argocd-server
```

`group.name`이 같은 Ingress들은 ALB 하나로 합쳐진다. Ingress마다 ALB가 생기지 않는다. 반대로 `group.name`을 바꾸면 **새 ALB가 생기고 기존 것이 사라진다** — 재구성 중에 이 값을 고정해 둔 덕분에 ALB 주소가 유지됐다.

이 방식의 제약도 알아둘 필요가 있다. `alb.ingress.kubernetes.io/inbound-cidrs`는 로드밸런서 전체에 작용하므로 그룹의 한 멤버에만 적용할 수 없다. Argo CD만 IP 제한하려면 별도 ALB(별도 group)로 분리해야 한다.

### 공통 ALB 설정 위치 — **[변경됨]** 차트 values로 이동

이전 판은 `platform/networking/public-alb-class.yaml`을 권장했다. **실제로는 그렇게 하면 ALB가 삭제된다.**

`IngressClass`와 `IngressClassParams`(둘 다 이름 `alb`)는 aws-load-balancer-controller Helm 차트가 만드는 리소스다. `platform/`에서 다시 선언하면 두 Application이 같은 리소스를 두고 다투게 되고, sync wave 순서상 controller(-20)가 prune한 뒤 platform(-10)이 다시 만드는 구간에서 controller가 ALB를 deprovision한다.

그래서 현재는 `argocd-apps/aws-load-balancer-controller.yaml`의 `helm.valuesObject` 아래에 둔다.

```yaml
ingressClass: alb
ingressClassParams:
  create: true
  spec:
    group:
      name: lore-sentry
    scheme: internet-facing
    targetType: ip
    ipAddressType: ipv4
    certificateArn:
      - arn:aws:acm:ap-northeast-2:<AWS_ACCOUNT_ID>:certificate/43ac...
    sslRedirectPort: "443"
    tags:
      - key: Project
        value: lore-sentry
```

`ingressClassParams.spec`이 비어 있으면 차트가 `spec.parameters`를 아예 렌더링하지 않는다. `kubectl get ingressclass`에서 `PARAMETERS <none>`으로 보였던 것이 이 때문이었다.

또한 `IngressClassParams`는 admission webhook이 검증하지 않으므로, 잘못된 필드를 써도 조용히 무시된다. CRD의 OpenAPI 스키마와 직접 대조해야 한다.

`IngressClassParams`에서 관리하는 값:

- `group.name`: 모든 Ingress를 ALB 하나로 결합
- `scheme: internet-facing`
- `targetType: ip`
- `certificateArn`: ACM wildcard 인증서
- `sslRedirectPort: "443"`
- 퍼블릭 subnet 선택
- 공통 AWS resource tags
- `ipAddressType`

각 Workload Ingress에 남길 값:

- `host`
- `path`
- backend Service
- 앱별 health check path/protocol/success code
- 실제 Listener 생성을 위한 `listen-ports`

Kubernetes Namespace의 metadata/annotation은 하위 리소스로 자동 상속되지 않는다. 임의의 공통 metadata까지 주입하려면 Kustomize, Helm, Kyverno 등이 필요하지만 현재 규모에서는 ALB 설정은 `IngressClassParams`, 애플리케이션 고유 설정은 각 Ingress에 두는 방향을 선택했다.

## 7. GitOps 저장소 구조 — Kustomize base + overlay

저장소 이름은 **`soma-lorekeeper/loresentry-gitops`**다. (이전 이름 `soma-loresentry-gitops`는 GitHub 리다이렉트로 남아 있으나 원격 URL은 정리했다.)

```text
loresentry-gitops/
├── bootstrap/
│   └── root-application.yaml          # 손으로 한 번만 apply
│
├── argocd-apps/                       # 컴포넌트별 Application
│   ├── aws-load-balancer-controller.yaml   # wave 없음(=0), ALB 설정도 여기
│   ├── strimzi-kafka-operator.yaml         # wave -5, Kafka CRD 먼저 설치
│   ├── platform.yaml                       # wave -10
│   └── workload-prod.yaml                  # wave 0
│
├── platform/                          # 클러스터 공통 인프라
│   ├── kustomization.yaml
│   ├── argocd/
│   │   ├── kustomization.yaml
│   │   └── ingress.yaml               # argocd.loresentry.com
│   └── storage/
│       ├── kustomization.yaml
│       └── gp3-storage-class.yaml
│
└── workload/
    ├── base/                          # 환경 중립 manifest
    │   ├── kustomization.yaml         # 서비스 디렉터리 목록
    │   ├── gateway/                   # deployment, service, poddisruptionbudget
    │   ├── ai-chat/                   # deployment, service
    │   ├── authentication/
    │   ├── content/
    │   └── graph-rag/
    └── overlays/
        ├── prod/
        │   ├── kustomization.yaml     # namespace, 이미지 태그, replica 수
        │   ├── namespace.yaml
        │   └── ingress.yaml           # 단일 공개 진입점
        └── dev/
            └── .gitkeep               # 아직 Application 없음
```

### Directory 모드가 아니라 Kustomize 모드다 — **[변경됨]**

이전 판은 `directory.recurse: true`를 권장했다. 지금은 **쓰지 않는다.**

Argo CD는 가리키는 디렉터리에 `kustomization.yaml`이 있으면 자동으로 Kustomize로 빌드한다. `directory.recurse`는 평범한 디렉터리 모드에 속하는 옵션이어서 둘은 **상호 배타적**이다. 둘을 함께 두면 Argo CD가 `kustomization.yaml`을 일반 manifest로 파싱하려 하고, 다음과 같이 실패한다.

```text
failed to discover server resources for group version kustomize.config.k8s.io/v1beta1
```

그래서 `platform`과 `workload-prod` Application에는 `directory` 블록이 아예 없다.

### base와 overlay의 역할 분담

| | 두는 것 | 두지 않는 것 |
|---|---|---|
| `base/<service>/` | Deployment, Service, 컨테이너 포트, probe, 리소스 | namespace, 실제 이미지 태그 |
| `overlays/prod/` | `namespace: prod`, `images:` 태그, `replicas:`, Ingress | 서비스 정의 자체 |

base의 이미지 태그는 `:bootstrap` 플레이스홀더이고, overlay의 `images:` 변환기가 항상 덮어쓴다. namespace를 base에 넣지 않는 이유는 overlay가 그것을 소유해야 dev/prod가 같은 base를 공유할 수 있기 때문이다.

### 서비스 추가 절차

1. `workload/base/<service>/`에 `deployment.yaml`, `service.yaml`, 둘을 나열한 `kustomization.yaml` 생성
2. `workload/base/kustomization.yaml`의 `resources:`에 디렉터리 추가
3. `workload/overlays/prod/kustomization.yaml`의 `images:`에 항목 추가 — **없으면 Lambda가 `ImageEntryNotFound`로 실패한다**
4. ECR 저장소 `<service>/api` 생성, 애플리케이션 저장소에 workflow 추가 (`ECR_REPOSITORY: <service>/api`)

자동화가 쓰는 것은 `images:` 항목 한 개의 `newTag` 값 하나뿐이다. 저장소의 다른 어떤 것도 CI가 건드리지 않는다.

## 8. AWS Load Balancer Controller 설치 설계

일반 EKS이므로 EKS Auto Mode용 `AmazonEKSLoadBalancingPolicy`를 사용하는 것이 아니라 AWS Load Balancer Controller 전용 IAM 정책과 Pod Identity를 사용한다.

예정 구성:

- Namespace: `kube-system`
- ServiceAccount: `aws-load-balancer-controller`
- Pod Identity IAM Role: `lore-sentry-load-balancer-controller-role`
- Helm chart: AWS EKS Chart 저장소
- 대화 당시 검토 버전: `3.5.0`
- replica: 2
- cluster: `lore-sentry-k8s`
- region: `ap-northeast-2`
- VPC: `vpc-<REDACTED>`

서브넷 태그:

```text
Public subnets:  kubernetes.io/role/elb=1
Private subnets: kubernetes.io/role/internal-elb=1
```

Argo CD가 Controller의 webhook TLS Secret/CA bundle과 충돌하지 않도록 Controller Application에 `ignoreDifferences` 설정을 둔다.

## 9. DNS와 TLS

- 도메인 관리: Cloudflare
- ACM 인증서: DNS 검증 성공
- DNS: `*.loresentry.com`을 ALB DNS로 연결하는 방향
- 최초 검증 시 Cloudflare Proxy는 `DNS only` 권장
- Proxy를 켜면 Cloudflare SSL/TLS 모드는 `Full (strict)` 권장
- `*.loresentry.com`은 루트 도메인 `loresentry.com`을 포함하지 않으므로 루트도 사용하면 별도 DNS/인증서 처리가 필요하다.
- ALB가 TLS를 종료하고, Ingress host/path 규칙으로 각 Service에 전달한다.

## 10. CI/CD 저장소 및 이미지 이름 규칙

애플리케이션 저장소 예:

```text
soma-lorekeeper/loresentry-graph-rag
```

**[변경됨]** 이전 판은 ECR 저장소를 GitOps 파일 경로(`workload/<ns>/<app>.yaml`)에 일대일로 대응시켰다. Kustomize로 옮기면서 **파일이 아니라 `images:` 항목 하나에 대응**하게 바뀌었다.

```text
ECR Repository : <service>/api
대상 파일       : workload/overlays/prod/kustomization.yaml   (항상 이 한 개)
대상 위치       : images[] 중 name 이 <registry>/<service>/api 인 항목의 newTag
```

현재 저장소와 서비스 대응:

| 애플리케이션 저장소 | ECR Repository | Kubernetes 리소스 이름 |
|---|---|---|
| `loresentry-gateway` | `gateway/api` | `gateway-api` |
| `loresentry-ai-chat` | `ai-chat/api` | `ai-chat-api` |
| `loresentry-authentication` | `authentication/api` | `authentication-api` |
| `loresentry-content` | `content/api` | `content-api` |
| `loresentry-graph-rag` | `graph-rag/api` | `graph-rag-api` |

모든 서비스가 같은 모양을 갖도록 규약을 고정했다.

| | 규약 |
|---|---|
| ECR 저장소 | `<service>/api`, 태그 immutable |
| base 디렉터리 | `workload/base/<service>/` |
| Deployment/Service 이름 | `<service>-api` |
| 컨테이너 포트 | `8000`, 이름 `http` |
| health check | `GET /health` → 2xx |
| Service 포트 | `80` → `targetPort: http` |
| 노출 | `ClusterIP`. Ingress 뒤에는 gateway만 |

## 11. CI 인증과 ECR 권한

GitHub Actions는 Access Key를 저장하지 않고 GitHub OIDC로 AWS 임시 자격 증명을 얻는다.

사용자가 선택한 범위:

```text
GitHub 조직 soma-lorekeeper의 모든 저장소가 공용 CI 역할 사용
```

신뢰 정책의 의미:

```text
repo:soma-lorekeeper@<GITHUB_ORG_ID>/*
```

**주의:** GitHub이 **immutable subject claim**을 쓰는 조직에서는 subject에 조직 ID와 저장소 ID가 들어간다.

```text
repo:<org>@<org-id>/<repo>@<repo-id>:ref:refs/heads/main
```

그래서 예전 형식인 `repo:soma-lorekeeper/*`만 신뢰 정책에 두면 `sts:AssumeRoleWithWebIdentity`가 `AccessDenied`로 실패한다. 진단은 workflow에 OIDC 토큰의 claim을 디코딩해 출력하는 임시 스텝을 넣어서 했다.

공용 역할 권한:

- 모든 ECR Repository에 push
- `loresentry-update-gitops` Lambda 호출

GitHub 조직 Actions 변수:

```text
AWS_CI_ROLE_ARN=arn:aws:iam::<AWS_ACCOUNT_ID>:role/<공용-CI-역할>
```

## 12. Lambda 기반 GitOps 업데이트

### Secrets Manager

GitHub fine-grained PAT를 저장한다.

```text
Secret name: loresentry/github/gitops-token
Resource owner: soma-lorekeeper
Repository access: All repositories
Repository permission: Contents read/write
```

GitOps `main`에 직접 commit할 수 있어야 한다. 보호 규칙이 이를 막으면 Lambda를 PR 생성 방식으로 바꿔야 한다.

### Lambda

```text
Function: loresentry-update-gitops
Runtime: Python 3.13        # 초기 설계는 3.14였으나 실제 배포는 3.13이다
Memory: 256 MB
Timeout: 30 seconds        # urllib 타임아웃이 15초이므로 기본 3초로는 부족하다
VPC 연결: 없음
IAM Role: loresentry-update-gitops-lambda-role
```

환경변수 — **실제 적용값**:

```text
GITHUB_OWNER=soma-lorekeeper
GITOPS_REPO=loresentry-gitops
GITOPS_BRANCH=main
GITOPS_ROOT=workload/overlays/prod
ECR_REGISTRY=<AWS_ACCOUNT_ID>.dkr.ecr.ap-northeast-2.amazonaws.com
GITHUB_TOKEN_SECRET=loresentry/github/gitops-token
```

이전 판의 `GITOPS_REPO=soma-loresentry-gitops`와 `GITOPS_ROOT=workload`는 둘 다 틀렸다. 전자는 GitHub Contents API에서 404를 냈고, 후자는 Kustomize 구조로 바뀌면서 유효하지 않다.

콘솔에서 함수를 만들면 자동 생성 역할(`loresentry-update-gitops-role-...`)이 붙는데, 그 역할에는 `secretsmanager:GetSecretValue` 권한이 없어 `AccessDenied`가 난다. 위의 전용 역할로 교체해야 한다.

Lambda 입력:

```json
{
  "repository": "graph-rag/api",
  "imageTag": "build-17-1"
}
```

Lambda 계산 결과:

```text
Manifest: workload/overlays/prod/kustomization.yaml
Image name: <AWS_ACCOUNT_ID>.dkr.ecr.ap-northeast-2.amazonaws.com/graph-rag/api
변경 대상: 위 name 을 가진 images[] 항목의 newTag → build-17-1
```

치환 로직은 정규식 한 방이 아니라 줄 단위 스캔이다. `- name: <이미지>` 줄을 찾고, 그 리스트 항목의 들여쓰기 범위 안에서 첫 `newTag:`를 찾는다. **정확히 한 개**를 찾지 못하면 `ImageEntryNotFound`로 실패하고 아무것도 쓰지 않는다. 같은 태그를 다시 적용하면 `unchanged`를 돌려주고 commit하지 않는다.

Lambda 동작:

1. `repository`와 `imageTag` 형식 검증
2. Secrets Manager에서 GitHub 토큰 조회
3. GitHub Contents API로 manifest 조회
4. 정확히 일치하는 ECR image 한 줄의 태그 변경
5. GitOps `main`에 commit
6. 동시 commit의 `409 Conflict`가 발생하면 최신 파일을 다시 읽고 재시도

임의 경로 접근을 막기 위해 `../`, 선행 `/`, 대문자 등은 거부한다. Manifest가 없거나 일치하는 이미지가 정확히 하나가 아니면 임의 생성/수정하지 않고 실패한다.

## 13. 이미지 버전 정책

이미지 태그 형식:

```text
build-<GITHUB_RUN_NUMBER>-<GITHUB_RUN_ATTEMPT>
```

예:

| 상황 | 이미지 태그 |
|---|---|
| 첫 Workflow 실행 | `build-1-1` |
| 두 번째 Workflow 실행 | `build-2-1` |
| 두 번째 실행 재시도 | `build-2-2` |
| 세 번째 Workflow 실행 | `build-3-1` |

- `GITHUB_RUN_NUMBER`: 해당 Workflow의 새 실행마다 증가
- `GITHUB_RUN_ATTEMPT`: 같은 실행을 재시도할 때 증가
- ECR tag를 Immutable로 설정해도 재실행 태그가 충돌하지 않는다.
- 이미지 OCI label에 `GITHUB_SHA`를 기록하여 실제 소스 commit도 추적한다.

## 14. GitHub Actions Workflow 책임

각 애플리케이션 저장소의 `.github/workflows/ci-cd.yaml`은 다음을 수행한다.

1. `main` push 감지
2. 코드 checkout
3. 런타임 준비와 의존성 설치 — Python 저장소는 `actions/setup-python@v6` + `pip install`, Java 저장소는 `actions/setup-java@v5` + `gradle/actions/setup-gradle@v5`
4. 테스트 실행 (`pytest` 또는 `./gradlew build`)
5. GitHub OIDC로 AWS 공용 역할 획득
6. ECR 로그인
7. `build-실행번호-시도번호` 태그 생성
8. `linux/amd64` 이미지 빌드 (`r7i` 노드가 x86_64)
9. ECR push
10. Lambda를 동기 방식으로 호출
11. Lambda의 GitOps commit 성공 여부 확인

저장소별 차이는 런타임 준비 스텝과 이 값뿐이다.

```yaml
env:
  ECR_REPOSITORY: graph-rag/api
```

Dockerfile에서 주의할 점 하나. Java 이미지는 `COPY --from=build /workspace/build/libs/*.jar app.jar`를 쓰는데, `./gradlew build`는 `*-plain.jar`까지 **두 개**를 만든다. 대상이 2개면 `COPY`는 실패하므로 빌드 스텝은 `clean bootJar -x test`여야 한다 (jar 1개).

Lambda 호출 payload:

```json
{
  "repository": "graph-rag/api",
  "imageTag": "build-17-1"
}
```

동일 저장소의 오래된 실행이 새 버전을 덮지 않도록 Workflow concurrency를 사용한다.

```yaml
concurrency:
  group: deploy-${{ github.repository }}
  cancel-in-progress: true
```

## 15. 체크리스트 — 완료분과 남은 것

### 완료

- [x] ACM ARN을 controller 차트 values의 `IngressClassParams`에 반영
- [x] `platform` / `workload-prod` Application을 Kustomize 모드로 정리 (`directory` 블록 제거)
- [x] AWS Load Balancer Controller 동작, ALB / Listener / Target Group 생성
- [x] IngressClass `alb` + IngressClassParams `PARAMETERS` 정상 표시
- [x] ECR 저장소 5개 생성 (immutable tag). **`graph-rag/api`만 `scanOnPush=false`다** — 나머지 4개는 켜져 있다. 의도한 차이가 아니면 맞춰야 한다
- [x] 공용 OIDC 역할에 ECR push + Lambda invoke 권한
- [x] 조직 변수 `AWS_CI_ROLE_ARN`
- [x] Secrets Manager GitHub PAT
- [x] Lambda 환경변수 교정 (`GITOPS_REPO`, `GITOPS_ROOT`, timeout, IAM role)
- [x] 5개 저장소에 Dockerfile / 테스트 / workflow 구성
- [x] 이미지 build/push → Lambda commit → Argo CD sync → RollingUpdate 전 구간 검증
- [x] 새 Pod IP가 ALB Target Group에 Healthy로 등록
- [x] gp3 StorageClass `reclaimPolicy: Retain`
- [x] Argo CD 대시보드 공개 + GitHub SSO
- [x] gateway CORS
- [x] 포트 규약을 **8000**으로 통일. 프론트엔드 `compose.local.yml`의 `NEXT_PUBLIC_API_BASE_URL`을 `http://localhost:8000`으로 수정했다
- [x] 노드 그룹 3대 확장 (`2a` 1 / `2b` 2). gateway의 `topologySpreadConstraints`와 PDB가 비로소 실제로 동작한다
- [x] 프론트엔드 배포 — S3 + CloudFront, `https://loresentry.com` (17절)
- [x] `auth-valkey` 캐시 (18절)
- [x] Kafka 브로커 3대 + 토픽 2개, Strimzi 운영 (19절)
- [x] RDS PostgreSQL 프로비저닝 + 논리 DB 3개와 서비스별 역할, 교차 접근 차단 확인 (19-B절)
- [x] Amazon Neptune 프로비저닝, 클러스터에서 도달 확인 (19-B절)
- [x] 4개 서비스에 DB 드라이버 연결 + `/health/db`, gateway 집계 엔드포인트. `https://api.loresentry.com/health/db`가 200으로 4개 연결 전부 보고

### 남은 것 — **[미구현]**

- [x] 스키마 마이그레이션 — Flyway. authentication·content는 Spring Boot 기동 시, ai-chat은 entrypoint에서 `flyway migrate` 후 uvicorn 시작. 테이블 정의와 반영 현황은 `TABLE_AND_LOGIC.md` §9
- [ ] JPA/ORM 매핑과 도메인 모델. 지금 붙어 있는 것은 연결 확인용 드라이버까지다
- [ ] `loresentry-lambda`를 git 저장소로 만들기. **파이프라인을 움직이는 코드가 버전 관리 밖에 있다** (§19-A)
- [ ] 클러스터 `root` Application의 저장소 URL 정정 — `kubectl apply -f bootstrap/root-application.yaml` (§19-A)
- [ ] `strimzi-kafka-operator` Application의 CRD `OutOfSync` 해소 (`ignoreDifferences`)
- [ ] `graph-rag/api` ECR의 `scanOnPush`를 나머지 4개와 맞추기
- [ ] 미사용 CloudFront 배포 `<CF_UNKNOWN_ID>` 용도 확인 후 정리
- [ ] Cloudflare 프록시/WAF 도입 여부 결정. 현재 DNS 전용이라 애플리케이션 앞단 보호 계층이 없다
- [ ] Gateway JWT 검증과 내부로의 신원 전달. 현재 모든 엔드포인트가 인증 없음
- [ ] AI 스트리밍 패스스루: `spring.http.clients.read-timeout`(10s)과 ALB `idle_timeout.timeout_seconds`(기본 60s) 상향
- [ ] 업스트림 retry / circuit breaking
- [ ] 관측성 (로그 수집, 메트릭, 트레이싱)
- [ ] content → graph-rag 이벤트 연동. 브로커·토픽·PostgreSQL이 모두 준비됐으므로 `outbox_events` 테이블은 content DB에 생성됐다. 남은 것은 outbox publisher와 graph-rag consumer다
- [ ] `auth-valkey`를 authentication 서비스에 실제 연결
- [ ] NetworkPolicy. vpc-cni의 `ENABLE_NETWORK_POLICY`가 꺼져 있어 **지금 NetworkPolicy를 써도 조용히 무시된다**
- [ ] 시크릿 관리. `auth-valkey` 비밀번호는 `kubectl`로 직접 만든 Secret이고 Git 밖에 있다. External Secrets Operator + 기존 Secrets Manager로 옮기는 것이 다음 단계
- [ ] 브로커 AZ 분산. 서브넷이 2개뿐이라 3 브로커가 `2b` 2 / `2a` 1로 나뉜다. `2b`가 통째로 죽으면 `min.insync.replicas=2`를 못 채워 쓰기가 멈춘다
- [ ] `dev` overlay와 `workload-dev` Application
- [x] 사용자 이미지 S3 — 버킷·IAM·Pod Identity·CloudFront `media.loresentry.com`·DNS 구축 확인 (17-A절, `IMAGE_UPLOAD_S3.md`). 클러스터 배선과 content 스토리지 서비스는 PR 대기

---

## 15-A. Argo CD 대시보드와 SSO

Argo CD는 `argocd.loresentry.com`에서 접근하며, `group.name: lore-sentry` 덕분에 API와 **같은 ALB**를 공유한다. Ingress는 클러스터 인프라이므로 `platform/argocd/`에 둔다.

```yaml
annotations:
  alb.ingress.kubernetes.io/backend-protocol: HTTPS
  alb.ingress.kubernetes.io/healthcheck-protocol: HTTPS
  alb.ingress.kubernetes.io/healthcheck-path: /healthz
backend:
  service:
    name: argocd-server
    port:
      number: 443
```

`backend-protocol: HTTPS`가 필요한 이유: Argo CD 서버가 스스로 TLS를 종료하고 평문 HTTP를 리다이렉트한다. ALB를 80번으로 붙이면 **리다이렉트 루프**가 된다. 대안인 `--insecure` 실행은 Argo CD ConfigMap 관리와 재시작을 수반하므로 annotation 방식을 택했다. ALB는 백엔드 인증서를 검증하지 않으므로 self-signed로 충분하다.

CLI는 gRPC를 쓰는데 ALB를 통과하려면 gRPC-Web이 필요하다.

```bash
argocd login argocd.loresentry.com --grpc-web
```

### GitHub SSO (Dex)

Argo CD는 OIDC만 이해하고 GitHub은 OAuth2만 제공하므로, 번들된 **Dex**가 그 사이를 잇는다.

```yaml
# argocd-cm 의 dex.config
connectors:
  - type: github
    id: github
    name: GitHub
    config:
      clientID: $dex.github.clientId
      clientSecret: $dex.github.clientSecret
      orgs:
        - name: soma-lorekeeper
```

`$` 접두사는 `argocd-secret`의 키를 참조한다는 뜻이다. 자격증명이 ConfigMap에 평문으로 남지 않는다.

`orgs` 필터가 **인증 단계에서** 조직 멤버만 통과시키므로 "인증 성공 = 조직 멤버"가 성립한다. 조직에 팀이 없으면 `groups` 클레임이 비어서 그룹 기반 RBAC를 쓸 수 없으므로, `argocd-rbac-cm`의 `policy.default`로 기본 역할을 준다.

```yaml
policy.default: role:admin
scopes: "[groups, email]"
```

조직에 사람이 추가되면 자동으로 포함된다. **이 구성은 Argo CD 로그인 페이지를 공개 인터넷에 두는 것이고, Argo CD admin은 클러스터의 무엇이든 바꿀 수 있다.** SSO가 확인되면 로컬 `admin` 계정을 비활성화하는 것이 다음 단계다.

---

## 15-B. 스토리지 — EBS 볼륨 보존

`gp3`가 기본 StorageClass이고 `reclaimPolicy: Retain`이다. PVC를 지워도 PersistentVolume과 그 아래 EBS 볼륨이 남는다. 실수로 `kubectl delete pvc`를 하거나 Argo CD가 prune해도 데이터가 살아남는다.

대가는 수동 정리다. PVC를 지우면 PV가 `Released`로 남고, 그 상태는 재사용도 안 되고 과금도 계속된다.

```bash
kubectl get pv                                  # STATUS Released 확인
kubectl delete pv <name>
aws ec2 delete-volume --volume-id <vol-...>     # EBS 볼륨 자체
```

`reclaimPolicy`는 StorageClass에서 **immutable**이므로 manifest에 다음이 필요하다.

```yaml
annotations:
  argocd.argoproj.io/sync-options: Replace=true,Force=true
```

이게 없으면 Argo CD가 해당 필드 변경을 적용할 수 없어 sync가 실패한다. 클래스를 교체해도 기존 볼륨은 영향받지 않는다 — PersistentVolume은 프로비저닝 시점에 자신의 reclaim policy를 기록하고 클래스를 다시 읽지 않는다.

---

## 15-C. CORS는 gateway 한 곳에서만

브라우저가 닿는 서비스는 gateway뿐이므로 CORS도 gateway에만 있다. 내부 서비스에는 없고, 필요도 없다.

```yaml
loresentry:
  cors:
    allowed-origin-patterns:
      - https://loresentry.com
      - https://*.loresentry.com
      - http://localhost:[*]
      - http://127.0.0.1:[*]
    allow-credentials: true
    max-age: 1h
```

`allowedOrigins`가 아니라 `allowedOriginPatterns`를 쓰는 이유: `allowCredentials: true`면 브라우저가 리터럴 `*`를 거부한다. 패턴을 쓰면 와일드카드를 유지하면서 요청마다 **구체적인 오리진 하나**를 그대로 돌려줄 수 있다.

패턴은 오리진 전체를 매칭하므로 유사 도메인은 403이다.

| Origin | 결과 |
|---|---|
| `https://loresentry.com` | 허용 |
| `https://app.loresentry.com` | 허용 |
| `http://localhost:3000` | 허용 |
| `https://loresentry.com.evil.com` | **403** |
| `http://loresentry.com` | **403** (평문 HTTP는 패턴에 없음) |

Spring이 `Vary: Origin`을 자동으로 붙인다. Cloudflare나 CDN이 API 응답을 캐시하기 시작하면 이게 없으면 한 오리진에 허용한 응답이 다른 오리진에 서빙될 수 있다.

`localhost`의 **모든 포트**를 허용한 것은 로컬 프론트엔드가 배포된 API를 호출하게 하려는 의도적 편의다. 동시에 `allowCredentials: true`와 짝지어져 있어 이 정책에서 가장 느슨한 부분이다. 지킬 것이 생기면 실제 프론트엔드 오리진으로 좁혀야 한다.

**예외 하나.** 사용자 이미지 업로드는 브라우저가 S3에 직접 `PUT` 하므로 미디어 버킷에 CORS가 따로 있다. `PUT`만, `loresentry.com`과 `localhost`만이다. 이유와 범위는 `IMAGE_UPLOAD_S3.md` §6에 있다. gateway가 유일한 CORS 경계라는 원칙은 **API**에 대해서만 유지된다.

## 16. 주요 검증 명령

아래 명령은 모두 `--context arn:aws:eks:ap-northeast-2:<AWS_ACCOUNT_ID>:cluster/lore-sentry-k8s`를 명시하는 것이 안전하다. 로컬 kubeconfig에 다른 클러스터가 섞여 있으면 `current-context`가 엉뚱한 클러스터를 가리킬 수 있다 — 실제로 한 번 겪었다.

```bash
# 클러스터와 시스템 구성
kubectl get nodes -o wide
kubectl get pods -A

# Argo CD
kubectl get applications -n argocd

# Load Balancer Controller
kubectl get deployment -n kube-system aws-load-balancer-controller
kubectl get pods -n kube-system \
  -l app.kubernetes.io/name=aws-load-balancer-controller

# Ingress 공통 클래스
kubectl get ingressclass
kubectl get ingressclassparams

# Workload
kubectl get deployment,service,ingress -n prod
kubectl get pods -n prod -o wide
kubectl rollout status deployment/graph-rag-api -n prod

# 실제 배포 이미지
kubectl get deployment graph-rag-api \
  -n prod \
  -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'

# Kustomize 렌더 결과를 클러스터에 붙이기 전에 확인
kubectl kustomize workload/overlays/prod

# overlay 가 base 에서 실제로 바꾸는 것만 보기
diff <(kubectl kustomize workload/base) <(kubectl kustomize workload/overlays/prod)

# Kafka
kubectl get kafka,kafkanodepool,kafkatopic -n prod
kubectl exec -n prod lore-sentry-broker-0 -- \
  bin/kafka-topics.sh --bootstrap-server lore-sentry-kafka-bootstrap.prod.svc:9092 \
  --describe --topic content.file.changed.v1

# auth-valkey (비밀번호는 Secret에서)
PW=$(kubectl get secret auth-valkey -n prod -o jsonpath='{.data.password}' | base64 -d)
kubectl exec -n prod deploy/auth-valkey -- \
  sh -c "valkey-cli -a '$PW' --no-auth-warning INFO stats"

# 프론트엔드 CDN
curl -sI https://loresentry.com/ | grep -i "x-cache\|cache-control"
aws cloudfront create-invalidation --distribution-id <CF_FRONTEND_ID> --paths "/*"

# 공개 진입점 전 구간
for p in health graph ai-chat auth content; do
  curl -s "https://api.loresentry.com/$p"; echo
done

# DB 연결 — 4개 서비스를 한 번에 (클라이언트가 볼 곳)
curl -s https://api.loresentry.com/health/db | jq

# DB 연결 — 서비스별로 직접
for d in authentication-api content-api ai-chat-api graph-rag-api; do
  echo "-- $d"
  kubectl exec -n prod deploy/$d -- \
    sh -c 'command -v curl >/dev/null && curl -s localhost:8000/health/db' 2>/dev/null \
    || kubectl run -n prod curl-$RANDOM --rm -i --restart=Never --image=curlimages/curl:8.11.1 \
         -- -s "http://${d}/health/db"
done

# RDS / Neptune 상태
aws rds describe-db-instances --profile lorekeeper \
  --db-instance-identifier lore-sentry-postgres \
  --query 'DBInstances[0].[DBInstanceStatus,Endpoint.Address,PubliclyAccessible]' --output text
aws neptune describe-db-clusters --profile lorekeeper \
  --db-cluster-identifier lore-sentry-neptune \
  --query 'DBClusters[0].[Status,Endpoint]' --output text

# 논리 DB와 소유자 (마스터 자격 증명은 Secrets Manager)
SECRET=$(aws rds describe-db-instances --profile lorekeeper \
  --db-instance-identifier lore-sentry-postgres \
  --query 'DBInstances[0].MasterUserSecret.SecretArn' --output text)
aws secretsmanager get-secret-value --profile lorekeeper --secret-id "$SECRET" \
  --query SecretString --output text
```

---

## 17. 프론트엔드 — S3 + CloudFront

프론트엔드는 **클러스터 밖에 있다.** Next.js를 `output: "export"`로 정적 파일로 만들어 CDN에서 제공하므로 컨테이너 이미지가 없고, 따라서 ECR → Lambda → Argo CD 경로를 타지 않는다. 10절의 `frontend/web` ECR 매핑과 6절의 `web.loresentry.com → frontend/web` ALB 라우팅은 폐기됐다.

```text
main push → GitHub Actions (pnpm check:cdn) → S3 sync → CloudFront invalidation
```

| 리소스 | 값 |
|---|---|
| S3 | `loresentry-web-prod-<AWS_ACCOUNT_ID>` (`ap-northeast-2`, 퍼블릭 접근 전면 차단) |
| CloudFront | `<CF_FRONTEND_ID>` / `dhi5kkvt5ncal.cloudfront.net` |
| OAC | `<CF_DIST_ID>` (SigV4) |
| CloudFront Function | `loresentry-web-rewrite` (viewer-request) |
| ACM | `loresentry.com` + `*.loresentry.com`, **`us-east-1`** |
| IAM | `loresentry-frontend-cdn-deploy` → `loresentry-ci-role` |

### 인증서가 두 벌인 이유

ALB용 인증서는 `ap-northeast-2`에 있지만 **CloudFront는 `us-east-1`의 인증서만 받는다.** 그래서 같은 도메인에 대해 리전이 다른 인증서 두 개를 유지한다.

발급은 DNS 작업 없이 끝났다. ACM은 **같은 계정·같은 도메인에 검증 토큰을 재사용**하므로, ALB 인증서 때문에 이미 Cloudflare에 있던 `_4648dd09….loresentry.com` CNAME이 그대로 쓰였다. **이 레코드는 두 인증서의 갱신에 모두 필요하므로 지우면 안 된다.**

### S3를 정적 웹사이트 호스팅으로 쓰지 않는 이유

버킷을 비공개로 두고 CloudFront가 OAC로 서명해 REST 엔드포인트에 접근한다. 대신 REST 오리진은 디렉터리 index를 해석하지 못한다. `trailingSlash: true`가 만드는 `out/login/index.html` 구조를 `loresentry-web-rewrite` 함수가 처리한다.

이 함수는 `/login` → `/login/` 리다이렉트가 아니라 URI를 곧바로 `/login/index.html`로 **rewrite**한다. `/workspace?projectId=…`처럼 쿼리스트링에 의존하는 라우트가 있어서, 리다이렉트를 한 번 태우면 쿼리가 유실될 위험이 있기 때문이다. `www` → apex 301에서도 쿼리스트링을 직접 재조립한다.

### 업로드가 3단계인 이유

1. `out/_next/`를 `max-age=31536000, immutable`로 **먼저**. 새 HTML이 참조할 청크가 미리 존재해야 한다.
2. 나머지를 `max-age=60`으로, `--delete`로 오래된 산출물 정리. `--delete`를 1단계에 걸면 현재 서비스 중인 HTML이 쓰는 청크가 사라진다.
3. `config.json`을 `no-store`로 따로. 이 파일만 교체하면 재빌드 없이 백엔드 주소를 바꿀 수 있고, CloudFront에서도 `CachingDisabled`로 분리해 뒀다.

### 한국어판과 영어판 — 2026-10-07

버킷에 언어판이 둘 있다. CI 가 `pnpm build:locales` 로 두 번 export 해 `/ko/`, `/en/` 아래에 같은 3단계로 올리고,
버킷 맨 위에는 한국어판을 남긴다(함수를 바꾸기 전 서빙 + `/404.html`). `loresentry-web-rewrite` 함수가 언어 없는
경로를 `ls_locale` 쿠키 → `CloudFront-Viewer-Country`(KR 이면 한국어) → `Accept-Language` → 영어 순으로 골라
`/<언어>/...` 로 rewrite 한다. 자세한 내용과 콘솔 작업(origin request policy 에 나라 헤더 넣기, 함수 publish 순서)은
[frontend/i18n.md](frontend/i18n.md), `loresentry-frontend/docs/deploy/README.md`.

## 17-A. 사용자 이미지 — 별도 S3 + CloudFront — **[구축 완료/확인]**

프론트엔드 버킷과는 **다른** 버킷이다. frontend CI의 `--delete` sync와 `/*` invalidation이 사용자 데이터에 닿으면 안 되기 때문이다.

```text
browser ──presigned PUT──> S3 loresentry-media-prod-<AWS_ACCOUNT_ID>
browser <──GET── CloudFront <CF_MEDIA_ID> (media.loresentry.com, OAC) <── 같은 버킷
content-api (SA content-api, Pod Identity → lore-sentry-content-role) ── presign · HeadObject
```

| 리소스 | 값 |
|---|---|
| S3 | `loresentry-media-prod-<AWS_ACCOUNT_ID>`, 퍼블릭 접근 전면 차단, `PUT` 전용 CORS |
| IAM | `lore-sentry-content-role` ← Pod Identity association `prod/content-api` |
| CloudFront | `<CF_MEDIA_ID>` / `<CF_MEDIA_DOMAIN>.cloudfront.net`, alias `media.loresentry.com`, `Deployed`, 17절과 같은 `us-east-1` 인증서 |
| Cloudflare | `media` CNAME, 프록시 끔 |
| GitOps | `workload/base/media/` ConfigMap, `workload/base/content/serviceaccount.yaml` (PR 대기) |

AWS 쪽은 `setup-media.sh`로 만들고 CLI로 확인했다. content에는 presign·검증·삭제를 하는 **스토리지 서비스 계층까지만** 있고, 공개 엔드포인트와 `image` 테이블은 도메인 개발 때 붙인다. 설계·제안 계약·구축 절차·검증은 [`IMAGE_UPLOAD_S3.md`](IMAGE_UPLOAD_S3.md)에 있다. 생성 스크립트는 `loresentry-content/docs/aws/setup-media.sh`.

Pod Identity association은 EKS API 객체라 Git에 둘 수 없다. 19-A의 목록에 들어간다.

---

## 18. auth-valkey — authentication 서비스 캐시

`workload/base/auth-valkey/`. Valkey 9.0.6, `prod` namespace, replica 1.

**순수 캐시로 정의했다.** 그래서 `save ""`와 `appendonly no`로 두 영속화 경로를 모두 끄고, 데이터 볼륨은 `emptyDir`다. 이 결정이 부수적으로 EBS를 피하게 해주는데, PVC를 쓰면 볼륨이 AZ에 묶여 pod가 그 AZ를 벗어나지 못한다.

`maxmemory-policy`는 `allkeys-lru`다. 모든 키가 언제든 다시 만들 수 있는 캐시이기 때문이다. **세션 저장소로 용도가 바뀌면 이 값과 `emptyDir`를 같이 바꿔야 한다** — `volatile-lru`로 TTL 있는 키만 축출하게 하고 실제 볼륨을 붙여야 한다. 지금 설정 그대로 세션을 넣으면 pod 재시작마다 전원 로그아웃되고, 메모리 압박 시 세션이 조용히 사라진다.

`maxmemory 768mb`는 컨테이너 limit 1Gi의 약 75%다. 나머지는 복사-후-쓰기와 단편화 오버헤드가 쓴다.

### 접속

```text
auth-valkey.prod.svc.cluster.local:6379
```

Valkey는 기본적으로 인증이 없으므로 `requirepass`를 걸었다. 비밀번호는 `prod` namespace의 Secret `auth-valkey`, key `password`에 있다.

**이 Secret은 Git에 없다.** `kubectl create secret`으로 직접 만들었고, Argo CD가 추적하지 않으므로 `prune: true`에도 지워지지 않는다. 클러스터에 아직 시크릿 관리 체계가 없어 택한 잠정 방식이고, External Secrets Operator + 기존 Secrets Manager로 옮기는 것이 다음 단계다.

replica를 늘려도 캐시가 커지지 않는다. 복제 설정이 없으므로 두 번째 replica는 **별개의 캐시**가 되어 키가 어느 pod에 있느냐에 따라 hit/miss가 갈린다. 그래서 `strategy: Recreate`로 두어 재시작 시 pod가 겹치지 않게 했다.

---

## 19. Kafka — Strimzi 기반

Strimzi 1.2.0 / Kafka 4.3.1, KRaft, 브로커 3대.

### 왜 Strimzi인가

Helm 차트로 브로커만 띄우는 것보다 operator가 무겁다. 그래도 고른 이유는 **`KafkaTopic` CRD로 토픽이 Git에 선언된다**는 점이다. "Git을 배포 상태의 Source of Truth로 쓴다"는 원칙이 토픽까지 확장되고, 롤링 재시작 때 in-sync 상태를 보며 한 대씩 내리는 일도 operator가 맡는다.

Application은 `argocd-apps/strimzi-kafka-operator.yaml`, sync-wave `-5`다. `workload-prod`(0)보다 앞서야 Kafka CRD가 Kafka 리소스보다 먼저 만들어진다. 그래도 첫 실행에서는 경합이 가능해서 Kafka 리소스마다 다음을 달았다.

```yaml
annotations:
  argocd.argoproj.io/sync-options: SkipDryRunOnMissingResource=true
```

operator는 `watchNamespaces: [prod]`로 범위를 좁혔고, `strimzi-system` namespace에 산다. Kafka CRD가 커서 `ServerSideApply=true`가 필수다 — 클라이언트 사이드 apply의 last-applied-configuration annotation에 담기지 않는 크기다.

### 구성

| 항목 | 값 |
|---|---|
| 클러스터 | `lore-sentry` |
| bootstrap | `lore-sentry-kafka-bootstrap.prod.svc:9092` (plain, 내부 전용) |
| 노드 풀 | `broker` 3대, roles `[controller, broker]` |
| 복제 | RF 3, `min.insync.replicas` 2 |
| 스토리지 | 브로커당 gp3 10Gi, `deleteClaim: false` |
| 배치 | `topologySpreadConstraints`로 노드당 1대 |

**combined roles**를 쓴 이유는 pod 수다. controller를 따로 빼면 3+3으로 6개가 되는데, 노드가 3대인 지금 구성에서 가용성 이득이 없다.

`auto.create.topics.enable: false`다. 토픽은 Git에만 존재해야 하며, 오타 난 토픽 이름이 조용히 실제 토픽을 만드는 일을 막는다.

### 토픽 — **[미확정]**

**어떤 토픽을 쓸지는 아직 정하지 않았다.** 아래 두 토픽은 Strimzi `KafkaTopic` CRD가 동작하는지 확인하려고 만든 **잠정 토픽**이고, 이름·개수·파티션 수·파티션 키 모두 도메인 이벤트 설계와 함께 다시 정한다 (`LORE_SENTRY_PROJECT_CONTEXT.md` §19.3).

| 토픽 (잠정) | 파티션 | 보존 |
|---|---|---|
| `content.file.changed.v1` | 6 | 7일 |
| `content.file.changed.v1.dlq` | 3 | 30일 |

파티션 키도 미확정이다. 후보로 `projectId`가 거론된 이유는, `fileId`로 잡으면 같은 프로젝트 안의 파일 변경들이 서로 다른 파티션에 흩어져 순서가 보장되지 않고, 프로젝트 단위 순서만 지키면 graph-rag의 참조 그래프가 수렴하기 때문이다. 이것은 근거이지 결정이 아니다.

DLQ 보존이 원본보다 긴 것은 의도적이다. 처리 실패한 이벤트는 사람이 찾아봤을 때 아직 남아 있어야 의미가 있다.

### 검증한 것

- 브로커 3대가 각각 다른 노드에 배치 (`2b` 2 / `2a` 1)
- 6개 파티션 전부 ISR 3/3
- `acks=all` produce → consume 왕복
- 같은 key(`glass-garden`)의 메시지 2건이 **동일 파티션**에 적재 — 프로젝트 단위 순서 보장 확인

### 알려진 상태: `strimzi-kafka-operator`가 OutOfSync로 보인다

CRD 10개 중 `kafkas.kafka.strimzi.io` 하나만 계속 `OutOfSync`다. **실제 드리프트가 아니다.**

- `kubectl diff -f 040-Crd-kafka.yaml` → 차이 0
- `description` 필드 수 chart 605 / live 605
- 나머지 9개 CRD는 전부 Synced

이 CRD만 JSON 805KB로, 다음으로 큰 것(345KB, Synced)의 두 배가 넘는다. Argo CD가 이 크기를 diff 단계에서 처리하지 못해 생기는 표시상의 문제이고, sync 자체는 `successfully synced (all tasks run)`으로 끝난다. Application health는 `Healthy`, Kafka는 `Ready`다.

`ignoreDifferences`로 덮을 수 있지만 **일부러 두었다.** 스키마를 무시하게 만들면 다음 Strimzi 업그레이드에서 진짜 CRD 변경까지 조용히 넘어가고, 하필 가장 중요한 CRD가 그 대상이 된다. 노란 상태 표시가 그 위험보다 낫다.


## 19-A. GitOps 밖에서 적용된 것 — 재현성 구멍

저장소만으로 클러스터를 재구성할 수 없는 항목이 현재 두 개 있다.

**`auth-valkey` Secret.** `kubectl create secret`으로 직접 만들었고 Argo CD가 추적하지 않는다 (§18 참고). 다음 단계는 External Secrets Operator + 기존 Secrets Manager다.

**데이터베이스 자격 증명.** PostgreSQL/Neptune 자체는 AWS 관리형이라 Kubernetes 매니페스트가 있을 수 없고, **접속 정보 중 비밀이 아닌 부분만** `workload/base/postgres/`·`workload/base/neptune/` ConfigMap으로 Git에 있다 (§19-B). 서비스별 비밀번호 Secret 3개(`authentication-db`, `content-db`, `ai-chat-db`)는 `auth-valkey`와 같은 이유로 Git 밖에 있다.

**`root` Application 자신.** App-of-Apps의 씨앗이라 자기 자신을 관리하지 않고 부트스트랩 시 손으로 apply한다. 그래서 저장소 이름을 `soma-loresentry-gitops` → `loresentry-gitops`로 바꾼 뒤에도 **클러스터의 `root`는 아직 옛 URL을 들고 있다.**

```text
클러스터의 root.spec.source.repoURL   .../soma-loresentry-gitops.git   ← 옛 이름
bootstrap/root-application.yaml       .../loresentry-gitops.git        ← 현재 이름
```

GitHub이 이름 변경 리다이렉트를 제공하므로 지금은 `Synced`로 동작하지만, **리다이렉트에 의존하는 상태다.** 옛 이름으로 새 저장소가 만들어지면 조용히 엉뚱한 곳을 보게 된다. `kubectl apply -f bootstrap/root-application.yaml`로 한 번 맞춰두는 것이 맞다.

**`loresentry-lambda` 디렉터리.** GitOps 단계 전체를 움직이는 Lambda 코드(`lambda_function.py`, `deploy.sh`, 테스트)가 **어떤 git 저장소에도 없다.** `.gitignore`와 README까지 있는데 `.git`이 없다. 이 코드가 사라지면 CI→GitOps 연결을 복원할 수 없다.

**`content-api`의 Pod Identity association.** `aws eks create-pod-identity-association`으로 만드는 EKS 객체라 매니페스트가 없다. ServiceAccount 자체는 Git에 있으므로 클러스터를 다시 만들면 SA는 돌아오지만 역할 연결은 `loresentry-content/docs/aws/setup-media.sh`를 다시 돌려야 한다 (17-A절).

**`strimzi-kafka-operator` Application이 `OutOfSync`다.** 대상은 `CustomResourceDefinition/kafkas.kafka.strimzi.io` 하나다. Helm 차트가 만든 CRD를 운영자가 다시 쓰는 전형적인 패턴이므로 `ignoreDifferences`가 필요할 수 있다. `Healthy`이므로 동작에는 지장이 없다.

관측된 `pg-bootstrap` Job Pod `Failed` 2개는 논리 DB 생성 과정의 잔여물이다. RDS 마스터 유저는 슈퍼유저가 아니라서 다른 역할을 소유자로 지정해 DB를 만들려면 그 역할의 멤버여야 하는데(`must be able to SET ROLE`), 첫 시도가 그 지점에서 멈췄다. `GRANT <role> TO CURRENT_USER`를 앞에 두고 멱등하게 다시 실행해 해결했다. 이 Job도 GitOps 밖에서 적용된 일회성 작업이다.

손으로 apply한 것은 Argo CD의 diff에 나타나지 않으므로 드리프트로도 보이지 않고, 클러스터를 다시 만들면 사라진다. 임시로 넣은 것이라도 Git에 들어오는 순간부터는 Argo CD가 지켜준다는 점이 GitOps의 요점이다.

```bash
# GitOps가 모르는 리소스 찾기: Argo CD tracking 애노테이션이 없는 workload
kubectl -n prod get all,secret,configmap -o json \
  | jq -r '.items[]
      | select(.metadata.annotations["argocd.argoproj.io/tracking-id"] == null)
      | "\(.kind)/\(.metadata.name)"'
```

---

## 19-B. 데이터베이스 — RDS PostgreSQL + Amazon Neptune

둘 다 AWS 관리형이고 **`lore-sentry-vpc`의 프라이빗 서브넷 전용**이다. 퍼블릭 액세스는 꺼져 있어 클러스터 밖에서는 붙을 수 없다.

### 프로비저닝된 것

| 리소스 | 식별자 | 사양 |
|---|---|---|
| RDS 인스턴스 | `lore-sentry-postgres` | PostgreSQL 18.6, `db.t4g.micro`, gp3 20GB, 암호화, Single-AZ |
| Neptune 클러스터 | `lore-sentry-neptune` | 엔진 1.4.8.0, 암호화, IAM 인증 비활성 |
| Neptune 인스턴스 | `lore-sentry-neptune-1` | `db.t4g.medium` (writer 1개) |
| DB 서브넷 그룹 | `lore-sentry-db-subnets` / `lore-sentry-neptune-subnets` | `private-a` + `private-b` |
| SG | `sg-<RDS>` (PostgreSQL) | inbound 5432 ← 클러스터 SG만 |
| SG | `sg-<NEPTUNE>` (Neptune) | inbound 8182 ← 클러스터 SG만 |

두 SG의 소스는 EKS 클러스터 SG `sg-<CLUSTER>` 하나다. 이 SG가 모든 워커 노드 ENI에 붙어 있고 VPC CNI가 Pod에 노드 ENI의 SG를 물려주므로, **SG 규칙 한 줄로 Pod 트래픽까지 덮인다.** CIDR을 열 필요가 없다.

양쪽 모두 `deletion-protection`과 백업 보관 7일을 켰다.

### 인스턴스 1개 + 논리 DB 3개

문서 초안은 서비스마다 RDS 인스턴스를 하나씩 두는 그림이었으나, **인스턴스 1개 안에 논리 DB 3개**로 시작했다. Database per Service의 요점은 *소유권 분리*이지 *물리 분리*가 아니고, `db.t4g.micro` 3대(월 약 $57)와 1대(약 $19)의 차이가 현 단계에서는 의미가 크다.

```text
lore-sentry-postgres
├── authentication  owner authentication_svc
├── content         owner content_svc
└── ai_chat         owner ai_chat_svc
```

소유권은 DB 소유자 + `CONNECT` 권한으로 강제한다.

```sql
REVOKE CONNECT ON DATABASE <db> FROM PUBLIC;
GRANT  CONNECT ON DATABASE <db> TO <svc_role>;
```

이 덕분에 **다른 서비스의 역할로는 접속 자체가 거부된다.** 클러스터 안에서 실제로 확인한 결과다.

```text
authentication_svc -> authentication : authentication_svc @ authentication
content_svc        -> content        : content_svc @ content
ai_chat_svc        -> ai_chat        : ai_chat_svc @ ai_chat

authentication_svc -> content : FATAL: permission denied for database "content"
                                DETAIL: User does not have CONNECT privilege.
content_svc        -> ai_chat : FATAL: permission denied for database "ai_chat"
```

나중에 특정 서비스만 트래픽이 커지면 그 DB만 `pg_dump`로 떼어내 별도 인스턴스로 옮기면 된다. 애플리케이션에서 바뀌는 값은 `DB_HOST` 하나다.

RDS 마스터 자격 증명은 `--manage-master-user-password`로 만들어 **Secrets Manager가 관리**한다 (`rds!db-b40d99b8-...`). 서비스 역할 비밀번호는 별개이며 마스터와 무관하다.

### 접속 정보 배선

비밀이 아닌 값은 Git의 ConfigMap에, 비밀번호는 Git 밖 Secret에 둔다.

```text
workload/base/postgres/configmap.yaml   host, port, <service>-database
workload/base/neptune/configmap.yaml    endpoint, reader-endpoint, port
```

각 Deployment가 주입받는 환경변수:

| 서비스 | 환경변수 |
|---|---|
| authentication | `DB_HOST` `DB_PORT` `DB_NAME` `DB_USERNAME` `DB_PASSWORD` |
| content | 동일 |
| ai-chat | 동일 |
| graph-rag | `NEPTUNE_ENDPOINT` `NEPTUNE_READER_ENDPOINT` `NEPTUNE_PORT` |

`DB_USERNAME`/`DB_PASSWORD`는 서비스별 Secret(`authentication-db`, `content-db`, `ai-chat-db`)의 `username`/`password` 키에서 온다. **이 Secret 3개는 Git에 없다** — `auth-valkey`와 같은 잠정 방식이고, External Secrets Operator로 옮기는 것이 다음 단계다 (§19-A).

### `/health/db` — 연결 확인 엔드포인트

서비스 4개가 각자 자기 저장소에 실제로 붙는지 보고하는 엔드포인트를 가진다. 성공은 200, 실패는 **503 + 드라이버 원본 에러**다.

| 서비스 | 구현 | 확인 내용 |
|---|---|---|
| authentication / content | Spring `JdbcClient` | `current_database()`, `current_user`, `version()` |
| ai-chat | `psycopg` | 동일 |
| graph-rag | `httpx` → Neptune `/status` | `role`, `dbEngineVersion`, Gremlin 버전 |

gateway는 이들을 묶어 한 번에 보여준다. **클라이언트가 볼 곳은 여기 하나다.**

```bash
curl -s https://api.loresentry.com/health/db | jq
```

```json
{
  "status": "ok",
  "service": "gateway-api",
  "upstreams": {
    "ai-chat":        { "status": "ok", "database": "ai_chat", "username": "ai_chat_svc" },
    "authentication": { "status": "ok", "database": "authentication", "username": "authentication_svc" },
    "content":        { "status": "ok", "database": "content", "username": "content_svc" },
    "graph-rag":      { "status": "ok", "role": "writer", "dbEngineVersion": "1.4.8.0.R1" }
  }
}
```

하나라도 실패하면 gateway는 `503`과 `status: degraded`를 주고, 어느 업스트림이 왜 실패했는지 그 업스트림의 에러 본문을 그대로 담는다. 업스트림의 503을 전송 실패로 취급하지 않기 때문에 원인 문구가 유실되지 않는다.

**Hikari는 `initialization-fail-timeout: -1`로 두었다.** 기본값이면 기동 시 커넥션 획득에 실패하는 순간 애플리케이션이 죽는데, 그러면 DB가 내려갔을 때 헬스 엔드포인트가 상태를 보고하기는커녕 Pod가 crash-loop에 빠진다. 이 값을 -1로 두어 **DB가 죽어도 컨테이너는 살아서 503으로 이유를 말한다.** 같은 이유로 `/health`(liveness/readiness 대상)는 DB를 건드리지 않는다 — DB 장애가 Pod 재시작으로 번지지 않게 하려는 의도적 분리다.

### 주의 — 이 계정은 Innovation Sandbox다

```text
arn:aws:sts::<AWS_ACCOUNT_ID>:assumed-role/AWSReservedSSO_myisb_IsbUsersPS_.../ISB-<N>
SCP: o-<REDACTED>/.../p-<REDACTED>   (ce:GetCostAndUsage 명시적 거부)
```

리스 만료나 예산 초과 시 **계정 내 리소스가 자동 정리된다.** 여기 쌓은 데이터는 영구 보존을 기대할 수 없다. Cost Explorer가 SCP로 막혀 있어 CLI로 소진액을 읽을 수 없으므로 잔여 예산은 ISB 포털에서 확인해야 한다.

월 비용 추정: RDS `db.t4g.micro` 약 $19 + Neptune `db.t4g.medium` 약 $85. Neptune에는 **30일 무료 평가판**(t3/t4g.medium 750시간, I/O 1000만, 스토리지 1GB)이 있어 첫 달은 실질 무료다. 상시 무료 티어는 없다.

## 20. 장애 원인별 확인 위치

| 증상 | 우선 확인할 것 |
|---|---|
| Node가 클러스터에 합류하지 못함 | 프라이빗 subnet의 NAT 경로, 노드 IAM, API endpoint 접근 |
| `aws-node`가 준비되지 않음 | VPC CNI Pod Identity/IAM, NAT 및 AWS API 접근 |
| GitHub Actions OIDC `AccessDenied` | IAM 역할 trust의 `repo:soma-lorekeeper@<org-id>/*`, `aud`, 조직 변수 ARN |
| Lambda `ImageEntryNotFound` | `overlays/prod/kustomization.yaml`의 `images:`에 해당 항목이 있는지 |
| Argo CD `failed to discover server resources for group version kustomize.config.k8s.io/v1beta1` | Application에 `directory.recurse`가 남아 있는지 (Kustomize 모드와 배타적) |
| Argo CD `namespaces "..." not found` (CreateNamespace=true인데도) | 진행 중인 operation이 spec 변경 이전에 시작된 경우. 재시도는 원래 operation을 재사용하므로 재시도 한도가 소진되고 새 auto-sync가 시작되면 해소된다 |
| Pod `Pending` / `Insufficient cpu` | 노드별 CPU request 총합(§3.4). Kafka 브로커라면 CPU가 아니라 **노드 수**가 원인일 수 있다 — hostname + `DoNotSchedule` 제약이라 브로커 수만큼 노드가 필요하다 |
| 브라우저 CORS 오류 | gateway의 `loresentry.cors.allowed-origin-patterns`. 내부 서비스에는 CORS가 없다 |
| Argo CD 리다이렉트 루프 | Ingress에 `backend-protocol: HTTPS`와 port 443이 설정됐는지 |
| ECR push `AccessDenied` | 공용 역할의 ECR upload/PutImage 권한 |
| Lambda invoke `AccessDenied` | 공용 역할의 `lambda:InvokeFunction` |
| Lambda GitHub `404` | GitOps repo 이름, `.yaml` 경로, PAT repository access |
| Lambda GitHub `403` | PAT 조직 승인, Contents write, branch protection |
| Lambda GitHub `409` | 동시 commit 충돌; 최신 SHA 재조회 및 재시도 |
| Pod `ImagePullBackOff` | ECR 이미지/태그 존재, 노드 역할의 `AmazonEC2ContainerRegistryPullOnly` |
| Kafka 토픽이 `READY=False` | entity-operator pod 상태, `kubectl describe kafkatopic` |
| 브로커가 `Pending` | 노드 CPU 여유, `topologySpreadConstraints`(노드당 1대라 노드 수보다 많이 못 뜬다) |
| producer가 `NOT_ENOUGH_REPLICAS` | ISR 수. `min.insync.replicas=2`라 브로커 2대 이상이 살아 있어야 쓰기가 된다 |
| Valkey pod가 `CrashLoopBackOff` | `prod` namespace의 Secret `auth-valkey` 존재 여부. 없으면 기동 자체가 안 된다 |
| Pod가 `CreateContainerConfigError` | `env`가 참조하는 ConfigMap/Secret 키가 실제로 있는지. DB 배선은 `postgres`/`neptune` ConfigMap과 `<service>-db` Secret을 **선행 조건**으로 요구한다 |
| `/health/db`가 `permission denied for database` | 그 서비스가 남의 DB에 붙으려 한 것이다. `DB_NAME`과 Secret의 `username` 조합을 확인한다 — 교차 접근은 의도적으로 막혀 있다 (§19-B) |
| `/health/db`가 `connection timed out` | PostgreSQL SG `sg-<RDS>` / Neptune SG `sg-<NEPTUNE>`의 inbound 소스가 클러스터 SG `sg-<CLUSTER>`인지. DB는 프라이빗 전용이라 클러스터 밖에서는 원래 안 된다 |
| `/health`는 200인데 `/health/db`가 503 | 정상 동작이다. liveness/readiness는 DB를 건드리지 않게 분리했으므로 DB 장애가 Pod 재시작으로 번지지 않는다 (§19-B) |
| RDS/Neptune이 사라졌다 | ISB 리스 만료 또는 예산 초과로 계정이 정리된 것일 수 있다. ISB 포털에서 리스 상태를 먼저 본다 (§19-B) |
| NetworkPolicy가 안 먹음 | vpc-cni `ENABLE_NETWORK_POLICY`. 꺼져 있으면 **에러 없이 무시된다** |
| `loresentry.com`이 옛 화면 | CloudFront invalidation 누락. HTML은 `max-age=60`이라 최대 1분 지연도 정상 |
| ALB 생성 실패 | Controller IAM, subnet tags, ACM ARN, IngressClassParams |
| Target이 Unhealthy | health check path/port, readiness, Service targetPort, Security Group |

## 21. 최종 운영 원칙

- 클러스터를 수동 수정하기보다 Git을 변경한다.
- CI는 이미지를 만들고 GitOps 버전을 갱신한다.
- Argo CD만 Kubernetes 배포 상태를 적용한다.
- ECR tag는 불변이고 재사용하지 않는다.
- 공통 ALB 정책은 aws-load-balancer-controller Application의 Helm values, 애플리케이션 라우팅은 `workload/overlays/<env>/ingress.yaml`에서 관리한다. 차트가 만드는 리소스를 다른 Application에서 다시 선언하지 않는다.
- 브라우저가 닿는 경계(CORS, 인증)는 gateway 한 곳에 둔다.
- Service는 안정적인 논리적 목적지이고, EndpointSlice는 현재 Ready Pod 목록이다.
- Load Balancer Controller가 EndpointSlice 변화를 AWS Target Group에 반영한다.
- 롤백은 `kubectl set image`보다 GitOps image 변경 commit을 revert하는 방식으로 수행한다.

