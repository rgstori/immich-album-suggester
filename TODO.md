# TODO: Immich Album Suggester Improvements

## 🔴 CRITICAL - Core Functionality Gaps

These features address significant gaps that can lead to a poor user experience or incorrect behavior.

1. **Sync with Existing Immich Albums to Prevent Duplicates** 🆕
   - **Problem**: Current logic only excludes assets from its own suggestions, has no knowledge of manually created Immich albums
   - **Impact**: High user frustration from duplicate album suggestions
   - **Fix**: Add `get_all_asset_ids_in_albums()` method in `immich_service.py`, cache results, integrate with exclusion logic

## 🟠 HIGH - Stability & User Trust

These items focus on making the application more robust and the user's actions more predictable.

2. **Suggest Additions to Existing Albums** 🆕
   - **Problem**: Current workflow is "create-only" - no help updating existing albums with new photos
   - **Impact**: Albums become stale; users must manually find and add new photos
   - **Fix**: Create `suggestion_for_addition` type, compare new assets against existing album metadata, UI for "Add N photos to existing album"

3. **Inefficient get_processed_asset_ids Query** 🔄 **ELEVATED FROM LOW**
   - **Problem**: Loads all asset IDs from all suggestions into memory for excluded_ids list
   - **Impact**: Significant memory consumption and slow startup, scaling issues with mature libraries
   - **Fix**: Use timestamp-based filtering instead of giant exclusion lists

## 🟡 MEDIUM - Code Quality & Maintainability

### **NEW MEDIUM PRIORITY ISSUES** 🚨

4. **Service Layer Unit Testing** 🆕
   - **Problem**: The service layer containing all business logic is untested
   - **Impact**: Refactoring is risky; bugs can be introduced easily
   - **Fix**: Introduce pytest and pytest-mock, write unit tests for each service with mocked dependencies

5. **Configuration Schema Validation** 🆕
   - **Problem**: Invalid config.yaml (misspelled keys, wrong data types) leads to NoneType errors at runtime
   - **Impact**: Poor user experience on setup; hard-to-diagnose errors
   - **Fix**: Use Pydantic to define Config model with validation at startup

6. **VLM Provider Plugin Architecture** 🆕
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
1. **Sync with Existing Immich Albums** - Prevents duplicate album suggestions
2. **Suggest Additions to Existing Albums** - Keeps albums current with new photos

### **NEXT SPRINT (High Impact)**  
1. **Service Layer Unit Testing** - Enable safe refactoring
2. **Configuration Schema Validation** - Better setup experience
3. **Inefficient get_processed_asset_ids Query** - Scaling for large libraries
4. **VLM Provider Plugin Architecture** - Multi-provider support

### **FUTURE ENHANCEMENTS**
5. **People-Aware Album Generation** - Leverage face recognition for smarter titles
6. **Interactive Album Refinement** - Photo removal and album splitting tools
7. **Full-Featured Table View** - Enhanced sorting, filtering, bulk operations
8. **Semantic Search-Based Albums** - Text query-driven album creation

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
- Interactive cover photo selection from grid interface
- Comprehensive metadata display (photos, dates, locations, status)
- Professional layout with improved navigation and controls
- Real-time database updates for all editable fields

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