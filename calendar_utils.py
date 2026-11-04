
import re
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from googleapiclient.discovery import build


# 캘린더 음성 명령용 프롬프트
calendar_prompt = """
당신은 김대리의 일정 관리 비서입니다.

첨부된 회의록에서 김대리에게 배정된 업무를 모두 찾아
캘린더에 등록할 일정으로 정리해주세요.

반드시 아래 형식으로 작성해주세요.

### 1. 일정 제목: 업무 이름
- **업무 내용**: 구체적인 업무 내용
- **마감일**: 마감일 또는 기한 미정
- **추가로 확인해야 할 사항**: 확인할 내용

업무가 여러 개라면 2, 3, 4 순서로 작성하세요.

정확한 날짜가 명시된 경우 YYYY-MM-DD 형식으로 적으세요.
정확한 날짜가 없다면 임의로 정하지 마세요.
'다음 주까지' 같은 표현은 그대로 유지하세요.
"""


# Google Calendar 일정 등록 함수
def add_calendar_event(calendar_credentials, title, description, due_date):

    calendar_service = build(
        "calendar",
        "v3",
        credentials=calendar_credentials
    )

    event = {
        "summary": title,
        "description": description,
        "start": {"date": due_date.isoformat()},
        "end": {"date": (due_date + timedelta(days=1)).isoformat()}
    }

    created_event = calendar_service.events().insert(
        calendarId="primary",
        body=event
    ).execute()

    return created_event.get("htmlLink")


# AI 답변에서 일정 추출 후 자동 등록
def register_calendar_tasks(calendar_credentials, answer):

    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    results = []

    # AI 답변을 업무별로 분리
    blocks = re.split(
        r"(?=^###\s*\d+\.\s*일정 제목:)",
        answer,
        flags=re.MULTILINE
    )

    for block in blocks:

        title = re.search(
            r"일정 제목:\s*(.+)", block
        )

        description = re.search(
            r"\*\*업무 내용\*\*:\s*(.+)", block
        )

        deadline = re.search(
            r"\*\*마감일\*\*:\s*(.+)", block
        )

        # 필수 정보가 없으면 건너뛰기
        if not (title and description and deadline):
            continue

        title = title.group(1).strip()
        description = description.group(1).strip()
        deadline = deadline.group(1).strip()

        # 정확한 날짜가 있는 경우
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", deadline):
            event_date = date.fromisoformat(deadline)

        # 날짜가 불명확한 경우
        else:
            event_date = today

            title = "[마감일 확인 필요] " + title

            description += f"\n원래 마감일: {deadline}"
            description += "\n등록일은 실제 마감일이 아닙니다."

        # Google Calendar에 실제 등록
        link = add_calendar_event(
            calendar_credentials,
            title,
            description,
            event_date
        )

        results.append((title, link))

    return results
