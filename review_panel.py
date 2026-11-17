"""AIVLE 복습 패널: 일정 이미지 -> 사용자 확인 -> 개인 체크리스트."""

import base64
import hashlib
import json
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

import streamlit as st
from openai import OpenAI


def _today():
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _parse_schedule(image_bytes, mime):
    """Vision 호출은 '일정 분석' 버튼을 눌렀을 때만 수행."""
    today = _today().isoformat()
    encoded = base64.b64encode(image_bytes).decode("ascii")
    instruction = f"""
너는 교육 일정 이미지에서 수업 일정을 추출하는 도우미야.
현재 한국 날짜는 {today}이고, 화면에 보이는 Today 표시는 촬영 당시 표시일 수도 있어.
반드시 이미지에서 직접 확인되는 정보만 추출해. 화면에 보이는 날짜/월/연도가
불확실하면 date를 빈 문자열로 기록하고 임의로 정하지 마.
예: 'RAG 1일차'부터 'RAG 5일차'는 별도 날짜가 보일 때 각각 별도 행으로 추출.
교육 과정이 아니라 일정 제목·교과목명이 되는 텍스트를 subject에 기록.
주차만 있고 날짜가 없으면 날짜를 만들지 마.
이미지에 Today로 표시된 날짜가 명확하면 today_marker_date에 기록해.
정확히 이 JSON 형식만 반환해:
{{"today_marker_date":"YYYY-MM-DD 또는 빈 문자열",
 "lessons":[{{"date":"YYYY-MM-DD 또는 빈 문자열",
             "subject":"교과목명","session":"1일차 등 화면에 보이는 차수"}}]}}
수업이나 날짜가 불확실하면 생략하거나 빈 문자열을 사용해. 수업명은 줄임말을 유지해.
"""
    response = OpenAI().chat.completions.create(
        model="gpt-4o-mini",
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": instruction},
            {"role": "user", "content": [
                {"type": "text", "text": "이미지에 있는 과목별 일정을 정확하게 추출해 줘."},
                {"type": "image_url", "image_url": {
                    "url": f"data:{mime};base64,{encoded}", "detail": "high"
                }},
            ]},
        ],
    )
    parsed = json.loads(response.choices[0].message.content)
    lessons = []
    for row in parsed.get("lessons", []):
        if not isinstance(row, dict):
            continue
        subject = str(row.get("subject") or "").strip()
        if not subject:
            continue
        lessons.append({
            "date": str(row.get("date") or "").strip(),
            "subject": subject,
            "session": str(row.get("session") or "").strip(),
        })
    return lessons[:80], str(parsed.get("today_marker_date") or "")


def _valid_date(value):
    try:
        return date.fromisoformat(str(value).strip())
    except (ValueError, TypeError):
        return None


def _rows_from_editor(edited):
    rows = edited.to_dict("records") if hasattr(edited, "to_dict") else list(edited)
    normalized = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        subject = str(row.get("subject") or "").strip()
        if subject:
            normalized.append({
                "date": str(row.get("date") or "").strip(),
                "subject": subject,
                "session": str(row.get("session") or "").strip(),
            })
    return normalized


def render_review_sidebar():
    """app.py에서 호출. 사이드바에 복습 기능과 링크를 출력."""
    with st.sidebar:
        st.divider()
        st.subheader("📚 나의 복습")
        today = _today()
        st.caption(f"한국 시간 기준 오늘: {today.isoformat()}")
        uploaded = st.file_uploader(
            "교육 일정 스크린샷", type=["png", "jpg", "jpeg"], key="study_schedule_image"
        )
        st.caption("이미지 분석 버튼을 누를 때만 Vision API가 호출됩니다.")

        if uploaded is not None:
            raw = uploaded.getvalue()
            picture_id = hashlib.sha256(raw).hexdigest()
            if st.session_state.get("review_upload_id") != picture_id:
                st.session_state.review_upload_id = picture_id
                st.session_state.review_draft = []
                st.session_state.review_confirmed = []
                st.session_state.review_marker = ""
                st.session_state.review_completed = {}
                if "review_schedule_editor" in st.session_state:
                    del st.session_state["review_schedule_editor"]
                # 이전 스크린샷 분석 결과를 재사용하지 않음
            if st.button("🔎 일정 이미지 분석", key="analyze_study_image", use_container_width=True):
                try:
                    mime = "image/png" if uploaded.type == "image/png" else "image/jpeg"
                    with st.spinner("이미지에서 날짜와 교과목을 읽는 중..."):
                        rows, marker = _parse_schedule(raw, mime)
                    st.session_state.review_draft = rows
                    st.session_state.review_marker = marker
                    if not rows:
                        st.warning("읽힌 일정이 없습니다. 날짜와 과목이 보이게 다시 촬영해 주세요.")
                except Exception as exc:
                    st.error(f"일정 분석 실패: {exc}")

        draft = st.session_state.get("review_draft", [])
        if draft:
            with st.expander("📝 인식된 일정 확인·수정", expanded=True):
                st.caption("잘못 읽힌 과목·날짜를 수정하고 확정해 주세요. 날짜 형식: YYYY-MM-DD")
                marker = st.session_state.get("review_marker", "")
                if marker:
                    st.caption(f"이미지의 Today 표시 날짜: {marker} (현재 날짜와 다를 수 있음)")
                edited = st.data_editor(
                    draft,
                    num_rows="dynamic",
                    use_container_width=True,
                    key="review_schedule_editor",
                    column_config={
                        "date": st.column_config.TextColumn("날짜", help="YYYY-MM-DD"),
                        "subject": st.column_config.TextColumn("교과목"),
                        "session": st.column_config.TextColumn("차수"),
                    },
                )
                if st.button("✅ 이 일정 확정", key="confirm_study_schedule", use_container_width=True):
                    confirmed = _rows_from_editor(edited)
                    bad = [x["date"] for x in confirmed if not _valid_date(x["date"])]
                    if bad:
                        st.warning("날짜가 비어 있거나 잘못된 항목이 있어요. 정확한 날짜로 수정해 주세요.")
                    elif not confirmed:
                        st.warning("저장할 일정이 없습니다.")
                    else:
                        st.session_state.review_confirmed = confirmed
                        st.session_state.review_completed = {}  # 새 일정에서 체크 상태 초기화
                        for k in list(st.session_state):
                            if k.startswith("review_checkbox_"):
                                del st.session_state[k]
                        st.success("복습 체크리스트가 생성됐어요.")

        confirmed = st.session_state.get("review_confirmed", [])
        if confirmed:
            st.caption("복습 체크리스트 · 체크 상태는 현재 접속 세션에서 유지됩니다")
            # 같은 과목에 여러 날짜가 있더라도 마지막 날짜를 종료일로 사용
            end_by_subject = {}
            for row in confirmed:
                d = _valid_date(row["date"])
                if d:
                    s = row["subject"]
                    end_by_subject[s] = max(d, end_by_subject.get(s, d))

            if not isinstance(st.session_state.get("review_completed"), dict):
                st.session_state.review_completed = {}

            ordered = sorted(confirmed, key=lambda r: (r["date"], r["subject"], r["session"]))
            for i, row in enumerate(ordered):
                identity = f'{row["date"]}|{row["subject"]}|{row["session"]}|{i}'
                widget_key = "review_checkbox_" + hashlib.md5(identity.encode()).hexdigest()
                title = f'{row["date"][5:]} · {row["subject"]} {row["session"]}'.strip()
                checked = st.checkbox(title, key=widget_key)
                st.session_state.review_completed[identity] = checked

            done = sum(bool(x) for x in st.session_state.review_completed.values())
            st.caption(f"복습 체크 완료: {done}/{len(ordered)}")
            for subject, end_date in sorted(end_by_subject.items(), key=lambda x: x[1]):
                if end_date == today:
                    st.info(f"🎓 오늘은 **{subject}** 마지막 수업일이에요. 총 복습을 진행해 주세요!")
                elif end_date > today:
                    st.caption(f"{subject} 종료 예정: {end_date.isoformat()}")


        st.divider()

        st.link_button(
    "🤖 챗지피티랑 복습하기",
    "https://chatgpt.com/",
    use_container_width=True
)

        st.link_button(
    "🎓 AIVLE-EDU 바로가기",
    "https://aivle.edu.kt.co.kr/",
    use_container_width=True
)
