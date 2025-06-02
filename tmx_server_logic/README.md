# TMX Server Logic

This directory contains the Python-based backend server for TMXEditor, designed to handle the core logic of TMX file parsing, manipulation, storage, and conversion. It is built using FastAPI.

## Architecture

The server follows a typical layered architecture:

-   **`main.py` (API Layer):** Defines the FastAPI application, exposes HTTP endpoints for various TMX operations, and handles request/response validation using Pydantic models.
-   **`services.py` (Service Layer):** Contains the `TMXService` class, which orchestrates the business logic. It acts as an intermediary between the API layer and the data/parser layers. It manages an active TMX file session.
-   **`storage.py` (Data Access Layer):** Implements `SQLiteStore` for persisting TMX data (header, translation units, variants) into an SQLite database for the duration of an active editing session.
-   **`models.py` (Data Models):** Defines Pydantic models for TMX data structures (e.g., `TMXHeader`, `TranslationUnit`, `TranslationUnitVariant`) and API request/response payloads.
-   **`tmx_parser.py` (Parsing Layer):** Includes `TMXParser` responsible for parsing TMX files into Pydantic models and writing these models back to TMX files.
-   **`tmx_converter.py` (Conversion Layer):** Provides `TMXConverter` for converting TMX data to/from other formats like CSV and Excel.

## Key Functionalities & API Endpoints

The server provides a comprehensive set of functionalities for TMX data management:

### 1. File Operations
   -   **`POST /file/open`**: Opens a TMX file from a server-local path into the active session.
   -   **`POST /file/upload_and_open`**: Uploads a TMX file and opens it into the active session.
   -   **`POST /file/close`**: Closes the currently active TMX file session.
   -   **`POST /file/save`**: Saves the active TMX session content to a specified file path.
   -   **`GET /file/info`**: Retrieves information about the currently active TMX file.

### 2. Translation Unit (Segment) Retrieval
   -   **`GET /segments`**: Retrieves translation units with extensive filtering (text, language, untranslated status), sorting, and pagination capabilities.

### 3. Language Operations
   -   **`POST /languages/add`**: Validates and conceptually 'adds' a language to the session (primarily for validation).
   -   **`POST /languages/remove`**: Removes all segments of a specific language.
   -   **`POST /languages/change_code`**: Changes occurrences of an old language code to a new one.
   *   **`POST /header/source_language`**: Sets the source language in the TMX header.

### 4. Maintenance Operations
   -   **`POST /maintenance/consolidate`**: Consolidates TUs based on identical source text.
   -   **`POST /maintenance/remove_untranslated`**: Removes TUs untranslated in target languages.
   -   **`POST /maintenance/remove_same_as_source`**: Clears target variants identical to the source.
   -   **`POST /maintenance/remove_duplicates`**: Removes fully duplicate TUs.
   -   **`POST /maintenance/remove_tags`**: Strips XML tags from all segments.
   -   **`POST /maintenance/remove_spaces`**: Trims leading/trailing whitespace from segments.
   -   **`POST /maintenance/search_replace`**: Performs text search and replace in segments.

### 5. Metadata Editing
   -   **`GET /units/{tu_db_id}/metadata`**: Gets attributes, properties, and notes for a TU.
   -   **`PUT /units/{tu_db_id}/attributes`**: Sets custom attributes for a TU.
   -   **`PUT /units/{tu_db_id}/properties`**: Sets properties for a TU.
   -   **`PUT /units/{tu_db_id}/notes`**: Sets notes for a TU.
   -   **`GET /units/{tu_db_id}/variants/{lang_code}/metadata`**: Gets attributes, properties, and notes for a TUV.
   -   **`PUT /units/{tu_db_id}/variants/{lang_code}/attributes`**: Sets custom attributes for a TUV.
   -   **`PUT /units/{tu_db_id}/variants/{lang_code}/properties`**: Sets properties for a TUV.
   -   **`PUT /units/{tu_db_id}/variants/{lang_code}/notes`**: Sets notes for a TUV.

### 6. Translation Unit CRUD
   -   **`POST /units`**: Creates a new Translation Unit.
   -   **`DELETE /units/{tu_db_id}`**: Deletes a Translation Unit.

### 7. Data Conversion (To be fully exposed via API)
   The `TMXConverter` class supports:
   -   CSV to TMX content.
   -   Excel to TMX content.
   -   TMX content to CSV.
   -   TMX content to Excel.
   (Endpoints for these are planned).

### 8. Health Check
   -   **`GET /health`**: A simple health check endpoint.


## Setup and Running

1.  **Prerequisites:**
    *   Python 3.8+
    *   Poetry (for dependency management, recommended) or pip.

2.  **Installation (using Poetry):**
    ```bash
    # Navigate to the root of the TMXEditor project
    # Assuming pyproject.toml and poetry.lock are set up for this sub-project
    # or you initialize poetry within tmx_server_logic/
    cd tmx_server_logic 
    poetry install
    ```
    **Installation (using pip):**
    ```bash
    # Navigate to tmx_server_logic/
    cd tmx_server_logic
    # Ensure requirements.txt is generated or dependencies are listed
    pip install fastapi uvicorn pydantic lxml python-multipart openpyxl
    ```

3.  **Running the Server:**
    The server uses Uvicorn as the ASGI server.
    ```bash
    # From the tmx_server_logic/ directory
    uvicorn main:app --reload --host 0.0.0.0 --port 8000
    ```
    Or, if running from the parent directory of `tmx_server_logic`:
    ```bash
    python -m uvicorn tmx_server_logic.main:app --reload --host 0.0.0.0 --port 8000
    ```
    The server will be accessible at `http://localhost:8000`. API documentation (Swagger UI) is available at `http://localhost:8000/docs`.

## Areas for Future Completion/Improvement

*   **Expose `TMXConverter` via API:** Add FastAPI endpoints in `main.py` to make CSV/Excel import/export functionalities accessible.
*   **Configuration Management:** Move settings like database paths, default indentation, etc., to a configuration file or environment variables instead of being hardcoded (e.g. `tmx_editor_main.db` in `main.py` vs `tmx_editor_active.db` in `services.py`).
*   **Comprehensive Testing:** Expand unit and integration tests for all services and endpoints.
*   **User Authentication/Authorization:** For a production environment, secure the API endpoints.
*   **`original_position` Handling:** Ensure the `original_position` of Translation Units is robustly managed across all operations, especially creation, deletion, and consolidation, if strict ordering is critical.
*   **Error Handling:** Enhance error handling and provide more detailed error responses for some edge cases.
*   **Asynchronous Operations:** For potentially long-running tasks (e.g., processing very large files), consider implementing asynchronous task handling (e.g., using Celery).
*   **TUV ID in Metadata Updates:** While currently functional, the service layer's TUV metadata updates (`_set_tuv_generic_metadata`) interact with `storage.update_tuv_field`. The original plan mentioned a potential `tuv_id` lookup. Current implementation seems to bypass this by using `tu_db_id` and `lang_code`. This should be confirmed to be robust for all metadata types.
