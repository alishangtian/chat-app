
import json
import logging
import asyncio
from typing import Dict, Any, List, AsyncGenerator

from web_crawler import search_with_serper, fetch_webpage_content
from arxiv_crawler import crawl_arxiv_papers

logger = logging.getLogger(__name__)

# 工具定义列表
tools = [
    {
        "type": "function",
        "function": {
            "name": "search_web",
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
    },
    {
        "type": "function",
        "function": {
            "name": "search_arxiv",
            "description": """在arXiv上搜索学术论文。适用场景：
1. 搜索最新的计算机科学研究论文
2. 查找特定主题的学术文献
3. 获取论文的PDF下载链接和详细信息
4. 浏览论文摘要和作者信息

使用说明：
- 支持中文和英文搜索
- 默认返回最新的25篇相关论文
- 返回结果包含标题、作者、摘要、分类、PDF链接等详细信息""",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "论文搜索关键词"
                    }
                },
                "required": ["query"]
            }
        }
    }
]

# 工具映射表
tool_map = {
    "search_web": search_with_serper,
    "search_arxiv": crawl_arxiv_papers
}

async def process_tool_calls(tool_calls: List[Dict[str, Any]], request_id: str = None) -> AsyncGenerator[Dict[str, Any], None]:
    """处理模型返回的工具调用请求，分步返回结果。

    Args:
        tool_calls: 工具调用列表
        request_id: 请求ID用于日志追踪

    Yields:
        Dict[str, Any]: 包含搜索结果或非搜索结果的字典
    """
    
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
                    # 为所有工具添加request_id参数
                    tool_arguments["request_id"] = request_id
                    
                    # 统一使用异步调用
                    result = await tool_function(tool_arguments)
                    
                    # 根据工具类型处理结果
                    if tool_name == "search_web":
                        if result.get("status") == "success":
                            # 返回初始搜索结果
                            yield {
                                "type": "search_results",
                                "results": result.get("data", []),
                                "isInitialResults": True
                            }
                            
                            # 并行处理需要爬取的网页
                            import asyncio
                            from asyncio import Queue

                            # 创建一个队列用于收集更新
                            update_queue = Queue()
                            total_items = 0
                            completed_items = 0

                            async def fetch_and_update(item):
                                nonlocal total_items, completed_items
                                if not item.get("needsFetch"):
                                    return
                                
                                try:
                                    # 更新爬取状态
                                    item["fetchStatus"] = "fetching"
                                    await update_queue.put({
                                        "type": "search_result_update",
                                        "result": item,
                                        "progress": {
                                            "total": total_items,
                                            "completed": completed_items,
                                            "current_url": item["link"]
                                        }
                                    })
                                    
                                    # 爬取网页内容
                                    fetch_result = await fetch_webpage_content(item["link"])
                                    
                                    # 更新结果
                                    if fetch_result['status'] == 'success':
                                        item.update({
                                            "fetchStatus": "completed",
                                            "title": fetch_result['title'],
                                            "description": fetch_result['description'],
                                            "content": fetch_result['content']
                                        })
                                    else:
                                        item.update({
                                            "fetchStatus": "error",
                                            "error": fetch_result['error']
                                        })
                                except Exception as e:
                                    error_msg = str(e)
                                    logger.error(f"Error fetching content for {item['link']}: {error_msg}")
                                    item["fetchStatus"] = "error"
                                    item["error"] = f"爬取失败: {error_msg}"
                                
                                completed_items += 1
                                await update_queue.put({
                                    "type": "search_result_update",
                                    "result": item,
                                    "progress": {
                                        "total": total_items,
                                        "completed": completed_items,
                                        "current_url": item["link"]
                                    }
                                })

                                # 发送进度事件
                                await update_queue.put({
                                    "type": "event",
                                    "data": json.dumps({
                                        "status": "fetching_progress",
                                        "progress": {
                                            "total": total_items,
                                            "completed": completed_items,
                                            "percentage": round(completed_items / total_items * 100, 2)
                                        },
                                        "message": f"正在爬取网页 ({completed_items}/{total_items})"
                                    }, ensure_ascii=False)
                                })

                            async def process_queue():
                                while True:
                                    update = await update_queue.get()
                                    yield update
                                    update_queue.task_done()
                                    if update["type"] == "event" and completed_items == total_items:
                                        break
                            
                            items_to_fetch = [item for item in result.get("data", []) if item.get("needsFetch")]
                            total_items = len(items_to_fetch)
                            logger.info(f"[{request_id}] Found {total_items} items to fetch")
                            
                            if total_items > 0:
                                # 创建爬取任务
                                fetch_tasks = [asyncio.create_task(fetch_and_update(item)) for item in items_to_fetch]
                                # 创建队列处理任务
                                queue_task = asyncio.create_task(process_queue())
                                
                                # 等待所有任务完成
                                await asyncio.gather(*fetch_tasks)
                                async for update in queue_task:
                                    yield update
                                
                                # 发送完成事件
                                yield {
                                    "type": "event",
                                    "data": json.dumps({
                                        "status": "parsing_completed",
                                        "message": "所有网页读取完成",
                                        "progress": {
                                            "total": total_items,
                                            "completed": total_items,
                                            "percentage": 100
                                        }
                                    }, ensure_ascii=False)
                                }
                            
                    elif tool_name == "search_arxiv":
                        # 返回工具调用结果
                        yield {
                            "type": "tool_result",
                            "tool_name": tool_name,
                            "result": result
                        }
                        
                        # 发送完成事件
                        yield {
                            "type": "event",
                            "data": json.dumps({
                                "status": "parsing_completed",
                                "message": "论文搜索完成"
                            }, ensure_ascii=False)
                        }
                    else:
                        # 其他工具的结果直接返回
                        yield {
                            "type": "tool_result",
                            "tool_name": tool_name,
                            "result": result
                        }
                        
                        # 发送完成事件
                        yield {
                            "type": "event",
                            "data": json.dumps({
                                "status": "function_completed",
                                "message": "工具调用完成"
                            }, ensure_ascii=False)
                        }
                        
                    logger.info(f"[{request_id}] Tool execution completed")
                    
                except Exception as e:
                    logger.error(f"[{request_id}] Error executing tool {tool_name}: {str(e)}")
            else:
                logger.error(f"[{request_id}] Tool {tool_name} not found in tool_map")
                
        except Exception as e:
            logger.error(f"[{request_id}] Error processing tool call: {str(e)}")
