
import os, json, smtplib, re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import calendar as pycal

# 업무 고유 ID 생성 및 유사 업무 비교
import uuid
import hashlib
from difflib import SequenceMatcher

# ============================================================
# 다양한 업무 문서 파일 형식 지원
# ============================================================
# TXT  : 일반 텍스트
# PDF  : PDF 문서
# Excel: XLSX, XLS, CSV
# Word : DOCX
# 한글 : HWPX, HWP
# 음성 : MP3, WAV, M4A
# ============================================================

import io
import zipfile
import tempfile
import subprocess
import xml.etree.ElementTree as ET

from pathlib import Path
from pypdf import PdfReader
from docx import Document

import pandas as pd
import altair as alt
import streamlit as st
from openai import OpenAI
from email.mime.text import MIMEText

# ── 기본 설정 ─────────────────────────────────────
# 키는 환경변수에서 자동
client = OpenAI()

# 이메일 요청 시 받을 주소 (음성 인식 오류를 피하려고 코드에 고정)
MY_EMAIL = "soulsky0218@gmail.com" #수정필요 (기본수신 이메일)

# 에이블 대표 이미지 경로 (app.py와 같은 폴더에 둔 파일 이름)
IMAGE_PATH = "able.png"

# 사이드바 메뉴 (아래 셀들도 이 이름으로 화면을 나눔)
PAGES = ["🎙️ AI 비서", "📊 업무 우선순위 분석", "📅 마감 캘린더", "📨 팔로업 메일", "🎧 출근길 브리핑",
         "🍃 오늘의 할 일", "🗂️ 회의 자료실"]

# 대화 기록 최대 개수 (질문 1 + 답변 1 = 2개 → 10이면 최근 5번의 대화)
MAX_HISTORY = 10

# 주석 추가 ~~~
def kst_today():
    return datetime.now(ZoneInfo("Asia/Seoul")).date()

def kst_now_str():
    return datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S")


# 음성용 텍스트 정리: 마크다운 기호를 빼야 TTS가 매끄럽게 읽음
def to_speech_text(text):
    text = re.sub(r"[#*`>|]", "", text)              # 제목·굵게 등 기호 제거
    text = re.sub(r"^\s*-\s*", "", text, flags=re.M)  # 줄 앞 목록 기호 '-' 제거
    return text[:4000]                                # TTS 입력 길이 한도

# 에이블 역할
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
- 이메일 요청이 있으면 반드시 send_email 도구를 사용해 실제로 보냅니다. 도구를 쓰지 않고 보냈다고 말하지 않습니다.
- 받는 사람 주소는 [김 대리 이메일 주소]에 적힌 값만 사용합니다.
"""

# ── 이메일 발송 함수 (미션 1) ──
def send_email(to, subject, body):
    # Streamlit Secrets에서 Gmail 발송 계정 정보 가져오기
    sender = st.secrets.get("GMAIL_ADDRESS", os.getenv("GMAIL_ADDRESS"))
    password = st.secrets.get("GMAIL_APP_PASSWORD", os.getenv("GMAIL_APP_PASSWORD"))

    # Secrets에 값이 없으면 연결 전에 바로 안내
    if not sender or not password:
        return {"status": "failed",
                "error": "GMAIL_ADDRESS 또는 GMAIL_APP_PASSWORD가 설정되지 않았습니다."}

    msg = MIMEText(body, "plain", "utf-8")   # 한글 본문 깨짐 방지
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to

    try:
        # 465 포트: 처음부터 SSL로 연결 (클라우드 서버에서 더 안정적)
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as s:
            s.login(sender, password)
            s.sendmail(sender, to, msg.as_string())
        return {"status": "success"}
    except Exception as e:
        return {"status": "failed", "error": str(e)}

# ── 도구 정의 (미션 7)  ──
# description: LLM이 도구를 언제·어떻게 쓸지 판단하는 기준
tools = [{
    "type": "function",
    "name": "send_email",
    "description": "이메일을 발송합니다. 요청받은 내용은 한 통의 이메일로 모아서 보냅니다.",
    "parameters": {
        "type": "object",
        "properties": {
            "to": {"type": "string",
                   "description": "이메일 수신자. 예: 'test@gmail.com'"},
            "subject": {"type": "string",
                        "description": "이메일 제목. 예: '마케팅 전략 초안'"},
            "body": {"type": "string",
                     "description": "이메일 본문. 회의 요약과 김 대리 담당 업무를 모두 포함하고, 마크다운 기호(#, *, -) 없이 일반 텍스트로 작성. 예: '안녕하세요, 김 대리님. 비서 에이블입니다.'"}
        },
        "required": ["to", "subject", "body"],
        "additionalProperties": False
    },
    "strict": True
}]

# ── 이메일 요청인지 판단 ('메일' + '보내다/전송/발송'이 함께 있어야 요청으로 봄) ──
def wants_email(question):
    q = question.replace(" ", "")                        # '이 메일로'처럼 띄어 써도 인식
    has_mail = "메일" in q or "email" in q.lower()
    has_send = re.search(r"보내|전송|발송|송부", q) is not None
    return has_mail and has_send

# ── 에이블에게 질문하기 (음성·텍스트 대화 공통) ──
def ask_able(question, current_meeting):
    active_doc_name = st.session_state.active_doc
    history = st.session_state.messages_dict.get(active_doc_name, [])
    """질문 → LLM 호출(이메일 도구 포함) → (답변, 이메일 발송 결과) 반환"""
    # 프롬프트: 질문 + 고정 이메일 주소 + 회의록
    prompt = f"""
{question}

[김 대리 이메일 주소]
{MY_EMAIL}

[미팅 내용: {active_doc_name}]
{current_meeting}
"""
    # 1단계: 역할 → 이전 대화(멀티턴) → 이번 질문
    request = {
        "model": "gpt-4o-mini",
        "input": [{"role": "system", "content": sys_role}]
                 + history
                 + [{"role": "user", "content": prompt}],
        "temperature": 0
    }
    # 이메일 도구는 '메일을 보내 달라'는 요청이 확실할 때만 전달 (파이썬이 판단)
    #  - 요청이 없으면 도구 자체를 주지 않아서, 이전 대화에 메일 요청이 있어도 다시 보내지 않음
    if wants_email(question):
        request["tools"] = tools
        request["tool_choice"] = {"type": "function", "name": "send_email"}
        request["parallel_tool_calls"] = False     # 메일 한 통만
    response = client.responses.create(**request)

    tool_calls = [item for item in response.output if item.type == "function_call"]
    if not tool_calls:
        return response.output_text, None

    # 2단계: LLM이 정한 인자(to, subject, body)로 이메일 함수 실행
    tool_outputs = []
    email = None
    for item in tool_calls:
        if item.name == "send_email":
            args = json.loads(item.arguments)
            result = send_email(**args)
            email = {"to": args["to"], **result}
            tool_outputs.append({
                "type": "function_call_output",
                "call_id": item.call_id,
                "output": json.dumps(result, ensure_ascii=False)
            })

    # 3단계: 실행 결과를 LLM에 돌려주고 최종 답변 받기
    final_response = client.responses.create(
        model="gpt-4o-mini",
        previous_response_id=response.id,
        input=tool_outputs,
        temperature=0
    )
    return final_response.output_text, email

# ══════════════════════════════════════════════════
# 화면 공통: 제목 · 소개 · 세션 상태 · 사이드바 메뉴
# ══════════════════════════════════════════════════
st.set_page_config(page_title="AI 개인비서 에이블", page_icon="🤖", layout="wide")
st.title("🤖 AI 개인비서 에이블")
st.caption("다중 회의록/업무자료 분석, 스마트 캘린더, 실시간 대화 및 지출/목표 대시보드를 제공합니다.")

# 소개 영역: 왼쪽 이미지 / 오른쪽 역할·핵심 기능
left, right = st.columns([1, 5])
with left:
    if os.path.exists(IMAGE_PATH):
        st.image(IMAGE_PATH, width=180)
    else:
        st.info("🖼️ 이미지 준비 중")
with right:
    st.markdown("""
#### 김 대리의 AI 개인비서, 에이블
- 📝 회의록을 분석해 **요약하고 내 업무를 찾아** 드립니다.
- 🎙️ **음성이나 대화로 질문**하면 글과 음성으로 답하고, 요청 시 **이메일**로 보냅니다.
- 📅 업무 **우선순위**를 정리하고 마감일을 **캘린더**에 표시합니다.
""")
st.divider()

# 세션 상태 초기화 (S04: 처음 한 번만)
if "messages" not in st.session_state:
    st.session_state.messages = []        # 대화 히스토리 (음성·텍스트 대화 공통)
if "meeting_text" not in st.session_state:
    st.session_state.meeting_text = ""    # 업로드한 회의록 (메뉴를 옮겨도 유지)
if "notice" not in st.session_state:
    st.session_state.notice = None        # 최근 처리 결과 (인식된 질문, 이메일 발송 결과)
if "last_audio" not in st.session_state:
    st.session_state.last_audio = None    # 최근 답변 음성
    st.session_state.play_audio = False   # 새 답변일 때만 자동 재생

# 문서 업로드 및 문서별 대화 기록 초기화 (사이드바 실행 전에 필요)
if "uploaded_docs" not in st.session_state:
    st.session_state.uploaded_docs = {}       # {문서명: 본문내용}
# ============================================================
# 업로드 위젯 및 삭제 확인 상태 관리
# ============================================================

# 파일 업로드 위젯 초기화 버전
if "upload_widget_version" not in st.session_state:
    st.session_state.upload_widget_version = 0

# 삭제 확인 대기 문서
if "sidebar_delete_target" not in st.session_state:
    st.session_state.sidebar_delete_target = None
if "active_doc" not in st.session_state:
    st.session_state.active_doc = ""          # 현재 선택한 문서
if "archived_meetings" not in st.session_state:
    st.session_state.archived_meetings = []   # 삭제한 문서도 보존하는 아카이브
# ============================================================
# 문서 삭제 2단계 확인 상태 관리
# ============================================================
# None : 삭제 요청 없음
# 문서 ID : 해당 문서의 삭제 확인 대기
# ============================================================
if "delete_target" not in st.session_state:
    st.session_state.delete_target = None
if "messages_dict" not in st.session_state:
    st.session_state.messages_dict = {}       # 문서별 대화 기록


# 동일 문서 중복 저장 방지
# 파일명만 비교하지 않고 실제 문서 내용을 기준으로 확인
import hashlib

def get_document_hash(content):
    return hashlib.sha256(
        content.encode("utf-8")
    ).hexdigest()

# ============================================================
# 다양한 파일 형식의 내용을 텍스트로 변환
# ============================================================
# 목적:
# 사용자가 여러 종류의 마케팅 업무자료를 업로드하면
# 파일 형식에 맞게 내용을 추출하여 AI 분석에 활용
#
# 지원:
# TXT, CSV, PDF, XLSX, XLS, DOCX, HWPX
# MP3, WAV, M4A
#
# HWP는 별도의 변환 도구가 필요할 수 있음
# ============================================================

def extract_file_content(uploaded_file):

    # 1. 파일 확장자 확인
    filename = uploaded_file.name
    ext = Path(filename).suffix.lower()

    # 2. 파일을 바이트 데이터로 읽기
    file_bytes = uploaded_file.getvalue()

    # ========================================================
    # TXT 파일
    # ========================================================
    if ext == ".txt":

        for encoding in ["utf-8-sig", "cp949", "euc-kr"]:

            try:
                return file_bytes.decode(encoding)

            except UnicodeDecodeError:
                continue

        raise ValueError("TXT 파일의 문자 인코딩을 확인해 주세요.")

    # ========================================================
    # CSV 파일
    # ========================================================
    elif ext == ".csv":

        for encoding in ["utf-8-sig", "cp949", "euc-kr"]:

            try:
                df = pd.read_csv(
                    io.BytesIO(file_bytes),
                    encoding=encoding
                )
                return df.to_csv(index=False)

            except UnicodeDecodeError:
                continue

        raise ValueError("CSV 파일의 문자 인코딩을 확인해 주세요.")

    # ========================================================
    # PDF 파일
    # ========================================================
    elif ext == ".pdf":

        reader = PdfReader(io.BytesIO(file_bytes))

        pages = []

        for i, page in enumerate(reader.pages):

            page_text = page.extract_text() or ""

            pages.append(
                f"[페이지 {i + 1}]\n{page_text}"
            )

        return "\n\n".join(pages)

    # ========================================================
    # Excel 파일
    # ========================================================
    elif ext in [".xlsx", ".xls"]:

        sheets = pd.read_excel(
            io.BytesIO(file_bytes),
            sheet_name=None
        )

        result = []

        for sheet_name, df in sheets.items():

            result.append(
                f"[시트: {sheet_name}]\n"
                + df.to_csv(index=False)
            )

        return "\n\n".join(result)

    # ========================================================
    # Word 파일
    # ========================================================
    elif ext == ".docx":

        doc = Document(io.BytesIO(file_bytes))

        paragraphs = [
            p.text
            for p in doc.paragraphs
            if p.text.strip()
        ]

        # Word 문서의 표도 함께 읽기
        for table in doc.tables:

            for row in table.rows:

                paragraphs.append(
                    " | ".join(
                        cell.text
                        for cell in row.cells
                    )
                )

        return "\n".join(paragraphs)

    # ========================================================
    # 한글 HWPX 파일
    # ========================================================
    elif ext == ".hwpx":

        result = []

        with zipfile.ZipFile(io.BytesIO(file_bytes)) as z:

            section_files = sorted(
                name
                for name in z.namelist()
                if name.startswith("Contents/section")
                and name.endswith(".xml")
            )

            for section_name in section_files:

                root = ET.fromstring(z.read(section_name))

                texts = [
                    element.text
                    for element in root.iter()
                    if element.tag.split("}")[-1] == "t"
                    and element.text
                ]

                result.append("\n".join(texts))

        if not result:
            raise ValueError("HWPX 문서에서 본문을 찾지 못했습니다.")

        return "\n\n".join(result)

    # ========================================================
    # 한글 HWP 파일
    # ========================================================
    elif ext == ".hwp":

        # HWP 바이너리 파일은 HWPX와 내부 구조가 다름
        # pyhwp의 hwp5txt 도구를 이용하여 텍스트 변환
        with tempfile.TemporaryDirectory() as tmpdir:

            path = Path(tmpdir) / "uploaded.hwp"
            path.write_bytes(file_bytes)

            try:
                result = subprocess.run(
                    ["hwp5txt", str(path)],
                    capture_output=True,
                    timeout=30,
                    check=True
                )

            except FileNotFoundError:
                raise ValueError(
                    "HWP 변환 도구가 설치되지 않았습니다. "
                    "pyhwp 설치를 확인하거나 HWPX로 변환해 주세요."
                )

            except subprocess.CalledProcessError:
                raise ValueError(
                    "HWP 파일 변환에 실패했습니다. "
                    "파일 형식을 확인해 주세요."
                )

            return result.stdout.decode(
                "utf-8",
                errors="replace"
            )

    # ========================================================
    # 음성 녹음 파일
    # ========================================================
    elif ext in [".mp3", ".wav", ".m4a"]:

        # OpenAI Speech-to-Text API 사용
        transcript = client.audio.transcriptions.create(
            model="whisper-1",
            file=(filename, file_bytes)
        )

        return transcript.text

    # ========================================================
    # 지원하지 않는 파일 (파일별 처리 한계 : 구형 word, doc 파일은 지원x, .hwp-pyhwp 변환 도구가 정상 설치된 환경에서만 사용 가능, 녹음파일-OPENAI API의 파일 크기 제한이 있음)
    # ========================================================
    else:

        raise ValueError(
            f"지원하지 않는 파일 형식입니다: {ext}"
        )

# 사이드바 메뉴: 누른 메뉴의 기능만 화면에 표시
with st.sidebar:
    st.header("🤖 에이블 메뉴")
    page = st.radio("기능 선택", PAGES, label_visibility="collapsed")
    st.divider()

    # 회의록 업로드 (사이드바에 있어서 어느 메뉴에서든 올리고 바꿀 수 있음)
    st.subheader("📄 문서 업로드")
        # 지원하는 마케팅 업무자료 파일 형식
    uploaded_files = st.file_uploader(
        "업무자료 업로드",
        type=[
            "txt", "csv",
            "pdf",
            "xlsx", "xls",
            "docx",
            "hwp", "hwpx",
            "mp3", "wav", "m4a"
        ],
        accept_multiple_files=True,
        label_visibility="collapsed",
        key=f"work_file_upload_{st.session_state.upload_widget_version}"
    )

    # ============================================================
    # 업로드한 문서의 내용 추출 및 작업 목록 등록
    # ============================================================
    # 1. 파일 형식별 텍스트 추출
    # 2. 동일 파일 재등록 방지
    # 3. 현재 작업 대상 문서 자동 선택
    # 4. 추출 실패 시 오류 메시지 표시
    # ============================================================

    if uploaded_files:

        loaded_count = 0

        for uf in uploaded_files:

            # 기존 작업 목록에 없는 파일만 추가
            if uf.name not in st.session_state.uploaded_docs:

                try:

                    # 파일 형식별 텍스트 추출
                    content = extract_file_content(uf)

                    # 추출된 내용이 없는 경우
                    if not content.strip():

                        st.warning(
                            f"'{uf.name}' 파일에서 "
                            "분석할 텍스트를 찾지 못했습니다."
                        )

                        continue

                    # 기존 작업 목록에 등록
                    st.session_state.uploaded_docs[
                        uf.name
                    ] = content

                    # 현재 작업 대상으로 선택
                    st.session_state.active_doc = uf.name

                    loaded_count += 1

                except Exception as e:

                    st.error(
                        f"'{uf.name}' 파일 처리 오류: {e}"
                    )

        if loaded_count > 0:

            st.success(
                f"{loaded_count}개 파일 로드 완료!"
            )

        st.success(f"{len(uploaded_files)}개 파일 로드 완료!")

    # ============================================================
    # 현재 작업 대상 문서 선택 및 삭제
    # ============================================================
    # 기능:
    # 1. 업로드한 문서 중 작업 대상 선택
    # 2. 다른 문서로 변경
    # 3. 선택한 문서 삭제 요청
    # 4. 삭제 취소 또는 최종 삭제
    #
    # 주의:
    # 작업 목록에서 삭제해도 회의 자료실의
    # 보관 기록은 유지합니다.
    # ============================================================

    if st.session_state.uploaded_docs:

        st.divider()

        st.subheader("🎯 현재 작업 대상 문서")

        doc_names = list(
            st.session_state.uploaded_docs.keys()
        )

        # 현재 선택한 문서가 없으면 첫 번째 문서를 선택
        if st.session_state.active_doc not in doc_names:
            st.session_state.active_doc = doc_names[0]

        # 1. 작업 대상 문서 선택
        selected_doc = st.selectbox(
            "작업할 문서를 선택하세요.",
            doc_names,
            index=doc_names.index(
                st.session_state.active_doc
            ),
            key="active_document_selector"
        )

        # 선택 문서 변경
        if selected_doc != st.session_state.active_doc:

            st.session_state.active_doc = selected_doc

            # 다른 문서를 선택하면 삭제 요청 취소
            st.session_state.sidebar_delete_target = None

            st.rerun()

        # 2. 삭제 요청 버튼
        if (
            st.session_state.sidebar_delete_target
            != selected_doc
        ):

            if st.button(
                "🗑️ 선택한 문서 삭제",
                key="sidebar_delete_request",
                use_container_width=True
            ):

                st.session_state.sidebar_delete_target = (
                    selected_doc
                )

                st.rerun()

        # 3. 삭제 확인 단계
        else:

            st.warning(
                f"⚠️ '{selected_doc}' 문서를 삭제하시겠습니까?"
            )

            col_cancel, col_delete = st.columns(2)

            # 삭제 취소
            with col_cancel:

                if st.button(
                    "취소",
                    key="sidebar_delete_cancel",
                    use_container_width=True
                ):

                    st.session_state.sidebar_delete_target = None

                    st.rerun()

            # 최종 삭제
            with col_delete:

                if st.button(
                    "최종 삭제",
                    key="sidebar_delete_confirm",
                    use_container_width=True
                ):

                    # 작업 목록에서 선택 문서 제거
                    st.session_state.uploaded_docs.pop(
                        selected_doc,
                        None
                    )

                    # 문서별 대화 기록도 작업 목록에서 제거
                    st.session_state.messages_dict.pop(
                        selected_doc,
                        None
                    )

                    # 남은 문서 중 첫 번째 문서 선택
                    remaining_docs = list(
                        st.session_state.uploaded_docs.keys()
                    )

                    st.session_state.active_doc = (
                        remaining_docs[0]
                        if remaining_docs
                        else ""
                    )

                    # 삭제 확인 상태 초기화
                    st.session_state.sidebar_delete_target = None

                    # 업로드 위젯 초기화
                    # 삭제한 파일이 다시 자동 등록되는 현상 방지
                    st.session_state.upload_widget_version += 1

                    st.rerun()

    else:

        st.info("현재 등록된 작업 문서가 없습니다.")

    st.divider()
    active_history = st.session_state.messages_dict.get(st.session_state.active_doc, [])
    st.subheader("📋 히스토리 요약")
    st.markdown(f"**현재 문서:** `{st.session_state.active_doc or '선택 없음'}`")
    st.markdown(f"**대화 기록:** {len(active_history) // 2}건 (최대 {MAX_HISTORY//2}회)")

current_meeting = st.session_state.uploaded_docs.get(st.session_state.active_doc, "")


# ══════════════════════════════════════════════════
# 메뉴 1: AI 비서 (회의록 · 음성 요청 · 실시간 대화)
# ══════════════════════════════════════════════════
if page == PAGES[0]:
    col_left, col_right = st.columns([1, 1], gap="large")

    # ── 왼쪽: 회의록 업로드 + 음성 요청 ──
    with col_left:
        st.subheader("📄 문서 업로드")
        if not current_meeting:
            st.info("사이드바에서 문서 파일을 업로드하면 여기서 내용을 볼 수 있습니다.")
        else:
            with st.expander("문서 원문 미리보기", expanded=True):     # 눌러야 내용이 펼쳐짐
                st.text_area("문서 내용", current_meeting, height=230, label_visibility="collapsed")

        st.divider()
        st.subheader("🎙️ 음성으로 요청하기")
        audio_value = st.audio_input("무엇을 도와드릴까요?")
        voice_on = st.toggle("🔊 답변을 음성으로 듣기", value=True)
        run_voice = st.button("실행", type="primary", use_container_width=True)

        # 최근 처리 결과: 인식된 질문(STT 정확성 확인), 이메일 발송 결과
        notice = st.session_state.notice
        if notice:
            if notice["source"] == "voice":
                st.markdown("#### 🎙️ 인식된 질문")
                st.info(notice["question"])
            if notice["email"]:
                if notice["email"]["status"] == "success":
                    st.success(f"📧 {notice['email']['to']}(으)로 이메일을 보냈습니다.")
                else:
                    st.error(f"이메일 발송 실패: {notice['email'].get('error')}")

    # ── 오른쪽: 에이블과 실시간 대화 (음성 질문도 함께 표시) ──
    with col_right:
        st.subheader(f"💬 '{st.session_state.active_doc}' 실시간 대화")
        current_history = st.session_state.messages_dict.setdefault(st.session_state.active_doc, [])
        with st.container(height=480, border=True):
            if not current_history:
                st.caption(f"'{st.session_state.active_doc}'에 대한 첫 대화를 시작해 보세요.")
            for message in current_history:
                with st.chat_message(message["role"]):
                    st.markdown(message["content"])

        # 최근 답변 음성 (새 답변일 때만 자동 재생)
        if st.session_state.last_audio:
            st.audio(st.session_state.last_audio, format="audio/mp3",
                     autoplay=st.session_state.play_audio)
            st.session_state.play_audio = False

        with st.form("chat_form", clear_on_submit=True):
            user_input = st.text_area("에이블에게 메시지 보내기",
                                      placeholder="어떤 업무를 도와드릴까요?")
            submitted = st.form_submit_button("💬 에이블에게 보내기",
                                              type="primary", use_container_width=True)

        if st.button("🗑️ 대화 기록 지우기", use_container_width=True):
            st.session_state.messages_dict[st.session_state.active_doc] = []
            st.session_state.notice = None
            st.session_state.last_audio = None
            st.rerun()

    # ── 질문 처리: 음성(실행 버튼) 또는 텍스트(보내기 버튼) ──
    question, source = None, None
    if run_voice:
        if audio_value is None:
            col_left.warning("먼저 마이크 버튼을 눌러 질문을 녹음해 주세요.")
        else:
            try:
                with col_left, st.spinner("음성을 인식하고 있습니다.."):
                    # STT: 녹음은 파일이 아니라 메모리 데이터 → (파일명, 바이트)로 전달
                    transcript = client.audio.transcriptions.create(
                        model="whisper-1",
                        file=("recording.wav", audio_value.getvalue())
                    )
                question, source = transcript.text, "voice"
            except Exception as e:
                col_left.error(f"음성 인식 오류: {e}")
    elif submitted and user_input.strip():
        question, source = user_input.strip(), "text"

    if question:
        try:
            with col_right, st.spinner("답변을 준비하고 있습니다.."):
                answer, email = ask_able(question, current_meeting)

                # 대화 히스토리 저장 (질문·답변 한 쌍씩, 최근 MAX_HISTORY개만 유지)
                current_history.append({"role": "user", "content": question})
                current_history.append({"role": "assistant", "content": answer})
                st.session_state.messages_dict[st.session_state.active_doc] = current_history[-MAX_HISTORY:]
                st.session_state.notice = {"source": source, "question": question, "email": email}

                # LLM 최종 응답을 음성으로 즉시 재생 (미션 5·6 TTS)
                if voice_on:
                    speech = client.audio.speech.create(
                        model="tts-1",
                        voice="nova",
                        input=to_speech_text(answer),
                        instructions="비서가 담당자에게 얘기하는 정중한 말투로 빠뜨리지 말고 또박또박 읽어주세요."
                    )
                    st.session_state.last_audio = speech.content
                    st.session_state.play_audio = True
            st.rerun()
        except Exception as e:
            col_right.error(f"오류: {e}")



# ============================================================
# 메뉴 2: 업무 우선순위 분석 + 마케팅 예산 대시보드
# ============================================================

elif page == PAGES[1]:

    st.subheader("📊 업무 우선순위 분석")
    st.caption(
        "김 대리의 업무를 긴급도·중요도로 분류하고 "
        "업무 간 선행관계를 분석합니다."
    )

    # 업무 집중도 제거 / 선행관계 추가
    priority_schema = {
        "type": "json_schema",
        "name": "priority",
        "schema": {
            "type": "object",
            "properties": {
                "업무목록": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "업무명": {"type": "string"},
                            "기한": {"type": ["string", "null"]},
                            "긴급도": {
                                "type": "string",
                                "enum": ["높음", "낮음"]
                            },
                            "중요도": {
                                "type": "string",
                                "enum": ["높음", "낮음"]
                            },
                            "근거": {"type": "string"}
                        },
                        "required": [
                            "업무명", "기한", "긴급도",
                            "중요도", "근거"
                        ],
                        "additionalProperties": False
                    }
                },
                "선행관계": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "선행업무": {"type": "string"},
                            "후행업무": {"type": "string"},
                            "관계유형": {
                                "type": "string",
                                "enum": ["명시", "추정"]
                            },
                            "근거": {"type": "string"}
                        },
                        "required": [
                            "선행업무", "후행업무",
                            "관계유형", "근거"
                        ],
                        "additionalProperties": False
                    }
                }
            },
            "required": ["업무목록", "선행관계"],
            "additionalProperties": False
        }
    }

    # 우선순위 및 선행관계 분석 프롬프트
    priority_template = """
다음 회의록을 분석하세요.

1. 김 대리에게 배정된 업무를 모두 추출하세요.

- 기한은 회의록에 명시된 표현 그대로 작성하고,
  없으면 null로 작성하세요.
- 긴급도: 마감일이 가깝거나 서둘러야 한다고 언급되면
  '높음', 아니면 '낮음'
- 중요도: 신제품 출시와 매출에 직접 영향을 주면
  '높음', 보조 업무면 '낮음'
- 판단 이유는 회의록 내용을 근거로 작성하세요.

2. 김 대리의 업무 간 선행관계를 분석하세요.

- A 업무의 결과가 B 업무의 시작 또는 완료에 필요한
  경우에만 선행관계를 작성하세요.
- 선행업무와 후행업무는 업무목록의 이름과
  동일하게 작성하세요.
- 회의록에 의존관계가 명확히 언급되면 '명시',
  업무 내용상 예상되면 '추정'으로 분류하세요.
- 단순히 우선순위가 높다는 이유로
  선행관계를 만들지 마세요.
- 서로 순환하는 선행관계를 만들지 마세요.
- 관계를 찾을 수 없으면 빈 배열 []을 반환하세요.
- 각 관계의 판단 근거를 작성하세요.
- AI 추정은 확정된 사실처럼 표현하지 마세요.

3. 분석 규칙

- 회의록에 없는 업무나 마감일을 만들지 마세요.
- 명시된 사실과 AI 추정을 구분하세요.
- 다른 직원에게 업무를 위임하도록 추천하지 마세요.

[회의록]
{meeting}
"""

    # 아이젠하워 매트릭스 4분면
    def classify(urgent, important):

        if urgent == "높음" and important == "높음":
            return "Q1 · 즉시 착수"

        elif urgent == "낮음" and important == "높음":
            return "Q2 · 일정 확보"

        elif urgent == "높음" and important == "낮음":
            return "Q3 · 신속 처리 검토"

        else:
            return "Q4 · 후순위 검토"

    # 분석 실행
    if st.button("우선순위 분석"):

        if not current_meeting:
            st.warning("먼저 회의록 파일을 업로드해 주세요.")

        else:
            try:
                with st.spinner("업무를 분석하고 있습니다..."):

                    response = client.responses.create(
                        model="gpt-4o-mini",
                        input=[{
                            "role": "user",
                            "content": priority_template.format(
                                meeting=current_meeting
                            )
                        }],
                        text={"format": priority_schema},
                        temperature=0
                    )

                    data = json.loads(response.output_text)

                    st.session_state[
                        f"priority_data_{st.session_state.active_doc}"
                    ] = data

            except Exception as e:
                st.error(f"분석 오류: {e}")

    # 문서별 분석 결과 유지
    pdata = st.session_state.get(
        f"priority_data_{st.session_state.active_doc}"
    )

    if pdata:

        try:
            df_task = pd.DataFrame(pdata["업무목록"])

            if not df_task.empty:

                df_task["기한"] = (
                    df_task["기한"]
                    .fillna("기한 미정")
                    .replace("null", "기한 미정")
                )

                df_task["분류"] = df_task.apply(
                    lambda row: classify(
                        row["긴급도"],
                        row["중요도"]
                    ),
                    axis=1
                )

                # ----------------------------------------
                # ① 아이젠하워 매트릭스
                # ----------------------------------------

                st.markdown(
                    "#### 📊 업무 우선순위 분석 결과"
                )

                st.caption(
                    "긴급도와 중요도에 따라 "
                    "4개 영역으로 분류합니다."
                )

                def show_quadrant(
                    title, urgent, important, guide
                ):

                    matched = df_task[
                        (df_task["긴급도"] == urgent)
                        & (df_task["중요도"] == important)
                    ]

                    with st.container(border=True):

                        st.markdown(f"**{title}**")
                        st.caption(guide)

                        if matched.empty:
                            st.write("해당 업무 없음")

                        else:
                            for _, task in matched.iterrows():

                                st.markdown(
                                    f"- **{task['업무명']}**"
                                    f"  \n  ⏰ {task['기한']}"
                                )

                # 첫 번째 행
                col1, col2 = st.columns(2)

                with col1:
                    show_quadrant(
                        "🔴 Q1 · 즉시 착수",
                        "높음",
                        "높음",
                        "중요하고 긴급한 업무"
                    )

                with col2:
                    show_quadrant(
                        "🔵 Q2 · 일정 확보",
                        "낮음",
                        "높음",
                        "중요하지만 긴급하지 않은 업무"
                    )

                # 두 번째 행
                col3, col4 = st.columns(2)

                with col3:
                    show_quadrant(
                        "🟠 Q3 · 신속 처리 검토",
                        "높음",
                        "낮음",
                        "긴급하지만 중요도가 낮은 업무"
                    )

                with col4:
                    show_quadrant(
                        "⚪ Q4 · 후순위 검토",
                        "낮음",
                        "낮음",
                        "중요도와 긴급도가 낮은 업무"
                    )

                # ----------------------------------------
                # ② 판단 근거
                # ----------------------------------------

                st.markdown("#### 📋 판단 근거")

                st.dataframe(
                    df_task[[
                        "분류",
                        "업무명",
                        "기한",
                        "긴급도",
                        "중요도",
                        "근거"
                    ]],
                    column_config={
                        "근거": st.column_config.TextColumn(
                            "분석 근거 및 배경",
                            width="large"
                        ),
                        "업무명": st.column_config.TextColumn(
                            "배정 업무명",
                            width="medium"
                        )
                    },
                    use_container_width=True,
                    hide_index=True
                )

                st.caption(
                    "※ 긴급도와 중요도는 회의록을 "
                    "바탕으로 한 AI 판단입니다."
                )

            else:
                st.info(
                    "김 대리에게 배정된 업무가 없습니다."
                )

            # ----------------------------------------
            # ③ 업무 선행관계
            # ----------------------------------------

            st.markdown("#### 🔗 업무 선행관계")

            relations = pdata.get("선행관계", [])

            if relations:

                for relation in relations:

                    before = relation["선행업무"]
                    after = relation["후행업무"]
                    relation_type = relation["관계유형"]
                    reason = relation["근거"]

                    st.markdown(
                        f"**{before}** → **{after}**"
                    )

                    st.caption(
                        f"관계유형: {relation_type}"
                    )

                    st.write(
                        f"**판단 근거:** {reason}"
                    )

                    st.divider()

                st.caption(
                    "※ '추정' 관계는 실제 업무 의존성 "
                    "확인이 필요합니다."
                )

            else:
                st.info(
                    "회의록에서 확인하거나 추정할 수 있는 "
                    "업무 선행관계가 없습니다."
                )

        except Exception as e:
            st.error(f"분석 결과 표시 오류: {e}")

    # ----------------------------------------
    # ④ 마케팅 예산 지출 내역
    # 목표 진행 상황 표 삭제
    # 예산 표와 그래프를 화면 전체 너비로 확장
    # ----------------------------------------

    st.divider()

    st.markdown(
        "### 💰 마케팅 예산 지출 내역 (총 5,000만 원)"
    )

    st.caption(
        "아래 금액은 기존 팀원 코드에 입력된 예산 배분 예시입니다. "
        "실제 지출 내역과 자동 연동된 데이터는 아닙니다."
    )

    df_expense = pd.DataFrame({
        "항목": [
            "SNS 광고 (인스타/유튜브)",
            "인플루언서 협업",
            "숏폼/카드뉴스 제작",
            "출시 프로모션 이벤트",
            "예비비"
        ],
        "금액(백만원)": [
            25.0,
            10.0,
            8.0,
            5.0,
            2.0
        ]
    })

    # 예산 비중 자동 계산
    df_expense["비중(%)"] = (
        df_expense["금액(백만원)"]
        / df_expense["금액(백만원)"].sum()
        * 100
    ).round(1)

    # 총 예산
    st.metric(
        "총 계획 예산",
        "5,000만 원"
    )
    # 예산 표
    st.dataframe(
        df_expense[["항목", "금액(백만원)", "비중(%)"]],
        hide_index=True,
        use_container_width=True
    )
   
    # 예산 그래프: 0부터 시작하는 눈금 + 금액 표시 + X축 45도
    df_expense["표시금액"] = df_expense["금액(백만원)"].apply(
        lambda x: f"{x:g}백만원"
    )

    x_axis = alt.X(
        "항목:N",
        sort=None,
        axis=alt.Axis(
            title=None,
            labelAngle=-45,
            labelAlign="right",
            labelLimit=250
        )
    )

    y_axis = alt.Y(
        "금액(백만원):Q",
        scale=alt.Scale(
            domain=[0, 30],
            nice=False
        ),
        axis=alt.Axis(
            title="금액 (백만원)",
            values=[0, 5, 10, 15, 20, 25, 30]
        )
    )

    bars = alt.Chart(df_expense).mark_bar(
        size=55,
        cornerRadiusTopLeft=5,
        cornerRadiusTopRight=5
    ).encode(
        x=x_axis,
        y=y_axis,
        tooltip=[
            "항목:N",
            "금액(백만원):Q",
            "비중(%):Q"
        ]
    )

    labels = alt.Chart(df_expense).mark_text(
        dy=-12,
        fontSize=13,
        fontWeight="bold"
    ).encode(
        x=x_axis,
        y=y_axis,
        text="표시금액:N"
    )

    chart = (bars + labels).properties(
        height=400
    )

    st.altair_chart(
        chart,
        width="stretch"
    )

# ── 사이드바 아래쪽: 회의록 상태 · 저장된 대화 ──
with st.sidebar:
    status = "✅" if current_meeting else "⬜"

    st.subheader(f"📋 히스토리 관리 {status}")

    active_history = st.session_state.messages_dict.get(
        st.session_state.active_doc, []
    )

    st.markdown(
        f"<p style='font-size:0.875rem; margin:0;'>저장된 대화</p>"
        f"<p style='font-size:1.75rem; margin:0;'>"
        f"{len(active_history) // 2}건</p>",
        unsafe_allow_html=True
    )
