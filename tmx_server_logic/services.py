import os
import shutil # For file operations in upload_and_open if it were here (it's in main.py)
import os
import shutil # For file operations in upload_and_open if it were here (it's in main.py)
import os
import shutil # For file operations in upload_and_open if it were here (it's in main.py)
import re # For language code validation
import sqlite3 # For catching IntegrityError
import json # For metadata methods
from datetime import datetime, timezone # Ensure datetime and timezone are imported for now(timezone.utc)
from typing import Optional, Dict, Any, List, Iterable, Tuple
from collections import defaultdict # For consolidate_units
from lxml import etree # For extract_pure_text_from_segment_xml

from .storage import SQLiteStore
from .tmx_parser import TMXParser
# from .tmx_converter import TMXConverter # TMXConverter is from Chunk 6, not used in this chunk
from .models import TMXHeader, TranslationUnit, TranslationUnitVariant, FileInfoResponse, TMXAttribute, TMXProperty, TMXNote # Added metadata models
# from .config import settings # Config for default indentation, if used. For now, hardcode or pass directly.

# Utility function to extract pure text. Can be a static method or here.
def extract_pure_text_from_segment_xml(segment_xml: str) -> str:
    """
    Extracts and returns the concatenated text content from an XML segment string.
    e.g., "<seg>This is <b>bold</b> text.</seg>" -> "This is bold text."
    """
    if not segment_xml or not segment_xml.strip():
        return ""
    try:
        # Wrap with a root element if segment_xml is just the content of <seg>
        # If segment_xml is a full <seg>...</seg> string, etree.fromstring should handle it.
        parser = etree.XMLParser(recover=True) # recover=True helps with potentially malformed XML snippets
        xml_element = etree.fromstring(segment_xml, parser=parser)
        
        # Concatenate all text nodes, including those within child elements.
        # The result of stringify_children is already unicode.
        text_content = "".join(xml_element.itertext())
        return text_content.strip()
    except etree.XMLSyntaxError as e:
        # If parsing fails, it might be plain text or invalid XML.
        # As a fallback, return the original string, or handle error appropriately.
        # For TMX, <seg> content is often text with inline tags. If it's pure text, this might be too complex.
        # However, TMX spec implies <seg> contains content, which can be mixed.
        print(f"XMLSyntaxError while extracting pure text from '{segment_xml}': {e}. Falling back to no text.")
        return "" # Or raise an error, or return segment_xml if that's desired fallback

class TMXService:
    """
    Service layer for managing TMX file operations.
    Orchestrates parsing, storage, and retrieval of TMX data.
    """

    def __init__(self, db_path: str = "tmx_editor_active.db"):
        """
        Initializes the TMXService.
        :param db_path: Path to the SQLite database file to be used for the active session.
        """
        self.db_path: str = db_path
        self.store: Optional[SQLiteStore] = None
        self.parser: TMXParser = TMXParser()
        # self.converter: TMXConverter = TMXConverter() # For Chunk 6

        self._is_active: bool = False
        self._current_tmx_file_id: Optional[int] = None
        self._current_file_path: Optional[str] = None # Store the path of the currently open file

    def _init_store(self, for_new_file: bool = True, new_file_path: Optional[str] = None) -> None:
        """
        Initializes or re-initializes the SQLiteStore.
        If for_new_file is True, it clears existing data and prepares for a new TMX file.
        """
        if self.store:
            self.store.close()
        
        self.store = SQLiteStore(self.db_path)
        self._is_active = False # Reset active state until file is fully processed
        self._current_tmx_file_id = None
        self._current_file_path = None

        if for_new_file:
            if not new_file_path:
                # Default name if none provided, though open_file should always provide one.
                new_file_path = f"active_session_{datetime.now(timezone.utc).timestamp()}.tmx"
            # This method clears all tables and creates a new entry in tmx_files, returning its ID.
            self._current_tmx_file_id = self.store.prepare_new_tmx_file_session(new_file_path)
            self._current_file_path = new_file_path # Store the path for the new session
        # If not for_new_file, implies re-opening an existing DB, logic would be different (not covered here)

    def _get_active_store(self) -> SQLiteStore:
        """Returns the active SQLiteStore, raising an exception if none is active."""
        if not self.store or not self._is_active or self._current_tmx_file_id is None:
            raise ValueError("No active TMX file session. Please open a file first.")
        return self.store

    def _ensure_active_tmx_file_id(self) -> int:
        """Ensures there's an active TMX file ID, raising an exception if not."""
        if self._current_tmx_file_id is None or not self._is_active:
            # This state should ideally be prevented by _get_active_store or similar checks.
            raise ValueError("No active TMX file ID. Session might not be properly initialized.")
        return self._current_tmx_file_id

    def open_file(self, file_path: str) -> None:
        """
        Opens a TMX file, parses it, and loads its content into the database.
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"TMX file not found: {file_path}")

        # Initialize a fresh store session for this file
        self._init_store(for_new_file=True, new_file_path=file_path)
        # _current_tmx_file_id and _current_file_path are now set by _init_store

        if self._current_tmx_file_id is None: # Should be set by _init_store
             raise RuntimeError("Failed to initialize TMX file session and obtain a file ID.")

        try:
            # parse_tmx_file returns a tuple: (TMXHeader, list_of_TranslationUnits)
            # The list_of_TranslationUnits part is already a list, not just an iterable,
            # based on typical parser implementations and how it's used.
            header_model, tu_models_list = self.parser.parse_tmx_file(file_path)
            
            processed_tus_for_storage = []
            for tu_model in tu_models_list:
                for variant_model in tu_model.variants:
                    variant_model.segment_text_pure = extract_pure_text_from_segment_xml(variant_model.segment_xml)
                processed_tus_for_storage.append(tu_model)

            if self.store: # Store must be initialized by _init_store
                self.store.save_header(header_model, tmx_file_id=self._current_tmx_file_id)
                self.store.add_translation_units(processed_tus_for_storage, tmx_file_id=self._current_tmx_file_id)
                self._is_active = True # Mark session as active only after successful loading
            else: # Should not happen due to _init_store logic
                raise RuntimeError("SQLiteStore not initialized during open_file.")

        except Exception as e:
            # If any error occurs during parsing or DB loading, reset to inactive state.
            self.close_file() # Cleans up store and active state
            raise RuntimeError(f"Error opening or processing TMX file {file_path}: {e}") from e


    def close_file(self) -> None:
        """
        Closes the current TMX file session and releases resources.
        """
        if self.store:
            self.store.close()
            self.store = None
        self._is_active = False
        self._current_tmx_file_id = None
        self._current_file_path = None
        # print("TMX file session closed.") # For debugging

    def save_file(self, output_file_path: str, indentation: Optional[int] = 2) -> None:
        """
        Saves the currently active TMX data (header and TUs) to a new TMX file.
        :param output_file_path: Path where the TMX file will be saved.
        :param indentation: Number of spaces for pretty printing. Defaults to 2.
        """
        store = self._get_active_store() # Ensures store is active and valid
        tmx_id = self._ensure_active_tmx_file_id() # Ensures current_tmx_file_id is valid

        header = store.get_header(tmx_id)
        if header is None:
            raise ValueError("No header found for the active TMX file. Cannot save.")
        
        # Fetch all translation units for the active file
        # The get_all_translation_units from storage.py already handles fetching by tmx_file_id
        units = store.get_all_translation_units(tmx_id) 

        # Default indentation (could come from settings)
        indentation_to_use = indentation if indentation is not None else 2 

        self.parser.write_tmx_file(output_file_path, header, units, indentation=indentation_to_use)

    def get_file_info(self) -> FileInfoResponse:
        """
        Provides information about the currently active TMX file session.
        """
        if not self._is_active or not self.store or self._current_tmx_file_id is None or self._current_file_path is None:
            return FileInfoResponse(is_active=False)

        try:
            header_model = self.store.get_header(self._current_tmx_file_id)
            langs = self.store.get_language_codes(self._current_tmx_file_id)
            count = self.store.get_tu_count(self._current_tmx_file_id)

            return FileInfoResponse(
                is_active=True,
                file_path=self._current_file_path,
                tmx_file_id=self._current_tmx_file_id,
                header=header_model, # Pydantic model directly, FastAPI will handle serialization
                tu_count=count,
                language_codes=langs
            )
        except Exception as e:
            # If there's an error fetching info, report as inactive or with error details
            print(f"Error fetching file info: {e}")
            return FileInfoResponse(is_active=False, file_path=self._current_file_path, tmx_file_id=self._current_tmx_file_id)

    # Destructor to ensure DB connection is closed if service instance is deleted
    def __del__(self):
        self.close_file()

    # --- Chunk 5 Part 1 Methods ---

    def add_language(self, lang_code: str) -> Dict[str, str]:
        """
        Validates a language code and confirms its availability for use.
        Currently, this is a conceptual operation as languages are implicitly defined by TUVs.
        Future enhancements might involve updating TMXHeader.declared_languages.
        """
        self._ensure_active_tmx_file_id() # Ensures a file session is active
        store = self._get_active_store()

        # Basic validation for lang_code format (e.g., xx-YY or xx)
        # A more robust validation would use a library like `langcodes`.
        if not isinstance(lang_code, str) or not (2 <= len(lang_code) <= 10) or not lang_code.isidentifier():
            # A simple check, not exhaustive for BCP 47.
            # langcodes.tag_is_valid(lang_code) would be better if library allowed.
            if not re.match(r"^[a-zA-Z]{2,3}(-[a-zA-Z0-9]{2,8})*$", lang_code): # Basic BCP47-like check
                 raise ValueError(f"Invalid language code format: '{lang_code}'.")

        # Check if language already effectively exists (i.e., TUVs exist for this language)
        # This is just informational, as adding TUVs is how a language is "added".
        # existing_langs = store.get_language_codes(self._current_tmx_file_id)
        # if lang_code in existing_langs:
        #     return {"status": "info", "message": f"Language '{lang_code}' already has segments in the current TMX."}

        # For now, just confirm availability.
        return {"status": "success", "message": f"Language code '{lang_code}' is valid and available for use."}

    def remove_language(self, lang_code: str) -> Dict[str, str]:
        """
        Removes all translation unit variants (TUVs) for a given language code
        from the currently active TMX file.
        """
        tmx_id = self._ensure_active_tmx_file_id()
        store = self._get_active_store()
        
        try:
            deleted_count = store.remove_language_variants(lang_code, tmx_id)
            # Also, if TMXHeader had a list of declared languages, remove lang_code from there.
            # header = store.get_header(tmx_id)
            # if header and hasattr(header, 'declared_languages') and lang_code in header.declared_languages:
            #    header.declared_languages.remove(lang_code)
            #    store.save_header(header, tmx_id)
            return {"status": "success", "message": f"{deleted_count} segments for language '{lang_code}' removed."}
        except Exception as e: # Catch potential errors from store layer
            raise RuntimeError(f"Failed to remove language '{lang_code}': {str(e)}")


    def change_language_code(
        self, old_lang_code: str, new_lang_code: str, current_user_id: Optional[str] = None
    ) -> Dict[str, str]:
        """
        Changes all occurrences of old_lang_code to new_lang_code for TUVs
        in the currently active TMX file.
        """
        tmx_id = self._ensure_active_tmx_file_id()
        store = self._get_active_store()

        if old_lang_code == new_lang_code:
            raise ValueError("Old and new language codes cannot be the same.")

        # Basic validation for new_lang_code format
        if not isinstance(new_lang_code, str) or not (2 <= len(new_lang_code) <= 10) or not new_lang_code.isidentifier():
             if not re.match(r"^[a-zA-Z]{2,3}(-[a-zA-Z0-9]{2,8})*$", new_lang_code):
                raise ValueError(f"Invalid new language code format: '{new_lang_code}'.")

        existing_langs = store.get_language_codes(tmx_id)
        if old_lang_code not in existing_langs:
            raise ValueError(f"Old language code '{old_lang_code}' not found in the current TMX file.")
        if new_lang_code in existing_langs:
            raise ValueError(f"New language code '{new_lang_code}' already exists in the current TMX file. Cannot merge.")

        try:
            updated_count = store.update_language_code_in_variants(
                old_lang_code, new_lang_code, tmx_id, current_user_id
            )
            # If TMXHeader had declared_languages:
            # header = store.get_header(tmx_id)
            # if header and hasattr(header, 'declared_languages'):
            #   if old_lang_code in header.declared_languages:
            #       header.declared_languages.remove(old_lang_code)
            #   if new_lang_code not in header.declared_languages:
            #       header.declared_languages.append(new_lang_code)
            #   store.save_header(header, tmx_id)

            return {
                "status": "success",
                "message": f"Language code '{old_lang_code}' changed to '{new_lang_code}' for {updated_count} segments."
            }
        except sqlite3.IntegrityError: # Raised by store if new_lang_code constraint fails (should be caught by above check though)
            raise ValueError(f"Failed to change language code due to a conflict: '{new_lang_code}' might already exist for some translation units where '{old_lang_code}' was present.")
        except Exception as e:
            raise RuntimeError(f"Failed to change language code: {str(e)}")

    def set_source_language(self, lang_code: str) -> Dict[str, str]:
        """
        Sets the source language (srclang) in the header of the active TMX file.
        """
        tmx_id = self._ensure_active_tmx_file_id()
        store = self._get_active_store()

        # Basic validation for lang_code format
        if not isinstance(lang_code, str) or not (2 <= len(lang_code) <= 10) or not lang_code.isidentifier():
            if not re.match(r"^[a-zA-Z]{2,3}(-[a-zA-Z0-9]{2,8})*$", lang_code):
                raise ValueError(f"Invalid language code format: '{lang_code}'.")

        header = store.get_header(tmx_id)
        if header is None:
            # This should not happen if _ensure_active_tmx_file_id worked and open_file created a header
            raise RuntimeError("Active TMX file has no header. Cannot set source language.")
        
        header.srclang = lang_code
        header.change_date = datetime.now(timezone.utc) # Consistent timezone-aware datetime
        # header.change_id = current_user_id # If header model supports change_id and it's passed

        store.save_header(header, tmx_id)
        return {"status": "success", "message": f"Source language set to '{lang_code}'."}

    # --- End of Chunk 5 Part 1 Methods ---

    # --- Chunk 5 Part 2 Methods ---

    def _create_empty_segment_xml(self) -> str:
        """Helper to create a standard empty TMX segment string."""
        return "<seg></seg>"

    def consolidate_units(self, source_lang_code: str, current_user_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Consolidates duplicate translation units based on source text.
        Merges translations from duplicates into a master TU and deletes redundant TUs.
        """
        tmx_id = self._ensure_active_tmx_file_id()
        store = self._get_active_store()

        all_tus = store.get_all_translation_units(tmx_file_id=tmx_id, limit=-1) # Get all
        
        tus_by_source_text: Dict[str, List[TranslationUnit]] = defaultdict(list)
        for tu in all_tus:
            source_tuv = next((v for v in tu.variants if v.lang == source_lang_code), None)
            if source_tuv and source_tuv.segment_text_pure:
                tus_by_source_text[source_tuv.segment_text_pure].append(tu)
        
        consolidated_count = 0
        deleted_count = 0
        
        target_lang_codes = [lc for lc in store.get_language_codes(tmx_id) if lc != source_lang_code]

        for source_text, group_of_tus in tus_by_source_text.items():
            if len(group_of_tus) <= 1:
                continue

            # Sort by original_position then id_in_db to pick the master consistently.
            # TU model needs id_in_db to be populated by _row_to_tu.
            group_of_tus.sort(key=lambda tu: (tu.custom_attributes.get("original_position", float('inf')), tu.id_in_db or float('inf')))
            master_tu = group_of_tus[0]
            
            if master_tu.id_in_db is None: continue # Should not happen

            for i in range(1, len(group_of_tus)):
                duplicate_tu = group_of_tus[i]
                if duplicate_tu.id_in_db is None: continue

                merged_to_master_flag = False
                for target_lang in target_lang_codes:
                    master_target_tuv = next((v for v in master_tu.variants if v.lang == target_lang), None)
                    duplicate_target_tuv = next((v for v in duplicate_tu.variants if v.lang == target_lang), None)

                    if duplicate_target_tuv and duplicate_target_tuv.segment_text_pure:
                        if master_target_tuv is None or not master_target_tuv.segment_text_pure:
                            # Master is untranslated or doesn't exist, duplicate has translation. Copy it.
                            new_tuv_for_master = TranslationUnitVariant(
                                lang=target_lang,
                                segment_xml=duplicate_target_tuv.segment_xml,
                                segment_text_pure=duplicate_target_tuv.segment_text_pure,
                                # Copy other relevant fields like properties, notes, creation/change dates if needed
                                # For simplicity, just copying core content.
                                creation_date=duplicate_target_tuv.creation_date, # Or master_tu's if preferred
                                creation_id=duplicate_target_tuv.creation_id,
                                change_id=current_user_id
                            )
                            store.save_tuv(new_tuv_for_master, master_tu.id_in_db, current_user_id)
                            # Reload master_tu to reflect the new TUV for subsequent logic within this group
                            updated_master_tu = store.get_translation_unit_by_db_id(master_tu.id_in_db)
                            if updated_master_tu: master_tu = updated_master_tu
                            merged_to_master_flag = True
                
                # Delete the duplicate TU (it's now fully merged or was redundant)
                store.delete_translation_unit(duplicate_tu.id_in_db)
                deleted_count += 1
                if merged_to_master_flag:
                    consolidated_count +=1 # Count TU as consolidated if content was merged from it

        return {
            "status": "success",
            "message": f"Consolidation complete. TUs merged/updated: {consolidated_count}. Redundant TUs deleted: {deleted_count}.",
            "merged_updated_count": consolidated_count,
            "deleted_redundant_count": deleted_count
        }

    def remove_untranslated_units(self, source_lang_code: str) -> Dict[str, Any]:
        """
        Removes translation units that have a non-empty source language variant
        but are untranslated in all other languages.
        """
        tmx_id = self._ensure_active_tmx_file_id()
        store = self._get_active_store()
        
        # Validate source_lang_code
        if not re.match(r"^[a-zA-Z]{2,3}(-[a-zA-Z0-9]{2,8})*$", source_lang_code):
            raise ValueError(f"Invalid source language code format: '{source_lang_code}'.")

        deleted_count = store.delete_untranslated_tus(source_lang_code, tmx_id)
        return {
            "status": "success",
            "message": f"{deleted_count} untranslated translation units removed (based on source '{source_lang_code}').",
            "deleted_count": deleted_count
        }

    def remove_variants_same_as_source(self, source_lang_code: str, current_user_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Clears target language variants if their text content is identical to the source language variant.
        "Clearing" means setting segment_xml and segment_text_pure to empty.
        """
        tmx_id = self._ensure_active_tmx_file_id()
        store = self._get_active_store()

        if not re.match(r"^[a-zA-Z]{2,3}(-[a-zA-Z0-9]{2,8})*$", source_lang_code):
            raise ValueError(f"Invalid source language code format: '{source_lang_code}'.")

        cleared_count = store.clear_variants_matching_source(source_lang_code, tmx_id, current_user_id)
        return {
            "status": "success",
            "message": f"{cleared_count} target segments matching source '{source_lang_code}' were cleared.",
            "cleared_count": cleared_count
        }

    def remove_duplicate_units(self) -> Dict[str, Any]:
        """
        Removes duplicate translation units based on identical content across all variants.
        Keeps the TU that was created first (lowest original_position or id).
        """
        tmx_id = self._ensure_active_tmx_file_id()
        store = self._get_active_store()
        deleted_count = store.delete_duplicate_tus(tmx_id)
        return {
            "status": "success",
            "message": f"{deleted_count} duplicate translation units removed.",
            "deleted_count": deleted_count
        }

    # --- End of Chunk 5 Part 2 Methods ---

    # --- Chunk 5 Part 3 Methods ---

    def _create_segment_xml_str(self, text_content: str) -> str:
        """
        Creates a simple <seg>text_content</seg> XML string.
        Uses lxml to ensure proper XML character escaping if text_content has special chars.
        """
        seg_element = etree.Element("seg")
        seg_element.text = text_content
        # Ensure encoding is unicode to get a string, not bytes
        return etree.tostring(seg_element, encoding="unicode")

    def remove_all_tags_from_segments(self, current_user_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Removes all tags from all segments in all TUVs for the active TMX file.
        The segment content becomes plain text.
        """
        tmx_id = self._ensure_active_tmx_file_id()
        store = self._get_active_store()
        # The create_segment_xml helper for store.strip_tags_from_all_segments should be in storage.py
        modified_count = store.strip_tags_from_all_segments(tmx_id, current_user_id)
        return {
            "status": "success",
            "message": f"{modified_count} segments had tags stripped.",
            "modified_count": modified_count
        }

    def remove_leading_trailing_spaces_from_segments(self, current_user_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Removes leading and trailing spaces from all segments in all TUVs.
        """
        tmx_id = self._ensure_active_tmx_file_id()
        store = self._get_active_store()
        modified_count = store.strip_spaces_from_all_segments(tmx_id, current_user_id)
        return {
            "status": "success",
            "message": f"{modified_count} segments had leading/trailing spaces stripped.",
            "modified_count": modified_count
        }

    def search_and_replace(
        self, 
        search_text: str, 
        replace_text: str, 
        lang_code: Optional[str], 
        is_regex: bool, 
        case_sensitive: bool, 
        current_user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Performs search and replace on segment text for specified TUVs.
        """
        tmx_id = self._ensure_active_tmx_file_id()
        store = self._get_active_store()
        
        if not search_text and not is_regex: # Empty search text is only valid for regex if it means something specific (e.g. ^)
            raise ValueError("Search text cannot be empty for non-regex search.")

        modified_count = store.search_and_replace_in_segments(
            tmx_file_id=tmx_id,
            search_text=search_text,
            replace_text=replace_text,
            lang_code=lang_code,
            is_regex=is_regex,
            case_sensitive=case_sensitive,
            current_user_id=current_user_id
        )
        return {
            "status": "success",
            "message": f"{modified_count} segments were modified by search and replace.",
            "modified_count": modified_count
        }

    def get_tu_metadata(self, tu_db_id: int) -> Dict[str, Any]:
        """Retrieves custom attributes, properties, and notes for a specific Translation Unit."""
        store = self._get_active_store()
        tu = store.get_translation_unit_by_db_id(tu_db_id)
        if not tu:
            raise ValueError(f"Translation Unit with DB ID {tu_db_id} not found.")
        return {
            "tuid": tu.tuid, # Included for context
            "id_in_db": tu.id_in_db,
            "custom_attributes": [attr.model_dump() for attr in tu.custom_attributes],
            "properties": [prop.model_dump() for prop in tu.properties],
            "notes": [note.model_dump() for note in tu.notes]
        }

    def get_tuv_metadata(self, tu_db_id: int, lang_code: str) -> Dict[str, Any]:
        """Retrieves custom attributes, properties, and notes for a specific Translation Unit Variant."""
        store = self._get_active_store()
        tuv = store.get_tuv(tu_db_id, lang_code) # Assumes get_tuv is implemented in store
        if not tuv:
            raise ValueError(f"TUV for TU DB ID {tu_db_id} and language '{lang_code}' not found.")
        return {
            "lang": tuv.lang, # Included for context
            "custom_attributes": [attr.model_dump() for attr in tuv.custom_attributes],
            "properties": [prop.model_dump() for prop in tuv.properties],
            "notes": [note.model_dump() for note in tuv.notes]
        }
    
    def _set_tu_generic_metadata(self, tu_db_id: int, field_name: str, data: List[Dict], current_user_id: Optional[str]) -> Dict[str, str]:
        store = self._get_active_store()
        if not store.get_translation_unit_by_db_id(tu_db_id):
            raise ValueError(f"Translation Unit with DB ID {tu_db_id} not found.")
        
        json_data_str = json.dumps(data)
        success = store.update_tu_field_json(tu_db_id, field_name, json_data_str, current_user_id)
        if success:
            return {"status": "success", "message": f"TU {field_name} updated for TU DB ID {tu_db_id}."}
        else:
            raise RuntimeError(f"Failed to update TU {field_name} for TU DB ID {tu_db_id}.")

    def set_tu_attributes(self, tu_db_id: int, attributes_data: List[Dict], current_user_id: Optional[str] = None) -> Dict[str, str]:
        return self._set_tu_generic_metadata(tu_db_id, "custom_attributes", attributes_data, current_user_id)

    def set_tu_properties(self, tu_db_id: int, properties_data: List[Dict], current_user_id: Optional[str] = None) -> Dict[str, str]:
        return self._set_tu_generic_metadata(tu_db_id, "properties", properties_data, current_user_id)

    def set_tu_notes(self, tu_db_id: int, notes_data: List[Dict], current_user_id: Optional[str] = None) -> Dict[str, str]:
        return self._set_tu_generic_metadata(tu_db_id, "notes", notes_data, current_user_id)

    def _set_tuv_generic_metadata(self, tu_db_id: int, lang_code: str, field_name: str, data: List[Dict], current_user_id: Optional[str]) -> Dict[str, str]:
        store = self._get_active_store()
        # The store method update_tuv_field_json was designed to take tuv_id (PK of TUV table).
        # We need a way to get this or adapt the store method.
        # For now, assuming the store method update_tuv_field_json is adapted to work with (tu_db_id, lang_code)
        # This is a temporary assumption for the service layer based on the previous `update_tuv_field` which took tu_db_id, lang.
        # If `update_tuv_field_json` strictly needs `tuv_id`, this service method would need to fetch it first.
        
        # Let's assume `update_tuv_field_json_by_lang` exists or `update_tuv_field_json` is modified in storage.
        # This was addressed in previous iterations by making `update_tuv_field` take `tu_db_id` and `lang`.
        # TheChunk 5 Part 3 design for store `update_tuv_field_json` specifies `(tuv_id: int, ...)`.
        # This requires fetching the TUV's specific ID first.
        
        tuv = store.get_tuv(tu_db_id, lang_code) # This fetches the TUV model
        if not tuv:
            raise ValueError(f"TUV for TU DB ID {tu_db_id} and lang '{lang_code}' not found.")
        
        # To use `update_tuv_field_json(tuv_id=...)`, we need the TUV's actual primary key.
        # The `get_tuv` method in storage currently does not return the row `id`.
        # This needs to be rectified in `SQLiteStore.get_tuv` or a new method added.
        # For now, I'll assume `update_tuv_field_json` in the store can be called with `tu_db_id` and `lang_code`.
        # This is a known deviation from the spec for `update_tuv_field_json` for now.
        # A better approach is to adjust `update_tuv_field_json` to take `tu_db_id` and `lang_code`
        # and perform the lookup of the TUV's actual `id` within that store method.

        json_data_str = json.dumps(data)
        # Let's assume the store method is flexible or we have an alternative like:
        # success = store.update_tuv_field_json_by_tu_and_lang(tu_db_id, lang_code, field_name, json_data_str, current_user_id)
        # For now, I will proceed with the assumption that the store method will be flexible enough.
        # If store.update_tuv_field_json is strict about tuv_id, this will need a change in store.
        # The previous `update_tuv_field` (not _json) in store *did* take tu_db_id, lang.
        
        # To proceed, let's assume we will need to add a method to store: `get_tuv_internal_id(tu_db_id, lang)`
        # Or, modify `update_tuv_field_json` to accept tu_db_id and lang.
        # For now, I'll write it as if the store method takes tu_db_id and lang.
        # This means the specified `update_tuv_field_json(self, tuv_id: int, ...)` in store needs adjustment.
        # Let's call it `update_tuv_field_json_by_tu_lang` in the store for clarity for now.
        
        # Re-evaluating: The design specified `update_tuv_field_json(self, tuv_id: int, ...)`.
        # The service layer MUST fetch the `tuv_id`.
        # So, `SQLiteStore.get_tuv` must be modified to return the `id` field, or a new method like `get_tuv_with_id`.
        # Let's assume `get_tuv` returns the TUV model which doesn't have `id`.
        # This is a design flaw that needs to be addressed in the store's `get_tuv` or by adding a specific method.
        # For now, I will skip the implementation of set_tuv_generic_metadata and its callers.
        # This will be noted as a required change for the store.
        # ----
        # UPDATE: Given the tools, I cannot modify `get_tuv` in `storage.py` in this turn.
        # I will proceed by defining these service methods, but they will be non-functional
        # for TUV metadata until `storage.py` is adapted or a workaround is found.
        # For the purpose of this subtask, I will write them assuming `update_tuv_field_json` in store
        # can somehow be made to work with `tu_db_id` and `lang_code` or that `tuv_id` can be obtained.
        # Let's use the existing `store.update_tuv_field` which takes `tu_db_id` and `lang_code`.
        # This means the data must be a list of model_dump() for properties/notes/attrs.

        json_data_str = json.dumps(data)
        success = store.update_tuv_field(tu_db_id, lang_code, field_name, data) # Using existing store method

        if success:
            return {"status": "success", "message": f"TUV {field_name} updated for TU DB ID {tu_db_id}, lang '{lang_code}'."}
        else:
            raise RuntimeError(f"Failed to update TUV {field_name} for TU DB ID {tu_db_id}, lang '{lang_code}'.")


    def set_tuv_attributes(self, tu_db_id: int, lang_code: str, attributes_data: List[Dict], current_user_id: Optional[str] = None) -> Dict[str, str]:
        # Note: current_user_id is not directly used by store.update_tuv_field but would be if it used update_tuv_field_json
        return self._set_tuv_generic_metadata(tu_db_id, lang_code, "custom_attributes", attributes_data, current_user_id)

    def set_tuv_properties(self, tu_db_id: int, lang_code: str, properties_data: List[Dict], current_user_id: Optional[str] = None) -> Dict[str, str]:
        return self._set_tuv_generic_metadata(tu_db_id, lang_code, "properties", properties_data, current_user_id)

    def set_tuv_notes(self, tu_db_id: int, lang_code: str, notes_data: List[Dict], current_user_id: Optional[str] = None) -> Dict[str, str]:
        return self._set_tuv_generic_metadata(tu_db_id, lang_code, "notes", notes_data, current_user_id)

    def insert_new_translation_unit(
        self, 
        tuid: Optional[str] = None, 
        variants_data: Optional[List[Dict]] = None, # Expects list of dicts for TUVs
        properties_data: Optional[List[Dict]] = None,
        notes_data: Optional[List[Dict]] = None,
        custom_attributes_data: Optional[List[Dict]] = None,
        seg_type: Optional[str] = None,
        datatype: Optional[str] = None,
        usage_count: Optional[int] = None,
        # original_position: Optional[int] = None, # Store method handles this if needed
        current_user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        tmx_id = self._ensure_active_tmx_file_id()
        store = self._get_active_store()

        parsed_variants = []
        if variants_data:
            for var_data in variants_data:
                if "segment_xml" in var_data: # segment_xml is required
                    var_data["segment_text_pure"] = extract_pure_text_from_segment_xml(var_data["segment_xml"])
                else:
                    raise ValueError("Each variant must have 'segment_xml' and 'lang'.")
                if "lang" not in var_data:
                     raise ValueError("Each variant must have 'lang'.")

                var_data["creation_id"] = var_data.get("creation_id", current_user_id)
                var_data["change_id"] = var_data.get("change_id", current_user_id)
                # Pydantic models will use default_factory for dates if not provided
                if "creation_date" not in var_data: var_data["creation_date"] = datetime.now(timezone.utc)
                if "change_date" not in var_data: var_data["change_date"] = datetime.now(timezone.utc)
                parsed_variants.append(TranslationUnitVariant(**var_data))
        
        if not parsed_variants:
            raise ValueError("Cannot insert a translation unit with no variants.")

        tu_creation_date = datetime.now(timezone.utc)
        new_tu_model = TranslationUnit(
            tuid=tuid,
            variants=parsed_variants,
            properties=[TMXProperty(**p) for p in (properties_data or [])],
            notes=[TMXNote(**n) for n in (notes_data or [])],
            custom_attributes=[TMXAttribute(**ca) for ca in (custom_attributes_data or [])],
            seg_type=seg_type,
            datatype=datatype,
            usage_count=usage_count,
            creation_date=tu_creation_date,
            change_date=tu_creation_date,
            creation_id=current_user_id,
            change_id=current_user_id
        )
        
        tu_db_id = store.add_translation_unit(new_tu_model, tmx_id) 

        created_tu = store.get_translation_unit_by_db_id(tu_db_id)
        if not created_tu:
            raise RuntimeError(f"Failed to retrieve newly inserted TU with DB ID {tu_db_id}.")
        
        return created_tu.model_dump(exclude_none=True)

    def delete_translation_unit_by_id(self, tu_db_id: int) -> Dict[str, str]:
        """Deletes a translation unit by its database ID."""
        store = self._get_active_store() 
        if store.delete_translation_unit(tu_db_id):
            return {"status": "success", "message": f"Translation Unit with DB ID {tu_db_id} deleted."}
        else:
            raise ValueError(f"Translation Unit with DB ID {tu_db_id} not found or could not be deleted.")

    # --- End of Chunk 5 Part 3 Methods ---

    def get_segments(
        self,
        filter_text: Optional[str] = None,
        filter_lang_code: Optional[str] = None,
        case_sensitive_filter: bool = False,
        is_regex_filter: bool = False,
        filter_untranslated_to_other_langs: bool = False,
        source_lang_for_untranslated_filter: Optional[str] = None,
        sort_by_text_in_lang_code: Optional[str] = None,
        sort_ascending: bool = True,
        start_offset: int = 0,
        page_size: int = 20
    ) -> Dict[str, Any]:
        """
        Retrieves translation units based on various filter, sort, and pagination criteria.
        """
        store = self._get_active_store()
        tmx_id = self._ensure_active_tmx_file_id()

        if page_size < 0: # Allow 0 or -1 for "all", though store handles -1 for no limit.
            page_size = -1 
        if start_offset < 0:
            start_offset = 0

        tus, total_count = store.get_translation_units_filtered(
            tmx_file_id=tmx_id,
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

        return {
            "status": "success", # Changed from "Success" to "success" for consistency
            "units": [tu.model_dump(exclude_none=True) for tu in tus], # Convert Pydantic models to dicts
            "total_count": total_count,
            "offset": start_offset,
            "limit": page_size if page_size > 0 else total_count # if page_size was 0 or -1, limit is total_count
        }
        
# Example of TMXConverter (from Chunk 6, for context, not for this chunk's execution)
# class TMXConverter:
#     def to_alternative_format(self, data): # Placeholder
#         pass
#     def from_alternative_format(self, data): # Placeholder
#         pass

if __name__ == '__main__':
    # Basic test for TMXService (requires a dummy TMX file)
    # The existing test needs to be updated to handle datetime in TMXHeader correctly
    # For new methods of Chunk 5:
    import re # For lang code validation in service methods
    import sqlite3 # For IntegrityError in change_language_code test
    from datetime import datetime # For set_source_language

    dummy_tmx_content = """<?xml version="1.0" encoding="UTF-8"?>
<tmx version="1.4">
  <header creationtool="TestTool" creationtoolversion="1.0" segtype="sentence" o-tmf="unknown" adminlang="en" srclang="en-US" datatype="plaintext" creationdate="20230101T120000Z"/>
  <body>
    <tu tuid="1">
      <tuv xml:lang="en-US"><seg>Hello world.</seg></tuv>
      <tuv xml:lang="fr-FR"><seg>Bonjour le monde.</seg></tuv>
      <tuv xml:lang="es-ES"><seg>Hola mundo.</seg></tuv>
    </tu>
    <tu tuid="2">
      <prop type="x-category">General</prop>
      <tuv xml:lang="en-US"><seg>Another sentence.</seg></tuv>
      <tuv xml:lang="fr-FR"><seg>Une autre phrase.</seg></tuv>
    </tu>
  </body>
</tmx>
"""
    dummy_tmx_filepath = "dummy_chunk5_test.tmx"
    with open(dummy_tmx_filepath, "w", encoding="utf-8") as f:
        f.write(dummy_tmx_content)

    output_tmx_filepath = "dummy_chunk5_output.tmx"
    test_db_path = "test_service_chunk5.db"

    service = TMXService(db_path=test_db_path)

    try:
        print(f"--- Opening file: {dummy_tmx_filepath} ---")
        service.open_file(dummy_tmx_filepath)
        print("File opened successfully.")
        
        info_after_open = service.get_file_info()
        assert info_after_open.is_active
        assert info_after_open.header is not None
        print(f"Initial languages: {info_after_open.language_codes}")
        assert "es-ES" in info_after_open.language_codes

        print("\n--- Testing add_language ---")
        # Test adding a new, valid language (conceptual for now)
        add_lang_result = service.add_language("de-DE")
        print(add_lang_result)
        assert add_lang_result["status"] == "success"
        # Test adding an invalid language
        try:
            service.add_language("invalid code")
        except ValueError as e:
            print(f"Caught expected error for invalid lang: {e}")
        
        print("\n--- Testing remove_language ---")
        remove_result = service.remove_language("es-ES")
        print(remove_result)
        assert remove_result["status"] == "success"
        assert "1" in remove_result["message"] # 1 segment for es-ES in tuid="1"
        info_after_remove = service.get_file_info()
        print(f"Languages after remove: {info_after_remove.language_codes}")
        assert "es-ES" not in info_after_remove.language_codes

        print("\n--- Testing change_language_code ---")
        change_result = service.change_language_code("fr-FR", "fr-CA", "test_user")
        print(change_result)
        assert change_result["status"] == "success"
        assert "2" in change_result["message"] # 2 segments for fr-FR
        info_after_change = service.get_file_info()
        print(f"Languages after change: {info_after_change.language_codes}")
        assert "fr-FR" not in info_after_change.language_codes
        assert "fr-CA" in info_after_change.language_codes
        
        # Test changing to an existing language
        try:
            service.change_language_code("en-US", "fr-CA") # fr-CA now exists
        except ValueError as e:
            print(f"Caught expected error for changing to existing lang: {e}")

        print("\n--- Testing set_source_language ---")
        assert info_after_open.header.srclang == "en-US" # Initial srclang
        set_src_result = service.set_source_language("fr-CA")
        print(set_src_result)
        assert set_src_result["status"] == "success"
        info_after_set_src = service.get_file_info()
        assert info_after_set_src.header is not None
        assert info_after_set_src.header.srclang == "fr-CA"
        print(f"Source language after set: {info_after_set_src.header.srclang}")


    except Exception as e:
        print(f"Error during TMXService Chunk 5 test: {e}")
    finally:
        print("\n--- Closing file ---")
        service.close_file()
        # Clean up
        if os.path.exists(dummy_tmx_filepath): os.remove(dummy_tmx_filepath)
        if os.path.exists(output_tmx_filepath): os.remove(output_tmx_filepath) # Not used in this test
        if os.path.exists(test_db_path): os.remove(test_db_path)
        print("\nChunk 5 test files cleaned up.")

    # Previous tests from services.py for other functionalities can be run separately or merged.
    # The extract_pure_text_from_segment_xml tests are independent.
#     def to_alternative_format(self, data): # Placeholder
#         pass
#     def from_alternative_format(self, data): # Placeholder
#         pass

if __name__ == '__main__':
    # Basic test for TMXService (requires a dummy TMX file)
    dummy_tmx_content = """<?xml version="1.0" encoding="UTF-8"?>
<tmx version="1.4">
  <header creationtool="TestTool" creationtoolversion="1.0" segtype="sentence" o-tmf="unknown" adminlang="en" srclang="en-US" datatype="plaintext" />
  <body>
    <tu tuid="1">
      <tuv xml:lang="en-US"><seg>Hello world.</seg></tuv>
      <tuv xml:lang="fr-FR"><seg>Bonjour le monde.</seg></tuv>
    </tu>
    <tu tuid="2">
      <prop type="x-category">General</prop>
      <tuv xml:lang="en-US"><seg>Another sentence.</seg></tuv>
      <tuv xml:lang="fr-FR"><seg>Une autre phrase.</seg></tuv>
    </tu>
  </body>
</tmx>
"""
    dummy_tmx_filepath = "dummy_test_input.tmx"
    with open(dummy_tmx_filepath, "w", encoding="utf-8") as f:
        f.write(dummy_tmx_content)

    output_tmx_filepath = "dummy_test_output.tmx"
    test_db_path = "test_service.db"

    service = TMXService(db_path=test_db_path)

    try:
        print(f"--- Initial File Info ---")
        info = service.get_file_info()
        print(info.model_dump_json(indent=2))
        assert not info.is_active

        print(f"\n--- Opening file: {dummy_tmx_filepath} ---")
        service.open_file(dummy_tmx_filepath)
        print("File opened successfully.")
        
        info_after_open = service.get_file_info()
        print(f"\n--- File Info After Open ---")
        print(info_after_open.model_dump_json(indent=2))
        assert info_after_open.is_active
        assert info_after_open.file_path == dummy_tmx_filepath
        assert info_after_open.header is not None
        assert info_after_open.header.srclang == "en-US"
        assert info_after_open.tu_count == 2
        assert sorted(info_after_open.language_codes) == sorted(["en-US", "fr-FR"])

        print(f"\n--- Saving file to: {output_tmx_filepath} ---")
        service.save_file(output_tmx_filepath)
        print(f"File saved to {output_tmx_filepath}")
        assert os.path.exists(output_tmx_filepath)

        # Verify content of saved file (basic check)
        with open(output_tmx_filepath, "r", encoding="utf-8") as f_out:
            saved_content = f_out.read()
            assert "<seg>Hello world.</seg>" in saved_content
            assert 'srclang="en-US"' in saved_content


    except Exception as e:
        print(f"Error during TMXService test: {e}")
    finally:
        print("\n--- Closing file ---")
        service.close_file()
        info_after_close = service.get_file_info()
        print(f"\n--- File Info After Close ---")
        print(info_after_close.model_dump_json(indent=2))
        assert not info_after_close.is_active
        
        # Clean up dummy files
        if os.path.exists(dummy_tmx_filepath):
            os.remove(dummy_tmx_filepath)
        if os.path.exists(output_tmx_filepath):
            os.remove(output_tmx_filepath)
        if os.path.exists(test_db_path):
            os.remove(test_db_path)
        print("\nTest files cleaned up.")

    print("\n--- Testing extract_pure_text_from_segment_xml ---")
    xml_sample1 = "<seg>This is <b>bold</b> and <i>italic</i> text.</seg>"
    assert extract_pure_text_from_segment_xml(xml_sample1) == "This is bold and italic text."
    xml_sample2 = "<seg>Just text</seg>"
    assert extract_pure_text_from_segment_xml(xml_sample2) == "Just text"
    xml_sample3 = "<seg></seg>"
    assert extract_pure_text_from_segment_xml(xml_sample3) == ""
    xml_sample4 = "<seg>  Leading and trailing spaces  </seg>"
    assert extract_pure_text_from_segment_xml(xml_sample4) == "Leading and trailing spaces"
    xml_sample5 = "<seg>Text with <ph x='1'/> placeholder.</seg>" # Placeholder tag
    assert extract_pure_text_from_segment_xml(xml_sample5) == "Text with  placeholder." # Note: text from <ph> is empty string
    malformed_xml = "<seg>This is <b>unclosed bold text.</seg>"
    # The behavior with malformed XML depends on lxml's recover=True, it might still extract some text
    extracted_malformed = extract_pure_text_from_segment_xml(malformed_xml)
    print(f"Extracted from malformed ('{malformed_xml}'): '{extracted_malformed}'") # Likely "This is unclosed bold text."
    assert "This is unclosed bold text." in extracted_malformed

    print("extract_pure_text_from_segment_xml tests passed.")
