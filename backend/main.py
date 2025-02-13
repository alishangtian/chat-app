from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from typing import List, Optional, Dict, Any, AsyncGenerator
import httpx
import json
import asyncio
import logging
import os
from datetime import datetime
from sse_starlette.sse import EventSourceResponse
from config import settings
from tools import process_tool_calls, tools

# 配置日志
log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
os.makedirs(log_dir, exist_ok=True)

log_file = os.path.join(log_dir, f'app_{datetime.now().strftime("%Y%m%d")}.log')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = FastAPI()

# 获取当前文件所在目录的父目录作为项目根目录
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 配置CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载静态文件目录
app.mount("/static", StaticFiles(directory=os.path.join(ROOT_DIR, "static")), name="static")


async def check_tool_calls(messages: List[Dict[str, str]], request_id: str = None) -> Optional[List[Dict[str, Any]]]:
    """检查是否需要工具调用
    
    Args:
        messages: 对话消息列表
        request_id: 请求ID用于日志追踪
        
    Returns:
        Optional[List[Dict[str, Any]]]: 如果需要工具调用则返回工具调用列表，否则返回None
    """
    logger.info(f"[{request_id}] 检查是否需要工具调用")
    
    # 构建系统消息
    system_content = "你是一个智能助手。你可以使用工具来帮助回答问题。"
    all_messages = [{"role": "system", "content": system_content}] + messages
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                settings.BASE_URL,
                json={
                    "model": settings.FUNCTIONCALL_MODEL,
                    "messages": all_messages,
                    "stream": False,
                    "max_tokens": 32000,
                    "temperature": 0.7,
                    "top_p": 0.9,
                    "tools": tools,
                    "tool_choice": "auto"
                },
                headers={"Content-Type": "application/json","Authorization": f"Bearer {settings.API_TOKEN}"},
                timeout=60.0
            )
            
            if response.status_code != 200:
                error_detail = await response.text()
                logger.error(f"[{request_id}] 大模型返回错误: {error_detail}")
                raise HTTPException(status_code=500, detail="抱歉，服务器暂时繁忙，请稍后再试。")
            logger.info(f"[{request_id}] 工具调用检查结果: {response.text}")
            response_data = response.json()
            if "choices" in response_data and response_data["choices"]:
                choice = response_data["choices"][0]
                if choice["finish_reason"] and choice["finish_reason"] == "tool_calls":
                    return choice["message"]["tool_calls"]
                
            return None
            
    except Exception as e:
        error_msg = f"检查工具调用时发生错误: {str(e)}"
        logger.error(f"[{request_id}] {error_msg}")
        raise HTTPException(status_code=500, detail="抱歉，服务器暂时繁忙，请稍后再试。")

async def generate_model_response(messages: List[Dict[str, str]], request_id: str = None) -> AsyncGenerator[str, None]:
    """生成模型回复
    
    Args:
        messages: 对话消息列表
        request_id: 请求ID用于日志追踪
        
    Yields:
        str: 模型回复的文本片段
    """
    logger.info(f"[{request_id}] 开始生成模型回复")
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                settings.BASE_URL,
                json={
                    "model": settings.MODEL,
                    "messages": messages,
                    "stream": True,
                    "max_tokens": 32000,
                    "temperature": 0.7,
                    "top_p": 0.9
                },
                headers={"Content-Type": "application/json","Authorization": f"Bearer {settings.API_TOKEN}"},
                timeout=60.0
            )
            
            if response.status_code != 200:
                error_detail = await response.text()
                logger.error(f"[{request_id}] 大模型返回错误: {error_detail}")
                raise HTTPException(status_code=500, detail="抱歉，服务器暂时繁忙，请稍后再试。")
            
            async for line in response.aiter_lines():
                # logger.info(f"[{request_id}] {settings.MODEL}返回: {line}")
                if line.strip() and line.startswith("data: "):
                    data = line[6:]  # 移除 "data: " 前缀
                    if data == "[DONE]":
                        break
                    try:
                        json_data = json.loads(data)
                        if "choices" in json_data and json_data["choices"]:
                            choice = json_data["choices"][0]
                            if "content" in choice.get("delta", {}) and choice["delta"]["content"]:
                                yield choice["delta"]["content"]
                    except json.JSONDecodeError:
                        continue
                            
    except Exception as e:
        error_msg = f"生成模型回复时发生错误: {str(e)}"
        logger.error(f"[{request_id}] {error_msg}")
        raise HTTPException(status_code=500, detail="抱歉，服务器暂时繁忙，请稍后再试。")

async def chat_with_llm(messages: List[Dict[str, str]], request_id: str = None, use_search: bool = True) -> AsyncGenerator[Dict[str, Any], None]:
    """使用大模型 API进行对话"""
    logger.info(f"[{request_id}] 开始与 {settings.MODEL} 对话")
    
    try:
        if use_search:
            # 检查是否需要工具调用
            tool_calls = await check_tool_calls(messages, request_id)
            if tool_calls:
                yield {
                    "type": "tool_calls",
                    "tool_calls": tool_calls
                }
                return
        
        # 如果不需要工具调用或禁用搜索，生成普通回复
        async for content in generate_model_response(messages, request_id):
            yield content
            
    except Exception as e:
        error_msg = f"对话过程发生错误: {str(e)}"
        logger.error(f"[{request_id}] {error_msg}")
        raise HTTPException(status_code=500, detail="抱歉，服务器暂时繁忙，请稍后再试。")

async def stream_chat_response(message: str, request_id: str, use_search: bool = True):
    """处理聊天请求并返回SSE响应"""
    logger.info(f"[{request_id}] 开始处理聊天请求: {message}, use_search: {use_search}")
    
    try:
        # 开始生成回答
        logger.info(f"[{request_id}] 开始生成回答")
        yield {
            "event": "status",
            "data": json.dumps({
                "status": "generating", 
                "message": "正在生成回答..."
            }, ensure_ascii=False)
        }
        # 添加一个短暂的延迟，确保前端能接收到状态更新
        await asyncio.sleep(0.1)
        
        messages = [{"role": "user", "content": message}]
        try:
            async for content in chat_with_llm(messages, request_id, use_search):
                if isinstance(content, dict) and content.get("type") == "tool_calls":
                    # 收到工具调用请求
                    tool_calls = content.get("tool_calls", [])
                    if tool_calls:
                        # 检查是否包含搜索相关的工具调用
                        has_search_tool = any(
                            call.get("function", {}).get("name", "").startswith("search")
                            for call in tool_calls
                        )
                        if has_search_tool:
                            yield {
                                "event": "status",
                                "data": json.dumps({
                                    "status": "searching",
                                    "message": "正在搜索相关信息..."
                                }, ensure_ascii=False)
                            }
                        
                        # 执行工具调用
                        results = await process_tool_calls(tool_calls, request_id)
                        if results:
                            yield {
                                "event": "search_results",
                                "data": json.dumps({
                                    "status": "success",
                                    "results": results,
                                    "message": f"找到 {len(results)} 条相关信息"
                                }, ensure_ascii=False)
                            }
                            
                            # 构建系统提示词和上下文
                            context = ""
                            
                            # 优先处理answerBox结果
                            answer_box_results = [r for r in results if r.get("isAnswerBox")]
                            regular_results = [r for r in results if not r.get("isAnswerBox")]
                            
                            # 先添加answerBox内容
                            for result in answer_box_results:
                                context += f"[重要参考信息]\n{result['title']}\n{result['content']}\n\n"
                            
                            # 再添加其他搜索结果
                            for result in regular_results:
                                context += f"{result['title']}\n{result['content']}\n\n"
                            
                            system_prompt = f"""
                            你是一个杰出的人工智能助手。
                            以下是与问题相关的背景信息，其中标记为[重要参考信息]的内容非常重要且与问题紧密相关：
                            {context}
                            请根据这些信息回答问题，重点参考[重要参考信息]。
                            回答要自然流畅，不要提及信息来源。
                            最终回复要格式清晰、内容友好。
                            """
                            
                            # 构建新的消息列表
                            new_messages = [
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": message}
                            ]
                            
                            # 继续生成回答
                            yield {
                                "event": "status",
                                "data": json.dumps({
                                    "status": "generating",
                                    "message": "正在生成回答..."
                                }, ensure_ascii=False)
                            }
                            
                            # 调用模型生成回复
                            async for content in generate_model_response(new_messages, request_id):
                                yield {
                                    "event": "answer",
                                    "data": json.dumps({
                                        "status": "streaming",
                                        "content": content
                                    }, ensure_ascii=False)
                                }
                                await asyncio.sleep(0.01)
                else:
                    # 普通文本内容
                    yield {
                        "event": "answer",
                        "data": json.dumps({
                            "status": "streaming",
                            "content": content
                        }, ensure_ascii=False)
                    }
                    # 添加一个极短的延迟，避免事件堆积
                    await asyncio.sleep(0.01)
            
            logger.info(f"[{request_id}] 回答生成完成")
            # 添加一个短暂的延迟，确保之前的内容都已经发送
            await asyncio.sleep(0.1)
            yield {
                "event": "status",
                "data": json.dumps({
                    "status": "completed", 
                    "message": "回答生成完成"
                }, ensure_ascii=False)
            }
            
        except Exception as e:
            error_msg = f"生成回答时发生错误: {str(e)}"
            logger.error(f"[{request_id}] {error_msg}", exc_info=True)
            yield {
                "event": "error",
                "data": json.dumps({
                    "status": "error", 
                    "error": "抱歉，服务器暂时繁忙，请稍后再试。",
                    "details": "服务暂时繁忙"
                }, ensure_ascii=False)
            }
            
    except Exception as e:
        error_msg = f"处理请求时发生错误: {str(e)}"
        logger.error(f"[{request_id}] {error_msg}", exc_info=True)
        yield {
            "event": "error",
            "data": json.dumps({
                "status": "error", 
                "error": "抱歉，服务器暂时繁忙，请稍后再试。",
                "details": "服务暂时繁忙"
            }, ensure_ascii=False)
        }
    finally:
        logger.info(f"[{request_id}] 请求处理完成")

@app.get("/api/chat")
async def chat(message: str, request: Request, use_search: bool = True):
    """统一的聊天接口，使用SSE返回所有类型的响应"""
    request_id = f"req_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{id(request)}"
    logger.info(f"[{request_id}] 收到新的聊天请求，use_search={use_search}")
    
    return EventSourceResponse(
        stream_chat_response(message, request_id, use_search),
        media_type="text/event-stream; charset=utf-8"
    )

@app.get("/")
async def root():
    """重定向到index.html"""
    return RedirectResponse(url="/index.html")

# 最后挂载根目录静态文件
app.mount("/", StaticFiles(directory=ROOT_DIR, html=True), name="root")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
