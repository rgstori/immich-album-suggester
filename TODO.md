# TODO: Immich Album Suggester Improvements

## 🔴 CRITICAL - Core Functionality Gaps

These features address significant gaps that can lead to a poor user experience or incorrect behavior.

### **NO OUTSTANDING CRITICAL ISSUES** ✅

All critical core functionality gaps have been resolved and moved to the completed section.

## 🟠 HIGH - Stability & User Trust

These items focus on making the application more robust and the user's actions more predictable.

### **NO OUTSTANDING HIGH PRIORITY ISSUES** ✅

All high priority stability and user trust issues have been resolved.


## 🟡 MEDIUM - Code Quality & Maintainability

### **REFACTORING & COMPLEXITY REDUCTION** 🆕 ⭐ **HIGH PRIORITY**

1. **~~Introduce Data Transfer Objects (DTOs)~~** ✅ **COMPLETED**
   - **~~Problem~~**: ~~Heavy reliance on generic dictionaries (Dict[str, Any]) for data entities is error-prone~~
   - **~~Impact~~**: ~~Typo-prone keys, no IDE support, difficult static analysis~~
   - **~~Fix~~**: ~~Use dataclasses/TypedDict for core entities like Suggestion with centralized parsing logic~~
   - **✅ SOLUTION**: Implemented comprehensive DTO system with `SuggestionAlbum`, `ImmichAlbum`, `VLMAnalysis`, `ClusteringCandidate`, and `PhotoAsset` dataclasses providing type safety, IDE support, and centralized data validation throughout the entire application

2. **Refactor Repetitive UI Rendering Logic** 🆕 ⭐
   - **Problem**: Duplicate thumbnail display code across render_photo_grid, render_weak_asset_selector, and sidebar
   - **Impact**: High maintenance burden, inconsistent UI behavior
   - **Fix**: Create reusable render_thumbnail_card() and format_suggestion_metadata() functions

3. **Generalize Pagination Logic** 🆕 ⭐
   - **Problem**: Separate pagination implementations for Core Photos and Weak Assets
   - **Impact**: Code duplication, inconsistent pagination behavior
   - **Fix**: Single render_pagination_controls() function with configurable page keys

4. **Decompose Complex Service Methods** 🆕 ⭐
   - **Problem**: Large methods like get_albums_with_metadata() do too many things
   - **Impact**: Hard to read, test, and maintain
   - **Fix**: Break into smaller helper methods (_fetch_all_albums_from_api, _extract_metadata_from_album_assets)

5. **Standardize Date Handling** 🆕 ⭐
   - **Problem**: Date parsing scattered across multiple files with inconsistent formats
   - **Impact**: Fragile date handling, potential bugs
   - **Fix**: Single utility function, consistent ISO 8601 storage, datetime objects for DB operations

### **TESTING & VALIDATION** 🚨

6. **Service Layer Unit Testing** 🆕
   - **Problem**: The service layer containing all business logic is untested
   - **Impact**: Refactoring is risky; bugs can be introduced easily
   - **Fix**: Introduce pytest and pytest-mock, write unit tests for each service with mocked dependencies

7. **Configuration Schema Validation** 🆕
   - **Problem**: Invalid config.yaml (misspelled keys, wrong data types) leads to NoneType errors at runtime
   - **Impact**: Poor user experience on setup; hard-to-diagnose errors
   - **Fix**: Use Pydantic to define Config model with validation at startup

8. **VLM Provider Plugin Architecture** 🆕
   - **Problem**: VLM logic tightly coupled to Ollama's API
   - **Impact**: Difficult to switch to other providers (OpenAI, Anthropic, Gemini)
   - **Fix**: Abstract base class `VLMProvider`, concrete implementations, factory pattern

## 🟢 LOW - Performance & Enhancement

### **NEW LOW PRIORITY ISSUES** 🚨

7. **Redundant Thumbnail Requests** 🔄 **PARTIALLY ADDRESSED**
    - **Remaining Issue**: Some edge cases might trigger duplicate requests before cache is populated
    - **Fix**: Implement in-memory request deduplication lock with `in_flight_requests` set

8. **Missing Graceful Degradation in UI** 🆕
    - **Problem**: If VLM is configured but unavailable, enrichment fails hard with poor UI feedback
    - **Impact**: UI for `enrichment_failed` suggestion is not helpful
    - **Fix**: Allow manual title/description editing and approval with default metadata even after VLM failure

9. **No Telemetry/Metrics** 🆕
    - **Problem**: No visibility into system performance or usage patterns
    - **Fix**: Add optional telemetry for clustering performance, VLM response times, etc.

## 🔵 ENHANCEMENT - New Features

These are new, high-value features that expand the application's capabilities beyond its current scope.

### **AI & Core Logic Enhancements** 🆕

10. **People-Aware Album Generation** 🆕
    - **Description**: Leverage Immich's existing face recognition to create smarter albums
    - **Enhancement**: 
      - Query face data in `immich_db.py`, identify top 3-5 people per cluster
      - Add people context to VLM prompt: "People present include: Alice, Bob, Charlie"
      - Generate richer titles like "Alice's 5th Birthday Party" instead of "Event in August 2024"

11. **Semantic Search-Based Albums** 🆕
    - **Description**: Allow users to create albums based on text queries
    - **Enhancement**:
      - Add text input: "Create an album of... (e.g., 'all my photos of snowy mountains')"
      - Use text embedding model for query, cosine similarity search against photo embeddings
      - Return top N matching photos as new album suggestion

12. **Smarter Cover Photo & Highlight Selection** 🆕
    - **Description**: Improve cover photo selection beyond semi-random VLM choice
    - **Enhancement**:
      - Post-process VLM samples with quality metrics (vibrancy, sharpness, faces)
      - Combine VLM index with quality score for optimal cover selection
      - Implement highlight parsing for photo favoriting in Immich

### **UI & UX Enhancements** 🆕

13. **Interactive Album Refinement** 🆕
    - **Description**: Give users more control than just "approve" or "reject"
    - **Enhancement**:
      - **Remove Photo**: Add 'Remove' (🗑️) icon on thumbnails to exclude photos from final album
      - **Split Album**: "Split Album" button to re-run clustering with stricter thresholds, breaking into distinct events

14. **Merge Preview and Undo** 🔄 **EXPANDED FROM EXISTING #19**
    - **Description**: Current merge is a blind operation
    - **Enhancement**:
      - **Visual Preview**: Modal/page showing combined photo grid before merge confirmation
      - **Undo**: Store original suggestion IDs in `merged_suggestions_log`, "Undo last merge" button (5-minute window)

15. **Full-Featured Table View** 🔄 **EXPANDED FROM EXISTING #18**
    - **Description**: Table view could be a powerhouse for suggestion management
    - **Enhancement**:
      - **Full Sorting**: Backend sorting for all columns (Title, Location, Status) in `database_service.py`
      - **Filtering**: Search box to filter suggestions by title or location
      - **Bulk Status Changes**: Checkboxes and dropdown for bulk "Approve"/"Reject" actions

## Implementation Priority

### **IMMEDIATE (Critical User Experience)**
1. ✅ **Sync with Existing Immich Albums** - Prevents duplicate album suggestions
2. ✅ **Suggest Additions to Existing Albums** - Keeps albums current with new photos

### **NEXT SPRINT (Complexity Reduction - High Impact)** ⭐
1. **Introduce Data Transfer Objects (DTOs)** - Type safety and IDE support
2. **Refactor Repetitive UI Rendering Logic** - Eliminate code duplication 
3. **Generalize Pagination Logic** - Single reusable pagination component
4. **Decompose Complex Service Methods** - Improve maintainability
5. **Standardize Date Handling** - Centralized date processing

### **FOLLOWING SPRINT (Testing & Architecture)**  
6. **Service Layer Unit Testing** - Enable safe refactoring
7. **Configuration Schema Validation** - Better setup experience
8. **VLM Provider Plugin Architecture** - Multi-provider support

### **FUTURE ENHANCEMENTS**
10. **People-Aware Album Generation** - Leverage face recognition for smarter titles
11. **Interactive Album Refinement** - Photo removal and album splitting tools
12. **Full-Featured Table View** - Enhanced sorting, filtering, bulk operations
13. **Semantic Search-Based Albums** - Text query-driven album creation

## Recently Completed ✅

**Security & Critical Issues (All Fixed)**
- SQL injection vulnerabilities with whitelist validation
- API key exposure completely removed from logs
- Input validation for all user-controlled data

**Auto-Refresh & Process Management**
- Smart polling system with adaptive intervals
- Real-time suggestion list updates
- Proper subprocess lifecycle management
- Toast notifications for completion events

**Resource Management & Performance**
- Database connection leaks fixed with proper cleanup
- LRU cache implementation with 50MB memory limit
- Thread-safe cache with automatic eviction
- Selective cache invalidation by suggestion ID

**User Experience Improvements**
- Informative error messages with retry functionality
- Better thumbnail loading feedback
- Improved error logging and debugging

**Enhanced UI & Decision-Making Features**
- Thumbnail previews in suggestion list for visual assessment
- Editable album titles and descriptions in both stages
- Interactive cover photo selection from grid interface with mode-based UI
- Comprehensive metadata display (photos, dates, locations, status)
- Professional layout with improved navigation and controls
- Real-time database updates for all editable fields
- Compact date and location metadata display under photo thumbnails
- Visual cover selection mode with clear state indicators and cancel option

**Service-Oriented Architecture (v2.0)**
- Complete separation of business logic into service layer
- Thread-safe singleton pattern for consistent state management
- Centralized configuration and logging system
- Custom exception hierarchy for specific error handling
- Clean separation between UI, orchestration, and business logic layers

**Critical Security Fixes (v2.1)**
- **SQL Injection Vulnerabilities** ✅
- **API Key Exposure** ✅  
- **Missing Input Validation** ✅
- **SQL Injection in Schema Migration** ✅ - Fixed with whitelist validation
- **Thread Safety in Singleton Pattern** ✅ - Fixed with double-checked locking
- **Uncontrolled Resource Consumption** ✅ - Added VLM request size validation

**High Priority Stability Issues (v2.0-v2.1)**
- UI Auto-Refresh ✅
- Database Connection Leaks ✅
- Zombie Process Risk ✅
- Unbounded Memory Cache ✅
- Database Transaction Atomicity ✅
- Broad Exception Handling in VLM ✅
- Process Cleanup on Shutdown ✅ - Added signal handlers and graceful termination
- VLM Request Size Validation ✅ - Added context window and image size validation

**Code Quality Improvements (v2.3)**
- Comprehensive type hints added to all service methods and core modules
- Standardized logging throughout codebase replacing print() statements
- All hardcoded configuration values moved to config.yaml for maintainability
- Enhanced configuration management with proper defaults and validation
- Centralized session state management with UISessionState class and type-safe operations

**Core Functionality Enhancements (v2.4)**
- **Sync with Existing Immich Albums** ✅ - Prevents duplicate album suggestions by excluding assets already in manually created albums
- **Suggest Additions to Existing Albums** ✅ - Enables discovery and addition of relevant photos to existing albums
- Added `get_all_asset_ids_in_albums()` method with caching to avoid API hammering  
- Integrated album exclusion logic into clustering workflow with graceful error handling
- Added UI cache refresh button for immediate album data updates
- Implemented `get_albums_with_metadata()` for detailed album analysis via Immich API
- Created `find_potential_additions_to_albums()` clustering algorithm for temporal/spatial photo matching
- Added `from_immich` status and specialized UI workflow for existing album management
- Enhanced photo count displays throughout interface to show existing + potential addition format
- Added `add_assets_to_album()` API function for seamless photo addition to existing albums
- **Duplicate Prevention & Cleanup** ✅ - Prevents duplicate Immich albums on repeated scans with automatic cleanup
- **Robust Date/Location Parsing** ✅ - Enhanced EXIF metadata extraction with multiple format support and fallback handling

**Medium Priority Items Completed in v2.3**
- **Complex Session State Management** ✅ - Created centralized UISessionState class with type-safe state transitions
- **Incomplete Type Hints** ✅ - Added comprehensive type hints to service methods
- **Inconsistent Error Logging** ✅ - Standardized logging throughout codebase  
- **Hardcoded Configuration Values** ✅ - Moved all hardcoded values to config.yaml
- Album View Missing Metadata ✅
- Poor Error Messages ✅
- Inefficient Cache Clearing ✅
- Album Switching Issues ✅
- Enrichment Workflow Problems ✅

**UI Architecture Enhancements (v2.2)**
- Dual view system: table overview when no album selected, detailed view for individual albums
- Comprehensive table view with sortable columns (date, photo count) and visual status indicators
- Enhanced bulk operations: multi-select merge functionality with intelligent data combination
- Merge algorithm with asset deduplication, date range calculation, and location intelligence
- Two-stage confirmation flows for destructive operations with preview information
- Unified selection state management across sidebar and main table views
- Accessibility improvements: proper checkbox labels and keyboard navigation

---

## Code Quality Metrics Target
- [x] 100% type hint coverage for public APIs
- [ ] <5 broad exception handlers (`except Exception:`)
- [x] Zero hardcoded configuration values
- [ ] All database operations in explicit transactions
- [ ] 90%+ test coverage for service layer

The refactoring has significantly improved the architecture, but there are still some critical security and stability issues that need immediate attention, particularly around thread safety and SQL injection in the migration code.