from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pandas as pd

from analysis import (
    clean_text,
    load_books,
    parse_price,
    parse_timestamp,
    reconcile_users,
)

SCHEMA = """
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS books;
DROP TABLE IF EXISTS users;

CREATE TABLE users (
    store TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    entity_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    address TEXT NOT NULL,
    phone TEXT NOT NULL,
    email TEXT NOT NULL,
    PRIMARY KEY (store, user_id)
);

CREATE TABLE books (
    store TEXT NOT NULL,
    book_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    authors TEXT NOT NULL,
    title_has_person_name INTEGER NOT NULL CHECK (title_has_person_name IN (0, 1)),
    PRIMARY KEY (store, book_id)
);

CREATE TABLE orders (
    store TEXT NOT NULL,
    order_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    entity_id INTEGER NOT NULL,
    book_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL,
    unit_price_usd REAL NOT NULL,
    paid_price REAL NOT NULL,
    order_date TEXT NOT NULL,
    PRIMARY KEY (store, order_id)
);

CREATE INDEX idx_users_store_entity ON users(store, entity_id);
CREATE INDEX idx_users_store_email ON users(store, email);
CREATE INDEX idx_books_store_title ON books(store, title);
CREATE INDEX idx_orders_store_date ON orders(store, order_date);
CREATE INDEX idx_orders_store_entity ON orders(store, entity_id);
"""

NAME_STOPWORDS = {
    "miss", "mrs", "mr", "ms", "dr", "prof", "rev", "rep", "sen", "gov",
    "pres", "amb", "adm", "hon", "esq", "jr", "sr", "ii", "iii", "iv", "v",
    "may", "will", "hope", "faith", "rose", "summer", "winter", "king", "queen",
}


def name_tokens(users: pd.DataFrame) -> set[str]:
    tokens: set[str] = set()
    for name in users["name"]:
        for token in clean_text(name).replace("'", " ").replace("-", " ").split():
            normalized = "".join(char for char in token.casefold() if char.isalpha())
            if len(normalized) >= 4 and normalized not in NAME_STOPWORDS:
                tokens.add(normalized)
    return tokens


def title_has_person_name(title: str, people_tokens: set[str]) -> bool:
    title_tokens = {
        "".join(char for char in token.casefold() if char.isalpha())
        for token in clean_text(title).replace("'", " ").replace("-", " ").split()
    }
    return bool((title_tokens - NAME_STOPWORDS) & people_tokens)


def prepare_store(store_dir: Path) -> tuple[list[tuple], list[tuple], list[tuple]]:
    store = store_dir.name
    users_raw = pd.read_csv(store_dir / "users.csv")
    users, mapping = reconcile_users(users_raw)
    user_rows = [
        (
            store,
            int(row.id),
            int(row.entity_id),
            clean_text(row.name),
            clean_text(row.address),
            clean_text(row.phone),
            clean_text(row.email),
        )
        for row in users.itertuples(index=False)
    ]

    books = load_books(store_dir / "books.yaml")
    people_tokens = name_tokens(users)
    book_rows = [
        (
            store,
            int(row.id),
            clean_text(row.title),
            clean_text(row.author_label),
            int(title_has_person_name(row.title, people_tokens)),
        )
        for row in books.itertuples(index=False)
    ]
    valid_book_ids = {row[1] for row in book_rows}

    orders_raw = pd.read_parquet(store_dir / "orders.parquet").drop_duplicates("id")
    order_rows = []
    for row in orders_raw.itertuples(index=False):
        try:
            quantity = int(row.quantity)
            unit_price = parse_price(row.unit_price)
            timestamp = parse_timestamp(row.timestamp)
        except (TypeError, ValueError):
            continue
        if (
            quantity < 0
            or pd.isna(timestamp)
            or int(row.user_id) not in mapping
            or int(row.book_id) not in valid_book_ids
        ):
            continue
        paid = unit_price * quantity
        order_rows.append(
            (
                store,
                int(row.id),
                int(row.user_id),
                int(mapping[int(row.user_id)]),
                int(row.book_id),
                quantity,
                float(unit_price),
                float(paid),
                timestamp.strftime("%Y-%m-%d"),
            )
        )
    return user_rows, book_rows, order_rows


def build(data_dir: Path, database: Path) -> None:
    database.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database)) as connection:
        connection.executescript(SCHEMA)
        for store_dir in sorted(data_dir.glob("STORE*")):
            users, books, orders = prepare_store(store_dir)
            connection.executemany("INSERT INTO users VALUES (?, ?, ?, ?, ?, ?, ?)", users)
            connection.executemany("INSERT INTO books VALUES (?, ?, ?, ?, ?)", books)
            connection.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", orders)
        connection.commit()


if __name__ == "__main__":
    project = Path(__file__).resolve().parent
    target = project / "analytics.sqlite3"
    build(project / "data", target)
    with closing(sqlite3.connect(target)) as connection:
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("users", "books", "orders")
        }
    print(counts)
