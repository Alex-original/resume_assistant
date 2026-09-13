# 求职助手 · 生产镜像
#
# 为什么用 slim 而不是 alpine：本项目的 PDF 解析依赖 pypdfium2，
# 它带的是预编译的 manylinux 轮子；alpine 用 musl，没有对应轮子，
# 装的时候会去编译，镜像里就得塞一整套编译工具链——得不偿失。
FROM python:3.13-slim

# 不写 .pyc、日志不缓冲（容器里日志要能实时看到）
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# 先把依赖单独拷进来装：改代码不会让依赖层缓存失效，重建快很多
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY web/ ./web/
COPY scripts/ ./scripts/
COPY run.sh ./
RUN chmod +x run.sh && mkdir -p /app/data

# 数据目录挂出来；容器删了数据还在
VOLUME ["/app/data"]

EXPOSE 7870

# 健康检查：/health 是公开的，不需要登录
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:7870/health', timeout=3).status==200 else 1)"

# 单进程足够：接口都是 async，阻塞的 AI 调用走线程池。
# 多用户各写各的 SQLite（WAL），也不需要多进程来扛并发。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7870", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
