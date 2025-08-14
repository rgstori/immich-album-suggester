# Architecture and Design Decisions: Immich Album Suggester v2.3

**Version: 2.3**  
**Last Updated: 2025-08-14**

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

*   **`ConfigService`:** The single source of truth for all configuration. It loads `config.yaml` and `.env` files, **initializes the application-wide logging system**, and implements **thread-safe singleton pattern** to prevent race conditions in multi-threaded environments.
*   **`DatabaseService`:** The exclusive gateway to the local `suggestions.db`. All SQLite read/write operations are centralized here with **SQL injection protection** using whitelist validation for schema operations.
*   **`ImmichService`:** A façade for all communication with the Immich system. It intelligently uses the direct PostgreSQL connection for bulk reads and the official API for writes and thumbnail downloads.
*   **`ProcessService`:** Manages all background `subprocess` tasks with **graceful shutdown handling**. It provides signal handlers and cleanup mechanisms to prevent zombie processes during application termination.

### 4.3. Core Logic & Low-Level IO

These modules contain specific algorithms or direct I/O logic and are called *by* the services.

*   **`app/clustering.py` & `app/geocoding.py`:** Contain the analytical algorithms for finding events and enriching them with location data.
*   **`app/vlm.py`:** Logic for prompting the VLM and parsing its response. It now depends on `ImmichService` for obtaining images and includes **request size validation** to prevent VLM context window overflow.
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

### 6.2. v2.1 Security & Robustness Enhancements

Version 2.1 focused on addressing critical security and stability issues discovered during architecture review:

*   **Security (SQL Injection Prevention):** Implemented whitelist validation in `DatabaseService._add_column_if_not_exists()` to prevent SQL injection attacks during schema migrations. Only predefined table names, column names, and data types are permitted.
*   **Security (Thread-Safe Singleton):** Enhanced `ConfigService` with double-checked locking pattern using `threading.Lock()` to prevent race conditions in multi-threaded environments that could lead to multiple instances or partial initialization.
*   **Robustness (VLM Resource Protection):** Added comprehensive request size validation in `vlm.py` to prevent VLM context window overflow. Validates both total token count and individual image sizes (max 2MB base64) before sending requests.
*   **Robustness (Process Cleanup):** Implemented graceful shutdown handling in `ProcessService` with signal handlers (`SIGTERM`, `SIGINT`) and `atexit` cleanup. Prevents zombie processes during application crashes or forced termination with 5-second graceful timeout before force kill.

### 6.3. Foundational "Gotchas" and Learnings (v1.0)

These are the original key findings that remain fundamental to the application's design.

*   **Database & API Interaction:**
    *   **Gotcha (Postgres):** Direct, read-only PostgreSQL access is **the only viable method** for fetching bulk CLIP embeddings. The Immich API does not expose an endpoint for this. The `ImmichService` continues to use this method for performance.
    *   **Gotcha (API Endpoints):** The correct thumbnail URL can vary between Immich versions (`/api/asset/thumbnail/{id}` vs. `/api/assets/{id}/thumbnail`). The `immich_api.py` module remains robust by trying multiple candidates.
    *   **Gotcha (Image Formats):** The Immich API often serves thumbnails as `WebP` regardless of `Accept` headers. The client must convert these images (e.g., to JPEG) in memory before sending them to the VLM, which may not support WebP.

*   **VLM Resilience:**
    *   **Gotcha (Prompt Engineering):** VLMs require highly-structured, non-conversational prompts to reliably return JSON. This is handled in `vlm.py` and the prompt is externalized to `config.yaml` for easy tuning.
    *   **Gotcha (Context Window):** VLM calls can fail if the total size of the prompt and base64-encoded images exceeds the model's context window. This is now **proactively validated** with size estimation before requests are sent, preventing silent failures.
    *   **Decision (Resilience):** The system must be resilient to VLM failures. This is achieved via the two-pass system, a robust retry mechanism in `vlm.py`, flexible parsing of the VLM's response, and comprehensive request validation.

*   **UI Performance & UX:**
    *   **Decision (Caching):** To provide a smooth gallery experience, thumbnails are aggressively cached. This makes pagination and browsing instantaneous after an initial load.
    *   **Gotcha (Image Orientation):** Mobile phone photos often contain EXIF orientation tags. Without processing these tags, images appear rotated. A helper function correctly reads the orientation tag and rotates the image before display.
    *   **Decision (Weak Candidates):** The concept of "Weak Candidates" (photos from "bridge" eventlets) is presented to the user as "Additional Photos" to be more intuitive, providing a powerful but easy-to-understand way to fine-tune album contents.

*   **Configuration & Deployment:**
    *   **Decision (Externalized Config):** All tunable parameters, from clustering thresholds to VLM prompts, are externalized to `config.yaml`.
    *   **Decision (Containerization):** The entire application is containerized via `Dockerfile` and `docker-compose.yml`, simplifying deployment and ensuring a consistent runtime environment.
    *   **Gotcha (Python Module Execution):** Running a script from within a package requires the `python -m <package>.<module>` syntax (e.g., `python -m app.main`). The `ProcessService` now correctly uses this syntax to ensure robust execution of the backend engine.

## 7. v2.2 UI Architecture & User Experience Enhancements

Version 2.2 introduced significant improvements to the user interface architecture and user experience, focusing on better use of screen real estate and enhanced workflow management.

### 7.1. Dual View System Architecture

*   **Design Decision (Table vs. Album Views):** Implemented a dual view system where the main content area serves different purposes based on user context:
    *   **Table View:** When no album is selected, displays a comprehensive table of all pending suggestions with full metadata
    *   **Album View:** When a specific album is selected, shows detailed photo galleries and individual album management tools

*   **Benefits:**
    *   **Optimized Screen Usage:** Makes full use of main content area when not reviewing specific albums
    *   **Workflow Efficiency:** Users can quickly assess and manage multiple suggestions without navigating through individual albums
    *   **Context-Appropriate Controls:** Each view provides the most relevant tools for that stage of the workflow

### 7.2. Enhanced Bulk Operations

*   **Design Decision (Multi-Selection Management):** Unified selection state across both sidebar and table views using shared session state
*   **Merge Functionality:** Added sophisticated album merging with intelligent data combination:
    *   **Asset Deduplication:** Automatically removes duplicate photos when combining albums
    *   **Date Range Merging:** Calculates comprehensive date ranges from all source albums
    *   **Location Intelligence:** Chooses primary location based on frequency across merged albums
    *   **Status Reset:** Merged albums return to `pending_enrichment` for fresh AI analysis

### 7.3. Improved Information Architecture

*   **Sortable Data Presentation:** Table headers provide interactive sorting with visual feedback (arrows)
*   **Status Visualization:** Comprehensive status indicators with emojis and real-time updates
*   **Metadata Richness:** Complete album information displayed in compact, scannable format
*   **Progressive Disclosure:** Basic information in table view, detailed information in album view

### 7.4. Confirmation Flow Patterns

*   **Two-Stage Confirmations:** Critical operations (merge, delete all) use staged confirmation patterns
*   **Preview Information:** Users see exactly what will happen before confirming destructive operations
*   **State Management:** Confirmation dialogs use unique session keys to prevent conflicts

This dual view architecture significantly improves the user experience by providing the right interface for each stage of album management, from high-level overview to detailed review.

## 8. v2.3 Code Quality & Maintainability Enhancements

Version 2.3 focused on code quality improvements to enhance maintainability, debugging, and development productivity.

### 8.1. Type Safety Implementation

*   **Comprehensive Type Hints:** Added complete type annotations to all service methods and core modules:
    *   **Service Layer:** All `DatabaseService` and `ConfigService` methods now have full type signatures
    *   **Return Types:** Clear specification of return types (`List[Dict[str, Any]]`, `Optional[str]`, etc.)
    *   **Parameter Types:** All function parameters properly typed for better IDE support
    *   **Import Organization:** Proper typing imports (`from typing import List, Dict, Optional, Any, Literal, Iterator`)

*   **Benefits:**
    *   **IDE Support:** Enhanced autocompletion and error detection during development
    *   **Runtime Safety:** Earlier detection of type-related errors
    *   **Documentation:** Type hints serve as self-documenting code
    *   **Refactoring Safety:** Safer code changes with compile-time type checking

### 8.2. Logging Standardization

*   **Unified Logging Framework:** Eliminated inconsistent use of `print()` statements throughout codebase:
    *   **Module Coverage:** Updated `geocoding.py`, `clustering.py`, `immich_db.py`, `immich_api.py`
    *   **Appropriate Log Levels:** Used proper severity levels (`info`, `warning`, `error`, `critical`)
    *   **Consistent Format:** All modules now use the same logging format established by `ConfigService`
    *   **Preserved Essential Prints:** Kept critical startup error messages that occur before logging initialization

*   **Benefits:**
    *   **Production Monitoring:** Consistent log format enables better monitoring and alerting
    *   **Debugging Efficiency:** Structured logs with timestamps and severity levels
    *   **Configuration Control:** Log levels configurable via `config.yaml`
    *   **File + Console Output:** Logs written to both file and console for flexibility

### 8.3. Configuration Management Enhancement

*   **Eliminated Hardcoded Values:** Moved all magic numbers and constants to `config.yaml`:
    *   **UI Settings:** Cache sizes (`ui.cache_max_entries: 500`), log display settings (`ui.log_container_height: 200`, `ui.recent_logs_count: 50`)
    *   **VLM Processing:** Image token estimates (`vlm.image_token_estimate: 500`), size limits (`vlm.max_image_size_bytes: 2097152`)
    *   **Backward Compatibility:** All config values have sensible defaults for existing installations

*   **Benefits:**
    *   **Runtime Configurability:** Administrators can tune behavior without code changes
    *   **Environment Adaptation:** Different settings for development vs. production
    *   **Documentation:** Config file serves as comprehensive settings documentation
    *   **Consistency:** Single source of truth for all configurable parameters

### 8.4. Developer Experience Improvements

*   **Enhanced IDE Support:** Type hints enable better code completion, navigation, and refactoring
*   **Debugging Improvements:** Structured logging makes issue diagnosis more efficient
*   **Configuration Flexibility:** Easier experimentation with different settings during development
*   **Code Clarity:** Elimination of magic numbers makes code more self-documenting

These quality improvements establish a solid foundation for future development while maintaining full backward compatibility with existing deployments.