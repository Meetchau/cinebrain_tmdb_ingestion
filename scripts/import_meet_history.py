import json
import os
import re
from pathlib import Path

import requests
from dotenv import load_dotenv


load_dotenv()

TMDB_TOKEN = os.getenv("TMDB_API_TOKEN")

if not TMDB_TOKEN:
    raise RuntimeError(
        "TMDB_API_TOKEN not found. Create a .env file and add your "
        "TMDB API Read Access Token."
    )

BASE_URL = "https://api.themoviedb.org/3"

HEADERS = {
    "Authorization": f"Bearer {TMDB_TOKEN}",
    "accept": "application/json",
}

OUTPUT_FILE = Path(
    "data/processed/meet_movie_history.json"
)

WATCHED_MOVIES = [
    "Pyaasa",
    "Kaagaz Ke Phool",
    "Gangs of Wasseypur",
    "The Wolf of Wall Street",
    "The Handmaiden",
    "The Silence of the Lambs",
    "Pulp Fiction",
]


def normalize_title(title):
    title = title.lower().strip()
    title = re.sub(r"[^a-z0-9]+", " ", title)
    return re.sub(r"\s+", " ", title).strip()


def search_movie(title):
    response = requests.get(
        f"{BASE_URL}/search/movie",
        headers=HEADERS,
        params={
            "query": title,
            "include_adult": "false",
            "language": "en-US",
            "page": 1,
        },
        timeout=30,
    )

    response.raise_for_status()
    return response.json().get("results", [])


def choose_best_result(query, results):
    if not results:
        return None

    normalized_query = normalize_title(query)

    exact = [
        result
        for result in results
        if normalize_title(result.get("title", "")) == normalized_query
        or normalize_title(
            result.get("original_title", "")
        ) == normalized_query
    ]

    if exact:
        return exact[0]

    # Search results are already relevance-ranked by TMDB.
    return results[0]


def main():
    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    records = []

    print("\nResolving Meet's watched movies against TMDB...\n")

    for title in WATCHED_MOVIES:
        results = search_movie(title)
        result = choose_best_result(title, results)

        if result is None:
            print(f"[NOT FOUND] {title}")
            continue

        record = {
            "tmdb_id": result.get("id"),
            "title": result.get("title"),
            "original_title": result.get(
                "original_title"
            ),
            "release_date": result.get(
                "release_date"
            ),
            "year": (
                result.get("release_date", "")[:4]
                if result.get("release_date")
                else None
            ),
            "watched": True,
            "rating": None,
            "liked": None,
            "date_watched": None,
            "notes": None,
            "source": "manual_seed",
        }

        records.append(record)

        print(
            f"[OK] {title} -> "
            f"{record['title']} "
            f"({record['year']}) "
            f"[TMDB {record['tmdb_id']}]"
        )

    with OUTPUT_FILE.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            records,
            file,
            indent=2,
            ensure_ascii=False,
        )

    print(
        f"\nSaved {len(records)} watched movies to:"
        f"\n{OUTPUT_FILE}"
    )


if __name__ == "__main__":
    main()
