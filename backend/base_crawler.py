import logging
import random
import time
from typing import Dict, Any, List, Optional
import asyncio
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

class BaseCrawler:
    """基础爬虫类，提供通用的网页获取功能"""
    
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

    async def fetch_url(self, url: str, headers: Optional[Dict[str, str]] = None, 
                       delay: float = 1.0) -> Dict[str, Any]:
        """
        获取URL内容的基础方法
        
        Args:
            url: 目标URL
            headers: 可选的自定义请求头
            delay: 请求间隔延迟时间(秒)
        
        Returns:
            Dict包含状态码、响应内容等信息
        """
        if not await self._respect_robots_txt(url):
            raise ValueError(f"根据robots.txt规则，不允许爬取该URL: {url}")
        
        domain = urlparse(url).netloc
        await asyncio.sleep(delay)
        
        default_headers = {
            'User-Agent': self._get_random_user_agent(),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        }
        
        # 合并自定义请求头
        if headers:
            default_headers.update(headers)
        
        proxy = self._get_random_proxy()
        
        for attempt in range(self.retry_count):
            try:
                # 构建代理配置
                client_params = {
                    "timeout": self.timeout,
                    "follow_redirects": True
                }
                if proxy:
                    client_params["proxies"] = {"http://": proxy, "https://": proxy}
                
                async with httpx.AsyncClient(**client_params) as client:
                    response = await client.get(url, headers=default_headers)
                    
                    # 检查是否遇到反爬措施
                    if response.status_code == 403 or response.status_code == 429:
                        logger.warning(f"可能触发反爬机制 (状态码: {response.status_code})")
                        await asyncio.sleep(self.retry_delay * (attempt + 1))
                        continue
                    
                    response.raise_for_status()
                    
                    return {
                        'status': 'success',
                        'status_code': response.status_code,
                        'url': str(response.url),
                        'content': response.text,
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
