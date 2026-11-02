from enterprise_agent.agents.analysis.definition import AnalysisAgent
from enterprise_agent.agents.delegation.service import DelegationService
from enterprise_agent.agents.research.definition import ResearchAgent


class AgentFactory:
    def __init__(self, research: ResearchAgent, analysis: AnalysisAgent, delegation: DelegationService):
        self.research = research
        self.analysis = analysis
        self.delegation = delegation

    def get(self, role: str):
        if role == "research":
            return self.research
        if role == "analysis":
            return self.analysis
        raise KeyError(f"unknown worker role {role}")

