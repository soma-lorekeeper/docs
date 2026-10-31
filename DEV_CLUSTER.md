# dev 환경 — 인프라 구성

> 최신화: 2026-10-31 (prod 대비 점검 반영)
> 대상 환경: AWS `ap-northeast-2` / EKS `lore-sentry-dev-k8s` / namespace `dev`
> 상태: 구축·검증 완료. **남은 작업은 Google 콘솔 설정 하나다 (§9).**
> 원본: 스크립트와 매니페스트는 `loresentry-gitops`의 `bootstrap/dev/`·`argocd-apps-dev/`·`platform-dev/`·
> `workload/overlays/dev/`·`loadtest/`에 있다. 이 문서는 구성과 그 이유를 적는다.

## 0. 한눈에 보기

dev는 **prod와 같은 방식으로 만든 두 번째 환경**이다. 부하 테스트와 가용성(장애 주입) 테스트를 prod에 영향 없이 하려고 만들었고,
기능 확인용 스테이징으로도 쓸 수 있다. 같은 AWS 계정에 있다.

| 주소 | 용도 |
|---|---|
| `https://dev.loresentry.com` | dev 프런트엔드 |
| `https://api-dev.loresentry.com` | dev API (gateway) |
| `https://media-dev.loresentry.com` | dev 사용자 이미지 |
| `https://argocd-dev.loresentry.com` | dev Argo CD (로컬 `admin`) |
| `https://grafana-dev.loresentry.com` | dev Grafana (Prometheus) |
| `https://kibana-dev.loresentry.com` | dev Kibana (로그 검색, 지금은 관리자 `elastic` 계정만) |

**사용자가 쓰는 서비스(프런트·백엔드·데이터)는 완전히 분리돼 있다.** dev 화면에서 로그인하고 글을 쓰고 AI 챗을 써도 prod
서버와 prod DB에는 닿지 않는다. **기반 시설 일부(네트워크, 일부 IAM 역할, 이미지 저장소, GitOps 저장소, 계정)는 prod와 공유한다**
(§3). 공유하는 것을 바꾸면 prod도 같이 바뀐다.

```text
prod:  loresentry.com      → api.loresentry.com      → EKS lore-sentry-k8s      → RDS·Neptune·S3 (prod)
dev:   dev.loresentry.com  → api-dev.loresentry.com  → EKS lore-sentry-dev-k8s  → RDS·Neptune·S3 (dev)
```

## 1. 전체 구성

```text
                         Cloudflare DNS (전부 DNS only)
          ┌──────────────────────────┴──────────────────────────┐
     prod │                                                  dev │
          ▼                                                      ▼
 CloudFront(프런트) · ALB(prod)                       CloudFront(dev 프런트) · ALB(dev)
          │                                                      │
 ┌────────┴──────────── 공유 VPC 10.20.0.0/16 ────────────────────┴────────┐
 │ public 서브넷 (공유, 두 ALB 가 같이 놓임)                                    │
 │                                                                           │
 │ private 10.20.3/4  (prod)              private 10.20.13/14  (dev)          │
 │  ├ EKS lore-sentry-k8s                  ├ EKS lore-sentry-dev-k8s           │
 │  ├ RDS lore-sentry-postgres            ├ RDS lore-sentry-dev-postgres      │
 │  └ Neptune lore-sentry-neptune         └ Neptune lore-sentry-dev-neptune   │
 │                                                                           │
 │ regional NAT (공유) → 인터넷                                                │
 └───────────────────────────────────────────────────────────────────────────┘
```

## 2. 분리된 것 — dev 전용

| 계층 | dev 전용 리소스 |
|---|---|
| 프런트엔드 | S3 `loresentry-web-dev-<AWS_ACCOUNT_ID>`, CloudFront(`dev.loresentry.com`), CloudFront Function `loresentry-web-dev-rewrite` |
| 진입점 | ALB 1개 (IngressGroup `lore-sentry-dev`) — `api-dev`·`argocd-dev`·`grafana-dev`·`kibana-dev`가 공유. 접근 로그는 S3 `loresentry-aws-logs-dev-<AWS_ACCOUNT_ID>/alb/` |
| 클러스터 | EKS `lore-sentry-dev-k8s`, 노드 그룹 `lore-sentry-dev-pool`·`lore-sentry-dev-logging-pool`(로깅 전용 `r7i.large` 1대, 2b, taint `dedicated=logging:NoSchedule`), 애드온, Pod Identity 연결, access entry |
| 워크로드 | gateway·authentication·content·ai-chat(+요약기)·graph-rag, auth-valkey, openai-mock |
| 관계형 DB | RDS `lore-sentry-dev-postgres` — 논리 DB 3개, 서비스 역할 3개, 마스터 비밀번호(Secrets Manager) 모두 별도. 로그용 파라미터 그룹 `lore-sentry-dev-postgres18` |
| 그래프 DB | Neptune `lore-sentry-dev-neptune`. 로그용 파라미터 그룹 `lore-sentry-dev-neptune14`(slowquery 1초) |
| 메시징 | Strimzi Kafka 브로커 3대와 토픽 10개 (클러스터 안) |
| 디스크 | EBS 볼륨: Kafka 10Gi ×3, valkey 4Gi, Prometheus 20Gi, Elasticsearch 20Gi, Logstash 5Gi, aws-logs Filebeat 1Gi |
| 파일 | S3 `loresentry-{media,refresh}-dev-<AWS_ACCOUNT_ID>`, 이미지 CloudFront(`media-dev.loresentry.com`). 로그 버킷 `loresentry-log-archive-dev-<AWS_ACCOUNT_ID>`(가공본 1년)·`loresentry-aws-logs-dev-<AWS_ACCOUNT_ID>`(ALB·CloudFront 원천 30일) |
| 네트워크 | private 서브넷 `10.20.13.0/24`(2a)·`10.20.14.0/24`(2b), 라우팅 테이블 `lore-sentry-dev-private` |
| 보안 그룹 | 클러스터 SG, DB SG 2개(`lore-sentry-dev-postgres-sg`, `lore-sentry-dev-neptune-sg`), ALB SG |
| IAM | 앱 역할 `lore-sentry-dev-{content,graph-rag,logstash,filebeat-aws}-role` — dev 버킷·로그 그룹에만 접근 |
| Git 밖 Secret | JWT 키 쌍, DB 서비스 비밀번호, valkey ACL 비밀번호, Grafana admin, Google OAuth (모두 prod와 다른 값) |
| 배포·관측 | Argo CD, kube-prometheus-stack, ELK(ECK operator, Elasticsearch·Kibana·Logstash·Filebeat, 매니페스트는 gitops `logging/`), Telepresence |
| AWS 관리형 로그 | CloudWatch 로그 그룹 3개(RDS `postgresql`, Neptune `slowquery`, EKS `authenticator`, 보존 30일), CloudFront 로그 전달 2개(us-east-1). 켜는 스크립트는 `bootstrap/dev/08-aws-logs.sh`, 로그 규약은 [LOG_SCHEMA.md](LOG_SCHEMA.md) |
| 배포 Lambda | `loresentry-update-gitops-dev` |

## 3. 공유하는 것 — prod와 함께 쓴다

dev를 만들 때 EIP 쿼터(5개 중 4개 사용) 증설 없이 만들기 위해 네트워크를 prod에 얹었다. 나머지는 클러스터에 묶이지 않는
리소스라 재사용했다.

| 공유 리소스 | 영향 |
|---|---|
| VPC, IGW, **regional NAT**, public 서브넷 | dev의 외부 트래픽(이미지 pull, S3, CloudWatch Logs 조회)도 prod와 같은 NAT를 지난다. VPC endpoint가 없어 S3 통신도 NAT를 거친다. NAT 장애는 두 환경에 같이 온다 |
| IAM 역할: 클러스터·노드·vpc-cni·ebs-csi·로드밸런서 컨트롤러 | 정책을 바꾸면 두 클러스터에 같이 적용된다 |
| ECR `<service>/api` 5개 | dev 빌드와 prod 빌드가 같은 저장소에 쌓인다. 태그(`build-N-M`)는 겹치지 않지만 태그만 보고는 어느 브랜치 빌드인지 모른다 |
| ACM 인증서 (`*.loresentry.com`, ALB용·CloudFront용) | 갱신 검증 레코드도 공유 |
| CloudFront 정책(캐시·origin request·응답 헤더), OAC | dev 프런트 배포가 prod 설정을 그대로 쓴다. 정책을 고치면 prod 프런트도 바뀐다 |
| GitOps 저장소 `loresentry-gitops`의 `main` | 두 Argo CD가 같은 브랜치를 읽는다 (§7.5). `workload/base/` 변경은 두 환경에 동시에 반영된다 |
| CI: GitHub OIDC 역할, 배포용 GitHub 토큰(Secrets Manager) | 두 Lambda가 같은 토큰으로 커밋한다 |
| Google Cloud 프로젝트, OAuth 동의 화면 | OAuth 클라이언트는 dev 전용을 따로 쓰지만 동의 화면 설정은 같다 (§9) |
| AWS 계정(Innovation Sandbox), 서비스 쿼터, 예산 | dev 비용이 같은 예산에서 나간다. **리스 만료·예산 초과 시 prod와 dev가 함께 정리된다** |
| Cloudflare `loresentry.com` 영역 | 레코드만 다르다 |

### 격리는 보안 그룹이 맡는다

같은 VPC 안이라 라우팅은 열려 있다. 대신 각 DB SG는 자기 클러스터 SG에서 오는 연결만 받고, 두 클러스터 SG는 서로를 허용하지
않는다. 그래서 dev 파드는 prod RDS·Neptune·Kafka·파드에 닿지 않고, 반대도 마찬가지다.

| 실험 | prod 영향 |
|---|---|
| dev 노드·파드 종료, Kafka 브로커 종료, dev RDS·Neptune 재부팅, 2b AZ 노드 종료 | 없음 |
| dev ALB로 부하 | 없음 (ALB·서버·DB가 따로) |
| **VPC 안 private 서브넷에 부하 발생기를 두는 것** | 있음 — 공유 NAT를 지난다. 노트북이나 공인 IP를 가진 EC2에서 보낸다 |
| **NAT·VPC·공유 IAM 역할을 끄거나 바꾸는 실험** | 있음 — 하지 않는다 |

## 4. prod와 같은 것

| 항목 | 값 |
|---|---|
| EKS | v1.36, 인증 모드 `API`, endpoint public+private, 서비스 CIDR `172.20.0.0/16` |
| 노드 | `r7i.large` ×3 (min 2 / max 4), AL2023 `1.36.3-20260827`, 50GiB, node repair, **2a 1대 / 2b 2대** |
| 애드온 | vpc-cni·coredns·kube-proxy·ebs-csi·pod-identity-agent·metrics-server·node-monitoring-agent, 같은 버전 |
| RDS | PostgreSQL 18.6 `db.t4g.micro`, gp3 20GB, Single-AZ **2b**, 백업 7일 |
| Neptune | 1.4.8.0 `db.t4g.medium` writer 1대 **2b** |
| 클러스터 구성요소 | Argo CD v3.5.2, LBC 3.5.0, Strimzi 1.2.0 / Kafka 4.3.1, kube-prometheus-stack·Telepresence — 차트 버전과 values 동일. Grafana datasource 설정에 dev만 Loki가 없다(§11) |
| 워크로드 | `workload/base` 그대로. replica·CPU·메모리 request/limit 동일 |
| 오토스케일 | 없음 (HPA·Cluster Autoscaler 모두 없음, prod와 같음) |

노드 그룹은 처음에 2a 2대 / 2b 1대로 떴다. RDS와 Neptune이 2b에 있으므로, 그대로 두면 "2b AZ 장애" 실험이 prod와 다른 결과를
낸다. `bootstrap/dev/match-prod-azs.sh`로 prod와 같은 배치로 맞췄다.

## 5. prod와 다른 것 — 의도적으로

| 차이 | 이유 |
|---|---|
| 호스트 `dev`·`api-dev`·`media-dev`·`argocd-dev`·`grafana-dev` | 와일드카드 인증서가 한 단계만 덮으므로 `api.dev.`가 아니라 `api-dev.` |
| gateway·authentication이 **`dev` 스프링 프로필** | 두 서비스는 프로필마다 허용하는 도메인을 코드에 고정한다. `dev` 프로필은 프런트 `https://dev.loresentry.com`, API `https://api-dev.loresentry.com`, Google 콜백 `https://api-dev.loresentry.com/auth/oauth/google/callback`만 받는다. 쿠키(`__Host-`)와 보안 설정은 prod와 같다 |
| ConfigMap `postgres`·`neptune`·`media`·`refresh` | dev 리소스를 가리킨다. base는 그대로 두고 dev overlay에서 바꾼다 |
| **OpenAI mock** (`OPENAI_BASE_URL=http://openai-mock/v1`) | 같은 OpenAI 조직의 사용 한도를 prod와 나눠 쓰지 않고 비용을 쓰지 않으려고 (§8) |
| 프런트의 GA 측정 ID 비움, `robots.txt` 수집 금지 | dev 방문이 운영 GA·Google Ads 전환에 섞이지 않고, 검색엔진에 dev가 잡히지 않게 |
| Google 로그인은 dev 전용 OAuth 클라이언트 | prod 클라이언트와 비밀 값을 나누지 않으려고. **아직 설정 전이다 (§9)** |
| Argo CD SSO 없음 (로컬 `admin`) | GitHub OAuth App이 하나 더 필요하다. 필요해지면 추가한다 |
| 노드 SSH 없음 | prod는 22번 포트가 전 세계에 열려 있다. 따라 하지 않는다 |
| dev 미디어 버킷 CORS는 `https://dev.loresentry.com` | dev 프런트에서 이미지를 직접 올리므로 |
| 로그 수집이 ELK(`logging/`)이고 Loki·Alloy가 없다. 로깅 전용 노드 그룹 1대가 더 있다 | prod를 옮기기 전에 dev에서 먼저 세우고 검증했다. prod는 아직 Loki·Alloy다. ES·Logstash 디스크가 2b에 묶여 있어 로깅 노드는 2b 서브넷에만 둔다 |
| RDS·Neptune 로그용 파라미터 그룹과 로그 내보내기, EKS `authenticator` 로그, ALB·CloudFront 접근 로그 | 위와 같다. 파라미터 그룹 교체 때 재부팅이 필요해 dev에서 리허설했다(RDS 약 20초, Neptune 약 9초 중단). EKS audit은 CloudWatch 수집이 하루 약 2.3GB라 켜지 않는다(prod도 같은 방침) |

CORS·CSRF는 Origin `https://dev.loresentry.com` 기준이다. prod 프런트에서 dev API로의 요청은 거절된다.

## 6. 네임스페이스

dev 클러스터의 네임스페이스는 `dev`다. 분리는 클러스터가 맡고, 이름이 다른 것은 **사람의 실수를 막는 장치**다.

- 컨텍스트를 헷갈려 dev 클러스터에 `kubectl -n prod ...`를 치면 네임스페이스가 없어 바로 실패한다. 반대도 같다.
- Grafana에서는 `namespace="dev"`, Kibana에서는 `kubernetes.namespace: dev`로 바로 구분된다. ELK data stream 이름도 `logs-<dataset>-dev`로 끝난다.

| 환경 | kube context | 네임스페이스 |
|---|---|---|
| prod | `lore-sentry` | `prod` |
| dev | `lore-sentry-dev` | `dev` |

## 7. CI/CD

### 7.1 브랜치와 배포 대상

**각 저장소의 `main`은 prod로, `dev`는 dev로 배포된다.** 같은 workflow 파일(`.github/workflows/ci-cd.yaml`) 하나가 브랜치를 보고
배포 대상을 고른다.

| 저장소 | `main` → | `dev` → |
|---|---|---|
| gateway, authentication, content, ai-chat, graph-rag | Lambda `loresentry-update-gitops` → `workload/overlays/prod` → prod 클러스터 | Lambda `loresentry-update-gitops-dev` → `workload/overlays/dev` → dev 클러스터 |
| frontend | S3 `loresentry-web-prod-…` + prod CloudFront | S3 `loresentry-web-dev-…` + dev CloudFront |

### 7.2 백엔드

```text
dev 브랜치 push
  → GitHub Actions: 테스트·이미지 빌드
  → ECR <service>/api 에 build-<run>-<attempt> 로 push
  → Lambda loresentry-update-gitops-dev
  → loresentry-gitops main 의 workload/overlays/dev/kustomization.yaml newTag 한 줄 커밋
  → dev Argo CD 가 감지 → dev 클러스터 롤링 업데이트
```

| 설계 | 이유 |
|---|---|
| workflow 하나, `LAMBDA_FUNCTION_NAME`만 브랜치로 고른다 (`github.ref_name == 'dev'`) | 두 브랜치가 같은 빌드·테스트 과정을 거친다 |
| 두 브랜치가 한 workflow의 실행 번호를 나눠 쓴다 | ECR 태그가 immutable이라 태그가 겹치면 push가 실패한다. 같은 번호 체계를 쓰면 겹치지 않는다 |
| concurrency group을 브랜치별로 (`deploy-<repo>-<branch>`) | dev push가 진행 중인 prod 배포를 취소하지 않는다. 2026-10-30 실제로 gateway의 prod·dev 배포가 동시에 돌아 둘 다 성공했다 |
| Lambda 2개 (같은 코드, `GITOPS_ROOT`만 다름) | 함수가 고칠 수 있는 파일이 환경 변수로 고정된다. **dev push가 prod overlay를 고칠 경로가 없다** |

공용 CI 역할(`lore-sentry-github-actions-common`)에 dev Lambda 호출 권한을 더했다. GitHub OIDC 신뢰 조건은 원래 조직의 모든
ref를 허용하므로 바꾸지 않았다. Lambda 코드와 배포 스크립트는 `loresentry-lambda`에 있고, `deploy.sh`가 두 함수에 같은 코드를
올린다.

배포 커밋 메시지는 두 환경 모두 `deploy(<service>/api): update image to <tag>`다. **어느 환경인지는 바뀐 파일로 본다.**

### 7.3 프런트엔드

```text
dev 브랜치 push
  → 한국어판·영어판 빌드·검증 (pnpm check:cdn)
  → dev 빌드만: config.json 의 apiBaseUrl 을 api-dev 로, gaMeasurementId 를 빈 값으로, robots.txt 를 Disallow 로
  → S3 loresentry-web-dev 에 업로드 → dev CloudFront 무효화
```

dev CloudFront는 prod 배포 설정을 그대로 복사했다. 다른 것은 도메인, 원본 버킷, 설명, 언어 고르는 함수 이름뿐이다. 함수
코드도 prod에서 쓰는 코드를 복사했지만 이름을 따로 둬서, 함수를 고칠 때 dev에서 먼저 시험할 수 있다. CI 배포 권한
(`loresentry-frontend-cdn-deploy`)이 dev 버킷과 dev CloudFront를 함께 덮는다.

### 7.4 이미지와 코드 흐름

| 하고 싶은 것 | 방법 |
|---|---|
| 새 기능을 dev에서 먼저 확인 | 기능 브랜치 → `dev`로 PR·머지 → dev 자동 배포 |
| 확인한 것을 prod에 | `dev` → `main`으로 PR·머지 → prod 자동 배포 |
| dev를 prod 코드에 맞추기 | `main` → `dev`로 머지 (주기적으로) |
| dev를 prod와 같은 이미지로 되돌리기 | `bootstrap/dev/sync-images.sh` → gitops PR |
| workflow만 바꿀 때 prod 재배포 막기 | `main` 머지 커밋 제목에 `[skip ci]` |

2026-10-31 기준으로 gateway·authentication의 `dev`에는 `dev` 프로필 커밋이 있고 `main`에는 없다. gateway `main`에는 `dev`에
없는 커밋이 있다. `dev`를 `main`에 머지해도 prod는 `prod` 프로필로 돌므로 `dev` 프로필 코드는 동작에 영향이 없다.

### 7.5 GitOps — 저장소 하나, 브랜치 하나, 경로 둘

```text
loresentry-gitops (main)
├── bootstrap/root-application.yaml    prod 클러스터에 손으로 한 번 apply
├── bootstrap/dev/                     dev 를 만들고 지우는 스크립트, dev root-application.yaml
├── argocd-apps/                       ← prod Argo CD
├── argocd-apps-dev/                   ← dev Argo CD
├── platform/                          ← prod
├── platform-dev/                      ← dev (storage 는 platform/ 것을 그대로 가져온다)
└── workload/
    ├── base/                          ← 둘 다 (namespace 없음)
    └── overlays/
        ├── prod/                      namespace prod, prod 태그, api.loresentry.com
        └── dev/                       namespace dev, dev 태그, api-dev, 엔드포인트 패치, dev 프로필, openai-mock
```

- **dev Application을 `argocd-apps/`에 넣지 않는다.** prod root가 그 디렉터리를 읽고, 거기 있는 Application은 prod 클러스터에
  배포된다.
- `argocd-apps-dev/`는 prod Application의 복사본이다. 바뀐 값은 클러스터 이름·ALB 그룹·호스트·Strimzi와 Telepresence의
  네임스페이스·Telepresence 서브넷, ALB 접근 로그 설정이다. prod Application을 고치면 dev 복사본도 같이 고친다.
  ELK(`eck-operator.yaml`, `logging.yaml`)는 아직 dev에만 있고, Loki·Alloy는 prod에만 있다.
- `workload/base/`를 고치면 머지 즉시 두 환경에 반영된다. 매니페스트 변경을 dev에서 먼저 시험하는 순서는 지금 구조로는 없다.

## 8. OpenAI mock

dev의 ai-chat·요약기·graph-rag는 클러스터 안의 `openai-mock`(`workload/overlays/dev/openai-mock/`)을 부른다. OpenAI 공식
라이브러리가 `OPENAI_BASE_URL`을 읽으므로 앱 코드는 바꾸지 않았다. Python 표준 라이브러리만 쓰는 서버 하나이고, Responses API의
`/v1/responses`와 `/v1/responses/input_tokens`만 흉내 낸다.

| 요청 | mock의 응답 | 기본 지연 |
|---|---|---|
| ai-chat 문서 고르기 | 지도에서 문서 ID를 최대 3개 골라 `request_documents` 호출 — **content의 실제 문서 읽기가 일어난다** | 1.5~4초 |
| ai-chat 판정 | 항상 통과 | 0.8~2초 |
| 답변·요약 | 고정 문장 1,200자 | 3~8초 |
| graph-rag 최신화 | 스키마의 필수 항목만 채운 "변경 없음" | 0.8~2초 |

지연과 답 길이는 Deployment의 env(`MOCK_*_DELAY_MS`, `MOCK_ANSWER_CHARS`)로 바꾼다. 실제와 다른 점은 이렇다.

- 답의 품질은 평가할 수 없다.
- 판정이 항상 통과라 실제보다 작업 단계가 적을 수 있다.
- 오류(429·타임아웃)를 내지 않는다.
- 토큰 수는 글자 수 ÷ 4로 어림한다.

실제 OpenAI를 쓰려면 **별도 OpenAI 프로젝트**의 키로 `ai-chat-openai` Secret을 바꾸고, overlay의 `OPENAI_BASE_URL` 패치를 지운다.

## 9. 남은 작업 — Google 콘솔

dev의 백엔드는 dev 콜백을 받을 준비가 끝났다. 지금 dev Secret의 Google 클라이언트 값은 **더미**라서, dev에서 Google 로그인을
누르면 Google 화면에서 실패한다. 아래를 마치면 dev에서도 Google 로그인이 된다.

### 9.1 dev 전용 OAuth 클라이언트 만들기

1. Google Cloud Console에서 **prod 로그인용 OAuth 클라이언트가 있는 프로젝트**를 연다.
2. **API 및 서비스 → 사용자 인증 정보 → 사용자 인증 정보 만들기 → OAuth 클라이언트 ID**.
3. 애플리케이션 유형 **웹 애플리케이션**, 이름은 `Lore Sentry dev`처럼 구분되게.
4. **승인된 리디렉션 URI**에 정확히 이 값 하나:
   ```text
   https://api-dev.loresentry.com/auth/oauth/google/callback
   ```
   한 글자라도 다르면 Google이 `redirect_uri_mismatch`로 거절한다. 승인된 JavaScript 원본은 비워 둔다
   (로그인 교환을 서버가 한다).
5. 만들어진 **클라이언트 ID**와 **클라이언트 보안 비밀**을 복사한다.

prod 클라이언트에 dev 리디렉션 URI를 추가하는 방법도 있지만 권하지 않는다. prod와 dev가 같은 비밀 값을 갖게 되고, dev를 지울
때 prod 클라이언트를 손대야 한다.

### 9.2 OAuth 동의 화면 확인

**Google 인증 플랫폼 → 대상(또는 OAuth 동의 화면)**에서 확인한다.

| 확인할 것 | 조치 |
|---|---|
| 게시 상태가 **테스트** | dev에서 로그인할 Google 계정을 **테스트 사용자**에 추가한다. 추가하지 않은 계정은 `access_denied`로 막힌다 |
| 게시 상태가 **프로덕션** | 할 일 없음 |
| 승인된 도메인에 `loresentry.com` | prod 때문에 이미 있을 것이다. 없으면 추가한다 |

### 9.3 dev 클러스터에 넣기

```bash
cd loresentry-gitops/bootstrap/dev
./set-google-oauth.sh          # 클라이언트 ID·보안 비밀을 물어본다. 입력하면 auth 가 재시작된다
```

또는 `DEV_GOOGLE_CLIENT_ID=… DEV_GOOGLE_CLIENT_SECRET=… ./set-google-oauth.sh`. 스크립트는 `DEV_` 접두사가 붙은 변수만 읽는다.
셸에 로컬 개발용 `GOOGLE_CLIENT_ID`가 있어도 dev에 들어가지 않게 하려는 것이다.

### 9.4 확인

1. `https://dev.loresentry.com/login`에서 Google로 로그인한다.
2. Google 동의 → `https://api-dev.loresentry.com/auth/oauth/google/callback` → `https://dev.loresentry.com`으로 돌아와 로그인된
   상태면 끝이다. 처음 가입한 계정이면 약관 동의 화면이 먼저 나온다.

| 증상 | 원인 |
|---|---|
| Google 화면에 `invalid_client` | 클라이언트 ID가 아직 더미이거나 잘못 들어갔다. 9.3을 다시 한다 |
| `redirect_uri_mismatch` | 9.1의 리디렉션 URI가 정확히 일치하지 않는다 |
| `access_denied` / 앱이 테스트 중이라는 안내 | 9.2의 테스트 사용자에 그 계정이 없다 |
| 로그인 후 `/login?result=unavailable` | auth·세션 저장소 쪽 오류다. `kubectl --context lore-sentry-dev -n dev logs deploy/authentication-api` |

## 10. 팀원 접속 — kubectl 설정

dev 클러스터 접속은 prod와 **같은 AWS 자격 증명**(`lorekeeper` 프로파일의 액세스 키)을 쓴다. 새 키는 필요 없고,
kubeconfig에 dev 컨텍스트만 하나 더 만들면 된다. AWS CLI·kubectl·`lorekeeper` 프로파일 준비는
`ENVIRONMENT_SETTING.md` §1~§2를 따른다.

### 10.1 누가 접속할 수 있나

EKS access entry는 **클러스터마다 따로** 등록한다. dev에는 prod와 같은 사람이 같은 그룹으로 등록돼 있다.

| 신원 | dev 클러스터 | 비고 |
|---|---|---|
| `mem-jh` | `lore-viewers` | 액세스 키가 없으면 CLI를 쓸 수 없다 (`ENVIRONMENT_SETTING.md` 부록) |
| `mem-jh-2` | `lore-viewers` | |
| `mem-sj` | `lore-viewers` | |
| `mem-sh` | **없음** | prod와 같다. 필요하면 관리자가 등록한다 (10.6) |
| 관리자 SSO 역할 | `AmazonEKSClusterAdminPolicy` + `lore-viewers` | |

### 10.2 kubeconfig에 dev 컨텍스트 만들기

```bash
aws eks update-kubeconfig \
  --name lore-sentry-dev-k8s \
  --region ap-northeast-2 \
  --profile lorekeeper \
  --alias lore-sentry-dev

kubectl config set-context lore-sentry-dev --namespace=dev
```

prod 컨텍스트(`lore-sentry`, 기본 네임스페이스 `prod`)는 그대로 남는다. 이제 kubeconfig에 컨텍스트가 둘이다.

```bash
kubectl config get-contexts
# CURRENT   NAME              CLUSTER                                         NAMESPACE
#           lore-sentry       arn:aws:eks:...:cluster/lore-sentry-k8s         prod
# *         lore-sentry-dev   arn:aws:eks:...:cluster/lore-sentry-dev-k8s     dev
```

`update-kubeconfig`는 방금 만든 컨텍스트를 현재 컨텍스트로 바꾼다. **prod를 보던 터미널도 이제 dev를 본다.**

### 10.3 접속 확인

```bash
kubectl --context lore-sentry-dev auth whoami     # Groups 에 lore-viewers 가 있어야 한다
kubectl --context lore-sentry-dev get pods        # dev 네임스페이스의 파드 목록
```

| 증상 | 원인 |
|---|---|
| `You must be logged in to the server (Unauthorized)` | AWS 인증은 됐지만 dev 클러스터에 access entry가 없다. 관리자에게 등록을 요청한다 (10.6) |
| `context was not found ... lore-sentry-dev` | 10.2의 `--alias lore-sentry-dev`를 하지 않았다 |
| `namespaces "prod" not found` | dev 컨텍스트에서 `-n prod`를 썼다. 컨텍스트를 확인한다 |
| `Error from server (Forbidden)` | 권한 밖의 동작이다. 정상이다 (10.4) |

### 10.4 권한 — prod와 같다

`dev` 네임스페이스 **읽기 전용**이다. 아래 조회·차단 결과는 2026-10-31에 `kubectl auth can-i`로 dev와 prod가 같음을
확인했다. Telepresence는 prod와 같은 설정(관리 네임스페이스만 `dev`)으로 배포했지만, 팀원 계정으로 직접 연결해 보지는 않았다.

| 되는 것 | 안 되는 것 |
|---|---|
| Pod·Deployment·Service·Ingress·ConfigMap·Event 조회, `describe` | Secret 조회 |
| `kubectl logs` | `kubectl exec` |
| Telepresence 연결 (10.5) | 생성·수정·삭제·재시작 |
| | `dev` 외 네임스페이스(`kube-system`·`argocd`·`monitoring`), 노드, Strimzi 리소스(`kafkatopics` 등) |

Argo CD·Grafana의 dev admin 비밀번호는 `argocd`·`monitoring` 네임스페이스의 Secret이라 팀원이 직접 읽을 수 없다. 관리자에게
받는다.

### 10.5 prod와 dev를 같이 쓸 때

- **명령마다 `--context`를 붙이는 것을 습관으로 한다.** 현재 컨텍스트를 믿고 치면 엉뚱한 클러스터를 본다.
  ```bash
  kubectl config current-context
  kubectl config use-context lore-sentry       # prod 로 전환
  kubectl config use-context lore-sentry-dev   # dev 로 전환
  ```
- 네임스페이스 이름이 다르다(`prod` / `dev`). 컨텍스트를 헷갈리면 네임스페이스가 없다는 오류로 바로 드러난다.
- **Telepresence는 한 번에 한 클러스터만** 연결한다. 다른 쪽에 붙어 있으면 먼저 끊는다.
  ```bash
  telepresence quit -s
  telepresence connect --context lore-sentry-dev --namespace dev   # 클라이언트 2.31.x
  ```
  연결되면 dev 서비스(`http://content-api.dev`)와 dev DB에 닿는다. DB 마스터 비밀번호는
  `aws rds describe-db-instances --db-instance-identifier lore-sentry-dev-postgres`의 `MasterUserSecret`에서 꺼낸다
  (`ENVIRONMENT_SETTING.md` §6과 같은 절차, 인스턴스 이름만 다르다).
- Lens에서는 `lore-sentry-dev` 클러스터를 열고 Settings → Namespaces → Accessible Namespaces에 `dev`를 넣는다.

### 10.6 관리자 — 팀원을 dev에 등록

prod에 등록했다고 dev에 자동으로 들어가지 않는다. dev 클러스터에도 따로 만든다.

```bash
aws eks create-access-entry --profile lorekeeper --region ap-northeast-2 \
  --cluster-name lore-sentry-dev-k8s \
  --principal-arn arn:aws:iam::<AWS_ACCOUNT_ID>:user/<사용자> \
  --kubernetes-groups lore-viewers \
  --type STANDARD
```

권한은 `loresentry-gitops`의 `workload/overlays/dev/rbac.yaml`(RoleBinding `lore-viewers` → `view`)이 정한다. 팀원을 지울
때는 `aws eks delete-access-entry`를 두 클러스터에 모두 한다.

### 10.7 관리자 명령

```bash
# Argo CD admin 비밀번호
kubectl --context lore-sentry-dev -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d
# Grafana admin 비밀번호
kubectl --context lore-sentry-dev -n monitoring get secret grafana-admin -o jsonpath='{.data.admin-password}' | base64 -d
```

부하 테스트 사용자는 Google 로그인 없이 `loadtest/seed-sessions.sh`로 만든다. k6 시나리오와 요청 규칙은
`loresentry-gitops/loadtest/README.md`에 있다.

### Git 밖에서 관리하는 dev Secret

`bootstrap/dev/05-cluster-bootstrap.sh`가 prod와 다른 새 값으로 만들었다.

| Secret | 내용 |
|---|---|
| `authentication-db` `content-db` `ai-chat-db` | 서비스 DB 계정 |
| `authentication-runtime` | JWT 개인키·`kid`, valkey 계정, Google OAuth(§9), 콜백 주소 |
| `gateway-runtime` | JWT `kid`, valkey 계정 |
| `authentication-valkey-acl`, ConfigMap `authentication-jwt-public` | valkey ACL, JWT 공개키 |
| `ai-chat-openai` | mock용 더미 키 |
| `monitoring/grafana-admin` | Grafana admin |

## 11. 처음 만들 때 겪은 것과 알려진 문제

| 증상 | 원인 | 조치·상태 |
|---|---|---|
| Ingress·Service 동기화가 `x509: certificate signed by unknown authority`로 실패 | LBC 차트가 렌더마다 인증서를 새로 만들고, Argo CD가 `ignoreDifferences`로 Secret과 웹훅 `caBundle`의 live 값을 지킨다. 첫 동기화가 재시도되면 둘이 다른 렌더에서 온다 | `06-post-sync.sh`가 맞춘다 |
| 노드가 2a 2 / 2b 1 | 노드 그룹이 AZ를 고른다 | `match-prod-azs.sh` |
| 애드온 waiter가 DEGRADED에서 실패 | 노드가 뜨기 전 잠깐 DEGRADED가 된다 | ACTIVE까지 폴링 |
| k6가 첫 요청 뒤로 모두 401 | BFF가 세션 쿠키를 연장하면 k6가 쿠키 jar에 넣고, 직접 넣은 Cookie 헤더와 함께 **두 번** 보낸다 | k6 `cookies` 옵션에 `replace: true` |
| 로컬 `GOOGLE_CLIENT_*`가 dev Secret에 들어감 | 스크립트가 일반 이름의 환경 변수를 읽었다 | 되돌리고 `DEV_` 접두사로 바꿨다 |
| **gateway 파드가 새로 뜬 직후, 파드마다 첫 인증 요청이 503 `SESSION_UNAVAILABLE`** | 미확인. 세션 저장소 연결이나 검증 스크립트가 첫 요청 시점에 준비되지 않는 것으로 보인다. 오래 쉰 뒤에는 재현되지 않았다 | **미해결.** dev에서 3번 재현됐다. prod도 배포 직후 같을 가능성이 있다 |
| `strimzi-kafka-operator` OutOfSync | prod와 같은 표시상 문제 | 그대로 둔다 |
| dev Grafana에서 Loki datasource를 고르면 연결 실패 | provisioning에서 빼도 Grafana는 DB에 넣어 둔 datasource를 지우지 않는다 | Grafana 알림 작업 때 `deleteDatasources`로 지운다 |
| kube-system csi-snapshotter가 error를 계속 남긴다(하루 약 2만 줄) | 클러스터에 VolumeSnapshot CRD가 없다 | 그대로 둔다. Kibana에서 걸러 본다 |

## 12. prod 대비 점검 — 2026-10-31

prod와 dev의 실제 상태를 계층별로 꺼내 비교했다(prod는 조회만). 환경 이름·버킷 이름처럼 당연히 다른 값은 맞춘 뒤 비교했다.
dev 로깅 전환(2026-11-01) 뒤 달라진 행은 그 기준으로 고쳤다.

| 계층 | 결과 |
|---|---|
| EKS·노드 그룹·애드온·Pod Identity·access entry | 같다. 플랫폼 버전만 dev `eks.17` / prod `eks.14` (AWS가 정하는 값, 고정 불가) |
| 노드 | OS·커널·kubelet·containerd·allocatable·AZ 배치(2a 1 / 2b 2) 같다 |
| kube-system, VPC CNI 설정 | 같다 |
| Argo CD | 버전·구성요소·설정 같다(SSO·URL 제외). Application은 둘 다 9개이고, dev만 `eck-operator`·`logging`, prod만 `loki`·`alloy`가 있다. 나머지 7개는 정의·상태 같다 |
| 워크로드 | replica·리소스·probe·볼륨·보안 설정 같다. env 차이는 §5의 의도한 것뿐 |
| Service·PDB·NetworkPolicy·PVC·StorageClass·ConfigMap·Secret 키·Kafka·토픽 | 같다. prod에만 쓰이지 않는 옛 `auth-valkey` Secret이 남아 있다 |
| 모니터링 | dev는 PrometheusRule 31·ServiceMonitor 10·PodMonitor 2, 규칙 228개, 수집 대상 36/36 정상. prod(33·12·2, 251개)와의 차이는 prod에만 있는 Loki·Alloy 차트의 것이다. 울리는 경보(`Watchdog`, `KubeCPUOvercommit`)는 같다 |
| ALB | 리스너·헬스체크·대상 그룹 설정 같다. 속성은 dev만 접근 로그가 켜져 있다 |
| RDS·Neptune | 엔진·사양·스토리지·암호화·AZ 같다. 파라미터 그룹과 로그 내보내기는 dev만 켜져 있다(§5). 백업·점검 시간대는 **달랐다 → 맞췄다** |
| S3·CloudFront·Lambda | 같다(CORS 주소·태그 제외). dev는 로그 버킷 2개와 CloudFront 로그 전달이 더 있다 |
| 앱 IAM | graph-rag 같다. dev에는 loki 역할 대신 logstash·filebeat-aws 역할이 있다. **content 미디어 정책은 prod가 저장소 템플릿보다 옛 버전**이다(아래) |
| CI workflow | 6개 저장소 모두 `main`·`dev`의 파일이 같다 |

### 고친 것

- **dev 코드가 prod보다 뒤처져 있었다.** dev를 만든 뒤 `main`에 설정 오류 검사 기능이 들어갔다(gateway 4·content 2·ai-chat 6
  커밋, ai-chat 마이그레이션 V6~V8). dev 프런트는 이미 그 화면을 갖고 있어 dev에서 그 기능이 실패하는 상태였다.
  `main` → `dev`로 머지해 맞췄고, dev에서 설정 오류 검사가 끝까지 도는 것을 확인했다. **`main` → `dev` 머지는 주기적으로 해야
  한다** (§7.4).
- **DB 백업·점검 시간대.** dev 생성 때 무작위로 정해져 dev RDS 백업이 23:33 KST에 돌았다(prod 05:27). prod 값으로 맞췄고
  `03-databases.sh`가 앞으로 prod 값을 복사한다.

### 남은 것 — prod 쪽

- **prod content 역할의 미디어 정책(`lore-sentry-content-media-policy`)이 v1 그대로다.** 저장소 템플릿
  (`loresentry-content/docs/aws/iam-content-media-policy.json`)에는 없는 객체 확인용 `s3:ListBucket`이 있는데 prod에는 적용되지
  않았다. dev는 템플릿대로라 이 권한이 있다. content가 403도 "없음"으로 처리하므로 동작 차이는 없지만, 맞추려면
  `setup-media.sh`를 prod에 다시 실행한다.
- prod에 쓰이지 않는 `auth-valkey` Secret이 남아 있다.
- 배포된 prod Lambda 코드가 저장소와 주석 한 줄 다르다. 다음 `deploy.sh`에서 맞춰진다.

## 13. 비용과 정리

상시 가동 시 시간당 약 $1.0로 추정한다. EKS, 노드 4대(로깅 노드 포함), Neptune, RDS, ALB, EBS를 합한 값이고, NAT는 공유라 처리량 요금만
더해진다. 계정 SCP가 비용·가격 API를 막으므로 정확한 금액은 ISB 포털에서 본다.

```bash
CONFIRM=lore-sentry-dev-k8s loresentry-gitops/bootstrap/dev/teardown.sh
```

순서는 다음과 같다.

1. ALB를 먼저 놓아 준다.
2. 클러스터를 지운다(로깅 노드 그룹 포함).
3. `Retain`으로 남는 EBS 볼륨을 지운다.
4. DB와 로그용 파라미터 그룹, CloudFront 로그 전달, CloudFront 2개와 함수, 버킷 5개, dev IAM을 지우고, 프런트 배포 권한에서 dev 항목을 뺀다.
5. dev 서브넷을 지운다.
6. CloudWatch 로그 그룹 3개를 지운다.

공유 리소스는 건드리지 않는다. 지운 뒤 Cloudflare의 `dev`·`api-dev`·`media-dev`·`argocd-dev`·`grafana-dev`·`kibana-dev` 레코드와 Google
콘솔의 dev OAuth 클라이언트를 지운다. Lambda `loresentry-update-gitops-dev`와 각 저장소의 `dev` 브랜치는 남으니, dev를 다시
만들지 않을 거라면 함께 정리한다.
