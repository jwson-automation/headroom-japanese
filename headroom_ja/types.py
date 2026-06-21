"""Shared result model. Every compressor returns this."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CompressResult:
    text: str                       # compressed text to send to the LLM
    original_tokens: int
    compressed_tokens: int
    content_type: str = "unknown"
    cache_key: str | None = None    # for CCR; None means non-reversible
    kept: int = 0
    dropped: int = 0
    meta: dict = field(default_factory=dict)

    @property
    def ratio(self) -> float:
        """Savings ratio (0..1). 0.9 = reduced by 90%."""
        if self.original_tokens <= 0:
            return 0.0
        return 1.0 - self.compressed_tokens / self.original_tokens

    def __str__(self) -> str:
        return (
            f"[{self.content_type}] {self.original_tokens} -> "
            f"{self.compressed_tokens} tok ({self.ratio:.0%} saved, "
            f"{self.kept} kept / {self.dropped} dropped)"
        )
