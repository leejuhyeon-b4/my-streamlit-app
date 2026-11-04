
from datetime import timedelta
from googleapiclient.discovery import build

# 캘린더 음성 명령용 프롬프트
calendar_prompt = """
당신은 김대리의 일정 관리 비서입니다.

첨부된 회의록에서 김대리에게 배정된 업무를 모두 찾아
캘린더에 등록할 일정으로 정리해주세요.

각 일정에 대해 다음 정보를 알려주세요.
1. 일정 제목
2. 업무 내용
3. 마감일
4. 추가로 확인해야 할 사항

정확한 날짜나 시간이 명시되지 않았다면
임의로 정하지 말고 '확인 필요'라고 알려주세요.
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
