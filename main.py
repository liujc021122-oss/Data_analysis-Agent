"""数据分析智能体 - 使用示例

用法：
    python main.py                                     # 使用本地示例数据 cpc.csv / shop.csv（若存在）
    python main.py 你的数据1.csv 你的数据2.csv          # 传入你自己的数据文件
"""

import os
import sys

from data_analysis_agent import DataAnalysisAgent
from config.llm_config import LLMConfig


def main():
    # 优先使用命令行传入的数据文件，否则使用本地默认示例数据
    default_files = ["cpc.csv", "shop.csv"]
    files = sys.argv[1:] or default_files

    # 检查数据文件是否存在，缺失时给出清晰提示而不是直接崩溃
    missing = [f for f in files if not os.path.exists(f)]
    if missing:
        print("❌ 以下数据文件不存在：")
        for f in missing:
            print(f"   - {f}")
        print("\n请先准备你的数据文件，或运行：")
        print("   python main.py <数据文件1> <数据文件2> ...")
        print("\n数据文件会被 .gitignore 忽略（*.csv），不会提交到 GitHub，请使用你自己的数据。")
        sys.exit(1)

    llm_config = LLMConfig()
    agent = DataAnalysisAgent(llm_config)
    report = agent.analyze(
        user_input="基于外卖门店的数据，围绕营收/成本/利润/流量四部分，并绘制相关图表。最后生成汇报给我。",
        files=files,
    )
    print(report)


if __name__ == "__main__":
    main()
