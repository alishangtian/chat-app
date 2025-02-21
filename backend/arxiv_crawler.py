from bs4 import BeautifulSoup
from typing import List, Dict, Any
import logging

from base_crawler import BaseCrawler

logger = logging.getLogger(__name__)

class ArxivCrawler(BaseCrawler):
    """arXiv论文爬取工具，专注于论文内容解析"""

    @staticmethod
    def _parse_arxiv_results(html_content: str) -> List[Dict[str, Any]]:
        """解析arXiv搜索结果页面"""
        soup = BeautifulSoup(html_content, 'html.parser')
        papers = []
        
        paper_elements = soup.select('li.arxiv-result')
        for paper in paper_elements:
            try:
                # 提取论文ID和链接
                title_elem = paper.select_one('p.list-title a')
                paper_id = title_elem.text.strip()
                
                # 提取PDF链接
                pdf_link = None
                links = paper.select('p.list-title span a')
                for link in links:
                    if link.text.strip().lower() == 'pdf':
                        pdf_link = f"https://arxiv.org{link['href']}"
                        break
                
                # 提取标题
                title = paper.select_one('p.title.is-5').text.strip()
                
                # 提取作者
                authors = []
                authors_elem = paper.select_one('p.authors')
                if authors_elem:
                    author_links = authors_elem.select('a')
                    authors = [a.text.strip() for a in author_links]
                
                # 提取摘要
                abstract = ""
                abstract_elem = paper.select_one('p.abstract span.abstract-full')
                if abstract_elem:
                    abstract = abstract_elem.text.strip()
                else:
                    abstract_elem = paper.select_one('p.abstract span.abstract-short')
                    if abstract_elem:
                        abstract = abstract_elem.text.strip()
                
                # 提取发布日期
                submitted = ""
                submitted_elem = paper.select_one('p.is-size-7 span.has-text-black-bis:contains("Submitted")')
                if submitted_elem:
                    submitted_text = submitted_elem.find_next_sibling(string=True)
                    if submitted_text:
                        submitted = submitted_text.strip().strip(',')
                
                papers.append({
                    'paper_id': paper_id.split(':')[-1],
                    'title': title,
                    'authors': authors,
                    'content': abstract,
                    'submitted': submitted,
                    'link': f"https://arxiv.org/abs/{paper_id.split(':')[-1]}",
                    'isAnswerBox': False,
                    'needsFetch': False,
                    'fetchStatus': 'completed'
                })
                
            except Exception as e:
                logger.error(f"Error parsing paper: {str(e)}")
                continue
                
        return papers

    async def crawl_arxiv_papers(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        爬取arXiv论文搜索结果
        
        Args:
            args: 包含查询参数的字典，必须包含query字段
            
        Returns:
            Dict[str, Any]: 论文信息列表和状态信息
        """
        query = args.get("query")
        request_id = args.get("request_id")
        if not query:
            raise ValueError("Missing query parameter")
            
        logger.info(f"[{request_id}] 开始搜索arXiv论文，关键词: {query}")
        base_url = "https://arxiv.org/search/"
        params = {
            "query": query,
            "searchtype": "all",
            "abstracts": "show",
            "order": "-announced_date_first",
            "size": "25"
        }
        
        # 首先返回初始状态
        initial_response = {
            "status": "success",
            "data": [],
            "isInitialResults": True,
            "message": "开始获取arXiv论文数据..."
        }
        
        try:
            # 构建完整的URL
            from urllib.parse import urlencode
            full_url = f"{base_url}?{urlencode(params)}"
            
            # 使用基类的fetch_url方法获取页面内容
            result = await self.fetch_url(full_url)
            
            if result['status'] != 'success':
                return {
                    "status": "error",
                    "data": [],
                    "isInitialResults": False,
                    "message": result.get('message', 'Failed to fetch arXiv results')
                }
            
            # 解析搜索结果
            papers = self._parse_arxiv_results(result['content'])
            logger.info(f"[{request_id}] 找到 {len(papers)} 篇论文")
            
            # 返回完整结果
            return {
                "status": "success",
                "data": papers,
                "isInitialResults": False,
                "message": f"成功获取到 {len(papers)} 篇论文"
            }
            
        except Exception as e:
            error_msg = f"arXiv爬取失败: {str(e)}"
            logger.error(f"[{request_id}] {error_msg}")
            return {
                "status": "error",
                "data": [],
                "isInitialResults": False,
                "message": error_msg
            }

# 创建全局爬虫实例
crawler = ArxivCrawler()

async def crawl_arxiv_papers(args: Dict[str, Any]) -> Dict[str, Any]:
    """兼容性包装函数，使用全局爬虫实例"""
    return await crawler.crawl_arxiv_papers(args)
