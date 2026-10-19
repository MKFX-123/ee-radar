"""本地启动入口；不默认开放到局域网。"""
import uvicorn

if __name__ == '__main__':
    uvicorn.run('server.app:app', host='127.0.0.1', port=8000)
