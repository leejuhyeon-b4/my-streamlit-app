
import streamlit as st

# ==========================================
# 1. Streamlit 기본 설정
# ==========================================

st.set_page_config(
    page_title="AIVLE School 학습 도우미",
    page_icon="📘",
    layout="wide"
)

# ==========================================
# 2. 어제 작성한 RAG 파이프라인
# ==========================================

# 문서 포맷터
def format_docs(docs):
    return "\n\n".join(d.page_content for d in docs)


# RAG 파이프라인 함수
def rag_pipeline(filepath):

    # PDF 파일 읽기
    loader = PyMuPDF4LLMLoader(filepath)
    docs = loader.load()

    # 텍스트 분할
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,
        chunk_overlap=100,
        separators=["\n\n", "\n", ".", " ", ""]
    )
    chunks = splitter.split_documents(docs)

    # Embedding 모델
    embedding = OpenAIEmbeddings(
        model="text-embedding-3-small"
    )

    # 벡터저장소 만들기
    ChromaDB = Chroma(
        collection_name="aivle_docs",
        embedding_function=embedding,
        persist_directory="chroma_db"
    )

    # 기존 데이터 비우기
    ChromaDB.reset_collection()

    # 문서 추가
    _ = ChromaDB.add_documents(chunks)

    # 검색기 구성
    aivle_retriever = ChromaDB.as_retriever(
        search_kwargs={"k": 4}
    )

    # LLM 선언
    llm = ChatOpenAI(
        model_name="gpt-4o-mini",
        temperature=0
    )

    # 프롬프트 구성
    s_msg = """
    1. 반드시 제공된 문서 내용(context)에 근거하여 답변하세요.
    2. 문서에 없는 내용은 추측하지 말고
       "업로드된 문서에서 확인되지 않습니다."라고 답하세요.
    3. 답변은 간결하고 명확하게 작성하세요.
    4. 답변의 마지막 문장에는 응원의 메세지를 추가합니다.

    [문서]
    {context}
    """

    h_msg = "{input}"
    messages = [("system", s_msg), ("human", h_msg)]

    prompt = ChatPromptTemplate.from_messages(messages)
    get_question = RunnableLambda(lambda x: x["input"])

    # RAG 체인 구성
    rag_chain = (
        {
            "context": get_question | aivle_retriever | format_docs,
            "input": get_question
        }
        | prompt
        | llm
        | StrOutputParser()
    )

    return rag_chain


# ==========================================
# 3. Session State 초기화
# ==========================================

for name, initial in {
    "messages": [],
    "pdf_hash": None,
    "pdf_name": None,
    "rag_chain": None,
}.items():
    if name not in st.session_state:
        st.session_state[name] = initial


# ==========================================
# 4. 화면 구성
# ==========================================

st.title("📘 AIVLE School 학습 도우미")
st.caption("PDF 백서를 업로드한 뒤 문서에 관해 질문하세요")

with st.sidebar:
    st.header("📂 백서 업로드")

    uploaded_file = st.file_uploader(
        "PDF 파일 선택",
        type=["pdf"]
    )

    if st.button(
        "🗑️ 대화 삭제",
        use_container_width=True
    ):
        st.session_state.messages = []
        st.rerun()

    st.caption(
        "PDF가 변경될 때만 RAG 파이프라인을 다시 구성합니다"
    )


# ==========================================
# 5. PDF 업로드 → 임시 경로 생성
# ==========================================

if uploaded_file is None:

    # PDF가 없으면 기존 체인 연결 해제
    st.session_state.rag_chain = None
    st.session_state.pdf_hash = None
    st.session_state.pdf_name = None
    st.session_state.messages = []

else:
    file_bytes = uploaded_file.getvalue()

    # 동일 파일인지 확인하기 위한 해시값
    new_hash = hashlib.sha256(file_bytes).hexdigest()

    if new_hash != st.session_state.pdf_hash:

        # 이전 문서 정보 초기화
        st.session_state.rag_chain = None
        st.session_state.pdf_hash = None
        st.session_state.messages = []

        temp_path = None

        try:
            if not file_bytes.startswith(b"%PDF-"):
                raise ValueError(
                    "올바른 PDF 파일을 업로드해 주세요"
                )

            # 핵심: 업로드한 PDF를 임시 파일로 저장
            with tempfile.NamedTemporaryFile(
                delete=False,
                suffix=".pdf"
            ) as temp_file:

                temp_file.write(file_bytes)

                # 파일 객체가 아닌 경로 문자열
                temp_path = temp_file.name

            with st.spinner(
                "문서를 읽고 RAG 검색 DB를 생성하고 있습니다..."
            ):

                # 핵심: rag_pipeline에 경로 전달
                st.session_state.rag_chain = rag_pipeline(
                    temp_path
                )

            # 정상 처리된 PDF 기록
            st.session_state.pdf_hash = new_hash
            st.session_state.pdf_name = uploaded_file.name

        except Exception as exc:
            st.error(f"PDF 처리 중 오류: {exc}")

        finally:
            # 처리 완료 후 임시 파일 삭제
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)


# ==========================================
# 6. RAG 연결 확인
# ==========================================

if st.session_state.rag_chain is None:
    st.info("왼쪽에서 PDF를 업로드하면 질문할 수 있어요")
    st.stop()

st.success(
    f"'{st.session_state.pdf_name}' 문서를 이용 중입니다"
)


# ==========================================
# 7. 추천 질문
# ==========================================

st.subheader("💡 추천 질문")

suggestions = [
    "미니프로젝트와 빅프로젝트는 어떤 점이 다른가요?",
    "빅프로젝트 주차별 권장 진행 흐름을 설명해 주세요",
    "백서에 없는 규정은 어떻게 확인해야 하나요?",
]

columns = st.columns(3)
selected_question = None

for idx, suggestion in enumerate(suggestions):

    if columns[idx].button(
        suggestion,
        key=f"sample_question_{idx}",
        use_container_width=True
    ):
        selected_question = suggestion


# ==========================================
# 8. 이전 대화 출력
# ==========================================

for message in st.session_state.messages:

    with st.chat_message(message["role"]):
        st.markdown(message["content"])


# ==========================================
# 9. 질문 → RAG 답변
# ==========================================

user_input = st.chat_input(
    "백서에 대해 질문해 주세요"
)

question = selected_question or user_input

if question:

    # 사용자 질문 출력
    st.chat_message("user").markdown(question)

    st.session_state.messages.append({
        "role": "user",
        "content": question
    })

    # AI 답변 출력
    with st.chat_message("assistant"):

        try:
            answer = st.write_stream(
                st.session_state.rag_chain.stream({
                    "input": question
                })
            )

            st.session_state.messages.append({
                "role": "assistant",
                "content": answer
            })

        except Exception as exc:
            st.error(f"답변 생성 실패: {exc}")


st.caption(
    "현재는 이전 대화를 화면에만 저장합니다. "
    "후속 질문의 맥락 이해는 별도 구현해야 합니다"
)


