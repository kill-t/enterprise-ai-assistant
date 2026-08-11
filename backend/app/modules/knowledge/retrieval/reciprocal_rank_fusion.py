"""使用 Reciprocal Rank Fusion 合并不同检索渠道的有序 Chunk。"""

from dataclasses import dataclass
from enum import Enum
from uuid import UUID


class RetrievalChannel(str, Enum):
    """标识一个候选榜单来自哪种检索方式。"""

    VECTOR = "vector"
    LEXICAL = "lexical"


@dataclass(frozen=True, slots=True)
class RankedRetrievalList:
    """保存一个渠道按相关性从高到低排列的 Chunk ID。"""

    channel: RetrievalChannel
    chunk_ids: tuple[UUID, ...]

    def __post_init__(self) -> None:
        """禁止同一 Chunk 在一个榜单重复出现并获得多次贡献。"""
        if len(self.chunk_ids) != len(set(self.chunk_ids)):
            raise ValueError("同一检索榜单不能包含重复 Chunk")


@dataclass(frozen=True, slots=True)
class RrfRankContribution:
    """记录一个渠道的名次及其对最终 RRF 分数的贡献。"""

    channel: RetrievalChannel
    rank: int
    reciprocal_score: float


@dataclass(frozen=True, slots=True)
class RrfFusedCandidate:
    """保存一个 Chunk 的融合分数以及可解释的渠道贡献。"""

    chunk_id: UUID
    fused_score: float
    contributions: tuple[RrfRankContribution, ...]


class ReciprocalRankFusion:
    """只依据不同渠道的排名合并候选，避免直接比较原始分数。"""

    def __init__(self, *, rank_constant: int = 60) -> None:
        """保存平滑常数；常数越大，不同相邻名次的差距越小。"""
        if rank_constant <= 0:
            raise ValueError("RRF rank_constant 必须大于零")
        self.rank_constant = rank_constant

    def fuse(
        self,
        rankings: tuple[RankedRetrievalList, ...],
        *,
        limit: int,
    ) -> tuple[RrfFusedCandidate, ...]:
        """累加各渠道名次贡献，并用首次出现顺序稳定处理同分候选。"""
        if not 1 <= limit <= 200:
            raise ValueError("RRF limit 必须在 1 到 200 之间")
        channels = [ranking.channel for ranking in rankings]
        if len(channels) != len(set(channels)):
            raise ValueError("RRF 不能包含重复检索渠道")

        scores: dict[UUID, float] = {}
        contributions: dict[UUID, list[RrfRankContribution]] = {}
        first_seen: dict[UUID, int] = {}
        next_position = 0
        for ranking in rankings:
            for rank, chunk_id in enumerate(ranking.chunk_ids, start=1):
                if chunk_id not in first_seen:
                    first_seen[chunk_id] = next_position
                    next_position += 1
                reciprocal_score = 1 / (self.rank_constant + rank)
                scores[chunk_id] = scores.get(chunk_id, 0.0) + reciprocal_score
                contributions.setdefault(chunk_id, []).append(
                    RrfRankContribution(
                        channel=ranking.channel,
                        rank=rank,
                        reciprocal_score=reciprocal_score,
                    )
                )

        ordered_ids = sorted(
            scores,
            key=lambda chunk_id: (-scores[chunk_id], first_seen[chunk_id]),
        )[:limit]
        return tuple(
            RrfFusedCandidate(
                chunk_id=chunk_id,
                fused_score=scores[chunk_id],
                contributions=tuple(contributions[chunk_id]),
            )
            for chunk_id in ordered_ids
        )
