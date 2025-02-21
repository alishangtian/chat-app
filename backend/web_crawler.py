
import json
import logging
import asyncio
from datetime import datetime
from typing import Dict, Any, Optional
from bs4 import BeautifulSoup
import httpx

from config import settings
from base_crawler import BaseCrawler

logger = logging.getLogger(__name__)

class WebCrawler(BaseCrawler):
    """高级网页爬取工具，专注于通用网页内容提取功能"""

    @staticmethod
    def _extract_main_content(html_content: str) -> Dict[str, str]:
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

    @staticmethod
    async def _handle_javascript_page(url: str) -> Optional[str]:
        """处理JavaScript渲染的页面（需要时可以集成Playwright或Selenium）"""
        # TODO: 实现JavaScript页面渲染
        logger.warning(f"检测到JavaScript渲染页面: {url}，当前版本暂不支持完整渲染")
        return None

    async def fetch_webpage(self, url: str, js_render: bool = False, delay: float = 1.0) -> Dict[str, Any]:
        """
        获取并解析网页内容
        
        Args:
            url: 目标网页URL
            js_render: 是否需要JavaScript渲染
            delay: 请求间隔延迟时间(秒)
        
        Returns:
            Dict包含状态码、内容等信息
        """
        # 使用基类的fetch_url方法获取原始响应
        result = await self.fetch_url(url, delay=delay)
        
        if result['status'] != 'success':
            return result
            
        # 检查内容类型
        content_type = result['headers'].get('content-type', '').lower()
        if 'text/html' not in content_type:
            return {
                'status': 'error',
                'message': f'不支持的内容类型: {content_type}',
                'status_code': result['status_code']
            }
        
        # 检查是否需要JavaScript渲染
        if js_render and 'application/javascript' in content_type:
            js_content = await self._handle_javascript_page(url)
            if js_content:
                extracted_content = self._extract_main_content(js_content)
            else:
                extracted_content = self._extract_main_content(result['content'])
        else:
            extracted_content = self._extract_main_content(result['content'])
        
        return {
            'status': 'success',
            'status_code': result['status_code'],
            'url': result['url'],
            'content': extracted_content,
            'headers': result['headers']
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
        
        client_params = {
            "timeout": 30.0
        }
        async with httpx.AsyncClient(**client_params) as client:
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
    max_retries = 3
    retry_count = 0
    
    while retry_count < max_retries:
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
                
                # 返回结构化内容，包含更多元数据
                return {
                    'status': 'success',
                    'link': url,
                    'title': content['title'],
                    'description': content.get('meta_description', ''),
                    'content': main_content,
                    'metadata': {
                        'text_length': len(main_content),
                        'fetch_time': str(datetime.now()),
                        'is_summarized': len(main_content) > settings.MAX_CONTENT_LENGTH,
                        'content_type': result['headers'].get('content-type', ''),
                        'status_code': result['status_code']
                    }
                }
            
            # 如果是可重试的错误，继续重试
            if result.get('status_code') in [429, 503, 504]:
                retry_count += 1
                if retry_count < max_retries:
                    await asyncio.sleep(2 ** retry_count)  # 指数退避
                    continue
            
            return {
                'status': 'error',
                'link': url,
                'error': '无法获取网页内容',
                'metadata': {
                    'status_code': result.get('status_code'),
                    'retry_count': retry_count,
                    'fetch_time': str(datetime.now())
                }
            }
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error fetching webpage: {error_msg}")
            retry_count += 1
            
            if retry_count < max_retries:
                await asyncio.sleep(2 ** retry_count)  # 指数退避
                continue
                
            return {
                'status': 'error',
                'link': url,
                'error': f'爬取失败: {error_msg}',
                'metadata': {
                    'retry_count': retry_count,
                    'fetch_time': str(datetime.now()),
                    'error_type': type(e).__name__
                }
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
    
    client_params = {
        "timeout": 30.0
    }
    async with httpx.AsyncClient(**client_params) as client:
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
        
        # 返回初始结果，包含更明确的状态信息
        return {
            "status": "success",
            "data": initial_results,
            "isInitialResults": True,
            "message": "已获取搜索结果标题和摘要，正在获取详细内容..."
        }
