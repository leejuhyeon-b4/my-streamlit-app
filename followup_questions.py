"""답변 아래 후속 질문 추천: 자동 토글 기본 OFF, 수동 생성 가능."""

import hashlib
import json

import streamlit as st
from langchain_openai import ChatOpenAI


def _generate_questions(messages):
    recent = messages[-8:]  # 현재 세션의 최근 대화 최대 8메시지
    conversation = "\n".join(
        f'{"사용자" if m["role"] == "user" else "도우미"}: {m["content"][:1800]}'
        for m in recent
    )
    lessons = st.session_state.get("review_confirmed", [])
    subjects = list(dict.fromkeys(row.get("subject", "") for row in lessons if row.get("subject")))
    study_info = ", ".join(subjects[:5]) if subjects else "일정 정보 없음"
    instruction = (
        "너는 AIVLE School 학습 도우미의 후속 질문 제안기야. "
        "최근 대화에서 사용자가 다음에 궁금해할 구체적인 질문 3개를 생성해. "
        "중복되거나 이미 답한 질문은 제외하고, 백서에 없을 수 있는 확정 일정·규정은 지어내지 마. "
        "반드시 JSON만 응답해: {\"questions\": [\"질문1\", \"질문2\", \"질문3\"]}."
    )
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.3)
    response = llm.invoke([
        ("system", instruction),
        ("human", f"학습 중인 과목: {study_info}\n\n최근 대화:\n{conversation}"),
    ])
    content = str(response.content).strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    parsed = json.loads(content)
    return [str(q).strip() for q in parsed.get("questions", []) if str(q).strip()][:3]


def render_followup_questions(messages):
    """app.py 질문/답변 코드 *아래*에서 호출."""
    st.divider()
    st.subheader("✨ 다음 질문 추천")
    auto = st.toggle(
        "답변 후 자동 추천", value=False, key="followups_auto",
        help="OFF 상태에서도 필요한 답변에 대해서만 수동 생성할 수 있습니다.",
    )

    # 새 PDF/대화 초기화 등으로 메시지가 사라진 경우 기존 추천 제거
    if not messages or messages[-1].get("role") != "assistant":
        st.session_state.followups = []
        st.session_state.followups_for = None
        st.caption("질문하고 답변을 받은 뒤 추천을 생성할 수 있어요.")
        return

    # Document 객체(docs)는 JSON 직렬화할 수 없으므로 역할과 본문만 서명에 사용
    last_pair = [
        {"role": str(m.get("role", "")), "content": str(m.get("content", ""))}
        for m in messages[-2:]
    ]
    signature_text = json.dumps(
        {"doc_id": st.session_state.get("doc_id"), "last_pair": last_pair},
        ensure_ascii=False,
    )
    signature = hashlib.sha256(signature_text.encode()).hexdigest()
    same_answer = st.session_state.get("followups_for") == signature
    if not same_answer:
        st.session_state.followups = []

    manual = st.button(
        "💡 이번 답변에서 질문 3개 생성", key="create_followups", use_container_width=True
    )
    if manual or (auto and not same_answer):
        try:
            with st.spinner("이전 대화를 참고해 후속 질문을 만드는 중..."):
                st.session_state.followups = _generate_questions(messages)
                st.session_state.followups_for = signature
        except Exception as exc:
            st.warning(f"추천 질문 생성 실패: {exc}")
            st.session_state.followups_for = signature  # 반복 호출 방지

    if st.session_state.get("followups_for") == signature:
        questions = st.session_state.get("followups", [])
        for i, question in enumerate(questions):
            if st.button(question, key=f"followup_choice_{i}", use_container_width=True):
                st.session_state.pending_question = question
                st.rerun()
        if not questions:
            st.caption("추천 결과가 없어요. 필요하면 버튼을 눌러 다시 생성해 보세요.")
    else:
        st.caption("자동 추천은 OFF입니다. 필요할 때만 생성 버튼을 눌러 주세요.")
