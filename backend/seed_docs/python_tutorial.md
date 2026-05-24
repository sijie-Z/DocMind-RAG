# Python 编程基础教程

Python 是一种高级、解释型、面向对象的编程语言，由 Guido van Rossum 于 1991 年创建。

## 基本语法

### 变量和数据类型
```python
name = "DocMind"        # 字符串
version = 1.0            # 浮点数
users_count = 100        # 整数
is_active = True         # 布尔值
```

### 列表和循环
```python
fruits = ["苹果", "香蕉", "橙子"]
for fruit in fruits:
    print(fruit)
```

### 函数定义
```python
def greet(name: str) -> str:
    return f"你好, {name}!"

print(greet("世界"))
```

## 面向对象编程

```python
class Document:
    def __init__(self, title: str, content: str):
        self.title = title
        self.content = content

    def summarize(self) -> str:
        return f"文档: {self.title}, 长度: {len(self.content)} 字"

doc = Document("Python 教程", "内容...")
print(doc.summarize())
```

## 异步编程

```python
import asyncio

async def fetch_data(url: str) -> str:
    # 模拟网络请求
    await asyncio.sleep(1)
    return f"从 {url} 获取的数据"

async def main():
    result = await fetch_data("https://example.com")
    print(result)

asyncio.run(main())
```

Python 广泛应用于 Web 开发、数据分析、人工智能、自动化脚本等领域。
