
import os, base64, json, requests
import streamlit as st
from openai import OpenAI

# OpenAI 클라이언트 생성
client = OpenAI()

# 페이지 설정
st.set_page_config(page_title="음성 AI 서비스", page_icon="🤖", layout="wide")
st.title("음성 인식 AI 서비스")
st.write("음성로 질문하시면 답변해 드립니다.")

# 역할 지정
sys_role = """
당신은 '김 대리'의 개인 비서 '에이블'입니다.
김 대리가 회의에 참석하지 못했을 때, 회의 내용을 정리해 업무를 놓치지 않도록 돕습니다.

[주요 업무]
1. 회의록을 분석하고 핵심 내용을 요약합니다.
2. 회의록에서 김 대리에게 배정된 업무 지시사항을 찾아 자세히 정리합니다.
3. 김 대리의 다양한 질문에 답변합니다.

[답변 규칙]
- 한국어로 정중하고 간결하게 답변합니다.
- 회의록에 없는 내용은 지어내지 않습니다.
- 요약을 주제 → 논의 내용 → 결정 사항 순서로 구역을 나눠서 진행합니다.
- 김 대리 담당 업무는 요약과 별도로 정리하고, 업무마다 업무명, 세부 내용, 기한을 함께 적습니다. 기한이 언급되지 않은 업무는 '기한 미정'으로 표시합니다.
- 결정 사항에는 예산, 일정, 채널처럼 구체적인 수치와 항목을 포함합니다.
- 업무 세부 내용에는 회의에서 언급된 방향과 조건을 포함합니다.
- 기한은 회의에서 해당 업무에 대해 명시적으로 언급된 경우에만 적습니다. 추측하지 않습니다.
- 이메일은 김 대리가 요청한 경우에만 보내고 이메일 본문은 마크다운 기호 없이 작성합니다.
"""

# 상태 초기화
if "messages" not in st.session_state:
    st.session_state.messages = []

if "meeting_text" not in st.session_state:
    st.session_state.meeting_text = ""

# 지시 영역
with st.form("task_form"):
    uploaded_file = st.file_uploader("텍스트 파일 업로드", type=["txt"])
    user_text = st.text_area("텍스트로 지시하기")
    audio_value = st.audio_input("무엇을 도와드릴까요?")
    submit = st.form_submit_button("실행")


# 버튼을 눌렀을 때만 실행
if submit:

    # 회의록 파일 저장
    if uploaded_file is not None:
        meeting_text = uploaded_file.read().decode("utf-8")
        st.session_state.meeting_text = meeting_text

    meeting_text = st.session_state.meeting_text

    # 음성 명령 필수
    if audio_value is None:
        st.warning("음성으로 지시해주세요.")
        st.stop()

    # 시스템 메시지
    system_content = sys_role
    if meeting_text:
        system_content += f"\n\n다음 내용을 참고하세요:\n{meeting_text}"

    
    # 이전 대화 기록 불러오기
    input_messages = [
        {"role": "system", "content": system_content}
    ] + st.session_state.messages

    # STT: 음성 인식
    transcript = client.audio.transcriptions.create(
        model="whisper-1",
        file=("audio.wav", audio_value, "audio/wav")
    )

    voice_command = transcript.text.strip()
    st.write("인식된 음성:", voice_command)

    # 회의록 음성 명령 인식
    if "회의록" in voice_command:

        if not meeting_text:
            st.warning("회의록 파일을 먼저 업로드해주세요.")
            st.stop()

        user_input = """
        첨부된 회의록을 분석하여 다음 내용을 정리하세요.

        1. 회의 주제
        2. 주요 논의 내용
        3. 결정 사항 (예산, 일정, 채널 포함)
        4. 김대리에게 배정된 업무
        5. 업무별 세부 내용과 마감일

        회의록에 없는 내용은 추측하지 마세요.
        기한이 없다면 '기한 미정'으로 표시하세요.
        """

    else:
        user_input = voice_command

    # 추가 텍스트 지시
    if user_text.strip():
        user_input += "\n추가 지시: " + user_text.strip()

    # 사용자 메시지 추가
    input_messages.append({
        "role": "user",
        "content": user_input
    })

    # LLM 호출
    with st.spinner("답변을 준비하고 있습니다..."):
        response = client.responses.create(
            model="gpt-4o-mini",
            input=input_messages,
            temperature=0
        )

        answer = response.output_text

    # 대화 기록 저장
    st.session_state.messages.append({
        "role": "user",
        "content": user_input
    })

    st.session_state.messages.append({
        "role": "assistant",
        "content": answer
    })

    # 응답 출력
    st.subheader("AI 답변")
    st.write(answer)
