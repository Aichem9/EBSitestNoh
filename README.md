# EBSi 기출문항 바로채점 Streamlit

학생이 객관식 답안을 직접 제출하거나 교사가 CSV로 일괄 입력하면 즉시 채점하고, 응시자 평균과 문항별 오답률을 보여주는 Streamlit 앱입니다.

## 포함된 기능

- 학생용 1~5번 객관식 답안 입력 및 즉시 채점
- 개인별 정답 수, 점수, 오답 문항과 해설 표시
- 교사용 평균·최고·최저 점수 및 문항별 오답률 분석
- 학생 답안 CSV 일괄 업로드 및 결과 다운로드
- 정답·해설 PDF를 Claude API로 분석하여 새 시험 등록
- Claude API 키를 학생에게 노출하지 않는 서버 비밀 설정
- 최초 제출, 최근 제출, 최고 점수, 연습 모드 선택
- 기본 시험 2종 탑재

## 1. 설치

Python 3.11 이상을 권장합니다.

```bash
python -m venv .venv
```

Windows:

```bash
.venv\Scripts\activate
pip install -r requirements.txt
```

macOS/Linux:

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

## 2. 비밀 설정

`.streamlit/secrets.toml.example`을 `.streamlit/secrets.toml`로 복사하고 값을 입력합니다.

```toml
ADMIN_PASSWORD = "교사용-강력한-비밀번호"
ANTHROPIC_API_KEY = "sk-ant-..."
CLAUDE_MODEL = "claude-sonnet-5"
DATABASE_PATH = "data/chem_grader.db"
```

`ANTHROPIC_API_KEY`는 PDF 자동 분석에만 사용됩니다. 키가 없어도 기본 시험 응시, 채점, 통계, CSV 업로드와 PDF 텍스트 추출은 작동합니다.

## 3. 실행

```bash
streamlit run app.py
```

브라우저에서 표시되는 주소(보통 `http://localhost:8501`)로 접속합니다.

## 4. 학생 사용 방법

1. `학생 응시` 메뉴에서 시험을 선택합니다.
2. 학급과 익명 응시코드를 입력합니다.
3. 모든 문항의 답을 선택하고 제출합니다.
4. 점수, 정답 수, 오답 문항과 해설을 확인합니다.

학생에게는 Claude API 키나 교사 비밀번호를 알려주지 않습니다.

## 5. 교사 사용 방법

1. `교사 관리` 메뉴에서 `ADMIN_PASSWORD`로 로그인합니다.
2. 학급 분석, CSV 입력, 시험 PDF 등록 또는 시험 설정 탭을 사용합니다.
3. PDF 등록 시 AI 추출 결과를 반드시 검토한 뒤 저장합니다.

CSV 열 이름은 다음 형식입니다.

```text
class_name,student_code,q1,q2,q3,...
```

앱의 `입력 양식 CSV 다운로드` 버튼으로 시험 문항 수에 맞는 양식을 받을 수 있습니다.

## 데이터 보관 주의

기본값은 로컬 SQLite 파일입니다. 개인 PC나 지속 디스크가 있는 서버에서는 그대로 사용할 수 있습니다. 일부 무료 호스팅은 앱 재시작 때 로컬 파일이 사라질 수 있으므로 실제 학급 운영 전에는 다음 중 하나가 필요합니다.

- 지속 디스크가 제공되는 서버에 배포
- SQLite 대신 Supabase/PostgreSQL 같은 외부 데이터베이스 연결
- 수업 종료 직후 교사 화면에서 결과 CSV 다운로드

학생 이름 대신 익명 응시코드를 사용하고, 결과 파일은 교사만 접근할 수 있는 위치에 보관하세요.

## 테스트

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

또는 pytest가 없다면 다음 스모크 테스트를 실행할 수 있습니다.

```bash
python -c "from core import parse_answer_text; assert len(parse_answer_text('1:① 2:④')) == 2"
```
