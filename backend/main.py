from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from typing import List, Optional, Dict, Any, AsyncGenerator, Tuple
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

async def create_status_event(status: str, message: str) -> Dict:
    """创建状态事件"""
    return {
        "event": "status",
        "data": json.dumps({
            "status": status,
            "message": message
        }, ensure_ascii=False)
    }

async def create_tool_result_event(tool_name: str, result: Any, message: str = None) -> Dict:
    """创建统一的工具结果事件"""
    return {
        "event": "tool_result",
        "data": json.dumps({
            "tool_name": tool_name,
            "result": result,
            "message": message
        }, ensure_ascii=False)
    }

async def create_answer_event(content: str) -> Dict:
    """创建回答事件"""
    return {
        "event": "answer",
        "data": json.dumps({
            "status": "streaming",
            "content": content
        }, ensure_ascii=False)
    }

async def create_complete_event() -> Dict:
    """创建完成事件"""
    return {
        "event": "complete",
        "data": json.dumps({
            "status": "completed",
            "message": "回答完成"
        }, ensure_ascii=False)
    }

async def create_error_event(error_message: str) -> Dict:
    """创建错误事件"""
    return {
        "event": "error",
        "data": json.dumps({
            "error": error_message
        }, ensure_ascii=False)
    }

async def process_search_results(search_results: List[Dict]) -> str:
    """处理搜索结果，生成上下文"""
    context = ""
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
    return context

async def process_arxiv_results(non_search_results: List[Dict]) -> str:
    """处理论文搜索结果"""
    context = ""
    arxiv_results = [r for r in non_search_results if r['tool_name'] == 'search_arxiv']
    if arxiv_results:
        context += "[论文搜索结果]\n"
        for result in arxiv_results:
            if isinstance(result['result'], dict) and 'data' in result['result']:
                papers = result['result']['data']
                for paper in papers:
                    context += f"标题：{paper['title']}\n"
                    if paper.get('authors'):
                        context += f"作者：{', '.join(paper['authors'])}\n"
                    if paper.get('content'):  # arxiv_crawler 中使用 content 存储摘要
                        context += f"摘要：{paper['content']}\n"
                    if paper.get('link'):
                        context += f"链接：{paper['link']}\n"
                    if paper.get('submitted'):
                        context += f"发布日期：{paper['submitted']}\n"
                    context += "\n"
    return context

async def process_other_results(non_search_results: List[Dict]) -> str:
    """处理其他非搜索结果"""
    context = ""
    other_results = [r for r in non_search_results if r['tool_name'] != 'search_arxiv']
    if other_results:
        context += "[工具调用结果]\n"
        for result in other_results:
            tool_name = result['tool_name']
            tool_result = result['result']
            
            # 格式化工具结果
            formatted_result = ""
            if isinstance(tool_result, dict):
                # 字典类型结果，格式化为多行键值对
                for key, value in tool_result.items():
                    formatted_result += f"  - {key}: {value}\n"
            elif isinstance(tool_result, list):
                # 列表类型结果，每项单独一行
                for item in tool_result:
                    if isinstance(item, dict):
                        formatted_result += "  - " + ", ".join(f"{k}: {v}" for k, v in item.items()) + "\n"
                    else:
                        formatted_result += f"  - {item}\n"
            else:
                # 其他类型直接转换为字符串
                formatted_result = f"  - {str(tool_result)}\n"
            
            # 添加到上下文
            context += f"工具：{tool_name}\n"
            context += f"结果：\n{formatted_result}\n"
    return context

async def create_system_prompt(context: str) -> str:
    """创建系统提示词"""
    return f"""
    你是一个专业、智慧且富有同理心的AI助手。在回答问题时，请遵循以下原则：

    已为你提供以下背景信息：
    {context}

    信息处理指南：
    1. [论文搜索结果]标记的内容：
       - 这些是来自学术论文的专业研究成果
       - 优先使用这些信息构建回答的核心内容
       - 确保准确理解和传达论文中的关键发现和结论
       - 适当引用论文的研究方法和实验结果

    2. [重要参考信息]标记的内容：
       - 这些是最相关且可靠的信息源
       - 将这些信息与论文研究结果结合
       - 确保准确理解和传达其中的关键观点

    3. [工具调用结果]标记的内容：
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

async def process_tool_result(tool_result: Dict, search_results: List[Dict], non_search_results: List[Dict], parsing_started: bool) -> Tuple[List[Dict], bool]:
    """处理工具调用结果，立即发送事件到前端"""
    events = []
    
    # 检查是否为搜索相关结果
    if tool_result["type"] == "search_results":
        results = tool_result["results"]
        search_results.extend(results)
        
        # 如果结果来自工具调用，添加到non_search_results
        if "tool_name" in tool_result:
            non_search_results.append({
                "tool_name": tool_result["tool_name"],
                "result": {"data": results}
            })
            # 不发送重复的search_results事件
            return events, parsing_started
            
        # 立即发送搜索结果作为工具结果
        events.append(await create_tool_result_event(
            tool_name="search_web",
            result=results,
            message=f"找到 {len(results)} 条相关信息"
        ))
        
        # 如果有需要爬取的网页，立即发送开始爬取状态
        has_pages_to_fetch = any(r.get("needsFetch", False) for r in results)
        if has_pages_to_fetch:
            events.append(await create_status_event("fetch_start", "开始读取网页内容..."))
            
    elif tool_result["type"] == "search_result_update":
        result = tool_result["result"]
        # 更新搜索结果列表
        for i, sr in enumerate(search_results):
            if sr["link"] == result["link"]:
                search_results[i] = result
                break
        
        # 发送搜索结果更新事件
        events.append({
            "event": "tool_result",
            "data": json.dumps({
                "type": "search_result_update",
                "result": result
            }, ensure_ascii=False)
        })
        
        # 计算进度并发送状态更新
        completed_pages = sum(1 for sr in search_results if sr.get("fetchStatus") == "completed")
        total_pages = sum(1 for sr in search_results if sr.get("needsFetch", False))
        if total_pages > 0:
            progress = completed_pages / total_pages
            events.append({
                "event": "status",
                "data": json.dumps({
                    "status": "fetch_progress",
                    "message": f"正在读取网页 ({completed_pages}/{total_pages})...",
                    "progress": progress
                }, ensure_ascii=False)
            })
        
        # 检查是否所有页面都已完成爬取
        all_parsing_completed = all(
            not sr.get("needsFetch", False) or 
            sr.get("fetchStatus") == "completed" 
            for sr in search_results
        )
        if all_parsing_completed:
            events.append(await create_status_event("fetch_completed", "网页内容读取完成"))
            
    elif tool_result["type"] == "tool_result":
        # 添加到non_search_results
        non_search_results.append({
            "tool_name": tool_result["tool_name"],
            "result": tool_result["result"]
        })
        # 只有当不是arxiv搜索结果时才直接发送工具结果事件
        if tool_result["tool_name"] != "search_arxiv":
            events.append({
                "event": "tool_result",
                "data": json.dumps({
                    "tool_name": tool_result["tool_name"],
                    "result": tool_result["result"]
                }, ensure_ascii=False)
            })
    
    return events, parsing_started

async def stream_chat_response(message: str, request_id: str, selected_tools: List[str] = None):
    """处理聊天请求并返回SSE响应"""
    logger.info(f"[{request_id}] 开始处理聊天请求: {message}, selected_tools: {selected_tools}")
    
    try:
        # 开始生成回答
        logger.info(f"[{request_id}] 开始生成回答")
        yield await create_status_event("generating", "正在生成回答...")
        await asyncio.sleep(0.1)
        
        messages = [{"role": "user", "content": message}]
        try:
            # 如果selected_tools不为空，则使用工具
            if selected_tools:
                # 根据选择的工具过滤工具列表
                available_tools = [tool for tool in tools if tool["function"]["name"] in selected_tools]
                logger.info(f"[{request_id}] 使用的工具列表: {available_tools}")
                # 检查是否需要工具调用
                tool_calls = await check_tool_calls(messages, request_id, available_tools)
                if tool_calls:
                    # 检查是否包含搜索相关的工具调用
                    has_search_tool = any(
                        call.get("function", {}).get("name", "").startswith("search_web")
                        for call in tool_calls
                    )
                    if has_search_tool:
                        yield await create_status_event("searching", "正在搜索相关信息...")
                    
                    # 初始化结果列表
                    search_results = []
                    non_search_results = []
                    parsing_started = False
                    
                    # 执行工具调用并处理分步返回的结果
                    async for tool_result in process_tool_calls(tool_calls, request_id):
                        logger.info(f"[{request_id}] 工具调用结果: {tool_result}")
                        if "type" not in tool_result:
                            logger.error(f"[{request_id}] 工具调用结果缺少type字段: {tool_result}")
                            continue
                        
                        events, parsing_started = await process_tool_result(
                            tool_result, search_results, non_search_results, parsing_started
                        )
                        logger.info(f"[{request_id}] 生成的事件列表: {events}")
                        if events:
                            for event in events:
                                # 确保event是一个有效的事件对象
                                if isinstance(event, dict) and 'event' in event and 'data' in event:
                                    yield event
                                else:
                                    logger.error(f"[{request_id}] 无效的事件对象: {event}")
                    
                    # 准备生成最终回复
                    yield await create_status_event("generating", "正在生成回复...")
                    
                    # 构建上下文
                    context_parts = []
                    
                    # 处理搜索结果
                    search_context = await process_search_results(search_results)
                    if search_context.strip():
                        context_parts.append(search_context)
                    
                    # 处理论文搜索结果
                    arxiv_results = [r for r in non_search_results if r['tool_name'] == 'search_arxiv']
                    if arxiv_results:
                        for result in arxiv_results:
                            if isinstance(result['result'], dict) and 'data' in result['result']:
                                # 发送论文搜索结果到前端
                                papers = result['result']['data']
                                formatted_papers = []
                                for paper in papers:
                                    formatted_papers.append({
                                        'title': paper['title'],
                                        'authors': paper.get('authors', []),
                                        'content': paper.get('content', ''),  # 摘要内容
                                        'link': paper.get('link', ''),
                                        'submitted': paper.get('submitted', ''),
                                        'isArxiv': True  # 标记为arxiv论文
                                    })
                                yield await create_tool_result_event(
                                    tool_name="search_arxiv",
                                    result=formatted_papers,
                                    message=f"找到 {len(formatted_papers)} 篇相关论文"
                                )
                        
                        # 添加到上下文
                        arxiv_context = await process_arxiv_results(non_search_results)
                        if arxiv_context.strip():
                            context_parts.append(arxiv_context)
                            logger.info(f"[{request_id}] 添加论文上下文: {arxiv_context}")
                    
                    # 处理其他工具结果
                    other_context = await process_other_results(non_search_results)
                    if other_context.strip():
                        context_parts.append(other_context)
                    
                    # 合并所有上下文
                    context = "\n".join(filter(None, context_parts))
                    logger.info(f"[{request_id}] 最终上下文: {context}")
                    
                    # 构建系统提示词
                    system_prompt = await create_system_prompt(context)
                    
                    # 构建新的消息列表并生成回复
                    new_messages = [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": message}
                    ]
                    async for content in generate_model_response(new_messages, request_id):
                        yield await create_answer_event(content)
                        await asyncio.sleep(0.01)
                    
                    yield await create_complete_event()
                    return
            
            # 如果没有工具调用或工具被禁用，生成普通回复
            async for content in generate_model_response(messages, request_id):
                yield await create_answer_event(content)
                await asyncio.sleep(0.01)
            yield await create_complete_event()
            
        except Exception as e:
            error_msg = f"处理聊天响应时发生错误: {str(e)}"
            logger.error(f"[{request_id}] {error_msg}")
            yield await create_error_event("抱歉，服务器暂时繁忙，请稍后再试。")
            
    except Exception as e:
        error_msg = f"处理聊天响应时发生错误: {str(e)}"
        logger.error(f"[{request_id}] {error_msg}")
        yield await create_error_event("抱歉，服务器暂时繁忙，请稍后再试。")
