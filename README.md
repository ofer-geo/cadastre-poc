# Cadastre POC — Land Parcel Management System

A proof-of-concept for managing land parcels with full version history and a draft/approve workflow, built on **PostgreSQL + PostGIS** and **QGIS**.

---

## Overview

This POC demonstrates a cadastral system where:

- Every parcel change is preserved as a historical record (no data is ever deleted)
- Editors submit draft changes (create / modify / retire) for manager review
- Managers approve or reject drafts — approval atomically retires the old version and activates the new one
- A QGIS plugin provides map-based tools for both workflows

---

## Tech Stack

| Layer | Technology |
|---|---|
| Database | PostgreSQL 18 + PostGIS |
| GIS Client | QGIS 3.x + PyQGIS plugin |

---

## Repository Structure

```
├── cadaster_schema.sql          # Full database schema (run this first)
├── initial_cadastre_poc.shp     # Seed shapefile — 3 parcels, EPSG:4326
├── initial_cadastre_poc.*       # Shapefile sidecar files
└── qgis_plugin/
    └── cadastre_history_viewer/ # QGIS plugin (all three tools)
```

---

## Database Schema

Schema name: `cadaster`

### Tables

| Table | Description |
|---|---|
| `users` | Editors and managers (role: `editor` / `manager`) |
| `parcels` | All parcel versions, active and historical. Active = `valid_to IS NULL` |
| `draft_parcels` | Staging area for submitted changes. Status: `pending` / `approved` / `rejected` |

### Views

| View | Description |
|---|---|
| `active_parcels` | Currently active parcel versions — use this as the main QGIS layer |
| `parcel_history` | All versions of all parcels with `version_status` column |
| `pending_drafts` | Pending drafts joined with submitter username |

### Key constraints

- **No-overlap**: a partial unique index on `parcels(parcel_id) WHERE valid_to IS NULL` prevents two active versions of the same parcel existing simultaneously.

### Functions

| Function | Description |
|---|---|
| `approve_draft(draft_id, manager_id)` | Atomically retires the current active version and activates the draft |
| `reject_draft(draft_id, manager_id, review_notes)` | Marks the draft rejected |

---

## Setup

### 1. Database

Create a PostgreSQL database and run the schema:

```bash
psql -U postgres -d your_database -f cadaster_schema.sql
```

The script:
- Enables PostGIS
- Creates all tables, views, and functions
- Seeds 2 users (`admin_manager` / `field_editor`) and 3 parcels from the shapefile

### 2. QGIS Plugin

Copy the plugin folder to your QGIS plugins directory:

| Platform | Path |
|---|---|
| Windows | `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\` |
| macOS | `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/` |
| Linux | `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/` |

Then in QGIS: **Plugins → Manage and Install Plugins → Installed → enable "Cadastre History Viewer"**

> The plugin requires `psycopg2`, which is bundled with QGIS on all platforms.

### 3. Load the map layer

In QGIS, add a PostGIS layer:
- Connection: your PostgreSQL server
- Table: `cadaster.active_parcels`

This is the live layer the tools work against.

---

## QGIS Plugin — Three Tools

All tools appear in the **Cadastre** toolbar and menu.

### History Viewer
**Icon:** green parcel grid

Click a parcel on the map to open a popup showing all versions:
- Active version highlighted in **light green**
- Historical versions in **grey**
- Click a row to highlight that version's geometry on the map
- Click again to deselect

### Submit Draft
**Icon:** parcel grid + pencil

Editor workflow for proposing changes:

| Action | Steps |
|---|---|
| **Modify** | Select "Modify" → click a parcel on the map → edit attributes → Submit |
| **Retire** | Select "Retire" → click a parcel on the map → Submit |
| **Create New** | Select "Create New" → draw polygon (left-click to add points, right-click or Enter to finish) → fill attributes → Submit |

### Review Drafts
**Icon:** parcel grid + green ✚ / red ✕

Manager workflow for approving or rejecting submissions:
- Filter by status: **Pending / Approved / Rejected / All**
- Click a row to highlight the draft geometry on the map
  - **Orange** = proposed new state
  - **Blue** = current active state (modify/retire only)
- Add optional review notes, then **Approve** or **Reject**
- After approval the active layer refreshes automatically

---

## Versioning Model

```
parcels table (all versions)
─────────────────────────────────────────────────────────
gid  parcel_id  owner        valid_from    valid_to    
1    1          Unknown      2026-01-01    2026-05-26   ← historical
2    1          John Smith   2026-05-27    NULL         ← active
3    2          Unknown      2026-05-27    NULL         ← active
4    3          Unknown      2026-05-27    NULL         ← active
```

A `valid_to IS NULL` row is the current active version. Approving a modify draft sets `valid_to = today` on the existing active row and inserts a new row with `valid_to = NULL`.

---

## Seed Data

Two users are created automatically:

| Username | Role |
|---|---|
| `admin_manager` | manager |
| `field_editor` | editor |

Three parcels are loaded from `initial_cadastre_poc.shp` (small area in West Africa, EPSG:4326). Parcel 1 has two versions to demonstrate the history viewer.
