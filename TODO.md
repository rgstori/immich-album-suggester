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

## 🟡 MEDIUM - User Experience Issues

8. **Album View Missing Metadata**
   - Gallery view shows only images without photo metadata
   - **Fix**: Add metadata overlay showing EXIF data for each photo in gallery

9. **Poor Error Messages** ✅ COMPLETED
   - Replaced generic "X" with informative error messages
   - Added retry buttons for failed thumbnail loads
   - Improved logging with specific error details

10. **Inefficient Cache Clearing** ✅ COMPLETED
    - Implemented selective cache invalidation by suggestion ID
    - LRU cache automatically manages memory usage
    - Only clears relevant caches instead of everything

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

## Implementation Priority

### Next Sprint (High Impact)
- [ ] Fix database connection leaks (`immich_db.py`)
- [ ] Add photo metadata display in album gallery view
- [ ] Improve error messages for failed operations

### Future Enhancements
- [ ] Implement LRU cache with size limits
- [ ] Add comprehensive type hints
- [ ] Selective cache invalidation
- [ ] Move hardcoded values to config

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