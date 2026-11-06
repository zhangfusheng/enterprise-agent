from uuid import uuid4

from enterprise_agent.agents.analysis.definition import AnalysisAgent
from enterprise_agent.agents.analysis.schemas import AnalysisRequest, ResearchEvidenceBundle
from enterprise_agent.agents.delegation.context import child_context
from enterprise_agent.contracts.context import ExecutionContext
from enterprise_agent.contracts.delegations import DelegationRecord, DelegationStatus
from enterprise_agent.contracts.results import AgentResult
from enterprise_agent.governance.delegation import DelegationGuard


class DelegationService:
    def __init__(self, guard: DelegationGuard, analysis_agent: AnalysisAgent):
        self.guard = guard
        self.analysis_agent = analysis_agent
        self.records: list[DelegationRecord] = []

    def delegate_analysis(
        self,
        parent: ExecutionContext,
        objective: str,
        evidence: list[dict],
        agent_count: int,
        *,
        research_evidence: ResearchEvidenceBundle | None = None,
    ) -> AgentResult:
        self.guard.check(parent, "analysis", agent_count)
        delegation_id = f"delegation_{uuid4().hex[:16]}"
        agent_id = f"agent_{uuid4().hex[:16]}"
        child = child_context(parent, "analysis", agent_id, delegation_id, {"calculate", "save_report"})
        record = DelegationRecord(
            delegation_id=delegation_id,
            agent_id=agent_id,
            parent_agent_id=parent.agent_id,
            role="analysis",
            depth=child.depth,
            status=DelegationStatus.RUNNING,
            input_summary=f"objective={objective[:120]}; evidence={len(evidence)}",
        )
        self.records.append(record)
        try:
            result = self.analysis_agent.run(
                AnalysisRequest(
                    objective=objective,
                    evidence=evidence,
                    research_evidence=research_evidence,
                ),
                child,
            )
            self.records[-1] = record.model_copy(update={"status": DelegationStatus.SUCCEEDED, "result_summary": result.summary})
            return result
        except Exception as exc:
            self.records[-1] = record.model_copy(update={"status": DelegationStatus.FAILED, "result_summary": type(exc).__name__})
            raise

