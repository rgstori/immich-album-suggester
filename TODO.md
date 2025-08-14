# TODO: Immich Album Suggester Improvements

## 🔴 CRITICAL - Security Issues ✅ ALL COMPLETED

1. **SQL Injection Vulnerabilities** ✅
   - Added whitelist validation for table/column names in `ui.py` and `immich_db.py`
   - Parameterized queries with input validation

2. **API Key Exposure** ✅
   - Removed API keys from debug logs in `immich_api.py`
   - Removed database credentials from logs in `immich_db.py`
   - Implemented secure logging with Python `logging` module

3. **Missing Input Validation** ✅
   - Added validation for suggestion IDs and scan modes in subprocess calls
   - Whitelisted allowed inputs to prevent injection

## 🟠 HIGH - Stability & Resource Issues ✅ ALL COMPLETED

4. **UI Auto-Refresh** ✅ COMPLETED
   - Implemented smart polling: 2s when active, 10s when idle
   - Auto-refresh on scan/enrichment completion with toast notifications
   - Maintains current album view/page during refresh

5. **Database Connection Leaks** ✅ COMPLETED
   - Fixed connection cleanup in `fetch_assets` function
   - Added proper try/finally blocks and context managers
   - All database connections now properly closed

6. **Zombie Process Risk** ✅ COMPLETED
   - Implemented proper subprocess cleanup with `subprocess.PIPE`
   - Added exception handling for process creation
   - Auto-cleanup of completed processes

7. **Unbounded Memory Cache** ✅ COMPLETED
   - Implemented LRU cache with 50MB size limit
   - Thread-safe cache with automatic eviction
   - Suggestion-specific cache clearing

## 🟡 MEDIUM - User Experience Issues ✅ ALL COMPLETED

8. **Album View Missing Metadata** ✅ COMPLETED
   - Added comprehensive metadata display (photos, dates, locations, status)
   - Implemented interactive cover photo selection
   - Added editable titles and descriptions

9. **Poor Error Messages** ✅ COMPLETED
   - Replaced generic "X" with informative error messages
   - Added retry buttons for failed thumbnail loads
   - Improved logging with specific error details

10. **Inefficient Cache Clearing** ✅ COMPLETED
    - Implemented selective cache invalidation by suggestion ID
    - LRU cache automatically manages memory usage
    - Only clears relevant caches instead of everything

11. **Album Switching Issues** ✅ COMPLETED
    - Fixed misbehavior when switching between albums
    - Added proper loading states and progress indicators
    - Eliminated race conditions with dedicated switching logic

12. **Enrichment Workflow Problems** ✅ COMPLETED
    - Fixed albums disappearing from sidebar during enrichment
    - Added status-aware UI with appropriate controls per state
    - Enhanced feedback when enriching currently viewed album

## 🟢 LOW - Code Quality & Maintenance

11. **Broad Exception Handling**
    - Catch-all exception blocks hide specific errors
    - **Fix**: Use specific exception types

12. **Missing Type Hints**
    - No type annotations throughout codebase
    - **Fix**: Add comprehensive type hints

13. **Configuration Hardcoding**
    - Some values still hardcoded despite config.yaml
    - **Fix**: Move remaining hardcoded values to configuration

## NEW - Enhanced UI Features ✅ ALL COMPLETED

### **Decision-Making Interface Improvements**
- [x] **Thumbnail Previews** - Added preview images to suggestion list sidebar
- [x] **Editable Titles** - Made album names editable in both stage 1 and stage 2  
- [x] **Comprehensive Metadata** - Display photo counts, dates, locations, status
- [x] **Interactive Cover Selection** - Choose cover photo from grid of options
- [x] **Enhanced Album Editor** - Full editing interface with all metadata
- [x] **Smart Caching** - Optimized thumbnail loading with LRU cache
- [x] **Professional UI Layout** - Clean, organized interface design

## Implementation Priority

### Next Sprint (High Impact)
- [ ] **Photo Preview Grid** - Show 3-5 representative photos in sidebar for quick assessment
- [ ] **Date Range Display** - Show start/end dates for multi-day events  
- [ ] **Quick Bulk Actions** - "Approve All", "Reject All" buttons for batch processing
- [ ] **Confidence Scores** - Display clustering/AI confidence levels
- [ ] **Keyboard Shortcuts** - Add hotkeys for approve/reject workflow (A/R keys)
- [ ] **Duplicate Detection** - Flag potential duplicate albums from different clustering runs
- [ ] **Enrichment Progress** - Real-time progress tracking for VLM analysis
- [ ] **Batch Enrichment Queue** - Queue system for multiple enrichments with progress

### Future Enhancements  
- [ ] Add comprehensive type hints
- [ ] Smart sorting by confidence/quality scores
- [ ] Keyboard shortcuts for fast approve/reject workflow
- [ ] Export/import album decisions and metadata

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

**UI Behavior & State Management Fixes**
- Fixed album switching issue - can now open any album from sidebar
- Fixed delete all pending button with proper SQL query and feedback
- Resolved text input interference with button clicks using callbacks
- Improved state management consistency across all UI interactions

**Docker Environment & Subprocess Execution**
- Fixed subprocess execution for Docker containerized environment
- Enhanced error handling and debugging for process failures
- Added comprehensive debug information panel
- Improved process output capture and display for troubleshooting
- Docker-friendly script execution with proper PYTHONPATH setup

**Album Switching & Loading State Management**
- Fixed album view misbehavior when switching between albums
- Implemented proper loading states with progress indicators
- Added `switch_to_album()` function to prevent race conditions
- Enhanced thumbnail loading with real-time progress feedback
- Eliminated unnecessary reruns during photo selection
- Added photo counts to "Review Additional Photos" section
- Optimized weak asset selection with callback-based state management

**Enrichment Workflow Fixes (Latest)**
- Fixed enrichment behavior when album is currently open
- Albums now remain visible in sidebar during enrichment with proper status
- Added status-aware main album view with different interfaces per state
- Enhanced enrichment feedback for currently viewed albums
- Implemented proper handling of `pending_enrichment` → `enriching` → `pending` transitions
- Added real-time status indicators combining database and process states