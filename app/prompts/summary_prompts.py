from __future__ import annotations


SUMMARY_MAP_SYSTEM_PROMPT = (
    "你是文档摘要助手。请只根据给定文本片段生成中文摘要，不要编造未出现的信息。"
)


SUMMARY_REDUCE_SYSTEM_PROMPT = (
    "你是文档级总结助手。请把多个局部摘要整合成一份结构化中文总结。"
)


def build_map_prompt(*, question: str, content: str) -> str:
    return (
        f"用户请求：{question}\n\n"
        "请阅读下面的文档片段，输出 3-5 条简洁摘要，覆盖关键信息、风险和结论。\n\n"
        f"文档片段：\n{content}"
    )


def build_reduce_prompt(*, question: str, partial_summaries: list[str]) -> str:
    joined = "\n\n".join(f"局部摘要 {index + 1}:\n{summary}" for index, summary in enumerate(partial_summaries))
    return (
        f"用户请求：{question}\n\n"
        "请将下面的局部摘要整合为最终结果，并使用以下格式输出：\n"
        "一、文档概览\n"
        "二、关键要点\n"
        "三、风险与问题\n"
        "四、下一步建议\n\n"
        f"{joined}"
    )
