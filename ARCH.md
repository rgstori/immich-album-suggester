# Architecture and Design Decisions: Immich Album Suggester

**Version: 1.0**
**Last Updated: 2025-08-12**

## 1. Project Overview

This document outlines the architecture for the Immich Album Suggester, a system designed to intelligently analyze a personal photo library hosted on Immich.

The primary goals are to:
1.  **Cluster photos** into meaningful events using a combination of time, location, and visual similarity (via CLIP embeddings).
2.  **Enrich clusters** with real-world context like date and location names.
3.  **Use a Vision Language Model (VLM)** to analyze events, suggest a descriptive title, and identify highlight photos.
4.  **Provide a sophisticated Web UI** for users to trigger scans, manage, review, and approve these suggestions.
5.  **Create final albums** in Immich via its official API upon user approval.

This document serves as the primary guide for development, maintenance, and future enhancements.

## 2. Core Architectural Concept: The Decoupled Two-Pass System

The application's architecture is built on a **decoupled, two-pass design**. This is a fundamental design decision made to maximize robustness, scalability, and user experience. The two core analytical processes—clustering and VLM enrichment—are treated as separate, asynchronous tasks coordinated by the UI and the central `suggestions.db`.

*   **Pass 1: Clustering (`--mode` scan)**
    *   **Goal:** To perform the CPU- and database-intensive work of finding potential albums.
    *   **Process:** This pass connects to the Immich PostgreSQL database, fetches asset metadata and embeddings, performs the two-stage clustering, and stores the resulting raw candidates in the `suggestions.db` with a status of `pending_enrichment`.
    *   **Characteristics:** This process is relatively fast and does not depend on external network services like the VLM.

*   **Pass 2: VLM Enrichment (`--enrich-id` scan)**
    *   **Goal:** To perform the GPU- and network-intensive work of AI analysis.
    *   **Process:** This pass is triggered from the UI for one or more specific suggestions. It fetches a suggestion's data from `suggestions.db`, downloads sample images via the Immich API, queries the VLM service, and updates the suggestion with the AI-generated title, description, and cover photo.
    *   **Characteristics:** This process is slower and more prone to failure (due to network latency or VLM errors). Its separation prevents a VLM failure from halting the entire album discovery pipeline.

This decoupling allows users to quickly find all potential events in their library and then choose which ones to invest computational resources in for AI analysis, either individually or in batches.

## 3. System Architecture Diagram

```mermaid
graph TD
    subgraph "User Interface (Streamlit)"
        UI[ui.py <br/> (Command Center)]
    end

    subgraph "Backend Engine (Stateless Scripts)"
        P1[main.py --mode <br/>(Clustering Pass)]
        P2[main.py --enrich-id <br/>(Enrichment Pass)]
    end

    subgraph "Central State"
        DB_S[suggestions.db <br/> (SQLite)]
    end

    subgraph "External Systems"
        DB_I[Immich PostgreSQL DB]
        API_I[Immich API]
        VLM[VLM Service (Ollama)]
    end

    %% User Actions
    UI -- "1a. Start Clustering Scan" --> P1;
    UI -- "1b. Start Enrichment" --> P2;

    %% Pass 1: Clustering Flow
    P1 -- "2. Fetch Asset IDs to Exclude" --> DB_S;
    P1 -- "3. Fetch All Assets & Embeddings" --> DB_I;
    P1 -- "4. Store Raw Candidates (status: 'pending_enrichment')" --> DB_S;

    %% Pass 2: Enrichment Flow
    P2 -- "5. Fetch Candidate Data" --> DB_S;
    P2 -- "6. Download Thumbnails" --> API_I;
    P2 -- "7. Get VLM Analysis" --> VLM;
    P2 -- "8. Update Suggestion (status: 'pending')" --> DB_S;

    %% UI Interaction with State and API
    UI -- "Polls Logs & Suggestions" --> DB_S;
    UI -- "User Approves Album" --> API_I;
    UI -- "Updates Status (approved/rejected)" --> DB_S;
    UI -- "Displays Thumbnails" --> API_I;
```

## 4. Component Breakdown

Each component has a distinct and focused responsibility.

*   **Web UI (`ui.py`)**
    *   **Purpose:** The main entrypoint and control panel for the entire application.
    *   **Responsibilities:**
        *   Triggers background scans for both **Pass 1 (Clustering)** and **Pass 2 (Enrichment)**.
        *   Manages concurrent processes, preventing multiple clustering scans and tracking individual enrichment tasks.
        *   Provides a rich, sortable, and interactive list of all suggestions.
        *   Displays live logs by polling the `scan_logs` table.
        *   Features a cached, paginated gallery for fast browsing of suggestions.
        *   Calls the Immich `write` API to create albums upon user approval.

*   **Orchestrator (`app/main.py`)**
    *   **Purpose:** A stateless command-line script that serves as the entrypoint for all backend processing.
    *   **Responsibilities:** Parses command-line arguments (`--mode` or `--enrich-id`) to determine which pass to execute. It loads configuration and coordinates the other `app/` modules to perform the requested task.

*   **Suggestions Database (`suggestions.db`)**
    *   **Purpose:** The central state machine for the application, implemented as a simple SQLite database.
    *   **Responsibilities:**
        *   Persists album suggestions through their entire lifecycle.
        *   Stores logs from backend processes for the UI to display.
        *   Uses a `status` column (`pending_enrichment`, `enriching`, `pending`, `enrichment_failed`, `approved`, `rejected`) to track the precise state of each suggestion, enabling the robust, asynchronous workflow.

*   **Immich DB Connector (`app/immich_db.py`)**
    *   **Purpose:** Handles all `read-only` connections to the Immich PostgreSQL database.
    *   **Responsibilities:** Fetches bulk asset data (metadata, embeddings) for the clustering pass and targeted EXIF data for the single-photo view in the UI. **Decision:** Direct database access is the only performant method for acquiring CLIP embeddings in bulk.

*   **Immich API Connector (`app/immich_api.py`)**
    *   **Purpose:** Handles all interactions with the official Immich API.
    *   **Responsibilities:** Creates albums, adds assets, sets cover photos, and—critically—downloads thumbnails for both VLM analysis and UI display.

*   **Clustering Service (`app/clustering.py`)**
    *   **Purpose:** The analytical "brain" for identifying events.
    *   **Responsibilities:** Implements the two-stage clustering process. It identifies "strong" core photos vs. "weak" peripheral photos by identifying articulation points in the cluster graph, a key feature for providing user choice.

*   **VLM Service (`app/vlm.py`)**
    *   **Purpose:** The interface to the generative AI model (Ollama).
    *   **Responsibilities:** Prepares images and a structured prompt, sends the payload to the VLM, and resiliently parses the response. Includes a retry mechanism to handle transient network or model failures.

*   **Geocoding Utility (`app/geocoding.py`)**
    *   **Purpose:** A simple utility to add real-world location context.
    *   **Responsibilities:** Converts GPS coordinates into a primary country name using an external API.

## 5. Data Flow & State Machine

The lifecycle of a suggestion is managed by the `status` field in the `suggestions` table.

1.  **Creation:** A user starts an `incremental` or `full` scan from the UI. The `main.py --mode` script runs.
2.  **`pending_enrichment`:** The clustering pass identifies a new event and stores it in the database. It is now visible in the UI, ready for AI analysis.
3.  **Enrichment Trigger:** The user selects one or more suggestions in the UI and clicks "Enrich." The UI launches one or more `main.py --enrich-id` background processes.
4.  **`enriching`:** The backend script immediately updates the suggestion's status to `enriching`. The UI sees this and displays enrichment progress, keeps the album visible in the sidebar, and shows appropriate status-specific controls.
5.  **VLM Analysis:** The script communicates with the Immich API and the VLM.
    *   **On Success:** The script updates the suggestion with the VLM's title and description and sets the status to **`pending`**. The suggestion is now fully enriched and ready for final user review.
    *   **On Failure:** The script sets the status to **`enrichment_failed`**. The UI sees this and can display an error and a retry option.
6.  **User Review (`pending` status):** The user inspects the album, includes/excludes "additional" photos, and makes a decision.
    *   **On Approve:** The UI calls the Immich API to create the album and sets the status to **`approved`**. The suggestion is removed from the pending list.
    *   **On Reject:** The UI sets the status to **`rejected`**. The suggestion is removed from the pending list.

## 6. Key Design Decisions & "Gotchas" Encountered

This section documents critical decisions and learnings that shaped the final architecture.

*   **System Architecture:**
    *   **Decision:** A distributed system (UI/Engine on one machine, VLM on another, Immich on a third) communicating via IP addresses is proven and effective. The application is fully modularized into single-responsibility Python files.
    *   **Decision:** The UI is the central command console. Scans are triggered from the UI, which then monitors background `subprocess` objects. This prevents concurrent runs at the source and is simpler than a database-level lock.

*   **Database & API Interaction:**
    *   **Gotcha (Postgres):** Direct, read-only PostgreSQL access is **the only viable method** for fetching bulk CLIP embeddings. The Immich API does not expose an endpoint for this.
    *   **Gotcha (API Endpoints):** The correct thumbnail URL can vary between Immich versions (`/api/asset/thumbnail/{id}` vs. `/api/assets/{id}/thumbnail`). The API client must be robust and try multiple candidates.
    *   **Gotcha (Image Formats):** The Immich API often serves thumbnails as `WebP` regardless of `Accept` headers. The client must convert these images (e.g., to JPEG) in memory before sending them to the VLM, which may not support WebP.

*   **VLM Resilience:**
    *   **Gotcha (Prompt Engineering):** VLMs require highly-structured, non-conversational prompts to reliably return JSON. The exact prompt structure, including specifying the format (`format: "json"` in the API call) and explicitly defining the desired JSON schema in the text, is crucial. The prompt is externalized to `config.yaml` for easy tuning.
    *   **Gotcha (Context Window):** VLM calls fail if the total size of the prompt and base64-encoded images exceeds the model's context window. The `num_ctx` parameter must be explicitly set in the Ollama API call.
    *   **Decision:** The system must be resilient to VLM failures. This was achieved via the two-pass system, a retry mechanism in `vlm.py`, and flexible parsing of the VLM's response.

*   **UI Performance & UX:**
    *   **Decision:** To provide a smooth gallery experience, all thumbnails for a selected suggestion are pre-fetched and cached in memory using an LRU cache (50MB limit). This makes pagination and browsing instantaneous after an initial load.
    *   **Gotcha (Image Orientation):** Mobile phone photos often contain EXIF orientation tags. Without processing these tags, images appear rotated in the UI. A helper function was implemented to read the orientation tag and correctly rotate the image before display.
    *   **Decision:** The concept of "Weak Candidates" (photos from "bridge" eventlets) is presented to the user as "Additional Photos" to be more intuitive. This provides a powerful but easy-to-understand way for users to fine-tune album contents.
    *   **Gotcha (Album Switching):** Streamlit's session state required careful management to prevent race conditions when switching between albums. A dedicated `switch_to_album()` function with loading states was implemented to ensure clean transitions.
    *   **Gotcha (Enrichment State):** When enriching the currently viewed album, the UI needed to handle status transitions gracefully. The main view now shows different interfaces based on `pending_enrichment`, `enriching`, and `pending` statuses.

*   **Configuration & Deployment:**
    *   **Decision:** All tunable parameters, from clustering thresholds to VLM prompts and UI text, are externalized to `config.yaml`. This makes the Python code a stable logic layer that can be configured without code changes.
    *   **Decision:** The entire application is containerized via a `Dockerfile` and `docker-compose.yml`, simplifying deployment and ensuring a consistent runtime environment. The `data/` directory is volume-mounted to persist the SQLite database across container restarts.
    *   **Gotcha (Python Module Execution):** When running a script from within a package (like `app/main.py`), it is critical to use the `python -m <package>.<module>` syntax (e.g., `python -m app.main`). Running `python app/main.py` directly breaks Python's package context, causing `ImportError: attempted relative import with no known parent package` in sub-modules. The `ui.py` was updated to use `python -m ...` in its `subprocess` calls to ensure correct and robust execution of the backend engine in the Docker environment.