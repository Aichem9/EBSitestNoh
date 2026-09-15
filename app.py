from __future__ import annotations

import hmac
import os
from pathlib import Path

import pandas as pd
import streamlit as st

from core import (
    connect,
    create_submission,
    exam_analytics,
    get_exam,
    import_submissions,
    init_db,
    list_exams,
    parse_exam_locally,
    parse_exam_with_claude,
    save_exam,
    seed_exams,
    set_exam_settings,
)


st.set_page_config(
    page_title="화학2시간 탐구영역 바로채점",
    page_icon="⚗️",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .block-container {max-width: 1120px; padding-top: 2rem;}
    [data-testid="stMetric"] {background: #f7f9fc; border: 1px solid #e5eaf0; padding: 12px; border-radius: 12px;}
    .result-good {padding: 16px; border-radius: 12px; background: #edf9f0; border: 1px solid #b8e1c2;}
    </style>
    """,
    unsafe_allow_html=True,
)


def secret(name: str, default: str = "") -> str:
    try:
        return str(st.secrets.get(name, os.getenv(name, default)))
    except Exception:
        return os.getenv(name, default)


DB_PATH = secret("DATABASE_PATH", str(Path("data") / "chem_grader.db"))
ADMIN_PASSWORD = secret("ADMIN_PASSWORD")
ANTHROPIC_API_KEY = secret("ANTHROPIC_API_KEY")
CLAUDE_MODEL = secret("CLAUDE_MODEL", "claude-sonnet-5")


@st.cache_resource
def database():
    conn = connect(DB_PATH)
    init_db(conn)
    seed_exams(conn)
    return conn


conn = database()


def circled(number: int) -> str:
    return {1: "①", 2: "②", 3: "③", 4: "④", 5: "⑤"}[int(number)]


def exam_picker(active_only: bool, key: str):
    exams = list_exams(conn, active_only=active_only)
    if not exams:
        st.info("등록된 시험이 없습니다.")
        return None
    by_title = {exam["title"]: exam for exam in exams}
    title = st.selectbox("시험 선택", list(by_title), key=key)
    return by_title[title]


def student_page() -> None:
    st.title("⚗️ 화학 바로채점")
    st.caption("응시코드와 답안만 입력하면 즉시 채점됩니다. 이름은 저장하지 않습니다.")
    picked = exam_picker(active_only=True, key="student_exam")
    if not picked:
        return
    exam = get_exam(conn, picked["id"])
    st.info(f"{exam['subject']} · {exam['unit_name']} · 총 {len(exam['questions'])}문항")

    with st.form("student_answers", clear_on_submit=False):
        left, right = st.columns(2)
        class_name = left.text_input("학급", placeholder="예: 2-3")
        student_code = right.text_input("응시코드", placeholder="예: 2307")
        st.subheader("답안 입력")
        answers: dict[int, int | None] = {}
        columns = st.columns(2)
        for index, question in enumerate(exam["questions"]):
            number = int(question["number"])
            with columns[index % 2]:
                value = st.radio(
                    f"{number}번",
                    options=[0, 1, 2, 3, 4, 5],
                    format_func=lambda x: "선택 안 함" if x == 0 else circled(x),
                    horizontal=True,
                    key=f"student_q_{exam['id']}_{number}",
                )
                answers[number] = None if value == 0 else value
        submitted = st.form_submit_button("답안 제출 및 채점", type="primary", width="stretch")

    if submitted:
        missing = [str(number) for number, value in answers.items() if value is None]
        if missing:
            st.error(f"답하지 않은 문항: {', '.join(missing)}번")
            return
        try:
            result = create_submission(
                conn,
                exam["id"],
                class_name,
                student_code,
                {number: int(value) for number, value in answers.items() if value},
            )
            st.session_state["last_result"] = result
        except ValueError as exc:
            st.error(str(exc))

    result = st.session_state.get("last_result")
    if not result or result.get("exam_title") != exam["title"]:
        return

    st.divider()
    st.subheader("채점 결과")
    a, b, c = st.columns(3)
    a.metric("점수", f"{result['score']:.1f}점")
    b.metric("정답 수", f"{result['correct_count']}/{result['question_count']}")
    c.metric("응시 횟수", f"{result['attempt']}회")
    rows = []
    for item in result["details"]:
        rows.append(
            {
                "문항": item["question_number"],
                "내 답": circled(item["selected_answer"]),
                "정답": circled(item["correct_answer"]),
                "결과": "정답" if item["is_correct"] else "오답",
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    wrong = [item for item in result["details"] if not item["is_correct"]]
    if wrong:
        st.subheader("오답 해설")
        for item in wrong:
            with st.expander(
                f"{item['question_number']}번 · 내 답 {circled(item['selected_answer'])} / 정답 {circled(item['correct_answer'])}"
            ):
                st.write(item["explanation"] or "등록된 해설이 없습니다.")
    else:
        st.success("모든 문항을 맞혔습니다!")


def admin_login() -> bool:
    if st.session_state.get("admin_authenticated"):
        return True
    st.title("🔐 교사 관리")
    if not ADMIN_PASSWORD:
        st.error("ADMIN_PASSWORD가 설정되지 않았습니다. secrets.toml을 먼저 설정하세요.")
        return False
    with st.form("admin_login"):
        entered = st.text_input("교사 비밀번호", type="password")
        login = st.form_submit_button("로그인", type="primary")
    if login:
        if hmac.compare_digest(entered, ADMIN_PASSWORD):
            st.session_state["admin_authenticated"] = True
            st.rerun()
        st.error("비밀번호가 올바르지 않습니다.")
    return False


def analytics_tab() -> None:
    picked = exam_picker(active_only=False, key="analytics_exam")
    if not picked:
        return
    analytics = exam_analytics(conn, picked["id"])
    if not analytics["summary"]:
        st.info("아직 제출된 답안이 없습니다.")
        return
    summary = analytics["summary"]
    columns = st.columns(4)
    for column, label in zip(columns, ["응시자", "평균", "최고", "최저"]):
        suffix = "명" if label == "응시자" else "점"
        column.metric(label, f"{summary[label]}{suffix}")

    st.subheader("문항별 오답률")
    chart = analytics["items"].set_index("문항")[["오답률(%)"]]
    st.bar_chart(chart, color="#e45756")
    st.dataframe(
        analytics["items"].sort_values("오답률(%)", ascending=False),
        hide_index=True,
        width="stretch",
    )
    st.subheader("학생별 결과")
    st.dataframe(analytics["students"], hide_index=True, width="stretch")
    st.download_button(
        "학생별 결과 CSV 다운로드",
        analytics["students"].to_csv(index=False).encode("utf-8-sig"),
        file_name=f"{picked['title']}_결과.csv",
        mime="text/csv",
    )


def csv_import_tab() -> None:
    picked = exam_picker(active_only=False, key="csv_exam")
    if not picked:
        return
    exam = get_exam(conn, picked["id"])
    template = {"class_name": ["2-3"], "student_code": ["2301"]}
    for question in exam["questions"]:
        template[f"q{question['number']}"] = [1]
    template_frame = pd.DataFrame(template)
    st.download_button(
        "입력 양식 CSV 다운로드",
        template_frame.to_csv(index=False).encode("utf-8-sig"),
        file_name="학생답안_입력양식.csv",
        mime="text/csv",
    )
    uploaded = st.file_uploader("작성한 CSV 업로드", type=["csv"], key="csv_upload")
    if uploaded:
        try:
            frame = pd.read_csv(uploaded, dtype={"class_name": str, "student_code": str})
            st.dataframe(frame.head(10), hide_index=True, width="stretch")
            if st.button("일괄 채점하여 저장", type="primary"):
                success, errors = import_submissions(conn, picked["id"], frame)
                st.success(f"{success}명의 답안을 저장했습니다.")
                if errors:
                    st.warning("\n".join(errors[:20]))
        except Exception as exc:
            st.error(f"CSV를 읽지 못했습니다: {exc}")


def register_exam_tab() -> None:
    st.write("정답 PDF와 해설 PDF를 올리면 문항별 정보를 추출합니다. 저장 전에 반드시 검토하세요.")
    answer_pdf = st.file_uploader("정답 PDF (필수)", type=["pdf"], key="answer_pdf")
    explanation_pdf = st.file_uploader("해설 PDF (선택)", type=["pdf"], key="explanation_pdf")
    use_claude = st.checkbox(
        "Claude로 수식·표까지 분석",
        value=bool(ANTHROPIC_API_KEY),
        disabled=not bool(ANTHROPIC_API_KEY),
        help="API 키가 없으면 PDF 텍스트에서 정답과 해설을 추출합니다.",
    )
    if not ANTHROPIC_API_KEY:
        st.caption("Claude 사용 안 함: ANTHROPIC_API_KEY가 서버에 설정되지 않았습니다.")

    if st.button("PDF 분석", type="primary", disabled=answer_pdf is None):
        try:
            with st.spinner("PDF를 분석하고 있습니다..."):
                answer_bytes = answer_pdf.getvalue()
                explanation_bytes = explanation_pdf.getvalue() if explanation_pdf else None
                if use_claude:
                    parsed = parse_exam_with_claude(
                        answer_bytes,
                        explanation_bytes,
                        ANTHROPIC_API_KEY,
                        CLAUDE_MODEL,
                    )
                else:
                    parsed = parse_exam_locally(answer_bytes, explanation_bytes)
                st.session_state["parsed_exam"] = parsed
        except Exception as exc:
            st.error(f"분석하지 못했습니다: {exc}")

    parsed = st.session_state.get("parsed_exam")
    if not parsed:
        return
    st.divider()
    st.subheader("추출 결과 검토")
    title = st.text_input("시험명", value=parsed["title"], key="parsed_title")
    left, right = st.columns(2)
    subject = left.text_input("과목", value=parsed["subject"], key="parsed_subject")
    unit = right.text_input("단원", value=parsed["unit"], key="parsed_unit")
    frame = pd.DataFrame(parsed["questions"])
    frame.columns = ["문항", "정답", "해설"]
    edited = st.data_editor(
        frame,
        hide_index=True,
        width="stretch",
        disabled=["문항"],
        column_config={
            "문항": st.column_config.NumberColumn(format="%d번"),
            "정답": st.column_config.SelectboxColumn(options=[1, 2, 3, 4, 5], required=True),
            "해설": st.column_config.TextColumn(width="large"),
        },
        key="parsed_editor",
    )
    policy_labels = {
        "최근 제출 반영": "latest",
        "최초 제출만 인정": "first",
        "최고 점수 반영": "best",
        "연습 모드": "unlimited",
    }
    policy_label = st.selectbox("재응시 정책", list(policy_labels))
    if st.button("검토한 시험 저장", type="primary"):
        try:
            questions = [
                {
                    "number": int(row["문항"]),
                    "correct_answer": int(row["정답"]),
                    "explanation": str(row["해설"] or ""),
                }
                for _, row in edited.iterrows()
            ]
            save_exam(
                conn,
                title=title,
                subject=subject,
                unit_name=unit,
                questions=questions,
                retake_policy=policy_labels[policy_label],
            )
            st.session_state.pop("parsed_exam", None)
            st.success("시험을 저장했습니다.")
        except Exception as exc:
            st.error(f"저장하지 못했습니다: {exc}")


def settings_tab() -> None:
    picked = exam_picker(active_only=False, key="settings_exam")
    if not picked:
        return
    policy_labels = {
        "최근 제출 반영": "latest",
        "최초 제출만 인정": "first",
        "최고 점수 반영": "best",
        "연습 모드": "unlimited",
    }
    reverse = {value: label for label, value in policy_labels.items()}
    active = st.checkbox("학생 화면에 시험 공개", value=bool(picked["active"]))
    policy_label = st.selectbox(
        "재응시 정책",
        list(policy_labels),
        index=list(policy_labels).index(reverse.get(picked["retake_policy"], "최근 제출 반영")),
    )
    if st.button("설정 저장", type="primary"):
        set_exam_settings(conn, picked["id"], active, policy_labels[policy_label])
        st.success("설정을 저장했습니다.")


def admin_page() -> None:
    if not admin_login():
        return
    top_left, top_right = st.columns([5, 1])
    top_left.title("📊 교사 관리")
    if top_right.button("로그아웃"):
        st.session_state["admin_authenticated"] = False
        st.rerun()
    tabs = st.tabs(["학급 분석", "CSV 일괄 입력", "시험 PDF 등록", "시험 설정"])
    with tabs[0]:
        analytics_tab()
    with tabs[1]:
        csv_import_tab()
    with tabs[2]:
        register_exam_tab()
    with tabs[3]:
        settings_tab()


page = st.sidebar.radio("메뉴", ["학생 응시", "교사 관리"])
st.sidebar.caption("학생에게는 API 키가 필요하지 않습니다.")
if page == "학생 응시":
    student_page()
else:
    admin_page()
