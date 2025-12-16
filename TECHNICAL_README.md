# Technical Documentation: Commons Depicts Explorer

This document provides a detailed technical overview of the **Commons Depicts Explorer**, a Flask-based web application for searching Wikimedia Commons images by category and managing their "depicts" (P180) statements via Wikidata.

## Architecture Overview

The application is built using a **Flask (Python)** backend and a **Vanilla JavaScript/Bootstrap** frontend. It acts as a specialized client for the Wikimedia Commons and Wikidata APIs.

### Tech Stack
-   **Backend**: Python, Flask, `requests` (for MediaWiki API), `Flask-SQLAlchemy` (local DB stub), `Pillow` (image processing).
-   **Frontend**: HTML5, CSS3, Bootstrap 5, Vanilla JS.
-   **APIs**: Wikimedia Commons API (`/w/api.php`), Wikidata API.

## Core Components

### 1. Backend (`app.py`)

The backend serves the frontend and proxies requests to Wikimedia APIs to handle authentication and batch processing.

#### Search & Pagination Logic (`/api/search`)
-   **Category Traversal**: The app takes a search query (category name) and fetches files using the `generator=categorymembers` API parameters.
-   **Recursion**: It supports depth-1 recursion into subcategories if the main category yields fewer than 10 results.
-   **Uniqueness**: A Python `set` (`seen_pageids`) is used to strictly enforce that no duplicate images (same Page ID) appear in the results, even when traversing subcategories.
-   **Pagination**:
    -   Accepts a `page` parameter.
    -   Stateless pagination implementation: It fetches enough items from the start (`target = page * 10`) to satisfy the requested offset and then slices the result list `[start_idx : end_idx]`.
    -   This design avoids the complexity of MediaWiki continuation tokens for this specific "hybrid category + subcategory" search use case.

#### Depicts Data Enrichment (Structured Data on Commons)
To minimize API latency, "depicts" data is fetched in **batches** only for the 10 displayed results:
1.  **Extract M-IDs**: Converts Page IDs to MediaInfo IDs (e.g., `M12345`).
2.  **Batch SDC Fetch**: Calls `wbgetentities` on Commons to get "depicts" (P180) claims for all M-IDs in one request.
3.  **Batch Label Resolve**: Extracts all QIDs (Wikidata items) from the claims and performs a second `wbgetentities` call to Wikidata to fetch their English labels in bulk.
4.  **Merge**: The labels are mapped back to the images before sending the JSON response to the frontend.

#### Image Uploads & Optional Dependencies
-   **Pillow (PIL)**: Used to optimize images (convert to WebP) and generate thumbnails.
-   **Resilience**: The app includes a runtime check for Pillow. If the library is missing (e.g., due to MinGW build issues on Windows), the app disables upload functionality gracefully but keeps search fully operational.

### 2. Frontend (`script.js` & `index.html`)

-   **Masonry Layout**: Uses CSS Flexbox/Grid to display images.
-   **Load More**: Implements an "infinite scroll" style manual trigger. It tracks `currentPage` and appends new HTML elements to the grid without resetting the view.
-   **Split View**: A master-detail view where selecting an image opens a focused overlay on the right, loading full SDC data instantly.
-   **Wikidata Autocomplete**: When adding a new tag, the app queries the `wbsearchentities` action on Wikidata to provide real-time suggestions (debounced).

## API Endpoints

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/api/search` | Searches categories. Params: `q` (query), `page`. Returns files + depicts badges. |
| `GET` | `/api/file/<pageid>` | Fetches detailed SDC data for a single file. |
| `GET` | `/api/wikidata_search` | Proxy for Wikidata entity search (autocomplete). |
| `POST` | `/api/add_claim` | Adds a P180 statement. Requires bot credentials. |
| `POST` | `/api/upload` | Uploads and processes an image (Local gallery mode). |

## Authentication & Configuration

The application requires a `.env` file for write operations (adding depicts statements):
```bash
BOT_USERNAME=MyBot
BOT_PASSWORD=MyBotPassword
FLASK_SECRET_KEY=...
```
It uses `logintoken` and `csrftoken` flows from the MediaWiki API to authenticate edit requests.

## Database
A local SQLite database (`images.db`) is initialized for the local gallery feature but is strictly secondary to the live Wikimedia Commons integration.
