
import json
import hashlib
import streamlit as st
from openai import OpenAI
from google.oauth2.credentials import Credentials

from priority import priority_prompt
from calendar_utils import (
    calendar_prompt,
    add_calendar_event,
    extract_calendar_tasks,
    get_calendar_dates
)


# OpenAI 클라이언트
client = OpenAI()

# 페이지 설정
st.set_page_config(
    page_title="음성 AI 서비스",
    page_icon="🤖",
    layout="wide"
)

st.title("음성 인식 AI 서비스")
st.write("음성 또는 텍스트로 질문하시면 답변해 드립니다.")


# 역할 지정
sys_role = """
당신은 '김 대리'의 개인 비서 '에이블'입니다.
김 대리가 회의에 참석하지 못했을 때,
회의 내용을 정리해 업무를 놓치지 않도록 돕습니다.

[주요 업무]
1. 회의록을 분석하고 핵심 내용을 요약합니다.
2. 회의록에서 김 대리에게 배정된 업무 지시사항을 찾아 자세히 정리합니다.
3. 김 대리의 다양한 질문에 답변합니다.

[답변 규칙]
- 한국어로 정중하고 간결하게 답변합니다.
- 회의록에 없는 내용은 지어내지 않습니다.
- 요약을 주제 → 논의 내용 → 결정 사항 순서로 나눕니다.
- 김 대리 담당 업무는 별도로 정리합니다.
- 업무명, 세부 내용, 기한을 함께 적습니다.
- 기한이 없으면 '기한 미정'으로 표시합니다.
- 결정 사항에는 예산, 일정, 채널을 포함합니다.
- 업무의 방향과 조건을 구체적으로 정리합니다.
- 기한은 회의록에 언급된 경우에만 적습니다.
- 추측하지 않습니다.
- 이메일은 요청한 경우에만 보냅니다.
- 이메일 본문에는 마크다운 기호를 사용하지 않습니다.
"""


# 상태 초기화
if "messages" not in st.session_state:
    st.session_state.messages = []

if "meeting_text" not in st.session_state:
    st.session_state.meeting_text = ""

if "calendar_pending" not in st.session_state:
    st.session_state.calendar_pending = []

if "calendar_done" not in st.session_state:
    st.session_state.calendar_done = {}

if "calendar_links" not in st.session_state:
    st.session_state.calendar_links = []

if "calendar_meeting_id" not in st.session_state:
    st.session_state.calendar_meeting_id = None


# 입력 영역
with st.form("task_form"):

    uploaded_file = st.file_uploader(
        "텍스트 파일 업로드",
        type=["txt"]
    )

    user_text = st.text_area("텍스트로 지시하기")

    audio_value = st.audio_input(
        "무엇을 도와드릴까요?"
    )

    submit = st.form_submit_button("실행")


# 실행 버튼
if submit:

    # 회의록 저장
    if uploaded_file is not None:
        meeting_text = uploaded_file.getvalue().decode("utf-8")
        st.session_state.meeting_text = meeting_text

    meeting_text = st.session_state.meeting_text

    # 입력 확인
    if audio_value is None and not user_text.strip():
        st.warning("음성 또는 텍스트로 지시해주세요.")
        st.stop()

    # 시스템 메시지
    system_content = sys_role

    if meeting_text:
        system_content += (
            f"\n\n다음 내용을 참고하세요:\n{meeting_text}"
        )

    # 이전 대화 기록
    input_messages = [
        {"role": "system", "content": system_content}
    ] + st.session_state.messages

    # 음성 또는 텍스트 인식
    if audio_value is not None:

        transcript = client.audio.transcriptions.create(
            model="gpt-transcribe",
            file=("audio.wav", audio_value, "audio/wav"),
            language="ko"
        )

        voice_command = transcript.text.strip()

    else:
        voice_command = user_text.strip()

    st.write("인식된 명령:", voice_command)

    is_calendar_command = False

    # 회의록 명령
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

    # 우선순위 명령
    elif "우선순위" in voice_command.replace(" ", ""):

        if not meeting_text:
            st.warning("회의록 파일을 먼저 업로드해주세요.")
            st.stop()

        user_input = priority_prompt

    # 캘린더 명령
    elif "캘린더" in voice_command.replace(" ", ""):

        if not meeting_text:
            st.warning("회의록 파일을 먼저 업로드해주세요.")
            st.stop()

        user_input = calendar_prompt
        is_calendar_command = True

    else:
        user_input = voice_command

    # 추가 텍스트 지시
    if user_text.strip() and audio_value is not None:
        user_input += (
            "\n추가 지시: " + user_text.strip()
        )

    # 사용자 메시지
    input_messages.append({
        "role": "user",
        "content": user_input
    })

    # AI 답변 생성
    with st.spinner("답변을 준비하고 있습니다..."):

        response = client.responses.create(
            model="gpt-4o-mini",
            input=input_messages,
            temperature=0
        )

        answer = response.output_text

    # 멀티턴 기록
    st.session_state.messages.append({
        "role": "user",
        "content": user_input
    })

    st.session_state.messages.append({
        "role": "assistant",
        "content": answer
    })

    st.subheader("AI 답변")
    st.write(answer)

    # 캘린더 자동 등록
    if is_calendar_command:

        meeting_id = hashlib.sha256(
            meeting_text.encode()
        ).hexdigest()

        # 다른 회의록이면 캘린더 처리 상태 초기화
        if st.session_state.calendar_meeting_id != meeting_id:

            st.session_state.calendar_meeting_id = meeting_id
            st.session_state.calendar_pending = []
            st.session_state.calendar_done = {}
            st.session_state.calendar_links = []

        tasks = extract_calendar_tasks(answer)

        if not tasks:
            st.warning("등록할 일정을 찾지 못했습니다.")

        pending_keys = {
            task["key"]
            for task in st.session_state.calendar_pending
        }

        # 일정별 처리
        for i, task in enumerate(tasks):

            task_key = f"{i}:{task['title']}"

            # 이미 처리한 업무는 중복 등록하지 않음
            if (
                task_key in st.session_state.calendar_done
                or task_key in pending_keys
            ):
                continue

            dates = get_calendar_dates(task["deadline"])

            # 기한 미정 → 사용자 선택 대기
            if dates is None:

                st.session_state.calendar_pending.append({
                    **task,
                    "key": task_key
                })

                pending_keys.add(task_key)
                continue

            # 날짜가 있는 업무 → 자동 등록
            start_date, end_date = dates

            description = (
                task["description"]
                + f"\n회의록 기한: {task['deadline']}"
            )

            if start_date != end_date:
                description += (
                    "\n다음 주 월~금으로 등록한 기간 일정입니다."
                )

            try:
                credentials = Credentials.from_authorized_user_info(
                    json.loads(
                        st.secrets["calendar_oauth_json"]
                    )
                )

                link = add_calendar_event(
                    credentials,
                    task["title"],
                    description,
                    start_date,
                    end_date
                )

                st.session_state.calendar_done[task_key] = "등록"

                st.session_state.calendar_links.append(
                    (task["title"], link)
                )

            except Exception as e:
                st.error(
                    f"'{task['title']}' 등록 실패: "
                    f"{type(e).__name__}"
                )

        if st.session_state.calendar_pending:
            st.info(
                "기한 미정 업무는 아래에서 "
                "건너뛰거나 날짜를 직접 선택해주세요."
            )


# 기한 미정 업무 선택 영역
# 메인 실행 버튼과 별개로 표시
if st.session_state.calendar_pending:

    st.subheader("📅 기한 미정 업무 확인")

    choices = {}

    for i, task in enumerate(
        st.session_state.calendar_pending
    ):

        st.write(
            f"**{task['title']}** — "
            "기한이 없습니다. 건너뛸까요?"
        )

        selected = st.radio(
            "선택",
            ["예, 건너뛰기", "직접 입력"],
            key=f"calendar_choice_{i}",
            label_visibility="collapsed"
        )

        selected_date = None

        if selected == "직접 입력":

            selected_date = st.date_input(
                "등록할 날짜",
                value=None,
                key=f"calendar_date_{i}"
            )

        choices[task["key"]] = (
            selected,
            selected_date
        )

    # 기한 미정 업무 선택 적용
    if st.button("선택 내용 적용"):

        # 직접 입력인데 날짜가 없으면 등록하지 않음
        missing_date = any(
            choice == "직접 입력" and day is None
            for choice, day in choices.values()
        )

        if missing_date:
            st.warning(
                "직접 입력을 선택한 업무의 "
                "날짜를 지정해주세요."
            )

        else:
            remaining = []

            for task in st.session_state.calendar_pending:

                choice, day = choices[task["key"]]

                # 건너뛰기
                if choice == "예, 건너뛰기":

                    st.session_state.calendar_done[
                        task["key"]
                    ] = "건너뜀"

                    continue

                # 직접 입력한 날짜로 등록
                try:
                    credentials = (
                        Credentials.from_authorized_user_info(
                            json.loads(
                                st.secrets["calendar_oauth_json"]
                            )
                        )
                    )

                    description = (
                        task["description"]
                        + f"\n원래 기한: {task['deadline']}"
                        + "\n사용자가 지정한 등록 날짜입니다."
                    )

                    link = add_calendar_event(
                        credentials,
                        task["title"],
                        description,
                        day
                    )

                    st.session_state.calendar_done[
                        task["key"]
                    ] = "등록"

                    st.session_state.calendar_links.append(
                        (task["title"], link)
                    )

                except Exception as e:

                    remaining.append(task)

                    st.error(
                        f"'{task['title']}' 등록 실패: "
                        f"{type(e).__name__}"
                    )

            st.session_state.calendar_pending = remaining

            if not remaining:
                st.success("선택한 내용을 적용했습니다.")


# 등록 결과 표시
if st.session_state.calendar_links:

    st.subheader("Google Calendar 등록 내역")

    for title, link in st.session_state.calendar_links:
        st.markdown(f"- [{title}]({link})")

