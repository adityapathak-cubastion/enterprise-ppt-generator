# backend/progress.py
from dataclasses import dataclass, asdict
from typing import Dict
from datetime import datetime


@dataclass
class ProgressState:
    job_id: str
    step: str
    step_index: int
    total_steps: int
    progress_percent: int
    message: str
    updated_at: str

    def to_dict(self) -> Dict:
        return asdict(self)


class InMemoryProgressStore:
    """
    Simple process-local store.
    In real deployments, swap with Redis or DB.
    """
    def __init__(self) -> None:
        self._store: Dict[str, ProgressState] = {}

    def update(
        self,
        job_id: str,
        step: str,
        step_index: int,
        total_steps: int,
        progress_percent: int,
        message: str
    ) -> ProgressState:
        state = ProgressState(
            job_id=job_id,
            step=step,
            step_index=step_index,
            total_steps=total_steps,
            progress_percent=progress_percent,
            message=message,
            updated_at=datetime.utcnow().isoformat()
        )
        self._store[job_id] = state
        return state

    def get(self, job_id: str) -> Dict:
        state = self._store.get(job_id)
        return state.to_dict() if state else {}
    

# singleton-ish instance
progress_store = InMemoryProgressStore()
