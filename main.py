from data_analysis_agent import DataAnalysisAgent
from config.llm_config import LLMConfig

def main():
    llm_config = LLMConfig()
    agent = DataAnalysisAgent(llm_config)
    files = [
        r".\cpc.csv",
        r".\shop.csv"]
    report = agent.analyze(user_input="基于外卖门店的数据，围绕营收/成本/利润/流量四部分，并绘制相关图表。最后生成汇报给我。",files=files)
    print(report)
    
if __name__ == "__main__":
    main()
    