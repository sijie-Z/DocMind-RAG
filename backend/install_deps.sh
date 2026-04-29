# 使用系统包管理器安装Python包
echo "安装Python包管理器..."
pacman -S mingw-w64-ucrt-x86_64-python-pip --noconfirm

# 安装基础依赖
echo "安装FastAPI和相关依赖..."
pacman -S mingw-w64-ucrt-x86_64-python-fastapi --noconfirm
pacman -S mingw-w64-ucrt-x86_64-python-uvicorn --noconfirm
pacman -S mingw-w64-ucrt-x86_64-python-pydantic --noconfirm

# 安装文件处理库
echo "安装文件处理库..."
pacman -S mingw-w64-ucrt-x86_64-python-pypdf2 --noconfirm

# 安装HTTP客户端
echo "安装HTTP客户端..."
pacman -S mingw-w64-ucrt-x86_64-python-httpx --noconfirm

# 安装环境变量管理
echo "安装环境变量管理..."
pacman -S mingw-w64-ucrt-x86_64-python-python-dotenv --noconfirm

echo "所有依赖安装完成！"