class ChatApp {
    constructor() {
        this.messageInput = document.querySelector('.search-box input[type="text"]');
        this.sendButton = document.querySelector('.send-btn');
        this.answerContent = document.querySelector('.answer-content');
        this.referenceCards = document.querySelector('.reference-cards');
        this.toolsSelection = document.querySelector('#toolsSelection');
        this.referencesSection = document.querySelector('.references');
        this.currentAnswer = ''; // 用于缓存当前对话的原始文本内容
        
        this.initializeEventListeners();
        this.loadAvailableTools();
    }

    // 工具函数：截断过长的内容
    truncateContent(content, maxLength = 300) {
        if (!content) return '';
        return content.length > maxLength ? content.slice(0, maxLength) + '...' : content;
    }

    async loadAvailableTools() {
        try {
            const response = await fetch('/api/tools');
            const data = await response.json();
            
            // 清空现有工具选项
            this.toolsSelection.innerHTML = '';
            
            // 添加新的工具选项
            data.tools.forEach(tool => {
                const toolOption = document.createElement('div');
                toolOption.className = 'tool-option';
                toolOption.innerHTML = `
                    <input type="checkbox" id="${tool.name}" name="tool" value="${tool.name}" checked>
                    <label for="${tool.name}" title="${tool.description}">${tool.name}</label>
                `;
                this.toolsSelection.appendChild(toolOption);
            });
            
            // 更新工具选项引用
            this.toolOptions = document.querySelectorAll('.tool-option input[type="checkbox"]');
        } catch (error) {
            console.error('加载工具列表失败:', error);
        }
    }

    initializeEventListeners() {
        this.sendButton.addEventListener('click', () => this.handleSendMessage());
        this.messageInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') this.handleSendMessage();
        });

    }

    async handleSendMessage() {
        const message = this.messageInput.value.trim();
        if (!message) return;

        // 清空输入框
        this.messageInput.value = '';
        
        // 添加用户问题到对话框
        const userMessageHtml = `
            <div class="user-message">
                <span class="icon">👤</span>
                ${message}
            </div>
        `;
        
        // 如果有loading或placeholder，清除它
        if (this.answerContent.querySelector('.loading') || this.answerContent.querySelector('.placeholder')) {
            this.answerContent.innerHTML = userMessageHtml;
        } else {
            this.answerContent.innerHTML += userMessageHtml;
        }
        
        // 添加AI思考中的提示
        this.answerContent.innerHTML += '<div class="loading">思考中...</div>';
        this.currentAnswer = ''; // 重置当前对话的内容缓存

        let eventSource = null;
        try {
            // 准备请求数据
            const requestData = {
                message: message,
                request_id: Date.now().toString(),
                selected_tools: Array.from(this.toolOptions)
                    .filter(option => option.checked)
                    .map(option => option.value)
            };

            // 创建 SSE 连接
            const url = new URL('/api/chat', window.location.href);
            eventSource = new EventSource(`${url}?${new URLSearchParams({
                message: requestData.message,
                request_id: requestData.request_id,
                selected_tools: requestData.selected_tools.join(',')
            }).toString()}`);
            
            // 处理各种事件类型
            ['status', 'search_results', 'search_result_update', 'answer', 'error', 'complete'].forEach(eventType => {
                eventSource.addEventListener(eventType, (event) => {
                    try {
                        // 检查连接状态
                        if (eventSource.readyState === EventSource.CLOSED) {
                            console.log(`Connection closed, ignoring ${eventType} event`);
                            return;
                        }
                        
                        // 检查event.data是否为undefined或空
                        if (!event.data) {
                            console.warn(`Received empty ${eventType} event data`);
                            return;
                        }
                        
                        // 处理事件数据
                        this.handleEventData(event);
                        
                        // 如果是complete事件，关闭连接
                        if (eventType === 'complete') {
                            console.log('Received complete event, closing connection');
                            eventSource.close();
                            return;
                        }
                    } catch (error) {
                        console.error(`Error handling ${eventType} event:`, error);
                        this.showError({
                            error: '处理服务器响应时发生错误',
                            details: error.message
                        });
                    }
                });
            });

            eventSource.onerror = (error) => {
                console.error('SSE Error:', error);
                // 检查连接状态
                switch(eventSource.readyState) {
                    case EventSource.CONNECTING:
                        console.log('正在重新连接...');
                        break;
                    case EventSource.CLOSED:
                        console.log('Connection was closed');
                        // 只有在确实需要显示错误时才显示
                        if (!this.currentAnswer) {
                            this.showError('连接已断开，请重试');
                        }
                        break;
                    default:
                        this.showError('连接出错，请重试');
                        eventSource.close();
                }
            };

        } catch (error) {
            console.error('Error:', error);
            this.showError(`发生错误: ${error.message}`);
            if (eventSource) {
                eventSource.close();
            }
        }
    }

    handleEventData(event) {
        try {
            console.log('Received SSE event:', event.type, event.data);
            // 增强空数据检查
            if (!event.data || event.data.trim() === '') {
                console.warn('Received empty event data');
                return;
            }
            
            let data;
            try {
                data = JSON.parse(event.data);
            } catch (parseError) {
                console.error('Failed to parse event data:', parseError);
                return;
            }
            
            // 验证数据格式
            if (!data || typeof data !== 'object') {
                console.warn('Invalid event data format');
                return;
            }

            switch (event.type) {
                case 'status':
                    this.updateStatus(data);
                    break;
                case 'search_results':
                if (true) {
                        this.updateSearchResults(data);
                    }
                    break;
                case 'search_result_update':
                if (true) {
                        this.updateSearchResult(data);
                    }
                    break;
                case 'answer':
                    this.updateAnswer(data);
                    break;
                case 'error':
                    this.showError(data);
                    break;
                default:
                    console.warn('Unknown event type:', event.type, data);
            }
        } catch (error) {
            console.error('Error handling event data:', error, event);
            // 根据错误类型提供更具体的错误信息
            let errorMessage = '处理服务器响应时发生错误';
            let errorDetails = error.message;
            
            if (error instanceof SyntaxError) {
                errorMessage = '无效的服务器响应格式';
                errorDetails = '服务器返回了无效的数据格式';
            }
            
            this.showError({
                error: errorMessage,
                details: errorDetails
            });
        }
    }

    updateStatus(data) {
        console.log('Status update:', data);
        switch (data.status) {
            case 'searching':
                if (true) {
                    // 清空并添加搜索状态提示到搜索结果框
                    this.referenceCards.innerHTML = '<div class="loading">搜索中...</div>';
                    this.referencesSection.classList.add('visible');
                }
                // 更新聊天框状态
                const existingLoadingSearch = this.answerContent.querySelector('.loading');
                if (existingLoadingSearch) {
                    existingLoadingSearch.textContent = '搜索中...';
                }
                break;
            case 'parsing':
                // 更新搜索结果框的状态提示
                const loadingDivParse = this.referenceCards.querySelector('.loading');
                if (loadingDivParse) {
                    loadingDivParse.textContent = '网页解读中...';
                }
                // 更新聊天框状态
                const existingLoadingParse = this.answerContent.querySelector('.loading');
                if (existingLoadingParse) {
                    existingLoadingParse.textContent = '网页解读中...';
                }
                break;
            case 'parsing_completed':
                // 更新搜索结果框的状态提示
                const loadingDivComplete = this.referenceCards.querySelector('.loading');
                if (loadingDivComplete) {
                    loadingDivComplete.textContent = '解读结束';
                }
                // 更新聊天框状态
                const existingLoadingComplete = this.answerContent.querySelector('.loading');
                if (existingLoadingComplete) {
                    existingLoadingComplete.textContent = '解读结束';
                }
                break;
            case 'generating':
                // 移除搜索结果框中的状态提示（如果存在）
                const loadingDivGen = this.referenceCards.querySelector('.loading');
                if (loadingDivGen) {
                    loadingDivGen.remove();
                }
                // 移除现有的loading元素（如果有）
                const existingLoading = this.answerContent.querySelector('.loading');
                if (existingLoading) {
                    existingLoading.remove();
                }
                // 添加新的loading提示
                this.answerContent.insertAdjacentHTML('beforeend', '<div class="loading">生成回答中...</div>');
                break;
            case 'completed':
                console.log('Chat completed');
                break;
        }
    }

    updateSearchResults(data) {

        // 清空原有的搜索结果
        this.referenceCards.innerHTML = '';

        if (data.status === 'error') {
            this.referenceCards.innerHTML = `
                <div class="error">
                    <div>搜索出错：${data.error}</div>
                    <div class="error-details">${data.details || ''}</div>
                </div>`;
            this.referencesSection.classList.remove('visible');
            return;
        }

        const results = data.results || [];
        if (results.length === 0) {
            this.referenceCards.innerHTML = '<div class="no-results">暂无相关搜索结果</div>';
            this.referencesSection.classList.remove('visible');
            return;
        }

        // 显示搜索框
        this.referencesSection.classList.add('visible');

        // 流式添加新的搜索结果
        results.forEach((result, index) => {
            setTimeout(() => {
                const resultElement = document.createElement('div');
                resultElement.className = 'reference-card';
                resultElement.id = `result-${index}`;
                resultElement.style.opacity = '0';
                resultElement.style.transition = 'opacity 0.3s ease';
                
                resultElement.innerHTML = `
                    <div class="content">
                        <h3 class="paper-title">
                            <a href="${result.link}" target="_blank">${result.title || '无标题'}</a>
                        </h3>
                        ${result.isArxiv ? `
                            <div class="paper-authors">${Array.isArray(result.authors) ? result.authors.join(', ') : (result.authors || '未知作者')}</div>
                            <div class="paper-content">${this.truncateContent(result.abstract || result.content) || '无内容'}</div>
                            <div class="paper-date">发布时间：${result.submitted || '未知日期'}</div>
                        ` : `
                            <div class="paper-content">${this.truncateContent(result.content) || '无内容'}</div>
                            ${result.date ? `<div class="paper-date">发布时间：${result.date}</div>` : ''}
                        `}
                    </div>
                `;
                this.referenceCards.appendChild(resultElement);
                // 添加淡入动画
                setTimeout(() => resultElement.style.opacity = '1', 10);
            }, index * 100);
        });
    }

    updateSearchResult(data) {
        // 不处理网页爬取更新
        return;
    }

    updateAnswer(data) {
        if (data.status === 'error') {
            this.showError(data);
            return;
        }

        // 如果是第一次收到答案，清除loading状态并初始化AI回答div
        const loadingElement = this.answerContent.querySelector('.loading');
        if (loadingElement) {
            loadingElement.remove();
            // 创建新的AI回答div
            const aiMessageDiv = document.createElement('div');
            aiMessageDiv.className = 'ai-message';
            const markdownDiv = document.createElement('div');
            markdownDiv.className = 'markdown-content';
            aiMessageDiv.appendChild(markdownDiv);
            this.answerContent.appendChild(aiMessageDiv);
        }
        
        // 初始化showdown转换器
        const converter = new showdown.Converter({
            strikethrough: true,
            tables: true,
            tasklists: true,
            smoothLivePreview: true,
            simpleLineBreaks: true,
            openLinksInNewWindow: true,
            emoji: true
        });
        
        // 累积新的内容并更新显示
        if (data.content) {
            this.currentAnswer += data.content;
            
            // 获取或创建markdown内容div
            const aiMessageDiv = this.answerContent.querySelector('.ai-message:last-child');
            if (aiMessageDiv) {
                const markdownDiv = aiMessageDiv.querySelector('.markdown-content');
                if (markdownDiv) {
                    // 使用showdown转换Markdown内容
                    markdownDiv.innerHTML = converter.makeHtml(this.currentAnswer);
                    // 自动滚动到底部
                    this.answerContent.scrollTop = this.answerContent.scrollHeight;
                }
            }
        }
    }

    showError(data) {
        console.error('Error:', data);
        // 移除现有的loading元素（如果有）
        const loadingElement = this.answerContent.querySelector('.loading');
        if (loadingElement) {
            loadingElement.remove();
        }
        
        // 创建错误消息元素
        const errorDiv = document.createElement('div');
        errorDiv.className = 'error';
        errorDiv.innerHTML = `
            <div>${data.error || '发生错误'}</div>
            ${data.details ? `<div class="error-details">${data.details}</div>` : ''}
        `;
        
        // 如果存在AI回答div，替换它；否则添加到内容末尾
        const aiMessageDiv = this.answerContent.querySelector('.ai-message:last-child');
        if (aiMessageDiv) {
            aiMessageDiv.replaceWith(errorDiv);
        } else {
            this.answerContent.appendChild(errorDiv);
        }
    }
}

// 初始化应用
document.addEventListener('DOMContentLoaded', () => {
    new ChatApp();
});
