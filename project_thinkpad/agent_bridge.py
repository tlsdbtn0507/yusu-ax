import asyncio
import os
from dotenv import load_dotenv
import httpx
import re
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from telegram.error import BadRequest
from ollama import AsyncClient
from wakeonlan import send_magic_packet

load_dotenv()

# 1. 텔레그램에서 받은 토큰을 여기에 넣으세요
TOKEN = os.getenv('TELEGRAM_TOKEN')
_allowed_chat_id = os.getenv('ALLOWED_CHAT_ID')
ALLOWED_CHAT_ID = int(_allowed_chat_id) if _allowed_chat_id else None

def wake_mac():
    mac_address = os.getenv('MAC_ADDRESS')
    print(f"WOL 매직 패킷 전송 중... 대상 MAC: {mac_address}")
    send_magic_packet(mac_address)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # [보안 로직] 지정된 chat_id가 설정되어 있고 일치하지 않으면 무시
    if ALLOWED_CHAT_ID is not None and update.message.chat_id != ALLOWED_CHAT_ID:
        print(f"인가되지 않은 접근 시도 차단 - chat_id: {update.message.chat_id}")
        return

    user_text = update.message.text or update.message.caption or ""
    is_photo = bool(update.message.photo)
    print(f"아이폰으로부터 수신: {user_text} (사진 첨부: {is_photo})")
    
    # [UX 1] 초기 반응: 즉시 메시지 전송 및 객체 저장
    status_message = await update.message.reply_text("🤔 생각 중...")
    
    trigger_words = ["화면", "스캔"]
    is_trigger = is_photo or any(word in user_text for word in trigger_words)
    
    try:
        if is_trigger:
            # 외부 API로 화면/스캔 분석 요청
            await context.bot.edit_message_text(
                chat_id=update.message.chat_id,
                message_id=status_message.message_id,
                text="🤔 맥북의 화면을 스캔하고 있습니다..."
            )
            
            try:
                wake_mac()
                await asyncio.sleep(2)
                
                async with httpx.AsyncClient() as http_client:
                    # 타임아웃 60초 설정
                    response = await http_client.get("http://192.168.55.150:8000/analyze", timeout=60.0)
                    response.raise_for_status()
                    data = response.json()
                    analysis = data.get("analysis", "분석 결과를 찾을 수 없습니다.")
                    
                    await context.bot.edit_message_text(
                        chat_id=update.message.chat_id,
                        message_id=status_message.message_id,
                        text=analysis
                    )
                    
                    coord_match = re.search(r'COORDINATE:\s*\{"?[xy]"?:\s*(\d+),\s*"?[xy]"?:\s*(\d+)\}', analysis)
                    if coord_match and any(word in user_text for word in ["클릭", "눌러", "실행", "열어"]):
                        x = int(coord_match.group(1))
                        y = int(coord_match.group(2))
                        print(f"좌표 발견: {x}, {y}. 클릭 시도 중...")
                        try:
                            click_response = await http_client.post(
                                "http://192.168.55.150:8000/click",
                                json={"x": x, "y": y},
                                timeout=10.0
                            )
                            click_response.raise_for_status()
                            print(f"클릭 응답: {click_response.json()}")
                            await update.message.reply_text("좌표를 찾아 클릭을 수행했습니다")
                        except Exception as click_e:
                            print(f"클릭 요청 실패: {click_e}")
            except httpx.ConnectError:
                error_text = "❌ 맥북이 아직 깨어나지 않았거나 서버가 꺼져 있습니다."
                await context.bot.edit_message_text(chat_id=update.message.chat_id, message_id=status_message.message_id, text=error_text)
            except httpx.TimeoutException:
                error_text = "⏳ 맥북의 응답이 너무 늦습니다. 제미나이 분석 시간이 길어지고 있으니 잠시 후 다시 시도하세요. (Timeout: 60s)"
                await context.bot.edit_message_text(chat_id=update.message.chat_id, message_id=status_message.message_id, text=error_text)
            except httpx.HTTPStatusError as e:
                error_text = f"⚠️ 맥북 서버 응답 오류 (Status: {e.response.status_code}). 제미나이 API 키나 할당량을 확인하세요."
                await context.bot.edit_message_text(chat_id=update.message.chat_id, message_id=status_message.message_id, text=error_text)
            except Exception as e:
                error_text = f"⚠️ 시스템 오류가 발생했습니다: {str(e)}"
                await context.bot.edit_message_text(chat_id=update.message.chat_id, message_id=status_message.message_id, text=error_text)
        else:
            # [UX 2] 작업 상태 전환: Ollama 서버와 연결하여 답변 생성 시작 준비
            await context.bot.edit_message_text(
                chat_id=update.message.chat_id,
                message_id=status_message.message_id,
                text="⚡ 답변 생성 중..."
            )
            
            # [UX 3] 스트리밍 구현: AsyncClient를 이용해 비동기 스트리밍 활성화
            client = AsyncClient()
            response_stream = await client.chat(
                model='gemma4:latest', 
                messages=[{'role': 'user', 'content': user_text}],
                stream=True
            )
            
            full_text = ""
            last_updated_text = "⚡ 답변 생성 중..."
            last_updated_time = asyncio.get_event_loop().time()
            
            # [UX 5] 속도 최적화: 1.0초 간격으로 업데이트 (Throttling)
            update_interval = 1.0  
            
            # [UX 4] 실시간 업데이트: 청크를 누적하며 업데이트
            async for chunk in response_stream:
                content = chunk['message']['content']
                if content:
                    full_text += content
                    current_time = asyncio.get_event_loop().time()
                    
                    # 정해진 주기가 지났을 때 텔레그램 메시지 업데이트
                    if current_time - last_updated_time >= update_interval:
                        display_text = full_text + " ⚡"
                        
                        if display_text != last_updated_text:
                            try:
                                await context.bot.edit_message_text(
                                    chat_id=update.message.chat_id,
                                    message_id=status_message.message_id,
                                    text=display_text
                                )
                                last_updated_text = display_text
                                last_updated_time = current_time
                            except BadRequest as e:
                                # 텍스트가 변경되지 않은 경우 발생하는 에러 등은 무시
                                if "not modified" not in str(e).lower():
                                    print(f"메시지 업데이트 에러: {e}")
                            except Exception as e:
                                print(f"예상치 못한 에러: {e}")
                                
            # 스트리밍이 모두 완료된 후, 최종 텍스트로 깔끔하게 업데이트 (진행중 아이콘 제거)
            if full_text and full_text != last_updated_text:
                try:
                    await context.bot.edit_message_text(
                        chat_id=update.message.chat_id,
                        message_id=status_message.message_id,
                        text=full_text
                    )
                except BadRequest:
                    pass

    except Exception as e:
        # [UX 6] 에러 발생 시 사용자에게 알림
        print(f"오류 발생: {e}")
        error_msg = f"⚠️ 답변을 생성하는 중 에러가 발생했습니다:\n{str(e)}"
        try:
            await context.bot.edit_message_text(
                chat_id=update.message.chat_id,
                message_id=status_message.message_id,
                text=error_msg
            )
        except:
            # 원본 메시지 수정조차 실패할 경우 새 메시지로 에러 알림
            await update.message.reply_text(error_msg)

if __name__ == '__main__':
    print("씽크패드 서버 대기 중...")
    app = Application.builder().token(TOKEN).build()
    app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO) & (~filters.COMMAND), handle_message))
    app.run_polling()