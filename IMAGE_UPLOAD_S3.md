# 이미지 업로드 — S3 Presigned URL + CloudFront

> 최신화: 2026-09-18  
> 대상: 사용자 이미지(문서 삽화, 표지 등)의 저장·조회 인프라  
> 상태: **AWS 리소스와 클러스터 배선은 구축 완료. content에는 S3 스토리지 서비스 계층까지만 있고, 공개 엔드포인트·DB 기록·프론트엔드는 도메인 개발 때 통합한다.**

## 0. 한눈에 보는 현재 상태

| 항목 | 상태 |
|---|---|
| S3 `loresentry-media-prod-<AWS_ACCOUNT_ID>` (퍼블릭 차단, SSE-S3, `PUT` 전용 CORS, 라이프사이클) | **[구축 완료/확인]** |
| IAM `lore-sentry-content-role` + `lore-sentry-content-media-policy` | **[구축 완료/확인]** |
| Pod Identity association `lore-sentry-k8s / prod / content-api` | **[구축 완료/확인]** |
| CloudFront `<CF_MEDIA_ID>` (OAC, alias `media.loresentry.com`, Deployed) | **[구축 완료/확인]** |
| S3 버킷 정책 — 위 배포만 `GetObject` | **[구축 완료/확인]** |
| Cloudflare `media` CNAME → `<CF_MEDIA_DOMAIN>.cloudfront.net`, 프록시 끔 | **[구축 완료/확인]** |
| GitOps: `media` ConfigMap, `content-api` ServiceAccount, `MEDIA_*` env | 매니페스트 완료, PR 대기 (`loresentry-gitops#3`) |
| content `MediaStorageService` (presign · HeadObject 검증 · 삭제 · 공개 URL) | 코드 완료, 테스트 통과, PR 대기 (`loresentry-content#1`) |
| content 공개 엔드포인트, `image` 테이블 | **[미구현]** 도메인 개발 때 §4의 제안 계약으로 통합 |
| gateway 릴레이 | **[미구현]** 엔드포인트와 함께 |
| 프론트엔드 업로드 UI | **[미구현]** |

---

## 1. 방식 — 브라우저가 S3에 직접 올린다

핵심은 **Presigned URL**이다. content 서비스는 "이 키에, 이 타입으로, 이 크기로, 5분 안에"라는 서명이 붙은 S3 URL만 만들어 준다. 이미지 바이트는 gateway·content pod·ALB를 지나지 않는다.

```text
[업로드]
browser ──"png 1234바이트 올릴게"──> gateway ──> content.MediaStorageService.createImageUploadTicket
browser <── { uploadUrl, method: PUT, headers, expiresAt, publicUrl, key } ─────┘
browser ──PUT uploadUrl (Content-Type, Content-Length 그대로) + 파일 본체──> S3
browser ──"다 올렸어"──> gateway ──> content.MediaStorageService.verifyUploaded ──HeadObject──> S3

[조회]
<img src="https://media.loresentry.com/projects/{projectId}/images/{uuid}.png">
CloudFront <CF_MEDIA_ID> ──OAC(SigV4)──> S3 (퍼블릭 접근 전면 차단)
```

---

## 2. 왜 이 방식인가

| 후보 | 왜 안 되는가 |
|---|---|
| gateway → content → S3 프록시 업로드 | gateway는 `RestClient` read-timeout 10초로 업스트림을 릴레이한다. 멀티파트를 두 홉으로 옮기면 대역폭이 두 배, pod 메모리 점유, 타임아웃 상향이 따라온다. AI 스트리밍 때문에 이미 올려야 하는 타임아웃(§15 체크리스트)에 업로드까지 얹을 이유가 없다 |
| Access Key를 Secret에 넣고 SDK 사용 | `auth-valkey`·`<service>-db` Secret이 Git 밖에 있는 것이 이미 재현성 구멍(§19-A)이다. 키를 하나 더 만들면 그 빚이 는다. Pod Identity면 비밀 값 자체가 없다 |
| presign 전용 Lambda + API Gateway | GitOps 밖 구성 요소가 하나 더 생긴다. presign은 "이 사용자가 이 프로젝트에 올려도 되는가"라는 content의 인가 질문과 붙어 있어야 한다 |
| 프론트엔드 버킷 공유 | frontend CI가 `aws s3 sync out/ --delete`와 `create-invalidation --paths "/*"`를 배포마다 돌린다. 같은 버킷이면 이미지가 지워지고, 같은 배포면 캐시가 매번 날아간다 |

**presign 로직이 content에 있는 이유.** content README가 "프로젝트 인가 질문은 여기서 답한다"고 못 박고 있다. 이미지는 프로젝트에 속하므로 발급 권한 판단도 content 몫이다. gateway는 BFF라 AWS 자격 증명을 가질 이유가 없다.

---

## 3. 구성 요소

### AWS — **[구축 완료/확인]**

| 리소스 | 값 | 비고 |
|---|---|---|
| S3 | `loresentry-media-prod-<AWS_ACCOUNT_ID>` (`ap-northeast-2`) | Block Public Access 4항목 전부 켬, SSE-S3, 버저닝 없음 |
| S3 CORS | `https://loresentry.com`, `http://localhost:*`, `http://127.0.0.1:*`에 `PUT`만, `ExposeHeaders: ETag` | §6 참고 |
| S3 라이프사이클 | 미완료 멀티파트 1일 후 abort | |
| S3 버킷 정책 | `cloudfront.amazonaws.com`이 `s3:GetObject`, `AWS:SourceArn` = `<CF_MEDIA_ID>` 배포 ARN | 다른 배포·다른 주체는 읽을 수 없다 |
| IAM 정책 | `lore-sentry-content-media-policy` — `projects/*`에 `PutObject` `GetObject` `DeleteObject` | 버킷 전체가 아니라 접두사 하나 |
| IAM 역할 | `lore-sentry-content-role`, 신뢰 주체 `pods.eks.amazonaws.com` (`AssumeRole` + `TagSession`) | |
| Pod Identity association | `lore-sentry-k8s` / `prod` / `content-api` → 위 역할 | EKS API 객체. Git에 둘 수 없다 (§19-A) |
| CloudFront OAC | `loresentry-media-oac` (S3, SigV4, always) | |
| CloudFront | `<CF_MEDIA_ID>` / `<CF_MEDIA_DOMAIN>.cloudfront.net`, alias `media.loresentry.com`, `Deployed` | `CachingOptimized`, GET/HEAD, http2and3, 함수 없음 |
| ACM | `loresentry.com` + `*.loresentry.com`, **`us-east-1`** | 프론트엔드용으로 이미 있던 인증서 (§17) |
| Cloudflare | `media` CNAME → `<CF_MEDIA_DOMAIN>.cloudfront.net`, **프록시 끔** | 다른 호스트와 같은 DNS 전용 |

정책 문서와 생성 스크립트는 `loresentry-content/docs/aws/`에 있다. 스크립트는 멱등이라 재실행해도 안전하고, 클러스터를 다시 만들 때 Pod Identity association을 복원하는 수단이기도 하다.

### GitOps (`loresentry-gitops`, PR #3)

```text
workload/base/media/configmap.yaml            bucket, region, public-base-url
workload/base/content/serviceaccount.yaml     content-api
workload/base/content/deployment.yaml         serviceAccountName + MEDIA_BUCKET / MEDIA_REGION / MEDIA_PUBLIC_BASE_URL
```

`postgres`·`neptune` ConfigMap과 같은 원칙이다. 비밀이 아닌 값은 Git에, 비밀은 Git 밖에. 여기서는 비밀이 아예 없다.

### content (`loresentry-content`, PR #1) — 서비스 계층만

| 파일 | 역할 |
|---|---|
| `media/MediaProperties` | `media.*` 설정 |
| `media/MediaConfig` | `S3Client`·`S3Presigner` 빈. 자격 증명 공급자는 SDK 기본 체인 |
| `media/MediaStorageService` | `createImageUploadTicket` · `verifyUploaded` · `delete` · `publicUrl` |
| `media/UploadTicket`, `media/StoredObject` | 반환 값 |
| `media/InvalidUploadRequestException`, `media/ObjectNotUploadedException` | 호출자가 400·409로 옮길 예외 |

의존성은 AWS SDK v2 (`software.amazon.awssdk:s3`, BOM 2.46.7)를 직접 쓴다. awspring(Spring Cloud AWS)의 Boot 4.1 지원 여부를 따지지 않아도 되게 하기 위해서다.

**없는 것:** 컨트롤러, `image` 테이블, 마이그레이션 도구. 프로젝트·파일 도메인을 만들 때 §4를 참고해 붙인다.

---

## 4. 서비스 사용법과 통합 시 제안 계약

### 4.1 Java

```java
UploadTicket ticket = mediaStorageService.createImageUploadTicket(projectId, "image/png", 1234L);
// ticket.key()        projects/{projectId}/images/{uuid}.png
// ticket.uploadUrl()  https://loresentry-media-prod-….s3.ap-northeast-2.amazonaws.com/…?X-Amz-Expires=300&…
// ticket.method()     PUT
// ticket.headers()    { Content-Type: image/png, Content-Length: 1234 }   ← 브라우저가 그대로 보내야 한다
// ticket.expiresAt()  now + 5m
// ticket.publicUrl()  https://media.loresentry.com/projects/{projectId}/images/{uuid}.png

StoredObject stored = mediaStorageService.verifyUploaded(ticket.key(), 1234L);  // 없거나 크기 다르면 ObjectNotUploadedException
mediaStorageService.delete(ticket.key());
```

| 규칙 | 값 |
|---|---|
| 허용 타입 | `image/png` `image/jpeg` `image/webp` `image/gif` (`media.allowed-content-types`) |
| 최대 크기 | 10 MiB (`media.max-size-bytes`) |
| URL 수명 | 5분 (`media.upload-url-ttl`) |
| 키 | `projects/{projectId}/images/{uuid}.{ext}` — 확장자는 **contentType에서**, 파일명은 믿지 않는다 |

`Content-Type`·`Content-Length`가 서명에 들어 있으므로 다른 타입이나 더 큰 파일은 S3가 `403 SignatureDoesNotMatch`로 거부한다.

### 4.2 통합 시 만들 엔드포인트 — 제안

도메인 API를 붙일 때 기준으로 삼을 계약이다. 경로는 PROJECT_CONTEXT §5.3의 도메인 경로 규칙(`/projects/{projectId}/…`)을 따르고, gateway는 상태 코드와 본문을 그대로 릴레이한다.

```text
POST /projects/{projectId}/images
  { "fileName": "cover.png", "contentType": "image/png", "sizeBytes": 1234 }
  → 201 { imageId, key, uploadUrl, method, headers, expiresAt, publicUrl }
     content: 인가 확인 → image 행 PENDING INSERT → createImageUploadTicket

POST /projects/{projectId}/images/{imageId}/complete
  → 200 { imageId, status: COMMITTED, publicUrl, … }
     content: verifyUploaded(key, size) → COMMITTED. 이미 COMMITTED면 멱등

GET  /projects/{projectId}/images/{imageId}
```

| `error` | 상태 | 언제 |
|---|---|---|
| `invalid_upload_request` | 400 | `InvalidUploadRequestException` |
| `image_not_found` | 404 | 그 프로젝트에 그 이미지가 없음 |
| `object_not_uploaded` | 409 | `ObjectNotUploadedException` — PUT 전에 `complete`, 또는 크기 불일치 |

`image` 테이블 초안: `id uuid PK, project_id uuid, file_name text, s3_key text unique, content_type text, size_bytes bigint, status PENDING|COMMITTED, created_at, committed_at`. `PENDING`으로 남은 행과 고아 객체를 지우는 배치가 함께 필요하다. ERD 확정 시 `CORE_TABLE_ERD.md`에 맞춘다.

### 4.3 프론트엔드가 할 일

1. 티켓을 받는다. `sizeBytes`는 `File.size`.
2. `fetch(uploadUrl, { method, headers, body: file })`.
3. `complete`를 부르고 응답의 `publicUrl`을 문서에 넣는다.
4. 2에서 실패하면 재시도는 **1부터**. 티켓이 5분이면 만료된다.

`config.json`에 새 값은 필요 없다. `publicUrl`은 API가 준다.

---

## 5. 자격 증명 — Pod Identity

content pod의 AWS 자격 증명은 ServiceAccount `content-api`에 걸린 **EKS Pod Identity association**에서 온다. EBS CSI Driver와 AWS Load Balancer Controller가 이미 쓰는 방식이라(§3.1, §8) 에이전트 애드온은 설치돼 있다.

```text
pod (SA content-api)
  → AWS_CONTAINER_CREDENTIALS_FULL_URI (Pod Identity Agent, 노드 로컬)
  → sts:AssumeRole lore-sentry-content-role
  → 임시 자격 증명 (SDK가 갱신)
```

코드에는 자격 증명 설정이 없다. `S3Presigner.builder().region(…)`만 있고 공급자는 SDK 기본 체인이다. 그래서 **로컬에서는 같은 코드가 `AWS_PROFILE=lorekeeper`를 집는다.** 클러스터/로컬 분기가 없다.

presigned URL은 서명에 쓴 임시 자격 증명이 만료되면 URL 수명이 남아 있어도 무효가 된다. URL 5분은 Pod Identity 세션 수명보다 훨씬 짧으므로 문제되지 않는다. TTL을 시간 단위로 올릴 생각이면 이 관계를 다시 봐야 한다.

Association이 없거나 역할이 틀리면 pod는 정상 기동하고 `/health`·`/health/db`도 200이다. **presign 호출만** `Unable to load credentials`로 실패한다.

---

## 6. CORS — gateway 단일 경계의 의도적 예외

§15-C는 "CORS는 gateway 한 곳에서만"이다. 이 기능은 브라우저가 S3에 직접 `PUT` 하므로 **버킷 CORS가 하나 더 생긴다.** 두 번째 경계를 두는 대신 얻는 것이 §2의 이유 전부이므로 예외로 둔다.

버킷 CORS는 최소로 잡았다.

```json
{ "AllowedOrigins": ["https://loresentry.com", "http://localhost:*", "http://127.0.0.1:*"],
  "AllowedMethods": ["PUT"], "AllowedHeaders": ["*"], "ExposeHeaders": ["ETag"], "MaxAgeSeconds": 3600 }
```

- `GET`이 없다. 읽기는 CloudFront로만 나가고, 버킷 자체는 브라우저가 읽을 수 없다.
- `localhost` 허용은 gateway CORS의 `http://localhost:[*]`와 같은 이유의 편의이고, 같은 이유로 가장 느슨한 부분이다.
- CloudFront 쪽에는 CORS 헤더가 없다. `<img>`는 CORS가 필요 없다. 캔버스에서 픽셀을 읽는 등 `crossorigin`이 필요해지면 그때 CloudFront에 응답 헤더 정책을 붙인다.

---

## 7. 읽기는 공개 — 결정과 되돌리는 길

`media.loresentry.com/projects/…` URL은 **인증 없이** 열린다. 키의 `uuid`가 추측을 막을 뿐이다.

이렇게 둔 이유는 gateway에 JWT 검증조차 아직 없어서(§15 체크리스트) 이미지만 서명 쿠키로 잠그는 것이 앞뒤가 맞지 않기 때문이다. 프로젝트 비공개를 강제해야 하는 시점이 오면 **CloudFront signed cookie**를 얹는다. 그때 바뀌는 것은 CloudFront 동작(trusted key group)과 gateway가 쿠키를 발급하는 엔드포인트 하나이고, 버킷·키 구조·업로드 경로는 그대로다.

---

## 8. 구축 기록

### 8.1 AWS — `setup-media.sh` (실행 완료)

```bash
aws sso login --profile lorekeeper
cd loresentry-content/docs/aws
./setup-media.sh
```

순서대로 (1) 버킷·Block Public Access·SSE-S3·CORS·라이프사이클, (2) IAM 정책·역할·연결, (3) `eks-pod-identity-agent` 확인과 association, (4) OAC, (5) `us-east-1` 인증서를 찾아 CloudFront 배포, (6) 그 배포만 읽는 버킷 정책. 마지막에 배포 도메인을 출력한다.

### 8.2 Cloudflare — 수동 (완료)

```text
media.loresentry.com   CNAME   <CF_MEDIA_DOMAIN>.cloudfront.net   프록시 OFF
```

`api`·`argocd`·apex와 마찬가지로 DNS 전용이다. 인증서는 CloudFront가 ACM으로 종료하므로 Cloudflare 쪽 TLS 설정은 없다.

### 8.3 확인된 결과 (2026-09-18)

```text
S3 PublicAccessBlock      True True True True
S3 CORS                   PUT / loresentry.com, localhost:*, 127.0.0.1:* / ExposeHeaders ETag
S3 bucket policy          cloudfront.amazonaws.com GetObject, SourceArn = distribution/<CF_MEDIA_ID>
IAM role                  arn:aws:iam::<AWS_ACCOUNT_ID>:role/lore-sentry-content-role
  attached                lore-sentry-content-media-policy
Pod Identity association  lore-sentry-k8s / prod / content-api  (a-…)
CloudFront                <CF_MEDIA_ID>  <CF_MEDIA_DOMAIN>.cloudfront.net  Deployed  Enabled
DNS                       media.loresentry.com  CNAME  <CF_MEDIA_DOMAIN>.cloudfront.net
curl -I https://media.loresentry.com/   HTTP/2 403, x-amz-bucket-region: ap-northeast-2
```

루트 `403`은 정상이다. 객체가 없는 키를 CloudFront가 S3에 물었고 S3가 거절한 것이며, `x-amz-bucket-region` 헤더가 붙어 있으면 CloudFront → OAC → S3까지 연결된 것이다. 실제 객체를 올리면 `200`이 된다.

### 8.4 GitOps·서비스 — PR 머지

| 저장소 | 브랜치 | 머지하면 |
|---|---|---|
| `loresentry-gitops` | `feat/media-s3` | Argo CD가 SA·ConfigMap·env를 적용. 기존 이미지(`build-3-1`)는 `MEDIA_*`를 무시하므로 무해 |
| `loresentry-content` | `feat/image-upload-presign` | CI → ECR → Lambda → GitOps 태그 갱신. 외부 동작 변화 없음 (엔드포인트 없음) |

---

## 9. 검증 명령

```bash
export AWS_PROFILE=lorekeeper AWS_REGION=ap-northeast-2
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
BUCKET=loresentry-media-prod-$ACCOUNT

# AWS 리소스
aws s3api get-public-access-block --bucket $BUCKET
aws s3api get-bucket-cors --bucket $BUCKET
aws s3api get-bucket-policy --bucket $BUCKET --query Policy --output text | jq
aws iam list-attached-role-policies --role-name lore-sentry-content-role
aws eks list-pod-identity-associations --cluster-name lore-sentry-k8s --namespace prod
aws cloudfront list-distributions \
  --query "DistributionList.Items[?contains(Aliases.Items || \`[]\`, 'media.loresentry.com')].[Id,DomainName,Status]"
dig +short media.loresentry.com CNAME

# 클러스터 배선 (gitops PR 머지 후)
kubectl -n prod get sa content-api
kubectl -n prod get configmap media -o yaml
kubectl -n prod get deploy content-api -o jsonpath='{.spec.template.spec.serviceAccountName}{"\n"}'
kubectl -n prod exec deploy/content-api -- env | grep -E 'MEDIA_|AWS_CONTAINER'

# 읽기 경로 끝까지: 객체를 하나 올려 CloudFront로 받아 본다
printf '\x89PNG\r\n\x1a\n' > /tmp/t.png
aws s3 cp /tmp/t.png s3://$BUCKET/projects/smoke/images/t.png --content-type image/png
curl -sI https://media.loresentry.com/projects/smoke/images/t.png | head -1     # HTTP/2 200
aws s3 rm s3://$BUCKET/projects/smoke/images/t.png
```

`AWS_CONTAINER_CREDENTIALS_FULL_URI`와 `AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE`이 pod env에 있으면 Pod Identity가 주입된 것이다. 없으면 association이나 SA 이름을 본다.

---

## 10. 장애 원인별 확인 위치

| 증상 | 확인할 곳 |
|---|---|
| presign이 `Unable to load credentials` | Pod Identity association (`aws eks list-pod-identity-associations`), Deployment의 `serviceAccountName`, pod env의 `AWS_CONTAINER_*` |
| presign이 `AccessDenied` | 역할 정책의 Resource가 `projects/*`인지, 키가 그 접두사로 시작하는지 |
| 브라우저 PUT이 CORS로 막힘 | 버킷 CORS의 `AllowedOrigins`. 프리플라이트(`OPTIONS`)는 S3가 처리한다 |
| PUT이 `403 SignatureDoesNotMatch` | 보낸 `Content-Type`·`Content-Length`가 티켓의 `headers`와 다르다. 또는 5분 만료 |
| `verifyUploaded`가 `ObjectNotUploadedException` | PUT이 실제로 안 갔거나 크기가 다르다. `aws s3api head-object --bucket $BUCKET --key <key>` |
| `publicUrl`이 403인데 객체는 있음 | 버킷 정책의 `AWS:SourceArn`과 배포 ID 불일치, 또는 OAC 미연결. `x-amz-bucket-region`이 있으면 CloudFront→S3까지는 갔다 |
| `publicUrl`이 DNS 실패 | Cloudflare `media` CNAME (§8.2) |
| `publicUrl`이 옛 내용 | 같은 키에 다시 올린 경우다. 키는 uuid라 정상 흐름에서는 생기지 않는다 |
| 로컬 `bootRun`에서 presign 실패 | `AWS_PROFILE=lorekeeper`와 SSO 로그인 상태 |

---

## 11. 남은 것

- [ ] `loresentry-gitops#3`, `loresentry-content#1` 머지
- [ ] 도메인 개발 시 §4.2 엔드포인트와 `image` 테이블, gateway 릴레이, 프론트엔드 업로드 UI
- [ ] 업로드 인가. gateway JWT 검증이 들어오면 `projectId` 소유 확인을 content에 붙인다
- [ ] `PENDING` 행과 고아 객체 정리 배치
- [ ] 필요해지면 썸네일/리사이즈 (업로드 시 Lambda 또는 CloudFront 함수)
- [ ] 프로젝트 비공개 요구가 생기면 CloudFront signed cookie (§7)
