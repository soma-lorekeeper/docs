# 부하 테스트 — 동시 사용자 수

> 최신화: 2026-10-31
> 대상 환경: dev (`https://api-dev.loresentry.com`, EKS `lore-sentry-dev-k8s`, namespace `dev`)
> 상태: **계획.** 아직 실행하지 않았다. 스크립트는 `loresentry-gitops/loadtest/`에 둔다.
> 관련: 환경은 [DEV_CLUSTER.md](DEV_CLUSTER.md), 요청 규칙과 테스트 사용자 만들기는 `loresentry-gitops/loadtest/README.md`

## 0. 한눈에 보기

| 항목 | 내용 |
|---|---|
| 질문 | 지금 구성(prod와 같은 dev)으로 **동시에 작업하는 작가 몇 명까지** 합격 기준을 지키나. 그때 처음 막히는 곳은 어디인가 |
| 도구 | k6 (`grafana/k6:1.0.0` 컨테이너) |
| 실행 위치 | 같은 리전(`ap-northeast-2`)의 공인 IP EC2 1대 |
| 방법 | 가상 작가(VU)를 25 → 50 → 100 → 150 → 200 → 300 → 400명으로 늘리며 단계마다 6분씩 돌리고, 단계마다 기준을 판정한다 |
| 답 | 기준을 모두 지킨 가장 높은 단계. 그다음 단계에서 먼저 한계에 닿은 자원이 첫 병목이다 |
| 소요 | 준비 1시간, 실행 약 1시간 |

## 1. "동시 사용자"의 정의

- 동시 사용자 1명은 로그인해서 작업 중인 작가 1명이고, k6의 VU 1개다.
- VU는 실제 작가처럼 행동 사이에 쉰다. 쉬는 시간이 결과를 크게 바꾸므로 **"동시 사용자 N명"은 항상 §2의 행동 모델 기준**이다.
  결과를 보고할 때 행동 모델과 초당 요청 수를 함께 적는다.
- 이 질문에는 VU 수를 단계별로 늘리는 방식이 맞다. 초당 요청 수의 한계를 찾을 때는 요청률을 고정해 올리는 방식
  (`ramping-arrival-rate`)을 쓰지만, 여기서는 사람 수가 변수다. 행동 사이에 수십 초씩 쉬므로 서버가 느려져도 요청률이 거의
  줄지 않는다.

## 2. 가상 작가의 행동

prod 트래픽 통계가 없어서 아래 비율은 **가정**이다. prod ALB 요청 수나 GA 자료가 생기면 맞춘다.

한 번의 반복(약 65초)은 다음과 같다.

| 순서 | 행동 | 요청 | 빈도 | 쉬는 시간 |
|---|---|---|---|---|
| 1 | 홈 | `GET /auth/users/me`, `GET /projects`, `GET /chat/usage` | 매번 | 3~5초 |
| 2 | 프로젝트 열기 | `GET /projects/{id}/files`, `GET /projects/{id}/favorites`, `GET /projects/{id}/workspace-state` | 매번 | 3~5초 |
| 3 | 문서 열기 | `GET /files/{id}/content` (자기 프로젝트의 문서 하나를 무작위로) | 매번 | 5~10초 |
| 4 | 글쓰기 | `PUT /files/{id}/content` 3번 (`If-Match: "<revision_no>"`, `X-Save-Id`) | 매번 | 저장 사이 10~20초 |
| 5 | 관계 그래프 | `GET /projects/{id}/graph` | 30% | 5초 |
| 6 | 검색 | `GET /projects/{id}/search?q=…` | 20% | 5초 |
| 7 | AI 챗 질문 | `POST /chat/sessions/{sid}/messages` (SSE를 끝까지 받는다) | 10% | 10초 |

- 반복 한 번에 요청이 약 11개이므로 **VU 1명은 초당 약 0.16건**을 보낸다. 100명은 약 16건, 400명은 약 65건이다.
- 저장은 Content DB에 쓰고 outbox → Kafka → graph-rag → Neptune으로 이어진다. 그래서 저장 지연과 함께 **그래프 반영이 얼마나
  밀리는지**(Kafka consumer lag)를 본다.
- AI 챗은 openai-mock이 답한다. 한 턴에 약 5~14초 걸리고 그동안 SSE 연결을 붙잡는다.
- VU마다 **다른 사용자, 다른 프로젝트**를 쓴다. 사용자당 동시에 답 하나만 받는 제한(409)과 저장 충돌을 피하려는 것이다.
- 저장은 본문 끝의 한 줄만 바꾼다. 반복해도 문서가 커지지 않는다.

## 3. 합격 기준 (예시 — 팀이 정한다)

| 지표 | 기준 |
|---|---|
| 오류율 `http_req_failed` | 1% 미만 |
| 읽기 p95 (`kind:read`) | 500ms 미만 |
| 저장 p95 (`kind:save`) | 1초 미만 |
| AI 챗 첫 바이트 p95 (`http_req_waiting{kind:chat}`) | 25명 단계의 2배 이하. mock 지연이 들어 있어 절대값 대신 기준선 대비로 본다 |
| 서버 | 파드 재시작·`OOMKilled` 0 |
| 부하 발생기 | EC2 CPU 70% 미만. 넘으면 **그 단계 결과는 무효**다(측정기가 병목이 된다) |

## 4. 부하 모양

```text
예열(25명 3분, 버림) → 25 → 50 → 100 → 150 → 200 → 300 → 400
                       └ 단계마다 1분 증가 + 5분 유지, 단계 사이 3분 휴식
```

- **예열:** 25명으로 3분 돌리고 버린다. JVM 서비스(gateway·authentication·content)가 처음 몇 분 동안 느리기 때문이다.
- **단계마다 k6를 따로 실행한다.** 단계별 리포트가 깔끔하게 나뉘고, 사이에 쉬면서 Kafka lag과 연결이 정리된다.
- **기준이 깨지면 멈춘다.** 예를 들어 150은 통과하고 200이 실패하면, 175로 한 번 더 돌려 범위를 좁힌다.
- 판정에는 1분 증가 구간이 포함된다. 그 구간의 부하가 낮아 결과가 조금 낙관적이므로, 애매한 단계는 HTML 리포트의 시간
  그래프에서 유지 구간만 다시 본다.

## 5. 준비

### 5.1 dev 상태 확인 (노트북)

```bash
kubectl config current-context        # lore-sentry-dev 여야 한다
kubectl --context lore-sentry-dev -n dev get pods -o wide        # 전부 Ready, 어느 노드에 있는지 기록
kubectl --context lore-sentry-dev -n dev get deploy \
  -o custom-columns=NAME:.metadata.name,IMAGE:.spec.template.spec.containers[0].image   # 이미지 태그 기록
```

- **테스트 중에는 dev 배포를 멈춘다.** 각 저장소 `dev` 브랜치에 push하지 않도록 팀에 알린다. 중간에 롤링 업데이트가 일어나면 그
  단계 결과를 버려야 한다.
- 결과가 prod에 그대로 적용되려면 prod와 같은 코드로 잰다. `main` → `dev` 머지 또는 `bootstrap/dev/sync-images.sh`로 맞추고,
  어느 쪽이든 **잰 이미지 태그를 결과와 함께 적는다.**
- 파드 배치도 결과에 영향을 준다. 2026-10-31 기준으로 gateway 두 개가 같은 노드에 있다. 배치를 그대로 두고 재되, 기록은 남긴다.

### 5.2 테스트 데이터 (노트북)

```bash
cd loresentry-gitops
loadtest/seed-sessions.sh 450         # 최대 VU 400 + 여유. 사용자마다 로그인 세션이 생긴다
k6 run -e USERS=450 loadtest/prepare.js   # 사용자마다 샘플 프로젝트 1개
```

- `prepare.js`는 `shared-iterations`(VU 10개, 반복 450번)로 사용자를 하나씩 맡아, 프로젝트가 없으면
  `POST /projects/sample?locale=ko`를 부른다.
- 샘플 프로젝트를 만들면 graph-rag·요약기가 돈다. Grafana에서 **Kafka consumer lag이 0이 된 뒤에** 본 테스트를 시작한다.
- 샘플 프로젝트는 작다. 큰 작품(원고 100화 수준)에서의 결과는 따로 잰다.
- `.sessions.csv`는 로그인 자격이다. EC2로 옮긴 뒤 테스트가 끝나면 EC2와 함께 지운다. 세션은 14일 뒤 만료된다.

### 5.3 EC2 부하 발생기 만들기 (노트북)

| 항목 | 값 | 이유 |
|---|---|---|
| 리전 | `ap-northeast-2` | 네트워크 지연을 줄여 서버 쪽 한계만 본다 |
| 타입 | `c7i.xlarge` (4 vCPU, 8GiB) | VU 400과 SSE 연결 수십 개에 여유가 있다. vCPU 쿼터가 빠듯하면 `c7i.large` |
| AMI | Amazon Linux 2023 | |
| 네트워크 | 공유 VPC의 **public 서브넷 + 공인 IP** | private 서브넷에 두면 prod와 공유하는 NAT를 지난다. **금지** |
| 보안 그룹 | 인바운드 22번을 내 IP에만. 아웃바운드 전체 | prod 노드처럼 22번을 전 세계에 열지 않는다 |
| 비용 | 시간당 약 $0.2 | prod·dev와 같은 ISB 예산에서 나간다. 끝나면 바로 종료 |

```bash
# 온디맨드 Standard 인스턴스 vCPU 쿼터. prod·dev 노드 6대가 이미 12 vCPU를 쓴다
aws service-quotas get-service-quota --profile lorekeeper --region ap-northeast-2 \
  --service-code ec2 --quota-code L-1216C47A --query Quota.Value

aws ec2 create-key-pair --profile lorekeeper --region ap-northeast-2 \
  --key-name loresentry-loadgen --query KeyMaterial --output text > ~/.ssh/loresentry-loadgen.pem
chmod 600 ~/.ssh/loresentry-loadgen.pem

SG=$(aws ec2 create-security-group --profile lorekeeper --region ap-northeast-2 \
  --group-name loresentry-loadgen --description "k6 load generator" --vpc-id vpc-<REDACTED> \
  --query GroupId --output text)
aws ec2 authorize-security-group-ingress --profile lorekeeper --region ap-northeast-2 \
  --group-id "$SG" --protocol tcp --port 22 --cidr "$(curl -s https://checkip.amazonaws.com)/32"

aws ec2 run-instances --profile lorekeeper --region ap-northeast-2 \
  --image-id resolve:ssm:/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 \
  --instance-type c7i.xlarge \
  --subnet-id subnet-<PUBLIC_2A> --associate-public-ip-address \
  --security-group-ids "$SG" --key-name loresentry-loadgen \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=loresentry-loadgen}]'
```

```bash
# 스크립트와 세션 옮기기
scp -i ~/.ssh/loresentry-loadgen.pem -r loresentry-gitops/loadtest ec2-user@<EC2_IP>:~/loadtest

# EC2 에서
sudo dnf install -y docker && sudo systemctl enable --now docker && sudo usermod -aG docker ec2-user
# 다시 로그인
```

## 6. 스크립트 — `loadtest/concurrent-users.js` 핵심

요청 규칙(쿠키 `cookies` 옵션의 `replace: true`, 상태 변경 요청의 `Origin`·`X-LS-CSRF`)은 `smoke.js`와 같다.

```js
import http from "k6/http";
import { check, sleep } from "k6";
import { SharedArray } from "k6/data";
import { uuidv4, randomIntBetween } from "https://jslib.k6.io/k6-utils/1.4.0/index.js";

const BASE = __ENV.BASE_URL || "https://api-dev.loresentry.com";
const ORIGIN = "https://dev.loresentry.com";
if (/^https:\/\/api\.loresentry\.com/.test(BASE)) throw new Error("refusing to load-test the production API");

const sessions = new SharedArray("sessions", () =>
  open("./.sessions.csv").trim().split("\n").slice(1).map((line) => line.split(",")[1]),
);

export const options = {
  scenarios: {
    writers: {
      executor: "ramping-vus",
      stages: [
        { duration: __ENV.RAMP || "1m", target: Number(__ENV.VUS) },
        { duration: __ENV.HOLD || "5m", target: Number(__ENV.VUS) },
      ],
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.01"],
    "http_req_duration{kind:read}": ["p(95)<500"],
    "http_req_duration{kind:save}": ["p(95)<1000"],
  },
};

function p(kind, mutate = false, headers = {}) {
  if (mutate) Object.assign(headers, { Origin: ORIGIN, "X-LS-CSRF": "1", "Content-Type": "application/json" });
  const session = sessions[__VU - 1];
  return { headers, cookies: { "__Host-ls_session": { value: session, replace: true } }, tags: { kind }, timeout: "60s" };
}

let me = null;

function init() {
  const project = http.get(`${BASE}/projects`, p("read")).json("projects")[0];
  const docIds = http.get(`${BASE}/projects/${project.id}/files`, p("read"))
    .json("documents").filter((d) => !d.trashed_at).map((d) => d.id);
  const existing = http.get(`${BASE}/projects/${project.id}/chat/sessions`, p("read")).json("sessions");
  const chat = existing.length ? existing[0]
    : http.post(`${BASE}/projects/${project.id}/chat/sessions`, null, p("write", true)).json();
  return { projectId: project.id, docIds, chatId: chat.id };
}

export default function () {
  if (!me) me = init();
  const { projectId, docIds, chatId } = me;

  http.get(`${BASE}/auth/users/me`, p("read"));
  http.get(`${BASE}/projects`, p("read"));
  http.get(`${BASE}/chat/usage`, p("read"));
  sleep(randomIntBetween(3, 5));

  http.get(`${BASE}/projects/${projectId}/files`, p("read"));
  http.get(`${BASE}/projects/${projectId}/favorites`, p("read"));
  http.get(`${BASE}/projects/${projectId}/workspace-state`, p("read"));
  sleep(randomIntBetween(3, 5));

  const docId = docIds[randomIntBetween(0, docIds.length - 1)];
  let doc = http.get(`${BASE}/files/${docId}/content`, p("read")).json();
  sleep(randomIntBetween(5, 10));

  for (let i = 0; i < 3; i++) {
    const body = doc.body_md.replace(/\n\n부하 테스트 \d+$/, "") + `\n\n부하 테스트 ${Date.now()}`;
    const res = http.put(
      `${BASE}/files/${docId}/content`,
      JSON.stringify({ title: doc.title, body_md: body, properties: doc.properties, relations: doc.relations }),
      p("save", true, { "If-Match": `"${doc.revision_no}"`, "X-Save-Id": uuidv4() }),
    );
    if (check(res, { "save 200": (r) => r.status === 200 })) doc = res.json();
    sleep(randomIntBetween(10, 20));
  }

  if (Math.random() < 0.3) {
    http.get(`${BASE}/projects/${projectId}/graph`, p("read"));
    sleep(5);
  }
  if (Math.random() < 0.2) {
    http.get(`${BASE}/projects/${projectId}/search?q=${encodeURIComponent("이야기")}`, p("read"));
    sleep(5);
  }
  if (Math.random() < 0.1) {
    const res = http.post(
      `${BASE}/chat/sessions/${chatId}/messages`,
      JSON.stringify({ content: "주인공이 처음 등장하는 장면을 알려줘", intent: "ask", client_message_id: uuidv4() }),
      p("chat", true),
    );
    check(res, { "chat 200": (r) => r.status === 200 });
    sleep(10);
  }
}
```

- 사용자 수보다 VU가 많으면 `sessions[__VU - 1]`이 비어 실패한다. 시드 사용자를 최대 VU 이상으로 만든다.
- k6의 `http.post`는 SSE를 스트림이 끝날 때까지 받는다. 그래서 챗은 전체 시간(`http_req_duration`)이 아니라 첫 바이트
  (`http_req_waiting`)로 판정하고, 읽기·저장 기준에서는 태그로 뺀다.

## 7. 실행 (EC2)

```bash
cd ~/loadtest && mkdir -p results
k6() { docker run --rm --network host --ulimit nofile=65535:65535 -v "$PWD:/s" -w /s "$@" grafana/k6:1.0.0 run concurrent-users.js; }

k6 -e VUS=25 -e RAMP=30s -e HOLD=3m                         # 예열. 결과는 버린다
sleep 180

set -o pipefail
for VUS in 25 50 100 150 200 300 400; do
  echo "$VUS start $(date -u +%FT%TZ)" >> results/timeline.txt
  k6 -e VUS=$VUS -e K6_WEB_DASHBOARD=true -e K6_WEB_DASHBOARD_EXPORT=results/vus-$VUS.html \
    | tee results/vus-$VUS.log
  STATUS=$?
  echo "$VUS end $(date -u +%FT%TZ) exit=$STATUS" >> results/timeline.txt
  [ "$STATUS" -eq 0 ] || break                             # 기준이 깨지면(k6 종료 코드 99) 멈춘다
  sleep 180
done
```

- 실행하는 동안 다른 터미널에서 `top`으로 EC2 CPU를 본다. 70%를 넘으면 그 단계는 무효다.
- `timeline.txt`의 UTC 시각으로 Grafana·CloudWatch의 같은 구간을 찾는다(Grafana 화면은 KST일 수 있다).
- 끝나면 결과를 노트북으로 가져온다.
  ```bash
  scp -i ~/.ssh/loresentry-loadgen.pem -r ec2-user@<EC2_IP>:~/loadtest/results ./load-test-$(date +%m%d)
  ```

## 8. 볼 곳

| 어디 | 무엇 | 의미 |
|---|---|---|
| k6 HTML 리포트 | 단계별 p95·p99, 오류율, 초당 요청 수 | 합격 판정 |
| Grafana-dev | 노드 CPU, 파드 CPU·**CPU throttling**·메모리·재시작 | 어떤 파드가 먼저 limit에 닿나 |
| Grafana-dev | Kafka consumer lag | 저장 뒤 그래프 반영이 밀리나 |
| CloudWatch ALB (`k8s-loresentrydev-…`) | `RequestCount`, `TargetResponseTime`, `HTTPCode_Target_5XX_Count` | 서버 쪽 지연. k6 지연과의 차이는 네트워크와 발생기 몫이다 |
| CloudWatch RDS `lore-sentry-dev-postgres` | `CPUUtilization`, `DatabaseConnections`, `ReadLatency`·`WriteLatency`, `CPUCreditBalance` | DB가 병목인지 |
| CloudWatch Neptune | `CPUUtilization` | 그래프 조회·갱신 |
| Loki | `{namespace="dev"} |= "ERROR"`, `Connection is not available` | 오류의 원인, DB 커넥션 풀 고갈 |
| EC2 | CPU | 70% 넘으면 그 단계 무효 |

앱 자체의 HTTP 지표(ServiceMonitor)는 없다. 엔드포인트별 서버 지연은 ALB와 k6로 본다.

RDS·Neptune은 T 계열(버스트형)이다. 크레딧이 바닥났을 때 느려지는지(Standard 모드) 초과 요금이 붙는지(Unlimited 모드)는 아직
확인하지 않았다. 단계당 6분인 이번 테스트에는 영향이 작지만, `CPUCreditBalance`는 같이 기록한다.

## 9. 결과 해석과 기록

**답은 기준을 모두 지킨 가장 높은 단계다.** 예: "동시 작가 150명 (§2 행동 모델, 초당 약 24건, 이미지 태그 …)".

**첫 병목**은 기준이 처음 깨진 단계에서 가장 먼저 한계에 닿은 자원이다. 확인 전 후보는 다음과 같다.

| 후보 | 이렇게 보이면 의심한다 |
|---|---|
| content·authentication의 DB 커넥션 풀 (Hikari `maximum-pool-size: 5`) | 지연은 늘어나는데 파드 CPU와 RDS CPU는 여유가 있다. 로그에 `Connection is not available` |
| 파드 CPU limit (request 100m, limit 500m~1) | 특정 파드의 CPU throttling이 오른다 |
| gateway 두 개가 한 노드에 있음 | 그 노드의 CPU만 먼저 찬다 |
| RDS `db.t4g.micro` | RDS CPU나 커넥션 수가 먼저 찬다 |
| ai-chat 단일 프로세스 (uvicorn worker 1개) | 챗 첫 바이트가 다른 지표보다 먼저 나빠진다 |

결과는 단계마다 한 줄로 이 문서에 남긴다.

| 날짜 | VU | 초당 요청 | 읽기 p95 | 저장 p95 | 챗 첫 바이트 p95 | 오류율 | 가장 바빴던 자원 | 판정 |
|---|---|---|---|---|---|---|---|---|
| | | | | | | | | |

이미지 태그, 파드 배치, mock 지연 설정(`MOCK_*_DELAY_MS`), 행동 모델을 바꿨다면 그 내용도 함께 적는다.

## 10. 다음 단계와 정리

- 병목 하나를 고치고 같은 방법으로 다시 잰다. **한 번에 하나만** 바꿔야 무엇이 효과였는지 알 수 있다.
- 설정 변경은 `workload/overlays/dev`의 패치로 dev에서만 먼저 시험한다. `workload/base`를 고치면 머지 즉시 prod에도 반영된다.
- 끝나면 정리한다.
  ```bash
  aws ec2 terminate-instances --profile lorekeeper --region ap-northeast-2 --instance-ids <INSTANCE_ID>
  aws ec2 delete-security-group --profile lorekeeper --region ap-northeast-2 --group-id "$SG"   # 인스턴스 종료 후
  aws ec2 delete-key-pair --profile lorekeeper --region ap-northeast-2 --key-name loresentry-loadgen
  ```
