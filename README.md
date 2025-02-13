| Chat App |
|:-------------------:|
基于搜索引擎Api serper.dev的人工智能助手

## 页面
![image](https://github.com/user-attachments/assets/14d97cab-e9ea-4924-ab42-c281de382d2b)

## start process
### modify .env file
```env
# 搜索引擎serper.dev的API key
SERPER_API_KEY=
# 推理服务的API地址
BASE_URL=https://ip:port/v1/chat/completions
# 推理服务的API token
API_TOKEN=
# 推理服务的模型名称
MODEL=
# 推理服务的函数调用模型名称
FUNCTIONCALL_MODEL=

```
### start application
```shell
uvicorn main:app --reload
```

```log
INFO:     Will watch for changes in these directories: ['D:\\home\\workspace\\chat-app\\backend']
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     Started reloader process [3736] using StatReload
INFO:     Started server process [25668]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
```
