from typing import List, Dict, Any


def stitch_contexts(query: str, contexts: List[str], max_chars: int = 1200) -> str:
    """
    功能：无大模型时的“简易回答器”，把最相关的上下文片段拼接成一个可读的答案。
    小白解释：先把查到的内容整合，再在开头附上一个简短说明，模拟有依据的回答。
    """
    header = (
        f"根据检索到的知识片段，下面是和你的问题相关的内容汇总：\n"
        f"问题：{query}\n\n"
    )
    body = "\n\n".join(contexts)
    combined = header + body
    if len(combined) > max_chars:
        return combined[:max_chars] + "..."
    return combined


class SimpleGenerator:
    """
    简单答案生成器
    小白解释：这是一个"智能回答机器人"，能根据问题和上下文生成合适的回答
    """
    
    def __init__(self, max_length: int = 1000):
        self.max_length = max_length
        
    def generate(self, query: str, context: str) -> str:
        """
        生成答案
        小白解释：把问题和相关信息组合起来，生成一个自然的回答
        """
        if not context.strip():
            return "抱歉，我没有找到相关的信息来回答你的问题。"
        
        # 使用stitch_contexts生成答案
        answer = stitch_contexts(query, [context], self.max_length)
        
        return answer
    
    def generate_stream(self, query: str, context: str) -> List[str]:
        """
        流式生成答案（模拟）
        小白解释：一个字一个字地生成回答，像真人打字一样
        """
        full_answer = self.generate(query, context)
        
        # 模拟流式生成，按词语分割
        import re
        words = re.findall(r'\S+\s*', full_answer)
        
        # 逐步生成，每步生成一个词或标点
        stream_chunks = []
        current_text = ""
        
        for word in words:
            current_text += word
            if len(current_text) > 10 or word.strip() in '。！？.!?' :
                stream_chunks.append(current_text)
                current_text = ""
        
        if current_text:
            stream_chunks.append(current_text)
        
        return stream_chunks


# 测试函数
if __name__ == "__main__":
    print("=== 测试答案生成器 ===")
    
    generator = SimpleGenerator()
    
    query = "什么是人工智能？"
    context = """
    人工智能（AI）是计算机科学的一个分支，它企图了解智能的实质，并生产出一种新的能以人类智能相似的方式做出反应的智能机器。
    人工智能的研究包括机器人、语言识别、图像识别、自然语言处理和专家系统等。
    人工智能从诞生以来，理论和技术日益成熟，应用领域也不断扩大。
    """
    
    # 测试完整生成
    answer = generator.generate(query, context)
    print("完整答案:")
    print(answer)
    print("\n" + "="*50 + "\n")
    
    # 测试流式生成
    print("流式生成:")
    stream_chunks = generator.generate_stream(query, context)
    for i, chunk in enumerate(stream_chunks):
        print(f"片段 {i+1}: {chunk}")
    
    print("\n✅ 答案生成器测试完成！")