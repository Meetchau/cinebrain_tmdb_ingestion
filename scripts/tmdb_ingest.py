import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
from tqdm import tqdm


load_dotenv()

TMDB_TOKEN = os.getenv("TMDB_API_TOKEN")

if not TMDB_TOKEN:
    raise RuntimeError(
        "TMDB_API_TOKEN not found. Create a .env file and add your "
        "TMDB API Read Access Token."
    )

BASE_URL = "https://api.themoviedb.org/3"

OUTPUT_DIR = Path("data/raw")
OUTPUT_FILE = OUTPUT_DIR / "tmdb_movies.jsonl"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "Authorization": f"Bearer {TMDB_TOKEN}",
    "accept": "application/json",
}

# Start small and increase after verifying the pipeline.
MAX_PAGES = 10

# Small delay between requests. TMDB may impose its own limits.
REQUEST_DELAY = 0.15

session = requests.Session()
session.headers.update(HEADERS)


def tmdb_get(endpoint, params=None, retries=3):
    """GET a TMDB endpoint with retry and rate-limit handling."""
    url = f"{BASE_URL}{endpoint}"

    for attempt in range(retries):
        try:
            response = session.get(
                url,
                params=params,
                timeout=30,
            )

            if response.status_code == 429:
                retry_after = int(
                    response.headers.get("Retry-After", "2")
                )
                print(f"Rate limited. Waiting {retry_after}s...")
                time.sleep(retry_after)
                continue

            response.raise_for_status()
            time.sleep(REQUEST_DELAY)
            return response.json()

        except requests.RequestException as exc:
            if attempt == retries - 1:
                raise

            wait = 2 ** attempt
            print(
                f"Request failed: {exc}. "
                f"Retrying in {wait}s..."
            )
            time.sleep(wait)

    raise RuntimeError(f"TMDB request failed: {endpoint}")


def load_existing_ids():
    """Load TMDB IDs already stored so ingestion can resume."""
    existing_ids = set()

    if not OUTPUT_FILE.exists():
        return existing_ids

    with OUTPUT_FILE.open("r", encoding="utf-8") as file:
        for line in file:
            try:
                movie = json.loads(line)
                if movie.get("tmdb_id"):
                    existing_ids.add(movie["tmdb_id"])
            except json.JSONDecodeError:
                continue

    return existing_ids


def discover_movies(page):
    """Discover broadly popular movies for the initial catalogue."""
    params = {
        "language": "en-US",
        "sort_by": "popularity.desc",
        "include_adult": "false",
        "include_video": "false",
        "page": page,
    }

    return tmdb_get("/discover/movie", params=params)


def get_movie_details(movie_id):
    return tmdb_get(
        f"/movie/{movie_id}",
        params={"language": "en-US"},
    )


def get_movie_credits(movie_id):
    return tmdb_get(
        f"/movie/{movie_id}/credits",
        params={"language": "en-US"},
    )


def get_movie_keywords(movie_id):
    return tmdb_get(f"/movie/{movie_id}/keywords")


def extract_directors(credits):
    return [
        {
            "id": person.get("id"),
            "name": person.get("name"),
        }
        for person in credits.get("crew", [])
        if person.get("job") == "Director"
    ]


def extract_writers(credits):
    return [
        {
            "id": person.get("id"),
            "name": person.get("name"),
            "job": person.get("job"),
        }
        for person in credits.get("crew", [])
        if person.get("department") == "Writing"
    ]


def extract_cast(credits, limit=20):
    return [
        {
            "id": person.get("id"),
            "name": person.get("name"),
            "character": person.get("character"),
            "order": person.get("order"),
        }
        for person in credits.get("cast", [])[:limit]
    ]


def build_movie_record(movie, credits, keywords):
    return {
        "tmdb_id": movie.get("id"),
        "title": movie.get("title"),
        "original_title": movie.get("original_title"),
        "release_date": movie.get("release_date"),
        "year": (
            movie.get("release_date", "")[:4]
            if movie.get("release_date")
            else None
        ),
        "original_language": movie.get("original_language"),
        "origin_country": movie.get("origin_country", []),
        "overview": movie.get("overview"),
        "runtime": movie.get("runtime"),
        "genres": [
            {
                "id": genre.get("id"),
                "name": genre.get("name"),
            }
            for genre in movie.get("genres", [])
        ],
        "keywords": [
            {
                "id": keyword.get("id"),
                "name": keyword.get("name"),
            }
            for keyword in keywords.get("keywords", [])
        ],
        "directors": extract_directors(credits),
        "writers": extract_writers(credits),
        "cast": extract_cast(credits),
        "production_companies": [
            {
                "id": company.get("id"),
                "name": company.get("name"),
            }
            for company in movie.get("production_companies", [])
        ],
        "production_countries": [
            country.get("iso_3166_1")
            for country in movie.get("production_countries", [])
        ],
        "spoken_languages": [
            language.get("iso_639_1")
            for language in movie.get("spoken_languages", [])
        ],
        "popularity": movie.get("popularity"),
        "vote_average": movie.get("vote_average"),
        "vote_count": movie.get("vote_count"),
        "poster_path": movie.get("poster_path"),
        "backdrop_path": movie.get("backdrop_path"),
        "adult": movie.get("adult"),
        "homepage": movie.get("homepage"),
    }


def main():
    print("\n====================================")
    print("       CINEBRAIN TMDB INGESTION")
    print("====================================\n")

    existing_ids = load_existing_ids()

    print(f"Existing movies: {len(existing_ids)}")

    total_added = 0

    for page in range(1, MAX_PAGES + 1):
        print(f"\nDiscovering page {page}/{MAX_PAGES}...")

        data = discover_movies(page)
        results = data.get("results", [])

        if not results:
            print("No more movies found.")
            break

        for movie_summary in tqdm(results, desc=f"Page {page}"):
            movie_id = movie_summary.get("id")

            if not movie_id or movie_id in existing_ids:
                continue

            try:
                details = get_movie_details(movie_id)
                credits = get_movie_credits(movie_id)
                keywords = get_movie_keywords(movie_id)

                record = build_movie_record(
                    details,
                    credits,
                    keywords,
                )

                with OUTPUT_FILE.open(
                    "a",
                    encoding="utf-8",
                ) as file:
                    file.write(
                        json.dumps(
                            record,
                            ensure_ascii=False,
                        )
                        + "\n"
                    )

                existing_ids.add(movie_id)
                total_added += 1

            except Exception as exc:
                print(
                    f"\nFailed movie {movie_id}: {exc}"
                )

    print("\n====================================")
    print("             COMPLETE")
    print("====================================")
    print(f"Movies added: {total_added}")
    print(f"Total movies: {len(existing_ids)}")
    print(f"Output: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
