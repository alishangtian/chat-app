import json
import logging
from typing import Dict, Any, List

import httpx
from fastapi import HTTPException

from config import settings

logger = logging.getLogger(__name__)


async def search_with_serper(args: Dict[str, Any], request_id: str = None) -> Dict[str, Any]:
    """使用Serper API进行搜索。

    Args:
        args: 包含查询参数的字典
        request_id: 请求ID用于日志追踪

    Returns:
        Dict[str, Any]: 搜索结果，包含状态和数据
    """
    query = args.get("query")
    if not query:
        return {"status": "error", "message": "Missing query parameter"}

    logger.info(f"[{request_id}] 开始搜索查询: {query}")
    
    headers = {
        "X-API-KEY": settings.SERPER_API_KEY,
        "Content-Type": "application/json"
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            settings.SERPER_API_URL,
            headers=headers,
            json={
                "q": query,
                "num": 10,
                "hl": "zh-cn",
                "gl": "cn",
                "tbs": "qdr:y"
            }
        )
        
        if response.status_code != 200:
            error_msg = f"搜索服务返回错误状态码: {response.status_code}"
            logger.error(f"[{request_id}] {error_msg}")
            return {
                "status": "error",
                "message": "搜索服务暂时不可用，请稍后再试。"
            }
        
        data = response.json()
        
        # 处理answerBox结果
        answer_box_result = None
        if "answerBox" in data:
            answer_box = data["answerBox"]
            if isinstance(answer_box, dict):
                answer_box_result = {
                    "title": answer_box.get("title", ""),
                    "content": answer_box.get("answer", ""),
                    "source": answer_box.get("source", "")
                }
        
        # 处理普通搜索结果
        organic_results = []
        if "organic" in data:
            for result in data["organic"]:
                organic_results.append({
                    "title": result.get("title", ""),
                    "content": result.get("snippet", ""),
                    "link": result.get("link", "")
                })
        
        return {
            "status": "success",
            "data": {
                "answerBox": answer_box_result,
                "organic": organic_results
            }
        }


# 工具定义列表
tools = [
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": """通过Serper API在互联网上搜索并获取实时信息。适用场景：
1. 获取最新新闻和实时事件信息
2. 查找特定主题的详细资料和教程
3. 搜索产品信息和用户评价
4. 获取技术问题的解决方案

使用说明：
- 支持中文和英文搜索
- 支持精确匹配（使用引号）
- 支持站内搜索（使用site:域名）
- 默认返回前10条最相关结果""",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索查询词"
                    }
                },
                "required": ["query"]
            }
        }
    }
]


# 工具映射表
tool_map = {
    "search": search_with_serper,
    # 可以添加更多工具函数
}


async def process_tool_calls(tool_calls: List[Dict[str, Any]], request_id: str = None) -> List[Dict[str, Any]]:
    """处理模型返回的工具调用请求。

    Args:
        tool_calls: 工具调用列表
        request_id: 请求ID用于日志追踪

    Returns:
        List[Dict[str, Any]]: 工具调用结果列表
    """
    results = []
    
    for tool_call in tool_calls:
        try:
            # 获取工具调用的详细信息
            tool_call_id = tool_call.get('id')
            function_info = tool_call.get('function', {})
            tool_name = function_info.get('name')
            arguments = function_info.get('arguments')
            
            # 解析参数
            try:
                tool_arguments = json.loads(arguments)
            except json.JSONDecodeError:
                logger.error(f"[{request_id}] Failed to parse arguments for tool {tool_name}")
                continue
            
            logger.info(f"[{request_id}] Processing tool call:")
            logger.info(f"Tool ID: {tool_call_id}")
            logger.info(f"Tool Name: {tool_name}")
            logger.info(f"Arguments: {tool_arguments}")
            
            # 执行工具调用
            tool_function = tool_map.get(tool_name)
            if tool_function:
                try:
                    result = await tool_function(tool_arguments, request_id)
                    if result.get("status") == "success":
                        data = result.get("data", {})
                        if data.get("answerBox"):
                            results.append(data["answerBox"])
                        results.extend(data.get("organic", []))
                    logger.info(f"[{request_id}] Tool execution result: {json.dumps(result, ensure_ascii=False)}")
                    
                except Exception as e:
                    logger.error(f"[{request_id}] Error executing tool {tool_name}: {str(e)}")
            else:
                logger.error(f"[{request_id}] Tool {tool_name} not found in tool_map")
                
        except Exception as e:
            logger.error(f"[{request_id}] Error processing tool call: {str(e)}")
    
    return results
