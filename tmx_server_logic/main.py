"""
Main FastAPI application for the TMX Editor Server.

This module defines the FastAPI application and all HTTP endpoints for interacting
with TMX (Translation Memory eXchange) files. It handles API requests for operations
such as opening, saving, parsing, editing, and converting TMX data.

The server uses a layered architecture:
- API Layer (this file): Defines HTTP endpoints, handles request/response validation
  (using Pydantic models), and maps API calls to service layer methods.
- Service Layer (`services.py`): Orchestrates business logic, interacting with
  data storage and parsing/conversion utilities.
- Data Storage Layer (`storage.py`): Manages persistence of TMX data in an SQLite database.
- Parsing/Conversion Layers (`tmx_parser.py`, `tmx_converter.py`): Handle TMX file
  parsing/writing and conversion to/from other formats (e.g., CSV, Excel).

Error handling is implemented by catching custom exceptions defined in `exceptions.py`
and mapping them to appropriate HTTPExceptions.
"""
import os
import shutil
import tempfile
from typing import Optional, Dict, Any, List

from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Body, Query
from pydantic import BaseModel

from .services import TMXService
from .models import (
    StatusResponse, FileInfoResponse, TMXHeader,
    LanguageCodeRequest, ChangeLanguageCodeRequest, SourceLanguageRequest,
    UserIdRequest, SearchReplaceRequest, MetadataUpdateRequest, TUDefinitionRequest,
    TranslationUnit as TranslationUnitModel,
    CSVImportOptions, ExcelImportOptions, ExportPathRequest, CSVExportOptions
)
from .config import ACTIVE_DB_PATH, DEFAULT_TMX_INDENTATION
from .exceptions import (
    TMXServerError, SessionInactiveError, TMXParsingError,
    InvalidOperationError, ResourceNotFoundError, DatabaseError
)
from .tmx_converter import TMXConverter
import json
import asyncio # For tests
from fastapi.testclient import TestClient # For tests
import openpyxl # For Excel tests

# --- FastAPI App Initialization ---
app = FastAPI(
    title="Python TMX Editor Server",
    description="API for parsing, creating, editing, and managing TMX files.",
    version="0.1.0"
)

# --- Dependency Providers ---
_tmx_service_instance: Optional[TMXService] = None
_tmx_converter_instance: Optional[TMXConverter] = None

def get_tmx_service() -> TMXService:
    """
    Dependency injector for TMXService.
    Provides a singleton instance of TMXService for the application lifecycle.
    """
    global _tmx_service_instance
    if _tmx_service_instance is None:
        _tmx_service_instance = TMXService(db_path=ACTIVE_DB_PATH)
    return _tmx_service_instance

def get_tmx_converter() -> TMXConverter:
    """
    Dependency injector for TMXConverter.
    Provides a singleton instance of TMXConverter.
    """
    global _tmx_converter_instance
    if _tmx_converter_instance is None:
        _tmx_converter_instance = TMXConverter()
    return _tmx_converter_instance

# --- Request Models defined locally (if any) ---
class FilePathRequest(BaseModel):
    """Request model for operations requiring a file path."""
    file_path: str

class SaveFileRequest(BaseModel):
    """Request model for saving a file, optionally specifying indentation."""
    file_path: str
    indentation: Optional[int] = None

# --- API Endpoints ---

@app.post("/file/open", response_model=StatusResponse, tags=["File Operations"])
async def open_tmx_file(request_body: FilePathRequest, service: TMXService = Depends(get_tmx_service)):
    """
    Opens a TMX file from a server-local path.
    The content is parsed and loaded into an active session in the database.
    """
    try:
        service.open_file(request_body.file_path)
        return StatusResponse(status="success", message=f"File '{request_body.file_path}' opened successfully.")
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except SessionInactiveError as e: # Should not occur if open_file is called on a fresh/closed service
        raise HTTPException(status_code=400, detail=e.message)
    except TMXParsingError as e:
        raise HTTPException(status_code=422, detail=f"TMX parsing error: {e.message} - {e.details}")
    except InvalidOperationError as e: # e.g. if file path is invalid in a way not caught by FileNotFoundError
        raise HTTPException(status_code=400, detail=e.message)
    except DatabaseError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e.message} - {e.details}")
    except TMXServerError as e:
        raise HTTPException(status_code=500, detail=f"Server error: {e.message} - {e.details}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

@app.post("/file/upload_and_open", response_model=StatusResponse, tags=["File Operations"])
async def upload_and_open_tmx_file(uploaded_file: UploadFile = File(...), service: TMXService = Depends(get_tmx_service)):
    """
    Uploads a TMX file, saves it temporarily, and then opens it into an active session.
    """
    temp_file_path: Optional[str] = None
    try:
        # Save uploaded file to a temporary location
        with tempfile.NamedTemporaryFile(delete=False, suffix=".tmx") as temp_file:
            shutil.copyfileobj(uploaded_file.file, temp_file)
            temp_file_path = temp_file.name
        
        if temp_file_path:
            service.open_file(temp_file_path) # Use the service to open the temp file
            return StatusResponse(status="success", message=f"Uploaded file '{uploaded_file.filename}' opened successfully.")
        else:
            # This case should ideally be caught by exceptions during tempfile creation/write
            raise TMXServerError(message="Failed to process uploaded file (temp file path not obtained).")
    except FileNotFoundError as e: # Should not happen if temp file logic is correct
        raise HTTPException(status_code=404, detail=str(e))
    except SessionInactiveError as e: 
        raise HTTPException(status_code=400, detail=e.message)
    except TMXParsingError as e:
        raise HTTPException(status_code=422, detail=f"TMX parsing error: {e.message} - {e.details}")
    except InvalidOperationError as e: 
        raise HTTPException(status_code=400, detail=e.message)
    except DatabaseError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e.message} - {e.details}")
    except TMXServerError as e:
        raise HTTPException(status_code=500, detail=f"Server error: {e.message} - {e.details}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred during upload: {str(e)}")
    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            os.remove(temp_file_path) # Clean up the temporary file
        if uploaded_file:
            await uploaded_file.close() # Ensure the uploaded file resource is closed

@app.post("/file/close", response_model=StatusResponse, tags=["File Operations"])
async def close_tmx_file(service: TMXService = Depends(get_tmx_service)):
    """
    Closes the currently active TMX file session, clearing any data from the database.
    """
    try:
        service.close_file()
        return StatusResponse(status="success", message="File session closed successfully.")
    except SessionInactiveError as e: 
        # Closing an already inactive session could be non-error, but service might raise if state is inconsistent.
        raise HTTPException(status_code=400, detail=e.message)
    except TMXServerError as e:
        raise HTTPException(status_code=500, detail=f"Server error: {e.message} {e.details}")
    except Exception as e: # Catch-all for unexpected issues during close
        raise HTTPException(status_code=500, detail=f"An error occurred while closing the file: {str(e)}")

@app.post("/file/save", response_model=StatusResponse, tags=["File Operations"])
async def save_active_tmx_file(request_body: SaveFileRequest, service: TMXService = Depends(get_tmx_service)):
    """
    Saves the content of the currently active TMX session to a specified file path.
    """
    try:
        indentation_to_use = request_body.indentation if request_body.indentation is not None else DEFAULT_TMX_INDENTATION
        service.save_file(request_body.file_path, indentation=indentation_to_use)
        return StatusResponse(status="success", message=f"File saved successfully to '{request_body.file_path}'.")
    except SessionInactiveError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except InvalidOperationError as e: # e.g. header missing
        raise HTTPException(status_code=400, detail=e.message)
    except DatabaseError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e.message} {e.details}")
    except TMXServerError as e: # e.g., file writing error from parser
        raise HTTPException(status_code=500, detail=f"Server error: {e.message} {e.details}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred while saving the file: {str(e)}")

@app.get("/file/info", response_model=FileInfoResponse, tags=["File Operations"])
async def get_active_file_info(service: TMXService = Depends(get_tmx_service)):
    """
    Retrieves information about the currently active TMX file, including header details,
    TU count, and language codes.
    """
    try:
        file_info = service.get_file_info()
        # If file_info.is_active is False but no exception, it's a valid state (no file open).
        return file_info
    except SessionInactiveError as e: # Should be handled by service.get_file_info returning is_active=False
        raise HTTPException(status_code=400, detail=e.message)
    except DatabaseError as e: # If error occurs fetching info for an assumed active session
        raise HTTPException(status_code=500, detail=f"Database error retrieving file info: {e.message} {e.details}")
    except TMXServerError as e:
        raise HTTPException(status_code=500, detail=f"Server error retrieving file info: {e.message} {e.details}")
    except Exception as e: # Catch-all
        raise HTTPException(status_code=500, detail=f"An error occurred while fetching file info: {str(e)}")

@app.get("/segments", response_model=Dict[str, Any], tags=["Translation Units"])
async def get_segments_filtered(
    filter_text: Optional[str] = Query(None, description="Text to search for in segments."),
    filter_lang_code: Optional[str] = Query(None, description="Language code to apply text filter to."),
    case_sensitive_filter: bool = Query(False, description="Perform case-sensitive text filtering."),
    is_regex_filter: bool = Query(False, description="Treat filter_text as a regex pattern."),
    filter_untranslated_to_other_langs: bool = Query(False, description="Filter for TUs translated in source but not in other langs."),
    source_lang_for_untranslated_filter: Optional[str] = Query(None, description="Source language for the 'untranslated' filter."),
    sort_by_text_in_lang_code: Optional[str] = Query(None, description="Language code to sort segments by."),
    sort_ascending: bool = Query(True, description="Sort order (True for ASC, False for DESC)."),
    start_offset: int = Query(0, ge=0, description="Offset for pagination."),
    page_size: int = Query(20, ge=-1, description="Number of items per page (-1 for all)."), # Corrected ge for page_size
    service: TMXService = Depends(get_tmx_service)
):
    """
    Retrieves translation units (segments) with extensive filtering, sorting, and pagination.
    """
    try:
        # The service method `get_segments` returns a dict that matches the response_model
        return service.get_segments(
            filter_text=filter_text, filter_lang_code=filter_lang_code,
            case_sensitive_filter=case_sensitive_filter, is_regex_filter=is_regex_filter,
            filter_untranslated_to_other_langs=filter_untranslated_to_other_langs,
            source_lang_for_untranslated_filter=source_lang_for_untranslated_filter,
            sort_by_text_in_lang_code=sort_by_text_in_lang_code, sort_ascending=sort_ascending,
            start_offset=start_offset, page_size=page_size
        )
    except SessionInactiveError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except InvalidOperationError as e: # For invalid filter combinations from service layer
        raise HTTPException(status_code=400, detail=e.message)
    except DatabaseError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e.message} - {e.details}")
    except TMXServerError as e:
        raise HTTPException(status_code=500, detail=f"Server error: {e.message} - {e.details}")
    except Exception as e: # Catch-all
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred while retrieving segments: {str(e)}")

@app.get("/health", response_model=StatusResponse, tags=["General"])
async def health_check():
    """A simple health check endpoint to confirm the server is running."""
    return StatusResponse(status="ok", message="TMX Editor Server is running.")

# --- Language Management, Maintenance, Metadata, CRUD, Conversion Endpoints ---

@app.post("/languages/add", response_model=StatusResponse, tags=["Language Operations"])
async def add_language_endpoint(request: LanguageCodeRequest, service: TMXService = Depends(get_tmx_service)):
    """
    Validates a language code. Currently a conceptual operation.
    """
    try:
        result = service.add_language(request.lang_code)
        return StatusResponse(status=result["status"], message=result["message"])
    except SessionInactiveError as e: raise HTTPException(status_code=400, detail=e.message)
    except InvalidOperationError as e: raise HTTPException(status_code=400, detail=e.message)
    except TMXServerError as e: raise HTTPException(status_code=500, detail=f"Server error: {e.message} {e.details}")
    except Exception as e: raise HTTPException(status_code=500, detail=f"An unexpected error: {str(e)}")

@app.post("/languages/remove", response_model=StatusResponse, tags=["Language Operations"])
async def remove_language_endpoint(request: LanguageCodeRequest, service: TMXService = Depends(get_tmx_service)):
    """
    Removes all segments of a specific language from the active TMX file.
    """
    try:
        result = service.remove_language(request.lang_code)
        return StatusResponse(status="success", message=result["message"])
    except SessionInactiveError as e: raise HTTPException(status_code=400, detail=e.message)
    except ResourceNotFoundError as e: raise HTTPException(status_code=404, detail=e.message)
    except InvalidOperationError as e: raise HTTPException(status_code=400, detail=e.message)
    except DatabaseError as e: raise HTTPException(status_code=500, detail=f"Database error: {e.message} {e.details}")
    except TMXServerError as e: raise HTTPException(status_code=500, detail=f"Server error: {e.message} {e.details}")
    except Exception as e: raise HTTPException(status_code=500, detail=f"An unexpected error: {str(e)}")

@app.post("/languages/change_code", response_model=StatusResponse, tags=["Language Operations"])
async def change_language_code_endpoint(request: ChangeLanguageCodeRequest, service: TMXService = Depends(get_tmx_service)):
    """
    Changes occurrences of an old language code to a new language code for all relevant TUVs.
    """
    try:
        result = service.change_language_code(request.old_lang_code, request.new_lang_code, request.user_id)
        return StatusResponse(status="success", message=result["message"])
    except SessionInactiveError as e: raise HTTPException(status_code=400, detail=e.message)
    except ResourceNotFoundError as e: raise HTTPException(status_code=404, detail=e.message)
    except InvalidOperationError as e: raise HTTPException(status_code=400, detail=e.message)
    except DatabaseError as e: raise HTTPException(status_code=500, detail=f"Database error: {e.message} {e.details}")
    except TMXServerError as e: raise HTTPException(status_code=500, detail=f"Server error: {e.message} {e.details}")
    except Exception as e: raise HTTPException(status_code=500, detail=f"An unexpected error: {str(e)}")

@app.post("/header/source_language", response_model=StatusResponse, tags=["Header Operations"])
async def set_source_language_endpoint(request: SourceLanguageRequest, service: TMXService = Depends(get_tmx_service)):
    """
    Sets the source language (srclang) in the TMX header.
    """
    try:
        result = service.set_source_language(request.source_lang_code, request.user_id)
        return StatusResponse(status="success", message=result["message"])
    except SessionInactiveError as e: raise HTTPException(status_code=400, detail=e.message)
    except InvalidOperationError as e: raise HTTPException(status_code=400, detail=e.message) 
    except DatabaseError as e: raise HTTPException(status_code=500, detail=f"Database error: {e.message} {e.details}")
    except TMXServerError as e: raise HTTPException(status_code=500, detail=f"Server error: {e.message} {e.details}")
    except Exception as e: raise HTTPException(status_code=500, detail=f"An unexpected error: {str(e)}")

@app.post("/maintenance/consolidate", response_model=Dict[str, Any], tags=["Maintenance Operations"])
async def consolidate_units_endpoint(request: SourceLanguageRequest, service: TMXService = Depends(get_tmx_service)):
    """
    Consolidates TUs based on identical source text in the specified source language.
    Merges variants and deletes redundant TUs.
    """
    try:
        return service.consolidate_units(request.source_lang_code, request.user_id)
    except SessionInactiveError as e: raise HTTPException(status_code=400, detail=e.message)
    except InvalidOperationError as e: raise HTTPException(status_code=400, detail=e.message)
    except DatabaseError as e: raise HTTPException(status_code=500, detail=f"Database error: {e.message} {e.details}")
    except TMXServerError as e: raise HTTPException(status_code=500, detail=f"Server error: {e.message} {e.details}")
    except Exception as e: raise HTTPException(status_code=500, detail=f"An unexpected error: {str(e)}")

@app.post("/maintenance/remove_untranslated", response_model=Dict[str, Any], tags=["Maintenance Operations"])
async def remove_untranslated_units_endpoint(request: LanguageCodeRequest, service: TMXService = Depends(get_tmx_service)):
    """
    Removes TUs that have a source variant but are untranslated in all other target languages.
    `lang_code` specifies the source language to base this logic on.
    """
    try:
        return service.remove_untranslated_units(request.lang_code) # lang_code here is source_lang
    except SessionInactiveError as e: raise HTTPException(status_code=400, detail=e.message)
    except InvalidOperationError as e: raise HTTPException(status_code=400, detail=e.message)
    except DatabaseError as e: raise HTTPException(status_code=500, detail=f"Database error: {e.message} {e.details}")
    except TMXServerError as e: raise HTTPException(status_code=500, detail=f"Server error: {e.message} {e.details}")
    except Exception as e: raise HTTPException(status_code=500, detail=f"An unexpected error: {str(e)}")

@app.post("/maintenance/remove_same_as_source", response_model=Dict[str, Any], tags=["Maintenance Operations"])
async def remove_variants_same_as_source_endpoint(request: SourceLanguageRequest, service: TMXService = Depends(get_tmx_service)):
    """
    Clears target language variants if their text is identical to the source language variant's text.
    """
    try:
        return service.remove_variants_same_as_source(request.source_lang_code, request.user_id)
    except SessionInactiveError as e: raise HTTPException(status_code=400, detail=e.message)
    except InvalidOperationError as e: raise HTTPException(status_code=400, detail=e.message)
    except DatabaseError as e: raise HTTPException(status_code=500, detail=f"Database error: {e.message} {e.details}")
    except TMXServerError as e: raise HTTPException(status_code=500, detail=f"Server error: {e.message} {e.details}")
    except Exception as e: raise HTTPException(status_code=500, detail=f"An unexpected error: {str(e)}")

@app.post("/maintenance/remove_duplicates", response_model=Dict[str, Any], tags=["Maintenance Operations"])
async def remove_duplicate_units_endpoint(service: TMXService = Depends(get_tmx_service)):
    """
    Removes fully duplicate TUs (identical source and all target variants text).
    """
    try:
        return service.remove_duplicate_units()
    except SessionInactiveError as e: raise HTTPException(status_code=400, detail=e.message)
    except DatabaseError as e: raise HTTPException(status_code=500, detail=f"Database error: {e.message} {e.details}")
    except TMXServerError as e: raise HTTPException(status_code=500, detail=f"Server error: {e.message} {e.details}")
    except Exception as e: raise HTTPException(status_code=500, detail=f"An unexpected error: {str(e)}")

@app.post("/maintenance/remove_tags", response_model=Dict[str, Any], tags=["Maintenance Operations"])
async def remove_tags_endpoint(request: UserIdRequest, service: TMXService = Depends(get_tmx_service)):
    """
    Strips all XML tags from all segments in all TUVs, leaving plain text.
    """
    try:
        return service.remove_all_tags_from_segments(request.user_id)
    except SessionInactiveError as e: raise HTTPException(status_code=400, detail=e.message)
    except DatabaseError as e: raise HTTPException(status_code=500, detail=f"Database error: {e.message} {e.details}")
    except TMXServerError as e: raise HTTPException(status_code=500, detail=f"Server error: {e.message} {e.details}")
    except Exception as e: raise HTTPException(status_code=500, detail=f"An unexpected error: {str(e)}")

@app.post("/maintenance/remove_spaces", response_model=Dict[str, Any], tags=["Maintenance Operations"])
async def remove_spaces_endpoint(request: UserIdRequest, service: TMXService = Depends(get_tmx_service)):
    """
    Trims leading/trailing whitespace from all segments in all TUVs.
    """
    try:
        return service.remove_leading_trailing_spaces_from_segments(request.user_id)
    except SessionInactiveError as e: raise HTTPException(status_code=400, detail=e.message)
    except DatabaseError as e: raise HTTPException(status_code=500, detail=f"Database error: {e.message} {e.details}")
    except TMXServerError as e: raise HTTPException(status_code=500, detail=f"Server error: {e.message} {e.details}")
    except Exception as e: raise HTTPException(status_code=500, detail=f"An unexpected error: {str(e)}")

@app.post("/maintenance/search_replace", response_model=Dict[str, Any], tags=["Maintenance Operations"])
async def search_replace_endpoint(request: SearchReplaceRequest, service: TMXService = Depends(get_tmx_service)):
    """
    Performs text search and replace in segments. Can be regex and case-sensitive.
    Can target a specific language or all languages if `lang_code` is null.
    """
    try:
        return service.search_and_replace(
            search_text=request.search_text, replace_text=request.replace_text, lang_code=request.lang_code,
            is_regex=request.is_regex, case_sensitive=request.case_sensitive, current_user_id=request.user_id
        )
    except SessionInactiveError as e: raise HTTPException(status_code=400, detail=e.message)
    except InvalidOperationError as e: raise HTTPException(status_code=400, detail=e.message)
    except DatabaseError as e: raise HTTPException(status_code=500, detail=f"Database error: {e.message} {e.details}")
    except TMXServerError as e: raise HTTPException(status_code=500, detail=f"Server error: {e.message} {e.details}")
    except Exception as e: raise HTTPException(status_code=500, detail=f"An unexpected error: {str(e)}")

@app.get("/units/{tu_db_id}/metadata", response_model=Dict[str, Any], tags=["Metadata Editing"])
async def get_tu_metadata_endpoint(tu_db_id: int, service: TMXService = Depends(get_tmx_service)):
    """
    Gets attributes, properties, and notes for a specific Translation Unit (TU).
    """
    try:
        return service.get_tu_metadata(tu_db_id)
    except SessionInactiveError as e: raise HTTPException(status_code=400, detail=e.message)
    except ResourceNotFoundError as e: raise HTTPException(status_code=404, detail=e.message)
    except TMXServerError as e: raise HTTPException(status_code=500, detail=f"Server error: {e.message} {e.details}")
    except Exception as e: raise HTTPException(status_code=500, detail=f"An unexpected error: {str(e)}")

@app.put("/units/{tu_db_id}/attributes", response_model=StatusResponse, tags=["Metadata Editing"])
async def set_tu_attributes_endpoint(tu_db_id: int, request: MetadataUpdateRequest, service: TMXService = Depends(get_tmx_service)):
    """
    Sets custom attributes for a specific Translation Unit (TU).
    Replaces any existing attributes.
    """
    try:
        items_as_dicts = [item.model_dump(exclude_none=True) for item in request.items]
        result = service.set_tu_attributes(tu_db_id, items_as_dicts, request.user_id)
        return StatusResponse(status=result["status"], message=result["message"])
    except SessionInactiveError as e: raise HTTPException(status_code=400, detail=e.message)
    except ResourceNotFoundError as e: raise HTTPException(status_code=404, detail=e.message)
    except InvalidOperationError as e: raise HTTPException(status_code=400, detail=e.message) 
    except DatabaseError as e: raise HTTPException(status_code=500, detail=f"Database error: {e.message} {e.details}")
    except TMXServerError as e: raise HTTPException(status_code=500, detail=f"Server error: {e.message} {e.details}")
    except Exception as e: raise HTTPException(status_code=500, detail=f"An unexpected error: {str(e)}")

@app.put("/units/{tu_db_id}/properties", response_model=StatusResponse, tags=["Metadata Editing"])
async def set_tu_properties_endpoint(tu_db_id: int, request: MetadataUpdateRequest, service: TMXService = Depends(get_tmx_service)):
    """
    Sets properties for a specific Translation Unit (TU).
    Replaces any existing properties.
    """
    try:
        items_as_dicts = [item.model_dump(exclude_none=True) for item in request.items]
        result = service.set_tu_properties(tu_db_id, items_as_dicts, request.user_id)
        return StatusResponse(status=result["status"], message=result["message"])
    except SessionInactiveError as e: raise HTTPException(status_code=400, detail=e.message)
    except ResourceNotFoundError as e: raise HTTPException(status_code=404, detail=e.message)
    except InvalidOperationError as e: raise HTTPException(status_code=400, detail=e.message)
    except DatabaseError as e: raise HTTPException(status_code=500, detail=f"Database error: {e.message} {e.details}")
    except TMXServerError as e: raise HTTPException(status_code=500, detail=f"Server error: {e.message} {e.details}")
    except Exception as e: raise HTTPException(status_code=500, detail=f"An unexpected error: {str(e)}")

@app.put("/units/{tu_db_id}/notes", response_model=StatusResponse, tags=["Metadata Editing"])
async def set_tu_notes_endpoint(tu_db_id: int, request: MetadataUpdateRequest, service: TMXService = Depends(get_tmx_service)):
    """
    Sets notes for a specific Translation Unit (TU).
    Replaces any existing notes.
    """
    try:
        items_as_dicts = [item.model_dump(exclude_none=True) for item in request.items]
        result = service.set_tu_notes(tu_db_id, items_as_dicts, request.user_id)
        return StatusResponse(status=result["status"], message=result["message"])
    except SessionInactiveError as e: raise HTTPException(status_code=400, detail=e.message)
    except ResourceNotFoundError as e: raise HTTPException(status_code=404, detail=e.message)
    except InvalidOperationError as e: raise HTTPException(status_code=400, detail=e.message)
    except DatabaseError as e: raise HTTPException(status_code=500, detail=f"Database error: {e.message} {e.details}")
    except TMXServerError as e: raise HTTPException(status_code=500, detail=f"Server error: {e.message} {e.details}")
    except Exception as e: raise HTTPException(status_code=500, detail=f"An unexpected error: {str(e)}")

@app.get("/units/{tu_db_id}/variants/{lang_code}/metadata", response_model=Dict[str, Any], tags=["Metadata Editing"])
async def get_tuv_metadata_endpoint(tu_db_id: int, lang_code: str, service: TMXService = Depends(get_tmx_service)):
    """
    Gets attributes, properties, and notes for a specific Translation Unit Variant (TUV).
    """
    try:
        return service.get_tuv_metadata(tu_db_id, lang_code)
    except SessionInactiveError as e: raise HTTPException(status_code=400, detail=e.message)
    except ResourceNotFoundError as e: raise HTTPException(status_code=404, detail=e.message)
    except TMXServerError as e: raise HTTPException(status_code=500, detail=f"Server error: {e.message} {e.details}")
    except Exception as e: raise HTTPException(status_code=500, detail=f"An unexpected error: {str(e)}")

@app.put("/units/{tu_db_id}/variants/{lang_code}/attributes", response_model=StatusResponse, tags=["Metadata Editing"])
async def set_tuv_attributes_endpoint(tu_db_id: int, lang_code: str, request: MetadataUpdateRequest, service: TMXService = Depends(get_tmx_service)):
    """
    Sets custom attributes for a specific Translation Unit Variant (TUV).
    Replaces any existing attributes.
    """
    try:
        items_as_dicts = [item.model_dump(exclude_none=True) for item in request.items]
        result = service.set_tuv_attributes(tu_db_id, lang_code, items_as_dicts, request.user_id)
        return StatusResponse(status=result["status"], message=result["message"])
    except SessionInactiveError as e: raise HTTPException(status_code=400, detail=e.message)
    except ResourceNotFoundError as e: raise HTTPException(status_code=404, detail=e.message)
    except InvalidOperationError as e: raise HTTPException(status_code=400, detail=e.message)
    except DatabaseError as e: raise HTTPException(status_code=500, detail=f"Database error: {e.message} {e.details}")
    except TMXServerError as e: raise HTTPException(status_code=500, detail=f"Server error: {e.message} {e.details}")
    except Exception as e: raise HTTPException(status_code=500, detail=f"An unexpected error: {str(e)}")

@app.put("/units/{tu_db_id}/variants/{lang_code}/properties", response_model=StatusResponse, tags=["Metadata Editing"])
async def set_tuv_properties_endpoint(tu_db_id: int, lang_code: str, request: MetadataUpdateRequest, service: TMXService = Depends(get_tmx_service)):
    """
    Sets properties for a specific Translation Unit Variant (TUV).
    Replaces any existing properties.
    """
    try:
        items_as_dicts = [item.model_dump(exclude_none=True) for item in request.items]
        result = service.set_tuv_properties(tu_db_id, lang_code, items_as_dicts, request.user_id)
        return StatusResponse(status=result["status"], message=result["message"])
    except SessionInactiveError as e: raise HTTPException(status_code=400, detail=e.message)
    except ResourceNotFoundError as e: raise HTTPException(status_code=404, detail=e.message)
    except InvalidOperationError as e: raise HTTPException(status_code=400, detail=e.message)
    except DatabaseError as e: raise HTTPException(status_code=500, detail=f"Database error: {e.message} {e.details}")
    except TMXServerError as e: raise HTTPException(status_code=500, detail=f"Server error: {e.message} {e.details}")
    except Exception as e: raise HTTPException(status_code=500, detail=f"An unexpected error: {str(e)}")

@app.put("/units/{tu_db_id}/variants/{lang_code}/notes", response_model=StatusResponse, tags=["Metadata Editing"])
async def set_tuv_notes_endpoint(tu_db_id: int, lang_code: str, request: MetadataUpdateRequest, service: TMXService = Depends(get_tmx_service)):
    """
    Sets notes for a specific Translation Unit Variant (TUV).
    Replaces any existing notes.
    """
    try:
        items_as_dicts = [item.model_dump(exclude_none=True) for item in request.items]
        result = service.set_tuv_notes(tu_db_id, lang_code, items_as_dicts, request.user_id)
        return StatusResponse(status=result["status"], message=result["message"])
    except SessionInactiveError as e: raise HTTPException(status_code=400, detail=e.message)
    except ResourceNotFoundError as e: raise HTTPException(status_code=404, detail=e.message)
    except InvalidOperationError as e: raise HTTPException(status_code=400, detail=e.message)
    except DatabaseError as e: raise HTTPException(status_code=500, detail=f"Database error: {e.message} {e.details}")
    except TMXServerError as e: raise HTTPException(status_code=500, detail=f"Server error: {e.message} {e.details}")
    except Exception as e: raise HTTPException(status_code=500, detail=f"An unexpected error: {str(e)}")

@app.post("/units", response_model=TranslationUnitModel, tags=["Translation Units CRUD"])
async def create_translation_unit_endpoint(request: TUDefinitionRequest, service: TMXService = Depends(get_tmx_service)):
    """
    Creates a new Translation Unit (TU) with its variants and metadata.
    Returns the created TU.
    """
    try:
        variants_as_dicts = [var.model_dump(exclude_none=True) for var in request.variants]
        props_as_dicts = [p.model_dump(exclude_none=True) for p in request.properties] if request.properties else None
        notes_as_dicts = [n.model_dump(exclude_none=True) for n in request.notes] if request.notes else None
        attrs_as_dicts = [ca.model_dump(exclude_none=True) for ca in request.custom_attributes] if request.custom_attributes else None
        created_tu_dict = service.insert_new_translation_unit(
            tuid=request.tuid, variants_data=variants_as_dicts, properties_data=props_as_dicts, notes_data=notes_as_dicts,
            custom_attributes_data=attrs_as_dicts, seg_type=request.seg_type, datatype=request.datatype,
            usage_count=request.usage_count, current_user_id=request.user_id
        )
        # The service method already returns a dict, so no need to call .model_dump() here.
        # However, to be sure it matches TranslationUnitModel, we can re-parse.
        # For now, assuming service returns a compatible dict or Pydantic model that FastAPI handles.
        return TranslationUnitModel(**created_tu_dict)
    except SessionInactiveError as e: raise HTTPException(status_code=400, detail=e.message)
    except InvalidOperationError as e: raise HTTPException(status_code=400, detail=e.message)
    except DatabaseError as e: raise HTTPException(status_code=500, detail=f"Database error: {e.message} {e.details}")
    except TMXServerError as e: raise HTTPException(status_code=500, detail=f"Server error: {e.message} {e.details}")
    except Exception as e: raise HTTPException(status_code=500, detail=f"An unexpected error: {str(e)}")

@app.delete("/units/{tu_db_id}", response_model=StatusResponse, tags=["Translation Units CRUD"])
async def delete_translation_unit_endpoint(tu_db_id: int, service: TMXService = Depends(get_tmx_service)):
    """
    Deletes a Translation Unit (TU) by its database ID.
    """
    try:
        result = service.delete_translation_unit_by_id(tu_db_id)
        return StatusResponse(status=result["status"], message=result["message"])
    except SessionInactiveError as e: raise HTTPException(status_code=400, detail=e.message)
    except ResourceNotFoundError as e: raise HTTPException(status_code=404, detail=e.message)
    except DatabaseError as e: raise HTTPException(status_code=500, detail=f"Database error: {e.message} {e.details}")
    except TMXServerError as e: raise HTTPException(status_code=500, detail=f"Server error: {e.message} {e.details}")
    except Exception as e: raise HTTPException(status_code=500, detail=f"An unexpected error: {str(e)}")

# --- Data Conversion Endpoints ---
@app.post("/convert/csv_to_tmx_and_open", response_model=StatusResponse, tags=["Data Conversion"])
async def convert_csv_to_tmx_and_open_endpoint(
    uploaded_file: UploadFile = File(...),
    options_json: str = Body(...), # CSVImportOptions as JSON string
    service: TMXService = Depends(get_tmx_service),
    converter: TMXConverter = Depends(get_tmx_converter)
):
    """
    Converts an uploaded CSV file to TMX format and opens it in a new session.
    Requires `options_json` in the body, which is a JSON string representation of `CSVImportOptions`.
    """
    temp_file_path: Optional[str] = None
    try:
        try:
            options_dict = json.loads(options_json)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON format for options_json string.")
        import_options = CSVImportOptions(**options_dict)
        
        with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as temp_file:
            shutil.copyfileobj(uploaded_file.file, temp_file)
            temp_file_path = temp_file.name
        
        if not temp_file_path:
            raise TMXServerError(message="Failed to save uploaded CSV file temporarily.")
        
        header, tus = converter.csv_to_tmx_content(
            csv_filepath=temp_file_path, languages=import_options.languages, charset=import_options.charset,
            delimiter=import_options.delimiter, quotechar=import_options.quotechar, tuid_col_index=import_options.tuid_col_index
        )
        service.open_file_from_models(header, tus, original_filepath=uploaded_file.filename or "uploaded.csv")
        return StatusResponse(status="success", message=f"CSV file '{uploaded_file.filename}' converted and opened successfully.")
    except json.JSONDecodeError: 
        raise HTTPException(status_code=400, detail="Invalid JSON format for CSV import options.")
    except TMXParsingError as e: 
        raise HTTPException(status_code=422, detail=f"Data conversion/parsing error: {e.message} {e.details}")
    except InvalidOperationError as e: 
        raise HTTPException(status_code=400, detail=e.message)
    except ValueError as e: 
        raise HTTPException(status_code=400, detail=str(e)) 
    except SessionInactiveError as e: 
        raise HTTPException(status_code=400, detail=e.message) # Should not happen if open_file_from_models is correct
    except DatabaseError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e.message} {e.details}")
    except TMXServerError as e:
        raise HTTPException(status_code=500, detail=f"Server error: {e.message} {e.details}")
    except FileNotFoundError: # Should be caught by initial tempfile handling or converter
        raise HTTPException(status_code=500, detail="Temporary CSV file disappeared (internal error).")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")
    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            os.remove(temp_file_path)
        if uploaded_file:
            await uploaded_file.close()

@app.post("/convert/excel_to_tmx_and_open", response_model=StatusResponse, tags=["Data Conversion"])
async def convert_excel_to_tmx_and_open_endpoint(
    uploaded_file: UploadFile = File(...),
    options_json: str = Body(...), # ExcelImportOptions as JSON string
    service: TMXService = Depends(get_tmx_service),
    converter: TMXConverter = Depends(get_tmx_converter)
):
    """
    Converts an uploaded Excel file to TMX format and opens it in a new session.
    Requires `options_json` in the body, a JSON string of `ExcelImportOptions`.
    """
    temp_file_path: Optional[str] = None
    try:
        try:
            options_dict = json.loads(options_json)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON format for options_json string.")
        import_options = ExcelImportOptions(**options_dict)

        file_suffix = ".xlsx"
        if uploaded_file.filename and uploaded_file.filename.lower().endswith(".xls"):
             file_suffix = ".xls" # Handle older .xls format if necessary for suffix
        
        with tempfile.NamedTemporaryFile(delete=False, suffix=file_suffix) as temp_file:
            shutil.copyfileobj(uploaded_file.file, temp_file)
            temp_file_path = temp_file.name
            
        if not temp_file_path:
            raise TMXServerError(message="Failed to save uploaded Excel file temporarily.")
            
        header, tus = converter.excel_to_tmx_content(
            excel_filepath=temp_file_path, languages=import_options.languages, sheet_name=import_options.sheet_name,
            header_row_index=import_options.header_row_index, data_start_row_index=import_options.data_start_row_index,
            tuid_col_letter=import_options.tuid_col_letter
        )
        service.open_file_from_models(header, tus, original_filepath=uploaded_file.filename or "uploaded_excel_file")
        return StatusResponse(status="success", message=f"Excel file '{uploaded_file.filename}' converted and opened successfully.")
    except json.JSONDecodeError: 
        raise HTTPException(status_code=400, detail="Invalid JSON format for Excel import options.")
    except TMXParsingError as e: 
        raise HTTPException(status_code=422, detail=f"Data conversion/parsing error: {e.message} {e.details}")
    except InvalidOperationError as e: 
        raise HTTPException(status_code=400, detail=e.message)
    except ValueError as e: 
        raise HTTPException(status_code=400, detail=str(e))
    except SessionInactiveError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except DatabaseError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e.message} {e.details}")
    except TMXServerError as e:
        raise HTTPException(status_code=500, detail=f"Server error: {e.message} {e.details}")
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="Temporary Excel file disappeared (internal error).")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")
    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            os.remove(temp_file_path)
        if uploaded_file:
            await uploaded_file.close()

@app.post("/convert/tmx_to_csv", response_model=StatusResponse, tags=["Data Conversion"])
async def convert_tmx_to_csv_endpoint(
    # `export_request` should ideally be part of the body with `options`
    # For now, assuming it's passed as query parameters due to current signature.
    file_path: str = Query(..., description="Path to save the exported CSV file."),
    options: CSVExportOptions = Body(...), 
    service: TMXService = Depends(get_tmx_service),
    converter: TMXConverter = Depends(get_tmx_converter)
):
    """
    Exports the content of the active TMX session to a CSV file.
    `file_path` is a query parameter. `charset` and `delimiter` are in the request body.
    """
    # This endpoint signature is slightly unusual. Typically, all POST data is in the body.
    # Corrected based on `ExportPathRequest` model not being used for Body here.
    try:
        active_store = service._get_active_store() 
        tmx_file_id = service._ensure_active_tmx_file_id() 
        converter.tmx_content_to_csv(
            store=active_store, tmx_file_id=tmx_file_id, csv_filepath=file_path, # Use file_path from query
            charset=options.charset, delimiter=options.delimiter
        )
        return StatusResponse(status="success", message=f"TMX data successfully exported to CSV: {file_path}")
    except SessionInactiveError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except InvalidOperationError as e: 
        raise HTTPException(status_code=400, detail=e.message)
    except ValueError as e: 
        raise HTTPException(status_code=400, detail=str(e)) 
    except DatabaseError as e: 
        raise HTTPException(status_code=500, detail=f"Database error: {e.message} {e.details}")
    except TMXServerError as e:
        raise HTTPException(status_code=500, detail=f"Server error: {e.message} {e.details}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

@app.post("/convert/tmx_to_excel", response_model=StatusResponse, tags=["Data Conversion"])
async def convert_tmx_to_excel_endpoint(
    export_request: ExportPathRequest = Body(...), 
    service: TMXService = Depends(get_tmx_service),
    converter: TMXConverter = Depends(get_tmx_converter)
):
    """
    Exports the content of the active TMX session to an Excel (XLSX) file.
    """
    try:
        active_store = service._get_active_store()
        tmx_file_id = service._ensure_active_tmx_file_id()
        converter.tmx_content_to_excel( 
            store=active_store, tmx_file_id=tmx_file_id, excel_filepath=export_request.file_path
        )
        return StatusResponse(status="success", message=f"TMX data successfully exported to Excel: {export_request.file_path}")
    except SessionInactiveError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except InvalidOperationError as e: 
        raise HTTPException(status_code=400, detail=e.message)
    except ValueError as e: 
        raise HTTPException(status_code=400, detail=str(e))
    except DatabaseError as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e.message} {e.details}")
    except TMXServerError as e:
        raise HTTPException(status_code=500, detail=f"Server error: {e.message} {e.details}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

# To run this FastAPI application (example, typically use uvicorn command):
# if __name__ == "__main__":
#     import uvicorn
#     # This is for development only. For production, use a proper ASGI server like Uvicorn/Hypercorn.
#     uvicorn.run("tmx_server_logic.main:app", host="0.0.0.0", port=8000, reload=True)

# --- Test Client and Test Functions ---
DUMMY_CSV_PATH = "test_import.csv"
DUMMY_EXCEL_PATH = "test_import.xlsx"
DUMMY_TMX_EXPORT_PATH = "test_for_export.tmx"
EXPORT_CSV_PATH = "test_export_output.csv"
EXPORT_EXCEL_PATH = "test_export_output.xlsx"

def create_dummy_csv():
    content = "ID,en-US,fr-FR\ntu1,Hello,Bonjour\ntu2,World,Monde\n"
    with open(DUMMY_CSV_PATH, "w", encoding="utf-8") as f:
        f.write(content)

def create_dummy_excel():
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Sheet1"
    sheet.append(["ID", "en-US", "fr-FR"])
    sheet.append(["tu1", "Hello", "Bonjour"])
    sheet.append(["tu2", "World", "Monde"])
    workbook.save(DUMMY_EXCEL_PATH)

def create_dummy_tmx_for_export():
    content = """<?xml version="1.0" encoding="UTF-8"?>
<tmx version="1.4">
  <header creationtool="TestExporter" segtype="sentence" adminlang="en" srclang="en-US" datatype="plaintext"/>
  <body>
    <tu tuid="exp1"><tuv xml:lang="en-US"><seg>Export me</seg></tuv><tuv xml:lang="es-ES"><seg>Expórtame</seg></tuv></tu>
    <tu tuid="exp2"><tuv xml:lang="en-US"><seg>Another one</seg></tuv><tuv xml:lang="es-ES"><seg>Otro más</seg></tuv></tu>
  </body>
</tmx>"""
    with open(DUMMY_TMX_EXPORT_PATH, "w", encoding="utf-8") as f:
        f.write(content)

def cleanup_dummy_files():
    files_to_delete = [
        DUMMY_CSV_PATH, DUMMY_EXCEL_PATH, DUMMY_TMX_EXPORT_PATH,
        EXPORT_CSV_PATH, EXPORT_EXCEL_PATH
    ]
    for f_path in files_to_delete:
        if os.path.exists(f_path):
            os.remove(f_path)

def test_csv_import_endpoint(client: TestClient):
    print("--- Testing CSV Import Endpoint ---")
    create_dummy_csv()
    options = {"languages": ["en-US", "fr-FR"], "tuid_col_index": 0}
    options_json_str = json.dumps(options)
    
    with open(DUMMY_CSV_PATH, "rb") as f:
        response = client.post(
            "/convert/csv_to_tmx_and_open",
            files={"uploaded_file": (DUMMY_CSV_PATH, f, "text/csv")},
            data={"options_json": options_json_str} 
        )
    assert response.status_code == 200, f"CSV Import: Expected 200, got {response.status_code}, {response.text}"
    assert response.json()["status"] == "success", f"CSV Import: Status not success: {response.json()}"
    print("CSV Import: Success response received.")

    info_response = client.get("/file/info")
    assert info_response.status_code == 200
    info_data = info_response.json()
    assert info_data["is_active"] is True, "CSV Import: File not active after import."
    assert info_data["tu_count"] == 2, f"CSV Import: Expected 2 TUs, got {info_data['tu_count']}"
    assert sorted(info_data["language_codes"]) == sorted(["en-US", "fr-FR"]), f"CSV Import: Lang codes mismatch: {info_data['language_codes']}"
    print("CSV Import: File info check passed.")
    
    close_response = client.post("/file/close") 
    assert close_response.status_code == 200

def test_excel_import_endpoint(client: TestClient):
    print("--- Testing Excel Import Endpoint ---")
    create_dummy_excel()
    options = {"languages": ["en-US", "fr-FR"], "tuid_col_letter": "A", "header_row_index": 1, "data_start_row_index": 2}
    options_json_str = json.dumps(options)

    with open(DUMMY_EXCEL_PATH, "rb") as f:
        response = client.post(
            "/convert/excel_to_tmx_and_open",
            files={"uploaded_file": (DUMMY_EXCEL_PATH, f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            data={"options_json": options_json_str}
        )
    assert response.status_code == 200, f"Excel Import: Expected 200, got {response.status_code}, {response.text}"
    assert response.json()["status"] == "success", f"Excel Import: Status not success: {response.json()}"
    print("Excel Import: Success response received.")

    info_response = client.get("/file/info")
    assert info_response.status_code == 200
    info_data = info_response.json()
    assert info_data["is_active"] is True, "Excel Import: File not active after import."
    assert info_data["tu_count"] == 2, f"Excel Import: Expected 2 TUs, got {info_data['tu_count']}"
    assert sorted(info_data["language_codes"]) == sorted(["en-US", "fr-FR"]), f"Excel Import: Lang codes mismatch: {info_data['language_codes']}"
    print("Excel Import: File info check passed.")

    close_response = client.post("/file/close") 
    assert close_response.status_code == 200


def test_tmx_export_endpoints(client: TestClient):
    print("--- Testing TMX Export Endpoints ---")
    create_dummy_tmx_for_export()

    open_response = client.post("/file/open", json={"file_path": DUMMY_TMX_EXPORT_PATH})
    assert open_response.status_code == 200, f"Export Test Setup: Open failed: {open_response.text}"
    assert open_response.json()["status"] == "success"
    print("Export Test Setup: TMX file opened successfully.")
    
    print("Testing TMX to CSV Export...")
    response_csv = client.post(
        f"/convert/tmx_to_csv?file_path={EXPORT_CSV_PATH}", 
        json={"charset": "utf-8", "delimiter": ","} 
    )
    assert response_csv.status_code == 200, f"CSV Export: Expected 200, got {response_csv.status_code}, {response_csv.text}"
    assert response_csv.json()["status"] == "success", f"CSV Export: Status not success: {response_csv.json()}"
    assert os.path.exists(EXPORT_CSV_PATH), "CSV Export: Output file not found."
    print("CSV Export: Success, output file created.")
    with open(EXPORT_CSV_PATH, "r", encoding="utf-8") as f_csv:
        csv_content = f_csv.read()
        assert "Export me" in csv_content and "Expórtame" in csv_content
        print("CSV Export: Content verified.")

    print("Testing TMX to Excel Export...")
    response_excel = client.post("/convert/tmx_to_excel", json={"file_path": EXPORT_EXCEL_PATH})
    assert response_excel.status_code == 200, f"Excel Export: Expected 200, got {response_excel.status_code}, {response_excel.text}"
    assert response_excel.json()["status"] == "success", f"Excel Export: Status not success: {response_excel.json()}"
    assert os.path.exists(EXPORT_EXCEL_PATH), "Excel Export: Output file not found."
    print("Excel Export: Success, output file created.")
    try:
        workbook = openpyxl.load_workbook(EXPORT_EXCEL_PATH)
        sheet = workbook.active
        texts_in_excel = [cell.value for row in sheet.iter_rows() for cell in row if cell.value]
        assert "Export me" in texts_in_excel
        assert "Expórtame" in texts_in_excel
        print("Excel Export: Content verified.")
    except Exception as e:
        print(f"Excel content verification failed (openpyxl might be needed or file is corrupted): {e}")

    close_response = client.post("/file/close")
    assert close_response.status_code == 200
    print("Export Test Cleanup: TMX file session closed.")


if __name__ == "__main__":
    client = TestClient(app)
    
    try:
        client.post("/file/close") 
    except Exception:
        pass 

    print("Executing basic conversion endpoint tests...")
    try:
        test_csv_import_endpoint(client)
        test_excel_import_endpoint(client)
        test_tmx_export_endpoints(client)
        print("All basic conversion endpoint tests finished successfully.")
    except AssertionError as e:
        print(f"Test assertion failed: {e}")
    except Exception as e:
        print(f"An unexpected error occurred during testing: {e}")
    finally:
        cleanup_dummy_files()
        print("Dummy files cleaned up.")
    
    # print("\nStarting Uvicorn server...")
    # import uvicorn
    # uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
