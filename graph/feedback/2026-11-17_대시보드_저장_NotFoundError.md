# 대시보드 저장 버튼이 NotFoundError로 실패한다

- 작성: 2026-11-17, 프로젝트 매니저
- 상황: 사용자가 web 대시보드에서 승인과 메모를 적고 저장 버튼을 눌렀다.
- 결과: 저장하지 못했다. 입력이 프로젝트에 도착하지 않았다.

## 일어난 일

1. 사용자가 `dashboard.html`에서 저장 버튼을 눌렀다.
2. 화면에 이 메시지가 나왔다: `A requested file or directory could not be found at the time an operation was processed.`
3. 프로젝트 매니저가 입력 자리를 모두 확인했다. 모두 비어 있었다.
   - `dashboard.html`의 `gf-input` 칸: `null`
   - 다운로드 폴더의 `gf-input-*.json`: 없음
   - `index/inbox.json`: 대기 0건
   - `.dashboard/approvals.json`: 2026-11-16 이후 바뀌지 않음

## 원인

### 확인한 것

- 메시지는 브라우저 File System Access API의 `NotFoundError` 문구다.
- `dashboard_html.py`의 `htmlFile()`은 IndexedDB(`gf-dashboard`)에 저장한 파일 핸들을 다시 쓴다.
- 권한이 `granted`이면 핸들이 아직 그 파일을 가리키는지 확인하지 않는다.
- `getFile()` 또는 `createWritable()`이 `NotFoundError`를 던지면 `catch`가 메시지만 보이고 끝난다.
  - 핸들을 지우지 않는다.
  - 파일을 다시 고르게 하지 않는다.
  - 다운로드 대체 경로(`gf-input-*.json`)로 가지 않는다.
- 그래서 같은 오류가 매번 되풀이된다.

### 추정 (확인하지 않음)

- 저장한 핸들이 지금 없는 경로를 가리킨다. 후보 둘:
  - 프로젝트 폴더 이름이 바뀌었다. 같은 위치에 `260207.010_금융데이터` 폴더가 따로 있다. CLAUDE.md 제목은 `_test`다.
  - `gf.atomic_write()`가 `os.replace()`로 `dashboard.html`을 갈아 끼운다. 그래서 브라우저가 옛 파일 항목을 잃었을 수 있다.

## 영향

- 사용자가 대시보드로 승인, 메모, 실행 요청을 보낼 수 없다.
- 사용자는 저장되었는지 알 수 없다. 오류 문구가 영어이고 대처 방법이 없다.

## 개선안

1. `htmlFile()`에서 `getFile()`을 `try`로 감싼다. `NotFoundError`이면 IndexedDB 핸들을 지우고 `showOpenFilePicker`로 다시 고르게 한다.
2. 저장 `catch`에서 `NotFoundError`와 `NotReadableError`이면 1번과 같이 한 번 다시 한다.
3. 다시 해도 실패하면 다운로드 대체 경로로 저장한다.
4. 오류 메시지를 한국어로 바꾸고 대처 방법을 함께 보인다.

## 임시 대처 (사용자)

1. `dashboard.html`을 연 브라우저에서 F12로 개발자 도구를 연다.
2. `Application` → `IndexedDB` → `gf-dashboard`를 지운다.
3. F5로 새로고침한다.
4. 저장 버튼을 누른다. 파일 선택 창이 나오면 이 프로젝트의 `dashboard.html`을 고른다.
