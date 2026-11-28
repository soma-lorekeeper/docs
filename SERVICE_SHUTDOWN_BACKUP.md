# 서비스 종료 백업 — 운영 데이터와 프로젝트 키

> 최신화: 2026-11-29
> 대상: 서비스 종료(2026-11-29) 뒤 2026-12-29까지 들어오는 데이터 요청에 응하기 위한 운영 백업
> 상태: **최종 백업 완료(2026-11-29 03:38 KST, §4.2).** 자동 실행 예약은 설치되지 않아 손으로 실행했다. 백업 시점에 서비스는 아직 열려 있었다.

## 0. 요약

- 서비스는 2026-11-29 00:00(11월 28일에서 29일로 넘어가는 자정)에 종료한다. 가입자는 2026-12-29까지 `contact@loresentry.com`으로 자기 원고를 요청할 수 있고, 그 뒤 백업을 파기한다.
- 백업은 운영 DB 3개의 덤프, S3 버킷 2개, **평문 프로젝트 키**로 이루어진다. 이 셋이 있으면 AWS 없이 모든 문서를 복호화할 수 있다.
- KMS 마스터 키는 밖으로 꺼낼 수 없다. 대신 마스터 키로 감싼 프로젝트 키를 한 번 풀어 평문으로 저장한다([MANUSCRIPT_ENCRYPTION.md](MANUSCRIPT_ENCRYPTION.md) §2).
- 관리자 역할은 원래 이 키로 Decrypt를 할 수 없다. 백업할 때만 키 정책에 허용 문장을 더했다가 바로 되돌린다(§3).
- 백업은 팀원 한 명의 노트북 `~/loresentry-backup/`에만 둔다. 저장소, 클라우드 드라이브, 메신저에 올리지 않는다.
- 스크립트는 [shutdown-backup/](shutdown-backup/)에 있다. 키와 덤프는 이 저장소에 넣지 않는다.

---

## 1. 백업에 들어 있는 것

```text
~/loresentry-backup/prod-<날짜>/
  db/authentication.dump      pg_dump -Fc (소유자·권한 제외)
  db/content.dump
  db/ai_chat.dump
  s3/media/                   이미지 버킷 loresentry-media-prod-…
  s3/refresh/                 최신화 버킷 loresentry-refresh-prod-…
  keys/project-keys.json      프로젝트 id → 평문 프로젝트 키(AES-256, base64)
  keys/key-policy.original.json   작업 전 KMS 키 정책
  tools/                      이 저장소 shutdown-backup/ 의 사본
  backup.log, SHA256SUMS
```

| 넣지 않은 것 | 이유 |
|---|---|
| Neptune 그래프 | content DB의 문서와 관계에서 다시 만들 수 있는 파생 데이터다 |
| Kafka 토픽 | 본문은 content DB의 암호문과 같은 것이다 |
| KMS 마스터 키 | KMS 밖으로 나오지 않는다. 프로젝트 키를 풀어 두었으므로 필요 없다 |
| 로그(Loki·Elasticsearch·S3 로그 버킷) | 데이터 요청에 쓰이지 않는다 |

**1차 백업(2026-11-26)의 크기:** 계정 165개, 프로젝트 191개, 프로젝트 키 183개, 문서 3,878개, 이미지 305개, 합계 147MB. 키가 없는 프로젝트 8개는 문서가 없는 프로젝트다.

## 2. 스크립트

| 파일 | 하는 일 |
|---|---|
| `backup.sh` | 전체를 한 번에 실행한다. DB 덤프 → S3 받기 → 키 정책 임시 허용 → 키 풀기 → 정책 원복 → 거부 확인 → 검증 → 체크섬 |
| `unwrap_keys.py` | 덤프의 `project_keys`와 ai-chat의 `wrapped_key`를 모아 KMS로 푼다. 이미 푼 키는 건너뛰고, 실패하면 5초 간격으로 8번까지 다시 시도한다 |
| `ls_backup.py` | 덤프를 읽고 암호문을 여는 공통 코드. 형식과 AAD는 MANUSCRIPT_ENCRYPTION.md §3과 같다 |
| `verify.py` | 백업만으로 모든 암호문을 열어 본다. 하나라도 실패하거나 키가 없는 프로젝트에 문서가 있으면 실패로 끝난다 |
| `export_user.py` | 계정 하나의 문서를 txt 파일로 내보낸다(§6) |
| `com.loresentry.final-backup.plist` | 최종 백업을 예약하는 launchd 설정(§5) |

**필요한 것:** `aws` CLI(`lorekeeper` 프로파일), `psql`·`pg_dump` 18(libpq), `jq`, Telepresence 2.31.x, Python 3와 `cryptography` 패키지. 접속 준비는 [ENVIRONMENT_SETTING.md](ENVIRONMENT_SETTING.md) §2·§6과 같다.

**환경 변수**

| 변수 | 뜻 |
|---|---|
| `PREV` | 이전 백업 폴더. 그 안의 키 파일을 가져와 이미 푼 키는 다시 풀지 않는다 |
| `SKIP_UNWRAP=1` | 키 정책을 건드리지 않는다. 덤프와 검증만 시험할 때 쓴다 |
| `BACKUP_ROOT` | 백업을 둘 곳. 기본값은 `~/loresentry-backup` |

## 3. 키 정책을 잠시 여는 방법

키 정책은 관리 역할에 `Decrypt`를 주지 않는다(MANUSCRIPT_ENCRYPTION.md §7). 그래서 `backup.sh`는 다음 순서로 움직인다.

1. 현재 정책을 `keys/key-policy.original.json`에 저장한다. 그 안에 `TemporaryBackupUnwrap` 문장이 이미 있으면 멈춘다. 지난 실행이 원복하지 못한 정책을 원본으로 삼지 않기 위해서다.
2. 실행한 사람의 SSO 역할에 `kms:Decrypt`를 주는 문장 `TemporaryBackupUnwrap`을 더한다. 조건은 다른 문장과 같이 `kms:EncryptionContext:project_id`다.
3. 30초 기다린 뒤 키를 푼다. 남은 키가 있으면 20초 간격으로 세 번까지 다시 돈다.
4. 정책을 원본으로 되돌린다. 스크립트가 중간에 실패해도 종료 시점에 되돌린다.
5. 실제 감싼 키로 `Decrypt`를 불러 `AccessDenied`가 세 번 이어서 나오는지 확인한다. 3분 안에 그렇게 되지 않으면 로그에 `WARNING`을 남긴다.

**정책 변경은 바로 퍼지지 않는다.** 1차 백업에서 허용 문장을 더한 직후 8초 뒤의 호출 4건이 거부됐고, 되돌린 직후의 호출 하나는 통과했다. 그래서 더한 직후와 되돌린 직후의 결과 한 번을 그대로 믿지 않고, 다시 시도하고 여러 번 확인한다.

이 작업은 CloudTrail에 `PutKeyPolicy` 두 번과 SSO 역할의 `Decrypt` 호출로 남는다.

## 4. 1차 백업 기록 (2026-11-26)

| 항목 | 결과 |
|---|---|
| 시점 | 2026-11-26 10:50 KST |
| 위치 | `~/loresentry-backup/prod-2026-11-26/` |
| 키 | 183개 모두 풀림. 정책 전파 지연으로 세 번에 나눠 풀었다 |
| 키 정책 | 원본과 같음(principal 배열 순서만 다름). 원복 뒤 `Decrypt`는 `AccessDenied` |

**오프라인 검증(`verify.py`)**

| 대상 | 개수 | 실패 |
|---|---|---|
| 문서 본문 JSON | 3,878 | 0 |
| 문서 본문 텍스트(길이와 MAC까지 일치) | 3,878 | 0 |
| 버전 스냅샷 | 4,766 | 0 |
| 최신화 작업본(left·right·origin) | 927 | 0 |
| ai-chat 요약 | 3,743 | 0 |

- `refresh_results.payload_enc` 117건은 검증하지 않았다. 최신화 결과 원본이고, 작가에게 돌려줄 문서와 작업본은 위에서 따로 확인됐다.
- 예시 계정 `sampleqa11112353`을 `export_user.py`로 내보내 프로젝트 2개, 문서 112개가 폴더별 txt로 나오는 것을 확인했다.

### 4.2 최종 백업 (2026-11-29)

| 항목 | 결과 |
|---|---|
| 시점 | 2026-11-29 03:38 KST, `backup.sh`를 손으로 실행 |
| 위치 | `~/loresentry-backup/prod-2026-11-29-0338/` |
| 규모 | 계정 165개, 프로젝트 188개, 프로젝트 키 180개, 문서 3,711개, 이미지 305개, 합계 130MB |
| 키 | 11-26 백업의 키를 다시 썼다. 새로 풀어야 할 키는 없었다. 삭제된 프로젝트의 키 3개는 뺐다 |
| 키 정책 | 원본과 같음. 원복 뒤 `Decrypt`는 `AccessDenied` |
| 검증 | 문서 본문 3,711개, 버전 4,579개, 작업본 857개, 요약 3,581개 모두 복호화, 실패 0 |

**11-26 백업과 달라진 것**

- 한 계정이 탈퇴한 뒤 같은 이메일로 다시 가입했다. 탈퇴할 때 그 계정의 프로젝트 3개와 문서 168개가 지워졌다. **이 데이터는 11-26 백업에만 남아 있다.**
- 문서 1개가 새로 생기고 4개가 수정됐다. 마지막 저장은 2026-11-29 01:26 KST로, 예정된 종료 시각(00:00) 뒤다. 백업 시점에 서비스가 아직 열려 있었으므로, 서비스를 내린 뒤 한 번 더 백업해야 이후 저장까지 담긴다.
- 최신화 버킷의 객체 4개는 수명 규칙으로 만료되어 0개가 됐다.

**고친 것**

- 원복 뒤 거부를 확인하는 단계가 실제로는 거부됐는데도 `WARNING: decrypt still allowed`를 남겼다. `pipefail` 아래에서 `aws`의 실패 코드가 `grep`의 일치보다 앞서 판정됐기 때문이다. 출력을 먼저 받아 문자열로 비교하도록 고쳤다.
- 이전 백업의 키를 가져오면 삭제된 프로젝트의 키까지 따라왔다. `unwrap_keys.py`가 덤프에 있는 프로젝트의 키만 남기도록 고쳤다.

## 5. 최종 백업 — 2026-11-29 00:10 자동 실행

1차 백업 뒤에 쓰인 원고와 새 프로젝트는 최종 백업에만 들어간다. 서비스는 11월 28일에서 29일로 넘어가는 자정에 종료한다. 그 10분 뒤인 00:10에 `backup.sh`를 launchd로 한 번 실행한다.

### 5.1 미리 해 둘 일 (11월 28일 낮)

```bash
mkdir -p ~/loresentry-backup/tools
cp shutdown-backup/*.py shutdown-backup/backup.sh ~/loresentry-backup/tools/
cp shutdown-backup/com.loresentry.final-backup.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.loresentry.final-backup.plist
launchctl print gui/$(id -u)/com.loresentry.final-backup | grep -E "state|program"
```

스크립트를 `~/loresentry-backup/tools/`에 복사해 두는 이유는 launchd가 데스크톱·문서 폴더의 파일을 읽지 못할 수 있기 때문이다.

### 5.2 자정 전에 확인할 것 (11월 28일 밤)

| 확인 | 방법 | 이유 |
|---|---|---|
| AWS 로그인 | 28일 22시 이후에 `aws sso login --profile lorekeeper` | SSO 세션이 끝나 있으면 첫 명령에서 멈춘다. 접근 토큰은 1시간짜리지만 세션이 살아 있는 동안 CLI가 스스로 갱신한다 |
| Telepresence | `telepresence connect --context lore-sentry --namespace prod` 뒤 `telepresence status` | 연결이 끊겨 있으면 스크립트가 다시 연결을 시도하지만, 관리자 권한 승인이 필요하면 멈춘다 |
| 전원과 잠자기 | 전원을 꽂고 덮개를 열어 둔다. `sudo pmset schedule wake "11/28/26 23:55:00"` | 잠들어 있으면 launchd는 깨어난 뒤에야 실행한다 |
| 키 정책 | `TemporaryBackupUnwrap` 문장이 없어야 한다 | 남아 있으면 스크립트가 멈춘다(§3) |

### 5.3 종료와 백업의 순서

서비스는 2026-11-29 00:00에 종료한다. 백업은 실행 시점의 스냅샷이므로 **종료가 끝난 뒤에 돌아야** 마지막 저장까지 담긴다. 그래서 예약 시각을 자정이 아니라 00:10으로 두었다.

- 00:10에 서비스가 아직 열려 있으면 그 뒤에 저장한 원고는 백업에 없다. 종료가 늦어졌다면 종료한 뒤 `backup.sh`를 손으로 한 번 더 실행한다.
- 종료는 쓰기 경로(gateway, content)를 내리는 것을 뜻한다. **RDS, KMS 키, S3 버킷, Telepresence가 쓰는 클러스터는 백업 검증이 끝날 때까지 그대로 둔다.** 이것들이 없으면 백업을 만들 수 없다.
- 서비스를 내리는 방법(gitops에서 replica를 0으로, 프런트를 종료 안내 화면으로 등)은 이 문서를 쓰는 시점에 정해지지 않았다.

### 5.4 실행 뒤 확인 (11월 29일 아침)

```bash
tail -20 ~/loresentry-backup/prod-2026-11-29-*/backup.log
```

| 로그에 있어야 하는 줄 | 뜻 |
|---|---|
| `key policy restored` | 정책을 되돌렸다 |
| `decrypt is denied again` | 관리 역할의 Decrypt가 다시 막혔다 |
| `failed: 0`, `verification passed` | 모든 암호문이 백업만으로 열린다 |
| `done: …` | 끝까지 실행됐다 |

- `WARNING: decrypt still allowed`가 있으면 키 정책을 직접 확인하고 `keys/key-policy.original.json`으로 되돌린다.
- 로그가 없으면 `/tmp/loresentry-final-backup.out`을 본다. 대부분 SSO 세션 만료나 Telepresence 미연결이다. 원인을 고친 뒤 `PREV=~/loresentry-backup/prod-2026-11-26 ~/loresentry-backup/tools/backup.sh`로 손으로 실행한다.
- 끝나면 예약을 지운다. 그대로 두면 다음 해 같은 날에 다시 실행된다.

```bash
launchctl bootout gui/$(id -u)/com.loresentry.final-backup
rm ~/Library/LaunchAgents/com.loresentry.final-backup.plist
```

### 5.5 아직 확인하지 않은 것

- `backup.sh`의 덤프·S3·검증 단계는 2026-11-26에 `SKIP_UNWRAP=1`로 시험해 통과했다. **키 정책을 열고 닫는 단계는 스크립트로는 실행해 보지 않았다.** 같은 명령을 손으로 실행해 1차 백업을 만들었다.
- launchd 예약은 설치해 보지 않았다.
- SSO 세션이 로그인 뒤 몇 시간 유지되는지 재지 않았다.

## 6. 데이터 요청 처리 (2026-12-29까지)

1. 요청 메일의 발신 주소가 가입 이메일과 같은지 확인한다. 아이디로 가입한 계정은 이메일이 없으므로 아이디와 작품 이름 등으로 본인임을 확인한다.
2. 가장 최근 백업 폴더에서 내보낸다.

```bash
export PATH="$(brew --prefix libpq)/bin:$PATH"
cd ~/loresentry-backup/prod-2026-11-29-*/tools
python3 export_user.py someone@gmail.com
```

3. `exports/<계정>/<프로젝트>/<폴더>/<문서 제목>.txt`로 나온다. 원고는 에피소드 폴더 아래에 놓이고, 휴지통에 있던 프로젝트와 문서는 이름에 표시된다.
4. 폴더를 압축해 회신한다. 보낸 뒤 `exports/` 아래 사본은 지운다.

내보내는 것은 문서의 본문 텍스트다. 서식, 속성 표, 메모, 관계, 이미지, AI 챗 기록은 들어가지 않는다. 요청이 있으면 덤프와 `s3/media/`에서 따로 꺼낸다.

## 7. 파기 (2026-12-29 이후)

- `~/loresentry-backup/` 전체를 지우고 휴지통을 비운다. Time Machine을 쓰면 그 안의 사본도 지운다.
- KMS 마스터 키, RDS 인스턴스와 자동 백업·스냅샷, S3 버킷을 지우는 인프라 정리는 이 문서의 범위 밖이다. 다만 **최종 백업의 검증이 통과하기 전에는 RDS와 KMS 키를 지우지 않는다.**
