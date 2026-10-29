from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

STORES = ("STORE1", "STORE2", "STORE3", "STORE4", "STORE5")


class DataAPI:
    def __init__(self, database: Path):
        self.database = database

    def _query(self, sql: str, params: list[Any] | tuple[Any, ...] = ()) -> list[dict]:
        with closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute(sql, params).fetchall()]

    @staticmethod
    def _store(store: str) -> str:
        value = store.upper()
        if value not in STORES:
            raise ValueError(f"Unknown store: {store}")
        return value

    def overview(self, store: str) -> dict:
        store = self._store(store)
        unique_users = self._query(
            "SELECT COUNT(DISTINCT entity_id) AS value FROM users WHERE store = ?", [store]
        )[0]["value"]
        author_sets = self._query(
            "SELECT COUNT(DISTINCT authors) AS value FROM books WHERE store = ?", [store]
        )[0]["value"]
        top_days = self._query(
            """
            SELECT order_date AS date, ROUND(SUM(paid_price), 2) AS revenue
            FROM orders WHERE store = ? GROUP BY order_date
            ORDER BY revenue DESC, date ASC LIMIT 5
            """,
            [store],
        )
        popular = self._query(
            """
            SELECT b.authors, SUM(o.quantity) AS units
            FROM orders o JOIN books b ON b.store = o.store AND b.book_id = o.book_id
            WHERE o.store = ? GROUP BY b.authors
            ORDER BY units DESC, b.authors ASC LIMIT 1
            """,
            [store],
        )[0]
        best = self._query(
            """
            SELECT entity_id, ROUND(SUM(paid_price), 2) AS spend
            FROM orders WHERE store = ? GROUP BY entity_id
            ORDER BY spend DESC, entity_id ASC LIMIT 1
            """,
            [store],
        )[0]
        aliases = self._query(
            "SELECT user_id FROM users WHERE store = ? AND entity_id = ? ORDER BY user_id",
            [store, best["entity_id"]],
        )
        return {
            "store": store,
            "top_days": top_days,
            "unique_users": unique_users,
            "unique_author_sets": author_sets,
            "popular_authors": popular["authors"],
            "popular_units": popular["units"],
            "best_buyer_ids": [row["user_id"] for row in aliases],
            "best_buyer_spend": best["spend"],
        }

    def users(
        self,
        store: str,
        email_prefixes: list[str] | None,
        name_contains: str | None,
        limit: int,
    ) -> dict:
        store = self._store(store)
        where = ["store = ?"]
        params: list[Any] = [store]
        prefixes = [prefix.casefold().strip() for prefix in (email_prefixes or []) if prefix.strip()]
        if prefixes:
            where.append("(" + " OR ".join("LOWER(email) LIKE ?" for _ in prefixes) + ")")
            params.extend(f"{prefix}%" for prefix in prefixes)
        if name_contains:
            where.append("LOWER(name) LIKE ?")
            params.append(f"%{name_contains.casefold()}%")
        clause = " AND ".join(where)
        count = self._query(
            f"SELECT COUNT(*) AS rows, COUNT(DISTINCT entity_id) AS people FROM users WHERE {clause}",
            params,
        )[0]
        rows = self._query(
            f"SELECT user_id, entity_id, name, email FROM users WHERE {clause} ORDER BY user_id LIMIT ?",
            [*params, max(1, min(limit, 100))],
        )
        return {"store": store, "matched_rows": count["rows"], "unique_people": count["people"], "rows": rows}

    def books(
        self,
        store: str,
        title_contains: str | None,
        author_contains: str | None,
        person_name_filter: str,
        limit: int,
    ) -> dict:
        store = self._store(store)
        where = ["store = ?"]
        params: list[Any] = [store]
        if title_contains:
            where.append("LOWER(title) LIKE ?")
            params.append(f"%{title_contains.casefold()}%")
        if author_contains:
            where.append("LOWER(authors) LIKE ?")
            params.append(f"%{author_contains.casefold()}%")
        if person_name_filter == "include":
            where.append("title_has_person_name = 1")
        elif person_name_filter == "exclude":
            where.append("title_has_person_name = 0")
        elif person_name_filter != "any":
            raise ValueError("person_name_filter must be any, include, or exclude")
        clause = " AND ".join(where)
        count = self._query(f"SELECT COUNT(*) AS value FROM books WHERE {clause}", params)[0]["value"]
        rows = self._query(
            f"""
            SELECT book_id, title, authors, title_has_person_name
            FROM books WHERE {clause} ORDER BY title, book_id LIMIT ?
            """,
            [*params, max(1, min(limit, 100))],
        )
        return {"store": store, "matched_books": count, "rows": rows}

    def revenue(
        self,
        store: str,
        start_date: str | None,
        end_date: str | None,
        group_by: str,
        limit: int,
    ) -> dict:
        store = self._store(store)
        where = ["store = ?"]
        params: list[Any] = [store]
        if start_date:
            where.append("order_date >= ?")
            params.append(start_date)
        if end_date:
            where.append("order_date <= ?")
            params.append(end_date)
        if group_by not in {"day", "month", "total"}:
            raise ValueError("group_by must be day, month, or total")
        period = {"day": "order_date", "month": "substr(order_date, 1, 7)", "total": "'all'"}[group_by]
        clause = " AND ".join(where)
        rows = self._query(
            f"""
            SELECT {period} AS period, ROUND(SUM(paid_price), 2) AS revenue,
                   SUM(quantity) AS units, COUNT(*) AS orders
            FROM orders WHERE {clause} GROUP BY period
            ORDER BY period LIMIT ?
            """,
            [*params, max(1, min(limit, 400))],
        )
        return {"store": store, "group_by": group_by, "rows": rows}

    def dashboard_action(
        self,
        store: str,
        view: str,
        person_name_filter: str,
        email_prefixes: list[str],
        start_date: str | None,
        end_date: str | None,
        search: str | None,
    ) -> dict:
        store = self._store(store)
        if view not in {"overview", "books", "users", "revenue"}:
            raise ValueError("Unsupported dashboard view")
        if person_name_filter not in {"any", "include", "exclude"}:
            raise ValueError("Unsupported person-name filter")
        return {
            "type": "dashboard_update",
            "store": store,
            "view": view,
            "filters": {
                "person_name_filter": person_name_filter,
                "email_prefixes": email_prefixes,
                "start_date": start_date,
                "end_date": end_date,
                "search": search,
            },
        }
