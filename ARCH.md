# Architecture and Design Decisions: Immich Album Suggester v0.2

**Version: 0.2**
**Last Updated: 2025-08-13**

## 1. Project Overview

This document outlines the architecture for the Immich Album Suggester, a system designed to intelligently analyze a personal photo library hosted on Immich.

The primary goals are to:
1.  **Cluster photos** into meaningful events using a combination of time, location, and visual similarity (via CLIP embeddings).
2.  **Enrich clusters** with real-world context like date and location names.
3.  **Use a Vision Language Model (VLM)** to analyze events, suggest a descriptive title, and identify highlight photos.
4.  **Provide a sophisticated Web UI** for users to trigger scans, manage, review, and approve these suggestions.
5.  **Create final albums** in Immich via its official API upon user approval.

This document serves as the primary guide for development, maintenance, and future enhancements.

## 2. Core Architectural Concepts

The application is built on two fundamental principles: a **Decoupled Two-Pass System** and a **Service-Oriented Architecture**.

### 2.1. The Decoupled Two-Pass System

The core analytical processes—clustering and VLM enrichment—are treated as separate, asynchronous tasks to maximize robustness and user experience.
*   **Pass 1: Clustering (`--mode`):** A fast, CPU-intensive pass that finds potential albums by connecting to the Immich DB, running clustering algorithms, and storing raw candidates in the local `suggestions.db`.
*   **Pass 2: VLM Enrichment (`--enrich-id`):** A slower, GPU/network-intensive pass that adds AI-generated metadata. It is triggered for specific candidates, preventing VLM failures from halting the entire discovery pipeline.

### 2.2. Service-Oriented Architecture (v2.0)

To enhance maintainability, testability, and clarity, the application's business logic is encapsulated within a dedicated **Service Layer**. High-level components like the UI (`ui.py`) and the backend engine (`app/main.py`) are lean orchestrators that delegate tasks to these services. This separation of concerns is the cornerstone of the v2.0 architecture.

## 3. System Architecture Diagram

The diagram below illustrates the flow of control. The UI and Backend Engine do not interact directly with databases or external APIs; they communicate exclusively through the Application Services layer.

```mermaid
graph TD
    subgraph "User Interface (Streamlit)"
        UI[ui.py <br/> (Presentation Layer)]
    end

    subgraph "Backend Engine (CLI)"
        MAIN[main.py <br/> (Orchestration Layer)]
    end
    
    subgraph "Application Services (Business Logic Layer)"
        PS[ProcessService]
        DS[DatabaseService]
        IS[ImmichService]
        CS[ConfigService]
    end

    subgraph "Core Logic & Low-Level IO"
        CLUST[clustering.py]
        VLM_MOD[vlm.py]
        IMM_DB[immich_db.py]
        IMM_API[immich_api.py]
    end

    subgraph "Data & External Systems"
        DB_S[suggestions.db <br/> (SQLite)]
        DB_I[Immich PostgreSQL DB]
        API_I[Immich API]
        VLM_SVC[VLM Service (Ollama)]
    end

    %% UI to Services Flow
    UI -- "1. Calls start_scan()" --> PS;
    UI -- "2. Calls get_pending_suggestions()" --> DS;
    UI -- "3. Calls get_cached_thumbnail()" --> IS;
    UI -- "4. Calls create_album()" --> IS;
    
    %% Process Service to Backend Flow
    PS -- "Launches" --> MAIN;
    
    %% Backend to Services Flow
    MAIN -- "Uses" --> DS;
    MAIN -- "Uses" --> IS;
    
    %% Services to Low-Level Logic Flow
    DS -- "Writes/Reads" --> DB_S;
    IS -- "Uses for bulk reads" --> IMM_DB;
    IS -- "Uses for writes/thumbnails" --> IMM_API;
    IMM_DB -- "Reads from" --> DB_I;
    IMM_API -- "Calls" --> API_I;
    IS -- "Provides thumbnails to" --> VLM_MOD;
    VLM_MOD -- "Calls" --> VLM_SVC;
    MAIN -- "Uses" --> CLUST;

    %% Global Config Access
    UI --> CS;
    MAIN --> CS;
    DS --> CS;
    IS --> CS;
```

## 4. Component Breakdown (v2.0)

### 4.1. High-Level Layers

*   **`ui.py` (Presentation Layer):** A pure UI component. Its sole responsibility is to render Streamlit widgets and manage UI-specific state (`st.session_state`). It contains **no business logic** and delegates all actions (fetching data, starting processes, creating albums) to the service layer.
*   **`app/main.py` (Orchestration Layer):** A lean, stateless command-line script. It parses arguments and orchestrates the execution of a clustering or enrichment pass by calling methods on the appropriate services.

### 4.2. The Service Layer (`app/services/`)

This layer contains the application's core business logic, encapsulated in single-responsibility, singleton classes.

*   **`ConfigService`:** The single source of truth for all configuration. It loads `config.yaml` and `.env` files and, critically, **initializes the application-wide logging system**, ensuring all modules produce consistent, timestamped logs.
*   **`DatabaseService`:** The exclusive gateway to the local `suggestions.db`. All SQLite read/write operations are centralized here.
*   **`ImmichService`:** A façade for all communication with the Immich system. It intelligently uses the direct PostgreSQL connection for bulk reads and the official API for writes and thumbnail downloads.
*   **`ProcessService`:** Manages all background `subprocess` tasks. It provides a simple interface to start and monitor backend jobs, abstracting this complexity away from the UI.

### 4.3. Core Logic & Low-Level IO

These modules contain specific algorithms or direct I/O logic and are called *by* the services.

*   **`app/clustering.py` & `app/geocoding.py`:** Contain the analytical algorithms for finding events and enriching them with location data.
*   **`app/vlm.py`:** Logic for prompting the VLM and parsing its response. It now depends on `ImmichService` for obtaining images.
*   **`app/immich_db.py` & `app/immich_api.py`:** Low-level modules for direct interaction with the Immich database and API, respectively.
*   **`app/exceptions.py`:** Defines a hierarchy of custom exceptions, allowing for specific error handling and clearer classification of issues.

## 5. Data Flow & State Machine

The lifecycle of a suggestion is managed by the `status` field in the `suggestions` table. The state flow remains a core concept of the application.

1.  **Creation:** A user starts a scan via the UI. The `ProcessService` launches `main.py --mode`, which uses `ImmichService` and `Clustering` to find a new event.
2.  **`pending_enrichment`:** `DatabaseService` stores the new candidate with this status. It is now visible in the UI, ready for AI analysis.
3.  **Enrichment Trigger:** The user selects a suggestion. The UI calls `ProcessService.start_enrichment()`.
4.  **`enriching`:** The backend `main.py` script, via `DatabaseService`, immediately updates the status to `enriching`. The UI sees this and displays the updated status.
5.  **VLM Analysis:** The script communicates with the VLM via the `vlm.py` module.
    *   **On Success:** The script calls `DatabaseService.update_suggestion_with_analysis()`, which updates the title, description, and status to **`pending`**.
    *   **On Failure:** The script (or a top-level exception handler) calls `DatabaseService.update_suggestion_status()` to set the status to **`enrichment_failed`**.
6.  **User Review (`pending` status):** The user inspects the fully enriched album.
    *   **On Approve:** The UI calls `ImmichService.create_album()` and then `DatabaseService.update_suggestion_status()` to set the status to **`approved`**.
    *   **On Reject:** The UI calls `DatabaseService.update_suggestion_status()` to set the status to **`rejected`**.

## 6. Key Design Decisions & "Gotchas" Encountered

This section documents critical decisions and learnings that shaped the final architecture.

### 6.1. v2.0 Architectural Decisions

The v2.0 refactoring introduced a more mature and maintainable structure based on the following decisions:

*   **Decision (Service-Oriented Architecture):** The primary decision was to abstract all business logic into a dedicated service layer (`app/services`). This was done to enforce separation of concerns, dramatically improving maintainability, testability, and code clarity.
*   **Decision (Singleton Services):** A singleton pattern was used for services to ensure a single, consistent state and avoid re-initializing expensive resources like database connections or API clients on every call.
*   **Decision (Centralized Logging):** The `ConfigService` establishes a root logger at startup. This ensures all parts of the application produce structured, timestamped logs to both the console and a file (`logs/app.log`), providing high visibility during development and for production monitoring.
*   **Decision (Specific Exception Hierarchy):** A new `app/exceptions.py` file was created to define a hierarchy of custom exceptions. This allows for more granular error handling and prevents silent failures. Services raise specific errors (e.g., `VLMConnectionError`), which are caught and logged by the top-level orchestrators, providing both code clarity and detailed debugging information.

### 6.2. Foundational "Gotchas" and Learnings (v1.0)

These are the original key findings that remain fundamental to the application's design.

*   **Database & API Interaction:**
    *   **Gotcha (Postgres):** Direct, read-only PostgreSQL access is **the only viable method** for fetching bulk CLIP embeddings. The Immich API does not expose an endpoint for this. The `ImmichService` continues to use this method for performance.
    *   **Gotcha (API Endpoints):** The correct thumbnail URL can vary between Immich versions (`/api/asset/thumbnail/{id}` vs. `/api/assets/{id}/thumbnail`). The `immich_api.py` module remains robust by trying multiple candidates.
    *   **Gotcha (Image Formats):** The Immich API often serves thumbnails as `WebP` regardless of `Accept` headers. The client must convert these images (e.g., to JPEG) in memory before sending them to the VLM, which may not support WebP.

*   **VLM Resilience:**
    *   **Gotcha (Prompt Engineering):** VLMs require highly-structured, non-conversational prompts to reliably return JSON. This is handled in `vlm.py` and the prompt is externalized to `config.yaml` for easy tuning.
    *   **Gotcha (Context Window):** VLM calls can fail if the total size of the prompt and base64-encoded images exceeds the model's context window. This is managed by setting the `num_ctx` parameter in the Ollama API call.
    *   **Decision (Resilience):** The system must be resilient to VLM failures. This is achieved via the two-pass system, a robust retry mechanism in `vlm.py`, and flexible parsing of the VLM's response.

*   **UI Performance & UX:**
    *   **Decision (Caching):** To provide a smooth gallery experience, thumbnails are aggressively cached. This makes pagination and browsing instantaneous after an initial load.
    *   **Gotcha (Image Orientation):** Mobile phone photos often contain EXIF orientation tags. Without processing these tags, images appear rotated. A helper function correctly reads the orientation tag and rotates the image before display.
    *   **Decision (Weak Candidates):** The concept of "Weak Candidates" (photos from "bridge" eventlets) is presented to the user as "Additional Photos" to be more intuitive, providing a powerful but easy-to-understand way to fine-tune album contents.

*   **Configuration & Deployment:**
    *   **Decision (Externalized Config):** All tunable parameters, from clustering thresholds to VLM prompts, are externalized to `config.yaml`.
    *   **Decision (Containerization):** The entire application is containerized via `Dockerfile` and `docker-compose.yml`, simplifying deployment and ensuring a consistent runtime environment.
    *   **Gotcha (Python Module Execution):** Running a script from within a package requires the `python -m <package>.<module>` syntax (e.g., `python -m app.main`). The `ProcessService` now correctly uses this syntax to ensure robust execution of the backend engine.