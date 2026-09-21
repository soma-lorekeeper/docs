# Lore Sentry 문서

프로젝트의 설계 의도와 실제 구축 상태를 기록한다.

| 문서 | 내용 |
|---|---|
| [ENVIRONMENT_SETTING.md](ENVIRONMENT_SETTING.md) | **팀원 온보딩.** AWS 자격 증명과 EKS 클러스터 조회 접근 설정 |
| [INFRA_AND_CICD.md](INFRA_AND_CICD.md) | EKS · GitOps · CI/CD 구축 기록. 인프라 전반 |
| [LORE_SENTRY_PROJECT_CONTEXT.md](LORE_SENTRY_PROJECT_CONTEXT.md) | 서비스 아키텍처와 도메인 설계 |
| [CORE_FEATURE_REQUIREMENTS.md](CORE_FEATURE_REQUIREMENTS.md) | 서비스가 제공해야 할 핵심 기능 요구사항과 용어 정의 |
| [GRAPH_INBOX_PATTERN.md](GRAPH_INBOX_PATTERN.md) | Content → Neptune 동기화 흐름. Outbox·Inbox가 각각 보장하는 것 |
| [OUTBOX_CDC_VS_POLLING.md](OUTBOX_CDC_VS_POLLING.md) | Outbox 행을 Kafka로 내보내는 방식 비교. 폴링 퍼블리셔와 CDC(Debezium)의 장단점과 선택 근거 |
| [IMAGE_UPLOAD_S3.md](IMAGE_UPLOAD_S3.md) | 이미지 업로드. S3 presigned PUT 직접 업로드, Pod Identity, CloudFront `media.loresentry.com` |
| [CONTENT_PROJECT_API.md](CONTENT_PROJECT_API.md) | **계획.** Content의 프로젝트 CRUD 엔드포인트 스펙. Kafka·파일·인증을 제외한 첫 도메인 작업 |
| [TABLE_AND_LOGIC.md](TABLE_AND_LOGIC.md) | Authentication·Content·AI Chat 테이블(Flyway로 운영 DB 반영됨)과 3-way 문서 최신화·diff·저장 흐름 |

## 새로 합류했다면

[ENVIRONMENT_SETTING.md](ENVIRONMENT_SETTING.md)부터 본다. 클러스터 조회 권한을 얻는 절차이고, 부여되는 권한은 `prod` 네임스페이스에 대한 **읽기 전용**이다.

## 식별자 마스킹

AWS 계정 ID, VPC·서브넷·보안 그룹 ID, CloudFront 배포 ID 등은 플레이스홀더로 치환되어 있다.

```text
<AWS_ACCOUNT_ID>   sg-<CLUSTER>   subnet-<PRIVATE_2A>   vpc-<REDACTED>
```

실제 값은 AWS 콘솔이나 CLI에서 확인한다.

```bash
aws sts get-caller-identity --profile lorekeeper
aws eks describe-cluster --name lore-sentry-k8s --region ap-northeast-2
```

이 저장소는 private이지만, 저장소 밖으로 내용을 옮길 때를 대비해 마스킹을 유지한다.
