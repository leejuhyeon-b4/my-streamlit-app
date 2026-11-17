# ==================================================
# PART 1. 기본 코드
# 환경 설정, PDF 로딩, 청크 분할, 벡터DB 생성
# ==================================================

import os
import re
import json
import logging
import math
import hashlib
import tempfile
from pathlib import Path
from collections import Counter

import streamlit as st
import io
import pymupdf
from review_panel import render_review_sidebar
from followup_questions import render_followup_questions
from big_project_planner import render_big_project_planner
from langchain_pymupdf4llm import PyMuPDF4LLMLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_chroma import Chroma
from langchain_core.documents import Document

LOGO_PATH = Path(__file__).resolve().parent / "assets" / "ai_study_helper_banner.png"

# 1. Streamlit 페이지 설정
st.set_page_config(
    page_title="에이블 백서 | AIVLE School",
    page_icon="📘",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 2. 화면 디자인
st.markdown("""
<style>
.stApp {
    background-color: #F7F9FC;
}
[data-testid="stSidebar"] {
    background-color: #EEF3F8;
    border-right: 1px solid #DCE4EC;
}
.block-container {
    max-width: 1100px;
    padding-top: 2rem;
}
h1, h2, h3 {
    color: #17283D;
    letter-spacing: -0.03em;
}
[data-testid="stChatMessage"] {
    background: white;
    border: 1px solid #DFE7EF;
    border-radius: 12px;
}
.stButton > button {
    border-radius: 9px;
    border-color: #DCE4EC;
}
.stButton > button[kind="primary"] {
    background-color: #1D416D;
    color: white;
    border-color: #1D416D;
}
</style>
""", unsafe_allow_html=True)

# 3. OpenAI API 키 설정
try:
    API_KEY = st.secrets["OPENAI_API_KEY"]
except (KeyError, FileNotFoundError):
    API_KEY = os.getenv("OPENAI_API_KEY")

if not API_KEY:
    st.error("Streamlit Secrets에 OPENAI_API_KEY를 등록하세요.")
    st.stop()

os.environ["OPENAI_API_KEY"] = API_KEY

# 4. 임베딩 모델과 LLM 생성
@st.cache_resource(show_spinner=False)
def get_embeddings():
    return OpenAIEmbeddings(model="text-embedding-3-small")

@st.cache_resource(show_spinner=False)
def get_llm():
    return ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0
    )

# 5. 기존 ChromaDB 연결
@st.cache_resource(show_spinner=False)
def get_default_db():
    if not os.path.isdir("chroma_db"):
        return None

    db = Chroma(
        collection_name="aivle_docs",
        embedding_function=get_embeddings(),
        persist_directory="chroma_db"
    )

    count = db._collection.count()
    if count <= 0:
        return None

    # 기본 PDF와 DB가 동일한 문서에서 생성되었는지 검증
    manifest_path = "chroma_manifest.json"
    pdf_path = "AIVLE School 백서.pdf"
    if os.path.isfile(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        if manifest.get("collection") != "aivle_docs" or manifest.get("chunk_count") != count:
            raise ValueError("기본 벡터DB와 백서 인덱스 정보가 일치하지 않습니다. Colab에서 DB를 재생성해 주세요.")
        if os.path.isfile(pdf_path):
            with open(pdf_path, "rb") as f:
                actual_hash = hashlib.sha256(f.read()).hexdigest()
            if actual_hash != manifest.get("pdf_sha256"):
                raise ValueError("기본 PDF와 벡터DB의 원본 버전이 다릅니다. 백서와 DB를 함께 배포해 주세요.")
        else:
            st.warning("기본 백서 PDF 원문이 없어 DB의 원본 파일 버전을 직접 검증할 수 없습니다.")
    else:
        st.warning("기본 DB의 버전 정보가 없습니다. Colab의 DB 생성 셀부터 재실행 후 배포해 주세요.")
    return db

# 6. PDF RAG 파이프라인
@st.cache_resource(show_spinner=False)
def rag_pipeline(filepath):
    """PDF 파일 경로(filepath)를 받아 Chroma 벡터DB를 반환합니다."""
    filepath = Path(filepath)

    # 파일 경로를 기반으로 PDF 내용 추출
    docs = PyMuPDF4LLMLoader(str(filepath)).load()

    # 기존 청크 분할 설정 유지
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=900,
        chunk_overlap=160
    )
    chunks = splitter.split_documents(docs)

    if not chunks:
        raise ValueError("PDF에서 텍스트를 추출하지 못했습니다.")

    # 출처에는 업로드 시의 PDF 이름을 저장
    for chunk in chunks:
        chunk.metadata["source"] = filepath.name

    # 기존 컬렉션 식별 방식 유지: 내용의 SHA256 사용
    file_hash = hashlib.sha256(filepath.read_bytes()).hexdigest()
    db = Chroma(
        collection_name="pdf_" + file_hash[:24],
        embedding_function=get_embeddings()
    )
    db.add_documents(chunks)
    return db



# ==================================================
# PART 2. RAG 파이프라인 고도화
# BM25 + MMR + RRF + 리랭킹 + 대화 맥락
# ==================================================

# 1. 키워드 추출
def tokenize(text):
    words = re.findall(r"[가-힣a-z0-9]+", text.lower())
    tokens = words.copy()

    # 한국어 검색을 위해 2글자 단위도 추가
    for word in words:
        if re.search(r"[가-힣]", word):
            tokens.extend(
                word[i:i+2] for i in range(len(word)-1)
            )

    return tokens


# 2. BM25: 키워드 기반 문서 검색
def bm25_search(query, docs):

    if not docs:
        return []

    corpus = [
        Counter(tokenize(doc.page_content))
        for doc in docs
    ]

    n = len(corpus)
    avg_len = sum(sum(c.values()) for c in corpus) / n or 1
    query_tokens = set(tokenize(query))

    df = Counter(
        token for counter in corpus for token in counter
    )

    scores = []

    for i, counter in enumerate(corpus):
        length = sum(counter.values())
        score = 0

        for token in query_tokens:
            freq = counter[token]

            if freq == 0:
                continue

            idf = math.log(
                1 + (n - df[token] + 0.5) / (df[token] + 0.5)
            )

            score += idf * freq * 2.2 / (
                freq + 1.2 * (0.25 + 0.75 * length / avg_len)
            )

        scores.append((i, score))

    order = sorted(
        scores,
        key=lambda x: x[1],
        reverse=True
    )

    return [docs[i] for i, score in order if score > 0]


# 3. 문서 중복 제거
def doc_key(doc):
    return hashlib.sha256(
        doc.page_content.encode("utf-8")
    ).hexdigest()


def remove_duplicates(docs):
    result = []
    seen = set()

    for doc in docs:
        key = doc_key(doc)

        if key not in seen:
            seen.add(key)
            result.append(doc)

    return result


# 4. 대화 맥락 반영: 후속 질문 재작성
def rewrite_question(question, history):

    if not history:
        return question

    recent = "\n".join(
        f"{m['role']}: {m['content'][:300]}"
        for m in history[-4:]
    )

    try:
        response = get_llm().invoke([
            (
                "system",
                "이전 대화를 참고하여 마지막 질문을 "
                "독립적인 문서 검색 질문으로 재작성하세요. "
                "없는 사실은 추가하지 말고 질문만 출력하세요."
            ),
            (
                "human",
                f"대화:\n{recent}\n\n질문: {question}"
            )
        ])

        return response.content.strip() or question

    except Exception:
        return question


# 5. 하이브리드 검색: MMR + BM25
def hybrid_search(db, query, top_k=7):

    # MMR: 의미 관련성과 문서 다양성 고려
    vector_docs = db.max_marginal_relevance_search(
        query,
        k=12,
        fetch_k=24,
        lambda_mult=0.65
    )

    count = db._collection.count()

    # BM25: 문서 전체에서 키워드 검색
    if count <= 1800:
        raw = db.get(include=["documents", "metadatas"])

        lexical_pool = [
            Document(
                page_content=text,
                metadata=meta or {}
            )
            for text, meta in zip(
                raw["documents"],
                raw["metadatas"]
            )
            if text
        ]
    else:
        lexical_pool = vector_docs

    keyword_docs = bm25_search(
        query,
        lexical_pool
    )[:16]

    # RRF: 두 검색 결과의 순위를 통합
    scores = Counter()

    for rank, doc in enumerate(vector_docs):
        scores[doc_key(doc)] += 1 / (60 + rank + 1)

    for rank, doc in enumerate(keyword_docs):
        scores[doc_key(doc)] += 1 / (60 + rank + 1)

    merged = remove_duplicates(
        vector_docs + keyword_docs
    )

    merged.sort(
        key=lambda doc: scores[doc_key(doc)],
        reverse=True
    )

    return merged[:top_k]


# 6. AI 리랭킹: 관련 문서 재선별
def rerank_documents(query, candidates):

    if not candidates:
        return []

    excerpts = "\n\n".join(
        f"[{i}] {doc.page_content[:850]}"
        for i, doc in enumerate(candidates)
    )

    try:
        response = get_llm().invoke([
            (
                "system",
                "질문에 직접 관련된 문서만 선택하세요. "
                "최대 4개를 관련성 높은 순서로 선택합니다. "
                'JSON만 출력하세요. 예: {"ids":[0,2]} '
                '관련 문서가 없으면 {"ids":[]}'
            ),
            (
                "human",
                f"질문: {query}\n\n문서:\n{excerpts}"
            )
        ])

        match = re.search(
            r"\{[\s\S]*\}",
            response.content
        )

        if not match:
            return []  # 유효한 관련성 판정이 없으면 근거 없는 답변 생성을 막음

        ids = json.loads(match.group(0)).get("ids", [])

        if not isinstance(ids, list):
            return []

        valid_ids = []

        for i in ids:
            if (
                isinstance(i, int)
                and 0 <= i < len(candidates)
                and i not in valid_ids
            ):
                valid_ids.append(i)

        return [candidates[i] for i in valid_ids[:4]]

    except Exception:
        logging.exception("문서 리랭킹 실패")
        return []  # 오류를 성공한 관련성 판단으로 취급하지 않음


# 7. 시스템 프롬프트: 환각 방지 및 근거 중심 답변
SYSTEM_PROMPT = """
당신은 AIVLE School의 디지털 백서 학습 도우미입니다.

답변 규칙:
1. 제공된 백서 근거를 바탕으로 답변합니다.
2. 확인되지 않는 정보는 추측하지 않습니다.
3. 공식 정보와 교육생 경험, 일반적인 학습 조언을 구분합니다.
4. 교육 일정, 평가 기준, 수료 조건을 만들어내지 않습니다.
5. 근거를 인용할 때 [자료 1]처럼 표시합니다.
6. 답변은 간결하고 이해하기 쉽게 작성합니다.
7. 트랙은 AI 개발자와 DX 컨설턴트만 사용합니다.
"""


# 8. 최종 RAG 답변 생성
def answer_question(question, db, history=None, roadmap=False):

    history = history or []

    # 이전 대화를 반영한 검색 질문
    search_query = rewrite_question(question, history)

    # 하이브리드 검색
    candidates = hybrid_search(db, search_query)

    # AI 리랭킹 및 관련성 검사
    selected = rerank_documents(search_query, candidates)

    # 관련 문서가 없으면 답변 제한
    if not selected:
        return (
            "업로드된 백서에서 관련 근거를 확인하지 못했습니다.",
            []
        )

    # 검색 문서를 LLM에 전달
    context = "\n\n".join(
        f"[자료 {i}]\n{doc.page_content[:2300]}"
        for i, doc in enumerate(selected, 1)
    )

    recent = "\n".join(
        f"{m['role']}: {m['content'][:300]}"
        for m in history[-4:]
    )

    extra = ""

    if roadmap:
        extra = (
            "\n단계별 학습 로드맵을 작성하세요. "
            "백서에 있는 사실과 일반적인 추천 활동을 구분하세요."
        )

    response = get_llm().invoke([
        (
            "system",
            SYSTEM_PROMPT + extra + "\n\n[백서 근거]\n" + context
        ),
        (
            "human",
            f"이전 대화:\n{recent}\n\n질문: {question}"
        )
    ])

    return response.content, selected


# 9. 답변 출처 표시
def show_sources(docs):

    with st.expander(f"참고한 백서 근거 ({len(docs)}개)"):

        for i, doc in enumerate(docs, 1):
            source = os.path.basename(
                str(doc.metadata.get("source", "백서"))
            )

            st.markdown(f"**자료 {i} · {source}**")

            page = doc.metadata.get("page")

            if page is not None:
                st.caption(f"페이지 메타데이터: {page}")

            st.write(doc.page_content[:1200])

            if i < len(docs):
                st.divider()

# ==================================================
# PART 3. 학습 로드맵 및 Streamlit UI
# 챗봇, 맞춤 로드맵, 백서 탐색
# ==================================================

# 1. 사이드바 메뉴
with st.sidebar:

    if LOGO_PATH.is_file():
        st.image(str(LOGO_PATH), use_container_width=True)
    else:
        st.title("AI 학습 도우미")

    st.divider()

    menu = st.radio(
        "서비스",
        [
            "지식 어시스턴트",
            "학습 로드맵",
            "백서 탐색",
            "빅프로젝트 플래너",
            "백서 잘라 보기"
        ]
    )

    st.divider()

    # PDF 업로드
    st.markdown("**백서 PDF 등록**")

    uploaded = st.file_uploader(
        "PDF 파일 선택",
        type=["pdf"]
    )

    st.caption("업로드한 백서를 AI 검색에 활용합니다.")

    # 기존 코드에 스크린샷 일정 인식·복습 패널 연결
    render_review_sidebar()

    if st.button("대화 기록 초기화", use_container_width=True):
        st.session_state.messages = []
        st.rerun()


# 2. 업로드 PDF 또는 기존 DB 연결
try:
    if uploaded is not None:

        # Streamlit 업로드 파일을 디스크에 저장해 파일 경로로 전달
        content = uploaded.getvalue()
        doc_id = hashlib.sha256(content).hexdigest()
        safe_name = uploaded.name.replace("\\", "/").split("/")[-1]
        upload_dir = Path(tempfile.gettempdir()) / "aivle_rag_uploads" / doc_id
        upload_dir.mkdir(parents=True, exist_ok=True)
        filepath = upload_dir / safe_name
        if not filepath.is_file():
            filepath.write_bytes(content)

        with st.spinner("백서 분석 중..."):
            db = rag_pipeline(filepath)

        doc_name = uploaded.name

    else:
        db = get_default_db()
        doc_id = "default"
        doc_name = "기본 백서 DB"

except Exception as e:
    st.error(f"문서 처리 오류: {e}")
    st.stop()


# 3. 문서 변경 시 기존 대화 초기화
if st.session_state.get("doc_id") != doc_id:
    st.session_state.doc_id = doc_id
    st.session_state.messages = []
    st.session_state.pop("roadmap_result", None)
    st.session_state.pop("explore_result", None)
    st.session_state.pop("big_plan_answer", None)
    st.session_state.pop("big_plan_pdf", None)
    st.session_state.pop("followups", None)
    st.session_state.pop("followups_for", None)
    st.session_state.pop("pending_question", None)

if "messages" not in st.session_state:
    st.session_state.messages = []

# 모든 메뉴에서 문서 식별자가 동일하도록 유지 (플래너/추천 질문 공용)
st.session_state.pdf_hash = doc_id


# 4. 메인 화면
if LOGO_PATH.is_file():
    st.image(str(LOGO_PATH), width=700)
else:
    st.title("AI 학습 도우미")
st.write(
    "교육과정과 프로젝트 경험을 연결하는 AI 지식 플랫폼. "
    "백서 기반 답변과 맞춤형 학습 방향을 제공합니다."
)

st.divider()

col1, col2, col3 = st.columns(3)

with col1:
    with st.container(border=True):
        st.caption("01 / KNOWLEDGE")
        st.markdown("**근거 기반 지식 검색**")
        st.caption("하이브리드 검색과 출처 확인")

with col2:
    with st.container(border=True):
        st.caption("02 / LEARNING")
        st.markdown("**맞춤 학습 로드맵**")
        st.caption("트랙과 수준에 따른 학습 추천")

with col3:
    with st.container(border=True):
        st.caption("03 / EXPLORE")
        st.markdown("**백서 주제 탐색**")
        st.caption("교육과정과 프로젝트 정보 요약")


# 5. DB 연결 확인
if db is None:
    st.info("왼쪽에서 백서 PDF를 업로드해 주세요.")
    st.stop()


# ==================================================
# 기능 A. 지식 어시스턴트
# ==================================================

if menu == "지식 어시스턴트":

    st.subheader("AI 학습 도우미")
    st.caption("백서에 근거한 답변과 참고 문서를 제공합니다.")

    # 기존 대화 출력
    for message in st.session_state.messages:

        with st.chat_message(message["role"]):
            st.markdown(message["content"])

            if message.get("docs"):
                show_sources(message["docs"])

    # 추천 질문
    if not st.session_state.messages:

        st.markdown("**추천 질문**")

        suggestions = [
            "미니프로젝트는 어떻게 진행되나요?",
            "빅프로젝트 준비 방법을 알려주세요.",
            "AI 개발자 트랙의 핵심 역량은 무엇인가요?",
            "DX 컨설턴트 트랙의 학습 방향은 무엇인가요?"
        ]

        cols = st.columns(2)

        for i, suggestion in enumerate(suggestions):

            with cols[i % 2]:

                if st.button(
                    suggestion,
                    key=f"question_{i}",
                    use_container_width=True
                ):
                    st.session_state.pending_question = suggestion
                    st.rerun()

    # 사용자 질문 입력
    question = st.chat_input("백서에 대해 질문하세요.")

    if st.session_state.get("pending_question"):
        question = st.session_state.pop("pending_question")

    if question:

        history = st.session_state.messages.copy()

        st.session_state.messages.append({
            "role": "user",
            "content": question
        })

        with st.chat_message("user"):
            st.markdown(question)

        with st.chat_message("assistant"):

            try:
                with st.spinner("백서 근거 검색 중..."):
                    response, docs = answer_question(
                        question,
                        db,
                        history=history
                    )

                st.markdown(response)
                show_sources(docs)

                st.session_state.messages.append({
                    "role": "assistant",
                    "content": response,
                    "docs": docs
                })

            except Exception as e:
                st.error(f"답변 생성 오류: {e}")


    # 대화 내용을 바탕으로 AI 후속 질문 제안
    render_followup_questions(st.session_state.messages)

# ==================================================
# 기능 B. 맞춤 학습 로드맵
# ==================================================

elif menu == "학습 로드맵":

    st.subheader("맞춤 학습 로드맵")
    st.caption(
        "학습 트랙, 현재 수준, 목표를 입력하면 "
        "백서 기반 학습 계획을 추천합니다."
    )

    # 1. 사용자 정보 입력
    with st.form("roadmap_form"):

        track = st.selectbox(
            "학습 트랙",
            [
                "AI 개발자 트랙",
                "DX 컨설턴트 트랙"
            ]
        )

        level = st.select_slider(
            "현재 학습 수준",
            options=["입문", "기초", "중급"]
        )

        goal = st.text_area(
            "학습 목표",
            placeholder="예: 빅프로젝트를 잘 준비하고 싶어요."
        )

        submitted = st.form_submit_button(
            "맞춤 로드맵 생성",
            type="primary",
            use_container_width=True
        )

    # 2. 사용자 정보 기반 프롬프트 생성
    if submitted:

        question = (
            f"학습 트랙: {track}\n"
            f"현재 수준: {level}\n"
            f"학습 목표: {goal or '핵심 역량 강화'}\n\n"
            "위 정보를 바탕으로 단계별 학습 로드맵을 작성하세요. "
            "각 단계의 학습 내용, 추천 실습, 기대 결과를 설명하세요. "
            "백서에 있는 사실과 일반적인 학습 제안을 구분하세요."
        )

        # 3. RAG 파이프라인으로 로드맵 생성
        try:
            with st.spinner("맞춤 학습 계획 생성 중..."):
                response, docs = answer_question(
                    question,
                    db,
                    roadmap=True
                )

            st.session_state.roadmap_result = {
                "response": response,
                "docs": docs
            }

        except Exception as e:
            st.error(f"로드맵 생성 오류: {e}")

    # 4. 로드맵 결과 및 저장
    if st.session_state.get("roadmap_result"):

        result = st.session_state.roadmap_result

        st.divider()
        st.subheader("추천 학습 계획")

        st.markdown(result["response"])

        show_sources(result["docs"])

        st.download_button(
            "로드맵 다운로드",
            data=result["response"],
            file_name="aivle_roadmap.txt",
            mime="text/plain"
        )


# ==================================================
# 기능 C. 백서 주제 탐색
# ==================================================

elif menu == "백서 탐색":

    st.subheader("백서 주제 탐색")
    st.caption(f"연결 문서: {doc_name}")

    # 1. 탐색할 주제 선택
    topic = st.selectbox(
        "관심 주제를 선택하세요",
        [
            "교육과정 개요",
            "미니프로젝트",
            "빅프로젝트",
            "AI 개발자 트랙",
            "DX 컨설턴트 트랙",
            "학습 기록과 성장"
        ]
    )

    # 2. 선택한 주제를 RAG로 검색
    if st.button("백서 내용 탐색", type="primary"):

        question = (
            f"백서에서 '{topic}'에 대한 내용을 찾아 "
            "핵심 정보만 정리해 주세요. "
            "근거가 부족하면 확인되지 않는다고 설명하세요."
        )

        try:
            with st.spinner("관련 백서 내용 검색 중..."):
                response, docs = answer_question(
                    question,
                    db
                )

            st.session_state.explore_result = {
                "topic": topic,
                "response": response,
                "docs": docs
            }

        except Exception as e:
            st.error(f"백서 탐색 오류: {e}")

    # 3. 탐색 결과와 출처 출력
    if st.session_state.get("explore_result"):

        result = st.session_state.explore_result

        st.divider()
        st.subheader(result["topic"])
        st.markdown(result["response"])

        show_sources(result["docs"])

# ==================================================
# 기능 D. 빅프로젝트 AI 플래너 (기존 RAG 활용)
# ==================================================
elif menu == "빅프로젝트 플래너":
    st.subheader("빅프로젝트 맞춤 플래너")

    class _PlannerRAG:
        def invoke(self, data):
            question = data.get("input", "") if isinstance(data, dict) else str(data)
            response, _ = answer_question(question, db)
            return response

    # 기존 플래너는 rag_chain.invoke({"input": ...}) 인터페이스 사용
    st.session_state.pdf_hash = doc_id
    render_big_project_planner(_PlannerRAG())
