
import re
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from googleapiclient.discovery import build


# 캘린더 음성 명령용 프롬프트
calendar_prompt = """
당신은 김대리의 일정 관리 비서입니다.

회의록에서 김대리에게 배정된 업무만 찾아
캘린더에 등록할 일정으로 정리해주세요.

각 업무를 반드시 다음 형식으로 작성하세요.

### 1. 일정 제목: 업무 이름
- **업무 내용**: 세부 업무 내용
- **마감일**: 회의록에서 언급된 기한
- **추가로 확인해야 할 사항**: 확인 내용

업무가 여러 개라면 2, 3 순서로 번호를 붙이세요.

정확한 날짜가 명시됐다면 YYYY-MM-DD로 작성하세요.
'다음 주까지' 같은 상대적 기한은 그대로 적으세요.
마감일 언급이 없으면 '기한 미정'이라고 적으세요.
임의로 마감일을 만들지 마세요.
"""


# Google Calendar 일정 등록 함수
def add_calendar_event(
    calendar_credentials,
    title,
    description,
    due_date,
    end_date=None
):

    calendar_service = build(
        "calendar",
        "v3",
        credentials=calendar_credentials
    )

    # 종료일을 지정하지 않으면 하루 일정
    last_day = end_date if end_date is not None else due_date

    event = {
        "summary": title,
        "description": description,
        "start": {"date": due_date.isoformat()},
        "end": {
            "date": (last_day + timedelta(days=1)).isoformat()
        }
    }

    created_event = calendar_service.events().insert(
        calendarId="primary",
        body=event
    ).execute()

    return created_event.get("htmlLink")


# AI 답변에서 업무 추출
def extract_calendar_tasks(answer):

    tasks = []

    blocks = re.split(
        r"(?=^###\s*\d+\.\s*일정 제목:)",
        answer,
        flags=re.MULTILINE
    )

    for block in blocks:

        title = re.search(
            r"^###\s*\d+\.\s*일정 제목:\s*(.+)$",
            block,
            re.MULTILINE
        )

        content = re.search(
            r"\*\*업무 내용\*\*:\s*(.+)",
            block
        )

        deadline = re.search(
            r"\*\*마감일\*\*:\s*(.+)",
            block
        )

        if title and content and deadline:
            tasks.append({
                "title": title.group(1).strip(),
                "description": content.group(1).strip(),
                "deadline": deadline.group(1).strip()
            })

    return tasks


# 회의록의 기한을 캘린더 날짜로 변환
def get_calendar_dates(deadline):

    today = datetime.now(
        ZoneInfo("Asia/Seoul")
    ).date()

    deadline = re.sub(r"\s+", "", deadline)

    # 정확한 날짜
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", deadline):

        day = date.fromisoformat(deadline)
        return day, day

    # 다음 주 → 월요일부터 금요일까지
    if re.fullmatch(
        r"다음주(까지|동안|내|내에)?",
        deadline
    ):

        next_monday = today + timedelta(
            days=7 - today.weekday()
        )

        next_friday = next_monday + timedelta(days=4)

        return next_monday, next_friday

    # 기한 미정 또는 해석할 수 없는 날짜
    return None
