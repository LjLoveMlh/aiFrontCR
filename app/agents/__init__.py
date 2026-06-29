"""阶段2 · LangGraph 评审工作流包.

模块：
    state   - ReviewState TypedDict
    prompts - 系统提示 + user 模板构造
    nodes   - 5 个节点函数（receive / rag / llm / classify / persist）
    workflow- StateGraph 编排 + compile 产物 review_app
"""