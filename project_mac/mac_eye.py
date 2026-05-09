import os
import subprocess
from fastapi import FastAPI, Body
import uvicorn
from google import genai
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv()

app = FastAPI()

# 제미나이 설정
api_key = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=api_key)
MODEL_ID = "gemini-3.1-pro-preview"

@app.get("/analyze")
async def analyze_screen():
    # 1. 화면 캡처 (macOS native screencapture 사용)
    screenshot_path = "screen.png"
    subprocess.run(["screencapture", "-x", screenshot_path])
    
    # 2. 제미나이 분석
    with open(screenshot_path, "rb") as f:
        image_bytes = f.read()
    
    response = client.models.generate_content(
        model=MODEL_ID,
        contents=[
            """이 화면을 분석해서 다음 지침을 따라줘:
        1. 화면 전체 내용을 한국어로 요약해.
        2. 만약 사용자가 특정 앱(예: 카카오톡)을 찾으라고 했다면, 그 앱 아이콘의 중심 좌표 {x, y}를 반드시 찾아내.
        3. 결과의 마지막에 'COORDINATE: {"x": 값, "y": 값}' 형식으로 좌표 정보를 포함해줘. 
           (좌표를 찾을 수 없다면 COORDINATE: None 이라고 써줘)""",
            genai.types.Part.from_bytes(data=image_bytes, mime_type="image/png")
        ]
    )
    
    return {"analysis": response.text}

# 1. AppleScript 기반 클릭 함수 (좌표 기반)
@app.post("/click")
async def remote_click(data: dict = Body(...)):
    x, y = data.get("x"), data.get("y")
    # AppleScript를 사용하여 특정 좌표 클릭 명령 생성
    script = f'tell application "System Events" to click at {{{x}, {y}}}'
    subprocess.run(["osascript", "-e", script])
    return {"status": f"Clicked at ({x}, {y}) via AppleScript"}

# 2. AppleScript 기반 문자 입력
@app.post("/type")
async def remote_type(data: dict = Body(...)):
    text = data.get("text")
    # 현재 포커스된 창에 텍스트 입력 후 엔터
    script = f'tell application "System Events" to keystroke "{text}" & return'
    subprocess.run(["osascript", "-e", script])
    return {"status": f"Typed: {text}"}

# 3. 디스플레이 깨우기 (caffeinate 활용)
@app.post("/wake")
async def wake_display():
    subprocess.run(["caffeinate", "-u", "-t", "5"])
    return {"status": "Display waked up by system command"}
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
