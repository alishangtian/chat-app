import json
import logging
import random
import time
from typing import Dict, Any, List, AsyncGenerator, Optional, Union
import asyncio
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from fastapi import HTTPException

from config import settings

logger = logging.getLogger(__name__)

class WebCrawler:
    """高级网页爬取工具，包含反爬虫和内容提取功能"""
    
    # 常用User-Agent列表
    USER_AGENTS = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:89.0) Gecko/20100101 Firefox/89.0',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.1 Safari/605.1.15',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/91.0.864.59'
    ]

    def __init__(self, proxy_pool: Optional[List[str]] = None):
        """
        初始化爬虫实例
        
        Args:
            proxy_pool: 可选的代理IP池列表，格式如 ["http://ip:port", ...]
        """
        self.proxy_pool = proxy_pool or []
        self.retry_count = 3
        self.retry_delay = 2
        self.timeout = 15.0
        self.last_request_time = {}  # 用于记录对每个域名的最后请求时间

    def _get_random_user_agent(self) -> str:
        """随机获取一个User-Agent"""
        return random.choice(self.USER_AGENTS)

    def _get_random_proxy(self) -> Optional[str]:
        """随机获取一个代理地址"""
        return random.choice(self.proxy_pool) if self.proxy_pool else None

    async def _respect_robots_txt(self, url: str) -> bool:
        """检查robots.txt规则（简化版）"""
        try:
            parsed_url = urlparse(url)
            base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
            robots_url = f"{base_url}/robots.txt"
            
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(robots_url)
                if response.status_code == 200:
                    # 简单检查是否允许访问
                    return 'Disallow: ' + parsed_url.path not in response.text
            return True
        except Exception:
            return True  # 如果无法获取robots.txt，默认允许访问

    def _is_rate_limited(self, domain: str) -> bool:
        """检查是否需要限制请求频率"""
        current_time = time.time()
        if domain in self.last_request_time:
            time_diff = current_time - self.last_request_time[domain]
            return time_diff < 1.0  # 对每个域名限制最少1秒间隔
        return False

    async def _wait_for_rate_limit(self, domain: str):
        """等待直到可以发送下一个请求"""
        while self._is_rate_limited(domain):
            await asyncio.sleep(0.1)
        self.last_request_time[domain] = time.time()

    def _extract_main_content(self, html_content: str) -> Dict[str, str]:
        """
        智能提取网页主要内容
        
        Returns:
            Dict包含标题、正文、元数据等信息
        """
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # 移除无用标签
        for tag in soup(['script', 'style', 'nav', 'footer', 'iframe']):
            tag.decompose()
        
        # 提取标题
        title = ""
        if soup.title:
            title = soup.title.string
        elif soup.h1:
            title = soup.h1.get_text(strip=True)
        
        # 提取meta描述
        meta_desc = ""
        meta_tag = soup.find('meta', attrs={'name': 'description'})
        if meta_tag:
            meta_desc = meta_tag.get('content', '')
        
        # 提取正文内容
        # 1. 首先尝试找到文章主体
        main_content = ""
        content_tags = soup.find_all(['article', 'main', 'div'], class_=['content', 'article', 'post'])
        
        if content_tags:
            # 使用最长的内容块作为主要内容
            main_content = max([tag.get_text(strip=True) for tag in content_tags], key=len)
        else:
            # 如果找不到明显的内容标记，提取所有p标签文本
            paragraphs = soup.find_all('p')
            main_content = '\n'.join(p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 50)
        
        return {
            'title': title,
            'meta_description': meta_desc,
            'main_content': main_content,
            'text_length': len(main_content)
        }

    async def _handle_javascript_page(self, url: str) -> Optional[str]:
        """处理JavaScript渲染的页面（需要时可以集成Playwright或Selenium）"""
        # TODO: 实现JavaScript页面渲染
        logger.warning(f"检测到JavaScript渲染页面: {url}，当前版本暂不支持完整渲染")
        return None

    async def fetch_webpage(self, url: str, js_render: bool = False) -> Dict[str, Any]:
        """
        获取并解析网页内容
        
        Args:
            url: 目标网页URL
            js_render: 是否需要JavaScript渲染
        
        Returns:
            Dict包含状态码、内容等信息
        """
        if not await self._respect_robots_txt(url):
            raise ValueError(f"根据robots.txt规则，不允许爬取该URL: {url}")
        
        domain = urlparse(url).netloc
        await self._wait_for_rate_limit(domain)
        
        headers = {
            'User-Agent': self._get_random_user_agent(),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Cache-Control': 'max-age=0'
        }
        
        proxy = self._get_random_proxy()
        
        for attempt in range(self.retry_count):
            try:
                async with httpx.AsyncClient(
                    timeout=self.timeout,
                    proxies=proxy,
                    follow_redirects=True
                ) as client:
                    response = await client.get(url, headers=headers)
                    
                    # 检查是否遇到反爬措施
                    if response.status_code == 403 or response.status_code == 429:
                        logger.warning(f"可能触发反爬机制 (状态码: {response.status_code})")
                        await asyncio.sleep(self.retry_delay * (attempt + 1))
                        continue
                    
                    response.raise_for_status()
                    
                    # 检查内容类型
                    content_type = response.headers.get('content-type', '').lower()
                    if 'text/html' not in content_type:
                        return {
                            'status': 'error',
                            'message': f'不支持的内容类型: {content_type}',
                            'status_code': response.status_code
                        }
                    
                    # 检查是否需要JavaScript渲染
                    if js_render and 'application/javascript' in content_type:
                        js_content = await self._handle_javascript_page(url)
                        if js_content:
                            extracted_content = self._extract_main_content(js_content)
                        else:
                            extracted_content = self._extract_main_content(response.text)
                    else:
                        extracted_content = self._extract_main_content(response.text)
                    
                    return {
                        'status': 'success',
                        'status_code': response.status_code,
                        'url': str(response.url),
                        'content': extracted_content,
                        'headers': dict(response.headers)
                    }
                    
            except httpx.HTTPStatusError as e:
                logger.error(f"HTTP错误: {str(e)}")
                if attempt == self.retry_count - 1:
                    return {
                        'status': 'error',
                        'message': f'HTTP错误: {str(e)}',
                        'status_code': e.response.status_code if e.response else None
                    }
                
            except httpx.RequestError as e:
                logger.error(f"请求错误: {str(e)}")
                if attempt == self.retry_count - 1:
                    return {
                        'status': 'error',
                        'message': f'请求错误: {str(e)}',
                        'status_code': None
                    }
                
            except Exception as e:
                logger.error(f"未知错误: {str(e)}")
                if attempt == self.retry_count - 1:
                    return {
                        'status': 'error',
                        'message': f'未知错误: {str(e)}',
                        'status_code': None
                    }
            
            await asyncio.sleep(self.retry_delay * (attempt + 1))
        
        return {
            'status': 'error',
            'message': '达到最大重试次数',
            'status_code': None
        }

# 创建全局爬虫实例
crawler = WebCrawler()

async def summarize_content(content: str, model: str, api_token: str, max_length: int) -> str:
    """使用模型对长文本进行总结"""
    try:
        messages = [
            {
                "role": "system",
                "content": f"下述文本过长，请进行精简和总结，使最终输出内容长度接近{max_length}字符。\n 切记：需要保持原始重要信息，去除不相关的网页标记信息"
            },
            {
                "role": "user",
                "content": content
            }
        ]
        
        # 打印格式化的提示词
        logger.info("总结内容的提示词:\n" + json.dumps(messages, ensure_ascii=False, indent=2))
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                settings.BASE_URL,
                json={
                    "model": model,
                    "messages": messages,
                    "stream": False,
                    "max_tokens": settings.MAX_CONTENT_LENGTH,
                    "temperature": 0.7
                },
                headers={"Authorization": f"Bearer {api_token}"},
                timeout=30.0
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get("choices") and result["choices"][0].get("message"):
                    return result["choices"][0]["message"]["content"]
            return content[:max_length]  # 如果总结失败，直接截断
    except Exception as e:
        logger.error(f"内容总结失败: {str(e)}")
        return content[:max_length]  # 发生错误时直接截断

async def fetch_webpage_content(url: str) -> Dict[str, str]:
    """获取网页内容并提取正文，返回格式化的内容"""
    try:
        result = await crawler.fetch_webpage(url)
        if result['status'] == 'success':
            content = result['content']
            main_content = content['main_content']
            
            # 检查内容长度是否超过限制
            if len(main_content) > settings.MAX_CONTENT_LENGTH:
                logger.info(f"网页内容长度超过限制 ({len(main_content)}字符)，进行内容总结")
                main_content = await summarize_content(
                    main_content,
                    settings.MODEL,
                    settings.API_TOKEN,
                    settings.MAX_CONTENT_LENGTH
                )
                logger.info(f"内容总结完成，总结后长度: {len(main_content)}字符 \n\n 总结后内容：{main_content}")
            
            # 返回结构化内容
            return {
                'status': 'success',
                'link': url,
                'title': content['title'],
                'description': content.get('meta_description', ''),
                'content': main_content
            }
        return {
            'status': 'error',
            'link': url,
            'error': '无法获取网页内容'
        }
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error fetching webpage: {error_msg}")
        return {
            'status': 'error',
            'link': url,
            'error': f'爬取失败: {error_msg}'
        }

async def search_with_serper(args: Dict[str, Any], request_id: str = None) -> Dict[str, Any]:
    """使用Serper API进行搜索，并分步返回结果。

    Args:
        args: 包含查询参数的字典
        request_id: 请求ID用于日志追踪

    Returns:
        Dict[str, Any]: 搜索结果，包含状态和数据，以及是否为初始结果的标志
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
        
        # 首先返回标题和摘要内容
        initial_results = []
        
        # 处理answerBox结果
        if "answerBox" in data:
            answer_box = data["answerBox"]
            if isinstance(answer_box, dict):
                initial_results.append({
                    "title": answer_box.get("title", ""),
                    "content": answer_box.get("answer", ""),
                    "source": answer_box.get("source", ""),
                    "isAnswerBox": True,
                    "link": "",
                    "needsFetch": False
                })
        
        # 处理普通搜索结果
        if "organic" in data:
            for result in data["organic"]:
                initial_results.append({
                    "title": result.get("title", ""),
                    "content": result.get("snippet", ""),
                    "link": result.get("link", ""),
                    "isAnswerBox": False,
                    "needsFetch": True,
                    "fetchStatus": "pending"  # pending, fetching, completed, error
                })
        
        return {
            "status": "success",
            "data": initial_results,
            "isInitialResults": True
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
                    result = await tool_function(tool_arguments, request_id)
                    
                    # 判断是否为搜索工具
                    if tool_name.startswith('search'):
                        if result.get("status") == "success":
                            # 首先返回初始结果
                            initial_results = result.get("data", [])
                            yield {
                                "type": "search_results",
                                "results": initial_results,
                                "isInitialResults": True
                            }
                            
                            # 只处理前5个需要爬取的网页
                            fetch_count = 0
                            for result in initial_results:
                                if result.get("needsFetch") and fetch_count < 5:
                                    fetch_count += 1
                                    try:
                                        # 更新状态为正在爬取
                                        result["fetchStatus"] = "fetching"
                                        yield {
                                            "type": "search_result_update",
                                            "result": result
                                        }
                                        
                                        # 爬取网页内容
                                        fetch_result = await fetch_webpage_content(result["link"])
                                        
                                        # 更新结果状态
                                        if fetch_result['status'] == 'success':
                                            result.update({
                                                "fetchStatus": "completed",
                                                "title": fetch_result['title'],
                                                "description": fetch_result['description'],
                                                "content": fetch_result['content']
                                            })
                                        else:
                                            result.update({
                                                "fetchStatus": "error",
                                                "error": fetch_result['error']
                                            })
                                            
                                        # 返回更新后的结果
                                        yield {
                                            "type": "search_result_update",
                                            "result": result
                                        }
                                    except Exception as e:
                                        error_msg = str(e)
                                        logger.error(f"Error fetching content for {result['link']}: {error_msg}")
                                        result["fetchStatus"] = "error"
                                        result["error"] = f"爬取失败: {error_msg}"
                                        yield {
                                            "type": "search_result_update",
                                            "result": result
                                        }
                            
                            # 发送网页读取完成事件
                            yield {
                                "type": "event",
                                "data": json.dumps({
                                    "status": "parsing_completed",
                                    "message": "网页读取完成"
                                }, ensure_ascii=False)
                            }
                    else:
                        # 非搜索工具的结果直接返回
                        yield {
                            "type": "tool_result",
                            "tool_name": tool_name,
                            "result": result
                        }
                        
                    logger.info(f"[{request_id}] Tool execution completed")
                    
                except Exception as e:
                    logger.error(f"[{request_id}] Error executing tool {tool_name}: {str(e)}")
            else:
                logger.error(f"[{request_id}] Tool {tool_name} not found in tool_map")
                
        except Exception as e:
            logger.error(f"[{request_id}] Error processing tool call: {str(e)}")
