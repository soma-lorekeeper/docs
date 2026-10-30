# 로컬 개발 환경 온보딩 — AWS · EKS 접근 설정

> 최신화: 2026-10-29
> 대상: Lore Sentry 팀원
> 대상 환경: AWS `ap-northeast-2` / EKS `lore-sentry-k8s` / namespace `prod`

이 문서는 팀원이 자기 노트북에서 운영 클러스터를 조회하고 운영 DB에 접속할 수 있게 만드는 절차다. 배포는 다루지 않는다 — 배포는 Argo CD가 GitOps 저장소를 보고 수행하며, 사람이 `kubectl`로 클러스터를 바꾸지 않는다 (`INFRA_AND_CICD.md` §21).

---

## 0. 먼저 알아둘 것 — 경로마다 권한이 다르다

`kubectl`은 읽기 전용이지만 팀원이 운영을 바꿀 수 없는 것은 아니다. AWS·DB·Argo CD·GitOps 저장소에서는 바꿀 수 있다 (2026-10-29 확인).

| 경로 | 팀원 권한 | 자세히 |
|---|---|---|
| `kubectl`·Lens·Telepresence | `prod` 읽기 전용 + Telepresence 연결 (`lore-viewers`) | 아래 표, §6, §10 |
| AWS 콘솔·CLI | `lorekeeper-owner` — `PowerUserAccess` + `IAMFullAccess`. 사실상 계정 관리자 | 부록 |
| RDS PostgreSQL | 마스터 계정 (`rds_superuser`) | §6 |
| Argo CD | GitHub 조직 멤버 전원이 admin | §7 |
| Grafana | 공용 admin 계정 하나 | §7 |
| GitOps 저장소 | write. `main`에 보호 규칙이 없어 push가 곧 배포다 | §8 |

바꿀 수 있다고 바꿔도 되는 것은 아니다. 클러스터 변경은 GitOps PR로 하고 (§8), AWS 리소스와 DB는 운영 중인 서비스의 것이다. IAM·EKS 권한이 있으니 자기 access entry에 정책을 붙여 클러스터 관리자가 될 수도 있지만 하지 않는다 — 클러스터 권한은 Git의 RBAC로 관리한다 (부록).

### `kubectl` 권한

**`kubectl`로 받는 권한은 `prod` 네임스페이스에 대한 읽기 전용이다.** 클러스터를 변경하는 모든 동작은 막혀 있고, 막히는 것이 정상이다.

| 되는 것 | 안 되는 것 |
|---|---|
| Pod·Deployment·Service·Ingress 조회 | **Secret 조회** (`kubectl get secret`) |
| ConfigMap·Event 조회 | `kubectl exec` (컨테이너 진입) |
| `kubectl logs` | 생성·수정·삭제 일체 |
| `kubectl describe` | `prod` 외 네임스페이스 (`kube-system`, `argocd`, `monitoring`, `default`). `ambassador`는 Telepresence 연결에 필요한 Pod 조회·port-forward만 된다 |
| `kubectl port-forward` (§6) | 노드·네임스페이스 등 클러스터 범위 리소스 |
| Telepresence 연결·intercept (§6) | Strimzi 리소스 (`kafkatopics` 등, §8) |

`Forbidden` 오류를 보면 설정이 잘못된 것이 아니라 **의도된 경계에 닿은 것**이다. 권한이 더 필요하면 §8을 본다.

> **Secret은 API로 읽을 수 없지만 완전히 가려진 것은 아니다.** Telepresence intercept는 대상 워크로드의 환경 변수를 가져오고, 거기에는 Secret에서 주입된 서비스 DB 자격 증명이 들어 있다 (§6). 팀원은 DB에 마스터 계정으로 접속하므로(§6) 이 값은 쓸 일이 없지만, 어느 쪽이든 **운영 데이터에 쓰기가 가능**하다는 점을 기억한다.

---

## 1. 사전 준비

| 도구 | 버전 | 확인 |
|---|---|---|
| AWS CLI | **v2** | `aws --version` |
| kubectl | 1.35 ~ 1.37 | `kubectl version --client` |
| Telepresence (§6에서만 필요) | **2.31.x** | `telepresence version` |
| psql (§6 DB 접속, 선택) | 18 | `psql --version` |
| Lens (§10, 선택) | 최신 | 앱 실행 |

클러스터가 Kubernetes v1.36이다. kubectl은 클러스터와 ±1 마이너 버전까지만 보증되므로 범위를 벗어나면 예상 못 한 오류가 난다.

Telepresence는 더 엄격하다. 클러스터에 설치된 Traffic Manager가 **2.31.2**이므로 클라이언트도 같은 마이너 버전이어야 한다. 버전이 벌어지면 연결 자체가 거부된다.

```bash
# macOS
brew install awscli kubectl
brew install telepresenceio/telepresence/telepresence-oss

# Linux (AWS CLI v2)
curl -s "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o awscliv2.zip
unzip -q awscliv2.zip && sudo ./aws/install

# Linux (Telepresence)
curl -fL https://github.com/telepresenceio/telepresence/releases/download/v2.31.2/telepresence-linux-amd64 \
  -o /usr/local/bin/telepresence && chmod +x /usr/local/bin/telepresence
```

AWS CLI v1은 쓰지 않는다. `aws eks get-token`의 동작이 다르다.

---

## 2. AWS 자격 증명 설정

관리자에게 **액세스 키 2종**을 받는다. 콘솔 로그인 URL과 암호는 웹 콘솔 전용이라 CLI에는 쓸 수 없다 (아래 `aws login` 참고).

```text
AWS Access Key ID        AKIA...            식별자
Secret Access Key        wJalrXUtnFEM...    비밀. 재발급 외에는 다시 볼 수 없다
```

프로파일 이름은 `lorekeeper`로 통일한다.

```bash
aws configure --profile lorekeeper
```

```text
AWS Access Key ID [None]:      AKIA...
AWS Secret Access Key [None]:  wJalrXUtnFEM...
Default region name [None]:    ap-northeast-2
Default output format [None]:  json
```

확인한다.

```bash
aws sts get-caller-identity --profile lorekeeper
```

```json
{
  "UserId": "AIDA...",
  "Account": "<AWS_ACCOUNT_ID>",
  "Arn": "arn:aws:iam::<AWS_ACCOUNT_ID>:user/mem-jh"
}
```

`Account`가 `<AWS_ACCOUNT_ID>`이고 `Arn`이 자기 사용자 이름이면 성공이다.

> **`aws login`은 쓸 수 없다.** AWS CLI 2.32부터 생긴 `aws login`(콘솔 로그인으로 CLI 자격 증명 받기)은 `signin:AuthorizeOAuth2Access`·`signin:CreateOAuth2Token` 권한이 필요하다. 이 계정은 Innovation Sandbox 조직에 속해 있고, 조직 SCP가 `signin:*`을 명시적으로 거부한다. IAM 쪽에서 `SignInLocalDevelopmentAccess`를 붙여도 SCP가 우선하므로 풀리지 않는다. 액세스 키 방식만 쓴다.

> **`lorekeeper` 프로파일이 이미 있으면 먼저 확인한다.** `~/.aws/config`의 `[profile lorekeeper]`에 `sso_session`·`sso_account_id` 같은 `sso_*` 줄이 남아 있으면 CLI가 액세스 키 대신 SSO를 쓰려다 `Token has expired`로 실패한다. 그 줄을 지우고 `region`·`output`만 남긴다.

> **`--profile`을 항상 명시한다.** 다른 AWS 계정을 쓰고 있다면 `default` 프로파일이 엉뚱한 계정을 가리킬 수 있다. 실제로 한 번 겪었다. 매번 치기 번거로우면 셸에 `export AWS_PROFILE=lorekeeper`를 둔다.

---

## 3. kubeconfig 설정

```bash
aws eks update-kubeconfig \
  --name lore-sentry-k8s \
  --region ap-northeast-2 \
  --profile lorekeeper \
  --alias lore-sentry
```

`--alias`로 컨텍스트 이름을 짧게 만든다. 이게 없으면 컨텍스트 이름이 `arn:aws:eks:ap-northeast-2:<AWS_ACCOUNT_ID>:cluster/lore-sentry-k8s`가 되어 매번 치기 어렵다.

기본 네임스페이스를 `prod`로 둔다. 어차피 다른 네임스페이스는 보이지 않는다.

```bash
kubectl config use-context lore-sentry
kubectl config set-context --current --namespace=prod
```

### dev 클러스터

부하·가용성 테스트용 dev 클러스터 `lore-sentry-dev-k8s`(네임스페이스 `dev`)도 같은 자격 증명으로 접속한다. 컨텍스트를 하나
더 만드는 절차와 권한은 `DEV_CLUSTER.md` §10에 있다.

### kubeconfig에는 비밀이 없다

생성된 설정은 이런 모양이다.

```yaml
users:
- name: lore-sentry
  user:
    exec:
      command: aws
      args: [--region, ap-northeast-2, eks, get-token, --cluster-name, lore-sentry-k8s]
      env:
      - name: AWS_PROFILE
        value: lorekeeper
```

`kubectl`은 API를 호출할 때마다 `aws eks get-token`을 실행해 **15분짜리 임시 토큰**을 받는다. 즉 kubeconfig 자체는 비밀이 아니고, 진짜 자격 증명은 `~/.aws/credentials`에 있다. 지켜야 할 파일은 그쪽이다.

---

## 4. 접속 확인

```bash
kubectl auth whoami
```

```text
ATTRIBUTE   VALUE
Username    arn:aws:iam::<AWS_ACCOUNT_ID>:user/mem-jh
Groups      [lore-viewers system:authenticated]
```

**`Groups`에 `lore-viewers`가 있어야 한다.** 이 한 줄이 설정의 두 계층을 모두 검증한다.

| 증상 | 원인 |
|---|---|
| 명령 자체가 `Unauthorized` | AWS 인증은 됐지만 클러스터에 등록되지 않았다 (§8) |
| `Groups`에 `lore-viewers`가 없다 | 등록은 됐지만 그룹이 안 붙었다. 관리자 문의 |
| 정상 출력 | 설정 완료 |

이어서 실제 조회를 해본다.

```bash
kubectl get pods
```

```text
NAME                                  READY   STATUS    RESTARTS   AGE
ai-chat-api-...                       1/1     Running   0          11h
auth-valkey-...                       1/1     Running   0          11h
authentication-api-...                1/1     Running   0          11h
content-api-...                       1/1     Running   0          11h
gateway-api-...                       1/1     Running   0          11h
graph-rag-api-...                     1/1     Running   0          11h
```

자기 권한 전체를 보고 싶으면 다음을 쓴다.

```bash
kubectl auth can-i --list -n prod
```

---

## 5. 자주 쓰는 명령

```bash
# 워크로드 상태
kubectl get pods -o wide
kubectl get deploy,svc,ingress
kubectl describe pod <pod-name>

# 로그
kubectl logs deploy/gateway-api --tail=100
kubectl logs deploy/gateway-api -f
kubectl logs deploy/gateway-api --previous     # 재시작 직전 로그

# 이벤트 (Pending, ImagePullBackOff 등 원인 추적)
kubectl get events --sort-by=.lastTimestamp | tail -30

# 배포된 이미지 태그 확인
kubectl get deploy -o custom-columns=\
'NAME:.metadata.name,IMAGE:.spec.template.spec.containers[0].image'
```

API 동작 확인은 클러스터가 아니라 공개 엔드포인트로 한다.

```bash
curl -s https://api.loresentry.com/health
curl -s https://api.loresentry.com/health/db | jq
```

`/health`는 게이트웨이 자신만 보고, `/health/db`는 4개 서비스의 DB 연결을 집계한다. `/health`가 200인데 `/health/db`가 503인 것은 정상 동작이다 — liveness가 DB 장애로 재시작되지 않도록 분리해 두었다 (`INFRA_AND_CICD.md` §19-B).

### 데이터베이스에는 kubectl로 붙을 수 없다

RDS와 Neptune은 VPC 프라이빗 서브넷 전용이고, Kubernetes Service가 아니므로 `kubectl`로는 닿는 경로가 없다. DB 상태만 보려면 `/health/db`로 충분하고, 실제로 쿼리를 던져야 하면 Telepresence를 쓴다 (§6).

---

## 6. Telepresence — 클러스터 네트워크에 참여하기

`kubectl`로는 리소스를 **조회**할 수 있을 뿐, 클러스터 내부 네트워크에 패킷을 보낼 수는 없다. Telepresence는 노트북을 클러스터 네트워크에 참여시켜 그 벽을 없앤다.

| 이것이 필요하면 | Telepresence |
|---|---|
| Pod 상태·로그 보기 | 필요 없다. `kubectl`로 충분하다 |
| **RDS·Neptune에 직접 쿼리** | 필요하다 |
| 클러스터 내부 Service 호출 (`content-api:80` 등) | 필요하다 |
| 로컬에서 돌리는 서비스로 운영 트래픽 받기 | 필요하다 (intercept) |

### 동작 방식

```text
노트북  ──▶  traffic-manager Pod (ambassador ns)  ──▶  클러스터 / VPC
                      │
                      └─ 패킷이 여기서 나가므로 DB 입장에서는
                         기존 서비스와 똑같은 출처로 보인다
```

RDS의 보안 그룹은 클러스터 SG에서 오는 5432만 허용한다. Telepresence 트래픽은 클러스터 Pod를 거쳐 나가므로 **보안 그룹을 열지 않고도** 통과한다.

RDS와 Neptune은 Kubernetes Service가 아니라 VPC 리소스이므로, 그 서브넷(`10.20.3.0/24`, `10.20.4.0/24`)이 Traffic Manager 설정에 `alsoProxySubnets`로 등록되어 있다. 클라이언트가 연결하면 자동으로 받아간다.

### 연결

```bash
telepresence connect --context lore-sentry --namespace prod
```

처음 실행하면 로컬 DNS와 라우팅 테이블을 바꾸기 위해 **관리자 권한을 요구한다.** 정상이다.

**`--namespace prod`를 반드시 붙인다.** Traffic Manager는 `prod`만 관리하도록 설정되어 있고, 생략하면 Telepresence가 컨텍스트의 기본 네임스페이스로 붙으려 한다. 그게 `default`면 `namespace default is not managed`로 실패한다.

```bash
telepresence status
```

```text
OSS User Daemon: Running
OSS Root Daemon: Running
Traffic Manager: Connected
```

작업이 끝나면 반드시 해제한다. 연결된 동안에는 로컬 DNS가 클러스터를 향하므로 다른 작업에 영향을 줄 수 있다.

```bash
telepresence quit
```

### 데이터베이스 접속

팀원은 RDS **마스터 계정**으로 접속한다. 논리 DB 3개(`authentication`·`content`·`ai_chat`)에 모두 들어갈 수 있고, RDS에서 줄 수 있는 가장 높은 권한(`rds_superuser`)이다. 서비스 계정(`content_svc` 등)은 자기 DB에만 붙는 애플리케이션용이라 쓰지 않는다 (`INFRA_AND_CICD.md` §19-B).

마스터 비밀번호는 RDS가 Secrets Manager(`rds!db-…`)에 두고 관리한다. **7일마다 자동으로 바뀌므로** 받아 적어 두지 말고 접속할 때마다 꺼낸다. 꺼내려면 `lorekeeper-owner` 그룹이어야 한다 (부록). 콘솔에서는 RDS → `lore-sentry-postgres` → 구성 → 마스터 자격 증명의 Secrets Manager 링크 → "보안 암호 값 검색"이다.

psql이 없으면 설치한다. `libpq`는 keg-only라 PATH에 직접 넣는다.

```bash
brew install libpq
export PATH="$(brew --prefix libpq)/bin:$PATH"
```

엔드포인트와 비밀번호는 AWS CLI로 얻는다. 엔드포인트를 문서에 적어두지 않는 이유는 인스턴스를 다시 만들면 바뀌기 때문이다.

```bash
export AWS_PROFILE=lorekeeper AWS_REGION=ap-northeast-2

RDS=$(aws rds describe-db-instances --db-instance-identifier lore-sentry-postgres \
      --query 'DBInstances[0].Endpoint.Address' --output text)
SECRET=$(aws rds describe-db-instances --db-instance-identifier lore-sentry-postgres \
         --query 'DBInstances[0].MasterUserSecret.SecretArn' --output text)
CREDS=$(aws secretsmanager get-secret-value --secret-id "$SECRET" --query SecretString --output text)
export PGUSER=$(jq -r .username <<<"$CREDS") PGPASSWORD=$(jq -r .password <<<"$CREDS")

NEPTUNE=$(aws neptune describe-db-clusters --db-cluster-identifier lore-sentry-neptune \
          --query 'DBClusters[0].Endpoint' --output text)
```

Telepresence가 연결된 상태에서 접속한다.

```bash
psql "host=$RDS dbname=content sslmode=require"   # 논리 DB: authentication | content | ai_chat
curl https://$NEPTUNE:8182/status -k              # Neptune
```

TablePlus·DBeaver 같은 GUI도 연결된 동안에는 같은 값(호스트 `$RDS`, 포트 5432, SSL require, `$PGUSER`/`$PGPASSWORD`)으로 붙는다.

**운영 DB의 관리자 계정이다.** `UPDATE`·`DELETE`·`DROP`이 그대로 반영되고, 되돌릴 수단은 7일치 자동 백업뿐이다. 조회만 할 때도 트랜잭션으로 감싼다.

```sql
BEGIN;
-- 확인할 쿼리
ROLLBACK;
```

테이블 같은 객체를 새로 만들어야 하면 먼저 그 DB의 서비스 역할로 바꾼다. 마스터로 만든 객체는 마스터 소유가 되어 서비스가 읽지 못하고, 이후 Flyway 마이그레이션이 그 객체를 고치다 실패한다. 스키마 변경은 원래 각 서비스의 마이그레이션으로 하는 것이 원칙이다.

```sql
SET ROLE content_svc;   -- authentication_svc | content_svc | ai_chat_svc
-- CREATE ...
RESET ROLE;
```

### 클러스터 내부 Service 호출

연결 중에는 클러스터 DNS가 그대로 동작한다.

```bash
curl http://content-api.prod/health
curl http://gateway-api.prod/health/db
```

게이트웨이를 거치지 않고 개별 서비스를 직접 찔러볼 수 있어, 장애 구간을 좁힐 때 유용하다.

### intercept — 운영 트래픽을 로컬로 가져오기

로컬에서 수정 중인 서비스로 `prod` 트래픽을 돌린다.

```bash
telepresence intercept content-api --port 8080:80 --namespace prod
# 로컬 8080 에서 서비스를 띄워두면 클러스터 트래픽이 여기로 온다

telepresence leave content-api
```

**운영 환경에 직접 영향을 준다.** intercept가 걸린 동안 해당 서비스로 가는 실제 요청이 노트북으로 오고, 로컬 서비스가 죽어 있으면 그 경로는 장애가 난다. 쓰기 전에 팀에 알리고, 끝나면 즉시 `leave`한다.

워크로드의 환경 변수를 파일로 받을 수 있다.

```bash
telepresence intercept content-api --port 8080:80 --env-file ./content.env
```

이 파일에는 **Secret에서 주입된 DB 자격 증명이 평문으로 들어 있다.** 저장소에 커밋하지 않는다.

### 문제 해결

| 증상 | 원인과 조치 |
|---|---|
| `context was not found for specified context: lore-sentry` | kubeconfig에 그 이름의 컨텍스트가 없다. §3의 `--alias lore-sentry`를 실행했는지 확인한다. `kubectl config get-contexts`로 실제 이름을 본다 |
| `namespace default is not managed` | `--namespace prod`를 빠뜨렸다. Traffic Manager는 `prod`만 관리한다. 컨텍스트 기본값도 맞춰두면 편하다 — `kubectl config set-context lore-sentry --namespace=prod` |
| 버전 불일치로 연결 거부 | 클라이언트가 Traffic Manager(2.31.2)와 다른 마이너 버전이다. `telepresence version`으로 확인하고 올린다 (§1) |
| `connect` 후에도 DB에 안 닿는다 | `telepresence status`에서 Root Daemon이 Running인지 본다. 관리자 권한 승인을 놓쳤을 수 있다 |
| 사내 VPN과 충돌한다 | VPN 대역이 `10.20.0.0/16`과 겹치면 라우팅이 깨진다. VPN을 끄거나 `--proxy-via`를 쓴다 |
| `telepresence quit` 후 DNS가 이상하다 | `telepresence quit -s`로 root daemon까지 완전히 종료한다 |

---

## 7. 대시보드 — Argo CD · Grafana

### Argo CD

```text
https://argocd.loresentry.com
```

GitHub 계정으로 로그인한다 (`soma-lorekeeper` 조직 멤버만 통과). 로그인하면 **누구나 Argo CD admin**이다 — 조직에 팀이 없어 `argocd-rbac-cm`의 `policy.default: role:admin`으로 기본 역할을 준다 (`INFRA_AND_CICD.md` GitHub SSO 절).

> **주의:** Argo CD 권한은 위의 `kubectl` 읽기 전용 권한과 **별개**다. Argo CD admin은 클러스터의 무엇이든 바꿀 수 있고, 대시보드의 Sync·Rollback·Delete는 실제로 클러스터를 바꾼다. 조회 외에는 누르지 않는다.

### Grafana

```text
https://grafana.loresentry.com
```

Prometheus 메트릭과 Loki 로그를 본다. 스택은 `monitoring` 네임스페이스에 있다 (`loresentry-gitops` README의 Observability 절).

로그인은 **공용 admin 계정 하나**뿐이다. 가입과 익명 접근은 꺼져 있다. 계정은 `monitoring`의 `grafana-admin` Secret(Git 밖)에 있고 팀원은 `kubectl`로 이 Secret을 읽을 수 없으므로 관리자에게 받는다. admin이라 대시보드·데이터 소스·알림 규칙을 바꿀 수 있다.

---

## 8. 문제 해결

| 증상 | 원인과 조치 |
|---|---|
| `error: You must be logged in to the server (Unauthorized)` | AWS 신원은 확인됐으나 클러스터에 등록되지 않았다. 관리자에게 access entry 생성을 요청한다 (§부록) |
| `Error from server (Forbidden): ...` | 권한 밖의 동작이다. **정상이다.** §0 표를 확인한다 |
| `Unable to connect to the server: dial tcp ... i/o timeout` | 네트워크 문제. 사내망·VPN·방화벽을 확인한다 |
| `An error occurred (InvalidClientTokenId)` | 액세스 키 오타 또는 비활성화된 키. `aws configure --profile lorekeeper`를 다시 실행한다 |
| `Error when retrieving token from sso` | `lorekeeper` 프로파일에 SSO 설정이 남아 있다. `~/.aws/config`에서 `sso_*` 줄을 지운다 (§2) |
| `aws login`이 권한 오류로 실패한다 | **알려진 제약.** 조직 SCP가 `signin:*`을 거부한다 (§2). 관리자에게 액세스 키 발급을 요청한다 |
| `exec: "aws": executable file not found in $PATH` | `kubectl`이 `aws`를 찾지 못한다. AWS CLI 설치와 PATH를 확인한다 |
| `The config profile (lorekeeper) could not be found` | 프로파일 이름 불일치. `aws configure list-profiles`로 확인한다 |
| 엉뚱한 클러스터가 응답한다 | `kubectl config current-context`를 확인한다. 여러 클러스터가 섞여 있으면 `--context lore-sentry`를 명시한다 |
| `kubectl get kafkatopics`가 `Forbidden` | **알려진 제약.** Strimzi CRD가 기본 `view` 역할에 포함되지 않는다. 필요하면 관리자에게 요청한다 |

### 권한이 더 필요하면

지금 권한으로 못 하는 일이 생기면 직접 우회하지 말고 관리자에게 요청한다. 클러스터 권한은 GitOps 저장소에서 관리되므로 변경 이력이 남는다.

GitOps 저장소(`soma-lorekeeper/loresentry-gitops`, 공개 저장소)에는 조직 멤버 모두 **write 권한이 있다.** `main`에는 보호 규칙이 **없다** (2026-10-29 확인). 직접 push해도 막히지 않고 Argo CD가 곧바로 클러스터에 반영하므로, 브랜치를 만들어 PR로 올리는 것을 규칙으로 지킨다. 배포 Lambda가 `main`에 직접 커밋하므로 보호 규칙을 켜려면 그 경로를 예외로 둬야 한다 (`INFRA_AND_CICD.md` §12).

---

## 9. 액세스 키 관리 수칙

액세스 키는 **만료되지 않는다.** 명시적으로 삭제할 때까지 유효하므로 다음을 지킨다.

- `~/.aws/credentials`를 저장소에 커밋하지 않는다. `.gitignore`로 막히지 않는 위치다
- 키를 Slack·카카오톡·이메일 평문으로 전달하거나 재전송하지 않는다
- 노트북 분실·키 유출이 의심되면 **즉시** 관리자에게 알린다. 키 삭제가 유일한 차단 수단이다
- 프로젝트를 떠날 때 관리자에게 키 삭제를 요청한다

키에는 그 사용자의 IAM 권한이 그대로 실린다. 지금 팀원은 모두 `lorekeeper-owner`(`PowerUserAccess` + `IAMFullAccess`)라 **유출되면 계정 전체가 위험하다** — RDS·S3 삭제부터 새 IAM 사용자와 키 생성까지 된다 (부록).

---

## 10. Lens — GUI로 클러스터 보기

Lens는 노트북에서 돌아가는 **데스크톱 앱**이다. 클러스터에 아무것도 설치하지 않는다. §3에서 만든 `~/.kube/config`의 `lore-sentry` 컨텍스트를 그대로 읽어 API 서버를 호출하므로, **`kubectl`과 인증도 권한도 같다.** `kubectl`로 안 되는 일은 Lens로도 안 된다.

```text
Lens (노트북)
  └─ ~/.kube/config 의 lore-sentry 컨텍스트
       └─ aws eks get-token (lorekeeper 프로파일) → 15분짜리 토큰
            └─ EKS API 서버
```

### 설치

```bash
brew install --cask lens
```

처음 실행하면 Lens ID 로그인을 요구한다. 무료 Personal 플랜으로 가입하면 된다. 로그인 없이 쓰고 싶으면 오픈소스 포크인 Freelens를 쓴다. 사용법은 같다.

```bash
brew install --cask freelens
```

### 연결

1. 왼쪽 클러스터 목록에서 `lore-sentry`를 연다. kubeconfig에 다른 컨텍스트가 있으면 함께 보이고, 같은 클러스터를 가리키는 `arn:aws:eks:...:cluster/lore-sentry-k8s` 항목도 보일 수 있다. `lore-sentry`만 핀 고정해 두면 헷갈리지 않는다
2. 클러스터 설정(우클릭 → Settings) → **Namespaces**의 Accessible Namespaces에 `prod`를 넣는다. 이걸 안 하면 Lens가 모든 네임스페이스를 조회하려다 `Forbidden`을 계속 띄운다
3. 상단 네임스페이스 필터를 `prod`로 둔다

### Lens에서 되는 것과 안 되는 것

§0과 같다. Lens에는 수정 버튼이 모두 보이지만 누르면 `Forbidden`이 난다. **정상이다.**

| 되는 것 | 안 되는 것 |
|---|---|
| Workloads (Pod·Deployment 등) 목록·상세 | Secrets 메뉴 |
| Pod 로그 보기 | Pod Shell (`exec`) |
| Events·ConfigMap·Service·Ingress 조회 | Edit·Delete·Scale·Restart |
| Pod 목록의 CPU·메모리 현재값 (metrics-server) | Nodes 화면, `prod` 외 네임스페이스 |
| | Port Forward |

**시계열 CPU·메모리 그래프는 나오지 않는다.** Lens 그래프는 Prometheus가 필요한데, Prometheus는 `monitoring` 네임스페이스에 있고 `lore-viewers`는 그 네임스페이스를 읽을 수 없다. 시계열은 Grafana(§7)에서 본다. Lens가 metrics 설치를 제안해도 누르지 않는다 — 클러스터에 리소스를 만드는 동작이라 권한이 없고, 필요하면 GitOps 저장소로 추가해야 한다 (§8).

### 문제 해결

| 증상 | 원인과 조치 |
|---|---|
| 앱 아이콘을 눌러도 창이 안 뜬다 | 처음 실행 때 macOS 확인 창이 뒤에 숨어 있을 수 있다. Finder의 응용 프로그램에서 Lens를 우클릭 → 열기로 실행한다 |
| 클러스터 연결 시 `aws: executable file not found` | Dock에서 실행한 앱이 셸 PATH를 못 읽었다. 터미널에서 `open -a Lens`로 실행하거나, `which aws` 경로가 `/usr/local/bin`·`/opt/homebrew/bin`에 있는지 확인한다 |
| `Unauthorized` 또는 토큰 오류 | Lens 문제가 아니다. 터미널에서 `kubectl --context lore-sentry auth whoami`가 되는지 먼저 본다 (§4) |
| 화면마다 `Forbidden` | Accessible Namespaces에 `prod`를 넣지 않았거나 권한 밖 메뉴를 열었다. 위 표를 확인한다 |
| 연결은 되는데 Workloads 등 사이드바 메뉴가 없다 | RBAC 그룹 없이 EKS access policy만으로 권한을 받은 신원이다 (주로 관리자). 아래 참고 |

### 관리자 계정에서 메뉴가 안 보일 때

Lens는 사이드바를 그리기 전에 `SelfSubjectRulesReview`로 권한 전체를 조회하고, 결과에 없는 메뉴는 숨긴다. 그런데 `AmazonEKSClusterAdminPolicy` 같은 **EKS access policy는 RBAC가 아니라 webhook authorizer가 판정**하므로 이 조회에 나타나지 않는다. 실제로는 관리자라 모든 요청이 통과하는데도 Lens에는 권한이 없는 것처럼 보인다.

```bash
kubectl auth can-i list pods -n prod    # yes — 개별 질의는 통과
kubectl auth can-i --list -n prod       # 리소스 권한 없음
# Warning: the list may be incomplete: webhook authorizer does not support user rule resolution
```

팀원은 `lore-viewers` RoleBinding(RBAC)으로 권한을 받으므로 이 문제가 없다. 관리자는 자기 access entry에 `lore-viewers` 그룹을 붙이면 기존 RoleBinding을 통해 `prod` 메뉴가 나타난다. access policy는 그대로이므로 관리자 권한은 유지된다.

```bash
aws eks update-access-entry --cluster-name lore-sentry-k8s \
  --principal-arn <관리자 신원 ARN> \
  --kubernetes-groups lore-viewers
kubectl auth whoami    # Groups 에 lore-viewers 가 보이면 적용된 것. Lens 에서 클러스터를 다시 연결한다
```

Nodes·`kube-system` 같은 클러스터 범위까지 Lens로 보려면 별도 그룹을 `cluster-admin`에 묶는 ClusterRoleBinding을 GitOps 저장소에 추가해야 한다.

> **관리자 권한으로 Lens를 쓸 때:** 관리자 자격 증명으로 붙으면 Lens 버튼이 실제로 동작한다. Delete·Edit·Scale은 운영 클러스터를 바로 바꾸고, Argo CD가 GitOps 기준으로 되돌리면서 상태가 흔들린다. 조회 용도로만 쓴다.

---

## 부록 — 관리자용

팀원 추가 시 수행하는 절차다. access entry는 GitOps 저장소가 아니라 AWS에 있으므로, 이 절차 자체가 재현 수단이다 (`INFRA_AND_CICD.md` §19-A).

**IAM 그룹.** `lorekeeper-member`는 `ReadOnlyAccess`, `lorekeeper-owner`는 `PowerUserAccess` + `IAMFullAccess`다. 팀원도 S3·EC2·RDS·Lambda 같은 리소스를 만들어야 해서 mem-jh·mem-jh-2·mem-sj를 `lorekeeper-owner`에 넣었다(두 그룹에 모두 속한다). IAM 역할 생성·`iam:PassRole`이 필요한 작업(Lambda 실행 역할, 인스턴스 프로파일, Pod Identity)도 팀원이 직접 하도록 `lorekeeper-owner`에 `IAMFullAccess`를 더 붙였다. 그래서 이 그룹은 사실상 관리자 권한이다(IAM으로 자기 권한을 넓힐 수 있다). 되돌릴 때는 `aws iam detach-group-policy --group-name lorekeeper-owner --policy-arn arn:aws:iam::aws:policy/IAMFullAccess`. 리소스를 만들 팀원을 새로 추가할 때는 아래 1번 뒤에 `aws iam add-user-to-group --user-name $USER --group-name lorekeeper-owner`를 더한다. 클러스터 권한은 IAM 그룹과 무관하게 access entry의 `lore-viewers`(읽기 전용)로 정해진다. `lorekeeper-owner`는 Secrets Manager를 읽을 수 있어 DB 마스터 비밀번호를 각자 꺼내 쓴다 (§6). 그래서 팀원에게 DB 관리자 권한을 따로 줄 필요가 없고, 비밀번호를 메신저로 전달하지 않는다. 2026-10-29에 mem-jh·mem-jh-2·mem-sj의 `secretsmanager:GetSecretValue`(마스터 비밀)와 `kms:Decrypt`(`aws/secretsmanager`)가 `allowed`임을 시뮬레이터로 확인했다.

**지금 팀원별 권한 (2026-10-29 확인).**

| 신원 | IAM 그룹 | 액세스 키 | 콘솔 암호 | 클러스터 (access entry) |
|---|---|---|---|---|
| mem-jh | member, owner | **없음** | 있음 | `lore-viewers` |
| mem-jh-2 | member, owner | 1개 (2026-09-19 발급) | 있음 | `lore-viewers` |
| mem-sj | member, owner | 1개 (2026-09-19 발급) | 있음 (로그인 기록 없음) | `lore-viewers` |
| mem-sh | owner | 없음 | 있음 | **없음** |
| SSO 역할 `AWSReservedSSO_myisb_IsbUsersPS_…` (관리자) | — | SSO | — | `AmazonEKSClusterAdminPolicy` + `lore-viewers` |

- 액세스 키가 없으면 CLI를 쓸 수 없어 §2~§6(`kubectl`·Telepresence·DB 접속)을 하지 못한다. mem-jh는 아래 3번으로 키를 발급해야 한다. mem-sh는 access entry도 없어 키가 있어도 `kubectl`이 `Unauthorized`다 (2번).
- `lorekeeper-member`의 세 사람은 모두 `lorekeeper-owner`에도 있어서, 지금 `ReadOnlyAccess`는 실질적인 제한이 아니다.
- owner 그룹의 실제 범위를 시뮬레이터로 보면 `eks:AssociateAccessPolicy`·`eks:UpdateAccessEntry`·`iam:AttachUserPolicy`·`iam:CreateAccessKey`·`rds:DeleteDBInstance`·`s3:DeleteBucket`·`ec2:TerminateInstances`가 모두 `allowed`다. 조직 SCP가 막는 것은 `organizations:LeaveOrganization`·`signin:*`·Cost Explorer 조회 같은 일부뿐이다.
- MFA 장치는 어느 사용자에게도 없다. 사용자마다 `IAMUserChangePassword`(콘솔 암호 변경)가 직접 붙어 있다.
- GitHub `soma-lorekeeper` 조직 멤버는 5명(Gomdadi·Heimdall-ROI·ju1115·lifeandsong·ohsuhyeon0119, 마지막이 조직 owner)이다. 모두 gitops 저장소 write이고 Argo CD admin이다 (§7, §8).

다시 확인할 때는 다음을 쓴다. 값을 꺼내지 않는 조회만 한다.

```bash
for U in $(aws iam list-users --query 'Users[].UserName' --output text); do
  echo "$U: $(aws iam list-groups-for-user --user-name "$U" --query 'Groups[].GroupName' --output text)" \
       "keys=$(aws iam list-access-keys --user-name "$U" --query 'length(AccessKeyMetadata)')"
done
aws eks list-access-entries --cluster-name lore-sentry-k8s
gh api orgs/soma-lorekeeper/members --jq '.[].login'
gh api repos/soma-lorekeeper/loresentry-gitops/branches/main/protection   # 404 면 보호 규칙 없음
```

**팀원 추가**

```bash
export AWS_PROFILE=lorekeeper AWS_REGION=ap-northeast-2
USER=mem-xx

# 1. IAM 사용자 생성 후 읽기 전용 그룹에 편입
aws iam create-user --user-name $USER
aws iam add-user-to-group --user-name $USER --group-name lorekeeper-member

# 2. 클러스터 접근 등록 — 그룹 문자열이 RoleBinding 과 일치해야 한다
aws eks create-access-entry \
  --cluster-name lore-sentry-k8s \
  --principal-arn arn:aws:iam::<AWS_ACCOUNT_ID>:user/$USER \
  --kubernetes-groups lore-viewers \
  --type STANDARD

# 3. 액세스 키 발급 — SecretAccessKey 는 이때 한 번만 출력된다
aws iam create-access-key --user-name $USER

# 4. (선택) 콘솔 암호 — 웹 콘솔을 볼 때만 필요하다. CLI에는 쓰이지 않는다
aws iam create-login-profile --user-name $USER --password '<임시 암호>' --password-reset-required
```

3번을 건너뛰면 팀원은 CLI에 쓸 자격 증명이 없다. 콘솔 암호만 발급한 상태가 이 경우다. `aws login`은 SCP에 막혀 대안이 되지 못한다 (§2). 조직 SCP가 막는지는 시뮬레이터의 `AllowedByOrganizations`로 확인한다.

```bash
aws iam simulate-principal-policy \
  --policy-source-arn arn:aws:iam::<AWS_ACCOUNT_ID>:user/$USER \
  --action-names signin:CreateOAuth2Token \
  --query 'EvaluationResults[].[EvalDecision,OrganizationsDecisionDetail.AllowedByOrganizations]'
# ["explicitDeny", false] — SCP 거부. IAM 정책으로는 풀 수 없다
```

`associate-access-policy`는 쓰지 않는다. AWS 관리형 정책을 붙이면 권한이 AWS 쪽에 갇혀 Git으로 관리할 수 없다. 권한은 `workload/overlays/prod/rbac.yaml`의 RoleBinding이 결정한다. 예외는 관리자 SSO 역할의 `AmazonEKSClusterAdminPolicy` 하나다. owner 그룹 팀원도 이 명령을 실행할 수 있는 IAM 권한이 있으므로, 이 규칙은 권한이 아니라 약속으로 지켜진다.

**팀원 입장에서 검증** — 키를 전달하기 전에 관리자 노트북에서 팀원 설정을 그대로 재현한다. `AWS_CONFIG_FILE`·`AWS_SHARED_CREDENTIALS_FILE`·`KUBECONFIG`를 임시 디렉터리로 돌리므로 관리자 자신의 `~/.aws`와 `~/.kube`는 건드리지 않는다. 이 셸을 닫으면 원상태다.

```bash
T=$(mktemp -d)
export AWS_CONFIG_FILE=$T/config AWS_SHARED_CREDENTIALS_FILE=$T/credentials KUBECONFIG=$T/kubeconfig
unset AWS_PROFILE AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN

# 이후 §2~§4 를 팀원과 똑같이 실행한다
aws configure --profile lorekeeper
# ...

rm -rf $T      # 끝나면 키가 담긴 임시 파일을 지운다
```

**제거**

```bash
aws eks delete-access-entry --cluster-name lore-sentry-k8s \
  --principal-arn arn:aws:iam::<AWS_ACCOUNT_ID>:user/$USER
aws iam list-access-keys --user-name $USER        # 키 ID 확인 후
aws iam delete-access-key --user-name $USER --access-key-id AKIA...
```

**권한 변경 전 검증** — 팀원에게 적용하기 전에 같은 권한을 흉내내 확인한다.

```bash
kubectl auth can-i --list -n prod --as=probe --as-group=lore-viewers
kubectl get secrets -n prod --as=probe --as-group=lore-viewers   # Forbidden 이어야 정상
```
