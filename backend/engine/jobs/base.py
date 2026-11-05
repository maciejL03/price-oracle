from abc import ABC, abstractmethod


class EngineJob(ABC):
    """A single, independently executable unit of async work."""

    @property
    @abstractmethod
    def key(self) -> str:
        """Stable identity used to prevent duplicate queued/running jobs."""
        raise NotImplementedError

    @abstractmethod
    async def execute(self) -> None:
        """Run the job once. Repetition is the scheduler's responsibility."""
        raise NotImplementedError
