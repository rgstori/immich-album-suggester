# Architecture and Design Decisions: Immich Album Suggester

**Version: 1.0**
**Last Updated: 2025-08-11**
 
## 1. Project Overview

This document outlines the architecture for the Immich Album Suggester, a system designed to intelligently analyze a personal photo library hosted on Immich.


The primary goals are to:
1.  **Cluster photos** into meaningful events using a combination of time, location, and visual similarity (via CLIP embeddings).
2.  **Enrich clusters** with real-world context like date and location names.
3.  **Use a Vision Language Model (VLM)** to analyze events, suggest a descriptive title, and identify highlight photos.
4.  **Provide a Web UI** for users to review, manage, and approve these suggestions.
5.  **Create final albums** in Immich via its official API upon user approval.

This document serves as the primary guide for development, maintenance, and future enhancements. The target audience includes human developers and AI assistants.

```mermaid
graph TD
    subgraph "User Interface (Docker on NAS)"
        UI[ui.py <br/>(Streamlit)]
    end

    subgraph "Backend Engine (Docker on NAS)"
        A[main.py <br/>(Scan Script)]
        DB_S[suggestions.db <br/>(SQLite)]
    end

    subgraph "External Systems"
        DB_I[Immich <br/>PostgreSQL DB]
        API_I[Immich API]
        VLM[VLM Service (Ollama on PC)]
    end

    UI -- "1. Start Scan" --- A;
    A -- "Sets 'scanning' status" --> DB_S;
    
    A -- "2. Fetch Exclusions" --> DB_S;
    A -- "3. Fetch Assets & Embeddings" --> DB_I;
    A -- "4. Perform Clustering & Analysis" --> A;

    subgraph "User Interface (Streamlit)"
        UI[Web UI]
    end

    subgraph "Backend Services (Docker on NAS)"
        A[main.py Script]
        DB_S[suggestions.db <br/>(SQLite)]
    end

    subgraph "External Systems"
        DB_I[Immich PostgreSQL DB]
        API_I[Immich API]
        VLM[VLM Service (Ollama on PC)]
    end

    UI -- "1. Start Scan (Updates Status)" --> A;
    A -- "2. Fetch Exclusions" --> DB_S;
    A -- "3. Fetch Assets & Embeddings" --> DB_I;
    A -- "4. Perform Clustering & Analysis" --> A;
    A -- "5. Request VLM Inference" --> VLM;
    VLM -- "6. Return JSON Analysis" --> A;
    A -- "7. Store Pending Suggestion" --> DB_S;

    UI -- "8. Polls for Logs & Progress" --> DB_S;
    UI -- "9. Fetches Suggestions" --> DB_S;
    UI -- "10. User Approves Album" --> API_I;
    UI -- "11. Updates Status" --> DB_S;
    UI -- "Fetches Thumbnails" --> API_I;
```

---

---

## 4. Component Breakdown

Each component has a distinct responsibility, ensuring separation of concerns.

*   **Orchestrator (`main.py`)**
    *   **Purpose:** The central coordinator of a scan. It is a stateless script triggered by the UI.
    *   **Responsibilities:** Loads configuration, checks and sets the scan status lock, calls other modules in sequence (DB -> Clustering -> VLM), and stores the final results in the suggestions database. It does not contain any core logic itself.

*   **Immich DB Connector (`immich_db.py`)**
    *   **Purpose:** Handles all `read-only` connections to the Immich PostgreSQL database.
    *   **Responsibilities:** Fetches all necessary asset data, including metadata and CLIP embedding vectors, in a single, efficient query.

*   **Immich API Connector (`immich_api.py`)**
    *   **Purpose:** Handles all `write` operations and interactions with the Immich API.
    *   **Responsibilities:** Creates new albums, adds assets to them, sets cover photos, favorites highlights, and robustly downloads thumbnails for VLM analysis.

*   **Clustering Service (`clustering.py`)**
    *   **Purpose:** The analytical "brain" of the system.
    *   **Responsibilities:** Implements the two-stage clustering process. **Stage 1** uses DBSCAN on time/space data to create small "eventlets." **Stage 2** uses a graph-based approach on CLIP embeddings to merge eventlets. It also identifies and separates "strong" vs. "weak" candidates based on their structural importance in the cluster graph (using articulation points).

*   **VLM Service (`vlm.py`)**
    *   **Purpose:** The interface to the generative AI model.
    *   **Responsibilities:** Prepares images and a structured prompt, sends the payload to the Ollama API, and resiliently parses the returned JSON, gracefully handling partial successes.
 
*   **Geocoding Utility (`geocoding.py`)**
    *   **Purpose:** A simple utility to add real-world location context.
    *   **Responsibilities:** Converts GPS coordinates into a primary country name.

*   **Web UI (`ui.py`)**
    *   **Purpose:** The main entrypoint and control panel for the entire application.
    *   **Responsibilities:** Triggers new scans (while preventing concurrent runs), displays live progress and logs by polling the database, lists pending suggestions, and provides a cached, interactive gallery for reviewing and approving albums. It is the sole component responsible for calling the Immich `write` API upon approval.

*   **Suggestions Database (`suggestions.db`)**
    *   **Purpose:** The central state machine for the application, implemented as a simple SQLite database.
    *   **Responsibilities:** Persists album suggestions and logs scan progress for the UI. The scan lock is managed implicitly by the UI's state, not a DB flag.

---
 
## 5. Key Data Structures

*   **The "Album Candidate" Object**

        *   `strong_asset_ids`: A list of asset IDs forming the core of the event.
        *   `weak_asset_ids`: A list of asset IDs for structurally less-central "bridge" eventlets.
        *   `min_date`, `max_date`: The start and end timestamps for the event.
        *   `gps_coords`: A list of `(lat, lon)` tuples for geocoding.

*   **`suggestions.db` Schema**
    *   **`suggestions` Table:** Stores the output of a scan for UI review.
        *   `id`, `status` ('pending', 'approved', 'rejected'), `created_at`, `vlm_title`, `vlm_description`, `strong_asset_ids_json`, `weak_asset_ids_json`, `cover_asset_id`.
    *   **`scan_logs` Table:** Stores all logs for UI display and debugging.
        *   `id`, `timestamp`, `level` ('INFO', 'PROGRESS', 'ERROR'), `message`.

---

## 6. Run Modes & State Management

*   **Scan Modes:**
    *   **Incremental:** Before fetching from Immich, `main.py` queries `suggestions.db` and compiles a list of all asset IDs already part of *any* suggestion. These IDs are then excluded from the main query to Immich.
    *   **Full Rescan:** This mode skips the exclusion step and processes all assets allowed by the `dev_mode` limit in the config. It does not wipe existing suggestions.

*   **State Management & Scan Lock:**
    *   The UI (`ui.py`) is the single point of control for starting scans.
    *   It uses Streamlit's `session_state` to hold the `subprocess.Popen` object of a running scan.
    *   The "Start Scan" buttons are disabled if this object exists and the process is still running (`poll() is None`), thus preventing concurrent scans at the UI level. This is simpler and more direct than a database lock for this architecture.

---


*   **Immich API Endpoint Nuances:**
    *   The correct thumbnail URL is `/api/asset/thumbnail/{id}`, not other documented paths.
    *   The API often serves thumbnails as `WebP` regardless of `Accept` headers. A robust client must download the image data and convert it to a standard format (like JPEG) in memory before use.

*   **VLM Interaction Requires Resilience:**
    *   **Prompt Engineering:** The VLM requires a highly-structured, machine-like prompt to reliably return JSON. The full prompt is externalized to `config.yaml` for easy tuning.
    *   **Context Window:** VLM calls can fail if the total size of the prompt and encoded images exceeds the model's context window. This must be explicitly set (`num_ctx`).
    *   **Flexible Parsing:** The VLM analysis is only considered a success if the core `title` and `description` fields are present. Other fields are treated as optional to maximize the utility of partial responses.

*   **UI Performance & UX:**
    *   **Background Scans:** Scans are run in a non-blocking background process. The UI polls the `scan_logs` table for progress, allowing the user to continue interacting with the app.
    *   **Thumbnail Caching:** To ensure a smooth gallery experience, all thumbnails for a selected suggestion are pre-fetched and cached in memory using `@st.cache_data`. This makes pagination and browsing instantaneous after the initial load.

*   **Weak Candidate UX:** The interactive gallery with "Select All" functionality was chosen to provide the best balance of power and ease-of-use for deciding which peripheral photos to include in an album.
