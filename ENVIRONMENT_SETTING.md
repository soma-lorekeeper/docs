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
| Pod·Deployment·Service·Ingress 조회 | **Secret 조회** (DB 비밀번호 등) |
| ConfigMap·Event 조회 | `kubectl exec` (컨테이너 진입) |
| `kubectl logs` | `kubectl port-forward` |
| `kubectl describe` | 생성·수정·삭제 일체 |
| | `prod` 외 네임스페이스 (`kube-system`, `argocd`, `default`) |
| | 노드·네임스페이스 등 클러스터 범위 리소스 |

AWS 쪽도 같은 원칙이다. `ReadOnlyAccess`가 부여되어 조회는 되지만 생성·삭제는 되지 않고, Secrets Manager 값 읽기와 KMS 복호화도 차단되어 있다.

`Forbidden` 오류를 보면 설정이 잘못된 것이 아니라 **의도된 경계에 닿은 것**이다. 권한이 더 필요하면 §7을 본다.

---

## 1. 사전 준비

| 도구 | 최소 버전 | 확인 |
|---|---|---|
| AWS CLI | **v2** | `aws --version` |
| kubectl | 1.35 ~ 1.37 | `kubectl version --client` |

클러스터가 Kubernetes v1.36이다. kubectl은 클러스터와 ±1 마이너 버전까지만 보증되므로 범위를 벗어나면 예상 못 한 오류가 난다.

```bash
# macOS
brew install awscli kubectl

# Linux (AWS CLI v2)
curl -s "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o awscliv2.zip
unzip -q awscliv2.zip && sudo ./aws/install
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
| 명령 자체가 `Unauthorized` | AWS 인증은 됐지만 클러스터에 등록되지 않았다 (§6) |
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

### 데이터베이스에는 직접 붙을 수 없다

RDS와 Neptune은 VPC 프라이빗 서브넷 전용이고, 클러스터 밖에서는 접근 경로가 없다. `port-forward`도 권한에서 제외되어 있으므로 우회도 되지 않는다. DB 상태는 `/health/db`로 확인한다.

---

## 6. Argo CD 대시보드

```text
https://argocd.loresentry.com
```

GitHub 계정으로 로그인한다 (`soma-lorekeeper` 조직 멤버만 통과).

> **주의:** Argo CD 계정 권한은 위의 `kubectl` 읽기 전용 권한과 **별개**다. 대시보드에서 Sync·Delete 같은 동작은 실제로 클러스터를 바꾼다. 조회 외에는 누르지 않는다.

---

## 7. 문제 해결

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

## 8. 액세스 키 관리 수칙

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
