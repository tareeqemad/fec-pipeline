"""Union-find over donor profiles and the donor_key of each cluster."""
import hashlib


class UnionFind:
    """Disjoint-set with path compression and union by rank - the cluster engine."""

    def __init__(self):
        self.parent = {}
        self.rank = {}

    def find(self, x):
        if x not in self.parent:
            self.parent[x] = x
            self.rank[x] = 0
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        if self.rank[ra] == self.rank[rb]:
            self.rank[ra] += 1
        return True


def _donor_keys(components: dict) -> dict:
    rid_to_key = {}
    for root, members in components.items():
        key = hashlib.sha256(root.encode()).hexdigest()[:12]
        for rid in members:
            rid_to_key[rid] = key
    return rid_to_key


def _has_signal(row: dict, prefix: str) -> bool:
    """Return whether an audit row contains a scoring signal with this prefix."""
    return any(
        signal.strip().startswith(prefix) for signal in row["signals"].split(";")
    )
