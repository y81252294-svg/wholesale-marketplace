from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.preprocessing import OneHotEncoder, StandardScaler


class HybridRecommender:
    def __init__(self, alpha: float = 0.5, model_version: str = "hybrid-v1"):
        if not 0 <= alpha <= 1:
            raise ValueError("alpha must be in [0, 1]")
        self.alpha = alpha
        self.model_version = model_version

    def fit(
        self,
        products: pd.DataFrame,
        purchases: pd.DataFrame,
        retailer_categories: dict[int, list[int]] | None = None,
    ):
        self.products = products.drop_duplicates("product_id").reset_index(drop=True).copy()
        self.retailer_categories = retailer_categories or {}
        product_ids = self.products["product_id"].tolist()
        self.product_index = {product_id: i for i, product_id in enumerate(product_ids)}
        category = self.products["category_id"].fillna(-1).astype(str).to_numpy().reshape(-1, 1)
        brand = self.products.get("brand", pd.Series("", index=self.products.index)).fillna("").astype(str).to_numpy().reshape(-1, 1)
        encoder = OneHotEncoder(handle_unknown="ignore")
        categorical = encoder.fit_transform(np.hstack([category, brand]))
        price = self.products["unit_price"].fillna(self.products["unit_price"].median()).to_numpy().reshape(-1, 1)
        price_scaled = StandardScaler().fit_transform(price)
        bands = pd.qcut(self.products["unit_price"].rank(method="first"), q=min(5, len(self.products)), labels=False).astype(str).to_numpy().reshape(-1, 1)
        price_band = OneHotEncoder(handle_unknown="ignore").fit_transform(bands)
        names = self.products["name"].fillna("").astype(str).tolist()
        text = TfidfVectorizer(ngram_range=(1, 2), min_df=1).fit_transform(names)
        from scipy.sparse import csr_matrix, hstack

        self.content_vectors = hstack([categorical, csr_matrix(price_scaled), price_band, text]).tocsr()
        self.purchase_matrix = (
            purchases.pivot_table(index="retailer_id", columns="product_id", values="quantity", aggfunc="sum", fill_value=0)
            if not purchases.empty
            else pd.DataFrame()
        )
        aligned = self.purchase_matrix.reindex(columns=product_ids, fill_value=0)
        self.item_similarity = cosine_similarity(aligned.T) if not aligned.empty else np.zeros((len(product_ids), len(product_ids)))
        np.fill_diagonal(self.item_similarity, 0)
        neighbor_limit = min(20, max(0, len(product_ids) - 1))
        if neighbor_limit:
            for row_index, similarities in enumerate(self.item_similarity):
                keep = np.argpartition(similarities, -neighbor_limit)[-neighbor_limit:]
                mask = np.ones(len(product_ids), dtype=bool)
                mask[keep] = False
                self.item_similarity[row_index, mask] = 0
        self.popularity = (
            purchases.groupby("product_id")["quantity"].sum().reindex(product_ids, fill_value=0).to_numpy(dtype=float)
            if not purchases.empty
            else np.zeros(len(product_ids))
        )
        return self

    @staticmethod
    def _minmax(values):
        values = np.asarray(values, dtype=float)
        if values.size == 0:
            return values
        low, high = values.min(), values.max()
        return np.zeros_like(values) if high == low else (values - low) / (high - low)

    def recommend(self, retailer_id: int, k: int = 10):
        if k < 1:
            raise ValueError("k must be positive")
        if not hasattr(self, "products"):
            raise RuntimeError("fit must be called before recommend")
        if retailer_id in self.purchase_matrix.index:
            history = self.purchase_matrix.loc[retailer_id].reindex(self.products.product_id, fill_value=0).to_numpy(dtype=float)
        else:
            history = np.zeros(len(self.products), dtype=float)
        seen = history > 0
        if seen.any():
            profile = np.asarray(
                self.content_vectors[seen].multiply(history[seen, None]).sum(axis=0)
            )
            content_scores = cosine_similarity(
                self.content_vectors,
                profile,
            ).ravel()
        else:
            selected = set(self.retailer_categories.get(retailer_id, []))
            content_scores = self.products["category_id"].isin(selected).to_numpy(dtype=float)
            if not selected:
                content_scores = self.popularity.copy()
        cf_scores = history @ self.item_similarity if seen.any() else np.zeros(len(history))
        cb_norm = self._minmax(content_scores)
        cf_norm = self._minmax(cf_scores)
        scores = self.alpha * cb_norm + (1 - self.alpha) * cf_norm
        cold = not seen.any()
        if cold and not self.retailer_categories.get(retailer_id):
            scores = self._minmax(self.popularity)
        scores[seen] = -np.inf
        ordered = np.argsort(-scores)[:k]
        return [
            {
                "product_id": int(self.products.iloc[index].product_id),
                "score": float(scores[index]),
                "model_version": self.model_version,
            }
            for index in ordered
            if np.isfinite(scores[index])
        ]


def choose_alpha(products, train_purchases, validation_purchases, retailer_categories=None, candidates=None):
    if candidates is None:
        candidates = np.linspace(0, 1, 11)
    validation_truth = validation_purchases.groupby("retailer_id")["product_id"].apply(set).to_dict()
    best_alpha, best_score = 0.5, -1.0
    for alpha in candidates:
        model = HybridRecommender(float(alpha)).fit(products, train_purchases, retailer_categories)
        hits = total = 0
        for retailer, truth in validation_truth.items():
            predicted = {row["product_id"] for row in model.recommend(int(retailer), 10)}
            hits += len(predicted & truth)
            total += len(truth)
        score = hits / total if total else 0.0
        if score > best_score:
            best_alpha, best_score = float(alpha), score
    return best_alpha


def ranking_metrics(actual: set[int], predicted: list[int], k: int):
    selected = predicted[:k]
    hits = sum(item in actual for item in selected)
    precision = hits / k if k else 0.0
    recall = hits / len(actual) if actual else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    dcg = sum(1 / np.log2(rank + 2) for rank, item in enumerate(selected) if item in actual)
    ideal = sum(1 / np.log2(rank + 2) for rank in range(min(len(actual), k)))
    return {
        "precision_at_k": precision,
        "recall_at_k": recall,
        "f1_at_k": f1,
        "ndcg_at_k": dcg / ideal if ideal else 0.0,
    }
