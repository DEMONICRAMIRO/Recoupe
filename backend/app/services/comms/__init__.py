from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class SendResult:
    success: bool
    provider: str
    provider_id: str | None
    status: str
    error: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)
