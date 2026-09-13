# 로컬 개발 환경 온보딩 — AWS · EKS 접근 설정

> 최신화: 2026-09-13
> 대상: Lore Sentry 팀원
> 대상 환경: AWS `ap-northeast-2` / EKS `lore-sentry-k8s` / namespace `prod`

이 문서는 팀원이 자기 노트북에서 운영 클러스터를 **조회**할 수 있게 만드는 절차다. 배포는 다루지 않는다 — 배포는 Argo CD가 GitOps 저장소를 보고 수행하며, 사람이 `kubectl`로 클러스터를 바꾸지 않는다 (`INFRA_AND_CICD.md` §21).

---

## 0. 먼저 알아둘 것 — 조회만 가능하다

**부여되는 권한은 `prod` 네임스페이스에 대한 읽기 전용이다.** 클러스터를 변경하는 모든 동작은 막혀 있고, 막히는 것이 정상이다.

| 되는 것 | 안 되는 것 |
|---|---|
| Pod·Deployment·Service·Ingress 조회 | **Secret 조회** (`kubectl get secret`) |
| ConfigMap·Event 조회 | `kubectl exec` (컨테이너 진입) |
| `kubectl logs` | 생성·수정·삭제 일체 |
| `kubectl describe` | `prod` 외 네임스페이스 (`kube-system`, `argocd`, `default`) |
| `kubectl port-forward` (§6) | 노드·네임스페이스 등 클러스터 범위 리소스 |
| Telepresence 연결·intercept (§6) | |

AWS 쪽도 같은 원칙이다. `ReadOnlyAccess`가 부여되어 조회는 되지만 생성·삭제는 되지 않고, Secrets Manager 값 읽기와 KMS 복호화도 차단되어 있다.

`Forbidden` 오류를 보면 설정이 잘못된 것이 아니라 **의도된 경계에 닿은 것**이다. 권한이 더 필요하면 §8을 본다.

> **Secret은 API로 읽을 수 없지만 완전히 가려진 것은 아니다.** Telepresence intercept는 대상 워크로드의 환경 변수를 가져오고, 거기에는 Secret에서 주입된 DB 자격 증명이 들어 있다 (§6). 팀이 애플리케이션 DB 계정을 공유하기로 했으므로 의도된 결과다. 그만큼 이 자격 증명은 **운영 데이터에 쓰기가 가능**하다는 점을 기억한다.

---

## 1. 사전 준비

| 도구 | 버전 | 확인 |
|---|---|---|
| AWS CLI | **v2** | `aws --version` |
| kubectl | 1.35 ~ 1.37 | `kubectl version --client` |
| Telepresence (§6에서만 필요) | **2.31.x** | `telepresence version` |

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

관리자에게 **액세스 키 2종**을 받는다.

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

엔드포인트는 AWS CLI로 얻는다. 문서에 적어두지 않는 이유는 인스턴스를 다시 만들면 바뀌기 때문이다.

```bash
export AWS_PROFILE=lorekeeper AWS_REGION=ap-northeast-2

RDS=$(aws rds describe-db-instances --db-instance-identifier lore-sentry-postgres \
      --query 'DBInstances[0].Endpoint.Address' --output text)

NEPTUNE=$(aws neptune describe-db-clusters --db-cluster-identifier lore-sentry-neptune \
          --query 'DBClusters[0].Endpoint' --output text)
```

연결된 상태에서 접속한다.

```bash
psql -h $RDS -U <user> -d content        # 논리 DB: authentication | content | ai_chat
curl https://$NEPTUNE:8182/status -k     # Neptune
```

논리 DB는 3개이고 **서비스마다 자기 DB에만 접근할 수 있는 역할이 따로 있다.** 남의 DB에 붙으면 `permission denied for database`가 난다. 의도된 격리다 (`INFRA_AND_CICD.md` §19-B).

자격 증명은 관리자에게 받는다. 이 계정들은 애플리케이션이 쓰는 것과 같으므로 **운영 데이터에 쓰기가 가능하다.** `UPDATE`·`DELETE`를 실행하면 그대로 반영된다. 조회 목적이라면 트랜잭션으로 감싸는 편이 안전하다.

```sql
BEGIN;
-- 확인할 쿼리
ROLLBACK;
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

## 7. Argo CD 대시보드

```text
https://argocd.loresentry.com
```

GitHub 계정으로 로그인한다 (`soma-lorekeeper` 조직 멤버만 통과).

> **주의:** Argo CD 계정 권한은 위의 `kubectl` 읽기 전용 권한과 **별개**다. 대시보드에서 Sync·Delete 같은 동작은 실제로 클러스터를 바꾼다. 조회 외에는 누르지 않는다.

---

## 8. 문제 해결

| 증상 | 원인과 조치 |
|---|---|
| `error: You must be logged in to the server (Unauthorized)` | AWS 신원은 확인됐으나 클러스터에 등록되지 않았다. 관리자에게 access entry 생성을 요청한다 (§부록) |
| `Error from server (Forbidden): ...` | 권한 밖의 동작이다. **정상이다.** §0 표를 확인한다 |
| `Unable to connect to the server: dial tcp ... i/o timeout` | 네트워크 문제. 사내망·VPN·방화벽을 확인한다 |
| `An error occurred (InvalidClientTokenId)` | 액세스 키 오타 또는 비활성화된 키. `aws configure --profile lorekeeper`를 다시 실행한다 |
| `exec: "aws": executable file not found in $PATH` | `kubectl`이 `aws`를 찾지 못한다. AWS CLI 설치와 PATH를 확인한다 |
| `The config profile (lorekeeper) could not be found` | 프로파일 이름 불일치. `aws configure list-profiles`로 확인한다 |
| 엉뚱한 클러스터가 응답한다 | `kubectl config current-context`를 확인한다. 여러 클러스터가 섞여 있으면 `--context lore-sentry`를 명시한다 |
| `kubectl get kafkatopics`가 `Forbidden` | **알려진 제약.** Strimzi CRD가 기본 `view` 역할에 포함되지 않는다. 필요하면 관리자에게 요청한다 |

### 권한이 더 필요하면

지금 권한으로 못 하는 일이 생기면 직접 우회하지 말고 관리자에게 요청한다. 클러스터 권한은 GitOps 저장소에서 관리되므로 변경 이력이 남는다.

GitOps 저장소(`soma-lorekeeper/loresentry-gitops`)에는 **write 권한이 있다.** 다만 `main`은 보호되어 있으므로 브랜치를 만들어 PR로 올린다. 머지되면 Argo CD가 자동으로 클러스터에 반영한다.

---

## 9. 액세스 키 관리 수칙

액세스 키는 **만료되지 않는다.** 명시적으로 삭제할 때까지 유효하므로 다음을 지킨다.

- `~/.aws/credentials`를 저장소에 커밋하지 않는다. `.gitignore`로 막히지 않는 위치다
- 키를 Slack·카카오톡·이메일 평문으로 전달하거나 재전송하지 않는다
- 노트북 분실·키 유출이 의심되면 **즉시** 관리자에게 알린다. 키 삭제가 유일한 차단 수단이다
- 프로젝트를 떠날 때 관리자에게 키 삭제를 요청한다

키 자체에는 `ReadOnlyAccess`만 있으므로 유출되어도 리소스가 파괴되지는 않는다. 다만 인프라 구성 정보 전체가 노출된다.

---

## 부록 — 관리자용

팀원 추가 시 수행하는 절차다. access entry는 GitOps 저장소가 아니라 AWS에 있으므로, 이 절차 자체가 재현 수단이다 (`INFRA_AND_CICD.md` §19-A).

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
```

`associate-access-policy`는 쓰지 않는다. AWS 관리형 정책을 붙이면 권한이 AWS 쪽에 갇혀 Git으로 관리할 수 없다. 권한은 `workload/overlays/prod/rbac.yaml`의 RoleBinding이 결정한다.

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
