from __future__ import annotations

import base64
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
from pypdf import PdfReader


CIRCLED = {"①": 1, "②": 2, "③": 3, "④": 4, "⑤": 5}

SUBJECTS = [
    "통합과학",
    "물리학1",
    "물리학2",
    "화학1",
    "화학2",
    "생명과학1",
    "지구과학1",
    "생활과 윤리",
    "사회문화",
    "한국지리",
    "윤리와 사상",
]

# 새 설치에서는 교사가 검토한 시험만 학생에게 공개합니다.
SEED_EXAMS: list[dict[str, Any]] = []


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS exams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL UNIQUE,
            subject TEXT NOT NULL,
            unit_name TEXT NOT NULL DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1,
            retake_policy TEXT NOT NULL DEFAULT 'latest',
            problem_pdf BLOB,
            problem_pdf_name TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exam_id INTEGER NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
            number INTEGER NOT NULL,
            correct_answer INTEGER NOT NULL CHECK(correct_answer BETWEEN 1 AND 5),
            explanation TEXT NOT NULL DEFAULT '',
            UNIQUE(exam_id, number)
        );

        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exam_id INTEGER NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
            class_name TEXT NOT NULL,
            student_code TEXT NOT NULL,
            score REAL NOT NULL,
            correct_count INTEGER NOT NULL,
            submitted_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS responses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            submission_id INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
            question_number INTEGER NOT NULL,
            selected_answer INTEGER NOT NULL CHECK(selected_answer BETWEEN 1 AND 5),
            is_correct INTEGER NOT NULL,
            UNIQUE(submission_id, question_number)
        );

        CREATE INDEX IF NOT EXISTS idx_submissions_exam
            ON submissions(exam_id, submitted_at);
        CREATE INDEX IF NOT EXISTS idx_submissions_student
            ON submissions(exam_id, class_name, student_code);
        """
    )
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(exams)")}
    if "problem_pdf" not in columns:
        conn.execute("ALTER TABLE exams ADD COLUMN problem_pdf BLOB")
    if "problem_pdf_name" not in columns:
        conn.execute("ALTER TABLE exams ADD COLUMN problem_pdf_name TEXT NOT NULL DEFAULT ''")
    conn.commit()


def seed_exams(conn: sqlite3.Connection) -> None:
    for exam in SEED_EXAMS:
        existing = conn.execute(
            "SELECT id FROM exams WHERE title = ?", (exam["title"],)
        ).fetchone()
        if existing:
            continue
        exam_id = save_exam(
            conn,
            title=exam["title"],
            subject=exam["subject"],
            unit_name=exam["unit"],
            questions=[
                {
                    "number": index,
                    "correct_answer": answer,
                    "explanation": "해설 PDF를 등록하면 이곳에 문항별 해설이 표시됩니다.",
                }
                for index, answer in enumerate(exam["answers"], start=1)
            ],
        )
        if not exam_id:
            raise RuntimeError("기본 시험을 생성하지 못했습니다.")


def save_exam(
    conn: sqlite3.Connection,
    title: str,
    subject: str,
    unit_name: str,
    questions: Iterable[dict[str, Any]],
    retake_policy: str = "latest",
    active: bool = True,
    problem_pdf: bytes | None = None,
    problem_pdf_name: str = "",
) -> int:
    clean_questions = sorted(list(questions), key=lambda q: int(q["number"]))
    if not title.strip():
        raise ValueError("시험명을 입력하세요.")
    if not clean_questions:
        raise ValueError("문항이 없습니다.")
    numbers = [int(q["number"]) for q in clean_questions]
    if numbers != list(range(1, len(numbers) + 1)):
        raise ValueError("문항 번호는 1번부터 연속되어야 합니다.")
    if any(int(q["correct_answer"]) not in range(1, 6) for q in clean_questions):
        raise ValueError("정답은 1~5 중 하나여야 합니다.")
    if retake_policy not in {"first", "latest", "best", "unlimited"}:
        raise ValueError("지원하지 않는 재응시 정책입니다.")

    with conn:
        cursor = conn.execute(
            """
            INSERT INTO exams(
                title, subject, unit_name, active, retake_policy,
                problem_pdf, problem_pdf_name, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                title.strip(),
                subject.strip() or "화학",
                unit_name.strip(),
                int(active),
                retake_policy,
                problem_pdf,
                problem_pdf_name.strip(),
                utc_now(),
            ),
        )
        exam_id = int(cursor.lastrowid)
        conn.executemany(
            """
            INSERT INTO questions(exam_id, number, correct_answer, explanation)
            VALUES (?, ?, ?, ?)
            """,
            [
                (
                    exam_id,
                    int(q["number"]),
                    int(q["correct_answer"]),
                    str(q.get("explanation", "")).strip(),
                )
                for q in clean_questions
            ],
        )
    return exam_id


def list_exams(conn: sqlite3.Connection, active_only: bool = False) -> list[dict[str, Any]]:
    where = "WHERE e.active = 1" if active_only else ""
    rows = conn.execute(
        f"""
        SELECT e.id, e.title, e.subject, e.unit_name, e.active,
               e.retake_policy, e.problem_pdf_name, e.created_at,
               COUNT(q.id) AS question_count
        FROM exams e
        LEFT JOIN questions q ON q.exam_id = e.id
        {where}
        GROUP BY e.id
        ORDER BY e.created_at DESC, e.id DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def get_exam(conn: sqlite3.Connection, exam_id: int) -> dict[str, Any]:
    exam = conn.execute("SELECT * FROM exams WHERE id = ?", (exam_id,)).fetchone()
    if exam is None:
        raise ValueError("시험을 찾을 수 없습니다.")
    result = dict(exam)
    result["questions"] = [
        dict(row)
        for row in conn.execute(
            "SELECT number, correct_answer, explanation FROM questions WHERE exam_id = ? ORDER BY number",
            (exam_id,),
        ).fetchall()
    ]
    return result


def set_exam_settings(
    conn: sqlite3.Connection, exam_id: int, active: bool, retake_policy: str
) -> None:
    if retake_policy not in {"first", "latest", "best", "unlimited"}:
        raise ValueError("지원하지 않는 재응시 정책입니다.")
    with conn:
        conn.execute(
            "UPDATE exams SET active = ?, retake_policy = ? WHERE id = ?",
            (int(active), retake_policy, exam_id),
        )


def score_answers(
    questions: Iterable[dict[str, Any]], answers: dict[int, int]
) -> dict[str, Any]:
    items = sorted(list(questions), key=lambda q: int(q["number"]))
    if len(answers) != len(items):
        raise ValueError("모든 문항에 답해야 합니다.")
    details = []
    correct_count = 0
    for question in items:
        number = int(question["number"])
        selected = int(answers[number])
        correct = int(question["correct_answer"])
        is_correct = selected == correct
        correct_count += int(is_correct)
        details.append(
            {
                "question_number": number,
                "selected_answer": selected,
                "correct_answer": correct,
                "is_correct": is_correct,
                "explanation": question.get("explanation", ""),
            }
        )
    score = round(correct_count / len(items) * 100, 1)
    return {"score": score, "correct_count": correct_count, "details": details}


def create_submission(
    conn: sqlite3.Connection,
    exam_id: int,
    class_name: str,
    student_code: str,
    answers: dict[int, int],
) -> dict[str, Any]:
    exam = get_exam(conn, exam_id)
    class_name = class_name.strip()
    student_code = student_code.strip()
    if not class_name or not student_code:
        raise ValueError("학급과 응시코드를 모두 입력하세요.")

    previous = conn.execute(
        """
        SELECT COUNT(*) AS n FROM submissions
        WHERE exam_id = ? AND class_name = ? AND student_code = ?
        """,
        (exam_id, class_name, student_code),
    ).fetchone()["n"]
    if exam["retake_policy"] == "first" and previous:
        raise ValueError("이 시험은 최초 제출만 인정됩니다.")

    result = score_answers(exam["questions"], answers)
    with conn:
        cursor = conn.execute(
            """
            INSERT INTO submissions(
                exam_id, class_name, student_code, score, correct_count, submitted_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                exam_id,
                class_name,
                student_code,
                result["score"],
                result["correct_count"],
                utc_now(),
            ),
        )
        submission_id = int(cursor.lastrowid)
        conn.executemany(
            """
            INSERT INTO responses(
                submission_id, question_number, selected_answer, is_correct
            ) VALUES (?, ?, ?, ?)
            """,
            [
                (
                    submission_id,
                    row["question_number"],
                    row["selected_answer"],
                    int(row["is_correct"]),
                )
                for row in result["details"]
            ],
        )
    result.update(
        {
            "submission_id": submission_id,
            "attempt": int(previous) + 1,
            "question_count": len(exam["questions"]),
            "exam_title": exam["title"],
        }
    )
    return result


def _selected_submissions_sql(policy: str) -> str:
    order = {
        "first": "s.submitted_at ASC, s.id ASC",
        "latest": "s.submitted_at DESC, s.id DESC",
        "best": "s.score DESC, s.submitted_at DESC, s.id DESC",
        "unlimited": "s.submitted_at DESC, s.id DESC",
    }.get(policy, "s.submitted_at DESC, s.id DESC")
    return f"""
        SELECT * FROM (
            SELECT s.*,
                   ROW_NUMBER() OVER (
                       PARTITION BY s.exam_id, s.class_name, s.student_code
                       ORDER BY {order}
                   ) AS rn
            FROM submissions s
            WHERE s.exam_id = ?
        ) ranked
        WHERE rn = 1
    """


def exam_analytics(conn: sqlite3.Connection, exam_id: int) -> dict[str, Any]:
    exam = get_exam(conn, exam_id)
    chosen = pd.read_sql_query(
        _selected_submissions_sql(exam["retake_policy"]), conn, params=(exam_id,)
    )
    if chosen.empty:
        return {"summary": {}, "students": chosen, "items": pd.DataFrame()}

    submission_ids = chosen["id"].astype(int).tolist()
    placeholders = ",".join("?" for _ in submission_ids)
    responses = pd.read_sql_query(
        f"SELECT * FROM responses WHERE submission_id IN ({placeholders})",
        conn,
        params=submission_ids,
    )
    question_count = len(exam["questions"])
    item_rows = []
    for number in range(1, question_count + 1):
        subset = responses[responses["question_number"] == number]
        incorrect = int((subset["is_correct"] == 0).sum())
        total = len(subset)
        item_rows.append(
            {
                "문항": number,
                "정답": exam["questions"][number - 1]["correct_answer"],
                "응답자": total,
                "오답자": incorrect,
                "오답률(%)": round(incorrect / total * 100, 1) if total else 0.0,
            }
        )
    students = chosen[
        ["class_name", "student_code", "score", "correct_count", "submitted_at"]
    ].copy()
    students.columns = ["학급", "응시코드", "점수", "정답수", "제출시각"]
    summary = {
        "응시자": int(len(students)),
        "평균": round(float(students["점수"].mean()), 1),
        "최고": round(float(students["점수"].max()), 1),
        "최저": round(float(students["점수"].min()), 1),
    }
    return {"summary": summary, "students": students, "items": pd.DataFrame(item_rows)}


def import_submissions(
    conn: sqlite3.Connection, exam_id: int, frame: pd.DataFrame
) -> tuple[int, list[str]]:
    exam = get_exam(conn, exam_id)
    required = {"class_name", "student_code"} | {
        f"q{q['number']}" for q in exam["questions"]
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"필수 열이 없습니다: {', '.join(sorted(missing))}")

    success = 0
    errors: list[str] = []
    for index, row in frame.iterrows():
        try:
            answers = {
                int(q["number"]): int(row[f"q{q['number']}"])
                for q in exam["questions"]
            }
            create_submission(
                conn,
                exam_id,
                str(row["class_name"]),
                str(row["student_code"]),
                answers,
            )
            success += 1
        except Exception as exc:  # continue importing valid rows
            errors.append(f"{index + 2}행: {exc}")
    return success, errors


def extract_pdf_text(pdf_bytes: bytes) -> str:
    from io import BytesIO

    reader = PdfReader(BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def parse_answer_text(text: str) -> list[dict[str, Any]]:
    # 일부 브라우저 생성 PDF는 글자 사이에 U+0001 같은 제어문자를 넣습니다.
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", text)
    pattern = re.compile(r"(?<!\d)(\d{1,3})\s*(?::|[.)]|번)\s*([①②③④⑤1-5])(?!\d)")
    found: dict[int, int] = {}
    for number_text, answer_text in pattern.findall(text):
        number = int(number_text)
        answer = CIRCLED[answer_text] if answer_text in CIRCLED else int(answer_text)
        if 1 <= answer <= 5:
            found[number] = answer
    if not found:
        raise ValueError("정답 PDF에서 '1:①' 형태의 정답을 찾지 못했습니다.")
    numbers = sorted(found)
    if numbers != list(range(1, max(numbers) + 1)):
        raise ValueError("추출된 문항 번호가 연속적이지 않습니다. 결과를 직접 확인하세요.")
    return [
        {"number": number, "correct_answer": found[number], "explanation": ""}
        for number in numbers
    ]


def parse_explanations(text: str) -> dict[int, str]:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", text)
    matches = list(re.finditer(r"(\d{1,3})\s*번\s*문제\s*해설", text))
    result: dict[int, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        result[int(match.group(1))] = text[match.end() : end].strip()
    return result


def parse_exam_locally(answer_pdf: bytes, explanation_pdf: bytes | None) -> dict[str, Any]:
    answer_text = extract_pdf_text(answer_pdf)
    questions = parse_answer_text(answer_text)
    explanations = (
        parse_explanations(extract_pdf_text(explanation_pdf)) if explanation_pdf else {}
    )
    for question in questions:
        question["explanation"] = explanations.get(question["number"], "")
    title = next((line.strip() for line in answer_text.splitlines() if line.strip()), "새 시험")
    return {"title": title, "subject": "화학", "unit": "", "questions": questions}


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I | re.S)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("Claude 응답에서 JSON을 찾지 못했습니다.")
        return json.loads(cleaned[start : end + 1])


def parse_exam_with_claude(
    problem_pdf: bytes | None,
    answer_pdf: bytes | None,
    explanation_pdf: bytes | None,
    api_key: str,
    subject: str,
    model: str = "claude-sonnet-5",
) -> dict[str, Any]:
    import anthropic

    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY가 설정되지 않았습니다.")
    if not any([problem_pdf, answer_pdf, explanation_pdf]):
        raise ValueError("분석할 PDF가 없습니다.")
    content: list[dict[str, Any]] = []
    document_map: list[str] = []
    if problem_pdf:
        content.append(
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": base64.standard_b64encode(problem_pdf).decode("ascii"),
                },
            }
        )
        document_map.append(f"{len(document_map) + 1}번째 PDF는 학생이 풀 문제지")
    if answer_pdf:
        content.append(
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": base64.standard_b64encode(answer_pdf).decode("ascii"),
                },
            }
        )
        document_map.append(f"{len(document_map) + 1}번째 PDF는 교사가 제공한 정답표")
    if explanation_pdf:
        content.append(
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": base64.standard_b64encode(explanation_pdf).decode("ascii"),
                },
            }
        )
        document_map.append(f"{len(document_map) + 1}번째 PDF는 교사가 제공한 해설")
    document_description = "; ".join(document_map)
    content.append(
        {
            "type": "text",
            "text": f"""
과목은 '{subject}'입니다. 자료 구성은 다음과 같습니다: {document_description}.
제공된 정답표와 해설은 문제를 직접 푼 결과보다 우선하는 권위 있는 자료입니다.
정답표가 없다면 문제를 직접 풀어 정답을 만들되, 불확실한 문항의 해설 앞에 '[교사 확인 필요]'를 붙이세요.
해설 자료가 없다면 각 문항의 핵심 풀이를 한국어로 간결하게 작성하세요.
문제지가 없고 해설 자료도 없다면 해설을 추측하지 말고 빈 문자열로 두세요.
반드시 아래 구조의 JSON 하나만 출력하세요. 마크다운 코드 블록은 쓰지 마세요.
{{
  "title": "시험명",
  "subject": "{subject}",
  "unit": "단원명",
  "questions": [
    {{"number": 1, "correct_answer": 1, "explanation": "1번 해설"}}
  ]
}}
correct_answer는 반드시 1~5의 정수여야 하며, 문항 번호는 1부터 연속되어야 합니다.
수식은 가능한 한 읽기 쉬운 일반 텍스트로 보존하세요.
""".strip(),
        }
    )
    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model=model,
        max_tokens=12000,
        messages=[{"role": "user", "content": content}],
    )
    text = "\n".join(block.text for block in message.content if block.type == "text")
    parsed = _extract_json(text)
    questions = parsed.get("questions")
    if not isinstance(questions, list) or not questions:
        raise ValueError("Claude가 문항 목록을 반환하지 않았습니다.")
    normalized = []
    for item in questions:
        normalized.append(
            {
                "number": int(item["number"]),
                "correct_answer": int(item["correct_answer"]),
                "explanation": str(item.get("explanation", "")).strip(),
            }
        )
    normalized.sort(key=lambda item: item["number"])
    if [q["number"] for q in normalized] != list(range(1, len(normalized) + 1)):
        raise ValueError("Claude가 반환한 문항 번호가 연속적이지 않습니다.")
    if any(q["correct_answer"] not in range(1, 6) for q in normalized):
        raise ValueError("Claude가 1~5 범위를 벗어난 정답을 반환했습니다.")
    return {
        "title": str(parsed.get("title", "새 시험")).strip(),
        "subject": subject,
        "unit": str(parsed.get("unit", "")).strip(),
        "questions": normalized,
    }


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
