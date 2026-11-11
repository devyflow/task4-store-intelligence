from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from itertools import combinations
from pathlib import Path

import pandas as pd
import yaml
from dateutil import parser as date_parser
from datetime import datetime

EURO_TO_USD = Decimal("1.2")
CENT = Decimal("0.01")
IDENTITY_FIELDS = ("name", "address", "phone", "email")


class UnionFind:
    def __init__(self, size: int):
        self.parent = list(range(size))
        self.rank = [0] * size

    def find(self, value: int) -> int:
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def union(self, left: int, right: int) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root == right_root:
            return
        if self.rank[left_root] < self.rank[right_root]:
            left_root, right_root = right_root, left_root
        self.parent[right_root] = left_root
        if self.rank[left_root] == self.rank[right_root]:
            self.rank[left_root] += 1


def clean_text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value).strip())


def normalize_identity(field: str, value: object) -> str:
    text = clean_text(value).casefold()
    if field == "email":
        return text.replace(" ", "")
    if field == "phone":
        return re.sub(r"\D", "", text)
    return re.sub(r"[^\w]", "", text, flags=re.UNICODE)


def parse_price(value: object) -> Decimal:
    text = clean_text(value).upper().replace(",", "")
    if not text:
        raise ValueError("missing price")
    is_euro = "EUR" in text or "€" in text
    is_usd = "USD" in text or "$" in text
    if is_euro == is_usd:
        raise ValueError(f"ambiguous currency: {value!r}")
    text = re.sub(r"(?<=\d)[^\d.\s](?=\d{1,2}(?:\D|$))", ".", text)
    match = re.search(r"\d+(?:\.\d+)?", text)
    if not match:
        raise ValueError(f"malformed price: {value!r}")
    amount = Decimal(match.group())
    if is_euro:
        amount *= EURO_TO_USD
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def parse_timestamp(value: object) -> pd.Timestamp:
    text = clean_text(value)
    if not text:
        return pd.NaT
    text = text.replace(";", " ").replace(",", " ")
    text = re.sub(r"\b([AP])\s*\.?\s*M\.?", r"\1M", text, flags=re.IGNORECASE)
    try:
        # A complete date must not depend on dateutil's default (today).
        # Comparing different defaults detects omitted year/month/day without
        # rejecting the mixed punctuation and date formats in the source.
        first = date_parser.parse(text, fuzzy=True, default=datetime(1900, 1, 1))
        second = date_parser.parse(text, fuzzy=True, default=datetime(2000, 12, 28))
        if first.date() != second.date():
            return pd.NaT
        return pd.Timestamp(first)
    except (TypeError, ValueError, OverflowError):
        return pd.NaT


def load_books(path: Path) -> pd.DataFrame:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    records = []
    for raw in payload:
        row = {str(key).lstrip(":"): value for key, value in raw.items()}
        records.append(row)
    books = pd.DataFrame(records)
    books["id"] = pd.to_numeric(books["id"], errors="coerce").astype("Int64")
    books["author"] = books["author"].map(clean_text)
    books = books.dropna(subset=["id"]).drop_duplicates(subset=["id"], keep="first")
    books["author_set"] = books["author"].map(
        lambda value: tuple(sorted({part.strip() for part in value.split(",") if part.strip()}))
    )
    books["author_label"] = books["author_set"].map(lambda names: ", ".join(names))
    return books


def reconcile_users(users: pd.DataFrame) -> tuple[pd.DataFrame, dict[int, int]]:
    users = users.drop_duplicates(subset=["id"], keep="first").copy().reset_index(drop=True)
    normalized = {
        field: users[field].map(lambda value, f=field: normalize_identity(f, value))
        for field in IDENTITY_FIELDS
    }
    union_find = UnionFind(len(users))

    # If only one field changed, the same person still shares at least three fields.
    for field_group in combinations(IDENTITY_FIELDS, 3):
        seen: dict[tuple[str, str, str], int] = {}
        for index in range(len(users)):
            key = tuple(normalized[field].iat[index] for field in field_group)
            if not all(key):
                continue
            previous = seen.get(key)
            if previous is None:
                seen[key] = index
            else:
                union_find.union(previous, index)

    roots = [union_find.find(index) for index in range(len(users))]
    root_to_entity: dict[int, int] = {}
    entity_ids: list[int] = []
    for root in roots:
        root_to_entity.setdefault(root, len(root_to_entity))
        entity_ids.append(root_to_entity[root])
    users["entity_id"] = entity_ids
    mapping = dict(zip(users["id"].astype(int), users["entity_id"].astype(int), strict=True))
    return users, mapping


def money(value: float | Decimal) -> str:
    return f"${Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP):,.2f}"


def analyze_store(store_dir: Path) -> dict:
    users_raw = pd.read_csv(store_dir / "users.csv")
    orders_raw = pd.read_parquet(store_dir / "orders.parquet")
    books = load_books(store_dir / "books.yaml")

    users, entity_mapping = reconcile_users(users_raw)
    orders = orders_raw.drop_duplicates(subset=["id"], keep="first").copy()
    orders["quantity"] = pd.to_numeric(orders["quantity"], errors="coerce")
    orders["timestamp_parsed"] = orders["timestamp"].map(parse_timestamp)

    parsed_prices = []
    for value in orders["unit_price"]:
        try:
            parsed_prices.append(parse_price(value))
        except ValueError:
            parsed_prices.append(None)
    orders["unit_price_usd"] = parsed_prices

    valid = (
        orders["quantity"].notna()
        & (orders["quantity"] >= 0)
        & orders["unit_price_usd"].notna()
        & orders["timestamp_parsed"].notna()
        & orders["user_id"].isin(entity_mapping)
        & orders["book_id"].isin(books["id"])
    )
    rejected_orders = int((~valid).sum())
    orders = orders.loc[valid].copy()
    orders["date"] = orders["timestamp_parsed"].dt.strftime("%Y-%m-%d")
    orders["paid_price"] = [
        (Decimal(str(quantity)) * unit_price).quantize(CENT, rounding=ROUND_HALF_UP)
        for quantity, unit_price in zip(orders["quantity"], orders["unit_price_usd"], strict=True)
    ]
    orders["entity_id"] = orders["user_id"].map(entity_mapping)

    daily = (
        orders.assign(paid_price_float=orders["paid_price"].map(float))
        .groupby("date", as_index=False)["paid_price_float"]
        .sum()
        .sort_values("date")
    )
    top_days = daily.nlargest(5, "paid_price_float")

    order_books = orders.merge(
        books[["id", "author_set", "author_label"]],
        left_on="book_id",
        right_on="id",
        how="left",
        suffixes=("", "_book"),
    )
    popular = (
        order_books.groupby(["author_set", "author_label"], as_index=False)["quantity"]
        .sum()
        .sort_values(["quantity", "author_label"], ascending=[False, True])
        .iloc[0]
    )

    customer_spend = (
        orders.assign(paid_price_float=orders["paid_price"].map(float))
        .groupby("entity_id")["paid_price_float"]
        .sum()
    )
    top_entity = int(customer_spend.idxmax())
    top_user_ids = sorted(users.loc[users["entity_id"] == top_entity, "id"].astype(int).tolist())
    top_spend = float(customer_spend.loc[top_entity])

    return {
        "store": store_dir.name,
        "top_days": [
            {"date": row.date, "revenue": round(float(row.paid_price_float), 2)}
            for row in top_days.itertuples(index=False)
        ],
        "unique_users": int(users["entity_id"].nunique()),
        "raw_user_rows": int(len(users_raw)),
        "unique_author_sets": int(books["author_set"].nunique()),
        "popular_authors": str(popular["author_label"]),
        "popular_units": int(popular["quantity"]),
        "best_buyer_ids": top_user_ids,
        "best_buyer_spend": round(top_spend, 2),
        "daily_revenue": [
            {"date": row.date, "revenue": round(float(row.paid_price_float), 2)}
            for row in daily.itertuples(index=False)
        ],
        "quality": {
            "order_rows": int(len(orders_raw)),
            "valid_orders": int(len(orders)),
            "rejected_orders": rejected_orders,
            "duplicate_order_ids": int(orders_raw.duplicated(subset=["id"]).sum()),
            "duplicate_user_ids": int(users_raw.duplicated(subset=["id"]).sum()),
        },
    }


def analyze_all(data_dir: Path) -> list[dict]:
    return [analyze_store(path) for path in sorted(data_dir.glob("STORE*"))]


def write_metrics(data_dir: Path, output_path: Path) -> list[dict]:
    metrics = analyze_all(data_dir)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")
    return metrics


if __name__ == "__main__":
    project = Path(__file__).resolve().parent
    result = write_metrics(project / "data", project / "outputs" / "metrics.json")
    for store in result:
        print(
            store["store"],
            store["unique_users"],
            store["unique_author_sets"],
            store["popular_authors"],
            store["best_buyer_ids"],
        )
