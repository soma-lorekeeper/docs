# 이미지 업로드 — S3 Presigned URL + CloudFront

> 최신화: 2026-09-18  
> 대상: 사용자 이미지 업로드(문서 삽화, 표지 등)의 저장·조회 경로  
> 상태: **코드·매니페스트 완료, AWS 리소스 생성은 [진행 예정]** (§9의 스크립트를 실행하면 끝난다)

## 0. 한눈에 보는 현재 상태

| 항목 | 상태 |
|---|---|
| content `POST /projects/{id}/images` presign 발급 · `complete` 검증 · Flyway `image` 테이블 | 코드 완료, 테스트 통과 (PR) |
| gateway `/projects/{id}/images…` 릴레이 3개 | 코드 완료, 테스트 통과 (PR) |
| GitOps: `media` ConfigMap, `content-api` ServiceAccount, `MEDIA_*` env | 매니페스트 완료 (PR) |
| S3 `loresentry-media-prod-<AWS_ACCOUNT_ID>` | **[진행 예정]** `setup-media.sh` |
| IAM `lore-sentry-content-role` + Pod Identity association | **[진행 예정]** `setup-media.sh` |
| CloudFront `<CF_MEDIA_ID>` → `media.loresentry.com` | **[진행 예정]** `setup-media.sh` |
| Cloudflare `media` CNAME | **[진행 예정]** 수동 |
| 프론트엔드 업로드 UI | **[미구현]** |
| 업로드 엔드포인트 인가 | **[미구현]** gateway JWT 검증이 아직 없다 |

---

## 1. 방식 — 브라우저가 S3에 직접 올린다

핵심은 **Presigned URL**이다. content 서비스는 "이 키에, 이 타입으로, 이 크기로, 5분 안에"라는 서명이 붙은 S3 URL만 만들어 준다. 이미지 바이트는 gateway·content pod·ALB를 지나지 않는다.

```text
[업로드]
browser ──POST /projects/{projectId}/images {fileName, contentType, sizeBytes}──> gateway ──> content
browser <── 201 { imageId, uploadUrl, method: PUT, headers, expiresAt, publicUrl } ──────────┘
browser ──PUT uploadUrl (Content-Type, Content-Length 그대로) + 파일 본체──> S3
browser ──POST /projects/{projectId}/images/{imageId}/complete──> gateway ──> content ──HeadObject──> S3
browser <── 200 { status: COMMITTED, publicUrl }

[조회]
<img src="https://media.loresentry.com/projects/{projectId}/images/{uuid}.png">
CloudFront <CF_MEDIA_ID> ──OAC(SigV4)──> S3 (퍼블릭 접근 전면 차단)
```

`image` 테이블의 행은 발급 시점에 `PENDING`으로 생기고, `complete`에서 S3에 객체가 실제로 있고 크기가 선언값과 같을 때만 `COMMITTED`가 된다.

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

### AWS

| 리소스 | 값 | 비고 |
|---|---|---|
| S3 | `loresentry-media-prod-<AWS_ACCOUNT_ID>` (`ap-northeast-2`) | Block Public Access 전부 켬, SSE-S3, 버저닝 없음 |
| S3 CORS | `https://loresentry.com`, `http://localhost:*`, `http://127.0.0.1:*`에 `PUT`만 | §6 참고 |
| S3 라이프사이클 | 미완료 멀티파트 1일 후 abort | |
| S3 버킷 정책 | `cloudfront.amazonaws.com`이 `s3:GetObject`, `AWS:SourceArn`을 `<CF_MEDIA_ID>`로 제한 | |
| IAM 정책 | `lore-sentry-content-media-policy` — `projects/*`에 `PutObject` `GetObject` `DeleteObject` | 버킷 전체가 아니라 접두사 하나 |
| IAM 역할 | `lore-sentry-content-role`, 신뢰 주체 `pods.eks.amazonaws.com` | |
| Pod Identity association | `lore-sentry-k8s` / `prod` / `content-api` → 위 역할 | EKS API 객체. Git에 둘 수 없다 |
| CloudFront OAC | `loresentry-media-oac` (SigV4, always) | |
| CloudFront | `<CF_MEDIA_ID>` / `<CF_MEDIA_DOMAIN>.cloudfront.net`, alias `media.loresentry.com` | `CachingOptimized`, GET/HEAD, 함수 없음 |
| ACM | `loresentry.com` + `*.loresentry.com`, **`us-east-1`** | 프론트엔드용으로 이미 있는 인증서를 그대로 쓴다 (§17) |
| Cloudflare | `media` CNAME → `<CF_MEDIA_DOMAIN>.cloudfront.net`, **프록시 끔** | 다른 호스트와 같은 DNS 전용 |

정책 문서와 생성 스크립트는 `loresentry-content/docs/aws/`에 있다.

### GitOps (`loresentry-gitops`)

```text
workload/base/media/configmap.yaml            bucket, region, public-base-url
workload/base/content/serviceaccount.yaml     content-api
workload/base/content/deployment.yaml         serviceAccountName + MEDIA_BUCKET / MEDIA_REGION / MEDIA_PUBLIC_BASE_URL
```

`postgres`·`neptune` ConfigMap과 같은 원칙이다. 비밀이 아닌 값은 Git에, 비밀은 Git 밖에. 여기서는 비밀이 아예 없다.

### content (`loresentry-content`)

| 파일 | 역할 |
|---|---|
| `media/ImageService` | 타입·크기·파일명 검증, 키 생성, presign, `complete`의 `HeadObject` 검증 |
| `media/ImageRepository` | `JdbcClient`로 `image` 테이블 |
| `media/MediaConfig` | `S3Client`·`S3Presigner` 빈. 자격 증명 공급자는 SDK 기본 체인 |
| `web/ImageController` | 엔드포인트 3개 |
| `db/migration/V1__create_image.sql` | Flyway. **이 기능이 Flyway 도입 시점**이다 |

의존성은 AWS SDK v2 (`software.amazon.awssdk:s3`, BOM 2.46.7)를 직접 쓴다. awspring(Spring Cloud AWS)의 Boot 4.1 지원 여부를 따지지 않아도 되게 하기 위해서다.

### gateway (`loresentry-gateway`)

`/projects/{projectId}/images`, `/{imageId}/complete`, `GET /{imageId}` 세 경로를 content로 그대로 넘긴다. 업스트림 상태 코드와 본문을 보존하므로 content의 `400`·`404`·`409`가 그대로 클라이언트에 닿고, 전송 실패만 gateway 자신의 `502`가 된다. 기존 `/content` 릴레이 프로브와 달리 **도메인 경로**를 쓴다 — PROJECT_CONTEXT §5.3의 `/projects/{projectId}/workspace` 예와 같은 자리다.

---

## 4. API 계약

### `POST /projects/{projectId}/images`

```json
{ "fileName": "cover.png", "contentType": "image/png", "sizeBytes": 1234 }
```

```json
201 Created   Location: /projects/{projectId}/images/{imageId}
{
  "imageId": "…",
  "key": "projects/{projectId}/images/{uuid}.png",
  "uploadUrl": "https://loresentry-media-prod-<AWS_ACCOUNT_ID>.s3.ap-northeast-2.amazonaws.com/projects/…?X-Amz-Algorithm=…&X-Amz-Expires=300&…",
  "method": "PUT",
  "headers": { "Content-Type": "image/png", "Content-Length": "1234" },
  "expiresAt": "2026-09-18T00:05:00Z",
  "publicUrl": "https://media.loresentry.com/projects/{projectId}/images/{uuid}.png"
}
```

브라우저는 `headers`를 **그대로** 붙여 `PUT` 해야 한다. `Content-Type`·`Content-Length`가 서명에 들어 있으므로 다른 타입이나 더 큰 파일은 S3가 `403 SignatureDoesNotMatch`로 거부한다.

### `POST /projects/{projectId}/images/{imageId}/complete`

```json
200 OK
{ "imageId": "…", "projectId": "…", "fileName": "cover.png", "key": "…",
  "contentType": "image/png", "sizeBytes": 1234, "status": "COMMITTED", "publicUrl": "…" }
```

이미 `COMMITTED`면 S3를 다시 묻지 않고 그대로 돌려준다 (멱등).

### `GET /projects/{projectId}/images/{imageId}`

위와 같은 형태. `status`가 `PENDING`이면 아직 `complete`가 오지 않은 것이다.

### 오류

| `error` | 상태 | 언제 |
|---|---|---|
| `invalid_upload_request` | 400 | `fileName` 없음, 허용 목록 밖 `contentType`, 0 이하 또는 10 MiB 초과 `sizeBytes` |
| `image_not_found` | 404 | 그 프로젝트에 그 이미지가 없음 |
| `object_not_uploaded` | 409 | PUT 전에 `complete`를 불렀거나, 올라간 크기가 선언과 다름 |
| `upstream_unavailable` | 502 | gateway → content 전송 실패 (gateway 응답) |

### 규칙

| | 값 |
|---|---|
| 허용 타입 | `image/png` `image/jpeg` `image/webp` `image/gif` |
| 최대 크기 | 10 MiB (`media.max-size-bytes`) |
| URL 수명 | 5분 (`media.upload-url-ttl`) |
| 키 | `projects/{projectId}/images/{uuid}.{ext}` — 확장자는 **contentType에서** 결정, 파일명은 믿지 않는다 |
| 파일명 | 255자 이하, 표시용으로만 저장 |

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

Association이 없거나 역할이 틀리면 pod는 정상 기동하고 `/health`·`/health/db`도 200이다. **presign 호출만** `Unable to load credentials`로 실패한다. §10 참고.

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

## 8. 프론트엔드가 할 일 — **[미구현]**

1. `POST /projects/{id}/images`로 티켓을 받는다. `sizeBytes`는 `File.size`.
2. `fetch(uploadUrl, { method, headers, body: file })`. 브라우저가 `Content-Length`를 자동으로 붙이지만, 받은 `headers`를 그대로 넘겨도 무해하다.
3. `complete`를 부르고, 응답의 `publicUrl`을 문서에 넣는다.
4. 2에서 실패하면 재시도는 **1부터** 한다. 티켓이 5분이면 만료되기 때문이다.

`config.json`에 새 값은 필요 없다. `publicUrl`은 API가 준다.

---

## 9. 구축 절차

### 9.1 AWS — `setup-media.sh`

```bash
aws sso login --profile lorekeeper
cd loresentry-content/docs/aws
./setup-media.sh
```

멱등하게 짜여 있어 다시 돌려도 안전하다. 순서대로:

1. 버킷 생성, Block Public Access, SSE-S3, CORS, 라이프사이클
2. IAM 정책 `lore-sentry-content-media-policy` (있으면 새 버전을 기본으로), 역할 `lore-sentry-content-role`, 연결
3. `eks-pod-identity-agent` 애드온 상태 확인, `prod/content-api` association 생성 또는 갱신
4. OAC `loresentry-media-oac`
5. `us-east-1`의 `loresentry.com` 인증서를 찾아 CloudFront 배포 생성 (alias `media.loresentry.com`)
6. 그 배포만 읽을 수 있는 버킷 정책

마지막에 배포 도메인을 출력한다. 이것으로 9.2를 한다.

### 9.2 Cloudflare — 수동

```text
media.loresentry.com   CNAME   <CF_MEDIA_DOMAIN>.cloudfront.net   프록시 OFF
```

`api`·`argocd`·apex와 마찬가지로 DNS 전용이다. 인증서는 CloudFront가 ACM으로 종료하므로 Cloudflare 쪽 TLS 설정은 없다.

### 9.3 GitOps·서비스 — PR 머지

| 저장소 | 브랜치 | 머지하면 |
|---|---|---|
| `loresentry-gitops` | `feat/media-s3` | Argo CD가 SA·ConfigMap·env를 적용. 기존 이미지(`build-3-1`)는 `MEDIA_*`를 무시하므로 무해 |
| `loresentry-content` | `feat/image-upload-presign` | CI → ECR → Lambda → GitOps 태그 갱신. **Flyway가 `image` 테이블을 만든다** |
| `loresentry-gateway` | `feat/image-upload-relay` | 같은 경로로 배포 |

순서는 gitops → content → gateway가 자연스럽지만 강제는 아니다. content 새 이미지가 SA 없이 먼저 뜨면 presign만 실패하고, gateway가 먼저 뜨면 새 경로가 content에서 404를 받을 뿐이다.

---

## 10. 검증

```bash
export AWS_PROFILE=lorekeeper AWS_REGION=ap-northeast-2
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
BUCKET=loresentry-media-prod-$ACCOUNT

# AWS 리소스
aws s3api get-public-access-block --bucket $BUCKET
aws s3api get-bucket-cors --bucket $BUCKET
aws eks list-pod-identity-associations --cluster-name lore-sentry-k8s --namespace prod
aws cloudfront list-distributions --query "DistributionList.Items[?contains(Aliases.Items, 'media.loresentry.com')].[Id,DomainName,Status]"

# 클러스터 배선
kubectl -n prod get sa content-api
kubectl -n prod get configmap media -o yaml
kubectl -n prod get deploy content-api -o jsonpath='{.spec.template.spec.serviceAccountName}{"\n"}'
kubectl -n prod exec deploy/content-api -- env | grep -E 'MEDIA_|AWS_CONTAINER'

# 전 구간: presign → PUT → complete → CDN
P=11111111-1111-1111-1111-111111111111
printf '\x89PNG\r\n\x1a\n' > /tmp/t.png
SIZE=$(stat -f%z /tmp/t.png)
T=$(curl -s -X POST https://api.loresentry.com/projects/$P/images \
  -H 'content-type: application/json' \
  -d "{\"fileName\":\"t.png\",\"contentType\":\"image/png\",\"sizeBytes\":$SIZE}")
echo "$T" | jq
curl -s -o /dev/null -w '%{http_code}\n' -X PUT "$(echo "$T" | jq -r .uploadUrl)" \
  -H "Content-Type: image/png" --data-binary @/tmp/t.png            # 200
curl -s -X POST https://api.loresentry.com/projects/$P/images/$(echo "$T" | jq -r .imageId)/complete | jq
curl -sI "$(echo "$T" | jq -r .publicUrl)" | head -1                  # HTTP/2 200
```

`AWS_CONTAINER_CREDENTIALS_FULL_URI`와 `AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE`이 pod env에 있으면 Pod Identity가 주입된 것이다. 없으면 association이나 SA 이름을 본다.

---

## 11. 장애 원인별 확인 위치

| 증상 | 확인할 곳 |
|---|---|
| presign이 500, 로그에 `Unable to load credentials` | Pod Identity association (`aws eks list-pod-identity-associations`), Deployment의 `serviceAccountName`, pod env의 `AWS_CONTAINER_*` |
| presign이 500, `AccessDenied` | 역할 정책의 Resource가 `projects/*`인지, 키가 그 접두사로 시작하는지 |
| 브라우저 PUT이 CORS로 막힘 | 버킷 CORS의 `AllowedOrigins`. 프리플라이트(`OPTIONS`)는 S3가 처리한다 |
| PUT이 `403 SignatureDoesNotMatch` | 보낸 `Content-Type`·`Content-Length`가 티켓의 `headers`와 다르다. 또는 5분 만료 |
| `complete`가 409 | PUT이 실제로 안 갔거나 크기가 다르다. `aws s3api head-object --bucket $BUCKET --key <key>` |
| `publicUrl`이 403 | 버킷 정책의 `AWS:SourceArn`과 배포 ID 불일치, 또는 OAC 미연결. CloudFront 오류 페이지의 `x-cache` 헤더로 CloudFront까지는 왔는지 확인 |
| `publicUrl`이 DNS 실패 | Cloudflare `media` CNAME 누락 (§9.2) |
| `publicUrl`이 옛 내용 | 같은 키에 다시 올린 경우다. 키는 uuid라 정상 흐름에서는 생기지 않는다 |
| content pod가 `CrashLoopBackOff`, 로그에 Flyway | `content` DB 접근 불가 또는 `content_svc`에 CREATE 권한 없음. Flyway 도입으로 DB 불가 시 기동 실패가 **의도된** 동작이 됐다 |
| 로컬 `bootRun`에서 presign 실패 | `AWS_PROFILE=lorekeeper`와 SSO 로그인 상태 |

---

## 12. 남은 것

- [ ] `setup-media.sh` 실행과 Cloudflare `media` CNAME — 이 문서 §0의 [진행 예정] 항목
- [ ] 프론트엔드 업로드 UI (§8)
- [ ] 업로드 엔드포인트 인가. gateway JWT 검증이 들어오면 `projectId` 소유 확인을 content에 붙인다
- [ ] `PENDING`으로 남은 행과 고아 객체 정리 배치
- [ ] 삭제 API (`DeleteObject` 권한은 이미 있다)
- [ ] 필요해지면 썸네일/리사이즈 (업로드 시 Lambda 또는 CloudFront 함수)
- [ ] 프로젝트 비공개 요구가 생기면 CloudFront signed cookie (§7)
- [ ] `content` 이미지 태그가 `build-3-1`에서 갱신되는지, Flyway 첫 실행 로그 확인
