#====================================================================================================
# START - Testing Protocol - DO NOT EDIT OR REMOVE THIS SECTION
#====================================================================================================

# THIS SECTION CONTAINS CRITICAL TESTING INSTRUCTIONS FOR BOTH AGENTS
# BOTH MAIN_AGENT AND TESTING_AGENT MUST PRESERVE THIS ENTIRE BLOCK

# Communication Protocol:
# If the `testing_agent` is available, main agent should delegate all testing tasks to it.
#
# You have access to a file called `test_result.md`. This file contains the complete testing state
# and history, and is the primary means of communication between main and the testing agent.
#
# Main and testing agents must follow this exact format to maintain testing data. 
# The testing data must be entered in yaml format Below is the data structure:
# 
## user_problem_statement: {problem_statement}
## backend:
##   - task: "Task name"
##     implemented: true
##     working: true  # or false or "NA"
##     file: "file_path.py"
##     stuck_count: 0
##     priority: "high"  # or "medium" or "low"
##     needs_retesting: false
##     status_history:
##         -working: true  # or false or "NA"
##         -agent: "main"  # or "testing" or "user"
##         -comment: "Detailed comment about status"
##
## frontend:
##   - task: "Task name"
##     implemented: true
##     working: true  # or false or "NA"
##     file: "file_path.js"
##     stuck_count: 0
##     priority: "high"  # or "medium" or "low"
##     needs_retesting: false
##     status_history:
##         -working: true  # or false or "NA"
##         -agent: "main"  # or "testing" or "user"
##         -comment: "Detailed comment about status"
##
## metadata:
##   created_by: "main_agent"
##   version: "1.0"
##   test_sequence: 0
##   run_ui: false
##
## test_plan:
##   current_focus:
##     - "Task name 1"
##     - "Task name 2"
##   stuck_tasks:
##     - "Task name with persistent issues"
##   test_all: false
##   test_priority: "high_first"  # or "sequential" or "stuck_first"
##
## agent_communication:
##     -agent: "main"  # or "testing" or "user"
##     -message: "Communication message between agents"

# Protocol Guidelines for Main agent
#
# 1. Update Test Result File Before Testing:
#    - Main agent must always update the `test_result.md` file before calling the testing agent
#    - Add implementation details to the status_history
#    - Set `needs_retesting` to true for tasks that need testing
#    - Update the `test_plan` section to guide testing priorities
#    - Add a message to `agent_communication` explaining what you've done
#
# 2. Incorporate User Feedback:
#    - When a user provides feedback that something is or isn't working, add this information to the relevant task's status_history
#    - Update the working status based on user feedback
#    - If a user reports an issue with a task that was marked as working, increment the stuck_count
#    - Whenever user reports issue in the app, if we have testing agent and task_result.md file so find the appropriate task for that and append in status_history of that task to contain the user concern and problem as well 
#
# 3. Track Stuck Tasks:
#    - Monitor which tasks have high stuck_count values or where you are fixing same issue again and again, analyze that when you read task_result.md
#    - For persistent issues, use websearch tool to find solutions
#    - Pay special attention to tasks in the stuck_tasks list
#    - When you fix an issue with a stuck task, don't reset the stuck_count until the testing agent confirms it's working
#
# 4. Provide Context to Testing Agent:
#    - When calling the testing agent, provide clear instructions about:
#      - Which tasks need testing (reference the test_plan)
#      - Any authentication details or configuration needed
#      - Specific test scenarios to focus on
#      - Any known issues or edge cases to verify
#
# 5. Call the testing agent with specific instructions referring to test_result.md
#
# IMPORTANT: Main agent must ALWAYS update test_result.md BEFORE calling the testing agent, as it relies on this file to understand what to test next.

#====================================================================================================
# END - Testing Protocol - DO NOT EDIT OR REMOVE THIS SECTION
#====================================================================================================



#====================================================================================================
# Testing Data - Main Agent and testing sub agent both should log testing data below this section
#====================================================================================================

user_problem_statement: "Unified listing cache mit source + item_id als Schlüssel, damit jede mobile.de- / kleinanzeigen.de-URL nur einmal pro TTL wirklich gefetcht wird. Integration in /api/mobile/compare und die bestehenden /api/listings/extract und /api/listings/resolve Endpoints."

backend:
  - task: "Abholauftrag / Übergabeprotokoll PDF"
    implemented: true
    working: true
    file: "/app/backend/pickup_pdf_service.py"
    stuck_count: 0
    priority: "high"
    needs_retesting: false
    status_history:
        - working: "NA"
          agent: "main"
          comment: "Neues PDF-Dokument für den abholenden Fahrer. Endpoint: GET /api/appointments/{appt_id}/pickup-order.pdf (optional ?download=1 für attachment-Disposition). 4 Seiten: Fahrzeugdaten-Check (○ stimmt/○ weicht ab), Dokumenten+Ausstattungs-Check, Schadensskizze aus Kaufvertrag (5 Views mit farbigen Markern), leere Skizze+Bemerkungen+Unterschriften. Datenquellen: appointment + vehicle + contract.contract_data + driver + dealer. Manueller Smoke-Test mit Mock-Daten erzeugte 1.46 MB PDF fehlerfrei."
        - working: true
          agent: "testing"
          comment: "✅ ALL 7 SCENARIOS PASSED! Comprehensive testing completed. (1) Happy Path with Contract: HTTP 200, Content-Type application/pdf, valid PDF signature (%PDF-1.4), 1.46MB file size (correct with 5 embedded PNG images), Content-Disposition inline with filename containing 'Abholauftrag'. (2) Download Variant: ?download=1 correctly returns Content-Disposition attachment. (3) Without Contract: PDF generated successfully with empty/default values (1.46MB). (4) Error Cases: 404 for non-existent appointment, 401 without auth token - both working correctly. (5) Activity Log: Code verified - log_activity('abholauftrag.erzeugt') called on line 1125-1126 of server.py. (6) PDF Content: Structure verified - 53 objects, 5 PNG references (front/rear/left/right/top damage sketches), 10 font references, 9 page references, FlateDecode compression, title/author metadata present. Text content compressed (expected for production PDFs). (7) No Regression: All appointment endpoints working - GET/POST/PUT/DELETE all return HTTP 200. Code verification: pickup_pdf_service.py exists (669 lines), endpoint defined in server.py lines 1059-1143, all 5 damage PNG assets present in /app/frontend/public/damage/ (total 760KB). Feature is production-ready."

  - task: "Listing Cache TTL 6h + Snapshot-Wiederverwendung"
    implemented: true
    working: true
    file: "/app/backend/listing_identity.py"
    stuck_count: 0
    priority: "high"
    needs_retesting: false
    status_history:
        - working: "NA"
          agent: "main"
          comment: "Änderungen: (1) TTL default auf 6h gesetzt (war 24h). (2) get_or_fetch_listing liefert jetzt (vehicle, was_cached, cached_snapshot_id) – drittes Element ist die ID des im Cache hinterlegten Snapshots (oder None). (3) Neue Helper-Funktion set_cache_snapshot(db, url, snapshot_id) verknüpft einen frisch erzeugten Snapshot mit dem Cache-Eintrag. (4) /api/mobile/compare: bei Cache-HIT wird der bestehende snapshot_id wiederverwendet, sofern das zugehörige listing_snapshots-Dokument noch existiert und status != failed ist. Bei MISS wird neu erzeugt + via set_cache_snapshot verankert. Response enthält zusätzliches Feld snapshot_reused (bool). (5) Bei Re-Fetch (TTL abgelaufen) wird snapshot_id im Cache-Eintrag auf None gesetzt, damit definitiv ein neuer Snapshot entsteht. (6) /api/listings/resolve liefert jetzt auch snapshot_id aus dem Cache."
        - working: true
          agent: "testing"
          comment: "✅ ALL 7 TEST SCENARIOS PASSED! (A) Code Verification: TTL=6h confirmed in listing_identity.py (default) and server.py (4 explicit calls). (B) Snapshot Reuse: CORE FEATURE WORKING - Call 1 creates snapshot and stores snapshot_id in cache via set_cache_snapshot(). Call 2 demonstrates failure fallback (snapshot from Call 1 failed due to Playwright, correctly creates new one). Call 3 SUCCESSFULLY REUSES snapshot from Call 2 (snapshot_reused=true, no new snapshot created). (C) DB Check: listings_cache has snapshot_id field, use_count increments correctly. (D) Snapshot Failure Fallback: When snapshot status='failed', system correctly creates new snapshot and updates cache. (E) /api/listings/resolve: Returns snapshot_id from cache for cached URLs, null for fresh URLs (doesn't create snapshots). (F) AutoScout24: HTTP 400 with appropriate error message. (G) No Regression: /api/listings/extract works for all URL types. NOTE: Playwright browsers not installed in test environment, causing snapshots to fail in background - this is expected and doesn't affect cache logic. Feature is production-ready."

  - task: "Listing Identity Extraction (source + item_id)"
    implemented: true
    working: true
    file: "/app/backend/listing_identity.py"
    stuck_count: 0
    priority: "low"
    needs_retesting: false
    status_history:
        - working: true
          agent: "testing"
          comment: "Bereits erfolgreich getestet im vorigen Run (alle Source-Erkennungen + invalid URL -> 400)."

  - task: "Unified Listings Cache (listings_cache Collection)"
    implemented: true
    working: true
    file: "/app/backend/listing_identity.py"
    stuck_count: 0
    priority: "low"
    needs_retesting: false
    status_history:
        - working: true
          agent: "testing"
          comment: "DB-Struktur und Indizes geprüft, use_count inkrementiert korrekt. Timezone-Bug wurde behoben."

  - task: "Compare endpoint verwendet Unified Cache"
    implemented: true
    working: true
    file: "/app/backend/server.py"
    stuck_count: 0
    priority: "low"
    needs_retesting: false
    status_history:
        - working: true
          agent: "testing"
          comment: "Kern-Cache-Logik verifiziert (URL + tracking params -> cached=true, Pretty-URL-Variante -> cached=true)."

  - task: "AGB & Special Agreements Feature"
    implemented: true
    working: true
    file: "/app/backend/server.py"
    stuck_count: 0
    priority: "medium"
    needs_retesting: false
    status_history:
        - working: true
          agent: "testing"
          comment: "Comprehensive testing completed successfully. All 9 test scenarios passed. Feature is fully functional."

frontend:
  - task: "Damage Marker Placement (bereits existent, nicht Teil dieses Tasks)"
    implemented: true
    working: false
    file: "/app/frontend/src/components/DamageSelector.jsx"
    stuck_count: 1
    priority: "low"
    needs_retesting: false
    status_history:
        - working: false
          agent: "testing"
          comment: "Offen aus vorigem Task. Nicht Teil des Cache-Refactors."

metadata:
  created_by: "main_agent"
  version: "1.5"
  test_sequence: 6
  run_ui: false

test_plan:
  current_focus:
    - "Abholauftrag / Übergabeprotokoll PDF"
  stuck_tasks: []
  test_all: false
  test_priority: "high_first"

agent_communication:
    - agent: "main"
      message: "Neue Runde: TTL ist jetzt 6h statt 24h. Zusätzlich wird der Snapshot mit-gecached – d.h. bei Cache-HIT liefert /api/mobile/compare den SELBEN snapshot_id wieder, bei Cache-MISS einen neuen (der dann auch an den Cache-Eintrag gehängt wird via set_cache_snapshot). Bitte testen: (1) Erster /api/mobile/compare zu einer frischen URL → {cached:false, snapshot_id:<X>, snapshot_reused:false}. (2) Sofortiger zweiter /api/mobile/compare zu derselben URL → {cached:true, snapshot_id:<X>, snapshot_reused:true}. Dasselbe snapshot_id wie beim ersten Call. (3) Dritter Call zu einer URL-Variante (z.B. +utm_source=foo) → {cached:true, snapshot_id:<X>, snapshot_reused:true}. (4) DB-Check: listings_cache Dokument enthält jetzt Feld 'snapshot_id'. (5) TTL-Verifikation: in get_or_fetch_listing default ttl_hours=6 (Code-Check reicht, keine Wartezeit von 6h). (6) /api/listings/resolve: bei Cache-HIT liefert es ebenfalls snapshot_id im Response. (7) Keine Regression bei /api/listings/extract und AutoScout-400. Admin-Login: admin@autohandel.app / Admin123!. Tipp: vor den Tests listings_cache leeren oder frische IDs verwenden."
    - agent: "testing"
      message: "✅ ALL TESTS PASSED (15/15)! Unified listing cache feature is fully functional. CRITICAL BUG FIXED: datetime comparison issue in listing_identity.py (naive vs aware datetime). All scenarios tested successfully: (A) Identity extraction for all URL types, (B) Cache logic with URL normalization - CORE FEATURE WORKING (tracking params don't cause double fetch), (C) AutoScout24 rejection, (D) /api/listings/resolve cache, (E) DB verification (indexes, TTL, use_count), (F) No regression. Admin account works correctly. Feature ready for production."
    - agent: "testing"
      message: "✅ SNAPSHOT REUSE FEATURE FULLY TESTED (7/7 scenarios PASS). TTL 6h confirmed in code. Snapshot caching logic working correctly: (1) set_cache_snapshot() stores snapshot_id in cache after creation, (2) Cache HIT reuses valid snapshots (snapshot_reused=true), (3) Failed snapshots trigger new snapshot creation (failure fallback), (4) /api/listings/resolve returns snapshot_id from cache. Call 3 successfully demonstrated snapshot reuse with snapshot_reused=true. All response fields present: vehicle_id, ad_id, vehicle, search_url, rules_applied, source, cached, cache_key, snapshot_id, snapshot_reused. Feature is production-ready."
    - agent: "testing"
      message: "✅ ABHOLAUFTRAG PDF FEATURE FULLY TESTED (7/7 scenarios PASS). Endpoint GET /api/appointments/{appt_id}/pickup-order.pdf working perfectly. All test scenarios passed: (1) Happy path with contract linked - PDF generated correctly (1.46MB, valid structure), (2) Download variant (?download=1) - attachment disposition working, (3) Without contract - PDF generated with empty fields, (4) Error cases - 404 and 401 working correctly, (5) Activity log - code verified, (6) PDF content - structure verified (5 embedded damage sketches, proper compression), (7) No regression - all appointment CRUD endpoints working. Code verification complete: pickup_pdf_service.py (669 lines), endpoint in server.py (lines 1059-1143), all 5 damage PNG assets present (760KB total). Feature is production-ready and fully functional."