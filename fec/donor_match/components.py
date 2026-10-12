"""Union-find clustering and donor_key hashing."""
import hashlib


class UnionFind:
    """Disjoint-set with path compression, union by rank."""

    # init empty parent/rank maps for union-find
    def __init__(self):
        self.parent = {}
        self.rank = {}

    # find x's cluster root, compressing the path
    def find(self, x):
        if x not in self.parent:
            self.parent[x] = x
            self.rank[x] = 0
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    # merge two clusters; false if already joined
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


# stable donor_key hash for each cluster member
def donor_keys(components: dict) -> dict:
    rid_to_key = {}
    for root, members in components.items():
        key = hashlib.sha256(root.encode()).hexdigest()[:12]
        for rid in members:
            rid_to_key[rid] = key
    return rid_to_key


# true if any audit signal has prefix
def has_signal(row: dict, prefix: str) -> bool:
    return any(
        signal.strip().startswith(prefix) for signal in row["signals"].split(";")
    )
