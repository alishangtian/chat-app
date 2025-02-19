import httpx
from bs4 import BeautifulSoup
from typing import List, Dict, Any
import logging

logger = logging.getLogger(__name__)

def crawl_arxiv_papers(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    爬取arXiv论文搜索结果
    
    Args:
        args: 包含查询参数的字典，必须包含query字段
        
    Returns:
        List[Dict[str, Any]]: 论文信息列表，每个字典包含论文的详细信息
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
        # 发送搜索请求
        with httpx.Client(timeout=30.0) as client:
            # 更新状态为正在获取数据
            logger.info(f"[{request_id}] 正在获取arXiv数据...")
            logger.info(f"[{request_id}] 发送请求到: {base_url}")
            logger.info(f"[{request_id}] 请求参数: {params}")
            response = client.get(base_url, params=params)
            response.raise_for_status()
            logger.info(f"[{request_id}] 请求成功，状态码: {response.status_code}")
            
            # 解析HTML内容
            soup = BeautifulSoup(response.text, 'html.parser')
            papers = []
            
            # 查找所有论文结果
            paper_elements = soup.select('li.arxiv-result')
            logger.info(f"[{request_id}] 找到 {len(paper_elements)} 篇论文")
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
                    logger.info(f"[{request_id}] 找到论文: {title}")
                    
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
                        'link': f"https://arxiv.org/abs/{paper_id.split(':')[-1]}"
                    })
                    
                except Exception as e:
                    logger.error(f"Error parsing paper: {str(e)}")
                    continue
            
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
