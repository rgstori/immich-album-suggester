# TODO: Immich Album Suggester Improvements

## 🔴 CRITICAL - Security & Safety Issues 

### **ALL CRITICAL ISSUES RESOLVED** ✅

All critical security and safety issues have been addressed:

- **SQL Injection Vulnerabilities** ✅
- **API Key Exposure** ✅  
- **Missing Input Validation** ✅
- **SQL Injection in Schema Migration** ✅ - Fixed with whitelist validation
- **Thread Safety in Singleton Pattern** ✅ - Fixed with double-checked locking
- **Uncontrolled Resource Consumption** ✅ - Added VLM request size validation

## 🟠 HIGH - Stability & Robustness Issues

### **NEW HIGH PRIORITY ISSUES** 🚨

### **PREVIOUSLY COMPLETED** ✅
- UI Auto-Refresh ✅
- Database Connection Leaks ✅
- Zombie Process Risk ✅
- Unbounded Memory Cache ✅
- Database Transaction Atomicity ✅
- Broad Exception Handling in VLM ✅
- Process Cleanup on Shutdown ✅ - Added signal handlers and graceful termination
- VLM Request Size Validation ✅ - Added context window and image size validation

## 🟡 MEDIUM - Code Quality & Maintainability

### **NEW MEDIUM PRIORITY ISSUES** 🚨

7. **Complex Session State Management** 🔄 **PARTIALLY ADDRESSED**
   - `ui.py` session state improved with merge functionality but still complex
   - **Remaining Issues**: 10+ session state variables with some interdependencies
   - **Recent Improvements**: Better unique key management for confirmations, unified selection state
   - **Remaining Fix**: Create a session state management class with clear state transitions

### **PREVIOUSLY COMPLETED** ✅
- **Incomplete Type Hints** ✅ - Added comprehensive type hints to service methods
- **Inconsistent Error Logging** ✅ - Standardized logging throughout codebase  
- **Hardcoded Configuration Values** ✅ - Moved all hardcoded values to config.yaml
- Album View Missing Metadata ✅
- Poor Error Messages ✅
- Inefficient Cache Clearing ✅
- Album Switching Issues ✅
- Enrichment Workflow Problems ✅

## 🟢 LOW - Performance & Enhancement

### **NEW LOW PRIORITY ISSUES** 🚨

10. **Inefficient Database Queries** 🆕
    - `get_processed_asset_ids()` loads all asset IDs into memory
    - **Impact**: High memory usage with large photo libraries
    - **Fix**: Use database-side filtering or pagination

11. **Redundant Thumbnail Requests** 🔄 **PARTIALLY ADDRESSED**
    - Recent improvements: Better caching in table view, reduced duplicate requests
    - **Remaining Issue**: Some edge cases during view transitions
    - **Fix**: Implement request deduplication in caching layer

12. **Missing Graceful Degradation** 🆕
    - UI breaks if VLM service is unavailable
    - **Fix**: Add graceful fallbacks and better error states

13. **No Telemetry/Metrics** 🆕
    - No visibility into system performance or usage patterns
    - **Fix**: Add optional telemetry for clustering performance, VLM response times, etc.

## 🔵 ENHANCEMENT - New Features

### **ARCHITECTURAL IMPROVEMENTS** 🆕

14. **Service Layer Testing** 🆕
    - No unit tests for the new service architecture
    - **Fix**: Add comprehensive test suite for services

15. **Configuration Validation** 🆕
    - No validation that config.yaml contains required fields
    - **Fix**: Add schema validation with helpful error messages

16. **Plugin Architecture for VLM Providers** 🆕
    - Currently hardcoded to Ollama
    - **Enhancement**: Abstract VLM interface to support multiple providers

17. **Album Template System** 🆕
    - Only basic title/description templates
    - **Enhancement**: Rich template system with conditional logic

### **UI ENHANCEMENTS** 🆕

18. **Advanced Table Features** 🆕
    - Additional sortable columns (title, location, status)
    - **Enhancement**: Implement database-level sorting for all columns
    - **Also**: Add filtering/search capabilities to table view

19. **Merge Preview** 🆕
    - Basic merge confirmation implemented
    - **Enhancement**: Visual preview showing combined photo grid before merge
    - **Also**: Undo functionality for recently merged albums

20. **Bulk Status Operations** 🆕
    - Current bulk operations: merge, enrich, delete
    - **Enhancement**: Bulk approve/reject, bulk status changes
    - **Also**: Batch album creation for approved suggestions

## Implementation Priority

### **IMMEDIATE (This Week)** ✅
1. ✅ Fix SQL injection in schema migration (Critical Security)
2. ✅ Implement thread-safe singleton pattern (Critical Safety)
3. ✅ Add VLM request size validation (High Stability)
4. ✅ Implement process cleanup handlers (High Stability)

### **NEXT SPRINT (High Impact)**  
1. ✅ Add comprehensive type hints (Code Quality)
2. ✅ Standardize error logging (Maintainability)  
3. ✅ Move hardcoded values to config (Maintainability)
4. Simplify session state management (Code Quality)

### **FUTURE ENHANCEMENTS**
5. Performance optimizations (database queries, caching)
6. Service layer testing
7. Configuration validation
8. Plugin architecture for VLM providers

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
- SQL injection prevention with whitelist validation in schema migrations
- Thread-safe configuration service with double-checked locking pattern
- VLM request size validation to prevent resource exhaustion
- Process cleanup handlers with graceful shutdown and signal handling

**Code Quality Improvements (v2.3)**
- Comprehensive type hints added to all service methods and core modules
- Standardized logging throughout codebase replacing print() statements
- All hardcoded configuration values moved to config.yaml for maintainability
- Enhanced configuration management with proper defaults and validation

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
- [ ] 100% type hint coverage for public APIs
- [ ] <5 broad exception handlers (`except Exception:`)
- [ ] Zero hardcoded configuration values
- [ ] All database operations in explicit transactions
- [ ] 90%+ test coverage for service layer

The refactoring has significantly improved the architecture, but there are still some critical security and stability issues that need immediate attention, particularly around thread safety and SQL injection in the migration code.