"""백서 검색과 연결된 빅프로젝트 주차별 맞춤 플래너."""

import streamlit as st


def render_big_project_planner(rag_chain):
    with st.expander("🗓️ 빅프로젝트 맞춤 플래너", expanded=False):
        st.caption("백서에서 확인되는 프로젝트 정보를 참고해 1~8주차 팀 실행안을 제안합니다. 백서에 없는 주차별 계획은 AI 제안으로 구분합니다.")
        with st.form("big_project_form"):
            col1, col2 = st.columns(2)
            with col1:
                week = st.selectbox("현재 진행 주차", list(range(1, 9)), format_func=lambda x: f"{x}주차")
            with col2:
                team_size = st.number_input("팀원 수", min_value=1, max_value=20, value=4, step=1)
            progress = st.text_area("현재까지 완료한 작업", placeholder="예: 주제 선정, 요구사항 정의 완료")
            blocker = st.text_area("지금 막힌 부분", placeholder="예: 데이터 확보가 늦어져 모델링 시작 못함")
            submit = st.form_submit_button("📋 우리 팀 실행 계획 생성", use_container_width=True)

        if submit:
            if rag_chain is None:
                st.warning("먼저 백서 PDF를 업로드해 주세요.")
                return
            prompt = f"""
나는 KT AIVLE School 빅프로젝트를 수행 중이다.
현재 {week}주차, 팀원 수 {team_size}명이다.
완료한 작업: {progress.strip() or '미입력'}
지금 막힌 부분: {blocker.strip() or '미입력'}

업로드된 백서에서 '{week}주차 빅프로젝트 권장 진도'와 관련된 내용을 찾아,
다음 항목을 제시해 줘.
1) 백서가 제시하는 해당 주차 권장 목표
2) 지금 팀 상황에서 우선할 실행 작업 3가지
3) {team_size}명 팀 기준 역할 분담 예시
4) 지연 위험 및 점검할 내용
반드시 백서의 공식 사실/과거 기수 참고 사례/권장안을 구별해 줘.
백서에서 근거를 찾지 못한 부분은 'AI 제안'으로 구분하고
확정 일정이나 사실인 것처럼 표현하지 마.
"""
            try:
                with st.spinner("백서에서 관련 내용을 검색하고 실행 계획을 만드는 중..."):
                    answer = rag_chain.invoke({"input": prompt})
                st.session_state.big_plan_answer = str(answer)
                st.session_state.big_plan_pdf = st.session_state.get("pdf_hash")
            except Exception as exc:
                st.error(f"플래너 생성 오류: {exc}")

        if (st.session_state.get("big_plan_answer")
                and st.session_state.get("big_plan_pdf") == st.session_state.get("pdf_hash")):
            st.markdown(st.session_state.big_plan_answer)
