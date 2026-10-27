# 문서 이미지와 "관계도"

> 작성일: 2026-10-27
> 상태: PR 대기. `loresentry-content`·`loresentry-gateway`·`loresentry-frontend` 의 `feat/document-image`, 이름 바꾸기는 `loresentry-frontend#71`(`feat/relationship-map-name`)
> 전제: [`../IMAGE_UPLOAD_S3.md`](../IMAGE_UPLOAD_S3.md)(업로드 티켓·S3 직접 PUT), [`../CONTENT_PROJECT_API.md`](../CONTENT_PROJECT_API.md)

문서마다 대표 이미지를 하나 넣는다. 관계도 노드가 분류 아이콘만 그리던 자리에 그 이미지가 보이고, 문서 제목 왼쪽에도 보인다.
함께 화면의 이름을 바꿨다 — "그래프"는 **관계도**, "그래프 최신화"는 **설정 문서 최신화**다.

## 1. 정한 것

| 항목 | 결정 | 이유 |
|---|---|---|
| 제목 옆 자리 | 이미지가 없어도 **44px 원을 늘 둔다**. 비면 들어간 면 위 종류 아이콘, 차면 사진 + 종류 색 2px 테두리. 10px 종류 점을 대신한다 | 사용자 결정. 넣을 때 제목이 밀리지 않고, 무엇을 넣는 자리인지 늘 보인다 |
| 자르기 | 고르면 **원형 자르기 창**. 끌기·휠·막대·단추·방향키·+/- | 사용자 결정. 세로로 긴 인물 그림은 가운데를 자르면 얼굴이 빠진다. 처음 자리도 세로 그림은 위쪽 4분의 1 에 맞춘다 |
| 보이는 곳 | 제목 옆, 관계도 노드, 선택한 노드 패널, 타임라인 이름 칸, 검색 결과 줄 | 사용자 결정. 사이드바 트리·탭은 16px 이하라 종류 아이콘을 둔다 |
| 대상 문서 | 원고를 포함한 모든 종류 | 문서별 이미지 |
| 버전과의 관계 | **문서 내용이 아니다.** 버전 스냅샷·최신화 비교·AI 입력에 들지 않고 `revision_no` 도 오르지 않는다 | 본문을 옛 버전으로 되돌려도 그림은 그대로여야 한다. 열린 편집기의 다음 저장이 이미지 때문에 충돌하면 안 된다 |
| 바꾸기·지우기 | 이전 이미지는 행과 S3 객체를 함께 지운다 | 이미지 하나는 문서 하나의 것이다 |
| 잠긴 문서 | 보기만 한다(서버도 `DOCUMENT_LOCKED`) | 잠금은 편집을 막는 것이다 |
| 저장 형식 | 브라우저에서 **512px 정사각형**으로 줄여 webp(못 만들면 jpeg)로 올린다 | 노드와 제목 옆 원은 많아야 40px 이다. 원본을 그대로 쓰면 노드마다 수 MB 를 매 프레임 그린다 |
| 입력 파일 | PNG·JPG·WEBP·GIF, 20MB 까지. GIF 는 첫 장면만 남는다 | 서버 한도(10MB)는 줄인 결과에 걸린다. 20MB 는 브라우저가 너무 큰 그림을 풀다 멈추지 않게 하는 한도다 |
| 이미지 주소 공개 | 기존 미디어처럼 로그인 없이 열리는 무작위 주소(`media.loresentry.com/projects/{p}/images/{uuid}.webp`) | 지금 구조 유지. 비공개가 필요하면 CloudFront 서명 쿠키 |
| 관계도 노드 크기 | 지금처럼 관계 수로만 정한다 | 이미지가 있다고 키우면 "많이 이어진 문서가 크다"는 신호가 흐려진다 |

## 2. 서버

**content** (`V17__document_image.sql`)

- `document.image_id UUID` — 복합 외래 키 `(image_id, project_id) → image(id, project_id)` 로 다른 프로젝트의 이미지를 가리킬 수 없다. 이미지 행이 지워지면 `ON DELETE SET NULL (image_id)`.
- `uq_document_image` 부분 유일 인덱스 — 이미지 하나는 문서 하나에만.
- `PUT /files/{id}/image {image_id}` → 200 Document. 업로드가 확인된(`COMMITTED`) 같은 프로젝트 이미지만 받는다. 오류: `IMAGE_NOT_FOUND`(404, 다른 프로젝트 포함), `OBJECT_NOT_UPLOADED`(409, 아직 `PENDING`), `INVALID_REQUEST`(400, 다른 문서가 쓰는 이미지), `DOCUMENT_LOCKED`.
- `DELETE /files/{id}/image` → 204. 없어도 204.
- 응답: 파일 트리 `documents[].image_url`, 관계도 `nodes[].image_url`. 문서 열기(`GET /files/{id}/content`)에는 싣지 않는다 — 화면은 이미 불러 둔 트리에서 읽는다.
- 이미지 바꾸기·지우기·문서 영구 삭제 때 이전 이미지 행을 지우고, 커밋 뒤에 S3 객체를 지운다(`MediaCleanup`). 프로젝트·계정 삭제는 원래 모든 이미지를 지웠다.
- 이벤트(outbox)를 쓰지 않는다. graph-rag·요약 스케줄러가 읽는 문서 내용이 아니다.
- 테스트 `DocumentImageApiTest` 13개, 전체 412개 통과.

**gateway** — 경로 2개, `Document`·`GraphNode` 의 `image_url`, `docs/CONTENT_API.md`. BFF 는 필드를 하나씩 선언하므로 빠뜨리면 조용히 사라진다. 전체 611개 통과.

## 3. 프론트

| 자리 | 파일 |
|---|---|
| 포트 | `FileService.setImage(fileId, blob)`, `clearImage(fileId)` |
| 업로드 | `services/api/images.ts` — 티켓 → S3 PUT(`credentials: "omit"`, CSRF 헤더 없음, `Content-Length` 는 브라우저가 정함) → complete → `PUT /files/{id}/image` |
| mock | `services/mock/files.ts` 가 같은 자리에 data URL 을 둔다 |
| 제목 옆 | `features/documents/document-image.tsx`(자리·메뉴·끌어 놓기·올리는 중), `image-crop-dialog.tsx`(자르기 창), `image-crop.ts`(계산, 테스트 7개) |
| 작은 자리 | `features/common/document-avatar.tsx` — 노드 패널·타임라인·검색. 이미지가 없거나 깨지면 종류 아이콘 |
| 관계도 | `features/graph/engine/node-images.ts`(캐시, 도착하면 다시 그리기), `graph-2d.tsx` `drawNodeImage` |

- 관계도는 화면 반지름 5px 이상인 노드에만 사진을 그린다(아이콘은 9px). 그보다 작으면 종류 색 점이다. 흐림 상태에서는 사진도 옅어진다(불투명도 0.28).
- 캔버스에 `crossOrigin` 없이 그린다. 미디어 CloudFront 는 GET 에 CORS 헤더를 주지 않아 `crossOrigin` 을 두면 아예 불러와지지 않는다. 대신 캔버스가 "오염"되는데, 이 화면은 픽셀을 읽지 않는다. 관계도를 PNG 로 내보내는 기능을 만들면 미디어 배포에 CORS 응답 헤더 정책을 붙이고 `crossOrigin = "anonymous"` 로 바꾼다.
- 검증: `pnpm check:cdn`(테스트 774개, 한국어·영어 빌드, 정적 export). mock 으로 1440×900 에서 빈 자리·자르기 창·채운 자리(라이트·다크)·관계도·노드 패널·타임라인·검색을 확인했다.

## 4. "관계도"·"설정 문서 최신화" 이름 바꾸기 (`loresentry-frontend#71`)

| 전 | 한국어 | 영어 |
|---|---|---|
| 그래프 | 관계도 | Relationship map |
| 그래프 최신화 | 설정 문서 최신화 | Refresh setting docs(사이드바), Setting document refresh(설명 글) |
| 그래프 최신화 중… {n}분 | 설정 문서 최신화 중… {n}분 | Refreshing docs… {n} min |
| 그래프 반영(버전 이름) | 최신화 반영 | From refresh |

- 화면 문구만 바꿨다. 탭 종류 `graph`, `?open=graph`, 저장된 작업공간 배치, 서버 경로(`/graph`, `/graph-refresh`)는 그대로다.
- 영어 사이드바 문구는 짧게 줄였다. "Refreshing setting documents…" 는 사이드바 말줄임표에 잘렸다.
- 사용 가이드·온보딩·작업공간 둘러보기·랜딩·`PRODUCT.md`·`DESIGN.md` 를 함께 바꿨다. 이 docs 의 다른 문서는 기록이라 옛 이름을 그대로 둔다.

## 5. 배포 순서

1. content — V17 은 칼럼·제약 추가뿐이라 옛 gateway·프론트와 함께 돌아도 된다.
2. gateway — 새 필드는 null 이 될 수 있어 옛 프론트가 무시한다.
3. frontend — #71(이름) 다음에 이미지 PR. 둘 다 병합할 때 **Squash and merge** 를 권한다(#71 첫 커밋에 실수로 들어갔다 지운 로컬 도구 파일이 있다).

## 6. 남은 것

- `PENDING` 이미지 정리 배치는 여전히 없다. 자르기 뒤 업로드가 중간에 실패하면 S3 에 고아 객체가 남을 수 있다(`CONTENT_PROJECT_API.md` 남은 작업 #25).
- 예시 프로젝트(`POST /projects/sample`)에는 이미지가 없다.
- 사진의 위치를 나중에 다시 맞추려면 원본이 필요하다. 지금은 512px 결과만 저장하므로 "바꾸기"로 다시 고른다.
- Pencil 와이어프레임에는 이 자리가 없다. 디자인 원천을 맞추려면 `lorekeeper.pen` 의 문서 머리(052–064)에 그려 넣어야 한다.
