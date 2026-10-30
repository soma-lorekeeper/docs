# dev 클러스터 — 부하·가용성 테스트 환경

> 최신화: 2026-10-30
> 대상 환경: AWS `ap-northeast-2` / EKS `lore-sentry-dev-k8s` / namespace `dev`
> 상태: 구축과 기능 확인을 마쳤다. 스크립트·매니페스트·운영 절차의 원본은 `loresentry-gitops`의
> `bootstrap/dev/README.md`와 `loadtest/README.md`다. 이 문서는 무엇을 왜 그렇게 했는지만 적는다.

## 1. 한 줄 요약

prod(`lore-sentry-k8s`)와 **같은 방식으로** 만든 두 번째 EKS 클러스터다. 같은 AWS 계정에 있고, prod의 VPC·NAT를
같이 쓰며, 클러스터·DB·버킷·ALB·Argo CD는 따로 가진다. prod에 부하를 주지 않고 부하·장애를 실험하려고 만들었다.

| 주소 | 용도 |
|---|---|
| `https://api-dev.loresentry.com` | dev API (gateway) |
| `https://argocd-dev.loresentry.com` | dev Argo CD (로컬 admin) |
| `https://grafana-dev.loresentry.com` | dev Grafana (Prometheus·Loki) |
| `https://media-dev.loresentry.com` | dev 사용자 이미지 CloudFront |
| `https://dev.loresentry.com` | dev 프런트엔드 (S3 + CloudFront, prod 배포 설정 복사) |

## 2. prod와 같은 것

EKS 버전·인증 모드·endpoint 설정·서비스 CIDR, 노드 그룹(`r7i.large` ×3, AMI 릴리스, 50GiB, node repair), 애드온 7개와
그 버전, RDS(PG 18.6 `db.t4g.micro` Single-AZ)와 Neptune(1.4.8.0 `db.t4g.medium` writer 1대), 논리 DB 3개와 서비스
역할·`CONNECT` 차단, Argo CD v3.5.2와 Application 구성, 차트 버전과 values, 워크로드 이미지 태그·replica·리소스.

**배치도 같게 맞췄다.** 노드는 2a 1대 / 2b 2대, RDS와 Neptune은 2b다. 노드 그룹은 처음에 2a 2 / 2b 1로 떴는데,
그대로 두면 "2b AZ 장애" 실험이 prod와 다른 결과를 낸다(prod는 2b에 노드 둘과 DB 둘이 모두 있다). 그래서 노드를
4대로 늘려 2b를 채운 뒤 2a 하나를 뺐다.

## 3. prod와 공유하는 것 — 쿼터 때문에

EIP 쿼터가 5개인데 prod가 4개를 쓴다(regional NAT 2개, ALB 2개). 쿼터 증설 없이 만들기 위해 NAT를 새로 만들지
않았다.

| 공유 | dev 전용 |
|---|---|
| VPC, IGW, regional NAT, public 서브넷 | private 서브넷 `10.20.13.0/24`·`10.20.14.0/24`와 라우팅 테이블 |
| ACM 인증서, ECR, CloudFront OAC | EKS 클러스터·노드 그룹, ALB(IngressGroup `lore-sentry-dev`), Argo CD |
| 클러스터·노드·vpc-cni·ebs-csi·LBC IAM 역할 (클러스터에 묶이지 않는 역할) | RDS·Neptune과 각 SG, S3 3개, 앱 IAM 역할 3개(dev 버킷만 접근) |

새 ALB는 EIP 쿼터에 걸리지 않았다. ALB의 공인 IP는 서비스 관리형으로 따로 센다.

격리는 보안 그룹으로 한다. 각 DB SG는 자기 클러스터 SG만 허용하고, 두 클러스터 SG는 서로를 허용하지 않는다.
**NAT는 공유이므로 부하 발생기를 이 VPC의 private 서브넷에 두지 않는다.**

## 4. prod와 다른 것 — 의도적으로

| 차이 | 이유 |
|---|---|
| 호스트 `*-dev.loresentry.com` | 와일드카드 인증서가 한 단계만 덮는다. `api.dev.`가 아니라 `api-dev.` |
| ConfigMap 4개(`postgres`·`neptune`·`media`·`refresh`)의 주소·버킷 | dev 리소스를 가리킨다. base는 그대로 두고 dev overlay에서 바꾼다 |
| **OpenAI mock** (`OPENAI_BASE_URL=http://openai-mock/v1`) | 같은 OpenAI 조직의 rate limit을 prod와 나눠 쓰지 않고, 돈을 쓰지 않으려고 |
| gateway·auth 는 `dev` 프로필 | 두 서비스는 프로필마다 허용하는 도메인을 코드에 고정한다. `dev` 프로필(2026-10-30, 각 저장소 `dev` 브랜치)은 프런트 `https://dev.loresentry.com`, API `https://api-dev.loresentry.com`, Google 콜백 `https://api-dev.loresentry.com/auth/oauth/google/callback` 만 받는다. 쿠키는 prod 와 같은 `__Host-` |
| Google 로그인은 dev 전용 OAuth 클라이언트로 | Google 콘솔에서 만든 클라이언트를 `bootstrap/dev/set-google-oauth.sh`로 넣는다. 부하 테스트 사용자는 지금처럼 DB·valkey 에 직접 만든다 |
| 프런트 GA 끔, `robots.txt` 수집 금지 | dev 방문이 운영 GA·Google Ads 전환에 섞이지 않게, 검색엔진에 잡히지 않게 |
| Argo CD SSO 없음, 노드 SSH 없음 | 실험과 무관하고, SSH는 prod에서도 전 세계에 열려 있어 따라 하지 않는다 |

CORS·CSRF 는 Origin `https://dev.loresentry.com` 기준이다. prod 프런트(`loresentry.com`)에서 dev API 로의 요청은 거절된다.

### OpenAI mock이 하는 일

Responses API를 흉내 낸다. ai-chat에는 지도에서 노드 id를 골라 `request_documents`를 부르는 응답을 주므로 **content의
문서 읽기 부하가 실제로 생긴다.** 판정은 통과, 답은 고정 문장이다. 지연은 env로 조절한다(도구 1.5–4초, 답 3–8초, 판정
0.8–2초). graph-rag에는 스키마의 최소 응답(변경 제안 없음)을 준다. 실제 OpenAI가 필요하면 별도 OpenAI 프로젝트 키로
바꾼다.

## 5. 배포 — 앱 저장소의 `dev` 브랜치 (2026-10-30)

백엔드 5개 저장소에 `dev` 브랜치를 만들었다. workflow 는 `main` 과 같은 파일 하나이고, 브랜치에 따라 Lambda 만 다르다.

| 브랜치 | Lambda | 고치는 overlay | 클러스터 |
|---|---|---|---|
| `main` | `loresentry-update-gitops` | `workload/overlays/prod` | prod |
| `dev` | `loresentry-update-gitops-dev` | `workload/overlays/dev` | dev |

- 두 Lambda 는 같은 코드에 `GITOPS_ROOT` 만 다르다. 그래서 dev push 가 prod overlay 를 고칠 경로가 없다.
- 한 workflow 의 실행 번호를 두 브랜치가 나눠 쓰므로 ECR(immutable) 태그가 겹치지 않는다.
- concurrency group 을 브랜치별로 나눠 dev push 가 진행 중인 prod 배포를 취소하지 않게 했다.
- 공용 CI 역할의 Lambda 호출 권한에 dev 함수를 더했다. OIDC 신뢰 조건은 이미 모든 ref 를 허용한다.
- workflow 변경은 `[skip ci]` 로 머지해 prod 를 같은 코드로 다시 배포하지 않았다.
- 프런트엔드도 `dev` 브랜치가 dev 버킷·CloudFront(`dev.loresentry.com`)에 배포한다. dev 빌드만 `apiBaseUrl` 을
  `api-dev` 로, GA 측정 ID 를 빈 값으로 바꾸고 `robots.txt` 로 수집을 막는다.

첫 실행에서 5개 모두 성공했고, gitops `main` 에 dev overlay 만 바꾼 커밋 5개가 생겼으며, dev 클러스터가 새 이미지로
바뀌는 동안 prod overlay 와 prod 파드는 이전 태그 그대로였다.

## 6. 확인한 것 (2026-10-29)

- Argo CD Application 9개 모두 Synced/Healthy. `strimzi-kafka-operator`의 OutOfSync는 prod와 같은 표시상 문제다
- 서비스 4개가 각자 DB·Neptune에 연결(`/health/db`), Kafka 브로커 노드당 1대, 토픽 10개 Ready, summarizer가
  `content.project.changed.v1`을 lag 0으로 소비
- 시드 사용자로 dev ALB를 거쳐: 계정 조회, 샘플 프로젝트 생성(문서 21개), 파일 트리·관계 그래프, 이미지 presign이
  dev 버킷을 가리킴, AI 챗 SSE가 지도 → 문서 3개 읽기 → 판정 → 답까지 8초
- k6 VU 5, 50초: 요청 855개, 실패 0.23%(클라이언트 측 2건), 읽기 p95 80ms
- 작업 전후 prod Argo CD 상태·`api.loresentry.com`·ALB 타깃 상태가 같음

## 7. 처음 만들 때 겪은 것

| 증상 | 원인 | 조치 |
|---|---|---|
| Ingress·Service 동기화가 `x509: certificate signed by unknown authority`로 실패 | LBC 차트가 렌더마다 자체 서명 인증서를 만들고, Argo CD가 `ignoreDifferences`로 Secret과 웹훅 `caBundle`의 live 값을 지킨다. 첫 동기화가 재시도되면 둘이 다른 렌더에서 온다 | `caBundle`을 Secret의 CA로 맞추고 컨트롤러 재시작 (`06-post-sync.sh`) |
| 노드가 2a 2 / 2b 1 | 노드 그룹이 AZ를 고른다 | `match-prod-azs.sh` |
| k6 요청이 첫 요청 뒤로 모두 401 | BFF가 `Set-Cookie`로 세션을 연장하면 k6가 jar에 넣고, 직접 넣은 Cookie 헤더와 함께 세션 쿠키를 **두 번** 보낸다. BFF는 이를 거절한다 | k6 `cookies` 옵션에 `replace: true` |
| 애드온 waiter가 DEGRADED에서 실패 | 노드가 뜨기 전 잠깐 DEGRADED가 된다 | ACTIVE가 될 때까지 폴링 |

## 8. 비용과 정리

상시 가동 시 시간당 약 $0.8로 추정한다(계정 SCP가 비용·가격 API를 막아 정확한 값은 ISB 포털에서 본다).
테스트가 끝나면 `CONFIRM=lore-sentry-dev-k8s bootstrap/dev/teardown.sh`로 지운다. gp3가 `Retain`이라 EBS 볼륨이
남으므로 스크립트가 따로 지운다. 공유 리소스는 건드리지 않는다. 그 뒤 Cloudflare 레코드 네 개를 지운다.
