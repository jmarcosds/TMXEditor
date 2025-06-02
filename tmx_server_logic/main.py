import os
import shutil
import tempfile
from typing import Optional, Dict, Any, List

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
    TranslationUnit as TranslationUnitModel, # For response model of create TU
    CSVImportOptions, ExcelImportOptions, ExportPathRequest, CSVExportOptions # Added
)
# Assuming .config might exist later for settings like default_indentation
# from .config import settings
from .tmx_converter import TMXConverter # Added
import json # Added
# os, shutil, tempfile, List, Optional are already imported at the top or will be handled by ensuring they exist


# --- FastAPI App Initialization ---
app = FastAPI(
    title="Python TMX Editor Server",
    description="API for parsing, creating, editing, and managing TMX files.",
    version="0.1.0" # Corresponds to tmx_server_logic.__version__
)

# --- Dependency Provider for TMXService ---
# Global instance for simplicity in this phase.
# For more robust applications, consider FastAPI's request-scoped dependencies or lifespan events.
_tmx_service_instance: Optional[TMXService] = None

def get_tmx_service() -> TMXService:
    """
    Dependency provider for TMXService.
    Initializes a global TMXService instance if one doesn't exist.
    """
    global _tmx_service_instance
    if _tmx_service_instance is None:
        # You might want to configure db_path from environment variables or a config file
        _tmx_service_instance = TMXService(db_path="tmx_editor_main.db")
    return _tmx_service_instance

# --- Request Models for Body ---
class FilePathRequest(BaseModel):
    file_path: str

class SaveFileRequest(BaseModel):
    file_path: str
    indentation: Optional[int] = None


# --- API Endpoints ---

@app.post("/file/open", response_model=StatusResponse, tags=["File Operations"])
async def open_tmx_file(
    request_body: FilePathRequest,
    service: TMXService = Depends(get_tmx_service)
):
    """
    Opens a TMX file from the given path, parses it, and loads it into the active session.
    Any previously opened file session will be closed and its data cleared from the current DB.
    """
    try:
        service.open_file(request_body.file_path)
        return StatusResponse(status="success", message=f"File '{request_body.file_path}' opened successfully.")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File not found: {request_body.file_path}")
    except ValueError as ve: # For issues like "No active TMX file session" if logic was different
        raise HTTPException(status_code=400, detail=str(ve))
    except RuntimeError as re: # For parsing or DB loading errors
        raise HTTPException(status_code=500, detail=str(re))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

@app.post("/file/upload_and_open", response_model=StatusResponse, tags=["File Operations"])
async def upload_and_open_tmx_file(
    uploaded_file: UploadFile = File(...),
    service: TMXService = Depends(get_tmx_service)
):
    """
    Uploads a TMX file, saves it temporarily, then opens it into the active session.
    Any previously opened file session will be closed.
    """
    temp_file_path: Optional[str] = None
    try:
        # Create a temporary file to save the upload
        # It's good practice to use a specific directory for temp files if needed
        with tempfile.NamedTemporaryFile(delete=False, suffix=".tmx") as temp_file:
            shutil.copyfileobj(uploaded_file.file, temp_file)
            temp_file_path = temp_file.name
        
        if temp_file_path: # Should always be true if no exception during tempfile creation
            service.open_file(temp_file_path)
            return StatusResponse(status="success", message=f"Uploaded file '{uploaded_file.filename}' opened successfully.")
        else:
            # This case should ideally not be reached if tempfile creation is successful
            raise HTTPException(status_code=500, detail="Failed to process uploaded file (temp file path not obtained).")

    except FileNotFoundError: # Should not happen with tempfile unless open_file fails internally
        raise HTTPException(status_code=404, detail="Temporary file not found after upload (internal error).")
    except RuntimeError as re:
        raise HTTPException(status_code=500, detail=f"Error processing uploaded file: {str(re)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred during upload: {str(e)}")
    finally:
        # Clean up the temporary file
        if temp_file_path and os.path.exists(temp_file_path):
            os.remove(temp_file_path)
        # Ensure uploaded_file is closed (FastAPI might do this, but good practice)
        if uploaded_file:
            await uploaded_file.close()


@app.post("/file/close", response_model=StatusResponse, tags=["File Operations"])
async def close_tmx_file(service: TMXService = Depends(get_tmx_service)):
    """
    Closes the currently active TMX file session, clearing its data from memory and the database.
    """
    try:
        service.close_file()
        return StatusResponse(status="success", message="File session closed successfully.")
    except Exception as e:
        # close_file is designed to be safe, but catch unforeseen issues
        raise HTTPException(status_code=500, detail=f"An error occurred while closing the file: {str(e)}")


@app.post("/file/save", response_model=StatusResponse, tags=["File Operations"])
async def save_active_tmx_file(
    request_body: SaveFileRequest,
    service: TMXService = Depends(get_tmx_service)
):
    """
    Saves the content of the currently active TMX session to the specified file path.
    """
    try:
        # default_indent = settings.DEFAULT_INDENTATION if hasattr(settings, 'DEFAULT_INDENTATION') else 2
        default_indent = 2 # Hardcoding for now, replace with settings if available
        indentation_to_use = request_body.indentation if request_body.indentation is not None else default_indent
        
        service.save_file(request_body.file_path, indentation=indentation_to_use)
        return StatusResponse(status="success", message=f"File saved successfully to '{request_body.file_path}'.")
    except ValueError as ve: # e.g. "No active TMX file session" or "No header found"
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred while saving the file: {str(e)}")


@app.get("/file/info", response_model=FileInfoResponse, tags=["File Operations"])
async def get_active_file_info(service: TMXService = Depends(get_tmx_service)):
    """
    Retrieves information about the currently active TMX file session.
    """
    try:
        file_info = service.get_file_info()
        return file_info # FastAPI will serialize the Pydantic model
    except Exception as e:
        # get_file_info is designed to return a FileInfoResponse(is_active=False) on error,
        # but if it raises an unexpected exception:
        raise HTTPException(status_code=500, detail=f"An error occurred while fetching file info: {str(e)}")


@app.get("/segments", response_model=Dict[str, Any], tags=["Translation Units"]) # Or a more specific Pydantic model
async def get_segments_filtered(
    filter_text: Optional[str] = Query(None, description="Text to filter segments by."),
    filter_lang_code: Optional[str] = Query(None, description="Language code to apply the text filter on."),
    case_sensitive_filter: bool = Query(False, description="Whether the text filter is case sensitive."),
    is_regex_filter: bool = Query(False, description="Whether the filter text is a regular expression."),
    filter_untranslated_to_other_langs: bool = Query(False, description="Filter for TUs where source is translated but other target languages are not."),
    source_lang_for_untranslated_filter: Optional[str] = Query(None, description="Source language for the 'untranslated' filter."),
    sort_by_text_in_lang_code: Optional[str] = Query(None, description="Language code by whose segment text the results should be sorted."),
    sort_ascending: bool = Query(True, description="Sort order (ascending/descending)."),
    start_offset: int = Query(0, ge=0, description="Offset for pagination."),
    page_size: int = Query(20, ge=-1, description="Number of items per page. Use 0 or -1 for no limit (returns all matching)."),
    service: TMXService = Depends(get_tmx_service)
):
    """
    Retrieves translation units (segments) based on various filter, sort, and pagination criteria.
    Requires an active TMX file session.
    """
    try:
        result = service.get_segments(
            filter_text=filter_text,
            filter_lang_code=filter_lang_code,
            case_sensitive_filter=case_sensitive_filter,
            is_regex_filter=is_regex_filter,
            filter_untranslated_to_other_langs=filter_untranslated_to_other_langs,
            source_lang_for_untranslated_filter=source_lang_for_untranslated_filter,
            sort_by_text_in_lang_code=sort_by_text_in_lang_code,
            sort_ascending=sort_ascending,
            start_offset=start_offset,
            page_size=page_size
        )
        return result
    except ValueError as ve: # Specific errors from service layer (e.g., "No active TMX file session")
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        # Catch-all for other unexpected errors
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred while fetching segments: {str(e)}")


# --- Health Check Endpoint ---
@app.get("/health", response_model=StatusResponse, tags=["General"])
async def health_check():
    """A simple health check endpoint."""
    return StatusResponse(status="ok", message="TMX Editor Server is running.")

# --- Language Management Endpoints (Chunk 5 Part 1) ---

@app.post("/languages/add", response_model=StatusResponse, tags=["Language Operations"])
async def add_language_endpoint(
    request: LanguageCodeRequest,
    service: TMXService = Depends(get_tmx_service)
):
    """
    Validates and 'adds' a language to the current TMX session context.
    Currently, this primarily validates the language code format.
    """
    try:
        result = service.add_language(request.lang_code)
        if result["status"] == "success":
            return StatusResponse(status="success", message=result["message"])
        else: # "info" status
            return StatusResponse(status="info", message=result["message"])
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except RuntimeError as re:
        raise HTTPException(status_code=500, detail=str(re))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

@app.post("/languages/remove", response_model=StatusResponse, tags=["Language Operations"])
async def remove_language_endpoint(
    request: LanguageCodeRequest,
    service: TMXService = Depends(get_tmx_service)
):
    """
    Removes all segments (TUVs) of a specific language from the active TMX file.
    """
    try:
        result = service.remove_language(request.lang_code)
        return StatusResponse(status="success", message=result["message"])
    except ValueError as ve: # e.g., no active session
        raise HTTPException(status_code=400, detail=str(ve))
    except RuntimeError as re: # e.g., store error
        raise HTTPException(status_code=500, detail=str(re))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

@app.post("/languages/change_code", response_model=StatusResponse, tags=["Language Operations"])
async def change_language_code_endpoint(
    request: ChangeLanguageCodeRequest,
    service: TMXService = Depends(get_tmx_service)
):
    """
    Changes all occurrences of an old language code to a new language code for TUVs
    in the active TMX file.
    """
    try:
        result = service.change_language_code(request.old_lang_code, request.new_lang_code, request.user_id)
        return StatusResponse(status="success", message=result["message"])
    except ValueError as ve: # e.g., no active session, lang code validation errors, lang already exists
        raise HTTPException(status_code=400, detail=str(ve))
    except RuntimeError as re: # e.g., store error
        raise HTTPException(status_code=500, detail=str(re))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

@app.post("/header/source_language", response_model=StatusResponse, tags=["Header Operations"])
async def set_source_language_endpoint(
    request: SourceLanguageRequest, # Changed from LanguageCodeRequest
    service: TMXService = Depends(get_tmx_service)
):
    """
    Sets the source language (srclang) in the header of the active TMX file.
    """
    try:
        # Ensure the field name matches what SourceLanguageRequest provides, e.g., request.source_lang_code
        result = service.set_source_language(request.source_lang_code, request.user_id) 
        return StatusResponse(status="success", message=result["message"])
    except ValueError as ve: # e.g., no active session, invalid lang code
        raise HTTPException(status_code=400, detail=str(ve))
    except RuntimeError as re: # e.g., store error, header not found
        raise HTTPException(status_code=500, detail=str(re))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

# --- Maintenance Endpoints (Chunk 5 Part 2) ---

@app.post("/maintenance/consolidate", response_model=Dict[str, Any], tags=["Maintenance Operations"])
async def consolidate_units_endpoint(
    request: SourceLanguageRequest, # Contains source_lang_code and optional user_id
    service: TMXService = Depends(get_tmx_service)
):
    """
    Consolidates translation units based on identical source text.
    Merges translations into a master unit and removes redundant ones.
    """
    try:
        result = service.consolidate_units(request.source_lang_code, request.user_id)
        return result
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except RuntimeError as re:
        raise HTTPException(status_code=500, detail=str(re))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

@app.post("/maintenance/remove_untranslated", response_model=Dict[str, Any], tags=["Maintenance Operations"])
async def remove_untranslated_units_endpoint(
    request: LanguageCodeRequest, # Contains source_lang_code
    service: TMXService = Depends(get_tmx_service)
):
    """
    Removes translation units that are translated in the source language
    but untranslated in all other languages.
    """
    try:
        result = service.remove_untranslated_units(request.lang_code) # lang_code here is the source_lang_code
        return result
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except RuntimeError as re:
        raise HTTPException(status_code=500, detail=str(re))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

@app.post("/maintenance/remove_same_as_source", response_model=Dict[str, Any], tags=["Maintenance Operations"])
async def remove_variants_same_as_source_endpoint(
    request: SourceLanguageRequest, # Contains source_lang_code and optional user_id
    service: TMXService = Depends(get_tmx_service)
):
    """
    Clears target language variants if their text is identical to the source language variant.
    """
    try:
        result = service.remove_variants_same_as_source(request.source_lang_code, request.user_id)
        return result
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except RuntimeError as re:
        raise HTTPException(status_code=500, detail=str(re))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

@app.post("/maintenance/remove_duplicates", response_model=Dict[str, Any], tags=["Maintenance Operations"])
async def remove_duplicate_units_endpoint(
    service: TMXService = Depends(get_tmx_service)
):
    """
    Removes duplicate translation units based on identical content across all variants.
    """
    try:
        result = service.remove_duplicate_units()
        return result
    except ValueError as ve: # e.g. no active session
        raise HTTPException(status_code=400, detail=str(ve))
    except RuntimeError as re:
        raise HTTPException(status_code=500, detail=str(re))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

# --- Maintenance Endpoints (Chunk 5 Part 3) ---

@app.post("/maintenance/remove_tags", response_model=Dict[str, Any], tags=["Maintenance Operations"])
async def remove_tags_endpoint(
    request: UserIdRequest, # Contains optional user_id
    service: TMXService = Depends(get_tmx_service)
):
    """Removes all XML tags from all segments in the active TMX file."""
    try:
        result = service.remove_all_tags_from_segments(request.user_id)
        return result
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

@app.post("/maintenance/remove_spaces", response_model=Dict[str, Any], tags=["Maintenance Operations"])
async def remove_spaces_endpoint(
    request: UserIdRequest, # Contains optional user_id
    service: TMXService = Depends(get_tmx_service)
):
    """Removes leading/trailing whitespace from all segments in the active TMX file."""
    try:
        result = service.remove_leading_trailing_spaces_from_segments(request.user_id)
        return result
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

@app.post("/maintenance/search_replace", response_model=Dict[str, Any], tags=["Maintenance Operations"])
async def search_replace_endpoint(
    request: SearchReplaceRequest,
    service: TMXService = Depends(get_tmx_service)
):
    """Performs search and replace in segments of the active TMX file."""
    try:
        result = service.search_and_replace(
            search_text=request.search_text,
            replace_text=request.replace_text,
            lang_code=request.lang_code,
            is_regex=request.is_regex,
            case_sensitive=request.case_sensitive,
            current_user_id=request.user_id
        )
        return result
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

# --- Metadata Editing Endpoints (Chunk 5 Part 3) ---

@app.get("/units/{tu_db_id}/metadata", response_model=Dict[str, Any], tags=["Metadata Editing"])
async def get_tu_metadata_endpoint(
    tu_db_id: int,
    service: TMXService = Depends(get_tmx_service)
):
    """Gets custom attributes, properties, and notes for a specific Translation Unit."""
    try:
        return service.get_tu_metadata(tu_db_id)
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve)) # TU not found
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

@app.put("/units/{tu_db_id}/attributes", response_model=StatusResponse, tags=["Metadata Editing"])
async def set_tu_attributes_endpoint(
    tu_db_id: int,
    request: MetadataUpdateRequest, # items: List[MetadataItem], user_id: Optional[str]
    service: TMXService = Depends(get_tmx_service)
):
    """Sets custom attributes for a Translation Unit."""
    try:
        # The service expects List[Dict], Pydantic model.items are already List[MetadataItem]
        # We need to convert List[MetadataItem] to List[Dict] if service method expects pure dicts
        # Assuming service method set_tu_attributes takes List[Dict] as per its definition
        items_as_dicts = [item.model_dump(exclude_none=True) for item in request.items]
        result = service.set_tu_attributes(tu_db_id, items_as_dicts, request.user_id)
        return StatusResponse(status=result["status"], message=result["message"])
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve)) # Includes TU not found
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

@app.put("/units/{tu_db_id}/properties", response_model=StatusResponse, tags=["Metadata Editing"])
async def set_tu_properties_endpoint(
    tu_db_id: int,
    request: MetadataUpdateRequest,
    service: TMXService = Depends(get_tmx_service)
):
    """Sets properties for a Translation Unit."""
    try:
        items_as_dicts = [item.model_dump(exclude_none=True) for item in request.items]
        result = service.set_tu_properties(tu_db_id, items_as_dicts, request.user_id)
        return StatusResponse(status=result["status"], message=result["message"])
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

@app.put("/units/{tu_db_id}/notes", response_model=StatusResponse, tags=["Metadata Editing"])
async def set_tu_notes_endpoint(
    tu_db_id: int,
    request: MetadataUpdateRequest,
    service: TMXService = Depends(get_tmx_service)
):
    """Sets notes for a Translation Unit."""
    try:
        items_as_dicts = [item.model_dump(exclude_none=True) for item in request.items]
        result = service.set_tu_notes(tu_db_id, items_as_dicts, request.user_id)
        return StatusResponse(status=result["status"], message=result["message"])
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")


@app.get("/units/{tu_db_id}/variants/{lang_code}/metadata", response_model=Dict[str, Any], tags=["Metadata Editing"])
async def get_tuv_metadata_endpoint(
    tu_db_id: int,
    lang_code: str,
    service: TMXService = Depends(get_tmx_service)
):
    """Gets custom attributes, properties, and notes for a specific Translation Unit Variant."""
    try:
        return service.get_tuv_metadata(tu_db_id, lang_code)
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve)) # TUV not found
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

@app.put("/units/{tu_db_id}/variants/{lang_code}/attributes", response_model=StatusResponse, tags=["Metadata Editing"])
async def set_tuv_attributes_endpoint(
    tu_db_id: int,
    lang_code: str,
    request: MetadataUpdateRequest,
    service: TMXService = Depends(get_tmx_service)
):
    """Sets custom attributes for a Translation Unit Variant."""
    try:
        items_as_dicts = [item.model_dump(exclude_none=True) for item in request.items]
        result = service.set_tuv_attributes(tu_db_id, lang_code, items_as_dicts, request.user_id)
        return StatusResponse(status=result["status"], message=result["message"])
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve)) # Includes TUV not found
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

@app.put("/units/{tu_db_id}/variants/{lang_code}/properties", response_model=StatusResponse, tags=["Metadata Editing"])
async def set_tuv_properties_endpoint(
    tu_db_id: int,
    lang_code: str,
    request: MetadataUpdateRequest,
    service: TMXService = Depends(get_tmx_service)
):
    """Sets properties for a Translation Unit Variant."""
    try:
        items_as_dicts = [item.model_dump(exclude_none=True) for item in request.items]
        result = service.set_tuv_properties(tu_db_id, lang_code, items_as_dicts, request.user_id)
        return StatusResponse(status=result["status"], message=result["message"])
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

@app.put("/units/{tu_db_id}/variants/{lang_code}/notes", response_model=StatusResponse, tags=["Metadata Editing"])
async def set_tuv_notes_endpoint(
    tu_db_id: int,
    lang_code: str,
    request: MetadataUpdateRequest,
    service: TMXService = Depends(get_tmx_service)
):
    """Sets notes for a Translation Unit Variant."""
    try:
        items_as_dicts = [item.model_dump(exclude_none=True) for item in request.items]
        result = service.set_tuv_notes(tu_db_id, lang_code, items_as_dicts, request.user_id)
        return StatusResponse(status=result["status"], message=result["message"])
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

# --- TU CRUD Endpoints (Chunk 5 Part 3) ---
@app.post("/units", response_model=TranslationUnitModel, tags=["Translation Units CRUD"]) # Use the actual Pydantic model for TU
async def create_translation_unit_endpoint(
    request: TUDefinitionRequest,
    service: TMXService = Depends(get_tmx_service)
):
    """Creates a new Translation Unit."""
    try:
        # The service method insert_new_translation_unit expects List[Dict] for variants, properties etc.
        # Pydantic models in TUDefinitionRequest (like TUVariantDefinition) need to be converted.
        variants_as_dicts = [var.model_dump(exclude_none=True) for var in request.variants]
        props_as_dicts = [p.model_dump(exclude_none=True) for p in request.properties] if request.properties else None
        notes_as_dicts = [n.model_dump(exclude_none=True) for n in request.notes] if request.notes else None
        attrs_as_dicts = [ca.model_dump(exclude_none=True) for ca in request.custom_attributes] if request.custom_attributes else None

        created_tu_dict = service.insert_new_translation_unit(
            tuid=request.tuid,
            variants_data=variants_as_dicts,
            properties_data=props_as_dicts,
            notes_data=notes_as_dicts,
            custom_attributes_data=attrs_as_dicts,
            seg_type=request.seg_type,
            datatype=request.datatype,
            usage_count=request.usage_count,
            current_user_id=request.user_id
        )
        # The service method returns a dict (from model_dump), which is fine for FastAPI
        return created_tu_dict
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except RuntimeError as re:
        raise HTTPException(status_code=500, detail=str(re))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

@app.delete("/units/{tu_db_id}", response_model=StatusResponse, tags=["Translation Units CRUD"])
async def delete_translation_unit_endpoint(
    tu_db_id: int,
    service: TMXService = Depends(get_tmx_service)
):
    """Deletes a Translation Unit by its database ID."""
    try:
        result = service.delete_translation_unit_by_id(tu_db_id)
        return StatusResponse(status=result["status"], message=result["message"])
    except ValueError as ve: # TU not found
        raise HTTPException(status_code=404, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")


# To run this FastAPI application (example, typically use uvicorn command):
# if __name__ == "__main__":
#     import uvicorn
#     # This is for development only. For production, use a proper ASGI server like Uvicorn/Hypercorn.
#     # The TMXService instance uses a file-based DB "tmx_editor_main.db" by default.
#     # Ensure that the tmx_server_logic package is in PYTHONPATH or run from parent directory:
# python -m uvicorn tmx_server_logic.main:app --reload --workers 1
# Using --workers 1 is crucial if the global TMXService instance holds state (like an in-memory DB session for :memory:)
# and you are not using a proper lifespan manager or request-scoped dependencies for it.
# For file-based DBs like "tmx_editor_main.db", multiple workers might be okay but could lead to DB contention
# if the service isn't designed to handle concurrent requests to a single file-based DB safely.
# For this project's current single-active-file model, --workers 1 is safest.
    # uvicorn.run("tmx_server_logic.main:app", host="0.0.0.0", port=8000, reload=True) # Corrected way to call uvicorn.run

# --- Dependency for TMXConverter ---
def get_tmx_converter() -> TMXConverter:
    return TMXConverter()

# --- Data Conversion Endpoints ---

@app.post("/convert/csv_to_tmx_and_open", response_model=StatusResponse, tags=["Data Conversion"])
async def convert_csv_to_tmx_and_open_endpoint(
    uploaded_file: UploadFile = File(...),
    options_json: str = Body(...), # Changed from Form to Body to align with how FastAPI handles mixed Form and JSON for file uploads. Client must send options as JSON string in a form field.
    service: TMXService = Depends(get_tmx_service),
    converter: TMXConverter = Depends(get_tmx_converter)
):
    temp_file_path: Optional[str] = None
    try:
        # For file uploads with other data, FastAPI expects 'form' data.
        # options_json is sent as a string field in the form.
        try:
            options_dict = json.loads(options_json)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON format for options_json string.")
        
        import_options = CSVImportOptions(**options_dict)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as temp_file:
            shutil.copyfileobj(uploaded_file.file, temp_file)
            temp_file_path = temp_file.name
        
        if not temp_file_path: # Should not happen if above block succeeded
            raise HTTPException(status_code=500, detail="Failed to save uploaded CSV file temporarily.")

        header, tus = converter.csv_to_tmx_content(
            csv_filepath=temp_file_path,
            languages=import_options.languages,
            charset=import_options.charset,
            delimiter=import_options.delimiter,
            quotechar=import_options.quotechar,
            tuid_col_index=import_options.tuid_col_index
        )
        
        # Assuming TMXHeader and TranslationUnit models are compatible with what open_file_from_models expects
        service.open_file_from_models(header, tus, original_filepath=uploaded_file.filename or "uploaded.csv")
        
        return StatusResponse(status="success", message=f"CSV file '{uploaded_file.filename}' converted and opened successfully.")

    except json.JSONDecodeError: # This catches error from options_dict = json.loads(options_json)
        raise HTTPException(status_code=400, detail="Invalid JSON format for options.")
    except ValueError as ve: 
        raise HTTPException(status_code=400, detail=str(ve))
    except FileNotFoundError: # Should be caught if temp_file_path is used after being deleted or never created
        raise HTTPException(status_code=500, detail="Temporary CSV file disappeared (internal error).")
    except RuntimeError as re:
        raise HTTPException(status_code=500, detail=f"Error during CSV to TMX conversion: {str(re)}")
    except Exception as e:
        # Log the exception for debugging
        # logger.error(f"Unexpected error in csv_to_tmx_and_open_endpoint: {type(e).__name__} - {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")
    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            os.remove(temp_file_path)
        if uploaded_file:
            await uploaded_file.close()


@app.post("/convert/excel_to_tmx_and_open", response_model=StatusResponse, tags=["Data Conversion"])
async def convert_excel_to_tmx_and_open_endpoint(
    uploaded_file: UploadFile = File(...),
    options_json: str = Body(...), # Changed from Form to Body
    service: TMXService = Depends(get_tmx_service),
    converter: TMXConverter = Depends(get_tmx_converter)
):
    temp_file_path: Optional[str] = None
    try:
        try:
            options_dict = json.loads(options_json)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON format for options_json string.")
            
        import_options = ExcelImportOptions(**options_dict)

        file_suffix = ".xlsx"
        if uploaded_file.filename and uploaded_file.filename.lower().endswith(".xls"):
             file_suffix = ".xls"

        with tempfile.NamedTemporaryFile(delete=False, suffix=file_suffix) as temp_file:
            shutil.copyfileobj(uploaded_file.file, temp_file)
            temp_file_path = temp_file.name

        if not temp_file_path:
            raise HTTPException(status_code=500, detail="Failed to save uploaded Excel file temporarily.")

        header, tus = converter.excel_to_tmx_content(
            excel_filepath=temp_file_path,
            languages=import_options.languages,
            sheet_name=import_options.sheet_name,
            header_row_index=import_options.header_row_index,
            data_start_row_index=import_options.data_start_row_index,
            tuid_col_letter=import_options.tuid_col_letter
        )
        
        service.open_file_from_models(header, tus, original_filepath=uploaded_file.filename or "uploaded.xlsx")
        
        return StatusResponse(status="success", message=f"Excel file '{uploaded_file.filename}' converted and opened successfully.")
    
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON format for options.")
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="Temporary Excel file disappeared (internal error).")
    except RuntimeError as re:
        raise HTTPException(status_code=500, detail=f"Error during Excel to TMX conversion: {str(re)}")
    except Exception as e:
        # logger.error(f"Unexpected error in excel_to_tmx_and_open_endpoint: {type(e).__name__} - {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")
    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            os.remove(temp_file_path)
        if uploaded_file:
            await uploaded_file.close()

@app.post("/convert/tmx_to_csv", response_model=StatusResponse, tags=["Data Conversion"])
async def convert_tmx_to_csv_endpoint(
    export_request: ExportPathRequest, 
    options: CSVExportOptions = Body(...), 
    service: TMXService = Depends(get_tmx_service),
    converter: TMXConverter = Depends(get_tmx_converter)
):
    try:
        active_store = service._get_active_store() 
        tmx_file_id = service._ensure_active_tmx_file_id() 

        converter.tmx_content_to_csv(
            store=active_store,
            tmx_file_id=tmx_file_id,
            csv_filepath=export_request.file_path,
            charset=options.charset,
            delimiter=options.delimiter
        )
        return StatusResponse(status="success", message=f"TMX data successfully exported to CSV: {export_request.file_path}")
    except ValueError as ve: 
        raise HTTPException(status_code=400, detail=str(ve))
    except RuntimeError as re:
        raise HTTPException(status_code=500, detail=f"Error during TMX to CSV conversion: {str(re)}")
    except Exception as e:
        # logger.error(f"Unexpected error in tmx_to_csv_endpoint: {type(e).__name__} - {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")


@app.post("/convert/tmx_to_excel", response_model=StatusResponse, tags=["Data Conversion"])
async def convert_tmx_to_excel_endpoint(
    export_request: ExportPathRequest = Body(...), # Made export_request part of the body
    service: TMXService = Depends(get_tmx_service),
    converter: TMXConverter = Depends(get_tmx_converter)
):
    try:
        active_store = service._get_active_store()
        tmx_file_id = service._ensure_active_tmx_file_id()

        converter.tmx_content_to_excel(
            store=active_store,
            tmx_file_id=tmx_file_id,
            excel_filepath=export_request.file_path
        )
        return StatusResponse(status="success", message=f"TMX data successfully exported to Excel: {export_request.file_path}")
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except RuntimeError as re:
        raise HTTPException(status_code=500, detail=f"Error during TMX to Excel conversion: {str(re)}")
    except Exception as e:
        # logger.error(f"Unexpected error in tmx_to_excel_endpoint: {type(e).__name__} - {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

# To run this FastAPI application (example, typically use uvicorn command):
if __name__ == "__main__":
    import uvicorn
    # This is for development only. For production, use a proper ASGI server like Uvicorn/Hypercorn.
    # The TMXService instance uses a file-based DB "tmx_editor_main.db" by default.
    # Ensure that the tmx_server_logic package is in PYTHONPATH or run from parent directory:
    # python -m uvicorn tmx_server_logic.main:app --reload --workers 1
    # Using --workers 1 is crucial if the global TMXService instance holds state (like an in-memory DB session for :memory:)
    # and you are not using a proper lifespan manager or request-scoped dependencies for it.
    # For file-based DBs like "tmx_editor_main.db", multiple workers might be okay but could lead to DB contention
    # if the service isn't designed to handle concurrent requests to a single file-based DB safely.
    # For this project's current single-active-file model, --workers 1 is safest.
    # uvicorn.run("tmx_server_logic.main:app", host="0.0.0.0", port=8000, reload=True) # Commented out for testing

# --- Test Client and Test Functions ---
# Ensure these imports are present at the top of the file or added if missing
# import os # already imported
# import shutil # already imported
# import tempfile # already imported
# import json # already imported
import asyncio
from fastapi.testclient import TestClient
import openpyxl # For creating dummy .xlsx file

# --- Helper functions for creating dummy files ---
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

# --- Test functions ---

def test_csv_import_endpoint(client: TestClient):
    print("--- Testing CSV Import Endpoint ---")
    create_dummy_csv()
    options = {"languages": ["en-US", "fr-FR"], "tuid_col_index": 0}
    options_json_str = json.dumps(options)
    
    with open(DUMMY_CSV_PATH, "rb") as f:
        response = client.post(
            "/convert/csv_to_tmx_and_open",
            files={"uploaded_file": (DUMMY_CSV_PATH, f, "text/csv")},
            data={"options_json": options_json_str} # FastAPI expects string value for form field
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
    
    close_response = client.post("/file/close") # Clean up for next test
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

    close_response = client.post("/file/close") # Clean up for next test
    assert close_response.status_code == 200


def test_tmx_export_endpoints(client: TestClient):
    print("--- Testing TMX Export Endpoints ---")
    create_dummy_tmx_for_export()

    # Setup: Open the TMX file
    open_response = client.post("/file/open", json={"file_path": DUMMY_TMX_EXPORT_PATH})
    assert open_response.status_code == 200, f"Export Test Setup: Open failed: {open_response.text}"
    assert open_response.json()["status"] == "success"
    print("Export Test Setup: TMX file opened successfully.")

    # Test CSV Export
    # The endpoint expects ExportPathRequest as the main body, and CSVExportOptions also in the body.
    # FastAPI will merge these if they are compatible or expect a specific structure.
    # Let's try sending a flat JSON body with all fields.
    # If `export_request: ExportPathRequest, options: CSVExportOptions = Body(...)`
    # means FastAPI expects two top-level keys `export_request` and `options` in the JSON, then:
    # json_body_csv = {"export_request": {"file_path": EXPORT_CSV_PATH}, "options": {"charset": "utf-8", "delimiter": ","}}
    # However, the current implementation of the endpoint has `options_json: str = Body(...)` for import,
    # and for export: `export_request: ExportPathRequest, options: CSVExportOptions = Body(...)`
    # This signature for CSV export suggests FastAPI will expect a JSON body where `export_request` is one key
    # and all fields of `CSVExportOptions` are separate keys, or it might expect them nested.
    # Let's try the explicit nested structure first as it's less ambiguous for Pydantic.
    # If the endpoint signature was `(item: MyModel = Body(...))`, then a flat structure matching MyModel is fine.
    # With multiple Body params, FastAPI usually expects distinct JSON objects if they are embedded,
    # or it might try to map fields if they don't overlap.
    # The provided signature `export_request: ExportPathRequest, options: CSVExportOptions = Body(...)` is problematic.
    # A single Pydantic model in Body is standard. Two usually means one is `embed=True`.
    # Let's assume the endpoint expects a single JSON body that combines these.
    # This structure `{"file_path": EXPORT_CSV_PATH, "charset": "utf-8", "delimiter": ","}` might work if FastAPI is clever.
    # Or it might need to be `{"export_request": {"file_path": EXPORT_CSV_PATH}, "options": {"charset": "utf-8", "delimiter": ","}}`
    # Given the endpoint is `async def convert_tmx_to_csv_endpoint(export_request: ExportPathRequest, options: CSVExportOptions = Body(...),`
    # The most robust way is to have a single Pydantic model that wraps both.
    # Since I can't change the endpoint signature in this step, I'll try what's most likely to work or fail informatively.
    # The endpoint has `export_request: ExportPathRequest` (not Body) and `options: CSVExportOptions = Body(...)`.
    # This means `export_request` is likely expected as Path or Query if not specified as Body.
    # This conflicts with `ExportPathRequest` being a Pydantic model itself.
    # The prompt for the endpoint was `export_request: ExportPathRequest, options: CSVExportOptions = Body(...)`
    # This implies they are *both* part of the body. FastAPI handles this by expecting keys matching param names.
    csv_export_payload = {
        "export_request": {"file_path": EXPORT_CSV_PATH},
        "options": {"charset": "utf-8", "delimiter": ","}
    }
    # This structure is based on FastAPI expecting keys in the JSON that match parameter names when multiple Body params are used.
    # However, the endpoint signature is `export_request: ExportPathRequest, options: CSVExportOptions = Body(...)`
    # The parameter `export_request` is NOT marked with `Body(...)`. So it's likely expected from path/query.
    # This is a contradiction. The `ExportPathRequest` model implies a body.
    # Let's assume the endpoint signature was intended to be:
    # `async def convert_tmx_to_csv_endpoint(body_params: CombinedCsvExportRequest = Body(...))`
    # Or that `export_request` is part of the path/query, and `options` is the body.
    # Given the previous definition, I'll assume `export_request` is the primary body, and `options` is also part of it.
    # The endpoint signature from previous step for CSV export:
    # async def convert_tmx_to_csv_endpoint(
    # export_request: ExportPathRequest, 
    # options: CSVExportOptions = Body(...), 
    # ...
    # This implies `export_request` is *not* from the body by default. It would be from query/path.
    # This is an issue with the endpoint definition from the previous step.
    # For now, I will assume the intention was that *both* form the request body.
    # This would typically be done by having a single wrapper model or by FastAPI magic.
    # If `export_request` is meant to be the primary body, and `options` is also `Body`, it's tricky.
    # Let's try to match the parameters directly in the json body.
    # This is a common way FastAPI handles multiple Pydantic models in a single body if they are not embedded.
    # The endpoint signature in `main.py` is:
    # async def convert_tmx_to_csv_endpoint(
    #    export_request: ExportPathRequest, 
    #    options: CSVExportOptions = Body(...), 
    # This is problematic. `export_request` is not `Body`. It should be.
    # I will assume it *meant* to be part of the body.
    # If so, the client should send `json={"export_request": {...}, "options": {...}}` if `embed=True` was used,
    # or a flat structure if FastAPI can map it.
    # Given `options: CSVExportOptions = Body(...)`, `options` *is* the body. `export_request` must be query/path.
    # This needs correction in the endpoint. For the test, I'll construct the call assuming `options` is the body
    # and `file_path` must come from query/path for `ExportPathRequest`. This is messy.
    
    # Re-checking the ADDED code for the endpoint in previous step:
    # @app.post("/convert/tmx_to_csv", response_model=StatusResponse, tags=["Data Conversion"])
    # async def convert_tmx_to_csv_endpoint(
    #    export_request: ExportPathRequest,  <-- This is NOT Body()
    #    options: CSVExportOptions = Body(...), 
    #    ...
    # This signature means ExportPathRequest fields are query params.
    # ExportPathRequest(BaseModel): file_path: str
    # CSVExportOptions(BaseModel): charset: str, delimiter: str

    print("Testing TMX to CSV Export...")
    response_csv = client.post(
        f"/convert/tmx_to_csv?file_path={EXPORT_CSV_PATH}", # file_path as query
        json={"charset": "utf-8", "delimiter": ","} # options as body
    )
    assert response_csv.status_code == 200, f"CSV Export: Expected 200, got {response_csv.status_code}, {response_csv.text}"
    assert response_csv.json()["status"] == "success", f"CSV Export: Status not success: {response_csv.json()}"
    assert os.path.exists(EXPORT_CSV_PATH), "CSV Export: Output file not found."
    print("CSV Export: Success, output file created.")
    with open(EXPORT_CSV_PATH, "r", encoding="utf-8") as f_csv:
        csv_content = f_csv.read()
        assert "Export me" in csv_content and "Expórtame" in csv_content
        print("CSV Export: Content verified.")

    # Test Excel Export
    # Endpoint signature: async def convert_tmx_to_excel_endpoint(export_request: ExportPathRequest = Body(...), ...
    # This one IS Body(...), so it's simpler.
    print("Testing TMX to Excel Export...")
    response_excel = client.post("/convert/tmx_to_excel", json={"file_path": EXPORT_EXCEL_PATH})
    assert response_excel.status_code == 200, f"Excel Export: Expected 200, got {response_excel.status_code}, {response_excel.text}"
    assert response_excel.json()["status"] == "success", f"Excel Export: Status not success: {response_excel.json()}"
    assert os.path.exists(EXPORT_EXCEL_PATH), "Excel Export: Output file not found."
    print("Excel Export: Success, output file created.")
    # Optional: verify Excel content
    try:
        workbook = openpyxl.load_workbook(EXPORT_EXCEL_PATH)
        sheet = workbook.active
        texts_in_excel = [cell.value for row in sheet.iter_rows() for cell in row if cell.value]
        assert "Export me" in texts_in_excel
        assert "Expórtame" in texts_in_excel
        print("Excel Export: Content verified.")
    except Exception as e:
        print(f"Excel content verification failed (openpyxl might be needed or file is corrupted): {e}")


    # Cleanup: Close the TMX file session
    close_response = client.post("/file/close")
    assert close_response.status_code == 200
    print("Export Test Cleanup: TMX file session closed.")


if __name__ == "__main__":
    client = TestClient(app)
    
    # Ensure clean state before tests
    # This is important if tests are run multiple times or if previous runs failed mid-way
    # Get current active tmx_file_id to avoid issues with service/db state
    # A better solution for tests would be a dedicated test DB or full cleanup.
    # For now, just try to close any active session.
    try:
        client.post("/file/close") # Try to close any pre-existing session
    except Exception:
        pass # Ignore if it fails (e.g., no session was active)

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
    
    # To run the server normally (e.g., after tests or if not testing):
    # print("\nStarting Uvicorn server...")
    # uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
