# Treasury COT Positioning Lab

**미국 국채선물 COT 포지셔닝 랩**

CFTC가 매주 발표하는 Traders in Financial Futures(TFF) 포지션과 미국 국채선물 가격·미 재무부 금리를 결합해, 참여 주체별 수급·혼잡도·실데이터 연구 신호를 추적하는 금융공학 대시보드입니다.

A point-in-time analytics dashboard combining CFTC COT positioning with U.S. Treasury futures prices and Treasury yields to track participant flows, crowding, and research signals.

## 가장 쉬운 실행 방법

Windows 탐색기에서 **`실행하기.bat`를 더블클릭**합니다.

1. 필요한 실행 환경을 자동으로 준비합니다.
2. CFTC·Yahoo Finance·미국 재무부 데이터를 불러옵니다.
3. 서버가 준비될 때까지 잠시 기다린 뒤 브라우저에서 `http://127.0.0.1:8000`을 엽니다.
4. 종료하려면 열려 있는 검은색 서버 창에서 `Ctrl+C`를 누르거나 창을 닫습니다.

첫 실행에는 패키지 설치와 전체 이력 다운로드 때문에 수 분이 걸릴 수 있습니다. 이후에는 `data/`의 로컬 캐시를 사용하므로 더 빠릅니다.

### 준비 사항

- Windows 10/11
- Python 3.11 이상
- 최초 설치와 최신 데이터 갱신을 위한 인터넷 연결

`uv`가 설치되어 있으면 잠금파일 기반 환경을 사용하고, 없으면 일반 Python 가상환경을 자동 생성합니다. Python 자체가 없으면 실행 창에 설치 안내가 표시됩니다.

## 화면에서 할 수 있는 일

- 시장 선택: 2Y, 5Y, 10Y, Ultra 10Y, Treasury Bond, Ultra Bond
- 기간 선택: 3개월, 6개월, 1년, 3년, 5년, 전체
- 주체 선택: Dealer, Asset Manager, Leveraged Funds, Other Reportables
- 순포지션, OI 대비 순포지션, 1·4·13주 변화 추적
- 52주 Z-Score, Robust Z-Score, 52주·156주 Percentile 확인
- Leveraged Funds와 Asset Manager 포지션 차이 확인
- 실제 선물가격과 미국 재무부 금리를 함께 확인
- 고정 규칙 전략 성과, 거래비용 반영 결과, 1·4·13주 Rank IC 확인

## 데이터 출처

- COT: CFTC Public Reporting Environment, TFF Futures Only (`gpe5-46if`)
- 가격: Yahoo Finance 연속선물 일봉
- 금리 교차검증: U.S. Treasury Daily Treasury Par Yield Curve Rates

CFTC 시장은 이름이 아니라 계약시장 코드로 식별합니다.

| 화면 | CFTC 코드 | 가격 심볼 |
|---|---:|---|
| 2Y | 042601 | ZT=F |
| 5Y | 044601 | ZF=F |
| 10Y | 043602 | ZN=F |
| Ultra 10Y | 043607 | TN=F |
| Treasury Bond | 020601 | ZB=F |
| Ultra Bond | 020604 | UB=F |

## 정보시점과 백테스트 원칙

COT 보고서는 화요일 포지션을 통상 금요일에 공개합니다. 과거 공개 가능일은 `기준 화요일 + 3일`로 추정하며, 공휴일로 지연된 실제 발표 시각과는 다를 수 있습니다.

백테스트는 미래정보 사용을 피하기 위해 COT 기준일 바로 다음 화요일 이후의 첫 거래일 종가를 진입가로 사용합니다. 전략 규칙은 평가 전에 고정하며, 매주 포지션 회전량당 1bp 비용을 반영합니다. 화면의 최신 관측치가 아직 진입일에 도달하지 않았다면 `진입 대기`로 표시합니다.

Yahoo 연속선물은 연구와 화면 개발에는 적합하지만 실제 운용용 정산가와 동일하지 않습니다. 정밀한 운용 검증에는 CME 계약월별 정산가, 명시적인 롤 규칙, 슬리피지, DV01 및 증거금 자료가 추가로 필요합니다.

## 명령줄 실행

더블클릭 대신 터미널에서 실행하려면 다음 명령을 사용합니다.

```powershell
uv sync
uv run python main.py
```

## 주요 파일

- `main.py`: 서버 실행 진입점
- `dashboard_app.py`: 화면/API 조립과 데이터 정합성 검사
- `data_fetcher.py`: CFTC 수집·정규화·포지션 지표 계산
- `price_fetcher.py`: 실제 선물가격 수집 및 캐시
- `treasury_yield_fetcher.py`: 미국 재무부 금리 수집 및 캐시
- `backtest.py`: 고정 전략·비용·Rank IC 검증
- `실행하기.bat`: 원클릭 실행 스크립트

## 라이선스

본 프로젝트는 [MIT License](LICENSE)를 따릅니다.
