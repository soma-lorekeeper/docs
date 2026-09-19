# Lore Sentry 프로젝트 핵심 컨텍스트

> 최신화: 2026-09-20  
> 목적: 이후 인프라/백엔드/MSA 설계 대화에서 공통 전제로 사용할 프로젝트 컨텍스트 문서  
> 범위: 서비스 컨셉, 주요 요구사항, MSA 구성, 인프라 아키텍처, EKS/GitOps/CI/CD 진행 상태, 확정된 설계 결정, 향후 검토 항목

**현재 상태 요약:** 5개 서비스(gateway · authentication · content · ai-chat · graph-rag)의 저장소·CI/CD·Kubernetes 배포가 전부 동작한다. `https://api.loresentry.com`에서 gateway를 통해 4개 내부 서비스까지 호출 체인이 확인됐다. 프론트엔드는 `https://loresentry.com`에 S3 + CloudFront로 배포됐고, **Pencil 기반 데스크톱 UX 설계와 frontend 구현 인계가 완료됐다**(§3.10). Kafka 브로커 3대와 `auth-valkey` 캐시가 클러스터에 올라가 있다. **DB도 프로비저닝됐다** — RDS PostgreSQL(논리 DB 3개)과 Amazon Neptune이 VPC 프라이빗 서브넷에 있고, 4개 서비스가 각자 자기 저장소에 붙는 것을 `https://api.loresentry.com/health/db`로 확인했다. 논리 DB 3개에는 Flyway로 테이블이 생성됐다(`TABLE_AND_LOGIC.md` §9). 다만 **각 서비스는 아직 도메인 로직이 없는 스켈레톤**이라 테이블을 읽고 쓰는 코드는 없다. Kafka도 브로커와 잠정 토픽만 있고, 어떤 토픽을 쓸지도 producer도 아직 없다. 즉 **플랫폼과 화면 설계는 준비됐고, 실제 도메인·API 연동은 시작 단계**다.

---

## 0. 문서 사용 원칙

이 문서는 현재까지의 요구사항, 최초 인프라 다이어그램, `INFRA_AND_CICD.md`, 이후 대화에서 변경·확정한 설계를 하나로 합친 기준 문서다. 인프라/CI-CD의 **구체적 구현과 검증 내역**은 `INFRA_AND_CICD.md`에 있고, 이 문서는 서비스·도메인·설계 결정을 다룬다.

표기는 다음 의미를 가진다.

- **[확정]** 현재 설계 기준으로 채택한 내용
- **[구축 완료/확인]** 실제 적용 또는 동작을 대화에서 확인한 내용
- **[진행 예정]** 설계는 있으나 실제 적용 여부를 아직 확인하지 않은 내용
- **[추후 검토]** 향후 필요 시 결정할 내용
- **[변경됨]** 최초 다이어그램/문서에서 이후 대화로 변경된 내용

기존 문서와 이후 대화가 충돌하면 **이 문서의 최신 확정 사항을 우선**한다.

---

# 1. 서비스 개요

## 1.1 서비스 한 줄 정의

**Lore Sentry는 AI 기능을 갖춘 소설 웹 에디터이며, 프로젝트 단위의 창작 문서와 파일 간 관계를 그래프로 저장·시각화하고, 해당 데이터에 기반한 RAG/GraphRAG AI 대화를 제공하는 서비스다.**

## 1.2 핵심 사용자 경험

사용자는 하나의 **프로젝트** 안에서 다음 작업을 수행한다.

1. 소설 원고 및 세계관 관련 문서를 작성한다.
2. 캐릭터, 사건, 장소, 조직, 아이템 등의 문서를 파일 단위로 관리한다.
3. 파일 사이에 명시적인 참조를 만든다.
4. 참조 관계를 그래프로 시각화한다.
5. 프로젝트의 소설/세계관 데이터를 참고하는 AI와 대화한다.
6. 문서 자동 저장, 버전 기록, 휴지통, 복원, 검색, 메모 등의 편집 기능을 사용한다.

## 1.3 서비스의 특징

### 그래프 기반 세계관

파일은 단순한 텍스트 문서에 그치지 않고 서로 관계를 가진다.

예시:

```text
캐릭터 A ──소속──> 조직 X
캐릭터 A ──등장──> 사건 Y
사건 Y     ──발생 장소──> 장소 Z
```

그래프 관계의 원천은 **명시적으로 저장된 파일 참조**다.

- 참조 속성
- 이벤트 시간 항목의 관련 파일
- 본문에 삽입한 명시적 파일 참조

본문에 단순히 파일 이름과 같은 문자열을 적었다고 해서 관계를 자동 추론하지 않는다.

### AI + RAG/GraphRAG

AI는 현재 프로젝트의 저장된 창작 자료와 그래프 정보를 참고해 답변한다.

```text
Project Files
     │
     ├── 제목/본문
     ├── 사용자 정의 속성
     ├── 이벤트 시간 항목
     └── 파일 참조
             │
             ▼
        RAG / GraphRAG
             │
             ▼
          AI Agent
             │
             ▼
          AI Response
```

다른 프로젝트, 휴지통 데이터, 접근 권한이 없는 데이터, 영구 삭제 데이터는 AI 참고 대상에서 제외해야 한다.

---

# 2. 도메인 핵심 용어

아래 용어를 프로젝트 전반의 기준 용어로 사용한다.

| 용어 | 의미 |
|---|---|
| 프로젝트 | 창작 자료와 작업을 묶는 최상위 단위이자 접근/데이터 분리 기준 |
| 작업공간 | 한 프로젝트의 콘텐츠를 탐색·편집하는 UI |
| 파일 | 프로젝트 안에서 식별자와 파일 유형을 가진 원본 콘텐츠 단위 |
| 파일 유형 | 원고, 설정, 캐릭터, 이벤트, 조직, 아이템, 장소, 세계관의 8종 |
| 파일 참조 | 다른 파일의 식별자를 지정한 명시적 연결 정보 |
| 관계 | 파일 참조로부터 생성되는 파일 사이의 방향성 연결 |
| 사용자 정의 속성 | 사용자가 이름과 타입을 지정하는 속성. 텍스트/참조 속성 존재 |
| AI 대화 세션 | 한 프로젝트에 귀속되는 AI 대화 단위 |
| AI 응답 | 사용자 메시지에 대해 생성된 AI 답변 |
| 프로젝트 메모 | 특정 파일이 아닌 프로젝트 자체에 귀속되는 메모 |
| 파일 메모 | 특정 파일 하나에 귀속되는 메모 |
| 파일 휴지통 | 프로젝트 내부에서 삭제된 파일/폴더의 복원 가능 영역 |
| 프로젝트 휴지통 | 삭제된 프로젝트의 복원 가능 영역 |
| 작업 상태 | 탭, 활성 파일, 편집 위치, 패널 배치, 그래프 보기 상태 등 UI 복원 정보 |

---

# 3. 주요 기능 요구사항 요약

## 3.1 인증 및 계정

- 로그인 수단은 Google OAuth 하나를 기준으로 한다.
- 로그인 성공 시 프로젝트 목록으로 이동한다.
- 로그인 실패/취소/세션 만료를 구분한다.
- 세션 만료 후 재로그인 시 가능한 경우 기존 작업공간으로 복귀한다.
- 사용자는 표시 이름을 수정할 수 있다.
- Google 이메일은 읽기 전용이다.
- 확인 후 로그아웃할 수 있다.

## 3.2 프로젝트 관리

- 사용자는 접근 가능한 프로젝트 목록을 조회한다.
- 프로젝트는 마지막 작업 일시 기준으로 정렬된다.
- 프로젝트 이름은 앞뒤 공백을 제거해 저장하며, 빈 값·공백만인 값·255자 초과를 허용하지 않는다.
- 프로젝트 이름은 사용자별 활성 프로젝트 범위에서 대소문자 무시 중복을 허용하지 않는다.
- 프로젝트 설정에서 이름과 설명을 관리한다.
- 프로젝트를 휴지통으로 이동하고 복원하거나 영구 삭제할 수 있다.
- 프로젝트 영구 삭제 시 소속 파일, 버전, 메모, AI 대화, 작업 상태, 즐겨찾기, 파일 참조 등도 함께 제거되어야 한다.
- 생성·이름 변경·휴지통 이동·복원·영구 삭제는 기본/빈/처리 중/성공/오류/확인 상태를 제공한다. 처리 중에는 중복 요청과 취소·닫기를 막고, 실패 시 입력과 현재 목록 맥락을 유지한 채 같은 위치에서 재시도할 수 있어야 한다.

## 3.3 파일/폴더/섹션/즐겨찾기

- 기본 섹션과 사용자 생성 섹션을 제공한다.
- 8종 파일과 폴더를 생성/이름 변경/이동할 수 있다.
- 동일 위치에서는 파일/폴더 타입과 무관하게 이름 중복을 허용하지 않는다.
- 즐겨찾기는 원본 복제가 아니라 원본 식별자를 가리키는 바로가기다.
- 즐겨찾기를 열어도 항상 원본 파일의 기존 탭으로 이동하며, 폴더는 탭으로 열지 않고 사이드바에서 펼치거나 접는다.
- 파일/폴더를 휴지통으로 이동하고 복원하거나 영구 삭제할 수 있다.
- TXT/Markdown/DOCX를 원고로 가져올 수 있다.
- 파일·폴더 생성과 이름 변경은 인라인 입력으로 수행하며, 생성/이동/삭제의 실패 후에도 이름·선택·드롭 대상 문맥을 유지해 재시도할 수 있어야 한다.
- 기본 섹션과 사용자 생성 섹션의 메뉴에서는 현재 섹션 바로 아래에 사용자 생성 섹션을 추가할 수 있다. 사용자 생성 섹션의 삭제는 확인과 오류 상태를 제공한다.

## 3.4 문서 편집

- 원고는 제목 + 본문 구조를 가진다.
- 원고 외 7종 파일은 제목 + 사용자 정의 속성 + 본문 구조를 가진다.
- 사용자 정의 속성은 텍스트 속성과 파일 참조 속성을 지원한다.
- 본문에도 명시적 파일 참조를 삽입할 수 있다.
- 자동 저장을 수행하며 저장 중/완료/오류 상태를 구분한다.
- 저장 실패 시 로컬 복구 가능성을 제공하고 재시도할 수 있어야 한다.
- 편집 모드를 별도로 켜지 않으며, 저장 중 탭 전환이나 보조 패널 열기로 변경 내용을 버리지 않는다. 같은 파일을 다시 열면 기존 탭과 마지막 스크롤 위치로 돌아간다.

## 3.5 버전/내보내기/편집 잠금

- 저장된 변경에 대해 자동 버전을 남긴다.
- 자동 버전은 최대 5분 간격, 30일 보관이 목표다.
- 사용자가 이름을 붙인 버전은 파일 영구 삭제 시까지 보관한다.
- 이전 버전 복원은 기존 파일을 덮어쓰는 것이 아니라 새 변경으로 저장한다.
- TXT/Markdown/PDF로 내보내기를 지원한다.
- 편집 잠금 시 제목, 본문, 속성, 시간 항목, 파일 참조, 버전 복원 등의 편집을 막는다.

## 3.6 메모

- 프로젝트 메모는 여러 개를 둘 수 있다.
- 파일 메모는 파일당 하나를 가진다.
- 자동 저장/복구를 제공한다.
- 소속 프로젝트/파일의 휴지통 상태에 따라 메모도 함께 비활성화/복원/삭제된다.
- 파일 메모 패널은 오른쪽 또는 아래에 둘 수 있고, 사용자가 마지막으로 선택한 배치와 크기를 프로젝트·사용자 단위로 복원한다. AI 챗과 동시에 열려 공간이 부족하면 기존 패널을 입력과 선택을 보존한 채 접고, 사용자가 수동으로 다시 열 수 있어야 한다.

## 3.7 검색

현재 프로젝트의 **활성 파일**을 대상으로 파일 제목과 본문을 검색한다.

검색 대상에 포함되지 않는 것:

- 휴지통 파일
- 다른 프로젝트 파일
- 폴더
- 프로젝트 설정
- 사용자 정의 속성 값
- 시간 항목의 별도 입력 내용
- 메모
- AI 대화 내역

검색 결과 우선순위는 다음을 목표로 한다.

```text
파일 제목 전체 일치
  > 파일 제목 부분 일치
  > 본문 일치
```

## 3.8 파일 관계 그래프

- 현재 프로젝트의 활성 파일만 표시한다.
- 관계가 없는 파일도 기본적으로 노드로 표시한다.
- 관계는 명시적 파일 참조에서 생성된다.
- 관계 방향은 `참조를 가진 파일 -> 참조 대상 파일`이다.
- 그래프는 조회/탐색용이며 초기 범위에서는 그래프에서 관계 자체를 직접 편집하지 않는다.
- 파일 유형 필터, 제목 검색, 확대/축소, 파일 열기, 보기 상태 복원을 지원한다.

## 3.9 AI 대화

- 프로젝트별 AI 대화 세션을 가진다.
- 사용자 메시지와 완성된 AI 응답을 저장한다.
- AI 응답은 스트리밍 방식으로 점진적으로 보여주는 것을 전제로 한다.
- 생성 중 중단할 수 있다.
- 세션을 새로 만들고, 이름을 변경하거나 확인 후 삭제할 수 있다. 새 세션의 빈 상태와 세션 목록·메뉴 상태를 제공한다.
- 중단/실패한 미완성 응답은 최종 대화 내역으로 보관하지 않는다.
- 네트워크 단절 시 응답 완료 여부를 확인한 뒤 재개해야 하며 동일 응답을 무조건 중복 생성하지 않는다.
- 완성된 응답 생성은 성공했으나 저장만 실패한 경우 **같은 응답 내용을 저장 재시도**한다.
- AI 대화는 원본 파일을 자동 수정하지 않는다.
- 초기에는 AI 사용량 제한을 두지 않는다.

## 3.10 확정된 프론트엔드 UX / Pencil 설계 기준

**[구축 완료 — 디자인 및 구현 인계]** 프론트엔드 저장소의 `docs/design/lorekeeper.pen`, `docs/design/lorekeeper.lib.pen` 및 화면 레지스트리는 이 서비스의 데스크톱 UX 기준이다. 기능·데이터 규칙이 Pencil 화면과 충돌하면 기능 문서와 이 문서의 요구사항을 먼저 적용하고, 그다음 Pencil 상태 화면과 공통 컴포넌트를 따른다.

### 정보 구조와 화면 범위

```text
랜딩(선택) → 로그인 → 프로젝트 목록 → 통합 작업공간
```

- 확정된 Pencil 상태 화면은 로그인, 프로젝트 목록/휴지통/계정, 통합 작업공간의 편집·파일·탭·검색·메모·속성 문서·시간 흐름·설정·도움말·AI 챗을 포괄하는 **136개**다. 화면 ID는 구현·검수용 고정 식별자이며, 하나의 Route는 기본·빈·로딩·처리·성공·오류·확인 상태 여러 개에 대응할 수 있다.
- 제공 Route는 `/`, `/login`, `/projects`, `/projects/guide`, `/projects/trash`, `/workspace`, `/design-system`이다. `/workspace`는 서버가 접근을 검증한 `projectId`가 있어야 하며, URL query 자체를 권한 근거로 사용하지 않는다.
- 프로젝트에 재진입하면 마지막 탭·활성 파일·편집 위치·패널 배치 등 작업 상태를 복원한다. 열 탭이 없으면 새 탭 화면 하나를 연다.
- 그래프의 데이터·행동 규칙은 본 문서 §3.8을 따른다. 현재 확정된 Pencil 화면 묶음에는 그래프의 시각 설계가 포함되지 않았으므로, 그래프 UI를 임의로 새 설계로 확장하지 않고 별도 시각 디자인 산출물을 기준으로 구현한다.

### 비동기 동작과 오류 UX

- 목록, 검색, 저장, 생성, 변경, 삭제, 복원, OAuth는 해당 기능의 로딩/처리/성공/오류 또는 확인 상태를 명시적으로 보여준다. 서버 결과가 확인되기 전에는 낙관적 성공을 표시하지 않는다.
- 오류는 원인을 임의로 추측하지 않고 설명·`다시 시도`를 제공하며, 사용자가 입력한 값과 현재 선택·탭·패널 문맥을 보존한다.
- 위험 행동(영구 삭제, 휴지통 이동, 로그아웃, 저장하지 않은 설정 폐기, AI 세션 삭제)은 확인 대화상자를 거친다. 처리 중에는 중복 실행을 막는다.
- 외부 가이드·피드백·정책 링크는 새 탭 열림을 사전에 알리고, 열기에 실패하면 원래 화면에서 재시도 또는 링크 복사를 제공한다.

### 접근성·테마·지원 화면 크기

- 키보드로 메뉴·탭·목록·대화상자를 완전히 조작할 수 있어야 한다. `Esc` 취소와 닫기 뒤에는 행동을 시작한 요소로 포커스를 복귀시키며, 확인 대화상자는 포커스를 내부에 가두고 기본 포커스를 취소 행동에 둔다.
- 선택·파일/시간 유형·저장/오류/위험 상태는 색상만으로 전달하지 않고 아이콘·테두리·레이블·문구를 함께 사용한다. 비동기 상태 알림은 한 번만 전달한다.
- 다크/라이트는 별도 화면 구조가 아니라 동일 컴포넌트와 토큰의 `mode` 전환으로 제공한다. 공통 디자인 토큰(색상·간격·반경·글자 체계)을 단일 원본으로 사용하며, 검증된 대비 기준은 일반 텍스트 4.5:1, 비텍스트 UI 3:1 이상이다.
- 현재 확정 범위는 **1440 × 900 데스크톱 통합 작업공간**이다. 모바일·태블릿 전용 레이아웃, 협업/멤버 권한, 분할 화면, AI 모델 설정, 파일 유형별 템플릿은 별도 후속 범위다.

### Backend 연동 전제

- 정적 프론트엔드는 CDN에서 제공되고, 브라우저가 별도 BFF/API를 호출한다. 실제 OAuth, 프로젝트·문서 데이터, 권한 판정과 저장 성공은 backend 응답으로만 확정한다.
- 현재 frontend 인계가 정의한 연동 경계는 Google OAuth 시작/로그아웃, 프로젝트 목록/접근 검증, 작업공간 복원/전환 전 저장/문서 변경, 계정 수정이다. HTTP endpoint·payload·오류 스키마는 gateway/BFF와 각 도메인 서비스의 API 계약에서 확정한다.

---

# 4. MSA 설계 원칙

## 4.1 기본 방향

**[확정] AWS EKS 위에서 MSA로 구성한다.**

서비스는 도메인별로 분리하고, 각 서비스가 자신의 데이터 저장소를 감싸는 형태를 지향한다.

```text
Gateway / BFF
   │
   ├── Authentication Service ── Auth DB
   ├── Content Service        ── Content DB
   ├── Agent / AI Chat        ── Agent DB
   └── GraphRAG Service       ── Amazon Neptune
```

## 4.2 Database per Service

**[확정] 기본 원칙은 서비스별 DB ownership이다.**

- 다른 서비스의 DB에 직접 접근하지 않는다.
- 필요한 데이터는 서비스 API나 이벤트를 통해 전달한다.
- 데이터 저장 기술은 서비스 목적에 따라 달라도 된다.

예:

```text
Content Service ── PostgreSQL
Agent Service   ── PostgreSQL
GraphRAG        ── Neptune
```

최초 다이어그램에서 AI Chat DB는 MongoDB였으나 이후 요구사항에서 **PostgreSQL로 변경**하는 방향으로 정리했다.

> **[확정] SQL 기반 서비스 3종(Authentication / Content / Agent)의 DB 엔진은 PostgreSQL로 통일한다.**

## 4.3 동기 통신과 비동기 통신

모든 서비스 통신을 Kafka로 보내지 않는다.

### 동기 통신

사용자 요청에 즉시 응답이 필요한 경우 HTTP/gRPC 계열의 동기 호출을 사용한다.

예:

```text
Browser
  ↓
Gateway
  ↓
Content Service
  ↓
response
```

### 비동기 통신

후속 작업이 즉시 사용자 응답 경로에 포함될 필요가 없거나 서비스 결합도를 낮춰야 하는 경우 Kafka 이벤트를 사용한다.

대표 후보:

```text
Content Service
  │
  └── FileUpdated event
          │
          ▼
        Kafka
          │
          ├── GraphRAG Consumer
          ├── Search/Index Consumer
          └── 기타 후속 처리
```

## 4.4 Outbox / Inbox / Saga

**[설계 목표] MSA 학습 목적을 포함해 표준적인 분산 시스템 패턴을 실제 서비스 흐름에 적용한다.**

- Transactional Outbox
- Inbox / idempotent consumer
- Saga
- Kafka 기반 비동기 메시징

가장 자연스러운 초기 적용 후보는 **Content 데이터 변경 -> GraphRAG/검색 데이터 동기화**다.

## 4.5 Gateway와 Kafka의 경계

Gateway가 사용자 요청을 받았다고 해서 곧바로 Kafka에 메시지를 넣는 구조를 기본으로 하지 않는다.

```text
Client request
   ↓
Gateway
   ↓
Domain Service
   ↓
Domain transaction
   ↓
Outbox
   ↓
Kafka
```

즉 이벤트 발행 책임은 일반적으로 실제 도메인 상태를 변경한 서비스 쪽에 둔다.

---

# 5. 외부 요청 아키텍처 - 현재 확정안

## 5.1 최종 요청 경로

**[확정]** 외부 API 요청 경로는 다음과 같이 본다.

```text
Client / Browser
      │
      ▼
 Cloudflare
      │
      ▼
 AWS ALB
      │
      ▼
 Gateway / BFF
      │
      ├── Authentication Service
      ├── Content Service
      ├── Agent Service
      └── GraphRAG Service
```

## 5.2 ALB 역할

**[확정] ALB는 인프라 레벨 L7 진입점이다.**

주요 책임:

- TLS termination
- 외부 HTTP/HTTPS 요청 수신
- Gateway workload에 대한 부하 분산
- health check
- 필요 시 최소한의 host/path 기반 forwarding

ALB의 책임이 아닌 것:

- 여러 마이크로서비스 호출
- 비즈니스 응답 조합
- 프로젝트/파일 도메인 로직
- API Composition

현재 EKS 설계는 AWS Load Balancer Controller와 `target-type: ip`를 이용해 ALB Target Group에 Pod IP를 등록하는 방식을 기준으로 한다.

논리적 표현:

```text
ALB -> Gateway Service -> Gateway Pod
```

실제 데이터 경로에 가까운 표현:

```text
ALB
  ↓
Target Group
  ↓
Gateway Pod IP
```

### 현재 실측값 — **[구축 완료]**

```text
ALB            k8s-loresentry-<REDACTED> (internet-facing, active)
Listener       :443 HTTPS + ACM 43ac7f41-...,  :80 HTTP
Target Group   k8s-prod-gatewaya-<REDACTED> — target-type ip, HTTP, HC /health
idle_timeout   60초 (기본값)
HTTP/2         활성
공유           argocd.loresentry.com과 같은 ALB (IngressGroup lore-sentry)
```

**Cloudflare는 데이터 경로에 없다.** `api`·`argocd`·`loresentry.com` 세 호스트 모두 프록시가 꺼진 DNS 전용이라, 이름만 해석하고 트래픽은 ALB(또는 CloudFront)로 직접 간다. 따라서 WAF·캐싱·DDoS 완화는 **현재 아무도 하지 않는다.**

`idle_timeout 60초`는 AI 스트리밍의 제약이다. gateway의 `spring.http.clients.read-timeout`이 10초이므로 SSE/WebSocket을 붙이려면 **둘 다** 올려야 한다.

## 5.3 Gateway / BFF 역할

**[확정] Gateway/BFF는 애플리케이션 레벨 API 경계다.**

주요 책임:

- 외부 클라이언트용 단일 API 진입점
- 내부 마이크로서비스 라우팅
- API Composition
- 여러 서비스 결과를 하나의 클라이언트 응답으로 조합
- 인증/인가 컨텍스트 처리 또는 전달
- 내부 MSA 토폴로지를 클라이언트로부터 은닉

예:

```text
GET /projects/{projectId}/workspace
           │
           ▼
        Gateway
       /   |    \
      /    |     \
Content  Auth   Graph
      \    |     /
       \   |    /
     combined response
```

### 현재 구현 수준 — **[진행 중]**

책임 소재는 확정이지만 **실제로 조합하는 도메인 엔드포인트는 아직 없다.** 현재 gateway가 가진 것은 업스트림 1:1 릴레이 4개(`/graph` `/ai-chat` `/auth` `/content`)와 **fan-out 집계 1개(`/health/db`)**다.

`/health/db`는 4개 업스트림의 같은 경로를 호출해 하나의 문서로 합치고, 전부 ok면 200, 하나라도 실패하면 503 + `status: degraded`를 반환한다. 업스트림의 에러 상태를 전송 실패로 취급하지 않아 **각 업스트림의 원본 에러 본문이 집계 응답에 그대로 보존된다.** 도메인 composition이 갖춰야 할 구조(fan-out → 부분 실패 표현 → 단일 응답)를 이미 가지고 있으므로, 앞으로 만들 composition 엔드포인트의 참고 구현으로 쓸 수 있다.

책임을 gateway에 둔 근거는 배제 논리다.

| 후보 | 왜 안 되는가 |
|---|---|
| ALB | L7이지만 응답 본문을 조합할 수 없다. host/path 분기가 한계다 |
| 도메인 서비스 | content가 authentication을 호출해 합치기 시작하면 서비스 간 의존이 얽히고, Database per Service의 경계가 호출 경계로 새어나간다 |
| 클라이언트 | 화면 하나가 4개 서비스를 호출하면 내부 MSA 토폴로지가 노출되고, 서비스를 쪼갤 때마다 프론트엔드가 깨진다 |
| Kafka | 비동기 이벤트 버스다. 사용자 응답을 기다리는 경로에 넣을 수 없다 (§4.5) |

## 5.4 NGINX Ingress Controller를 현재 구조에서 제외한 이유

처음에는 다음 구조를 검토했다.

```text
Cloudflare -> NLB -> NGINX Ingress -> Gateway -> Services
```

하지만 모든 외부 API 트래픽이 Gateway 하나로 들어가는 구조에서는 NGINX가 수행할 L7 routing이 사실상 다음 한 줄로 축소된다.

```text
모든 API 요청 -> Gateway
```

따라서 별도 NGINX layer를 추가할 실익이 현재는 크지 않다고 판단했다.

**[변경됨] 현재는 기존의 AWS-native한 `Cloudflare -> ALB -> Gateway -> Microservices` 구조를 유지한다.**

---

# 6. 서비스 구성

현재 논리적 서비스 경계는 다음과 같이 본다.

서비스별 저장소와 현재 구현 상태:

| 서비스 | 저장소 | 스택 | 상태 |
|---|---|---|---|
| Gateway / BFF | `loresentry-gateway` | Java 21 · Spring Boot 4.1.1 · WebMVC + 가상 스레드 | 배포됨, 라우팅·CORS 동작 |
| Authentication | `loresentry-authentication` | Java 21 · Spring Boot 4.1.1 | 배포됨, 스켈레톤 |
| Content | `loresentry-content` | Java 21 · Spring Boot 4.1.1 | 배포됨, 스켈레톤 |
| Agent / AI Chat | `loresentry-ai-chat` | Python 3.12 · FastAPI 0.141.1 | 배포됨, 스켈레톤 |
| GraphRAG | `loresentry-graph-rag` | Python 3.12 · FastAPI 0.141.1 | 배포됨, 스켈레톤 |
| Frontend | `loresentry-frontend` | Next.js 16.3.2 · React 19 | S3 + CloudFront 배포, 클러스터 밖 정적 제공. Pencil UX 설계/구현 인계 완료 |

"스켈레톤"은 `GET /`와 `GET /health`만 있다는 뜻이다. 모든 서비스가 포트 8000, `<service>-api` 이름, `/health` probe 규약을 공유한다.

## 6.1 Gateway / BFF

- 외부 API 단일 진입점
- API Composition
- 내부 서비스 요청 orchestration
- 클라이언트가 내부 마이크로서비스 주소를 직접 알지 않도록 보호
- **브라우저에 대한 CORS 경계** — 내부 서비스에는 CORS 설정이 없다

**[구축 완료]** 기술 스택 결정과 근거:

- **Spring Boot 4.1.1 / Java 21**: 원래 "Spring Boot 3"으로 정했으나 Initializr가 3.x를 더 제공하지 않는다(4.0.x, 4.1.x만). 결정의 핵심이었던 **WebFlux가 아닌 MVC + 가상 스레드**는 그대로다.
- **WebMVC + 가상 스레드, WebFlux 아님**: composition gateway는 대부분 다른 서비스를 기다리는 일이라 전통적으로 리액티브의 교과서 사례였다. 가상 스레드가 그 근거를 없앴다 — 가상 스레드에서 블로킹 코드는 대기 중 OS 스레드를 점유하지 않으므로, 평범한 MVC로 높은 fan-out 동시성을 처리한다. 순차적 코드, 읽을 수 있는 스택 트레이스, 다른 Spring 서비스와 같은 모델을 얻는다.
- **Spring Cloud Gateway 아님**: 이름과 달리 리버스 프록시(라우팅/레이트리밋용 필터 체인)다. 세 서비스를 호출해 응답을 병합하는 일에는 맞지 않고, composition 로직을 필터에 밀어넣으면 테스트·디버깅이 어렵다. Netty 기반 리액티브라서 MVC+가상 스레드로 피하려던 것을 다시 들인다.
- **GraphQL federation 아님**: API composition은 GraphQL의 본령이고 나중에 재검토할 가치가 있다. 지금 도입하면 스키마 설계, DataLoader/N+1, 캐싱, AI 응답 스트리밍의 어색함을 한꺼번에 떠안는다.

현재 엔드포인트 — 4개는 **relay probe**이고 완성된 API 표면이 아니다. 각 서비스의 실제 엔드포인트가 들어갈 자리다.

| Method | Path | 동작 |
|---|---|---|
| `GET` | `/health` | Kubernetes probe와 ALB target group용. **DB를 건드리지 않는다** |
| `GET` | `/health/db` | 4개 업스트림의 `/health/db`를 묶어 반환. 전부 ok면 200, 하나라도 실패하면 503 + `status: degraded` |
| `GET` | `/` | `{"service":"gateway-api"}` |
| `GET` | `/graph` | graph-rag 호출 |
| `GET` | `/ai-chat` | ai-chat 호출 |
| `GET` | `/auth` | authentication 호출 |
| `GET` | `/content` | content 호출 |

**의도적으로 하지 않은 것:** 아무 경로나 매칭되는 서비스로 흘려보내는 catch-all `/**` 프록시. 그러면 gateway가 ALB가 이미 하는 일을 중복하는 리버스 프록시가 되고, composition·신원 전달·응답 재구성을 둘 자리가 사라진다. 경로는 하나하나 명시 선언한다.

업스트림이 실패하면 스택 트레이스를 흘리지 않고 502에 어느 업스트림인지만 담는다.

```json
{ "error": "upstream_unavailable", "upstream": "content" }
```

한 업스트림 장애가 다른 경로에 영향을 주지 않는 것을 실제로 확인했다.

## 6.2 Authentication Service

- Google 기반 인증 흐름
- 사용자 계정 정보
- 표시 이름
- 인증 세션/토큰 관련 로직

기술 스택 초안: Spring Boot

DB: PostgreSQL 논리 DB `authentication` (역할 `authentication_svc`)

## 6.3 Content Service

가장 큰 창작 도메인 서비스 후보다.

담당 범위 후보:

- 프로젝트
- 파일
- 폴더/섹션
- 즐겨찾기
- 문서 본문/속성
- 파일 참조의 원천 데이터
- 버전
- 메모
- 휴지통
- 검색을 위한 원본 데이터
- 작업 상태 일부

기술 스택 초안: Spring Boot

DB: PostgreSQL 논리 DB `content` (역할 `content_svc`)

이미지 저장: S3 `loresentry-media-prod-<AWS_ACCOUNT_ID>`. content는 presigned PUT을 발급하고 업로드 완료를 검증할 뿐, 바이트를 받지 않는다. 자격 증명은 Pod Identity. 조회는 CloudFront `media.loresentry.com`. **[구축 완료/확인]** AWS 리소스와 DNS. content에는 `MediaStorageService`(서비스 계층)까지 있고 엔드포인트·`image` 테이블은 도메인 개발 때 붙인다. `IMAGE_UPLOAD_S3.md` 참고.

## 6.4 Agent / AI Chat Service

- AI 대화 세션
- 사용자 메시지
- AI 응답 상태 관리
- 응답 스트리밍
- 생성 중단/재시도
- Agent orchestration
- RAG/GraphRAG context 요청

기술 스택 초안: FastAPI

DB: 최초 MongoDB 그림 -> **[변경됨] PostgreSQL**. 논리 DB `ai_chat` (역할 `ai_chat_svc`)

## 6.5 GraphRAG Service

- 그래프 데이터 가공/조회
- RAG/GraphRAG retrieval
- Amazon Neptune 접근
- Content 변경 이벤트를 소비하여 그래프/RAG 데이터 반영하는 역할 후보

기술 스택 초안: FastAPI

DB: Amazon Neptune 클러스터 `lore-sentry-neptune` (writer 엔드포인트, 포트 8182)

## 6.6 Kafka

Kafka는 API request path에 직렬로 삽입되는 gateway가 아니라 **서비스 간 비동기 event bus**다.

주요 사용 후보:

- 파일 생성/수정/삭제 이벤트
- GraphRAG 동기화
- 검색 인덱스 갱신
- eventual consistency가 허용되는 후속 처리
- Saga coordination/choreography

---

# 7. 최초 인프라 다이어그램과 최신 변경점

최초 다이어그램은 다음 요소를 포함했다.

```text
AWS
├── Amazon EKS
├── ALB L7 Load Balancer
├── BFF / Gateway
├── Apache Kafka
├── authentication-server (Spring Boot) -> PostgreSQL
├── content-server        (Spring Boot) -> PostgreSQL
├── ai chat server        (FastAPI)     -> MongoDB
└── graphRAG server       (FastAPI)     -> Amazon Neptune
```

현재 변경/확정 사항:

1. **MongoDB -> PostgreSQL**
2. 한때 `L4 + NGINX Ingress` 구조를 검토했으나 현재는 제외
3. **Cloudflare -> ALB -> Gateway/BFF -> Microservices**로 확정
4. ALB의 역할은 TLS termination + Gateway workload load balancing
5. Gateway의 핵심 역할은 API routing + API Composition
6. Kafka는 비동기 서비스 통신용이며 Gateway의 대체재가 아님

---

# 8. AWS / EKS 인프라

## 8.1 AWS 기본 환경

**[구축 완료/확인]**

- Region: `ap-northeast-2` (서울)
- EKS Cluster: `lore-sentry-k8s`
- VPC: `lore-sentry-vpc`
- VPC ID: `vpc-<REDACTED>`
- 일반 EKS Managed Node Group 사용
- Node instance: `r7i.large` (2 vCPU, 약 16 GiB RAM)

## 8.2 VPC / Subnet

현재 VPC 구조:

```text
Internet
   │
   ▼
Internet Gateway
   │
   ├── Public Subnet A ── ALB
   ├── Public Subnet B ── ALB
   └── Public subnet NAT Gateway
                      │
                      ▼
             Private Subnet A/B
             ├── EKS Worker Nodes
             └── Pods
```

구성:

- Public subnet 2개
- Private subnet 2개
- 2개 Availability Zone
- Worker Node와 Pod는 Private subnet
- Internet-facing ALB는 Public subnet
- NAT Gateway는 Private subnet의 node/pod 인터넷 outbound 용도
- IGW는 VPC 단위 인터넷 연결

## 8.3 EKS networking 핵심

- EKS Control Plane은 AWS 관리 영역에 있다.
- 고객 VPC에 생성되는 ENI는 Control Plane과 VPC 사이 통신용이다.
- AWS VPC CNI가 Pod에 VPC IP를 할당한다.
- kube-proxy는 Service 트래픽을 위한 node kernel rule을 구성한다.
- CoreDNS는 Kubernetes Service name resolution을 제공한다.
- Service는 안정적인 논리 목적지다.
- EndpointSlice는 현재 service backend Pod endpoint 집합을 표현한다.

---

# 9. Kubernetes Namespace 전략

## 9.1 장기 방향

**[확정] 환경을 `dev`와 `prod`로 분리하는 방향을 채택한다.**

장기 개념:

```text
EKS Cluster
├── dev
│   ├── gateway
│   ├── auth
│   ├── content
│   ├── agent
│   └── graph-rag
│
└── prod
    ├── gateway
    ├── auth
    ├── content
    ├── agent
    └── graph-rag
```

여기서 `gateway`, `auth` 등은 별도 namespace가 아니라 같은 namespace 안의 Deployment/Service 단위다.

## 9.2 현재 구현 범위

**[확정] 현재는 `prod`만 실제 구현한다.**

- `prod` namespace를 실제 배포 대상으로 사용
- `dev`는 향후 도입할 환경 개념으로만 남김
- dev manifest / dev Argo CD application / dev branch workflow는 현재 작성하지 않음

## 9.3 Namespace 분리 기준

Namespace는 단순 폴더가 아니라 논리적 정책 경계다.

주요 분리 기준:

- RBAC가 다른가?
- NetworkPolicy가 다른가?
- ResourceQuota/Limit이 다른가?
- Secret/Config가 다른가?
- 배포 lifecycle이 다른가?
- 실수로 상호 접근하면 위험한가?

`dev`와 `prod`는 대부분의 조건이 다르기 때문에 향후 namespace를 분리할 가치가 있다.

다만 namespace는 VM/VPC 수준의 강한 격리가 아니다. 같은 EKS cluster와 worker node를 공유할 수 있다.

---

# 10. 현재 배포 브랜치 전략

**[확정] 현재 시점에서는 `main -> prod`만 구현한다.**

```text
Application Repository
      │
      │ push main
      ▼
GitHub Actions
      │
      ├── test
      ├── image build
      └── ECR push
              │
              ▼
        GitOps repo 갱신
              │
              ▼
           Argo CD
              │
              ▼
        EKS / prod
```

현재는 다음을 만들지 않는다.

- dev branch 기반 배포
- dev용 manifest
- dev용 Argo CD Application
- dev용 별도 overlay

향후 필요할 때 `base + dev/prod overlay` 구조로 확장할 수 있다.

---

# 11. GitOps / CI/CD 설계

## 11.1 전체 흐름

**[확정] CI는 EKS를 직접 수정하지 않는다.**

```text
Developer Git Push
      ↓
GitHub Actions
  test + build
      ↓
Amazon ECR
      ↓
AWS Lambda
  GitOps image tag update
      ↓
GitOps Repository
      ↓
Argo CD
      ↓
EKS RollingUpdate
      ↓
AWS Load Balancer Controller
  Target Group Pod IP update
```

실제 Kubernetes desired state는 GitOps repository가 관리하며, 클러스터 반영 주체는 Argo CD다.

## 11.2 GitOps Repository 구조 — **[구축 완료]** Kustomize

저장소는 `soma-lorekeeper/loresentry-gitops`다.

```text
loresentry-gitops/
├── bootstrap/root-application.yaml
├── argocd-apps/
│   ├── aws-load-balancer-controller.yaml   # wave 애노테이션 없음(=0), ALB 설정도 여기
│   ├── strimzi-kafka-operator.yaml         # wave -5, Kafka CRD 먼저 설치
│   ├── platform.yaml                       # wave -10
│   └── workload-prod.yaml                  # wave 0
├── platform/
│   ├── argocd/ingress.yaml                 # argocd.loresentry.com
│   └── storage/gp3-storage-class.yaml
└── workload/
    ├── base/
    │   ├── gateway/  ai-chat/  authentication/  content/  graph-rag/
    │   ├── auth-valkey/                    # Deployment, Service, ConfigMap
    │   ├── kafka/                          # Kafka, KafkaNodePool, KafkaTopic
    │   └── kustomization.yaml
    └── overlays/
        ├── prod/    # namespace, 이미지 태그, replica, ingress
        └── dev/     # .gitkeep 만 있음
```

이전 판의 "workload별 namespace" 구조는 폐기했다. 모든 애플리케이션 workload는 `prod` namespace에 있고, namespace는 base가 아니라 **overlay가 소유**한다. 그래야 dev/prod가 같은 base를 공유할 수 있다.

세부 규약(서비스 추가 절차, base/overlay 역할 분담)은 `INFRA_AND_CICD.md` §7에 있다.

## 11.3 Argo CD — **[구축 완료]**

- Root Application 적용, App-of-Apps 동작
- `platform`(-10) / `strimzi-kafka-operator`(-5) / `aws-load-balancer-controller`(애노테이션 없음=0) / `workload-prod`(0) 4개 Application
- **Kustomize 모드를 사용한다.** 이전의 "Directory mode + recursive" 방향은 **폐기**했다 — Argo CD는 디렉터리에 `kustomization.yaml`이 있으면 자동으로 Kustomize로 빌드하고, `directory.recurse`는 평범한 디렉터리 모드 옵션이라 둘은 상호 배타적이다. 함께 두면 `failed to discover server resources for group version kustomize.config.k8s.io/v1beta1`로 실패한다.
- 대시보드가 `argocd.loresentry.com`에 공개되어 있고 API와 **같은 ALB**를 공유한다
- **GitHub SSO(Dex)** 연동. `orgs: [soma-lorekeeper]` 필터가 인증 단계에서 조직 멤버만 통과시키므로 "인증 성공 = 조직 멤버"가 성립한다. 조직에 팀이 없어 `groups` 클레임이 비므로 `policy.default: role:admin`으로 기본 역할을 준다

**[추후 검토]** Argo CD 로그인 페이지가 공개 인터넷에 있고 Argo CD admin은 클러스터의 무엇이든 바꿀 수 있다. SSO 확인 후 로컬 `admin` 계정 비활성화가 다음 단계다.

## 11.4 ECR / 이미지 버전

이미지 태그 정책:

```text
build-<GITHUB_RUN_NUMBER>-<GITHUB_RUN_ATTEMPT>
```

예:

```text
build-1-1
build-2-1
build-2-2
```

원칙:

- ECR image tag immutable
- tag 재사용하지 않음
- OCI label에 GitHub SHA 기록

## 11.5 GitHub Actions

각 application repository workflow의 목표 역할:

1. `main` push 감지
2. checkout
3. dependency install
4. test
5. GitHub OIDC로 AWS role 획득
6. ECR login
7. image tag 생성
8. `linux/amd64` image build
9. ECR push
10. Lambda 동기 호출
11. GitOps commit 성공 여부 확인

동일 repository의 오래된 workflow가 새 버전을 덮지 않도록 concurrency 사용:

```yaml
concurrency:
  group: deploy-${{ github.repository }}
  cancel-in-progress: true
```

## 11.6 GitHub -> AWS 인증

**[설계 확정/일부 구축 예정]** Access Key 대신 GitHub OIDC를 사용한다.

범위:

```text
GitHub organization: soma-lorekeeper
```

공용 CI IAM Role이 담당할 권한:

- ECR push
- GitOps update Lambda invoke

## 11.7 Lambda 기반 GitOps image update — **[구축 완료]**

실제 배포값:

```text
Function: loresentry-update-gitops
Runtime: Python 3.13        # 초기 설계는 3.14였다
Memory: 256 MB
Timeout: 30 sec
Handler: lambda_function.lambda_handler
IAM Role: loresentry-update-gitops-lambda-role
VPC 연결: 없음               # 클러스터에 접근할 수 없다. 의도된 것이다
```

환경변수 실제값:

```text
GITHUB_OWNER=soma-lorekeeper
GITOPS_REPO=loresentry-gitops
GITOPS_BRANCH=main
GITOPS_ROOT=workload/overlays/prod
ECR_REGISTRY=<AWS_ACCOUNT_ID>.dkr.ecr.ap-northeast-2.amazonaws.com
GITHUB_TOKEN_SECRET=loresentry/github/gitops-token
```

**Lambda 코드가 버전 관리되지 않는다.** `loresentry-lambda/` 디렉터리에 `lambda_function.py`·`deploy.sh`·테스트가 있으나 git 저장소가 아니다. CI→GitOps 연결 전체를 이 코드가 담당하므로, 유실되면 파이프라인을 복원할 수 없다. 저장소로 만드는 것이 남은 일이다.

입력 예시:

```json
{
  "repository": "graph-rag/api",
  "imageTag": "build-17-1"
}
```

동작:

1. repository/imageTag 검증
2. Secrets Manager에서 GitHub token 조회
3. GitHub Contents API로 manifest 읽기
4. 대상 ECR image line의 tag 수정
5. GitOps main branch commit
6. 409 conflict 발생 시 최신 SHA 재조회 후 retry

---

# 12. AWS Load Balancer Controller / ALB

## 12.1 현재 설계

기존 문서 기준 표준 경로:

```text
ALB Ingress + ClusterIP Service + target-type: ip
```

AWS Load Balancer Controller의 책임:

- Kubernetes Ingress/Service 감시
- AWS API 호출
- ALB/NLB resource 생성/갱신
- Listener / Listener Rule 관리
- Target Group 관리
- EndpointSlice 기반 Pod IP 등록/해제
- 관련 Security Group 규칙 관리

## 12.2 현재 최신 애플리케이션 진입점

**[확정] ALB의 public API backend는 Gateway/BFF가 된다.**

기존처럼 각각의 microservice를 ALB가 직접 public routing하는 구조가 아니라:

```text
Internet
  ↓
Cloudflare
  ↓
ALB
  ↓
Gateway
  ↓
Internal Microservices
```

로 정리한다.

## 12.3 ALB 공통 정책 — **[구축 완료]**

`IngressClassParams`(이름 `alb`)가 ALB 단위 값을 모두 갖는다.

- `group.name: lore-sentry` — 같은 group인 Ingress들이 ALB 하나로 합쳐진다
- `scheme: internet-facing`
- `targetType: ip` — Target Group에 Pod IP가 직접 등록된다
- `ipAddressType: ipv4`
- `certificateArn` — ACM 인증서
- `sslRedirectPort: "443"` — HTTP는 301로 리다이렉트된다
- AWS resource tag

**[변경됨] 이 리소스는 `platform/`이 아니라 aws-load-balancer-controller Application의 Helm values에 있다.** 차트가 만드는 리소스를 `platform/`에서 다시 선언하면 두 Application이 같은 리소스를 두고 다투고, sync wave 순서상 controller가 prune하는 구간에 **ALB가 삭제된다.**

각 Ingress에는 애플리케이션 고유 값만 남긴다: host, path, backend Service, health check, `listen-ports`.

`group.name`이 재구성 중에도 고정돼 있었기 때문에 ALB 주소가 유지됐다. 반대로 이 값을 바꾸면 새 ALB가 생기고 기존 것이 사라진다.

제약 하나: `inbound-cidrs`는 로드밸런서 전체에 작용하므로 group의 한 멤버에만 적용할 수 없다. Argo CD만 IP 제한하려면 별도 ALB로 분리해야 한다.

---

# 13. DNS / TLS

**[구축 완료/확인 + 운영 방향]**

- Domain 관리: Cloudflare
- ACM certificate DNS validation 성공
- Cloudflare DNS가 AWS ALB로 연결
- TLS termination은 ALB에서 수행
- Cloudflare proxy 사용 시 SSL/TLS `Full (strict)` 권장 방향
- wildcard certificate는 root domain까지 자동 포함하지 않으므로 root domain 사용 시 별도 처리 필요

현재 외부 API 흐름:

```text
Browser
  ↓ HTTPS
Cloudflare
  ↓ HTTPS
ALB
  ↓ HTTP or HTTPS (cluster-side policy에 따라)
Gateway
```

---

# 14. 확인된 인프라 구축 진행상황

## 14.1 완료/확인된 내용

클러스터 기반:

- [x] EKS cluster `lore-sentry-k8s`, region `ap-northeast-2`
- [x] 일반 EKS Managed Node Group, `r7i.large` **3대** (`2a` 1 / `2b` 2)
- [x] Worker Node가 private subnet에서 Ready
- [x] 초기 NodeCreationFailure(NAT route 누락) 해결
- [x] VPC CNI 초기 장애(Pod Identity/IAM) 해결
- [x] EBS CSI Driver `ACTIVE` + Pod Identity association
- [x] ACM certificate Cloudflare DNS validation

네트워킹:

- [x] AWS Load Balancer Controller 동작
- [x] IngressClass `alb` + IngressClassParams 적용 (ALB 설정은 controller Helm values에 위치)
- [x] ALB 1개 생성, IngressGroup `lore-sentry`로 API와 Argo CD가 공유
- [x] `api.loresentry.com`, `argocd.loresentry.com` Cloudflare CNAME
- [x] HTTP → HTTPS 리다이렉트 (`sslRedirectPort: "443"`)

GitOps / CI/CD:

- [x] Argo CD Root Application + 4개 Application, Kustomize 모드 (sync wave: `platform` -10 → `strimzi-kafka-operator` -5 → `aws-load-balancer-controller`·`workload-prod` 0)
- [x] Argo CD GitHub SSO (Dex)
- [x] Lambda `loresentry-update-gitops` + Secrets Manager PAT
- [x] GitHub OIDC 신뢰 정책 (immutable subject claim 형식)
- [x] ECR 저장소 5개 (immutable tag, scanOnPush)
- [x] GitHub Actions workflow 5개 성공
- [x] 이미지 push → Lambda commit → Argo CD sync → RollingUpdate 전 구간
- [x] 새 Pod IP가 ALB Target Group에 Healthy로 등록
- [x] gp3 StorageClass `reclaimPolicy: Retain`

애플리케이션:

- [x] 5개 서비스 스켈레톤 배포, 전부 `1/1 Running`
- [x] gateway → 4개 서비스 동기 호출 체인 (공개 HTTPS로 검증)
- [x] gateway 가상 스레드 실제 동작 확인 (`thread` 필드가 `VirtualThread[...]`)
- [x] 업스트림 장애 격리 (한 서비스 down → 해당 경로만 502)
- [x] gateway CORS (`loresentry.com`, 서브도메인, localhost 임의 포트)

인프라 컴포넌트:

- [x] `auth-valkey` (Valkey 9.0.6) — 순수 캐시, 영속성 없음, `emptyDir`
- [x] Strimzi 1.2.0 오퍼레이터 (`strimzi-system`, `watchNamespaces: [prod]`)
- [x] Kafka `lore-sentry` 4.3.1 KRaft, 브로커 3대가 **노드당 1대**로 분산
- [x] 잠정 토픽 `content.file.changed.v1`(6 파티션) / `.dlq`(3 파티션) 둘 다 `Ready`. CRD 동작 확인용이며 토픽 설계 자체는 미확정 (§19.3)
- [x] 브로커 PVC 3개 `Bound`, 10Gi gp3, `deleteClaim: false`

알려진 미해결:

- [ ] `strimzi-kafka-operator`가 `kafkas.kafka.strimzi.io` CRD에서 `OutOfSync` (약 800KB CRD의 diff 문제. 기능은 정상)
- [ ] `auth-valkey` Secret이 Git에 없음 — 손으로 만든 것이라 Git만으로는 클러스터가 재현되지 않는다
- [ ] PostgreSQL이 GitOps 밖에서 적용됨 (`pg-bootstrap` Job이 `prod`에서 `Failed` 후 정리되는 것을 관측). 저장소에 PostgreSQL 매니페스트가 없다
- [ ] Kafka `plain` 리스너에 TLS/인증 없음. `type: internal`이지만 어느 namespace의 Pod든 접근 가능하다
- [ ] `gateway-api` 2 replica가 같은 노드에 있음 (§15.1-A)

## 14.2 아직 만들지 않은 것 — **[미구현]**

- [x] 스키마 마이그레이션. Flyway로 authentication/content/ai-chat 테이블 생성 (`TABLE_AND_LOGIC.md` §9)
- [ ] authentication/content/ai-chat의 영속성 코드. 테이블은 있고 repository·도메인 로직이 없다
- [ ] graph 모델과 RAG/GraphRAG retrieval. **Neptune 클러스터는 준비되어 연결까지 확인됐고, 그래프가 비어 있다**
- [ ] content → graph-rag 이벤트. 브로커/PostgreSQL은 준비됨. 토픽 설계(§19.3), Outbox 테이블, producer가 남았다
- [ ] Google OAuth 로그인 흐름, 토큰 발급
- [ ] Gateway JWT 검증과 내부로의 신원 전달 — **현재 모든 엔드포인트가 인증 없음**
- [ ] AI 응답 스트리밍 패스스루 (read-timeout 및 ALB idle_timeout 상향 필요)
- [ ] 업스트림 retry / circuit breaking
- [ ] 관측성 (로그/메트릭/트레이싱)
- [ ] `dev` overlay와 `workload-dev` Application
- [ ] 각 서비스의 도메인 로직 전체

---

# 15. 현재 prod Kubernetes 구성 — **[구축 완료]**

예상이 아니라 실제 배포된 상태다. 이름 규약은 `<service>-api`로 통일했다.

```text
Namespace: prod

애플리케이션 서비스           replicas   CPU request
├── gateway-api                  2          100m ×2
├── authentication-api           1          100m
├── content-api                  1          100m
├── ai-chat-api                  1          100m
└── graph-rag-api                1          250m

인프라 컴포넌트
├── auth-valkey                  1          100m      Valkey 9.0.6, 순수 캐시
├── lore-sentry-broker (0,1,2)   3          500m ×3   Kafka 4.3.1, KRaft
└── lore-sentry-entity-operator  1          100m      topic/user operator

PodDisruptionBudget
└── gateway-api (minAvailable: 1)

PersistentVolumeClaim
└── data-0-lore-sentry-broker-0/1/2   10Gi gp3, deleteClaim: false

Ingress
└── lore-sentry-public : api.loresentry.com -> gateway-api:80
```

인프라 컴포넌트는 애플리케이션 서비스 규약(`<service>-api`, 포트 8000, `GET /health`)을 **따르지 않는다.** HTTP 서비스가 아니기 때문이다.

외부 노출은 `gateway-api` 하나뿐이고, 나머지는 전부 `ClusterIP`로 클러스터 밖에서 라우팅되지 않는다.

`Namespace: argocd`에는 Argo CD와 `argocd-server` Ingress(`argocd.loresentry.com`)가 있다. 같은 ALB를 공유한다.

## 15.1 용량 제약

노드 그룹은 `r7i.large` **3대**(노드당 allocatable 1930m, 합계 5790m), 2 AZ에 걸쳐 있다.

```text
ip-10-20-3-103  (2a)   1210m / 1930m   62%
ip-10-20-4-14   (2b)   1340m / 1930m   69%
ip-10-20-4-37   (2b)   1190m / 1930m   61%
```

**CPU request가 스케줄링 병목**이고 메모리는 아니다. Kafka 브로커가 각 `500m`으로 가장 큰 소비자다.

초기에는 노드가 1대였고 여유 CPU가 470m뿐이어서, 신규 애플리케이션 서비스를 graph-rag의 `250m` 대신 `100m`으로 잡았다.

**노드가 3대가 된 직접적 원인은 Kafka다.** 브로커 pool이 `topologyKey: kubernetes.io/hostname` + `whenUnsatisfiable: DoNotSchedule`로 노드당 1대를 강제하므로, 세 번째 브로커는 세 번째 노드 없이는 뜨지 못한다.

## 15.1-A 두 spread 제약의 차이 — 배울 점

같은 기능처럼 보이지만 결과가 정반대다.

| | gateway-api | Kafka broker pool |
|---|---|---|
| topologyKey | `topology.kubernetes.io/zone` | `kubernetes.io/hostname` |
| whenUnsatisfiable | `ScheduleAnyway` | `DoNotSchedule` |
| 실제 결과 | 두 replica가 **같은 노드**에 떴다 | 노드당 정확히 1대 |

노드 3대 중 2대가 같은 zone(2b)이라 zone 기준 분산은 느슨하게 만족되고, `ScheduleAnyway`는 애초에 배치를 막지 않는다 — 권고일 뿐이다.

Kafka가 엄격한 이유는 **내구성 주장이 그 배치에 의존**하기 때문이다. 브로커 3개 중 2개가 한 노드에 있으면 노드 하나를 잃는 순간 `min.insync.replicas: 2`를 만족할 수 없고 `acks=all` 프로듀서가 멈춘다. 그러니 브로커가 기존 노드에 끼어 조용히 가정을 무너뜨리는 것보다 `Pending`으로 눈에 보이는 게 낫다.

게이트웨이도 진짜로 노드 하나를 견디게 하려면 zone이 아니라 hostname topologyKey가 필요하다. 현재 PDB(`minAvailable: 1`)도 두 replica가 한 노드에 있으면 노드 드레인을 막아주지 않는다.

## 15.2 내부 서비스 접근

내부 서비스는 외부에서 라우팅되지 않으므로 Ingress를 추가하는 대신 port-forward를 쓴다.

```bash
kubectl port-forward -n prod svc/content-api 8080:80
curl localhost:8080/health
```

gateway의 relay 엔드포인트(`/graph`, `/ai-chat`, `/auth`, `/content`)로도 각 호출 체인을 외부에서 확인할 수 있다.

---

# 16. 데이터 흐름 예시

## 16.1 일반 파일 조회

```text
Browser
  ↓
ALB
  ↓
Gateway
  ↓
Content Service
  ↓
Content DB
```

Kafka를 거치지 않는다.

## 16.2 파일 수정 + GraphRAG 동기화

```text
Browser
  ↓
ALB
  ↓
Gateway
  ↓
Content Service
  │
  ├── DB Transaction
  │      ├── file update
  │      └── outbox record
  │
  ▼
Response

Outbox Publisher
  ↓
Kafka
  ↓
GraphRAG Consumer
  ↓
Neptune / RAG index update
```

이 흐름은 Outbox/Inbox/idempotency를 적용하기 좋은 대표 사례다.

## 16.3 AI Chat 요청

```text
Browser
  ↓
ALB
  ↓
Gateway
  ↓
Agent Service
  ├── Content context 요청
  ├── GraphRAG retrieval 요청
  ├── LLM/Agent 수행
  └── AI response streaming
          ↓
       Gateway
          ↓
       Browser
```

생성 중 연결 종료/중단/재시도를 고려해 AI response 자체에 독립적인 상태 식별자가 필요할 가능성이 높다.

---

# 17. 현재 설계의 핵심 책임 분리

```text
Cloudflare
= 인터넷 edge / DNS / WAF / CDN / DDoS 방어

ALB
= TLS termination + AWS/EKS public L7 entry + Gateway workload load balancing

Gateway / BFF
= client-facing API + routing + API Composition

Microservices
= 각 도메인의 비즈니스 로직

Per-Service DB
= 각 서비스의 데이터 ownership

Kafka
= 비동기 서비스 이벤트 전달

Outbox / Inbox
= DB transaction과 event delivery의 신뢰성 / 멱등성 확보

Saga
= 여러 서비스에 걸친 분산 비즈니스 트랜잭션 관리

Argo CD
= GitOps desired state를 EKS에 반영

GitHub Actions
= test/build/image push + GitOps version update 요청
```

---

# 18. 현재 확정된 설계 결정 요약

## 서비스 / MSA

- [x] EKS 기반 MSA
- [x] Gateway/BFF 도입
- [x] Gateway에서 API Composition
- [x] 서비스별 DB ownership
- [x] Kafka는 비동기 메시징에 선택적으로 사용
- [x] Outbox/Inbox/Saga 학습 및 적용 방향
- [x] Neptune 기반 GraphRAG
- [x] AI Chat DB의 MongoDB 계획은 PostgreSQL로 변경
- [x] SQL 기반 서비스 DB 엔진은 PostgreSQL로 통일

## 외부 트래픽

- [x] `Cloudflare -> ALB -> Gateway -> Microservices`
- [x] ALB에서 TLS termination
- [x] ALB는 Gateway workload에 대한 load balancing
- [x] NGINX Ingress Controller는 현재 구조에 넣지 않음
- [x] ALB 1개를 IngressGroup `lore-sentry`로 공유 (API + Argo CD 대시보드)
- [x] HTTP는 301로 HTTPS 리다이렉트
- [x] ALB 공통 설정은 controller Helm values, 라우팅은 overlay Ingress
- [x] CORS는 gateway 한 곳에서만 (`allowedOriginPatterns` + `allowCredentials: true`)
- [x] gateway는 경로를 명시 선언한다. catch-all `/**` 프록시는 쓰지 않는다

## Kubernetes

- [x] 장기적으로 `dev` / `prod` namespace 분리
- [x] 현재는 `prod`만 구현
- [x] dev용 코드/manifest/workflow는 지금 만들지 않음
- [x] 각 서비스별 namespace 분리는 현재 하지 않음

## CI/CD

- [x] 현재 `main -> prod`
- [x] GitHub Actions -> ECR -> GitOps update -> Argo CD -> EKS
- [x] CI가 EKS를 직접 수정하지 않음
- [x] ECR immutable image tag, `build-<run>-<attempt>` 형식
- [x] GitHub OIDC 기반 AWS 인증 (immutable subject claim 형식으로 신뢰 정책 작성)
- [x] GitOps는 **Kustomize base + overlay**. Argo CD Directory mode는 쓰지 않음
- [x] Lambda는 `overlays/prod/kustomization.yaml`의 `images[].newTag` 한 값만 고침
- [x] 롤백은 GitOps commit `git revert`
- [x] 서비스 규약 통일: ECR `<service>/api`, 리소스 `<service>-api`, 포트 8000, `GET /health`
- [x] 의존성은 정확한 버전으로 pin (`==`, `>=` 아님)

## 스토리지

- [x] `gp3` 기본 StorageClass, `reclaimPolicy: Retain` — PVC를 지워도 EBS 볼륨이 남는다
- [x] `reclaimPolicy`는 immutable이므로 `argocd.argoproj.io/sync-options: Replace=true,Force=true` 필요
- [x] 사용자 이미지는 프론트엔드와 **별도** S3 버킷 + 별도 CloudFront `media.loresentry.com`. 브라우저가 presigned URL로 직접 업로드. content pod는 Pod Identity로 서명 (`IMAGE_UPLOAD_S3.md`)

---

# 19. 아직 추가 설계가 필요한 영역

다음 항목은 이후 질문에서 구체화할 필요가 있다.

## 19.1 서비스 경계

- 프로젝트/파일/메모/버전/검색을 모두 Content Service에 둘지
- 검색을 별도 Search Service로 분리할지
- 프로젝트/권한을 Authentication Service와 어느 수준까지 분리할지
- GraphRAG와 Agent의 책임 경계를 어디에 둘지

## 19.2 인증

**현재 모든 엔드포인트가 인증 없이 열려 있다.** 다음을 정해야 한다.

- Google OAuth를 누가 직접 처리할지
- Gateway가 access token을 검증할지
- Authentication Service에 introspection을 요청할지
- JWT / session token 구조
- 서비스 간 사용자 인증 context 전달 방식

CORS는 이미 gateway에 있으므로 인증도 같은 경계에 두는 것이 자연스럽다. `allowCredentials: true`로 이미 열어 둔 것은 쿠키 기반 세션을 염두에 둔 것이다.

## 19.3 Kafka

**[확정] self-managed Kafka on EKS, Strimzi 1.2.0 / Kafka 4.3.1.** 브로커 3대 KRaft, RF 3 / `min.insync.replicas` 2. 토픽은 `KafkaTopic` CRD로 GitOps 저장소에 선언한다. 구체적 구성은 `INFRA_AND_CICD.md` 19절.

**[미확정] 토픽 설계.** 클러스터에 있는 `content.file.changed.v1`과 `.dlq`는 `KafkaTopic` CRD 동작을 확인하려고 만든 잠정 토픽이다. 어떤 이벤트를 어떤 토픽으로 보낼지, 토픽을 몇 개 둘지, 파티션 키를 무엇으로 할지는 정하지 않았다. 파티션 키 후보로 projectId가 거론된 근거는 프로젝트 단위 순서만 지키면 graph-rag의 참조 그래프가 수렴하고, fileId로 잡으면 같은 프로젝트의 변경이 파티션에 흩어져 순서가 깨진다는 점이다. 근거일 뿐 결정은 아니다.

남은 결정:

- 어떤 도메인 이벤트를 발행할지, 이벤트와 토픽의 대응
- topic naming
- partition key
- event schema/versioning
- retry / DLQ
- Outbox publisher 구현
- Inbox table/idempotency key 구현

## 19.4 데이터베이스 — **[대부분 해소됨]**

제품과 분리 방식은 확정했다. 구체적인 리소스 식별자·SG·검증 결과는 `INFRA_AND_CICD.md` §19-B에 있다.

**논리 스키마와 PostgreSQL/Neptune 저장 경계는 [`TABLE_AND_LOGIC.md`](TABLE_AND_LOGIC.md)를 기준으로 한다.** Content PostgreSQL이 프로젝트·파일·명시적 참조의 원본이고, Neptune은 Kafka 이벤트로 재생성 가능한 활성 파일 관계의 투영본이다.

- **[확정] RDS PostgreSQL 18.6** (Aurora 아님). `lore-sentry-postgres`, `db.t4g.micro`, Single-AZ, gp3 20GB, 프라이빗 전용
- **[확정] 물리 분리가 아니라 인스턴스 1개 + 논리 DB 3개로 시작한다.** Database per Service의 요점은 소유권 분리이지 물리 분리가 아니고, 현 단계에서 인스턴스 3대는 비용만 3배다. 논리 DB(`authentication`/`content`/`ai_chat`)마다 소유자 역할을 두고 `REVOKE CONNECT ... FROM PUBLIC`으로 교차 접근을 차단했다 — 다른 서비스 역할로는 **접속 자체가 거부된다**
- **[확정] Amazon Neptune 1.4.8.0** `lore-sentry-neptune`, `db.t4g.medium` writer 1노드. `db.t4g.medium`이 Neptune의 최소 사양이다(t3/t4g는 medium 사이즈만 제공)
- **[확정] connection pool** — Spring은 Hikari `maximum-pool-size: 5`, `initialization-fail-timeout: -1`. 후자는 DB가 죽어도 컨테이너가 살아서 `/health/db`로 이유를 보고하게 하려는 것이다
- **[확정] backup** — RDS/Neptune 모두 보관 7일 + deletion protection
- **[확정] migration 도구 — Flyway.** Spring 서비스는 기동 시 자동 실행, ai-chat(FastAPI)도 언어를 섞지 않도록 Alembic 대신 이미지에 Flyway CLI를 넣어 entrypoint에서 실행한다. 적용된 버전은 `TABLE_AND_LOGIC.md` §9
- **[추후 검토] 서비스별 물리 분리 시점.** 특정 서비스만 부하가 커지면 그 DB만 `pg_dump`로 떼어 별도 인스턴스로 옮긴다. 애플리케이션에서 바뀌는 값은 `DB_HOST` 하나다
- **[주의] 이 AWS 계정은 Innovation Sandbox다.** 리스 만료·예산 초과 시 리소스가 자동 정리되므로 여기 쌓은 데이터의 영구 보존을 기대할 수 없다

## 19.5 AI / GraphRAG

- 어떤 데이터를 vector embedding할지
- Neptune에 어떤 node/edge model을 사용할지
- file update event와 GraphRAG update의 consistency 목표
- LLM provider
- streaming protocol: SSE vs WebSocket

## 19.5-A 로컬 개발 규약 — **[해소됨]**

포트 규약은 **8000**으로 통일했다. 프론트엔드 `compose.local.yml`이 `8080`을 가정하고 있었으나 `NEXT_PUBLIC_API_BASE_URL: http://localhost:8000`으로 맞췄다. 플랫폼 5개 서비스를 8080으로 옮기는 대안보다 한 줄 수정이 싸다.

```text
프론트엔드 dev server : localhost:3000   (컨테이너 3000 매핑)
게이트웨이            : localhost:8000
내부 서비스           : 8000 (컨테이너 포트, Service 는 80)
```

CORS는 `http://localhost:[*]`로 열려 있으므로 포트를 다시 바꾸더라도 브라우저 쪽은 막히지 않는다.

주의할 점 하나: `NEXT_PUBLIC_*`은 클라이언트 번들에 박히는 값이므로 `localhost:8000`은 **브라우저(호스트)** 기준으로 해석된다. 프론트엔드는 컨테이너 안에서 돌지만 요청은 호스트에서 나가므로 이게 맞다. 반대로 Next.js **서버 사이드**에서 이 값으로 fetch하게 되면 컨테이너 안의 `localhost`라서 게이트웨이에 닿지 않는다. 그때는 서버용 base URL을 따로 두어야 한다 (`host.docker.internal` 등). 현재 이 변수를 참조하는 코드는 없다.

## 19.6 관측성

- application logs
- metrics
- distributed tracing
- OpenTelemetry
- Prometheus/Grafana 또는 AWS native monitoring
- request correlation ID

## 19.7 보안

- Secret 관리
- NetworkPolicy
- Pod Security
- IAM / Pod Identity
- ~~RDS/Neptune SG 접근 제한~~ — **[해소됨]** 양쪽 모두 프라이빗 서브넷 전용이고 퍼블릭 액세스를 껐다. inbound는 EKS 클러스터 SG `sg-<CLUSTER>` 한 곳만 허용한다 (PostgreSQL 5432, Neptune 8182). CIDR은 열지 않았다
- DB 자격 증명 관리 — 서비스별 Secret 3개가 아직 `kubectl`로 만든 Git 밖 Secret이다. External Secrets Operator + Secrets Manager로 옮기는 것이 다음 단계
- Neptune IAM 데이터베이스 인증 — 현재 비활성이라 SG만이 접근 통제 수단이다
- WAF policy
- 이미지 업로드 인가 — `/projects/{id}/images`는 다른 엔드포인트와 마찬가지로 아직 인증이 없다. 미디어 버킷 CORS는 gateway 단일 CORS 원칙의 의도적 예외 (`IMAGE_UPLOAD_S3.md` §6·§7)

---

# 20. 현재 프로젝트를 한 장으로 정리

```text
                                     Internet
                                        │
                                        ▼
                                  ┌────────────┐
                                  │ Cloudflare │
                                  └─────┬──────┘
                                        │ HTTPS
                                        ▼
                                  ┌────────────┐
                                  │  AWS ALB   │
                                  │ TLS Term.  │
                                  └─────┬──────┘
                                        │
                                        ▼
                         ┌─────────────────────────┐
                         │      Gateway / BFF      │
                         │ routing + composition   │
                         │ CORS + (예정) 인증       │
                         │ Spring Boot 4.1 · MVC   │
                         │ + Virtual Threads       │
                         └────────────┬────────────┘
                                      │
                 ┌────────────────────┼────────────────────┐
                 │                    │                    │
                 ▼                    ▼                    ▼
       ┌─────────────────┐  ┌─────────────────┐  ┌────────────────┐
       │ Authentication  │  │     Content     │  │ Agent / AI Chat│
       │ Spring Boot 4.1 │  │ Spring Boot 4.1 │  │ FastAPI 0.141  │
       └────────┬────────┘  └───────┬─────────┘  └───────┬────────┘
                │                   │                     │
                ▼                   ▼                     │
           PostgreSQL          PostgreSQL                 │
                                    │                     │
                                    │ domain event        │
                                    ▼                     │
                              ┌───────────┐               │
                              │   Kafka   │               │
                              └─────┬─────┘               │
                                    │                     │
                                    ▼                     ▼
                             ┌─────────────────────────────┐
                             │      GraphRAG Service       │
                             │       FastAPI 0.141         │
                             └─────────────┬───────────────┘
                                           │
                                           ▼
                                   ┌──────────────┐
                                   │Amazon Neptune│
                                   │   Graph DB   │
                                   └──────────────┘

EKS namespace: prod (현재 구현 대상)
future namespace: dev

CI/CD:
main push
  -> GitHub Actions
  -> ECR
  -> Lambda updates GitOps repository (Kustomize overlay images[].newTag)
  -> Argo CD
  -> EKS prod RollingUpdate
```

## 20.1 지금 실제로 동작하는 것 / 아직 아닌 것

위 그림에서 **실선으로 살아 있는 부분**:

```text
Internet → Cloudflare → ALB → Gateway → 4개 서비스   ✅ 검증 완료
main push → Actions → ECR → Lambda → Argo CD → EKS   ✅ 5개 저장소 검증 완료
Gateway → 4개 서비스 → PostgreSQL / Neptune          ✅ /health/db 200 확인
```

**아직 그림뿐인 부분:**

```text
PostgreSQL      △  RDS 18.6 + 논리 DB 3개 Ready, 테이블 생성됨. 읽기/쓰기 코드 없음
Amazon Neptune  △  클러스터 Ready, 연결 확인. 그래프 모델/쿼리 코드 없음
Kafka           △  브로커 3대 + 토픽 2개는 Ready. producer/consumer 코드 없음
auth-valkey     △  캐시 서버는 Ready. authentication 서비스가 아직 연결 안 함
마이그레이션    ✅ Flyway. 3개 서비스 기동 시 적용
인증            ❌ 모든 엔드포인트가 무인증
Frontend        ✅ S3 + CloudFront, https://loresentry.com (클러스터 밖)
```

DB 연결은 **드라이버와 `/health/db`까지만** 넣었고 JPA/ORM 매핑은 넣지 않았다. 기동 실패를 피하는 방식도 정해져 있다 — `spring-boot-starter-jdbc`를 쓰면서 Hikari `initialization-fail-timeout: -1`을 두면 DB가 내려가도 컨테이너는 살아서 `/health/db`가 503으로 이유를 말한다. `/health`(liveness/readiness 대상)는 DB를 건드리지 않으므로 **DB 장애가 CrashLoopBackOff로 번지지 않는다.** 이 분리가 없으면 CI는 초록불인데 배포만 실패하는, 가장 진단하기 싫은 형태가 된다.

---

# 21. 현재 단계에서의 운영 원칙

1. **Git을 배포 상태의 Source of Truth로 사용한다.**
2. CI가 직접 `kubectl`로 production을 수정하지 않는다.
3. Argo CD가 Kubernetes desired state를 적용한다.
4. ECR image tag는 immutable하게 운영한다.
5. 외부 API는 Gateway를 통해서만 내부 서비스로 접근한다.
6. 마이크로서비스는 다른 서비스 DB를 직접 읽거나 쓰지 않는다.
7. 사용자 요청의 동기 경로와 Kafka 비동기 이벤트 경로를 구분한다.
8. prod와 dev는 장기적으로 분리하되 현재 구현은 prod/main만 진행한다.
9. 초기 팀 프로젝트 규모에서는 불필요한 인프라 layer를 추가하지 않는다.
10. MSA 패턴은 실제 도메인 문제를 해결하는 곳에 적용하고, 패턴 자체를 위한 과도한 분산은 피한다.

---

# 22. 이후 대화에서의 기본 전제

특별히 다시 변경하지 않는 한 이후 질문에서는 다음을 기본 전제로 사용한다.

```text
Cloudflare
  -> ALB (1개, IngressGroup lore-sentry)
  -> Gateway/BFF        ← 유일한 공개 진입점, CORS/인증 경계
  -> Microservices      ← 전부 ClusterIP

Kubernetes:
  EKS lore-sentry-k8s / prod namespace
  노드 r7i.large 1대 — CPU request가 스케줄링 병목

Deployment:
  main
  -> GitHub Actions
  -> ECR (immutable tag: build-<run>-<attempt>)
  -> Lambda: GitOps overlay 의 images[].newTag 한 줄
  -> Argo CD (Kustomize 모드)
  -> EKS prod

Repositories:
  loresentry-gateway / -authentication / -content / -ai-chat / -graph-rag
  loresentry-gitops (GitOps)
  loresentry-frontend (미배포)
  loresentry-lambda (로컬 워크스페이스, 독립 저장소 아님)

Conventions:
  ECR <service>/api · 리소스 <service>-api · 포트 8000 · GET /health

Stack:
  Java 21 + Spring Boot 4.1.1 (WebMVC + Virtual Threads)
  Python 3.12 + FastAPI 0.141.1

Messaging:
  synchronous API when immediate result is required
  Kafka for asynchronous domain events   ← 브로커 3대 Ready, producer 없음

Data:
  Database per Service (PostgreSQL 18.6) ← 인스턴스 1개 + 논리 DB 3개, 연결 확인
  Neptune for graph/GraphRAG (1.4.8.0)   ← writer 1노드, 연결 확인
  PostgreSQL 테이블은 Flyway로 생성됨, Neptune 그래프는 비어 있음

MSA patterns:
  Outbox / Inbox / Saga where appropriate
```

이후 설계 변경이 발생하면 이 문서를 기준 문서로 갱신한다.
