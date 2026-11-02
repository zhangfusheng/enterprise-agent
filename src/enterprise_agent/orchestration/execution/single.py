from enterprise_agent.agents.analysis.definition import AnalysisAgent
from enterprise_agent.agents.analysis.schemas import AnalysisRequest
from enterprise_agent.agents.research.definition import ResearchAgent
from enterprise_agent.agents.research.schemas import ResearchRequest
from enterprise_agent.contracts.context import ExecutionContext


def execute_single(role: str, text: str, context: ExecutionContext, research: ResearchAgent, analysis: AnalysisAgent):
    if role == "research":
        return research.run(ResearchRequest(query=text), context)
    return analysis.run(AnalysisRequest(objective=text), context)

