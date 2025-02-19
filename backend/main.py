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

# 先定义所有API路由
@app.get("/")
async def root():
    """重定向到index.html"""
    return RedirectResponse(url="/index.html")

@app.get("/api/tools")
async def get_tools():
    """获取可用工具列表"""
    tool_list = []
    for tool in tools:
        tool_list.append({
            "name": tool["function"]["name"],
            "description": tool["function"]["description"]
        })
    return {"tools": tool_list}

@app.get("/api/chat")
async def chat(request: Request):
    """处理聊天请求"""
    try:
        # 从URL参数中获取数据
        message = request.query_params.get("message", "")
        request_id = request.query_params.get("request_id", "")
        selected_tools = request.query_params.get("selected_tools", "").split(",") if request.query_params.get("selected_tools") else None
        
        if not message:
            raise HTTPException(status_code=400, detail="消息不能为空")
            
        return EventSourceResponse(
            stream_chat_response(message, request_id, selected_tools),
            media_type="text/event-stream"
        )
    except Exception as e:
        logger.error(f"处理聊天请求时发生错误: {str(e)}")
        raise HTTPException(status_code=500, detail="服务器内部错误")

# 挂载静态文件目录
app.mount("/static", StaticFiles(directory=os.path.join(ROOT_DIR, "static")), name="static")

# 最后挂载根目录静态文件
app.mount("/", StaticFiles(directory=ROOT_DIR, html=True), name="root")

async def check_tool_calls(messages: List[Dict[str, str]], request_id: str = None, available_tools: List[Dict] = None) -> Optional[List[Dict[str, Any]]]:
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
    
    # 打印格式化的提示词
    logger.info(f"[{request_id}] 工具调用检查的提示词:\n" + json.dumps(all_messages, ensure_ascii=False, indent=2))
    
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
                    "tools": available_tools,
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
    """生成模型回复"""
    logger.info(f"[{request_id}] 生成回复的提示词:\n" + json.dumps(messages, ensure_ascii=False, indent=2))
    
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
                if line.strip() and line.startswith("data: "):
                    data = line[6:]  # 移除 "data: " 前缀
                    if data == "[DONE]":
                        break
                    try:
                        json_data = json.loads(data)
                        if "choices" in json_data and json_data["choices"]:
                            choice = json_data["choices"][0]
                            if "delta" in choice and "content" in choice["delta"]:
                                yield choice["delta"]["content"]
                    except json.JSONDecodeError:
                        continue
                        
    except Exception as e:
        error_msg = f"生成模型回复时发生错误: {str(e)}"
        logger.error(f"[{request_id}] {error_msg}")
        raise HTTPException(status_code=500, detail="抱歉，服务器暂时繁忙，请稍后再试。")

async def chat_with_llm(messages: List[Dict[str, str]], request_id: str = None, selected_tools: List[str] = None) -> AsyncGenerator[Dict[str, Any], None]:
    """使用大模型 API进行对话"""
    logger.info(f"[{request_id}] 开始与 {settings.MODEL} 对话，选择的工具: {selected_tools}")
    
    try:
        # 如果selected_tools不为空，则使用工具
        if selected_tools:
            # 根据选择的工具过滤工具列表
            available_tools = [tool for tool in tools if tool["function"]["name"] in selected_tools]
            logger.info(f"[{request_id}] 使用的工具列表: {available_tools}")
            # 检查是否需要工具调用
            tool_calls = await check_tool_calls(messages, request_id, available_tools)
            if tool_calls:
                yield {
                    "type": "tool_calls",
                    "tool_calls": tool_calls
                }
                return
        
        # 如果不需要工具调用或禁用工具，生成普通回复
        async for content in generate_model_response(messages, request_id):
            yield content
            
    except Exception as e:
        error_msg = f"对话过程发生错误: {str(e)}"
        logger.error(f"[{request_id}] {error_msg}")
        raise HTTPException(status_code=500, detail="抱歉，服务器暂时繁忙，请稍后再试。")

async def stream_chat_response(message: str, request_id: str, selected_tools: List[str] = None):
    """处理聊天请求并返回SSE响应"""
    logger.info(f"[{request_id}] 开始处理聊天请求: {message}, selected_tools: {selected_tools}")
    
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
            async for content in chat_with_llm(messages, request_id, selected_tools):
                if isinstance(content, dict):
                    if content["type"] == "tool_calls":
                        # 收到工具调用请求
                        tool_calls = content.get("tool_calls", [])
                        if tool_calls:
                            # 检查是否包含搜索相关的工具调用
                            has_search_tool = any(
                                call.get("function", {}).get("name", "").startswith("search_web")
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
                            
                            # 初始化结果列表
                            search_results = []
                            non_search_results = []
                            
                            # 执行工具调用并处理分步返回的结果
                            parsing_started = False
                            async for tool_result in process_tool_calls(tool_calls, request_id):
                                logger.info(f"[{request_id}] 工具调用结果: {tool_result}")
                                if "type" not in tool_result:
                                    logger.error(f"[{request_id}] 工具调用结果缺少type字段: {tool_result}")
                                    continue
                                    
                                if tool_result["type"] == "search_results":
                                    # 初始搜索结果
                                    results = tool_result["results"]
                                    search_results.extend(results)
                                    yield {
                                        "event": "search_results",
                                        "data": json.dumps({
                                            "status": "success",
                                            "results": results,
                                            "isInitialResults": True,
                                            "message": f"找到 {len(results)} 条相关信息"
                                        }, ensure_ascii=False)
                                    }
                                elif tool_result["type"] == "search_result_update":
                                    # 单个搜索结果更新（网页内容爬取）
                                    result = tool_result["result"]
                                    if not parsing_started:
                                        parsing_started = True
                                        yield {
                                            "event": "status",
                                            "data": json.dumps({
                                                "status": "parsing",
                                                "message": "网页解读中..."
                                            }, ensure_ascii=False)
                                        }
                                    # 只在后端更新search_results，不发送到前端
                                    for i, sr in enumerate(search_results):
                                        if sr["link"] == result["link"]:
                                            search_results[i] = result
                                            break
                                    
                                    # 检查是否所有需要解析的结果都已完成
                                    all_parsing_completed = all(
                                        not sr.get("needsFetch", False) or 
                                        sr.get("fetchStatus") == "completed" 
                                        for sr in search_results
                                    )
                                    if all_parsing_completed:
                                        yield {
                                            "event": "status",
                                            "data": json.dumps({
                                                "status": "parsing_completed",
                                                "message": "解读结束"
                                            }, ensure_ascii=False)
                                        }
                                elif tool_result["type"] == "tool_result":
                                    # 非搜索工具的结果
                                    non_search_results.append({
                                        "tool_name": tool_result["tool_name"],
                                        "result": tool_result["result"]
                                    })
                            
                            # 所有工具调用完成后，整合结果并生成最终回复
                            all_results = {
                                "search_results": search_results,
                                "non_search_results": non_search_results
                            }
                            
                            # 准备生成最终回复
                            yield {
                                "event": "status",
                                "data": json.dumps({
                                    "status": "generating",
                                    "message": "正在生成回复..."
                                }, ensure_ascii=False)
                            }
                            
                            # 构建系统提示词和上下文
                            context = ""
                            
                            # 处理搜索结果
                            if search_results:
                                # 优先处理answerBox结果
                                answer_box_results = [r for r in search_results if r.get("isAnswerBox")]
                                regular_results = [r for r in search_results if not r.get("isAnswerBox")]
                                
                                # 先添加answerBox内容
                                for result in answer_box_results:
                                    content = result.get('content', '')
                                    context += f"[重要参考信息]\n{result['title']}\n{content}\n\n"
                                
                                # 再添加其他搜索结果（包括爬取的网页内容）
                                for result in regular_results:
                                    content = result.get('content', '')
                                    if result.get('fetchStatus') == 'completed':
                                        content = f"正文：\n{content}"
                                    context += f"标题：{result['title']}\n{content}\n\n"
                            
                            # 处理论文搜索结果
                            arxiv_results = [r for r in non_search_results if r['tool_name'] == 'search_arxiv']
                            if arxiv_results:
                                context += "[论文搜索结果]\n"
                                for result in arxiv_results:
                                    papers = result['result']
                                    for paper in papers:  # 处理所有论文
                                        context += f"标题：{paper['title']}\n"
                                        context += f"作者：{paper['authors']}\n"
                                        context += f"摘要：{paper['summary']}\n"
                                        context += f"链接：{paper['pdf_url']}\n\n"

                            # 处理其他非搜索结果
                            other_results = [r for r in non_search_results if r['tool_name'] != 'search_arxiv']
                            if other_results:
                                context += "[工具调用结果]\n"
                                for result in other_results:
                                    context += f"工具名称: {result['tool_name']}\n"
                                    context += f"执行结果: {json.dumps(result['result'], ensure_ascii=False)}\n\n"
                            
                            system_prompt = f"""
                            你是一个专业、智慧且富有同理心的AI助手。在回答问题时，请遵循以下原则：

                            已为你提供以下背景信息：
                            {context}

                            信息处理指南：
                            1. [重要参考信息]标记的内容：
                               - 这些是最相关且可靠的信息源
                               - 优先使用这些信息构建回答的核心内容
                               - 确保准确理解和传达其中的关键观点

                            2. [工具调用结果]标记的内容：
                               - 这些是通过专业工具获取的实时或特定数据
                               - 将这些数据与其他信息有机整合
                               - 注意数据的时效性和适用性

                            回答要求：
                            1. 内容组织：
                               - 采用清晰的层次结构
                               - 重点突出，条理分明
                               - 适当使用段落划分和标点符号

                            2. 表达方式：
                               - 使用自然、流畅的语言
                               - 避免生硬的引用或提及信息来源
                               - 保持专业性的同时确保易于理解

                            3. 回答态度：
                               - 保持友好、耐心的语气
                               - 展现专业性和可靠性
                               - 适当表达同理心，理解用户需求

                            4. 质量控制：
                               - 确保信息的准确性和相关性
                               - 在必要时提供补充说明
                               - 回答完整且有价值
                            """
                            
                            # 构建新的消息列表
                            new_messages = [
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": message}
                            ]
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
                else:
                    # 普通文本内容
                    yield {
                        "event": "answer",
                        "data": json.dumps({
                            "status": "streaming",
                            "content": content
                        }, ensure_ascii=False)
                    }
                    await asyncio.sleep(0.01)
                    
            # 所有内容发送完成后，发送complete事件
            yield {
                "event": "complete",
                "data": json.dumps({
                    "status": "completed",
                    "message": "回答完成"
                }, ensure_ascii=False)
            }
        except Exception as e:
            error_msg = f"处理聊天响应时发生错误: {str(e)}"
            logger.error(f"[{request_id}] {error_msg}")
            yield {
                "event": "error",
                "data": json.dumps({
                    "error": "抱歉，服务器暂时繁忙，请稍后再试。"
                }, ensure_ascii=False)
            }
    except Exception as e:
        error_msg = f"处理聊天响应时发生错误: {str(e)}"
        logger.error(f"[{request_id}] {error_msg}")
        yield {
            "event": "error",
            "data": json.dumps({
                "error": "抱歉，服务器暂时繁忙，请稍后再试。"
            }, ensure_ascii=False)
        }
