# kids-weekend-api

초등 1~3학년 주말 체험 후보를 모으는 Python 수집기입니다. 기존 insta_2026 운영과 독립적이며 게시·예약·텔레그램 전송은 하지 않습니다.

## 실행
Python 3.11 이상, 외부 패키지 불필요.

```sh
python -m unittest -v
python collector.py --sample --probe-images
# 전체 조회: 서울 열린데이터광장 인증키를 환경변수 SEOUL_API_KEY로 등록 후
python collector.py --probe-images
```

결과: output/reservations.json. 샘플은 서비스별 첫 5건으로 전체 추천 결과가 아닙니다.

## GitHub Actions
push 시 테스트와 공개 샘플 수집이 실행됩니다. Actions → Seoul reservation collection → 실행 → seoul-reservations 아티팩트에서 JSON을 받습니다.
전체 조회는 Settings → Secrets and variables → Actions → New repository secret에 SEOUL_API_KEY를 등록한 뒤 Run workflow에서 sample을 해제합니다. 키를 코드·이슈·채팅에 올리지 마세요.
정기 실행은 아직 설정하지 않았습니다.

## 데이터와 판단
- 교육: ListPublicReservationEducation / 문화행사: ListPublicReservationCulture
- SVCID 중복 제거, 한국 시간 조회시각, 접수·운영기간, 대상 원문, 분류, 예약 링크 저장.
- 명시된 학년 범위만 explicit_match. 가족·어린이·나이만 있는 경우 needs_review.
- 접수 중은 잔여석 보장이 아닙니다. remaining_seats=null.
- 운영기간이 주말과 겹쳐도 실제 토·일 회차가 있다는 뜻은 아닙니다. weekend_session_verified=false.
- candidate는 검토 후보이지 게시 가능한 최종 추천이 아닙니다.
- 무료/유료 표시만 수집하며 구체적인 금액은 상세 안내 확인이 필요합니다.
- 통신 실패와 인증 오류는 성공한 빈 결과로 처리하지 않습니다.

## 공식 이미지 확인
2026-10-21 공개 샘플 실응답에서 IMGURL을 확인했습니다. 대표 이미지 URL을 official_image_url에 저장합니다.
--probe-images는 최대 10건의 서울시 예약 이미지에 HEAD 요청으로 접근 여부와 MIME 유형을 확인합니다. 이미지 파일은 내려받지 않습니다.
사진인지 포스터인지 API는 보장하지 않으므로 official_image_unclassified로 표시합니다. 사진으로 단정하거나 자동 게시하지 않습니다.
공식 데이터셋은 공공누리 제1유형(출처표시)으로 안내되지만, 개별 이미지의 별도 권리표시·사진 속 인물·포스터 포함 저작물을 게시 전에 확인해야 합니다. image_rights_status=review_required를 유지합니다.
출처: 서울특별시 공공서비스예약 / 서울 열린데이터광장. 원문 예약 링크도 함께 표시하세요.

## 공식 문서
- https://data.seoul.go.kr/dataList/OA-2268/A/1/datasetView.do
- https://data.seoul.go.kr/dataList/OA-2269/O/1/datasetView.do
- https://data.seoul.go.kr/dataList/4/literacyView.do

서울시 문서의 공식 조회 주소는 http://openapi.seoul.go.kr:8088 입니다. 이 환경에서 해당 포트의 HTTPS 연결은 실패했습니다. 현재 문서의 HTTP 주소를 사용하며 인증키가 포함된 요청 URL은 로그·결과에 기록하지 않습니다. HTTPS 주소가 공식 지원되면 전환해야 합니다.

## 후보 선정 기준 (2026-10-21 변경)
앞으로 7일 안에 접수를 시작하는 프로그램을 후보로 선정하고 registration_start 오름차순으로 candidates에 저장합니다. --opening-days로 기간 변경 가능. 접수 시작일이 없거나 이미 접수 중/마감인 항목은 기본 후보에서 제외합니다. 행사일은 다음 주 이후여도 포함합니다. 전체 records는 검토용으로 유지합니다. 초1~3 대상이 불명확한 needs_review 후보는 원문 검토가 필요하며, 주말 회차·잔여석은 자동 확정하지 않습니다.

## 후보 선택 후 승인 게시 흐름
candidates.yml은 매일 한국 시간 06:37에 전체 API 수집을 예약하고 공개 정보만 data/candidates.json에 저장합니다. GitHub 예약은 지연될 수 있습니다. 키는 이 저장소 Secrets에만 유지합니다.
insta_2026의 candidate_bot.py가 이 목록을 읽고 07:07부터 텔레그램 후보 안내를 시도합니다. 일반적으로 15분 간격의 예약 실행에서 채택/승인 명령을 확인하며 GitHub 지연이 가능합니다.
후보 메시지의 채택 날짜코드 번호 → 직접 제작 카드·공식 API 사실 기반 본문 초안 → 승인 게시물ID 순서입니다. 같은 소유자, 최신 후보 안내 이후 명령만 처리합니다. 채택과 승인은 별도이며, 불확실한 게시/초안 생성은 자동 재시도하지 않습니다.
API 초안은 텔레그램에서 수정 게시물ID 요청내용 명령을 지원합니다. 무료 Gemini로 표현을 수정하고 사실 검증 후 기존 초안의 카드와 본문을 바꿉니다. 수정 시작 시 이전 승인을 해제하고 새 미리보기 이후 승인만 허용하며, 실패하면 revision_failed 상태로 게시를 막습니다. 기존 웹검색 자동 초안 경로는 예약 실행에서 교체했습니다. 후보 수집/전송은 실제 실행 확인됐으며 채택 이후 실제 게시 검증은 소유자의 채택·승인을 기다립니다.
