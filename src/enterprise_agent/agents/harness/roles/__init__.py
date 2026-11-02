"""Registered worker roles for the Deep Agents harness."""

from enterprise_agent.agents.harness.roles.analysis import ANALYSIS, AnalysisHarnessChatModel
from enterprise_agent.agents.harness.roles.research import RESEARCH, ResearchHarnessChatModel

__all__ = [
    "ANALYSIS",
    "RESEARCH",
    "AnalysisHarnessChatModel",
    "ResearchHarnessChatModel",
]
