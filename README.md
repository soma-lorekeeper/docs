# Lore Sentry 문서

프로젝트의 설계 의도와 실제 구축 상태를 기록한다.

| 문서 | 내용 |
|---|---|
| [ENVIRONMENT_SETTING.md](ENVIRONMENT_SETTING.md) | **팀원 온보딩.** AWS 자격 증명과 EKS 클러스터 조회 접근 설정, Lens GUI |
| [INFRA_AND_CICD.md](INFRA_AND_CICD.md) | EKS · GitOps · CI/CD 구축 기록. 인프라 전반 |
| [LORE_SENTRY_PROJECT_CONTEXT.md](LORE_SENTRY_PROJECT_CONTEXT.md) | 서비스 아키텍처와 도메인 설계 |
| [CORE_FEATURE_REQUIREMENTS.md](CORE_FEATURE_REQUIREMENTS.md) | 서비스가 제공해야 할 핵심 기능 요구사항과 용어 정의 |
| [AI_CHAT_REQUIREMENTS.md](AI_CHAT_REQUIREMENTS.md) | **AI 챗 백엔드 요구사항.** 세션·질문과 답·컨텍스트 스냅샷·분량 판정·근거 형식·SSE·검색·드래그 질의·API 계약·데이터 모델 |
| [AI_CHAT_NAVIGATION_DESIGN.md](AI_CHAT_NAVIGATION_DESIGN.md) | **AI 챗 탐색 설계.** 지도(노드·관계·요약)를 보고 모델이 정한 순서로 문서를 읽고, 판정 모델이 컨텍스트 충분도와 답의 합리성을 검토한 뒤에만 답한다. 요약 스케줄러, 프롬프트 |
| [AI_CHAT_NAVIGATION_IMPLEMENTATION.md](AI_CHAT_NAVIGATION_IMPLEMENTATION.md) | **AI 챗 탐색 구현 설계.** 직접 짠 에이전트 루프(답 → 판정 → 전달), 시간 예산, Kafka로 도는 요약 스케줄러, ai-chat·content·gitops 변경, DB·설정값·테스트, 배포 순서와 PR 단위 |
| [AI_CHAT_EVALUATION.md](AI_CHAT_EVALUATION.md) | **AI 챗 엔드투엔드 평가.** 정답이 정해진 이야기 세 편·질문 36개, 채점 방법, 기준선과 11회 개선의 결과, 찾은 문제와 고친 것, 운영 평가 |
| [AI_CHAT_DAILY_USAGE.md](AI_CHAT_DAILY_USAGE.md) | **AI 챗 일일 사용량.** 토큰 기반 크레딧과 하루 한도, 한국 시간 자정 초기화, 429 거절, `GET /chat/usage`, 80%·100% 화면 |
| [AI_CHAT_VERIFICATION_LOG.md](AI_CHAT_VERIFICATION_LOG.md) | **AI 챗 검증 기록.** 로컬 통합 검증, 운영 배포 검증, 체계적 평가 11회와 회차별 변경·결과, 응답 시간·품질 원인 조사 실험, 운영 평가, 수정 확인, 재현 방법 |
| [GRAPH_INBOX_PATTERN.md](GRAPH_INBOX_PATTERN.md) | Content → Neptune 동기화 흐름. Outbox·Inbox가 각각 보장하는 것 |
| [OUTBOX_CDC_VS_POLLING.md](OUTBOX_CDC_VS_POLLING.md) | Outbox 행을 Kafka로 내보내는 방식 비교. 폴링 퍼블리셔와 CDC(Debezium)의 장단점과 선택 근거 |
| [IMAGE_UPLOAD_S3.md](IMAGE_UPLOAD_S3.md) | 이미지 업로드. S3 presigned PUT 직접 업로드, Pod Identity, CloudFront `media.loresentry.com` |
| [CONTENT_PROJECT_API.md](CONTENT_PROJECT_API.md) | **Content API 구현 현황.** 동작하는 엔드포인트 26개, 구현되지 않은 기능과 막는 것, gateway 중계, 남은 작업 |
| [TABLE_AND_LOGIC.md](TABLE_AND_LOGIC.md) | Authentication·Content·AI Chat 테이블(Flyway로 운영 DB 반영됨)과 3-way 문서 최신화·diff·저장 흐름 |
| [GOOGLE_ADS.md](GOOGLE_ADS.md) | **Google Ads 검색 광고 진행 상황.** 선불 계정, 캠페인 설정·키워드·광고 문구, 가입 전환 측정, 남은 일 |

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
