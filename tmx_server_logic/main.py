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
    TranslationUnit as TranslationUnitModel # For response model of create TU
)
# Assuming .config might exist later for settings like default_indentation
# from .config import settings


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
    request: LanguageCodeRequest,
    service: TMXService = Depends(get_tmx_service)
):
    """
    Sets the source language (srclang) in the header of the active TMX file.
    """
    try:
        result = service.set_source_language(request.lang_code)
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
    uvicorn.run("tmx_server_logic.main:app", host="0.0.0.0", port=8000, reload=True) # Corrected way to call uvicorn.run
