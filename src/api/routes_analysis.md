# API Routes Analysis and Consolidation Plan

## Current State: 111 Routes with 4x Redundancy Each = 444 Route Registrations!

## Consolidation Plan: Reduce to ~25 Essential Routes

### Core CRUD Operations (5 routes)
```
GET    /api/prompt_manager/prompts        - List all prompts
POST   /api/prompt_manager/prompts        - Create new prompt
GET    /api/prompt_manager/prompts/{id}   - Get specific prompt
PUT    /api/prompt_manager/prompts/{id}   - Update prompt
DELETE /api/prompt_manager/prompts/{id}   - Delete prompt
```

### Image Management (4 routes)
```
GET    /api/prompt_manager/images         - List images
GET    /api/prompt_manager/images/{id}    - Get specific image
POST   /api/prompt_manager/images/link    - Link image to prompt
DELETE /api/prompt_manager/images/{id}    - Delete image
```

### Settings & Configuration (2 routes)
```
GET    /api/prompt_manager/settings       - Get all settings
PUT    /api/prompt_manager/settings       - Update settings
```

### System Management (5 routes)
```
GET    /api/prompt_manager/health         - Health check
POST   /api/prompt_manager/backup         - Create backup
POST   /api/prompt_manager/restore        - Restore from backup
POST   /api/prompt_manager/vacuum         - Database optimization
GET    /api/prompt_manager/stats          - System statistics
```

### Migration (3 routes)
```
GET    /api/prompt_manager/migration/check   - Check for v1 database
POST   /api/prompt_manager/migration/execute - Execute migration
GET    /api/prompt_manager/migration/status  - Migration status
```

### Web UI (1 route - SPA)
```
GET    /prompt_manager/web/*              - Serve web UI (catch-all for SPA)
```

### Search & Filtering (2 routes)
```
GET    /api/prompt_manager/search         - Search prompts
GET    /api/prompt_manager/duplicates     - Find duplicate prompts
```

### Collections (3 routes)
```
GET    /api/prompt_manager/collections    - List collections
POST   /api/prompt_manager/collections    - Create collection
DELETE /api/prompt_manager/collections/{id} - Delete collection
```

## Routes to REMOVE:
- All 4x redundant registrations (keep only /api/prompt_manager/* pattern)
- Separate HTML endpoints (gallery.html, stats.html, etc.) - use SPA instead
- Test endpoints in production
- Redundant admin/dashboard/web endpoints (consolidate to one)
- Excessive logging endpoints (8 routes just for logs!)

## Implementation Strategy:
1. Single API prefix: `/api/prompt_manager/`
2. Web UI served from: `/prompt_manager/web/`
3. RESTful design patterns
4. Proper HTTP methods (GET for read, POST for create, PUT for update, DELETE for delete)
5. No redundant registrations