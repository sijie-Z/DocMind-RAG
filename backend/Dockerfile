# ---- Build stage ----
FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libmariadb-dev pkg-config \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# 受限网络（无法直连 pypi.org）可用 --build-arg PIP_INDEX_URL=<镜像源> 构建；
# 不传该参数时行为与之前完全一致（使用默认 PyPI）。
ARG PIP_INDEX_URL
RUN pip install --no-cache-dir ${PIP_INDEX_URL:+-i "$PIP_INDEX_URL"} --prefix=/install -r requirements.txt

# ---------------------------------------------------------------------------
# 图片 OCR 依赖链换 headless（评审阻塞项修复，2026-10-10）：
# rapidocr-onnxruntime 的元数据强拉 GUI 版 opencv-python
# （Requires-Dist: opencv-python>=4.5.1.48）。GUI 版 Linux wheel 实测在
# python:3.11-slim 内 `import cv2` 直接失败：ImportError libxcb.so.1
# （wheel 自带 opencv_python.libs/ 捆绑的 Qt5/X11/xcb 链还缺核心 xcb 库），
# → 图片 OCR 在 docker/k8s 部署目标全量不可用。纯 CPU 文字检测/识别不需要
# 任何 GUI 能力，换同版本 headless 即可（实测零缺库导入）。
# 为什么按路径删除而不是 pip uninstall：--prefix=/install 的安装不在当前
# 解释器的 sys.path 里，pip uninstall 找不到它。GUI 版顶层产物就三项：
# cv2/、opencv_python-*.dist-info/、opencv_python.libs/（与 headless 的
# opencv_python_headless-*.dist-info 前缀不同，不会误伤）。
# ---------------------------------------------------------------------------
RUN SITE=/install/lib/python3.11/site-packages \
    && rm -rf "$SITE"/cv2 "$SITE"/opencv_python-*.dist-info "$SITE"/opencv_python.libs \
    && pip install --no-cache-dir ${PIP_INDEX_URL:+-i "$PIP_INDEX_URL"} \
       --prefix=/install --no-deps opencv-python-headless==5.0.0.93

# ---- Runtime stage ----
FROM python:3.11-slim

WORKDIR /app

# Only install runtime libs (no compiler)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmariadb3 curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -r -s /bin/false -d /app appuser

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# OCR 链导入自检：GUI 版 opencv 混入（或 headless 缺库）在无 X/GL 的 runtime
# import 即炸（libxcb.so.1 类错误）—— 把这类坏镜像挡在构建期，而不是部署后才
# 暴露（docker-build.yml 此前只 build 不 run，缺这类 smoke）。
RUN python -c "import cv2, rapidocr_onnxruntime; print('ocr chain ok:', cv2.__version__)"

# Copy application code
COPY . .

# ---- tiktoken BPE 缓存（issue #164）----
# RAG 聊天依赖 cl100k_base 编码：tiktoken 首次 get_encoding() 时会从公网下载 BPE 文件，
# 离线/受限网络下以 SSLError/ConnectionError 等穿透（比 ImportError 宽得多），聊天不可用。
# 这里把缓存在**构建期**固定进**最终镜像**，运行时零外网依赖：
#   - 缓存文件名 = sha1(BPE 的 URL)（tiktoken 约定），内容由 tiktoken 按 sha256 自校验，
#     文件随仓库携带（docker/tiktoken_cache/，1.6MB，来自 openaipublic.blob.core.windows.net，
#     sha256=223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7），
#     离线机器也能直接构建；
#   - 构建上下文未携带该文件时回退为构建期联网下载（常规环境，tiktoken 官方路径）；
#   - chown 给运行用户 appuser：可读（加载缓存）+ 可写（将来需要其它编码时可自行补缓存）。
ENV TIKTOKEN_CACHE_DIR=/opt/tiktoken_cache
COPY docker/tiktoken_cache/ /opt/tiktoken_cache/
RUN if [ -s "$TIKTOKEN_CACHE_DIR/9b5ad71b2ce5302211f9c61530b329a4922fc6a4" ]; then \
        echo "tiktoken cache: using pre-populated file from build context"; \
    else \
        echo "tiktoken cache: downloading at build time"; \
        python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"; \
    fi \
    && python -c "import tiktoken; print('tiktoken ready, n_vocab =', tiktoken.get_encoding('cl100k_base').n_vocab)" \
    && chown -R appuser:appuser "$TIKTOKEN_CACHE_DIR"

RUN mkdir -p logs temp_uploads \
    && chown -R appuser:appuser /app

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

USER appuser

HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
